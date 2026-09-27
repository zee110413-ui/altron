"""Recorded demo: starts the AI, a host game with a fresh normal world and the bot, prepares the world,
gives spoken orders and records Altron's window with his voice and subtitles.
Usage: demo.py <scenario>   (see SCENARIOS)"""
import asyncio
import base64
import json
import sys
import time

import numpy as np

import altron
from launcher import BRAIN_DIR, launch_host
from recorder import Recorder

COMMANDER = "MJreggich"   # the player's nick in the demo world (Altron calls him "командир")


def outcrop(ores, dz=6, width=9, rows=3):
    """A small quarry wall in front of the bot: rows of ore blocks, and stone behind it."""
    blocks, flat = [], []
    for ore, n in ores:
        flat += [ore] * n
    for i in range(width * rows):
        dx, dy = i % width - width // 2, i // width
        blocks.append([dx, dy, dz, flat[i] if i < len(flat) else "minecraft:stone"])
        blocks.append([dx, dy, dz + 1, "minecraft:stone"])
        blocks.append([dx, dy, dz + 2, "minecraft:stone"])
    return blocks


def tree(dx, dz, height=5):
    blocks = [[dx, dy, dz, "minecraft:oak_log"] for dy in range(height)]
    for lx in range(-2, 3):
        for lz in range(-2, 3):
            for dy in (height - 2, height - 1):
                if (lx, lz) != (0, 0) and abs(lx) + abs(lz) < 4:
                    blocks.append([dx + lx, dy, dz + lz, "minecraft:oak_leaves"])
            if abs(lx) + abs(lz) <= 1:
                blocks.append([dx + lx, height, dz + lz, "minecraft:oak_leaves"])
    return blocks


SCENARIOS = {
    # From nothing to an HBM iron anvil: wood -> tools -> stone -> iron -> furnace -> smelting -> anvil
    "anvil": {
        "world": "AltronDemo_Anvil",
        "setup": {"time": 6000, "clear_weather": True, "daylight_cycle": False, "mob_spawning": False, "surface": True,
                  "flatten": 16,   # a flat clearing: the seed's terrain (mountains, lakes) must not spoil the demo
                  "blocks": outcrop([("minecraft:iron_ore", 20), ("minecraft:coal_ore", 5)]) + tree(-7, -4) + tree(6, -6)},
        "script": [
            ("Альтрон, привет! Ты меня слышишь?", 3),
            ("Альтрон, сделай железную наковальню из мода HBM. Начни с нуля: дерево, кирки, руду — всё добудь и переплавь сам.", 40),
            ("Альтрон, поставь эту наковальню HBM рядом с собой и расскажи, что мы сможем на ней делать.", 5),
        ],
    },
}


async def wait_for(cond, timeout, step=1.0):
    t = time.time()
    while time.time() - t < timeout:
        if cond():
            return True
        await asyncio.sleep(step)
    return False


def hub_busy(hub):
    return (hub.busy or not hub.requests.empty() or bool(hub.agent_tasks) or bool(hub.queue)
            or hub.running is not None or (hub.macro_task is not None and not hub.macro_task.done()))


async def wait_idle(hub, minutes, quiet_sec=15):
    quiet, t = 0, time.time()
    while time.time() - t < minutes * 60:
        await asyncio.sleep(1)
        quiet = 0 if hub_busy(hub) else quiet + 1
        if quiet >= quiet_sec:
            return True
    hub.log("Режиссёр: время на команду вышло")
    return False


def commander_voice(tts, phrase):
    """The commander's voice: the same Piper voice without the robot effect, a tone lower (slowed 10%)."""
    pcm = np.frombuffer(b"".join(tts.synth(phrase)), dtype="<i2").astype(np.float32)
    n = int(len(pcm) * 1.1)
    low = np.interp(np.linspace(0, len(pcm) - 1, n), np.arange(len(pcm)), pcm)
    return low.astype("<i2").tobytes()


