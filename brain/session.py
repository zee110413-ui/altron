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
from launcher import BRAIN_DIR, apply_profile, launch_host, rel, resolve_install

COMMANDER = "MJreggich"


async def main(argv):
    src_name = argv[0]
    order = argv[argv.index("--order") + 1] if "--order" in argv else ""
    cfg = json.loads((BRAIN_DIR / "config.json").read_text(encoding="utf-8"))
    resolve_install(cfg, world=src_name)   # the pack that has this world, found when config.json leaves it empty
    world = "AltronLive_%s" % src_name.replace(" ", "")
    dst = rel(cfg.get("host_dir", "../host")) / "saves" / world
    if not dst.exists():
        src = rel(cfg["minecraft_dir"]) / "versions" / cfg["pack_version"] / "saves" / src_name
        print("Копирую мир %s -> %s (один раз, дальше он сохраняется) ..." % (src, dst), flush=True)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("session.lock"))
    # the commander plays in the host window on this same PC: Altron's body and AI in the light mode (memory)
    apply_profile(cfg, cfg.get("session_profile", "eco"))
    hub = altron.Hub(cfg)
    hub.loop = asyncio.get_running_loop()
    hub.attach_mode = "--attach" in argv   # before the game can connect: no second body on its "ready"
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
             asyncio.create_task(hub.agent_loop()), asyncio.create_task(heartbeat(hub)),
             asyncio.create_task(keep_body(hub)),
             # small talk when it is quiet, and «живи сам» (his own jobs while the commander is away)
             asyncio.create_task(hub.chatter_loop()), asyncio.create_task(hub.life_loop()),
             asyncio.create_task(hub.observe_loop())]   # his goals: he looks around and the AI decides
    await altron.wait_llm(cfg, hub.log)
    if "--attach" in argv:
        # a new brain for the game and body that are still running: they come back to this port by themselves
        await attach(hub)
        return
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


async def keep_body(hub):
    """Altron's body (his game client) fell out while the commander's game is open: start it again (at most once in
    3 minutes). It joins the same world and the brain carries on."""
    from launcher import launch_bot
    gone_since, last_start = None, 0.0
    import time
    while True:
        await asyncio.sleep(10)
        if hub.host is None or hub.bot is not None:
            gone_since = None
            continue
        if hub.bot_proc is not None and hub.bot_proc.poll() is None:
            gone_since = None   # his client is still starting (a fresh session takes a minute and more)
            continue
        if not (hub.joined or getattr(hub, "attach_mode", False)):
            continue   # the first body of a fresh session is the brain's own job
        gone_since = gone_since or time.time()
        if time.time() - gone_since > 60 and time.time() - last_start > 180:
            hub.log("Тело Альтрона пропало больше минуты назад — запускаю его заново.")
            try:
                hub.bot_proc = launch_bot(hub.cfg, hub.bot_server if isinstance(hub.bot_server, int) else 25566,
                                          hub.log, lite=hub.bot_lite)
            except Exception as e:
                hub.log("Не смог запустить тело: %s" % e)
            last_start = time.time()


async def heartbeat(hub):
    """"I am alive" every 10 s for brain/supervisor.py: a brain whose loop hangs stops writing it and gets restarted."""
    import os
    import time
    path = BRAIN_DIR / "logs" / "brain_heartbeat.json"
    while True:
        try:
            path.write_text(json.dumps({"t": time.time(), "pid": os.getpid(), "busy": hub.busy,
                                        "host": hub.host is not None, "bot": hub.bot is not None}), encoding="utf-8")
        except OSError:
            pass
        await asyncio.sleep(10)


async def attach(hub):
    """The brain was restarted (a fix, an update) while the commander keeps playing: wait for his game and Altron's
    body to connect again, take the world from the last session, and run until the game is closed."""
    try:
        hub.memory.world = (BRAIN_DIR / "logs" / "last_world.txt").read_text(encoding="utf-8").strip()
    except OSError:
        pass
    hub.owner = hub.owner or COMMANDER
    for _ in range(300):   # the body comes back by itself in seconds; if it is gone, keep_body starts it anew
        if hub.host is not None and hub.bot is not None:
            break
        await asyncio.sleep(1)
    if hub.host is None:
        hub.log("Игра командира не подключилась — закрыта?")
        return
    for _ in range(300):
        if hub.bot is not None:
            break
        await asyncio.sleep(1)
    if hub.bot is None:
        hub.log("Тело Альтрона так и не подключилось.")
        return
    hub.joined = True
    hub.log("Мозг перезапущен: игра и тело Альтрона на месте (мир %s)." % hub.memory.world)
    back = hub.restore_modes()   # carry on with what he was doing before the restart
    await hub.say("Я снова на связи." + (" Продолжаю: %s." % ", ".join(back) if back else ""))
    # the commander's last words, if the failure cut them short: answered now
    import time
    try:
        req = json.loads((BRAIN_DIR / "logs" / "restart_request.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        req = {}
    try:
        (BRAIN_DIR / "logs" / "restart_request.json").unlink()   # answered once, not after every restart
    except OSError:
        pass
    pending = req.get("pending")
    if pending and time.time() - pending[2] < 120:
        hub.log("Отвечаю на фразу, прерванную сбоем: %s" % pending[1])
        await hub.handle_phrase(pending[0], pending[1])
    gone = 0
    while gone < 60:   # the game was closed (no link for a minute): the session is over
        gone = gone + 5 if hub.host is None else 0
        await asyncio.sleep(5)
    hub.log("Игра командира закрыта — заканчиваю.")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        asyncio.run(main(sys.argv[1:]))
    except KeyboardInterrupt:
        pass
