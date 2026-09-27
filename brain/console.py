"""Talk to a running Altron brain from another window (it listens on 127.0.0.1:47800).

    console.py say "Альтрон, иди за мной"      — speak as the commander
    console.py bot status                      — ask the body (any bot command, args as JSON: bot find_block '{"block":"chest"}')
    console.py state                           — what the brain is doing now
"""
import json
import socket
import sys

from launcher import BRAIN_DIR


def main(argv):
    cfg = json.loads((BRAIN_DIR / "config.json").read_text(encoding="utf-8"))
    port = int(argv[argv.index("--port") + 1]) if "--port" in argv else cfg["brain_port"]
    argv = [a for i, a in enumerate(argv) if a != "--port" and (i == 0 or argv[i - 1] != "--port")]
    if not argv:
        print(__doc__)
        return
    if argv[0] == "say":
        msg = {"type": "say", "text": " ".join(argv[1:])}
    elif argv[0] == "probe":
        msg = {"type": "probe", "args": json.loads(argv[1]) if len(argv) > 1 else {}}
    elif argv[0] in ("bot", "task"):
        msg = {"type": argv[0], "name": argv[1] if len(argv) > 1 else "status", "args": json.loads(argv[2]) if len(argv) > 2 else {}}
    else:
        msg = {"type": "state"}
    with socket.create_connection(("127.0.0.1", port), timeout=120) as s:
        f = s.makefile("rwb")
        for m in ({"type": "hello", "role": "console"}, msg):
            f.write((json.dumps(m, ensure_ascii=False) + "\n").encode("utf-8"))
        f.flush()
        reply = json.loads(f.readline().decode("utf-8"))
    r = reply.get("result")
    if isinstance(r, dict) and r.get("image"):
        import base64
        out = BRAIN_DIR / "logs" / "altron_view.png"
        out.write_bytes(base64.b64decode(r.pop("image")))
        r["saved"] = str(out)
    print(r if isinstance(r, str) else json.dumps(r, ensure_ascii=False, indent=1)[:6000])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1:])
