"""brain/config.json for the field test on GitHub Actions: the Minecraft folder ci/setup_minecraft.py made, Java 17 of
the runner, the small Whisper model on the processor, and no idle thinking (the AI there is a stand-in)."""
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
path = ROOT / "brain" / "config.json"
cfg = json.loads(path.read_text(encoding="utf-8"))
cfg.update({
    "minecraft_dir": str(Path.home() / "mc"),
    "pack_version": "CIPack",
    "java": shutil.which("java"),
    "stt_model": "../models/whisper-tiny",
    "stt_model_cpu": "../models/whisper-tiny",
    "stt_device": "cpu",
    "idle_think_minutes": 0,
    "dataset": False,
})
path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("brain/config.json: Minecraft %s, Java %s, AI port %s" % (cfg["minecraft_dir"], cfg["java"], cfg["llm_port"]))
