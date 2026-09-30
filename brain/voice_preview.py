"""Listen to Altron's voices without the game: says a few phrases of each manner and saves them as .wav files in
test-reports/voice/ (and plays them on Windows), so the voice can be tuned by ear.

    .venv\\Scripts\\python.exe voice_preview.py                       both manners, the usual phrases
    .venv\\Scripts\\python.exe voice_preview.py --persona teammate --speed 1.15 --pitch 0.97 --crush 11025
    .venv\\Scripts\\python.exe voice_preview.py --text "Докладываю: у нас минус дом."
    .venv\\Scripts\\python.exe voice_preview.py --persona teammate --speed 1.15 --save   (keeps it in config.json)

--speed (1.0 = as the model speaks), --pitch (below 1 — lower), --crush (Hz of the digital grit, 0 — none),
--band LOW HIGH (Hz kept, like a small speaker), --flat NOISE NOISE_W (Piper's variation: lower is more monotonous),
--voice ru=path.onnx (another Piper voice for a language).
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
    ap.add_argument("--persona", choices=sorted(persona.PERSONAS), action="append")
    ap.add_argument("--text", action="append")
    ap.add_argument("--lang", default="ru")
    ap.add_argument("--speed", type=float)
    ap.add_argument("--pitch", type=float)
    ap.add_argument("--crush", type=int)
    ap.add_argument("--band", type=int, nargs=2)
    ap.add_argument("--flat", type=float, nargs=2)
    ap.add_argument("--voice", action="append", default=[], help="lang=path.onnx")
    ap.add_argument("--save", action="store_true", help="keep these settings in config.json for this manner")
    ap.add_argument("--no-play", action="store_true")
    a = ap.parse_args(argv)
    cfg_path = BRAIN_DIR / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    from speech import TTS
    out = BRAIN_DIR.parent / "test-reports" / "voice"
    out.mkdir(parents=True, exist_ok=True)
    tuned = {k: v for k, v in (("speed", a.speed), ("pitch", a.pitch), ("crush", a.crush),
                               ("band", a.band), ("flat", a.flat)) if v is not None}
    voices = dict(v.split("=", 1) for v in a.voice)
    files = []
    for name in a.persona or sorted(persona.PERSONAS):
        settings = persona.voice_settings(cfg, name)
        settings.update(tuned)
        settings["voices"].update(voices)
        tts = TTS(cfg, settings)
        for i, text in enumerate(a.text or PHRASES.get(a.lang, PHRASES["en"])):
            path = out / ("%s_%d.wav" % (name, i + 1))
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(48000)
                for chunk in tts.synth(text, a.lang):
                    w.writeframes(chunk)
            files.append(path)
            print("%s: %s -> %s" % (name, text, path))
        if a.save and (tuned or voices):
            p = cfg.setdefault("tts_personas", {}).setdefault(name, {})
            p.update(tuned)
            if voices:
                p.setdefault("voices", {}).update(voices)
            cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print("сохранил в config.json: tts_personas.%s = %s" % (name, json.dumps(p, ensure_ascii=False)))
    if sys.platform == "win32" and not a.no_play:
        import winsound
        for path in files:
            winsound.PlaySound(str(path), winsound.SND_FILENAME)
    return 0


if __name__ == "__main__":
    sys.exit(main())
