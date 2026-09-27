"""A normal Altron session (voice, real long-term memory — he learns) in a copy of a pack world, with the host game
opened by the brain: the commander can sit at the host window and play and talk, or orders can be given from here.

    session.py "Los Perrito" [--order "Альтрон, изучи моё производство"]

The copy is altron\\host\\saves\\AltronLive_<name>: made once and kept, so what Altron learns there (the blocks he saw,
the production map, places) stays for the next session. The original world is never touched.
Type phrases in this window to speak as the commander; /quit ends the session.
"""
import asyncio
import json
import shutil
import sys
import threading

import altron
from launcher import BRAIN_DIR, launch_host, rel

COMMANDER = "MJreggich"


async def main(argv):
    src_name = argv[0]
    order = argv[argv.index("--order") + 1] if "--order" in argv else ""
    cfg = json.loads((BRAIN_DIR / "config.json").read_text(encoding="utf-8"))
    world = "AltronLive_%s" % src_name.replace(" ", "")
    dst = rel(cfg.get("host_dir", "../host")) / "saves" / world
    if not dst.exists():
        src = rel(cfg["minecraft_dir"]) / "versions" / cfg["pack_version"] / "saves" / src_name
        print("Копирую мир %s -> %s (один раз, дальше он сохраняется) ..." % (src, dst), flush=True)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("session.lock"))
    hub = altron.Hub(cfg)
    hub.loop = asyncio.get_running_loop()
    altron.install_new_mod(cfg, hub.log)
    llm_proc = altron.start_llm(cfg, hub.log)

    def load_models():
        from knowledge import Knowledge
        from speech import STT, TTS
        hub.knowledge = Knowledge.load(cfg, hub.log)
        hub.tts = TTS(cfg)
        hub.stt = STT(cfg, hub.log)
        hub.log("Слух и голос готовы (распознавание речи: %s)." % hub.stt.device)

    await asyncio.to_thread(load_models)
    server = await altron.listen(hub, cfg["brain_port"])
    threading.Thread(target=hub.console_thread, daemon=True).start()
    tasks = [asyncio.create_task(server.serve_forever()), asyncio.create_task(hub.voice_loop()),
             asyncio.create_task(hub.agent_loop())]
    await altron.wait_llm(cfg, hub.log)
    host_proc = launch_host(cfg, world, COMMANDER, hub.log, live=True)
    hub.log("Игра командира открывает мир %s — в её окне можно играть и говорить в Voice Chat." % world)
    try:
        for _ in range(900):
            if hub.joined:
                break
            await asyncio.sleep(1)
        if order and hub.joined:
            await asyncio.sleep(10)
            hub.log("%s: %s" % (COMMANDER, order))
            await hub.handle_phrase(COMMANDER, order)
        while host_proc.poll() is None:   # until the commander's game is closed
            await asyncio.sleep(5)
        hub.log("Игра командира закрыта — заканчиваю сессию.")
    finally:
        hub.stop_bot()
        if host_proc.poll() is None:
            host_proc.terminate()
        if llm_proc is not None:
            llm_proc.terminate()
        for t in tasks:
            t.cancel()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        asyncio.run(main(sys.argv[1:]))
    except KeyboardInterrupt:
        pass
