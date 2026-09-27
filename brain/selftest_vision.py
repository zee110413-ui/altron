"""Self-test: ask the vision model about a real screenshot from the modpack."""
import asyncio
import base64
import json
import sys
import time

import httpx

from agent import LLM
from launcher import BRAIN_DIR

cfg = json.loads((BRAIN_DIR / "config.json").read_text(encoding="utf-8"))


async def main(path, question):
    async with httpx.AsyncClient() as c:
        for _ in range(180):
            try:
                if (await c.get("http://127.0.0.1:%d/health" % cfg["llm_port"], timeout=2)).status_code == 200:
                    break
            except Exception:
                pass
            await asyncio.sleep(1)
    img = base64.b64encode(open(path, "rb").read()).decode("ascii")
    t = time.time()
    answer = await LLM(cfg).vision(question, "(окно не открыто, это вид из глаз)", img, 1920, 1080)
    print("Вопрос:", question)
    print("Ответ (%.1f с): %s" % (time.time() - t, answer))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
