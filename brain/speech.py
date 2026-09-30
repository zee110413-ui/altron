"""Speech: Whisper speech-to-text and the voice Altron speaks with (Maxim: Windows or Amazon Polly)."""
import re

import numpy as np

from launcher import rel

PROMPT = ("Альтрон, иди за мной. Альтрон, найди алмазы. Добудь железо, уголь, дерево, руду. "
          "Скрафти кирку. Стреляй по зомби. Охраняй меня. Стоп. Запомни это место. "
          "Altron, follow me. Mine some iron. Craft a pickaxe. Guard me. Stop.")

# words Whisper should prefer when unsure (only with "stt_prompt": true)
HOTWORDS = "Альтрон Altron командир commander уголь железо алмазы печка стоп стой stop"

# Whisper's typical hallucinations on silence/noise
HALLUCINATIONS = ("субтитр", "продолжение следует", "спасибо за просмотр", "редактор субтитров", "корректор",
                  "dimatorzok", "подписывайтесь", "подпишись", "игорь негода", "заставк", "синецкая", "егорова",
                  "до новых встреч", "ставьте лайк", "в следующем видео", "спасибо за внимание")
# Quieter than this (peak of the phrase, 1.0 = full scale) is background noise that opened the voice chat, not speech:
# measured on the commander's real microphone: speech peaks 0.10-1.0, noise 0.01-0.05
MIN_PEAK = 0.07


def loud_enough(pcm):
    """pcm: int16 array. False for background noise (Whisper makes up phrases from it)."""
    return len(pcm) > 0 and float(np.max(np.abs(pcm.astype(np.float32)))) / 32768.0 >= MIN_PEAK


def _lowpass(taps, cutoff):
    n = np.arange(taps) - (taps - 1) / 2
    h = np.sinc(2 * cutoff * n) * np.hamming(taps)
    return (h / h.sum()).astype(np.float32)


_LP_48_16 = _lowpass(63, 7400 / 48000)


def resample(x, sr_from, sr_to):
    if sr_from == sr_to:
        return x
    n_out = int(round(len(x) * sr_to / sr_from))
    return np.interp(np.linspace(0, len(x) - 1, n_out), np.arange(len(x)), x).astype(np.float32)


def _cuda_dlls():
    """NVIDIA's cuBLAS/cuDNN from pip (nvidia-*-cu12 in the venv): make their DLLs findable for CTranslate2."""
    import os
    import sys
    from pathlib import Path
    root = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    dirs = [str(d) for d in root.glob("*/bin") if d.is_dir()]
    for d in dirs:
        try:
            os.add_dll_directory(d)
        except (OSError, AttributeError):
            pass
    os.environ["PATH"] = os.pathsep.join(dirs + [os.environ.get("PATH", "")])
    return bool(dirs)


