"""Prepares the bot's game folder and launches a second Minecraft client as the bot."""
import hashlib
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

from lang import ui

BRAIN_DIR = Path(__file__).resolve().parent

# Minimal settings for the bot's window: it only needs to tick (and be filmed), not to look pretty
BOT_OPTIONS = {
    "renderDistance": "4",
    "simulationDistance": "5",
    # the mod draws the world only when someone needs the picture, and sets the frame rate itself
    "maxFps": "10",
    "graphicsMode": "0",
    "ao": "false",
    "renderClouds": '"false"',
    "particles": "2",
    "entityShadows": "false",
    "entityDistanceScaling": "0.5",
    "mipmapLevels": "0",
    "biomeBlendRadius": "0",
    "prioritizeChunkUpdates": "0",
    "enableVsync": "false",
    "fullscreen": "false",
    "pauseOnLostFocus": "false",
    # the bot's game is completely silent: every sound category off
    "soundCategory_master": "0.0",
    "soundCategory_music": "0.0",
    "soundCategory_record": "0.0",
    "soundCategory_weather": "0.0",
    "soundCategory_block": "0.0",
    "soundCategory_hostile": "0.0",
    "soundCategory_neutral": "0.0",
    "soundCategory_player": "0.0",
    "soundCategory_ambient": "0.0",
    "soundCategory_voice": "0.0",
    "lang": "ru_ru",
    "onboardAccessibility": "false",
    "skipMultiplayerWarning": "true",
    "joinedFirstServer": "true",
    "tutorialStep": "none",
    "narrator": "0",
}


# Mods that only draw things for a human player: recipe viewer, HUD tooltips, mouse and keybind tweaks, and the
# experimental entity-render accelerator (big GPU buffers, ~0.7 GB of committed memory less without it).
# Altron plays through code, so his client runs without them and needs less memory; his abilities stay the same.
# Tested: the server accepts such a client (Xaero's map must stay: the server requires its network channel).
# "bot_lite": false in config.json turns this off; if a server refuses the lite client, the brain retries with all mods.
# Simple Voice Chat is skipped too: Altron does not listen through his own client (the host's plugin hears
# everyone), and with it his window played the voices a second time.
BOT_SKIP_MODS = ("jei-", "jade-", "appleskin", "enchantmentdescriptions", "mousetweaks", "controlling", "searchables",
                 "acceleratedrendering", "voicechat-")
# ModernFix memory savers for the bot: block models are baked when first needed instead of all at start,
# duplicate resource names are shared, the huge fallback unicode font is not kept (Russian is in the main font)
BOT_MODERNFIX = {"mixin.perf.dynamic_resources": "true", "mixin.perf.deduplicate_location": "true",
                 "mixin.feature.disable_unihex_font": "true"}
# The JVM gives memory it does not use back to Windows (periodic GC + small free-heap ratio), shares equal strings
JVM_LEAN = ["-XX:+UseG1GC", "-XX:+UseStringDeduplication", "-XX:MaxGCPauseMillis=50", "-XX:+ParallelRefProcEnabled",
            "-XX:G1PeriodicGCInterval=20000", "-XX:MinHeapFreeRatio=10", "-XX:MaxHeapFreeRatio=25"]


