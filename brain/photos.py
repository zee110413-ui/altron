"""Photograph a base with Altron's eyes, line by line (for working out and checking what goes where).

    photos.py [--centers "x,y,z;x,y,z"] [--radius 40]

Needs a running brain (session.py): talks to it like console.py. Altron is made a spectator for the shoot (to put the
camera anywhere, through roofs), each production line is taken from two sides from above, each building from its four
inner corners; then he is back in survival next to the commander. Pictures: brain\\logs\\photos\\*.png, and
brain\\logs\\photos\\index.json says what each one shows.
"""
import json
import re
import socket
import sys
import time

from launcher import BRAIN_DIR

OUT = BRAIN_DIR / "logs" / "photos"


def call(msg, timeout=120):
    s = socket.create_connection(("127.0.0.1", 47800), timeout=timeout)
    f = s.makefile("rwb")
    for m in ({"type": "hello", "role": "console"}, msg):
        f.write((json.dumps(m, ensure_ascii=False) + "\n").encode("utf-8"))
    f.flush()
    r = json.loads(f.readline().decode("utf-8"))["result"]
    s.close()
    return r


def contents_from_memory():
    import production
    mem = json.loads((BRAIN_DIR / "memory" / "memory.json").read_text(encoding="utf-8"))
    out = {}
    for les in mem.get("lessons", []):
        if les["kind"] != "production":
            continue
        m = re.search(r"@(-?\d+),(-?\d+),(-?\d+)", les["key"])
        if m:
            inside = les["text"].split("Внутри:")[-1].split(". По справочнику")[0]
            out[tuple(int(v) for v in m.groups())] = [(int(n), i) for n, i in production.ITEM_RE.findall(inside)]
    return out


def main(argv):
    import production
    centers = [[1716, 72, 778], [1760, 72, 737]]
    if "--centers" in argv:
        centers = [[int(v) for v in c.split(",")] for c in argv[argv.index("--centers") + 1].split(";")]
    radius = int(argv[argv.index("--radius") + 1]) if "--radius" in argv else 40
    blocks, seen = [], set()
    for c in centers:
        r = call({"type": "bot", "name": "production_map", "args": {"radius": radius, "x": c[0], "y": c[1], "z": c[2]}})
        for b in r.get("blocks", []):
            p = tuple(int(v) for v in b["pos"])
            if p not in seen:
                seen.add(p)
                blocks.append(b)
    res = production.analyze(blocks, contents_from_memory())
    OUT.mkdir(parents=True, exist_ok=True)
    state = call({"type": "state"})
    owner = (state.get("state") or {}).get("owner_pos") or centers[0]
    shots = []   # (name, target, what, how far he may step back for a clear view)
    nodes = res["nodes"]
    for z in res["lines"]:
        ms = [p for p in z["nodes"] if nodes[p].kind == "machine"]
        st = sorted(p for p in z["nodes"] if nodes[p].kind in ("store", "tank"))
        if ms:
            c = [sum(p[i] for p in ms) / len(ms) + .5 for i in range(3)]
            shots.append(("line%02d_machines" % z["n"], c, "здание %d, линия %d «%s»: машины" % (z["building"], z["n"], z["title"]), 8))
        for k, p in enumerate([st[0], st[-1]] if len(st) > 1 else st):
            shots.append(("line%02d_store%d" % (z["n"], k + 1), [p[0] + .5, p[1] + .5, p[2] + .5],
                          "здание %d, линия %d: %s %d %d %d" % (z["building"], z["n"], nodes[p].name, *p), 7))
    for b in res["buildings"]:
        if b["size"] >= 20:
            cx, cy, cz = b["center"]
            shots.append(("building%d_overview" % b["n"], [cx + .5, cy + .5, cz + .5], "здание %d целиком" % b["n"], 14))
    print("снимков: %d" % len(shots), flush=True)
    for old in OUT.glob("*.png"):
        old.unlink()
    call({"type": "probe", "args": {"bot_gamemode": "spectator"}})
    index = []
    try:
        for name, look, what, max_r in shots:
            # to the spot first (the server moves him), then he finds where to take it from himself
            call({"type": "probe", "args": {"tp_bot": [look[0], look[1] + 2, look[2]]}})
            time.sleep(1.5)
            path = str(OUT / (name + ".png"))
            r = call({"type": "task", "name": "photo", "args": {"x": look[0], "y": look[1], "z": look[2], "file": path,
                                                               "auto": True, "max_r": max_r}})
            print(name, "-", r, flush=True)
            index.append({"file": name + ".png", "what": what, "look": look, "result": r})
    finally:
        call({"type": "probe", "args": {"bot_gamemode": "survival", "tp_bot": [owner[0] + 1.5, owner[1], owner[2] + 1.5]}})
    (OUT / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "lines.txt").write_text("\n".join(production.describe(res, 40)), encoding="utf-8")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1:])