class STT:
    def __init__(self, cfg, log=print):
        from faster_whisper import WhisperModel
        path = str(rel(cfg["stt_model"]))
        device = cfg.get("stt_device", "auto")
        self.model = None
        if device in ("auto", "cuda") and _cuda_dlls():
            try:
                # on the video card a phrase takes ~0.1-0.3 s instead of ~2 s on the processor
                self.model = WhisperModel(path, device="cuda", compute_type=cfg.get("stt_compute_gpu", "float16"))
                self.model.transcribe(np.zeros(16000, dtype=np.float32), language="en")   # load the CUDA kernels now
                self.device = "видеокарта"
            except Exception as e:
                log("Распознавание речи на видеокарте не запустилось (%s) — работаю на процессоре." % e)
                self.model = None
        if self.model is None:
            # the big model is too slow on the processor: fall back to the small one there
            cpu_path = str(rel(cfg.get("stt_model_cpu", cfg["stt_model"])))
            self.model = WhisperModel(cpu_path, device="cpu", compute_type="int8", cpu_threads=cfg.get("stt_threads", 4))
            self.device = "процессор"
        # a wider search understands real, unclear speech better; on the video card it costs almost nothing
        self.beam = int(cfg.get("stt_beam", 5 if self.device == "видеокарта" else 1))
        # A prompt with example phrases made Whisper answer noise and unclear words with those very phrases
        # ("Стреляй по зону", "Принеси бочки с нефтью" said by nobody): off unless asked for
        self.prompt = PROMPT if cfg.get("stt_prompt", False) else None
        self.hotwords = HOTWORDS if cfg.get("stt_prompt", False) else None
        # "auto": Whisper hears which language it is, but only among the ones Altron is set to speak (a short
        # phrase alone is easily taken for a neighbouring language)
        lang = str(cfg.get("language", "auto")).lower()
        self.language = None if lang == "auto" else lang
        self.allowed = [c.lower() for c in cfg.get("languages", [])] if self.language is None else [self.language]
        self.last_lang = self.language or (self.allowed[0] if self.allowed else "en")

    def transcribe(self, pcm48):
        """pcm48: int16 numpy array, mono 48 kHz. Returns recognized text ('' if nothing)."""
        return self.transcribe_ex(pcm48)[0]

    def transcribe_ex(self, pcm48):
        """Text and how sure Whisper is about it: the mean log-probability of its words
        (about -0.2 for clear speech, below -0.9 for mumbling, noise or a guess)."""
        x = pcm48.astype(np.float32) / 32768.0
        x = np.convolve(x, _LP_48_16, mode="same")[::3]
        lang = self.language or self._detect(x)
        segments, info = self.model.transcribe(x, language=lang, beam_size=self.beam, vad_filter=True,
                                            condition_on_previous_text=False, without_timestamps=True,
                                            initial_prompt=self.prompt, hotwords=self.hotwords)
        heard = lang or getattr(info, "language", None)
        if heard and (not self.allowed or heard in self.allowed):
            self.last_lang = heard
        parts, logp, weight = [], 0.0, 0
        for s in segments:
            # Whisper's own rule for "this was not speech": likely silence and an unsure guess
            if s.no_speech_prob > 0.6 and s.avg_logprob < -1.0:
                continue
            words = max(1, len(s.text.split()))
            parts.append(s.text.strip())
            logp += s.avg_logprob * words
            weight += words
        text = " ".join(parts).strip()
        low = text.lower()
        copied = self.prompt and low.strip(" .!?") in [s.strip().lower() for s in re.split(r"[.!?]", self.prompt) if s.strip()]
        if not text or any(h in low for h in HALLUCINATIONS) or copied:
            return "", -9.0
        return text, logp / weight

    def _detect(self, x):
        """The most likely of the allowed languages (None: let Whisper decide by itself)."""
        if not self.allowed:
            return None
        if len(self.allowed) == 1:
            return self.allowed[0]
        try:
            _, _, probs = self.model.detect_language(x)
        except Exception:
            return None
        best = max(((p, c) for c, p in probs if c in self.allowed), default=None)
        return best[1] if best else self.allowed[0]