async def speak_to_altron(hub, rec, pcm, phrase):
    """Say an order into Simple Voice Chat, as if into the host player's microphone.
    Altron has to hear and recognize it himself; if the voice path fails, the order is passed on directly."""
    rec.add(pcm, COMMANDER, phrase)
    heard, hub.mic_failed = hub.heard, None
    for i in range(0, len(pcm), 96000):   # 1 s pieces
        hub.send(hub.host, {"type": "mic", "pcm": base64.b64encode(pcm[i:i + 96000]).decode("ascii")})
    await hub.host.drain()
    seconds = len(pcm) / 96000
    if await wait_for(lambda: hub.heard > heard or hub.mic_failed, seconds + 20, 0.2) and hub.heard > heard:
        hub.log("Режиссёр: Альтрон услышал приказ голосом")
        return True
    hub.log("Режиссёр: голос не дошёл (%s) — передаю приказ напрямую" % (hub.mic_failed or "не распознан"))
    await hub.handle_phrase(COMMANDER, phrase)
    return False


async def main(name):
    sc = SCENARIOS[name]
    cfg = json.loads((BRAIN_DIR / "config.json").read_text(encoding="utf-8"))
    cfg["bot_third_person"] = True
    cfg["bot_render_always"] = True      # the recorder films Altron's window: it must be drawn all the time
    cfg["bot_window"] = [1280, 720]
    cfg["chat_replies"] = False          # everything is spoken: orders and answers go through the voice chat
    world = "%s_%s" % (sc["world"], time.strftime("%m%d_%H%M"))   # a fresh world every run: start from nothing
    # every demo world starts with a clean memory, and demos never mix into Altron's real memories
    cfg["memory_dir"] = "memory_demo/" + world
    hub = altron.Hub(cfg)
    hub.loop = asyncio.get_running_loop()
    llm_proc = altron.start_llm(cfg, hub.log)

    voices = {}

    def load():
        from knowledge import Knowledge
        from speech import STT, TTS
        hub.knowledge = Knowledge.load(cfg, hub.log)
        hub.stt = STT(cfg)
        hub.tts = TTS(cfg)
        voices["commander"] = TTS(dict(cfg, tts_robot=0, tts_speed=1.12))
        hub.log("Справочник, слух и голоса готовы.")

    await asyncio.to_thread(load)
    server = await altron.listen(hub, cfg["brain_port"])
    loops = [asyncio.create_task(server.serve_forever()), asyncio.create_task(hub.agent_loop()),
             asyncio.create_task(hub.voice_loop())]
    await altron.wait_llm(cfg, hub.log)

    host_proc = launch_host(cfg, world, COMMANDER, hub.log)
    try:
        if not await wait_for(lambda: hub.host is not None, 300):
            raise RuntimeError("игра-хозяин не подключилась к мозгу")
        hub.log("Режиссёр: хозяин на связи, жду Альтрона в мире...")
        if not await wait_for(lambda: hub.joined, 600):
            raise RuntimeError("Альтрон не зашёл в мир")
        hub.send(hub.host, dict(sc["setup"], type="setup"))
        if not await wait_for(lambda: hub.setup_result is not None, 60):
            raise RuntimeError("мир не подготовлен")
        await asyncio.sleep(12)   # chunks render, Altron looks around and memorizes the outcrop

        stamp = time.strftime("%Y%m%d_%H%M")
        rec = Recorder(cfg, "../videos/%s_%s" % (name, stamp))
        rec.start()
        hub.recorder = rec
        await asyncio.sleep(3)
        for phrase, minutes in sc["script"]:
            hub.log("%s (голосом): %s" % (COMMANDER, phrase))
            pcm = await asyncio.to_thread(commander_voice, voices["commander"], phrase)
            await speak_to_altron(hub, rec, pcm, phrase)
            await wait_idle(hub, minutes)
            await asyncio.sleep(3)
        final = await asyncio.to_thread(rec.stop, "altron_%s.mp4" % name)
        hub.recorder = None
        hub.log("ВИДЕО ГОТОВО: %s" % final)
    finally:
        hub.stop_bot()
        host_proc.terminate()
        if llm_proc is not None:
            llm_proc.terminate()
        for t in loops:
            t.cancel()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "anvil"))
