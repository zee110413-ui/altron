"""Builds altron\\ai_server: everything of Altron for the second PC, runnable straight from that folder.
Nothing is installed anywhere: portable Python, the brain with its memories, speech models, llama.cpp and the Minecraft
files Altron's body needs (Java, the pack's mods/configs, libraries, assets). The big model is downloaded there."""
import json
import shutil
import sys
from pathlib import Path

from launcher import resolve_install

BRAIN = Path(__file__).resolve().parent
ALTRON = BRAIN.parent
KIT = ALTRON / "ai_server"
cfg = json.loads((BRAIN / "config.json").read_text(encoding="utf-8"))
try:
    _last = json.loads((BRAIN / "last_launch.json").read_text(encoding="utf-8"))
except Exception:
    _last = {}
resolve_install(cfg, _last.get("pack", ""), ask=sys.stdin is not None and sys.stdin.isatty())
MC = Path(cfg["minecraft_dir"])
PACK_NAME = cfg["pack_version"]
PACK = MC / "versions" / PACK_NAME
PY = Path(sys.base_prefix)            # the Python the brain's venv is built on
total = [0]


def copy_file(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and dst.stat().st_size == src.stat().st_size and dst.stat().st_mtime >= src.stat().st_mtime:
        return   # already there (the builder can be run again)
    shutil.copy2(src, dst)
    total[0] += src.stat().st_size


def copy_tree(src, dst, skip=()):
    for p in src.rglob("*"):
        rel = p.relative_to(src)
        if p.is_dir() or any(part in skip for part in rel.parts):
            continue
        copy_file(p, dst / rel)


def step(name):
    print("%-58s" % name, end="", flush=True)


def done():
    print("ok (скопировано всего %.1f ГБ)" % (total[0] / 1e9), flush=True)


step("Python (переносной)...")
copy_tree(PY, KIT / "python", skip=("site-packages", "__pycache__", "Scripts", "Doc", "tcl", "Tools", "test"))
(KIT / "python" / "Lib" / "site-packages").mkdir(parents=True, exist_ok=True)
done()

step("библиотеки (распознавание речи, голос, видеокарта)...")
copy_tree(BRAIN / ".venv", KIT / "venv", skip=("__pycache__",))
done()

step("мозг и его долгая память...")
for f in list(BRAIN.glob("*.py")) + [p for p in (BRAIN / "knowledge_cache.json",) if p.exists()]:
    copy_file(f, KIT / "brain" / f.name)
if (BRAIN / "memory").exists():
    copy_tree(BRAIN / "memory", KIT / "brain" / "memory")
done()

step("модели речи и голоса...")
for name in ("whisper-large-v3-turbo", "whisper-small", "piper"):
    copy_tree(ALTRON / "models" / name, KIT / "models" / name, skip=(".cache",))
done()

step("llama.cpp, память Альтрона о мирах...")
if (KIT / "llama").exists() and not (KIT / "tools" / "llama").exists():
    (KIT / "tools").mkdir(exist_ok=True)
    shutil.move(str(KIT / "llama"), str(KIT / "tools" / "llama"))
copy_tree(ALTRON / "tools" / "llama", KIT / "tools" / "llama")
for extra in cfg.get("extra_bot_mods", []):
    src = (BRAIN / extra).resolve()
    copy_file(src, KIT / "mod" / "libs" / src.name)
if (ALTRON / "bot" / "altron_memory").exists():
    copy_tree(ALTRON / "bot" / "altron_memory", KIT / "bot" / "altron_memory")
done()

step("Minecraft для тела Альтрона: Java...")
copy_tree(MC / "runtime" / "java-runtime-gamma", KIT / "minecraft" / "runtime" / "java-runtime-gamma")
done()

step("Minecraft: сборка (моды, настройки, TaCZ)...")
for f in (PACK_NAME + ".jar", PACK_NAME + ".json"):
    copy_file(PACK / f, KIT / "minecraft" / "versions" / PACK_NAME / f)
for d in ("mods", "config", "defaultconfigs", "tacz", "natives"):
    if (PACK / d).exists():
        copy_tree(PACK / d, KIT / "minecraft" / "versions" / PACK_NAME / d)
done()

step("Minecraft: библиотеки...")
data = json.loads((PACK / (PACK_NAME + ".json")).read_text(encoding="utf-8"))
for lib in data.get("libraries", []):
    art = lib.get("artifact") or (lib.get("downloads") or {}).get("artifact")
    if not art:
        continue
    path = art["path"]
    src = MC / path if path.startswith("libraries/") else MC / "libraries" / path
    if src.exists():
        copy_file(src, KIT / "minecraft" / src.relative_to(MC))
# Forge's installer made more jars that are not in the list but are looked for at start
# (client-*-srg.jar, client-*-extra.jar, forge-*-client.jar): take these folders whole
game_args = " ".join(a for a in data.get("arguments", {}).get("game", []) if isinstance(a, str))
forge_ver = game_args.split("--fml.forgeVersion ")[1].split()[0] if "--fml.forgeVersion " in game_args else ""
mcp_ver = game_args.split("--fml.mcpVersion ")[1].split()[0] if "--fml.mcpVersion " in game_args else ""
mc_ver = game_args.split("--fml.mcVersion ")[1].split()[0] if "--fml.mcVersion " in game_args else ""
for d in (MC / "libraries" / "net" / "minecraft" / "client" / ("%s-%s" % (mc_ver, mcp_ver)),
          MC / "libraries" / "net" / "minecraftforge" / "forge" / ("%s-%s" % (mc_ver, forge_ver))):
    if d.exists():
        copy_tree(d, KIT / "minecraft" / d.relative_to(MC))
done()

step("Minecraft: ресурсы (языки, звуки, шрифты)...")
index_name = data.get("assets", "5")
index = MC / "assets" / "indexes" / (index_name + ".json")
copy_file(index, KIT / "minecraft" / "assets" / "indexes" / index.name)
for obj in json.loads(index.read_text(encoding="utf-8"))["objects"].values():
    h = obj["hash"]
    src = MC / "assets" / "objects" / h[:2] / h
    if src.exists():
        copy_file(src, KIT / "minecraft" / "assets" / "objects" / h[:2] / h)
done()

step("настройки для второго ПК...")
pc2 = dict(cfg)
pc2.update({
    "game_pc_mode": True,
    "game_key": (KIT / "api_key.txt").read_text(encoding="ascii").strip(),
    "minecraft_dir": "../minecraft",
    "java": "../minecraft/runtime/java-runtime-gamma/windows/java-runtime-gamma/bin/javaw.exe",
    "llm_host": "127.0.0.1", "llm_api_key": "",
    # IQ3_XXS: the same 35B packed to 13.2 GB — nearly all of it fits the card (IQ4_XS filled the 16 GB of RAM)
    "llm_model": "../models/Qwen3.6-35B-A3B-UD-IQ3_XXS.gguf", "llm_mmproj": "../models/mmproj-F16.gguf",
    "llm_fit": True, "llm_context": 32768, "llm_fit_ctx": 24576, "llm_fit_margin_mb": 2048, "llm_cache_ram_mb": 256,
    "llm_think_user": False,
    "history_chars": 54000,
    "stt_model": "../models/whisper-large-v3-turbo", "stt_model_cpu": "../models/whisper-small",
    "profile": "eco",
    "extra_bot_mods": ["../mod/libs/" + Path(e).name for e in cfg.get("extra_bot_mods", [])],
})
(KIT / "brain" / "config.json").write_text(json.dumps(pc2, ensure_ascii=False, indent=2), encoding="utf-8")
done()

size = sum(p.stat().st_size for p in KIT.rglob("*") if p.is_file())
print("\nНабор готов: %s — %.1f ГБ (без большой нейросети, она качается на втором ПК)." % (KIT, size / 1e9))