def clean_for_speech(text):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"[a-z_]+:[a-z0-9_/.]+", "", text)          # registry ids
    text = re.sub(r"[*_#`>\[\]{}|]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


class TTS:
    """Altron's one voice: a speech-synthesizer program's voice, the same in every language (English with its robot
    accent), like the robot teammate Kava in videos. "voice" in config.json lists the voices it may be, the first one
    found wins: Maxim (IVONA, Kava's own voice) when it is installed in Windows or there are Amazon Polly keys, else the
    free Microsoft Pavel that Windows has. There is no other voice: without them Altron writes in the game chat."""

    VOICES = ["Maxim", "Pavel"]
    # how each voice is brought closer to Kava's Maxim: Pavel is a little higher and lighter, so he is read slightly
    # lower (the same words and pace, a deeper tone); "voice_tuning" in config.json changes it, voice_preview.py tries it
    TUNING = {"pavel": {"pitch": 0.9}}
    MOODS = {
        "alert": (1.15, 1.0),     # danger, a fight: faster
        "excited": (1.07, 1.0),   # an exclamation, good news
        "sad": (0.9, 0.85),       # sympathy, a loss: slower and quieter
        "cold": (0.96, 0.95),     # annoyed, offended: measured
        "bored": (0.93, 0.88),    # nothing to do: drawn out and quiet
    }

    def __init__(self, cfg, settings=None):
        self.cfg = cfg
        self.moods = bool(cfg.get("tts_moods", True))
        want = cfg.get("voice") or self.VOICES
        names = [want] if isinstance(want, str) else list(want)
        self.engine, self.voice, self.why = self.find(names)
        self.key = next((n.lower() for n in names if self.voice and n.lower() in self.voice.lower()), "")
        tuning = dict(self.TUNING.get(self.key, {}))
        tuning.update((cfg.get("voice_tuning") or {}).get(self.key) or {})
        self.pitch = min(1.5, max(0.6, float(tuning.get("pitch", 1.0))))
        self.failed_at = 0.0
        self.use(settings or {})

    @staticmethod
    def find(names):
        """("sapi", the installed voice) / ("polly", name) / (None, None, why not): the first of the names that can
        speak — a voice installed in Windows by a part of its name, or Maxim from Amazon Polly when the PC has keys."""
        import polly
        import sapi
        for name in names:
            installed = sapi.find(name)
            if installed:
                return "sapi", installed, ""
            if name.lower() == "maxim" and polly.credentials():
                return "polly", "Maxim", ""
        return None, None, ("нет голоса %s: в Windows не установлен (Параметры -> Время и язык -> Речь -> добавить "
                            "голос «Русский», в нём Павел)" % " / ".join(names))

    @property
    def ready(self):
        return self.engine is not None

    def describe(self):
        if self.engine == "sapi":
            return "%s (Windows)" % self.voice
        if self.engine == "polly":
            return "%s (Amazon Polly)" % self.voice
        return "нет — " + self.why

    def use(self, settings):
        """The pace of the manner of speaking (persona.py): {"speed": 1.08}."""
        self.speed = max(0.5, float(settings.get("speed", 1.0)) * float(self.cfg.get("tts_speed", 1.0)))
        return self

    def _say(self, text, pace):
        # a lower tone is read slower (see synth): the voice speaks that much faster first, so the pace stays
        pace = pace / self.pitch
        if self.engine == "sapi":
            import math
            import sapi
            # Windows counts the pace from -10 to 10, about three times faster or slower at the ends
            return sapi.speak(self.voice, text, round(10 * math.log(max(0.3, self.speed * pace)) / math.log(3)))
        import polly
        return polly.speak(self.voice, text, round(100 * self.speed * pace))

    @staticmethod
    def _finish(y, loud):
        peak = float(np.max(np.abs(y))) if len(y) else 0.0
        if peak > 0:
            y = y * (0.85 * loud / peak)
        y = np.concatenate([np.zeros(2400, dtype=np.float32), y, np.zeros(2400, dtype=np.float32)])
        return (y * 32767).astype("<i2").tobytes()

    def synth(self, text, lang=None, mood=None):
        """Yields 48 kHz mono s16le PCM chunks, one per sentence (nothing without the voice)."""
        text = clean_for_speech(text)
        if not text or not self.ready:
            return
        pace, loud = self.MOODS.get(mood, (1.0, 1.0)) if self.moods else (1.0, 1.0)
        for sentence in re.split(r"(?<=[.!?…])\s+", text):
            if not sentence.strip():
                continue
            try:
                pcm, sr = self._say(sentence, pace)
            except Exception as e:   # no other voice to fall back on: the words stay in the log and the chat
                import time
                if time.time() - self.failed_at > 60:
                    self.failed_at = time.time()
                    print("Голос %s не ответил: %s" % (self.describe(), e))
                return
            # read at a rate lower than it was made: the whole voice goes down by the pitch factor
            yield self._finish(resample(pcm.astype(np.float32) / 32768.0, sr * self.pitch, 48000), loud)
