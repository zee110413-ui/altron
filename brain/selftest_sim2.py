"""Hard simulation: the real brain (LLM + encyclopedia) drives a fake bot whose world uses the REAL recipes
of the modpack (read from the mod files). Machines, ores and mobs are simplified.
Phrases separated by "|" form a dialogue. Needs llama-server running."""
import asyncio
import json
import os
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

import altron
from knowledge import Knowledge
from launcher import BRAIN_DIR

CRAFTING = ("minecraft:crafting_shaped", "minecraft:crafting_shapeless")


class World:
    def __init__(self, k):
        self.k = k
        self.inv = defaultdict(int, {
            "minecraft:iron_pickaxe": 1, "minecraft:iron_sword": 1, "minecraft:oak_log": 16, "minecraft:coal": 24,
            "minecraft:raw_iron": 12, "minecraft:iron_ingot": 30, "minecraft:bread": 6, "minecraft:redstone": 4,
            "minecraft:cobblestone": 32, "minecraft:copper_ingot": 10})
        self.seen = {"tacz:gun_smith_table": "8 64 -2", "minecraft:furnace": "11 64 -3", "hbm_m:anvil_iron": "6 64 -4",
                     "minecraft:lapis_ore": "40 20 -30", "minecraft:copper_ore": "35 48 -20"}
        self.machines = {"hbm_m:anvil"}   # recipe types the base can do besides crafting/smelting/gunsmith
        self.k._build_index()

    # ---------------------------------------------------------------- inventory
    def items_for(self, ref):
        return self.k.resolve_tag(ref) if ref.startswith("#") else [ref]

    def have(self, ref):
        return sum(self.inv[i] for i in self.items_for(ref))

    def take(self, ref, n):
        for i in self.items_for(ref):
            use = min(n, self.inv[i])
            self.inv[i] -= use
            n -= use
            if n <= 0:
                return

    def missing(self, r, times):
        return [(ref, n * times - self.have(ref)) for ref, n in r["in"] if self.have(ref) < n * times]

    def text_inv(self):
        return "Инвентарь:\n" + "\n".join("- %s x%d" % (self.k.name(i), n) for i, n in self.inv.items() if n > 0)

    # ---------------------------------------------------------------- actions
    def craft(self, query, count, depth=0):
        item = self.k.find_id(query)
        if not item:
            return False, "не знаю предмет " + query
        recs = [self.k.recipes[i] for i in self.k._index[0].get(item, [])]
        if not recs:
            return False, "у %s нет рецепта (добывается или находится)" % self.k.name(item)
        usable = []
        for r in recs:
            t = r["type"]
            if t in CRAFTING:
                usable.append(r)
            elif t == "tacz:gun_smith_table_crafting":
                if "tacz:gun_smith_table" in self.seen:
                    usable.append(r)
            elif t in ("minecraft:smelting", "minecraft:blasting"):
                continue
            elif t in self.machines:
                usable.append(r)
        if not usable:
            kinds = sorted({r["type"] for r in recs})
            return False, ("%s делается в: %s — такой машины у меня рядом нет (не видел). Нужно построить/найти или спросить командира."
                           % (self.k.name(item), ", ".join(kinds)))
        r = usable[0]
        out = next((n for o, n in r["out"] if o == item), 1) or 1
        times = -(-max(1, count) // out)
        # like the real CraftTask: make missing crafting-table parts first
        for ref, lack in self.missing(r, times):
            sub = self.items_for(ref)[0]
            if depth < 2 and any(self.k.recipes[i]["type"] in CRAFTING for i in self.k._index[0].get(sub, [])):
                self.craft(sub, lack, depth + 1)
        miss = self.missing(r, times)
        if miss:
            return False, "не хватает для %s: %s" % (self.k.name(item), ", ".join("%dx %s" % (n, self.k.name(ref)) for ref, n in miss))
        for ref, n in r["in"]:
            self.take(ref, n * times)
        self.inv[item] += out * times
        where = {"tacz:gun_smith_table_crafting": " на оружейном столе"}.get(r["type"], "")
        return True, "сделал%s %dx %s" % (where, out * times, self.k.name(item))

    def mine(self, blocks, count):
        b = blocks[0].split(":")[-1] if blocks else "stone"
        drops = {"iron_ore": ("minecraft:raw_iron", 1), "copper_ore": ("minecraft:raw_copper", 3), "lapis_ore": ("minecraft:lapis_lazuli", 6),
                 "redstone_ore": ("minecraft:redstone", 4), "coal_ore": ("minecraft:coal", 1), "diamond_ore": ("minecraft:diamond", 1),
                 "gold_ore": ("minecraft:raw_gold", 1), "stone": ("minecraft:cobblestone", 1)}
        key = b.replace("deepslate_", "")
        if blocks[0].startswith("#") or key.endswith("_log") or key == "logs":
            key, drops[key] = key, ("minecraft:oak_log", 1)
        item, per = drops.get(key, (blocks[0] if ":" in blocks[0] else "minecraft:" + b, 1))
        self.inv[item] += per * count
        return "добыто блоков: %d. Получено: %s +%d" % (count, self.k.name(item), per * count)

    def smelt(self, query, count):
        item = self.k.find_id(query) or query
        for r in self.k.recipes:
            if r["type"] != "minecraft:smelting" or not r["in"]:
                continue
            if item in self.items_for(r["in"][0][0]):
                n = min(count, self.inv[item])
                if n <= 0:
                    return False, "нет в инвентаре: " + self.k.name(item)
                fuel = -(-n // 8)
                if self.inv["minecraft:coal"] < fuel:
                    return False, "нет топлива"
                self.inv[item] -= n
                self.inv["minecraft:coal"] -= fuel
                out = r["out"][0][0]
                self.inv[out] += n
                return True, "переплавил %d шт. в %s" % (n, self.k.name(out))
        return False, "это нельзя переплавить: " + self.k.name(item)


class FakeBot:
    def __init__(self, world):
        self.w = world
        self.task_id = 0
        self.wr = None

    def send(self, o):
        self.wr.write((json.dumps(o, ensure_ascii=False) + "\n").encode("utf-8"))

    def task(self, name, delay, finish):
        self.task_id += 1
        tid = self.task_id

        async def later():
            await asyncio.sleep(delay)
            ok, msg = finish()
            self.send({"type": "event", "event": "task_done" if ok else "task_failed", "task": name, "task_id": tid, "msg": msg})
        asyncio.create_task(later())
        return {"ok": True, "msg": "начал: " + name, "task_id": tid}

    def run(self, name, a):
        w, k = self.w, self.w.k
        if name == "inventory":
            return {"ok": True, "msg": w.text_inv()}
        if name == "inventory_ids":
            return {"ok": True, "msg": "инвентарь", "items": {i: n for i, n in w.inv.items() if n > 0}}
        if name == "status":
            return {"ok": True, "msg": "Позиция 10 64 -5, здоровье 20/20, еда 18/20, день. Игрок MJreggich рядом."}
        if name == "find_block":
            q = str(a.get("block", ""))
            hits = [(b, p) for b, p in w.seen.items() if q.split(":")[-1] in b or q in k.name(b).lower()]
            return {"ok": True, "msg": "; ".join("видел %s в %s" % (k.name(b), p) for b, p in hits) or "не видел такого"}
        if name in ("find_item", "recipe", "item_info"):
            return {"ok": True, "msg": k.search(str(a.get("query") or a.get("item", "")), 2)}
        if name == "mine":
            blocks = a.get("blocks") or ["stone"]
            if isinstance(blocks, str):
                blocks = [blocks]
            return self.task("mine", 2.0, lambda: (True, w.mine(blocks, int(a.get("count", 8)))))
        if name == "craft":
            return self.task("craft", 0.4, lambda: w.craft(str(a.get("item", "")), int(a.get("count", 1) or 1)))
        if name == "smelt":
            return self.task("smelt", 2.0, lambda: w.smelt(str(a.get("item", "")), int(a.get("count", 64) or 64)))
        if name == "attack":
            target = str(a.get("target", "hostile")).lower()

            def fin():
                if "creeper" in target or "крипер" in target:
                    w.inv["minecraft:gunpowder"] += 6
                    return True, "бой окончен, уничтожено: 3 крипера. Получено: Порох +6"
                w.inv["minecraft:rotten_flesh"] += 4
                return True, "бой окончен, уничтожено: 4 зомби. Получено: Гнилая плоть +4"
            return self.task("attack", 2.5, fin)
        if name == "give":
            item = k.find_id(str(a.get("item", "")))
            n = int(a.get("count", 0) or 0) or (w.inv[item] if item else 0)
            if not item or w.inv[item] < n or n <= 0:
                return {"ok": False, "msg": "нет такого предмета: " + str(a.get("item"))}

            def fin():
                w.inv[item] -= n
                return True, "отдал MJreggich: %d шт. %s" % (n, k.name(item))
            return self.task("give", 0.5, fin)
        if name == "build_multiblock":
            return {"ok": False, "msg": "для постройки не хватает материалов и инженерного молота"}
        if name == "memory":
            return {"ok": True, "msg": "Помню в этом мире: 1200 увиденных блоков, 2 сундука с содержимым, 3 встречи."}
        if name == "recall_items":
            q = str(a.get("query", "")).lower()[:5]
            stash = {"Сундук 20 64 -8 (12 бл.)": "5x Алмаз, 30x Булыжник", "Бочка 22 64 -8 (13 бл.)": "12x Порох"}
            hits = ["- %s: %s" % (s, v) for s, v in stash.items() if not q or q in v.lower()]
            return {"ok": True, "msg": "\n".join(hits) or "не помню, чтобы видел «%s» в сундуках или машинах" % q}
        if name == "recall_entities":
            q = str(a.get("query", "")).lower()[:4]
            seen = ["- Корова 40 70 12 (35 бл.), видел 5 мин назад", "- MJreggich 12 64 -3 (2 бл.), видел только что"]
            hits = [s for s in seen if not q or q in s.lower()]
            return {"ok": True, "msg": "\n".join(hits) or "не помню, чтобы видел «%s»" % q}
        if name == "goto":
            return self.task("goto", 1.0, lambda: (True, "дошёл до %s %s %s" % (a.get("x"), a.get("y"), a.get("z"))))
        if name in ("screen", "screenshot"):
            return {"ok": False, "msg": "(в симуляции нет экрана)"}
        return {"ok": True, "msg": "выполнил " + name}

    async def connect(self, port):
        reader, self.wr = await asyncio.open_connection("127.0.0.1", port)
        self.send({"type": "hello", "role": "bot"})
        self.send({"type": "state", "pos": [10.5, 64, -5.5], "dim": "minecraft:overworld", "hp": 20, "food": 18,
                   "held": "Железная кирка", "task": "", "progress": "", "owner_pos": [12.3, 64, -3.7]})
        self.send({"type": "event", "event": "joined", "world": "sim_world", "msg": "Альтрон в мире (симуляция)"})
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


async def main(text):
    cfg = json.loads((BRAIN_DIR / "config.json").read_text(encoding="utf-8"))
    cfg["brain_port"] = 47898
    cfg["chat_replies"] = False
    # tests never touch the real memory; ALTRON_TEST_MEM reuses a test memory to check a "restart"
    cfg["memory_dir"] = os.environ.get("ALTRON_TEST_MEM") or str(Path(tempfile.mkdtemp(prefix="altron_mem_")))
    hub = altron.Hub(cfg)
    hub.loop = asyncio.get_running_loop()
    hub.owner = "MJreggich"
    hub.knowledge = Knowledge.load(cfg)
    world = World(hub.knowledge)
    server = await asyncio.start_server(hub.on_client, "127.0.0.1", cfg["brain_port"])
    bot = FakeBot(world)
    asyncio.create_task(bot.connect(cfg["brain_port"]))
    agent = asyncio.create_task(hub.agent_loop())
    await asyncio.sleep(1)
    t0 = time.time()
    for phrase in [s.strip() for s in text.split("|") if s.strip()]:
        print("\n### MJreggich: " + phrase + "\n", flush=True)
        await hub.handle_phrase("MJreggich", phrase)
        quiet = 0
        while time.time() - t0 < 900:
            await asyncio.sleep(1)
            busy = (hub.busy or not hub.requests.empty() or hub.agent_tasks or hub.task_waiters
                    or hub.queue or hub.running is not None)
            quiet = 0 if busy else quiet + 1
            if quiet >= 8:
                break
    print("\n### Итог (%.0f с). %s" % (time.time() - t0, world.text_inv()))
    agent.cancel()
    server.close()


if __name__ == "__main__":
    asyncio.run(main(" ".join(sys.argv[1:])))
