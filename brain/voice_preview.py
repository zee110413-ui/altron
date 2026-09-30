"""Listen to Altron's voice without the game: Maxim says a few phrases, they are saved as .wav files in
test-reports/voice/ (and played on Windows).

    .venv\\Scripts\\python.exe voice_preview.py                   Russian phrases
    .venv\\Scripts\\python.exe voice_preview.py --lang en         English ones
    .venv\\Scripts\\python.exe voice_preview.py --text "Докладываю: у нас минус дом."
    .venv\\Scripts\\python.exe voice_preview.py --speed 1.15 --save   (keeps the pace in config.json)
    .venv\\Scripts\\python.exe voice_preview.py --list            the voices installed in Windows

Maxim is taken from Windows when it is installed there (SAPI 5), otherwise from Amazon Polly (brain/polly.json).
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
    tts = TTS(cfg, settings)
    print("Голос: %s" % tts.describe())
    if not tts.ready:
        print("Как дать Альтрону голос — КАК ИГРАТЬ.txt, раздел ГОЛОС.")
        return 1
    out = BRAIN_DIR.parent / "test-reports" / "voice"
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for i, text in enumerate(a.text or PHRASES.get(a.lang, PHRASES["en"])):
        path = out / ("%s_%s_%d.wav" % (name, a.lang, i + 1))
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(48000)
            for chunk in tts.synth(text, a.lang):
                w.writeframes(chunk)
        files.append(path)
        print("%s -> %s" % (text, path))
    if a.save and a.speed is not None:
        cfg.setdefault("tts_personas", {}).setdefault(name, {})["speed"] = a.speed
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("сохранил в config.json: tts_personas.%s.speed = %s" % (name, a.speed))
    if sys.platform == "win32" and not a.no_play:
        import winsound
        for path in files:
            winsound.PlaySound(str(path), winsound.SND_FILENAME)
    return 0


if __name__ == "__main__":
    sys.exit(main())
