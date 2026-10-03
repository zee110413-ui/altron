"""How fast Altron's real AI (the commander's Qwen 9B GGUF) answers on a GitHub machine without a video card: the real
system prompt and tools, two phrases (the second one finds the start of the prompt in llama-server's cache).

    python ci/llm_bench.py <port>
"""
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "brain"))
import agent  # noqa: E402


class Stub:
    owner = "MJreggich"
    persona = "teammate"
    lang = "ru"
    knowledge = None


cfg = json.loads((Path(__file__).resolve().parent.parent / "brain" / "config.json").read_text(encoding="utf-8"))
a = agent.Agent(cfg, Stub())
system = a._system()
tools = a._tools()
url = "http://127.0.0.1:%s/v1/chat/completions" % sys.argv[1]
for think in (False, True):
    for text in ("Альтрон, иди ко мне", "Как дела, железяка?"):
        body = {"model": "local", "messages": [{"role": "system", "content": system},
                                               {"role": "user", "content": "[MJreggich (командир) говорит]: %s\n"
                                                "[Состояние] Альтрон: x=0 y=64 z=0, здоровье 20/20, еда 20/20, в руке: "
                                                "пусто; командир MJreggich: x=6 y=64 z=3" % text}],
                "tools": tools, "temperature": 0.2, "chat_template_kwargs": {"enable_thinking": think}}
        t = time.time()
        r = httpx.post(url, json=body, timeout=3600).json()
        m = r["choices"][0]["message"]
        print("think=%s %-28s %.0f s  timings=%s" % (think, text, time.time() - t, json.dumps(r.get("timings", {}))))
        print("   reply:", (m.get("reasoning_content") or "")[:300].replace("\n", " "), "|", m.get("content"),
              json.dumps(m.get("tool_calls"), ensure_ascii=False)[:300], flush=True)
