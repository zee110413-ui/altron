"""Listen to Altron's voice without the game: it says a few phrases, they are saved as .wav files in
test-reports/voice/ (and played on Windows).

    .venv\\Scripts\\python.exe voice_preview.py                   Russian phrases
    .venv\\Scripts\\python.exe voice_preview.py --lang en         English ones
    .venv\\Scripts\\python.exe voice_preview.py --text "Докладываю: у нас минус дом."
    .venv\\Scripts\\python.exe voice_preview.py --speed 1.15 --save   (keeps the pace in config.json)
    .venv\\Scripts\\python.exe voice_preview.py --pitch 0.88 --save   (the tone: below 1 — lower, like Maxim)
    .venv\\Scripts\\python.exe voice_preview.py --list            the voices installed in Windows

The voice: IVONA Maxim when it is installed in Windows, otherwise the free Microsoft Pavel, brought closer to Maxim.
"""
import argparse
import json
import sys
import wave

import persona
from launcher import BRAIN_DIR

PHRASES = {
    "ru": ["Докладываю: у нас минус дом. Крипер передаёт привет.",
           "Я не застрял. Я держу оборону внутри стены.",
           "Алмазы нашёл. Радуюсь. Внутри."],
    "en": ["Report: we are down one house. The creeper says hi.",
           "I am not stuck. I am holding the inside of this wall."],
}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--persona", choices=sorted(persona.PERSONAS))
    ap.add_argument("--text", action="append")
    ap.add_argument("--lang", default="ru")
    ap.add_argument("--speed", type=float)
    ap.add_argument("--pitch", type=float, help="the tone of the voice: 1 — as it is, below 1 — lower")
    ap.add_argument("--list", action="store_true", help="list the voices installed in Windows")
    ap.add_argument("--save", action="store_true", help="keep the pace in config.json for this manner")
    ap.add_argument("--no-play", action="store_true")
    a = ap.parse_args(argv)
    if a.list:
        import sapi
        names = sapi.voices()
        print("Голоса Windows:" if names else "Голосов Windows не нашёл (или это не Windows).")
        for n in names:
            print("  " + n)
        return 0
    cfg_path = BRAIN_DIR / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    from speech import TTS
    name = a.persona or cfg.get("persona") or persona.DEFAULT
    settings = persona.voice_settings(cfg, name)
    if a.speed is not None:
        settings["speed"] = a.speed
    if a.pitch is not None:
        cfg = dict(cfg)
        want = cfg.get("voice") or TTS.VOICES
        for n in [want] if isinstance(want, str) else want:
            cfg.setdefault("voice_tuning", {}).setdefault(n.lower(), {})["pitch"] = a.pitch
    tts = TTS(cfg, settings)
    print("Голос: %s, тон %.2f, темп %.2f" % (tts.describe(), getattr(tts, "pitch", 1.0), settings.get("speed", 1.0)))
    if not tts.ready:
        print("Как дать Альтрону голос — КАК ИГРАТЬ.txt, раздел ГОЛОС.")
        return 1
    out = BRAIN_DIR.parent / "test-reports" / "voice"
    out.mkdir(parents=True, exist_ok=True)
    import numpy as np
    files = []
    for i, text in enumerate(a.text or PHRASES.get(a.lang, PHRASES["en"])):
        path = out / ("%s_%s_%d.wav" % (name, a.lang, i + 1))
        pcm = b"".join(tts.synth(text, a.lang))
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(48000)
            w.writeframes(pcm)
        files.append(path)
        y = np.frombuffer(pcm, dtype="<i2")
        peak = int(np.max(np.abs(y))) if len(y) else 0
        # how long and how loud: a silent file means the voice gave no sound, not the headphones
        print("%s -> %s (%.1f с, громкость %d%%%s)" % (text, path, len(y) / 48000, peak * 100 // 32767,
                                                     ", ТИШИНА: голос не дал звука" if peak < 300 else ""))
    if a.save and (a.speed is not None or a.pitch is not None):
        keep = json.loads(cfg_path.read_text(encoding="utf-8"))
        if a.speed is not None:
            keep.setdefault("tts_personas", {}).setdefault(name, {})["speed"] = a.speed
            print("сохранил темп: %s" % a.speed)
        if a.pitch is not None and getattr(tts, "key", ""):
            keep.setdefault("voice_tuning", {}).setdefault(tts.key, {})["pitch"] = a.pitch
            print("сохранил тон для %s: %s" % (tts.key, a.pitch))
        cfg_path.write_text(json.dumps(keep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if sys.platform == "win32" and not a.no_play:
        import winsound
        print("Играю в устройство Windows по умолчанию (Параметры -> Система -> Звук -> Вывод)...")
        for path in files:
            winsound.PlaySound(str(path), winsound.SND_FILENAME)
    return 0


if __name__ == "__main__":
    sys.exit(main())