# Three ways to run Altron. Only his own client and the AI server change: the commander's game is never touched
# (Altron plays from his own folder altron\bot with its own options.txt and configs).
PROFILES = {
    "eco": {
        "title_en": "Eco — the least resources",
        "about_en": "light client, the window is drawn only when needed, view distance 3 chunks, 2 GB Java, fewer vision rays, low priority, a shorter AI conversation memory",
        "title": "Экономный — минимум ресурсов",
        "about": "облегчённый клиент, окно рисуется только по надобности, дальность 3 чанка, 2 ГБ Java, "
                 "меньше лучей зрения, низкий приоритет, короче память разговора ИИ",
        "bot_lite": True, "bot_memory_mb": 2048, "bot_render_distance": 3, "bot_simulation_distance": 4,
        "bot_rays": 25, "bot_render_always": False, "bot_low_priority": True,
        # the instructions and 80 tools alone take ~15k tokens: with 16k the talk had no room and every answer failed
        "llm_context": 24576, "llm_cache_ram_mb": 512, "history_chars": 9000,
    },
    "balanced": {
        "title_en": "Balanced (normal)",
        "about_en": "light client, the window is drawn only when needed, view distance 4 chunks, 2.5 GB Java, low priority",
        "title": "Сбалансированный (обычный)",
        "about": "облегчённый клиент, окно рисуется только по надобности, дальность 4 чанка, 2,5 ГБ Java, "
                 "низкий приоритет",
        "bot_lite": True, "bot_memory_mb": 2560, "bot_render_distance": 4, "bot_simulation_distance": 5,
        "bot_rays": 50, "bot_render_always": False, "bot_low_priority": True,
        "llm_context": 24576, "llm_cache_ram_mb": 1024, "history_chars": 20000,
    },
    "max": {
        "title_en": "Max — everything at full",
        "about_en": "all the pack's mods, the window is always drawn (30 FPS, you can look through his eyes), view distance 8 chunks, 3.5 GB Java, more vision rays, normal priority, a long AI conversation memory",
        "title": "Максимальный — всё на полную",
        "about": "все моды сборки, окно рисуется всегда (30 FPS, можно смотреть его глазами), дальность 8 чанков, "
                 "3,5 ГБ Java, больше лучей зрения, обычный приоритет, длинная память разговора ИИ",
        "bot_lite": False, "bot_memory_mb": 3584, "bot_render_distance": 8, "bot_simulation_distance": 8,
        "bot_rays": 100, "bot_render_always": True, "bot_low_priority": False,
        "llm_context": 32768, "llm_cache_ram_mb": 2048, "history_chars": 40000,
    },
}
PROFILE_KEYS = {"eco": "eco", "эконом": "eco", "1": "eco", "balanced": "balanced", "баланс": "balanced", "2": "balanced",
                "max": "max", "макс": "max", "3": "max"}


def apply_profile(cfg, name):
    """Settings of a launch mode on top of config.json."""
    name = PROFILE_KEYS.get(str(name).lower(), "balanced")
    cfg.update({k: v for k, v in PROFILES[name].items() if k not in ("title", "about", "title_en", "about_en")})
    cfg["profile"] = name
    return name


def server_address(text, default_port=25565):
    """ "26.1.2.3" -> "26.1.2.3:25565"; "localhost:25566" -> "127.0.0.1:25566" (IPv4: see Config.SERVER in the mod)."""
    text = str(text).strip().replace(" ", "")
    if isinstance(text, str) and text.isdigit():
        return "127.0.0.1:%s" % text
    host, _, port = text.partition(":")
    if host.lower() == "localhost":
        host = "127.0.0.1"
    return "%s:%s" % (host, port or default_port)


BUILT_MOD = BRAIN_DIR.parent / "mod" / "build" / "libs" / "altron-0.1.0.jar"
MOD_NAME = "altron-0.1.0.jar"


def _in_use(path):
    """Is a running program (Minecraft) holding this file open? Checked by asking Windows for sole access."""
    import ctypes
    handle = ctypes.windll.kernel32.CreateFileW(str(path), 0x80000000, 0, None, 3, 0, None)   # GENERIC_READ, no sharing
    if handle == ctypes.c_void_p(-1).value:
        return ctypes.windll.kernel32.GetLastError() == 32   # ERROR_SHARING_VIOLATION
    ctypes.windll.kernel32.CloseHandle(handle)
    return False


def _newer_build(pack_jar):
    return BUILT_MOD.exists() and (not pack_jar.exists() or BUILT_MOD.stat().st_mtime > pack_jar.stat().st_mtime + 1)


def install_new_mod(cfg, log=print):
    """A freshly built Altron mod (altron\\mod\\build\\libs) goes into the pack by itself — but never under a running
    Minecraft (it reads classes from the jar while playing). Altron's own client gets it right away anyway."""
    target = rel(cfg["minecraft_dir"]) / "versions" / cfg["pack_version"] / "mods" / MOD_NAME
    if not _newer_build(target):
        return
    if target.exists() and _in_use(target):
        log("Новая версия мода: Альтрон получит её сразу, твоя игра — после её перезапуска.")
        return
    try:
        shutil.copy2(BUILT_MOD, target)
        log("Установил новую версию мода Альтрона в сборку.")
    except OSError as e:
        log("Не смог поставить новую версию мода в сборку (%s)." % e)


