"""Simulation: the real brain (LLM agent + hub) drives a fake bot in a tiny virtual world.
Shows how Altron plans a long task. Needs llama-server running (start_altron.bat or the test command)."""
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

import altron
from launcher import BRAIN_DIR

RECIPES = {  # item -> (output count, {ingredient: count})
    "iron_helmet": (1, {"iron_ingot": 5}), "iron_chestplate": (1, {"iron_ingot": 8}),
    "iron_leggings": (1, {"iron_ingot": 7}), "iron_boots": (1, {"iron_ingot": 4}),
    "iron_pickaxe": (1, {"iron_ingot": 3, "stick": 2}), "stick": (4, {"oak_planks": 2}),
    "oak_planks": (4, {"oak_log": 1}), "furnace": (1, {"cobblestone": 8}), "crafting_table": (1, {"oak_planks": 4}),
}
DROPS = {"iron_ore": "raw_iron", "deepslate_iron_ore": "raw_iron", "stone": "cobblestone", "coal_ore": "coal",
         "diamond_ore": "diamond", "deepslate_diamond_ore": "diamond", "oak_log": "oak_log"}
NAMES = {"железо": "iron_ingot", "железная броня": "iron_chestplate", "печь": "furnace", "печка": "furnace",
         "булыжник": "cobblestone", "уголь": "coal", "алмаз": "diamond"}


def short(i):
    return i.split(":")[-1].strip()


