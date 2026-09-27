"""Self-test: synthesize a phrase with Piper, then recognize it with Whisper."""
import json
import time
import wave

import numpy as np

from launcher import BRAIN_DIR
from speech import STT, TTS

cfg = json.loads((BRAIN_DIR / "config.json").read_text(encoding="utf-8"))

t = time.time()
tts = TTS(cfg)
print("TTS загружен за %.1f с" % (time.time() - t))
t = time.time()
stt = STT(cfg)
print("STT загружен за %.1f с" % (time.time() - t))

phrase = "Альтрон, найди пять алмазов и принеси бочки с нефтью."
t = time.time()
pcm = b"".join(tts.synth(phrase))
print("Синтез: %.2f с, длительность %.1f с" % (time.time() - t, len(pcm) / 2 / 48000))
with wave.open(str(BRAIN_DIR / "logs" / "selftest.wav"), "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(48000)
    w.writeframes(pcm)

# Recognize the plain (non-robot) voice, as a player's microphone would sound
tts.robot = 0
plain = np.frombuffer(b"".join(tts.synth(phrase)), dtype="<i2")
t = time.time()
text = stt.transcribe(plain)
print("Распознано за %.2f с: %r" % (time.time() - t, text))