def rel(p):
    p = Path(os.path.expandvars(os.path.expanduser(str(p))))
    return p if p.is_absolute() else (BRAIN_DIR / p).resolve()


# Minecraft's own language codes for the bot's client (item names he reads follow it)
MC_LANG = {"ru": "ru_ru", "en": "en_us", "uk": "uk_ua", "be": "be_by", "kk": "kk_kz", "de": "de_de", "fr": "fr_fr",
           "es": "es_es", "it": "it_it", "pl": "pl_pl", "pt": "pt_br", "tr": "tr_tr", "cs": "cs_cz", "nl": "nl_nl",
           "zh": "zh_cn", "ja": "ja_jp", "ko": "ko_kr"}


def primary_language(cfg):
    """The language Altron starts in: a fixed "language", or the first of "languages" when it is "auto"."""
    lang = str(cfg.get("language", "auto")).lower()
    if lang != "auto":
        return lang
    return (cfg.get("languages") or ["en"])[0]


def default_minecraft_dir():
    if os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / ".minecraft"
    mac = Path.home() / "Library" / "Application Support" / "minecraft"
    return mac if mac.exists() else Path.home() / ".minecraft"


def modded_versions(mc):
    """Installed game versions that carry mods: a folder in versions/ with its own json and a mods folder."""
    vers = mc / "versions"
    if not vers.is_dir():
        return []
    return sorted(d.name for d in vers.iterdir() if (d / (d.name + ".json")).exists() and (d / "mods").is_dir())


def find_java(mc):
    """Java 17 that Minecraft 1.20 ships with (the launcher's runtime), else the one on PATH."""
    for pattern in ("runtime/java-runtime-gamma/*/java-runtime-gamma/bin/javaw.exe",
                    "runtime/java-runtime-gamma/*/java-runtime-gamma/bin/java",
                    "runtime/java-runtime-gamma/*/java-runtime-gamma/jre.bundle/Contents/Home/bin/java"):
        found = sorted(mc.glob(pattern))
        if found:
            return str(found[0])
    return shutil.which("javaw") or shutil.which("java") or ""


def resolve_install(cfg, remembered_pack="", ask=False, world=""):
    """Fill in the Minecraft folder, the modpack and Java when config.json leaves them empty (or they moved):
    any Forge 1.20.1 pack works, not only the one Altron was first made for. world: prefer the pack that has this
    save. Returns the pack's name."""
    mc = rel(cfg["minecraft_dir"]) if cfg.get("minecraft_dir") else None
    if mc is None or not mc.is_dir():
        mc = default_minecraft_dir()
    cfg["minecraft_dir"] = str(mc)
    packs = modded_versions(mc)
    if world and not cfg.get("pack_version"):
        packs = [p for p in packs if (mc / "versions" / p / "saves" / world).is_dir()] + \
                [p for p in packs if not (mc / "versions" / p / "saves" / world).is_dir()]
    pack = cfg.get("pack_version") or remembered_pack
    if pack not in packs:
        if not packs:
            raise SystemExit(ui("В %s нет ни одной сборки с модами (versions/<имя>/mods). Укажи minecraft_dir и "
                                "pack_version в config.json.",
                                "No modpack with mods in %s (versions/<name>/mods). Set minecraft_dir and pack_version "
                                "in config.json.") % mc)
        pack = packs[0]
        if len(packs) > 1 and ask:
            print(ui("\nВ какой сборке играть с Альтроном?", "\nWhich modpack should Altron play in?"))
            for i, name in enumerate(packs, 1):
                print("  %d — %s" % (i, name))
            answer = input(ui("Сборка [1-%d]: ", "Modpack [1-%d]: ") % len(packs)).strip()
            if answer.isdigit() and 1 <= int(answer) <= len(packs):
                pack = packs[int(answer) - 1]
    cfg["pack_version"] = pack
    if not cfg.get("java") or not rel(cfg["java"]).exists():
        cfg["java"] = find_java(mc)
        if not cfg["java"]:
            raise SystemExit(ui("Не нашёл Java 17. Установи её или укажи путь в config.json (\"java\").",
                                "Java 17 not found. Install it or set its path in config.json (\"java\")."))
    return pack


