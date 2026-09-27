"""Speech: Whisper speech-to-text and Piper text-to-speech (both offline)."""
import re

import numpy as np

from launcher import rel

PROMPT = ("Альтрон, иди за мной. Альтрон, найди алмазы. Добудь железо, уголь, дерево, руду. "
          "Принеси бочки с нефтью. Скрафти кирку. Стреляй по зомби. Охраняй меня. Стоп. "
          "Наковальня из мода HBM, ракета HBM, TaCZ, Immersive Engineering. Запомни это место.")

# words of this pack Whisper should prefer when unsure ("ракету", not "руку"; "танк", not "танец")
HOTWORDS = "Альтрон командир ракета ракету танк танке HBM TaCZ наковальня печка уголь железо нефть стоп стой"

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
                self.model.transcribe(np.zeros(16000, dtype=np.float32), language="ru")   # load the CUDA kernels now
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

    def transcribe(self, pcm48):
        """pcm48: int16 numpy array, mono 48 kHz. Returns recognized text ('' if nothing)."""
        return self.transcribe_ex(pcm48)[0]

    def transcribe_ex(self, pcm48):
        """Text and how sure Whisper is about it: the mean log-probability of its words
        (about -0.2 for clear speech, below -0.9 for mumbling, noise or a guess)."""
        x = pcm48.astype(np.float32) / 32768.0
        x = np.convolve(x, _LP_48_16, mode="same")[::3]
        segments, _ = self.model.transcribe(x, language="ru", beam_size=self.beam, vad_filter=True,
                                            condition_on_previous_text=False, without_timestamps=True,
                                            initial_prompt=self.prompt, hotwords=self.hotwords)
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


def clean_for_speech(text):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"[a-z_]+:[a-z0-9_/.]+", "", text)          # registry ids
    text = re.sub(r"[*_#`>\[\]{}|]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


class TTS:
    def __init__(self, cfg):
        from piper import PiperVoice, SynthesisConfig
        self.voice = PiperVoice.load(str(rel(cfg["tts_voice"])))
        speed = max(0.5, float(cfg.get("tts_speed", 1.0)))
        self.syn = SynthesisConfig(length_scale=1.0 / speed, volume=1.0)
        self.robot = float(cfg.get("tts_robot", 0.25))

    def _robotize(self, y, sr):
        """Metallic 'helmet' resonance from two short echoes. No amplitude modulation:
        the earlier ring modulator made the voice sound choppy."""
        if self.robot <= 0:
            return y
        out = y.copy()
        for delay_ms, gain in ((3.1, 0.9), (7.3, 0.5)):
            d = int(sr * delay_ms / 1000)
            out[d:] += gain * self.robot * y[:-d]
        return out

    def synth(self, text):
        """Yields 48 kHz mono s16le PCM chunks, one per sentence."""
        text = clean_for_speech(text)
        if not text:
            return
        for chunk in self.voice.synthesize(text, self.syn):
            a = np.asarray(chunk.audio_int16_array, dtype=np.float32).reshape(-1) / 32768.0
            y = resample(a, chunk.sample_rate, 48000)
            y = self._robotize(y, 48000)
            peak = float(np.max(np.abs(y))) if len(y) else 0.0
            if peak > 0:
                y = y * (0.85 / peak)
            y = np.concatenate([np.zeros(2400, dtype=np.float32), y, np.zeros(2400, dtype=np.float32)])
            yield (y * 32767).astype("<i2").tobytes()