class FakeBot:
    def __init__(self):
        self.inv = {"stone_pickaxe": 1, "oak_log": 6, "coal": 4, "bread": 3}
        self.seen = {"iron_ore": ["30 40 -12", "33 38 -10"], "crafting_table": ["12 64 -3"]}
        self.task_id = 0
        self.w = None

    def send(self, o):
        self.w.write((json.dumps(o, ensure_ascii=False) + "\n").encode("utf-8"))

    def inv_text(self):
        return "Инвентарь:\n" + "\n".join("- %s x%d" % (k, v) for k, v in self.inv.items() if v > 0)

    def has(self, need, times=1):
        return {k: n * times - self.inv.get(k, 0) for k, n in need.items() if self.inv.get(k, 0) < n * times}

    def start(self, name, delay, finish):
        self.task_id += 1
        tid = self.task_id

        async def later():
            await asyncio.sleep(delay)
            ok, msg = finish()
            self.send({"type": "event", "event": "task_done" if ok else "task_failed", "task": name, "task_id": tid, "msg": msg})
        asyncio.create_task(later())
        return {"ok": True, "msg": "начал: " + name, "task_id": tid}

    def run(self, name, a):
        item = short(NAMES.get(str(a.get("item", "")).lower(), str(a.get("item", ""))))
        if name == "inventory":
            return {"ok": True, "msg": self.inv_text()}
        if name == "status":
            return {"ok": True, "msg": "Позиция 10 64 -5, здоровье 20/20, еда 18/20. Игрок Egor: 12 64 -3."}
        if name == "find_block":
            b = short(str(a.get("block", "")))
            pos = self.seen.get(b) or self.seen.get(b.replace("deepslate_", ""))
            return {"ok": True, "msg": ("Видел %s: %s" % (b, "; ".join(pos))) if pos else "Не видел %s поблизости" % b}
        if name == "find_item":
            q = str(a.get("query", ""))
            return {"ok": True, "msg": "- %s = minecraft:%s" % (q, short(NAMES.get(q.lower(), q)))}
        if name == "recipe":
            if item in RECIPES:
                out, need = RECIPES[item]
                return {"ok": True, "msg": "[crafting] %dx %s <- %s" % (out, item, ", ".join("%dx %s" % (v, k) for k, v in need.items()))}
            if item == "iron_ingot":
                return {"ok": True, "msg": "[smelting] iron_ingot <- raw_iron (печь + топливо)"}
            return {"ok": True, "msg": "Рецептов для %s не найдено" % item}
        if name == "mine":
            blocks = [short(b) for b in a.get("blocks", [])]
            count = int(a.get("count", 8))
            if any("diamond" in b for b in blocks) and self.inv.get("iron_pickaxe", 0) == 0:
                return {"ok": True, "msg": "начал: mine", "task_id": self._fail_now("mine", "нужна железная кирка или лучше. Попроси командира дать инструмент.")}
            drop = DROPS.get(blocks[0], blocks[0]) if blocks else "cobblestone"

            def fin():
                self.inv[drop] = self.inv.get(drop, 0) + count
                return True, "добыто блоков: %d. Получено: %s +%d" % (count, drop, count)
            return self.start("mine", 2.5, fin)
        if name == "craft":
            if item not in RECIPES:
                return {"ok": False, "msg": "нет рецепта верстака для %s" % item}
            out, need = RECIPES[item]
            times = -(-int(a.get("count", 1)) // out)
            # like the real CraftTask: make missing intermediate parts (sticks, planks) if possible
            for _ in range(3):
                for k, n in list(self.has(need, times).items()):
                    if k in RECIPES:
                        o2, need2 = RECIPES[k]
                        t2 = -(-n // o2)
                        for k2, n2 in list(self.has(need2, t2).items()):
                            if k2 in RECIPES and not self.has(RECIPES[k2][1], -(-n2 // RECIPES[k2][0])):
                                for k3, n3 in RECIPES[k2][1].items():
                                    self.inv[k3] -= n3 * -(-n2 // RECIPES[k2][0])
                                self.inv[k2] = self.inv.get(k2, 0) + RECIPES[k2][0] * -(-n2 // RECIPES[k2][0])
                        if not self.has(need2, t2):
                            for k2, n2 in need2.items():
                                self.inv[k2] -= n2 * t2
                            self.inv[k] = self.inv.get(k, 0) + o2 * t2
            miss = self.has(need, times)
            if miss:
                return self.start("craft", 0.3, lambda: (False, "не хватает для %s: %s" % (item, ", ".join("%dx %s" % (v, k) for k, v in miss.items()))))

            def fin():
                for k, n in need.items():
                    self.inv[k] -= n * times
                self.inv[item] = self.inv.get(item, 0) + out * times
                return True, "скрафтил %dx %s" % (out * times, item)
            return self.start("craft", 0.5, fin)
        if name == "place_block":
            if self.inv.get(item, 0) <= 0:
                return {"ok": False, "msg": "нет в инвентаре: " + item}

            def fin():
                self.inv[item] -= 1
                self.seen.setdefault(item, []).append("%s %s %s" % (a.get("x"), a.get("y"), a.get("z")))
                return True, "поставил %s" % item
            return self.start("place_block", 0.5, fin)
        if name == "smelt":
            if item not in ("raw_iron", "raw_gold", "cobblestone"):
                return {"ok": False, "msg": "это нельзя переплавить: " + item}
            if "furnace" not in self.seen:
                if self.inv.get("furnace", 0) > 0:
                    self.inv["furnace"] -= 1
                elif self.inv.get("cobblestone", 0) >= 8:
                    self.inv["cobblestone"] -= 8
                else:
                    have = self.inv.get("cobblestone", 0)
                    return self.start("smelt", 0.2, lambda: (False, "нет печки, а скрафтить её не из чего: не хватает для furnace: %dx cobblestone" % (8 - have)))
                self.seen["furnace"] = ["11 64 -3"]
            n = min(int(a.get("count", 1)), self.inv.get(item, 0))
            if n <= 0:
                return {"ok": False, "msg": "нет в инвентаре: " + item}
            fuel = -(-n // 8)
            if self.inv.get("coal", 0) < fuel:
                return self.start("smelt", 0.2, lambda: (False, "нет топлива"))

            def fin():
                self.inv[item] -= n
                self.inv["coal"] -= fuel
                self.inv["iron_ingot"] = self.inv.get("iron_ingot", 0) + n
                return True, "переплавил %d шт." % n
            return self.start("smelt", 3, fin)
        if name == "give":
            n = int(a.get("count", 0)) or self.inv.get(item, 0)
            if self.inv.get(item, 0) < n or n == 0:
                return {"ok": False, "msg": "нет такого предмета: " + item}

            def fin():
                self.inv[item] -= n
                return True, "отдал Egor: %d шт. %s" % (n, item)
            return self.start("give", 0.5, fin)
        return {"ok": True, "msg": "выполнил " + name}

    def _fail_now(self, name, msg):
        self.task_id += 1
        tid = self.task_id
        asyncio.get_event_loop().call_later(0.2, lambda: self.send(
            {"type": "event", "event": "task_failed", "task": name, "task_id": tid, "msg": msg}))
        return tid

    async def connect(self, port):
        reader, self.w = await asyncio.open_connection("127.0.0.1", port)
        self.send({"type": "hello", "role": "bot"})
        self.send({"type": "event", "event": "joined", "msg": "в мире"})
        while True:
            line = await reader.readline()
            if not line:
                return
            m = json.loads(line)
            if m.get("type") != "cmd":
                continue
            res = self.run(m["name"], m.get("args") or {})
            res.update({"type": "result", "id": m["id"]})
            self.send(res)
            if m["name"] == "chat":
                continue
            print("      [мир] инвентарь:", {k: v for k, v in self.inv.items() if v})


async def main(task_text):
    cfg = json.loads((BRAIN_DIR / "config.json").read_text(encoding="utf-8"))
    cfg["brain_port"] = 47899
    cfg["chat_replies"] = False
    cfg["memory_dir"] = str(Path(tempfile.mkdtemp(prefix="altron_mem_")))   # tests never touch the real memory
    hub = altron.Hub(cfg)
    hub.loop = asyncio.get_running_loop()
    hub.owner = "MJreggich"
    server = await asyncio.start_server(hub.on_client, "127.0.0.1", cfg["brain_port"])
    bot = FakeBot()
    asyncio.create_task(bot.connect(cfg["brain_port"]))
    agent = asyncio.create_task(hub.agent_loop())
    await asyncio.sleep(1)
    t0 = time.time()
    # several phrases separated by "|" form a dialogue
    for phrase in [s.strip() for s in task_text.split("|") if s.strip()]:
        print("\n### Egor: " + phrase + "\n")
        await hub.handle_phrase("MJreggich", phrase)
        quiet = 0
        while time.time() - t0 < 420:
            await asyncio.sleep(1)
            busy = hub.busy or not hub.requests.empty() or hub.agent_tasks or hub.task_waiters
            quiet = 0 if busy else quiet + 1
            if quiet >= 8:
                break
    print("\n### Итог: инвентарь бота:", {k: v for k, v in bot.inv.items() if v}, "(%.0f с)" % (time.time() - t0))
    agent.cancel()
    server.close()


if __name__ == "__main__":
    asyncio.run(main(" ".join(sys.argv[1:]) or "Альтрон, сделай мне полный комплект железной брони и отдай мне"))
