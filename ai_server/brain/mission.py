"""A real mission on a COPY of the commander's world: Altron gets one spoken order and does it himself.

    mission.py "Los Perrito" "Альтрон, сделай ..."  [--hours 4]

The world is copied into altron\\host\\saves\\AltronTest_<name>_<time> (the original is never touched), the brain starts
with the AI model and its encyclopedia, a host game opens the copy, Altron joins, hears the order and works on it.
Every minute the log gets where he is, what he is doing and what he carries; it ends when the wanted item is in his
inventory or the time is up. The copy stays for looking at afterwards.
"""
import asyncio
import json
import shutil
import sys
import time

import altron
from launcher import BRAIN_DIR, launch_host, rel
from polygon import COMMANDER, TEST_PORT, wait_for


async def main(argv):
    src_name, order = argv[0], argv[1]
    hours = float(argv[argv.index("--hours") + 1]) if "--hours" in argv else 4.0
    goal = argv[argv.index("--goal") + 1] if "--goal" in argv else ""
    find = argv[argv.index("--find") + 1] if "--find" in argv else ""          # blocks he has to find himself
    help_after = float(argv[argv.index("--help-after") + 1]) if "--help-after" in argv else 10
    hint = argv[argv.index("--hint") + 1] if "--hint" in argv else "Альтрон, я перенёс тебя к себе. Машины здесь, рядом — найди их и продолжай."
    cfg = json.loads((BRAIN_DIR / "config.json").read_text(encoding="utf-8"))
    src = rel(cfg["minecraft_dir"]) / "versions" / cfg["pack_version"] / "saves" / src_name
    world = "AltronTest_%s_%s" % (src_name.replace(" ", ""), time.strftime("%m%d_%H%M"))
    dst = rel(cfg.get("host_dir", "../host")) / "saves" / world
    print("Копирую мир %s -> %s ..." % (src, dst), flush=True)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("session.lock"))
    cfg.update({"brain_port": TEST_PORT, "chat_replies": False, "memory_dir": "memory_test/" + world})
    hub = altron.Hub(cfg)
    hub.loop = asyncio.get_running_loop()
    llm_proc = altron.start_llm(cfg, hub.log)

    def load():
        from knowledge import Knowledge
        hub.knowledge = Knowledge.load(cfg, hub.log)

    await asyncio.to_thread(load)
    server = await altron.listen(hub, cfg["brain_port"])
    loops = [asyncio.create_task(server.serve_forever()), asyncio.create_task(hub.agent_loop())]
    await altron.wait_llm(cfg, hub.log)
    host_proc = launch_host(cfg, world, COMMANDER, hub.log)
    try:
        if not await wait_for(lambda: hub.host is not None, 420):
            raise RuntimeError("игра-хозяин не подключилась")
        if not await wait_for(lambda: hub.joined, 900):
            raise RuntimeError("Альтрон не зашёл в мир")
        await asyncio.sleep(10)
        inv = await hub.bot_call("inventory_ids", {})
        hub.log("Режим игры Альтрона: %s" % ("творческий (сырьё берёт из меню, делает в машинах)" if inv.get("creative") else "выживание"))
        if find:
            # for the log only (Altron is not told): where do such machines really stand around the commander?
            h = (await hub.probe()).get("host", {}).get("pos")
            if h:
                words = [w.split(":")[-1].replace("machine_", "")[:6] for w in find.split(",")] + ["press"]
                sc = (await hub.probe(scan={"center": [int(v) for v in h], "r": 96, "words": words})).get("scan", {})
                hub.log("(для проверки, Альтрону не сообщается) вокруг командира %s: %s" % ([round(v) for v in h], sc or "ничего"))
        hub.log("%s (голосом): %s" % (COMMANDER, order))
        await hub.handle_phrase(COMMANDER, order)
        t0 = time.time()
        helped = False
        while time.time() - t0 < hours * 3600:
            # the commander's rule: if he cannot find the machines by himself, bring him over to the commander
            if find and not helped and time.time() - t0 > 60 * help_after:
                seen = (await hub.bot_call("find_block", {"block": find, "radius": 128})).get("msg", "")
                if "Нашёл" not in seen:
                    helped = True
                    h = (await hub.probe()).get("host", {}).get("pos")
                    if h:
                        await hub.run_tool("stop", {}, 5)   # the old search would lead him away from here again
                        await hub.probe(tp_bot=[h[0] + 1.5, h[1], h[2] + 1.5])
                        hub.log("Не нашёл сам за %d мин — телепортирую Альтрона к командиру (%s)" % (help_after, [round(v) for v in h]))
                        await asyncio.sleep(8)
                        await hub.handle_phrase(COMMANDER, hint)
            await asyncio.sleep(60)
            w = await hub.probe()
            b = w.get("bot") or {}
            items = b.get("items", {})
            top = ", ".join("%s x%d" % (k.split(":")[-1], v) for k, v in sorted(items.items(), key=lambda kv: -kv[1])[:12])
            s = hub.state
            broke = w.get("bot_broke_count", 0)
            hub.log("[%3.0f мин] Альтрон в %s, задача: %s %s | %s | сломал блоков в мире: %d%s" % (
                (time.time() - t0) / 60, [round(v) for v in b.get("pos", [0, 0, 0])], s.get("task", "-"), s.get("progress", ""), top,
                broke, (" (последние: %s)" % "; ".join(w.get("bot_broke", [])[-5:])) if broke else ""))
            # done only when his own work is over: finished ones he found lying in a machine do not count
            if goal and items.get(goal, 0) >= 1 and hub.macro_task is None and hub.running is None:
                hub.log("ЦЕЛЬ ДОСТИГНУТА: у Альтрона %dx %s" % (items[goal], goal))
                break
    finally:
        hub.stop_bot()
        host_proc.terminate()
        if llm_proc is not None:
            llm_proc.terminate()
        for t in loops:
            t.cancel()
        hub.log("Мир-копия оставлен для осмотра: %s" % dst)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main(sys.argv[1:]))
