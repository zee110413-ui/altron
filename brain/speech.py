"""Speech: Whisper speech-to-text and Piper text-to-speech (both offline)."""
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
    """Piper voices, one per language. The manner of speaking (persona.py) picks the style and the voices:
    "robot" — a light metallic helmet; "ultron" — a lower, doubled, cold synthetic voice with a metal resonance and a
    short hall (an effect on any voice model, not a copy of anyone's real voice); "synth" — a plain, narrow voice like
    an old text-to-speech program."""

    STYLES = {
        # pitch factor, metal comb, chorus depth, drive, hall
        "plain": (1.0, 0.0, 0.0, 0.0, 0.0),
        "robot": (1.0, 0.25, 0.0, 0.0, 0.0),
        "ultron": (0.84, 0.35, 0.55, 0.6, 0.22),
        "synth": (1.0, 0.0, 0.0, 0.15, 0.0),
    }
    BANDS = {"synth": (300, 5000)}   # Hz kept: a speech synthesizer of old sounds narrow, like through a small speaker
    CRUSH = {"synth": 16000}         # Hz: held samples give the slight digital grit of a text-to-speech program
    # Piper's variation of the voice and of the length of sounds (its defaults: 0.667, 0.8): lower is the even,
    # monotonous delivery of a speech synthesizer, the same whatever it says — which is what makes the jokes land
    FLAT = {"synth": (0.3, 0.35)}

    # how a mood changes the delivery: pace, pitch, loudness (on top of the style)
    MOODS = {
        "alert": (1.15, 1.05, 1.0),     # danger, a fight: faster and higher
        "excited": (1.07, 1.03, 1.0),   # an exclamation, good news
        "sad": (0.9, 0.96, 0.85),       # sympathy, a loss: slower, lower, quieter
        "cold": (0.96, 0.97, 0.95),     # annoyed, offended: measured and a little lower
        "bored": (0.93, 0.98, 0.88),    # nothing to do: drawn out and quiet
    }

    def __init__(self, cfg, settings=None):
        from piper import SynthesisConfig
        self._config = SynthesisConfig
        self.cfg = cfg
        self.voices = {}     # voice file -> loaded voice
        self.moods = bool(cfg.get("tts_moods", True))
        self.use(settings or {"style": cfg.get("tts_style", "robot"), "voices": cfg.get("tts_voices") or {}})
        self._voice(None)   # the fallback voice loads now: a broken path shows at start, not at the first word

    def use(self, settings):
        """Switch the manner of speaking: {"style", "voices": {lang: path}, "speed", "pitch", "band"}. A voice file
        that is not there (not downloaded yet) is skipped: that language is said with the common voice."""
        style = str(settings.get("style", "robot")).lower()
        pitch, comb, chorus, drive, hall = self.STYLES.get(style, self.STYLES["robot"])
        if "tts_robot" in self.cfg and style == "robot":
            comb = float(self.cfg["tts_robot"])
        self.style = style
        self.pitch = float(settings.get("pitch", pitch))
        self.comb, self.chorus, self.drive, self.hall = comb, chorus, drive, hall
        self.band = tuple(settings.get("band") or self.BANDS.get(style, ())) or None
        self.crush = int(settings.get("crush", self.CRUSH.get(style, 0)) or 0)
        self.flat = self.FLAT.get(style)
        self.speed = max(0.5, float(settings.get("speed", 1.0)) * float(self.cfg.get("tts_speed", 1.0)))
        self.paths = {}
        for k, v in (settings.get("voices") or {}).items():
            if v and rel(v).exists():
                self.paths[k.lower()] = v
        return self

    def _voice(self, lang):
        path = self.paths.get(lang) or self.cfg["tts_voice"]
        if path not in self.voices:
            from piper import PiperVoice
            self.voices[path] = PiperVoice.load(str(rel(path)))
        return self.voices[path]

    def _effects(self, y, sr):
        if self.band:
            low, high = self.band
            y = np.convolve(y, _lowpass(63, high / sr), mode="same")
            y = y - np.convolve(y, _lowpass(255, low / sr), mode="same")
        if 0 < self.crush < sr:
            step = int(round(sr / self.crush))
            y = np.repeat(y[::step], step)[:len(y)]
        out = y.copy()
        if self.chorus > 0:
            # a second, slightly wandering copy of the voice (5-11 ms): the "many voices in one" of a machine
            n = np.arange(len(y), dtype=np.float32)
            delay = sr * (0.008 + 0.003 * np.sin(2 * np.pi * 0.6 * n / sr))
            out += self.chorus * np.interp(n - delay, n, y, left=0.0)
        if self.comb > 0:
            # metallic resonance from two short echoes (no amplitude modulation: that made the voice choppy)
            for delay_ms, gain in ((3.1, 0.9), (7.3, 0.5)):
                d = int(sr * delay_ms / 1000)
                out[d:] += gain * self.comb * y[:-d]
        if self.drive > 0:
            peak = float(np.max(np.abs(out))) or 1.0
            out = np.tanh(out / peak * (1 + 3 * self.drive)) / np.tanh(1 + 3 * self.drive)
        if self.hall > 0:
            tail = out.copy()
            for delay_ms, gain in ((43, 0.55), (71, 0.4), (113, 0.28), (167, 0.18)):
                d = int(sr * delay_ms / 1000)
                if d < len(out):
                    tail[d:] += gain * self.hall * out[:-d]
            out = tail
        return out

    def synth(self, text, lang=None, mood=None):
        """Yields 48 kHz mono s16le PCM chunks, one per sentence."""
        text = clean_for_speech(text)
        if not text:
            return
        pace, rise, loud = self.MOODS.get(mood, (1.0, 1.0, 1.0)) if self.moods else (1.0, 1.0, 1.0)
        pitch = self.pitch * rise
        # lowering the pitch slows the voice down: speak that much faster first, so the pace stays the same
        flat = dict(zip(("noise_scale", "noise_w_scale"), self.flat)) if self.flat else {}
        syn = self._config(length_scale=pitch / (self.speed * pace), volume=1.0, **flat)
        for chunk in self._voice(lang).synthesize(text, syn):
            a = np.asarray(chunk.audio_int16_array, dtype=np.float32).reshape(-1) / 32768.0
            # read at a lower rate than it was made: the whole voice goes down by the pitch factor
            y = resample(a, chunk.sample_rate * pitch, 48000)
            y = self._effects(y, 48000)
            peak = float(np.max(np.abs(y))) if len(y) else 0.0
            if peak > 0:
                y = y * (0.85 * loud / peak)
            y = np.concatenate([np.zeros(2400, dtype=np.float32), y, np.zeros(2400, dtype=np.float32)])
            yield (y * 32767).astype("<i2").tobytes()
