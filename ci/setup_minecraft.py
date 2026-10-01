"""A Minecraft folder for the field test on a Linux machine with no launcher (GitHub Actions): vanilla 1.20.1 (the
client jar, its libraries and assets from Mojang), Forge from its own installer, and one "pack" made of the two, the way
the commander's launcher lays it out — versions/<pack>/<pack>.json with everything in it, <pack>.jar and a mods folder.

    python ci/setup_minecraft.py <minecraft dir> <forge version> [pack name]
"""
import json
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
ASSETS = "https://resources.download.minecraft.net/%s/%s"
FORGE = "https://maven.minecraftforge.net/net/minecraftforge/forge/%s/forge-%s-installer.jar"
MC = "1.20.1"


def get(url, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                return r.read()
        except OSError:
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


def fetch(url, path):
    path = Path(path)
    if path.exists() and path.stat().st_size > 0:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    data = get(url)
    # its own temporary name in each thread: the asset index lists some files twice
    tmp = path.with_name("%s.%d.part" % (path.name, threading.get_ident()))
    tmp.write_bytes(data)
    tmp.replace(path)


def rules_ok(rules):
    if not rules:
        return True
    allowed = False
    for r in rules:
        name = (r.get("os") or {}).get("name")
        if (not name or name == "linux") and not r.get("features"):
            allowed = r.get("action") == "allow"
    return allowed


def vanilla(mc):
    ver = next(v for v in json.loads(get(MANIFEST))["versions"] if v["id"] == MC)
    vjson = json.loads(get(ver["url"]))
    vdir = mc / "versions" / MC
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / (MC + ".json")).write_text(json.dumps(vjson, indent=1))
    fetch(vjson["downloads"]["client"]["url"], vdir / (MC + ".jar"))
    jobs = []
    for lib in vjson["libraries"]:
        art = (lib.get("downloads") or {}).get("artifact")
        if art and rules_ok(lib.get("rules")):
            jobs.append((art["url"], mc / "libraries" / art["path"]))
    index = json.loads(get(vjson["assetIndex"]["url"]))
    (mc / "assets" / "indexes").mkdir(parents=True, exist_ok=True)
    (mc / "assets" / "indexes" / (vjson["assetIndex"]["id"] + ".json")).write_text(json.dumps(index))
    for obj in index["objects"].values():
        h = obj["hash"]
        jobs.append((ASSETS % (h[:2], h), mc / "assets" / "objects" / h[:2] / h))
    jobs = list(dict((str(p), (u, p)) for u, p in jobs).values())   # each file once
    print("vanilla %s: %d files to fetch" % (MC, len(jobs)), flush=True)
    with ThreadPoolExecutor(32) as pool:
        list(pool.map(lambda j: fetch(*j), jobs))
    return vjson


def forge(mc, forge_version):
    full = "%s-%s" % (MC, forge_version)
    installer = mc / ("forge-%s-installer.jar" % full)
    fetch(FORGE % (full, full), installer)
    profiles = mc / "launcher_profiles.json"
    if not profiles.exists():
        profiles.write_text('{"profiles": {}}')   # the installer wants to see a launcher here
    subprocess.run(["java", "-jar", str(installer), "--installClient", str(mc)], check=True, cwd=str(mc))
    fid = "%s-forge-%s" % (MC, forge_version)
    return json.loads((mc / "versions" / fid / (fid + ".json")).read_text())


def merge(vanilla_json, forge_json, pack):
    """One version json the way a modpack launcher writes it: Forge's libraries first, then vanilla's; both sets of
    arguments; Forge's main class; vanilla's assets."""
    out = dict(vanilla_json)
    names = set()
    libs = []
    for lib in forge_json.get("libraries", []) + vanilla_json.get("libraries", []):
        key = lib["name"]
        if key not in names:
            names.add(key)
            libs.append(lib)
    out["libraries"] = libs
    out["mainClass"] = forge_json["mainClass"]
    args = {k: list(vanilla_json.get("arguments", {}).get(k, [])) for k in ("game", "jvm")}
    for k in ("game", "jvm"):
        args[k] += forge_json.get("arguments", {}).get(k, [])
    out["arguments"] = args
    out["id"] = pack
    out.pop("inheritsFrom", None)
    return out


def main():
    mc = Path(sys.argv[1]).resolve()
    forge_version = sys.argv[2]
    pack = sys.argv[3] if len(sys.argv) > 3 else "CIPack"
    v = vanilla(mc)
    f = forge(mc, forge_version)
    pdir = mc / "versions" / pack
    (pdir / "mods").mkdir(parents=True, exist_ok=True)
    (pdir / "saves").mkdir(exist_ok=True)
    (pdir / (pack + ".json")).write_text(json.dumps(merge(v, f, pack), indent=1))
    (pdir / (pack + ".jar")).write_bytes((mc / "versions" / MC / (MC + ".jar")).read_bytes())
    print("pack %s ready in %s" % (pack, pdir), flush=True)


if __name__ == "__main__":
    main()