def offline_uuid(name):
    """Same UUID an offline-mode server assigns (Java's UUID.nameUUIDFromBytes)."""
    h = bytearray(hashlib.md5(("OfflinePlayer:" + name).encode("utf-8")).digest())
    h[6] = (h[6] & 0x0F) | 0x30
    h[8] = (h[8] & 0x3F) | 0x80
    return uuid.UUID(bytes=bytes(h)).hex


def _rules_ok(rules):
    if not rules:
        return True
    allowed = False
    for r in rules:
        match = True
        os_rule = r.get("os") or {}
        if os_rule.get("name") and os_rule["name"] != "windows":
            match = False
        if r.get("features"):
            match = False  # demo, custom resolution, quick play: not used
        if match:
            allowed = r.get("action") == "allow"
    return allowed


def _patch_key_values(path, values, sep):
    lines = []
    if path.exists():
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    seen = set()
    out = []
    for line in lines:
        key = line.split(sep, 1)[0].strip() if sep in line and not line.startswith("#") else None
        if key in values:
            out.append(key + sep + values[key])
            seen.add(key)
        else:
            out.append(line)
    for k, v in values.items():
        if k not in seen:
            out.append(k + sep + v)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def prepare_bot_dir(cfg, log=print, game_dir=None, voice=False, options=None, lite=False):
    """Game folder for an extra client (the bot, or the demo host): hard-linked mods, copied configs.
    lite: the bot's memory-saving setup (no purely visual mods, ModernFix memory options)."""
    mc = rel(cfg["minecraft_dir"])
    pack = mc / "versions" / cfg["pack_version"]
    bot = rel(game_dir or cfg["bot_dir"])
    mods = bot / "mods"
    mods.mkdir(parents=True, exist_ok=True)

    skip = tuple(s.lower() for s in cfg.get("bot_skip_mods", BOT_SKIP_MODS)) if lite else ()
    if voice:
        skip = tuple(s for s in skip if not s.startswith("voicechat"))   # he talks through his own voice chat client
    wanted = {jar.name: jar for jar in (pack / "mods").glob("*.jar") if not (skip and jar.name.lower().startswith(skip))}
    for extra in cfg.get("extra_bot_mods", []):
        if "baritone" in str(extra).lower():
            continue   # an old config: his body has no Baritone any more
        p = rel(extra)
        if p.exists():
            wanted[p.name] = p
        else:
            log("Нет мода для бота: %s" % p)
    if MOD_NAME not in wanted and BUILT_MOD.exists():
        wanted[MOD_NAME] = BUILT_MOD   # another pack without Altron's mod: his body brings its own

    # Mods are hard links to the pack's files: no extra disk space, always in sync
    for existing in mods.glob("*.jar"):
        if existing.name not in wanted:
            existing.unlink()
    # a new Altron mod that could not go into the pack yet (the commander's game holds the old one): this client gets
    # its own copy now; the commander's game keeps the old jar until it restarts (the parts they share did not change)
    if MOD_NAME in wanted and _newer_build(wanted[MOD_NAME]):
        dst = mods / MOD_NAME
        try:
            if not (dst.exists() and not os.path.samefile(wanted[MOD_NAME], dst)
                    and dst.stat().st_mtime >= BUILT_MOD.stat().st_mtime):
                if dst.exists():
                    dst.unlink()   # only this client's name for the file; the pack's jar stays as it is
                shutil.copy2(BUILT_MOD, dst)
            del wanted[MOD_NAME]
        except OSError as e:
            log("Не смог дать Альтрону новую версию мода (%s), будет старая." % e)
    for name, src in wanted.items():
        dst = mods / name
        if dst.exists():
            try:
                if os.path.samefile(src, dst):
                    continue
            except OSError:
                pass
            dst.unlink()
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)

    for d in ("config", "defaultconfigs"):
        if (pack / d).exists():
            shutil.copytree(pack / d, bot / d, dirs_exist_ok=True)
    if (pack / "tacz").exists() and not (bot / "tacz").exists():
        shutil.copytree(pack / "tacz", bot / "tacz")

    # The bot must not play sounds or open a microphone: its voice comes from the brain
    _patch_key_values(bot / "config" / "voicechat" / "voicechat-client.properties",
                      {"disabled": "false" if voice else "true", "muted": "true", "onboarding_finished": "true"}, "=")
    if voice and options is None:
        # Altron's own voice chat on someone else's server: the real microphone of this PC is never sent (muted,
        # push-to-talk), and the voices he hears are not played through the speakers a second time
        _patch_key_values(bot / "config" / "voicechat" / "voicechat-client.properties",
                          {"voice_chat_volume": "0.0", "microphone_activation_type": "PTT"}, "=")
    opts = dict(options or BOT_OPTIONS)
    if options is None:
        opts["renderDistance"] = str(cfg.get("bot_render_distance", 4))
        opts["simulationDistance"] = str(cfg.get("bot_simulation_distance", 5))
        opts["lang"] = cfg.get("game_lang") or MC_LANG.get(primary_language(cfg), "en_us")
    _patch_key_values(bot / "options.txt", opts, ":")
    if lite:
        _patch_key_values(bot / "config" / "modernfix-mixins.properties", BOT_MODERNFIX, "=")
    return bot


