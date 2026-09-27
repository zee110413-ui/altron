"""Self-test: ask the local LLM a few commands and print which tools it would call (no game needed)."""
import asyncio
import json
import time

import httpx

from agent import LLM, SYSTEM_PROMPT
from launcher import BRAIN_DIR

cfg = json.loads((BRAIN_DIR / "config.json").read_text(encoding="utf-8"))
STATE = "[Состояние] Альтрон: x=10 y=64 z=-5, здоровье 20/20, еда 18/20, в руке: 1x Каменная кирка. Игрок Egor рядом: x=12 y=64 z=-3."

PHRASES = [
    "Альтрон, найди мне пять алмазов",
    "Альтрон, иди за мной и защищай",
    "Альтрон, там зомби, стреляй по ним",
    "Альтрон, перенеси бочку с нефтью с 100 64 20 на 110 64 25",
    "Альтрон, что нужно чтобы сделать баллистическую ракету?",
    "Альтрон, скрафти железную кирку",
    "Альтрон, как дела?",
    "Альтрон, положи всё железо в сундук на 15 64 -2",
]


async def main():
    async with httpx.AsyncClient() as c:
        for _ in range(180):
            try:
                if (await c.get("http://127.0.0.1:%d/health" % cfg["llm_port"], timeout=2)).status_code == 200:
                    break
            except Exception:
                pass
            await asyncio.sleep(1)
    llm = LLM(cfg)
    system = SYSTEM_PROMPT.format(bot="altron", owner="Egor")
    for p in PHRASES:
        t = time.time()
        msg = await llm.chat([{"role": "system", "content": system},
                              {"role": "user", "content": "[Egor говорит]: %s\n%s" % (p, STATE)}], force_tool=True)
        dt = time.time() - t
        calls = [(c["function"]["name"], c["function"]["arguments"]) for c in msg.get("tool_calls", [])]
        print("\n>>> %s  (%.1f с)" % (p, dt))
        print("    сказал:", msg["content"] or "-")
        for n, a in calls:
            print("    действие:", n, a)


if __name__ == "__main__":
    asyncio.run(main())