def build_command(cfg, server, name=None, game_dir=None, props=None, memory_mb=None, size=(854, 480)):
    mc = rel(cfg["minecraft_dir"])
    vid = cfg["pack_version"]
    vdir = mc / "versions" / vid
    data = json.loads((vdir / (vid + ".json")).read_text(encoding="utf-8"))
    bot = rel(game_dir or cfg["bot_dir"])
    name = name or cfg["bot_name"]
    libdir = mc / "libraries"

    cp = []
    for lib in data.get("libraries", []):
        if not _rules_ok(lib.get("rules")):
            continue
        art = lib.get("artifact") or (lib.get("downloads") or {}).get("artifact")
        if not art:
            continue
        path = art["path"]
        full = mc / path if path.startswith("libraries/") else libdir / path
        if full.exists() and str(full) not in cp:
            cp.append(str(full))
    cp.append(str(vdir / (vid + ".jar")))

    subst = {
        "${natives_directory}": str(vdir / "natives"),
        "${launcher_name}": "altron",
        "${launcher_version}": "1.0",
        "${classpath}": ";".join(cp),
        "${classpath_separator}": ";",
        "${library_directory}": str(libdir),
        "${version_name}": vid,
        "${auth_player_name}": name,
        "${game_directory}": str(bot),
        "${assets_root}": str(mc / "assets"),
        "${assets_index_name}": data.get("assets", "5"),
        "${auth_uuid}": offline_uuid(name),
        "${auth_access_token}": "0",
        "${user_type}": "legacy",
        "${version_type}": "release",
    }

    def expand(entries):
        out = []
        for e in entries:
            if isinstance(e, str):
                vals = [e]
            else:
                if not _rules_ok(e.get("rules")):
                    continue
                vals = e.get("values", e.get("value", []))
                if isinstance(vals, str):
                    vals = [vals]
            for v in vals:
                for k, s in subst.items():
                    v = v.replace(k, s)
                out.append(v)
        return out

    jvm = expand(data["arguments"]["jvm"])
    game = expand(data["arguments"]["game"])
    # Drop args whose value is an unresolved placeholder (clientid, xuid...)
    clean = []
    skip = False
    for i, a in enumerate(game):
        if skip:
            skip = False
            continue
        if a.startswith("--") and i + 1 < len(game) and "${" in game[i + 1]:
            skip = True
            continue
        clean.append(a)

    extra = [
        "-Xmx%dM" % (memory_mb or cfg.get("bot_memory_mb", 2560)),
        "-Xms512M",
    ] + JVM_LEAN + [
        "-Dfile.encoding=UTF-8",
        "-Daltron.name=" + cfg["bot_name"],
        "-Daltron.brainPort=%d" % cfg["brain_port"],
    ]
    if props is None:  # the bot
        extra += ["-Daltron.bot=true", "-Daltron.server=" + server_address(server),
                  "-Daltron.rays=%d" % int(cfg.get("bot_rays", 50))] + list(cfg.get("bot_jvm_extra", []))
        if cfg.get("bot_third_person"):
            extra.append("-Daltron.thirdPerson=true")
        if cfg.get("bot_render_always"):
            extra.append("-Daltron.render=true")   # recorded demos film his window: keep drawing it
        if cfg.get("bot_voice"):
            extra.append("-Daltron.voice=true")    # someone else's server: hears and speaks through his voice chat
    else:
        extra += ["-D%s=%s" % kv for kv in props.items()]
    return [str(rel(cfg["java"]))] + extra + jvm + [data["mainClass"]] + clean + ["--width", str(size[0]), "--height", str(size[1])]


def launch_bot(cfg, server, log=print, lite=True):
    """server: the LAN port of the commander's world, or "ip:port" of a server (Radmin VPN)."""
    lite = lite and cfg.get("bot_lite", True)
    # his voice chat client is on (muted, sound off) also in the commander's own world: then he shows up in the voice
    # group «Альтрон» next to the commander. He still hears and speaks through the host's plugin there.
    bot = prepare_bot_dir(cfg, log, lite=lite, voice=bool(cfg.get("bot_voice") or cfg.get("bot_in_voice_group", True)))
    size = tuple(cfg.get("bot_window", (854, 480)))
    cmd = build_command(cfg, server, size=size)
    (bot / "logs").mkdir(exist_ok=True)
    logf = open(bot / "logs" / "altron-launch.log", "w", encoding="utf-8", errors="replace")
    log("Запускаю клиент Альтрона (ник %s, сервер %s, режим «%s»%s)..." % (
        cfg["bot_name"], server_address(server), PROFILES.get(cfg.get("profile", "balanced"), PROFILES["balanced"])["title"],
        ", облегчённый" if lite else ", все моды"))
    # the commander's game comes first: Windows gives Altron's client the processor only when it is free
    flags = subprocess.BELOW_NORMAL_PRIORITY_CLASS if cfg.get("bot_low_priority", True) else 0
    return subprocess.Popen(cmd, cwd=str(bot), stdout=logf, stderr=subprocess.STDOUT, creationflags=flags)


# The demo host is not watched: render as little as possible (10 FPS), but keep the server's view distance
HOST_OPTIONS = dict(BOT_OPTIONS, renderDistance="6", maxFps="10", lang="ru_ru",
                    **{k: "1.0" for k in BOT_OPTIONS if k.startswith("soundCategory_")})
HOST_OPTIONS["soundCategory_master"] = "0.3"


def launch_host(cfg, world, name="MJreggich", log=print, live=False):
    """Host client for demos and tests: opens (or creates) `world` and shares it with the bot.
    live: the commander plays in it himself — a normal window and frame rate, his real microphone on (voice activation)."""
    options = dict(HOST_OPTIONS, maxFps="60", renderDistance="6", soundCategory_master="1.0") if live else HOST_OPTIONS
    host = prepare_bot_dir(cfg, log, game_dir=cfg.get("host_dir", "../host"), voice=True, options=options)
    # a bigger jitter buffer: Altron's voice stays smooth even when the PC is busy
    voice = {"output_buffer_size": "12", "audio_packet_threshold": "6"}
    if live:
        voice.update({"muted": "false", "microphone_activation_type": "VOICE", "voice_chat_volume": "1.0"})
    _patch_key_values(host / "config" / "voicechat" / "voicechat-client.properties", voice, "=")
    cmd = build_command(cfg, "127.0.0.1:0", name=name, game_dir=host, props={"altron.autoWorld": world},
                        memory_mb=cfg.get("host_memory_mb", 4096), size=(1280, 720) if live else (854, 480))
    (host / "logs").mkdir(exist_ok=True)
    logf = open(host / "logs" / "altron-launch.log", "w", encoding="utf-8", errors="replace")
    log("Запускаю игру-хозяина (ник %s, мир %s)..." % (name, world))
    return subprocess.Popen(cmd, cwd=str(host), stdout=logf, stderr=subprocess.STDOUT)
