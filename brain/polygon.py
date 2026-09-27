"""Полигон: проверка умений Альтрона в настоящей игре, без человека — сотни сценариев.

Запускает мозг, игру-хозяина с новым тестовым миром AltronTest_Polygon_* и клиент Альтрона. Перед каждым
сценарием площадка очищается и строится заново (лестница нужного мода, повернутая на нужную сторону, нужной
высоты; рычаг на стене/полу/потолке; дверь с петлёй слева/справа; сундук с нужными вещами...), Альтрон и
командир ставятся на старт, выполняется действие, и результат проверяется по САМОМУ МИРУ (сервер хозяина): где
Альтрон стоит, что у него и у командира в инвентаре, повернулся ли рычаг, открылась ли дверь, честно ли он
сказал, если не вышло. В конце отчёт (altron\\polygon_report.txt); тестовый мир и его память удаляются.
Настоящий мир и настоящая память Альтрона не трогаются.

    polygon.py                 все сценарии тела (каждое умение вызывается напрямую)
    polygon.py ladder door     только эти семейства/сценарии (по началу имени)
    polygon.py --llm           ещё и голосовые приказы через нейросеть (нужна модель на этом ПК)
    polygon.py --keep          не удалять тестовый мир
    polygon.py --list          только показать список сценариев
"""
import asyncio
import json
import math
import re
import shutil
import sys
import time

import altron
from launcher import BRAIN_DIR, launch_host, rel

COMMANDER = "MJreggich"
TEST_PORT = 47850     # not the real brain's port: a game of the commander's that is open never meets the test brain
STAGE = 18            # the stage is cleared in this radius before every scenario
HOST_AWAY = (-16, 0, 16)
WALL = "minecraft:stone_bricks"
DIRS = {"north": (0, -1), "east": (1, 0), "south": (0, 1), "west": (-1, 0)}
FACINGS = list(DIRS)


def right(f):
    return FACINGS[(FACINGS.index(f) + 1) % 4]


def opp(f):
    return FACINGS[(FACINGS.index(f) + 2) % 4]


class Sc:
    """One scenario, in the frame of its facing F: a = forward (toward F), b = to the right, y = up from the ground."""

    def __init__(self, family, name, title, act, f="north", blocks=(), containers=(), bot=(0, 0, 0), host=HOST_AWAY,
                 give_bot=(), cows=0, key=None, timeout=150, clear_host=True, stage=True, mobs=(), extra=None):
        self.family, self.name, self.title, self.act = family, name, title, act
        self.f, self.blocks, self.containers = f, list(blocks), list(containers)
        self.bot, self.host, self.give_bot, self.cows = bot, host, list(give_bot), cows
        self.key, self.timeout, self.clear_host = key, timeout, clear_host
        self.stage, self.mobs, self.extra = stage, list(mobs), dict(extra or {})
        self.info = {}

    def state(self, s):
        return s.format(F=self.f, B=opp(self.f), R=right(self.f), L=opp(right(self.f)))


class Course:
    def __init__(self, hub, log):
        self.hub, self.log = hub, log
        self.o = [0, 0, 0]
        self.ladders = ["minecraft:ladder"]   # + the pack's own ladders (asked from the server)
        self.results = []

    # ---------------------------------------------------------------- positions
    def w(self, sc, a, y, b):
        """Scenario frame -> world block."""
        f, r = DIRS[sc.f], DIRS[right(sc.f)]
        return [self.o[0] + a * f[0] + b * r[0], self.o[1] + y, self.o[2] + a * f[1] + b * r[1]]

    def ws(self, sc, a, y, b):
        x, y, z = self.w(sc, a, y, b)
        return [x + 0.5, y, z + 0.5]

    def local(self, sc, pos):
        """World position -> scenario frame (a, y, b)."""
        f, r = DIRS[sc.f], DIRS[right(sc.f)]
        dx, dz = pos[0] - (self.o[0] + 0.5), pos[2] - (self.o[2] + 0.5)
        return [dx * f[0] + dz * f[1], pos[1] - self.o[1], dx * r[0] + dz * r[1]]

    # ---------------------------------------------------------------- world state
    async def world(self, **what):
        r = await self.hub.probe(**what)
        if r.get("error"):
            raise RuntimeError("мир: " + r["error"])
        return r

    async def bot_at(self, sc):
        return self.local(sc, (await self.world())["bot"]["pos"])

    async def block(self, pos):
        r = await self.world(blocks=[pos])
        return r["blocks"]["%d,%d,%d" % tuple(pos)]

    async def dist_to_host(self):
        r = await self.world()
        return math.dist(r["bot"]["pos"], r["host"]["pos"])

    async def items(self, who, item, want=None, wait=0):
        """How many of item the bot/host has (waits up to `wait` s for `want`)."""
        t, n = time.time(), 0
        while True:
            n = (await self.world())[who]["items"].get(item, 0)
            if want is None or n >= want or time.time() - t >= wait:
                return n
            await asyncio.sleep(1)

    async def tool(self, name, args, wait=90):
        t = time.time()
        r = await self.hub.run_tool(name, args, wait)
        self.log("  %s %s -> %s (%.0f с)" % (name, json.dumps(args, ensure_ascii=False), r[:240].replace("\n", " | "),
                                            time.time() - t))
        return r

    async def use(self, pos, wait=40):
        return await self.tool("use_block", dict(zip("xyz", pos)), wait)

    # ---------------------------------------------------------------- staging
    async def prepare(self):
        info = await self.world(climbables=True, fall_damage=False)
        mods = sorted(c for c in info.get("climbables", []) if not c.startswith("minecraft:") and "ladder" in c)
        self.ladders = ["minecraft:ladder"] + mods
        self.log("Лестницы для проверки: %s" % ", ".join(self.ladders))
        hub = self.hub
        hub.setup_result = None
        hub.send(hub.host, {"type": "setup", "time": 6000, "clear_weather": True, "daylight_cycle": False,
                            "mob_spawning": False, "flatten": STAGE + 8})
        if not await wait_for(lambda: hub.setup_result is not None, 120):
            raise RuntimeError("площадка не выровнена")
        self.o = hub.setup_result["origin"]
        self.log("Площадка: центр %s" % self.o)

    async def ensure_bot(self):
        """Altron must be in the world: a mod's server error can throw him out (SuperbWarfare's vehicle gun did).
        Wait for him to come back; if he does not, start his client again."""
        from launcher import launch_bot
        t = time.time()
        restarted = False
        while True:
            w = await self.hub.probe()
            if w.get("bot"):
                return
            if time.time() - t < 3:
                self.log("Альтрона нет в мире — жду, пока он зайдёт обратно...")
            if not restarted and time.time() - t > 90:
                restarted = True
                self.log("Не вернулся сам — перезапускаю его клиент.")
                self.hub.stop_bot()
                await asyncio.sleep(5)
                self.hub.joined = False
                self.hub.bot_proc = launch_bot(self.hub.cfg, self.hub.bot_server, self.hub.log, lite=self.hub.bot_lite)
            if time.time() - t > 900:
                raise RuntimeError("Альтрон не вернулся в мир за 15 минут")
            await asyncio.sleep(5)

    async def stage(self, sc):
        """Clear the stage, build the scenario, put everyone on their spots."""
        await self.ensure_bot()
        await self.hub.run_tool("stop", {}, 5)
        await self.hub.bot_call("close_container", {})
        o, s = self.o, STAGE
        blocks = [self.w(sc, a, y, b) + [sc.state(st)] for a, y, b, st in sc.blocks]
        containers = [{"pos": self.w(sc, *c["at"]), "block": sc.state(c.get("block", "minecraft:chest")),
                       "items": c["items"]} for c in sc.containers]
        await self.world(
            fill=[[o[0] - s, o[1], o[2] - s, o[0] + s, o[1] + 16, o[2] + s, "minecraft:air"],
                  [o[0] - s, o[1] - 3, o[2] - s, o[0] + s, o[1] - 2, o[2] + s, "minecraft:dirt"],
                  [o[0] - s, o[1] - 1, o[2] - s, o[0] + s, o[1] - 1, o[2] + s, "minecraft:grass_block"]],
            clear_entities=[o[0], o[1], o[2], s + 12], heal=True, clear_bot=True, clear_host=sc.clear_host,
            containers=containers, give_bot=sc.give_bot, set=blocks,
            tp_bot=self.ws(sc, *sc.bot), tp_host=self.ws(sc, *sc.host), **sc.extra)
        mobs = [(3 + i % 3, 1, -3 + i // 3, "minecraft:cow") for i in range(sc.cows)] + sc.mobs
        if mobs:
            self.hub.setup_result = None
            self.hub.send(self.hub.host, {"type": "setup", "relative": False, "mobs": [
                [self.w(sc, a, 0, b)[0], y, self.w(sc, a, 0, b)[2], kind] for a, y, b, kind in mobs]})
            await wait_for(lambda: self.hub.setup_result is not None, 10)
        if sc.key:
            a, y, b, want = sc.key
            got = await self.block(self.w(sc, a, y, b))
            if not got.startswith(sc.state(want)):
                return "не построилось: %s вместо %s" % (got, sc.state(want))
        await asyncio.sleep(1.5)   # the blocks reach Altron's client, he glances around
        return ""


# ============================================================================ scenario families
def covered(lid):
    """Immersive Engineering ladders with a scaffold cage: closed on three sides, one goes in from above or below."""
    return lid.startswith("immersiveengineering:") and not lid.endswith("_none")


def short_name(lid):
    return lid.split(":")[1].replace("metal_ladder_", "ie_").replace("dark_steel_ladder", "enderio").replace(
        "reinforced_ladder", "sc_reinforced")


def ladder_column(lid, h, a=0, b=0, y0=0):
    """A ladder column; a covered IE ladder gets two open (uncovered) rungs at the bottom — a player is 1.8 tall and walks in under the cage — as one builds them to get in."""
    out = []
    for y in range(y0, y0 + h):
        kind = "immersiveengineering:metal_ladder_none" if covered(lid) and y < y0 + 2 else lid
        out.append((a, y, b, kind + "[facing={F}]"))
    return out


def ladder_family(course):
    out = []
    for lid in course.ladders:
        short = short_name(lid)
        for f in FACINGS:
            for h in (4, 6, 10):
                blocks = [(a, y, b, WALL) for a in (-3, -2, -1) for b in (-1, 0, 1) for y in range(h)]
                blocks += ladder_column(lid, h)
                key = (0, 2, 0, lid)
                tag = "%s_%s_h%d" % (short, f, h)

                def up(start):
                    async def act(c, sc):
                        r = await c.tool("climb", {"direction": "up"}, 90)
                        p = await c.bot_at(sc)
                        return p[1] >= sc.info["h"] - 0.1, "высота %.1f из %d (%s)" % (p[1], sc.info["h"], r[:120])
                    return act

                async def down(c, sc):
                    r = await c.tool("climb", {"direction": "down"}, 90)
                    p = await c.bot_at(sc)
                    return -0.5 < p[1] <= 0.5, "высота %.1f (%s)" % (p[1], r[:120])

                async def go_top(c, sc):
                    top = c.w(sc, -2, sc.info["h"], 0)
                    r = await c.tool("goto", dict(zip("xyz", top)), 120)
                    p = await c.bot_at(sc)
                    return p[1] >= sc.info["h"] - 0.1, "высота %.1f из %d (%s)" % (p[1], sc.info["h"], r[:120])

                for start, where in (((3, 0, 0), "спереди"), ((1, 0, 3), "сбоку"), ((-6, 0, 0), "из-за башни")):
                    sc = Sc("ladder", "ladder_up_%s_%s" % (tag, where.replace(" ", "")[:5]),
                            "лезет вверх по %s (лицом на %s, высота %d), старт %s" % (lid, f, h, where),
                            up(start), f, blocks, bot=start, key=key)
                    sc.info["h"] = h
                    out.append(sc)
                sc = Sc("ladder", "ladder_down_%s" % tag, "спускается по %s (лицом на %s, высота %d)" % (lid, f, h),
                        down, f, blocks, bot=(-2, h, 0), key=key)
                sc.info["h"] = h
                out.append(sc)
                sc = Sc("ladder", "ladder_goto_%s" % tag, "«иди на башню»: сам находит %s (лицом на %s, высота %d)"
                        % (lid, f, h), go_top, f, blocks, bot=(4, 0, -2), key=key, timeout=180)
                sc.info["h"] = h
                out.append(sc)
    return out


def honest(before, after, reply):
    """If the block did not change, he must say so; if it changed, he must not claim it did not."""
    return (before != after) != ("НЕ изменился" in reply)


def toggle_family():
    """Levers, buttons, doors, trapdoors, fence gates: used twice, checked by the block's state on the server."""
    out = []

    def toggler(prop, pos_ab, expect_change=True, twice=True):
        async def act(c, sc):
            pos = c.w(sc, *pos_ab)
            before = await c.block(pos)
            r1 = await c.use(pos)
            mid = await c.block(pos)
            if not r1.startswith("ГОТОВО"):
                return False, "не выполнил: " + r1[:160]
            if not honest(before, mid, r1):
                return False, "сказал неправду: было %s, стало %s, ответ «%s»" % (before, mid, r1[:120])
            if not expect_change:
                return mid == before, "не открывается рукой — и честно сказал об этом" if mid == before else "изменилось?! " + mid
            if "%s=true" % prop not in mid:
                return False, "не сработало: %s (%s)" % (mid, r1[:120])
            if not twice:
                return True, "сработало"
            r2 = await c.use(pos)
            end = await c.block(pos)
            return "%s=false" % prop in end, "включил и выключил" if "%s=false" % prop in end else "второй раз не сработало: " + end
        return act

    # levers: on a wall (4 sides), on the floor, on the ceiling; from near and from 7 blocks away
    for f in FACINGS:
        for place, blocks, at in (
                ("wall", [(0, 0, 0, WALL), (0, 1, 0, WALL), (1, 1, 0, "minecraft:lever[face=wall,facing={F},powered=false]")], (1, 1, 0)),
                ("floor", [(1, 0, 0, "minecraft:lever[face=floor,facing={F},powered=false]")], (1, 0, 0)),
                ("ceiling", [(1, 3, 0, WALL), (1, 2, 0, "minecraft:lever[face=ceiling,facing={F},powered=false]")], (1, 2, 0))):
            for start, dist in (((4, 0, 0), "рядом"), ((8, 0, 3), "издалека")):
                out.append(Sc("lever", "lever_%s_%s_%s" % (place, f, "near" if dist == "рядом" else "far"),
                              "рычаг на %s (лицом на %s), %s" % ({"wall": "стене", "floor": "полу", "ceiling": "потолке"}[place], f, dist),
                              toggler("powered", at), f, blocks, bot=start, key=at + ("minecraft:lever",)))
    # SecurityCraft: reinforced lever with no owner here — he must be honest if it does not obey him
    for f in FACINGS:
        blocks = [(0, 0, 0, WALL), (0, 1, 0, WALL), (1, 1, 0, "securitycraft:reinforced_lever[face=wall,facing={F},powered=false]")]

        async def sc_lever(c, sc):
            pos = c.w(sc, 1, 1, 0)
            before = await c.block(pos)
            r = await c.use(pos)
            after = await c.block(pos)
            return honest(before, after, r), ("сработал" if before != after else "не послушался — и честно сказал") \
                if honest(before, after, r) else "сказал неправду: «%s»" % r[:120]
        out.append(Sc("lever", "lever_securitycraft_%s" % f, "укреплённый рычаг SecurityCraft (лицом на %s): честный ответ" % f,
                      sc_lever, f, blocks, bot=(4, 0, 0), key=(1, 1, 0, "securitycraft:reinforced_lever")))
    # buttons spring back: the answer must say it worked
    for kind in ("stone_button", "oak_button"):
        for f in FACINGS + ["floor", "ceiling"]:
            ff = f if f in DIRS else "north"
            if f in DIRS:
                blocks, at = [(0, 0, 0, WALL), (0, 1, 0, WALL), (1, 1, 0, "minecraft:%s[face=wall,facing={F},powered=false]" % kind)], (1, 1, 0)
            elif f == "floor":
                blocks, at = [(1, 0, 0, "minecraft:%s[face=floor,facing={F},powered=false]" % kind)], (1, 0, 0)
            else:
                blocks, at = [(1, 3, 0, WALL), (1, 2, 0, "minecraft:%s[face=ceiling,facing={F},powered=false]" % kind)], (1, 2, 0)

            async def press(c, sc, at=at):
                r = await c.use(c.w(sc, *at))
                ok = "НЕ изменился" not in r and r.startswith("ГОТОВО")
                return ok, "нажал" if ok else "ответ «%s»" % r[:160]
            out.append(Sc("button", "button_%s_%s" % (kind.split("_")[0], f), "кнопка %s (%s)" % (kind, f), press, ff, blocks,
                          bot=(4, 0, 0), key=at + ("minecraft:" + kind,)))
    # doors: 5 wooden kinds + iron, 4 sides, hinge left/right
    for kind in ("oak_door", "spruce_door", "birch_door", "dark_oak_door", "mangrove_door", "iron_door"):
        for f in FACINGS:
            for hinge in ("left", "right"):
                blocks = [(0, y, b, WALL) for b in (-2, -1, 1, 2) for y in range(3)] + [(0, 2, 0, WALL)]
                blocks += [(0, 0, 0, "minecraft:%s[facing={F},half=lower,hinge=%s,open=false]" % (kind, hinge)),
                           (0, 1, 0, "minecraft:%s[facing={F},half=upper,hinge=%s,open=false]" % (kind, hinge))]
                out.append(Sc("door", "door_use_%s_%s_%s" % (kind.replace("_door", ""), f, hinge),
                              "дверь %s (лицом на %s, петли %s): открыть и закрыть" % (kind, f, hinge),
                              toggler("open", (0, 0, 0), expect_change=kind != "iron_door"), f, blocks, bot=(3, 0, 1),
                              key=(0, 0, 0, "minecraft:" + kind)))
    # trapdoors: bottom and top half, 4 sides; iron ones do not open by hand
    for kind in ("oak_trapdoor", "spruce_trapdoor", "iron_trapdoor"):
        for f in FACINGS:
            for half in ("bottom", "top"):
                y = 0 if half == "bottom" else 1
                blocks = [(1, y, 0, "minecraft:%s[facing={F},half=%s,open=false]" % (kind, half))]
                out.append(Sc("trapdoor", "trapdoor_%s_%s_%s" % (kind.replace("_trapdoor", ""), f, half),
                              "люк %s (%s, %s)" % (kind, f, half), toggler("open", (1, y, 0), expect_change=kind != "iron_trapdoor"),
                              f, blocks, bot=(4, 0, 1), key=(1, y, 0, "minecraft:" + kind)))
    # fence gates
    for kind in ("oak_fence_gate", "spruce_fence_gate", "birch_fence_gate"):
        fence = "minecraft:" + kind.replace("_gate", "")
        for f in FACINGS:
            blocks = [(0, 0, b, fence) for b in (-2, -1, 1, 2)] + [(0, 0, 0, "minecraft:%s[facing={F},open=false]" % kind)]
            out.append(Sc("gate", "gate_use_%s_%s" % (kind.replace("_fence_gate", ""), f), "калитка %s (%s)" % (kind, f),
                          toggler("open", (0, 0, 0)), f, blocks, bot=(3, 0, 1), key=(0, 0, 0, "minecraft:" + kind)))
    return out


def walk_through_family():
    """Into a closed room through its door / into a fenced pen through its gate."""
    out = []

    async def inside(c, sc):
        r = await c.tool("goto", dict(zip("xyz", c.w(sc, -4, 0, 0))), 90)
        p = await c.bot_at(sc)
        ok = -5.9 < p[0] < -2.55 and abs(p[2]) < 1.9 and -0.5 < p[1] < 1.5   # past the doorway, really inside
        return ok, "внутри" if ok else "стоит в %s (%s)" % ([round(v, 1) for v in p], r[:120])

    for kind in ("oak_door", "spruce_door", "birch_door"):
        for f in FACINGS:
            for is_open in ("false", "true"):
                blocks = [(a, y, b, "minecraft:obsidian") for a in range(-6, -1) for b in range(-2, 3) for y in range(-1, 4)
                          if a in (-6, -2) or b in (-2, 2) or y in (-1, 3)]
                blocks = [bl for bl in blocks if not (bl[0] == -2 and bl[2] == 0 and 0 <= bl[1] < 2)]
                blocks += [(-2, 0, 0, "minecraft:%s[facing={F},half=lower,hinge=left,open=%s]" % (kind, is_open)),
                           (-2, 1, 0, "minecraft:%s[facing={F},half=upper,hinge=left,open=%s]" % (kind, is_open))]
                out.append(Sc("room", "room_%s_%s_%s" % (kind.replace("_door", ""), f, "open" if is_open == "true" else "closed"),
                              "зайти в комнату через %s дверь %s (вход с %s)" % ("открытую" if is_open == "true" else "закрытую", kind, f),
                              inside, f, blocks, bot=(3, 0, 1), key=(-2, 0, 0, "minecraft:" + kind)))
    for kind in ("oak_fence_gate", "spruce_fence_gate"):
        fence = "minecraft:" + kind.replace("_gate", "")
        for f in FACINGS:
            blocks = [(a, 0, b, fence) for a in range(-6, -1) for b in range(-2, 3) if (a in (-6, -2) or b in (-2, 2))]
            blocks = [bl for bl in blocks if not (bl[0] == -2 and bl[2] == 0)] + [(-2, 0, 0, "minecraft:%s[facing={F},open=false]" % kind)]
            out.append(Sc("room", "pen_%s_%s" % (kind.replace("_fence_gate", ""), f), "зайти в загон через калитку %s (вход с %s)" % (kind, f),
                          inside, f, blocks, bot=(3, 0, 1), key=(-2, 0, 0, "minecraft:" + kind)))
    return out


ITEMS = [("minecraft:iron_ingot", 7), ("minecraft:bread", 5), ("minecraft:cobblestone", 64), ("minecraft:diamond", 1),
         ("minecraft:arrow", 20)]
CONTAINERS = ["minecraft:chest[facing={B}]", "minecraft:barrel[facing=up]", "minecraft:trapped_chest[facing={B}]"]


def container_family():
    out = []
    for ci, cont in enumerate(CONTAINERS):
        cname = cont.split(":")[1].split("[")[0]
        for ii, (item, n) in enumerate(ITEMS):
            f = FACINGS[(ci + ii) % 4]

            async def take(c, sc, item=item, n=n):
                r = await c.use(c.w(sc, 3, 0, 0))
                if not r.startswith("ГОТОВО"):
                    return False, "не открыл: " + r[:120]
                r2 = (await c.hub.bot_call("container_take", {"item": item, "count": n})).get("msg", "")
                await c.hub.bot_call("close_container", {})
                got = await c.items("bot", item, n, 3)
                return got == n, "взял %d" % got if got == n else "в инвентаре %d из %d (%s)" % (got, n, r2)
            out.append(Sc("chest", "take_%s_%s" % (cname, item.split(":")[1]), "открыть %s и взять %d %s" % (cname, n, item),
                          take, f, containers=[{"at": (3, 0, 0), "block": cont, "items": [[item, n]]}], bot=(0, 0, 0),
                          key=(3, 0, 0, cont.split("[")[0])))

            async def fetch(c, sc, item=item, n=n):
                t = time.time()
                r = await c.hub.fetch({"item": item, "count": n, "player": COMMANDER})
                c.log("  fetch -> %s (%.0f с)" % (r[:200], time.time() - t))
                got = await c.items("host", item, n, 8)
                return got >= n, "принёс %d" % got if got >= n else "командир получил %d из %d (%s)" % (got, n, r[:120])
            out.append(Sc("fetch", "fetch_%s_%s" % (cname, item.split(":")[1]),
                          "«принеси %s»: лежит в %s, куда он не заглядывал" % (item, cname), fetch, f,
                          containers=[{"at": (7, 0, 3), "block": cont, "items": [[item, n]]}], bot=(0, 0, 0), host=(-3, 0, -3),
                          key=(7, 0, 3, cont.split("[")[0])))
    for ii, (item, n) in enumerate(ITEMS[:3]):
        async def fetch2(c, sc, item=item, n=n):
            r = await c.hub.fetch({"item": item, "count": n, "player": COMMANDER})
            c.log("  fetch -> %s" % r[:200])
            got = await c.items("host", item, n, 8)
            return got >= n, "принёс %d" % got if got >= n else "командир получил %d из %d (%s)" % (got, n, r[:120])
        out.append(Sc("fetch", "fetch_second_chest_%s" % item.split(":")[1], "«принеси %s»: в первом сундуке нет, во втором есть" % item,
                      fetch2, FACINGS[ii], containers=[{"at": (4, 0, -3), "items": [["minecraft:dirt", 10]]},
                                                       {"at": (6, 0, 5), "items": [[item, n]]}],
                      bot=(0, 0, 0), host=(-3, 0, -3), key=(6, 0, 5, "minecraft:chest")))
    for ii, (item, n) in enumerate(ITEMS):
        for dist in (3, 12):
            async def give(c, sc, item=item, n=n):
                await c.tool("give", {"item": item, "count": n}, 60)
                got = await c.items("host", item, n, 8)
                return got >= n, "отдал %d" % got if got >= n else "у командира %d из %d" % (got, n)
            out.append(Sc("give", "give_%s_%d" % (item.split(":")[1], dist), "отдать командиру %d %s (он в %d бл.)" % (n, item, dist),
                          give, FACINGS[ii % 4], give_bot=[[item, n]], bot=(0, 0, 0), host=(dist, 0, 0)))
    return out


def move_family():
    out = []
    for f in FACINGS:
        for d in (4, 8, 16, 24):
            async def come(c, sc):
                r = await c.tool("come", {}, 90)
                dd = await c.dist_to_host()
                return dd <= 3.5, "подошёл (%.1f бл.)" % dd if dd <= 3.5 else "остался в %.1f бл. (%s)" % (dd, r[:120])
            out.append(Sc("come", "come_%s_%d" % (f, d), "«иди сюда» с %d бл. (%s)" % (d, f), come, f,
                          bot=(-d // 2, 0, 0), host=(d - d // 2, 0, 0)))
    routes = {"square": [(12, 0, 12), (-12, 0, 12), (-12, 0, -12), (12, 0, -12)],
              "zigzag": [(10, 0, -8), (-10, 0, -2), (10, 0, 4), (-10, 0, 10)],
              "back_forth": [(14, 0, 0), (-14, 0, 0), (14, 0, 0), (-14, 0, 0)],
              "far": [(15, 0, 15), (-15, 0, -15), (15, 0, -15), (-15, 0, 15)]}
    for name, route in routes.items():
        async def follow(c, sc, route=route):
            await c.tool("follow", {}, 0)
            lag = []
            for point in route:
                await c.world(tp_host=c.ws(sc, *point))
                t, d = time.time(), 99.0
                while time.time() - t < 45:
                    await asyncio.sleep(1)
                    d = await c.dist_to_host()
                    if d <= 4.5:
                        break
                lag.append((point, d, time.time() - t))
            await c.hub.run_tool("stop", {}, 5)
            bad = [(p, d) for p, d, _ in lag if d > 4.5]
            return not bad, ("догонял за %s с" % "/".join("%.0f" % s for _, _, s in lag)) if not bad else \
                "отстал: " + "; ".join("у %s — %.0f бл." % (p, d) for p, d in bad)
        out.append(Sc("follow", "follow_%s" % name, "«иди за мной»: маршрут %s" % name, follow, "north",
                      bot=(0, 0, 0), host=(2, 0, 0), timeout=240))

    def goto_check(target, tol=1.9):
        async def act(c, sc):
            r = await c.tool("goto", dict(zip("xyz", c.w(sc, *target))), 120)
            p = await c.bot_at(sc)
            d = math.dist([p[0], p[1], p[2]], [target[0] + 0.5 * 0, target[1], target[2]])
            ok = math.hypot(p[0] - target[0], p[2] - target[2]) <= tol + 0.6 and abs(p[1] - target[1]) < 1.1
            return ok, "дошёл" if ok else "стоит в %s, цель %s (%s)" % ([round(v, 1) for v in p], list(target), r[:120])
        return act

    for i, t in enumerate([(6, 0, 0), (0, 0, 10), (-12, 0, 5), (14, 0, -14), (-15, 0, -15), (3, 0, -9),
                           (15, 0, 15), (-8, 0, 12), (10, 0, 3), (-3, 0, -14), (7, 0, 7), (-14, 0, 0)]):
        out.append(Sc("goto", "goto_flat_%d" % i, "дойти до точки %s по ровному" % (t,), goto_check(t), "north", bot=(0, 0, 0)))
    for f in FACINGS:
        wall = [(0, y, b, WALL) for b in range(-4, 5) for y in range(3)]
        out.append(Sc("goto", "goto_around_wall_%s" % f, "обойти стену (%s)" % f, goto_check((5, 0, 0)), f, wall, bot=(-4, 0, 0)))
        steps = [(-a, y, 0, "minecraft:stone") for a in range(1, 5) for y in range(a)]
        out.append(Sc("goto", "goto_steps_%s" % f, "подняться по уступам в 1 блок на высоту 4 (%s)" % f,
                      goto_check((-4, 4, 0), 1.2), f, steps, bot=(3, 0, 0)))
        stairs = [(-a, a - 1, 0, "minecraft:oak_stairs[facing={B},half=bottom]") for a in range(1, 5)]
        stairs += [(-a, y, 0, "minecraft:oak_planks") for a in range(2, 6) for y in range(a - 1)] + [(-5, 3, 0, "minecraft:oak_planks")]
        out.append(Sc("goto", "goto_stairs_%s" % f, "подняться по деревянной лестнице-ступенькам (%s)" % f,
                      goto_check((-5, 4, 0), 1.2), f, stairs, bot=(3, 0, 0)))
        cliff = [(a, y, b, WALL) for a in (-3, -2, -1) for b in (-1, 0, 1) for y in range(3)]
        out.append(Sc("goto", "goto_down_cliff_%s" % f, "спуститься с уступа высотой 3 (%s)" % f,
                      goto_check((4, 0, 0)), f, cliff, bot=(-2, 3, 0)))
        trench = [(0, y, b, "minecraft:air") for b in range(-6, 7) for y in (-1, -2, -3)]
        out.append(Sc("goto", "goto_trench_%s" % f, "пройти через канаву шириной 1 и глубиной 3 (%s)" % f,
                      goto_check((4, 0, 0)), f, trench, bot=(-4, 0, 0)))
    return out


def see_family(course):
    out = []
    # a pillar 9 blocks ahead, the thing on its side facing Altron
    kinds = [(lid, [(1, y, 0, WALL) for y in range(4)] + [(0, y, 0, lid + "[facing={B}]") for y in range(4)], (0, 0, 0))
             for lid in course.ladders]
    kinds += [("minecraft:lever", [(1, 0, 0, WALL), (1, 1, 0, WALL), (0, 1, 0, "minecraft:lever[face=wall,facing={B}]")], (0, 1, 0)),
              ("minecraft:stone_button", [(1, 0, 0, WALL), (1, 1, 0, WALL), (0, 1, 0, "minecraft:stone_button[face=wall,facing={B}]")], (0, 1, 0)),
              ("minecraft:oak_door", [(0, 0, 0, "minecraft:oak_door[facing={F},half=lower]"), (0, 1, 0, "minecraft:oak_door[facing={F},half=upper]")], (0, 0, 0)),
              ("minecraft:iron_door", [(0, 0, 0, "minecraft:iron_door[facing={F},half=lower]"), (0, 1, 0, "minecraft:iron_door[facing={F},half=upper]")], (0, 0, 0)),
              ("minecraft:oak_trapdoor", [(0, 0, 0, "minecraft:oak_trapdoor[facing={F}]")], (0, 0, 0)),
              ("minecraft:oak_fence_gate", [(0, 0, 0, "minecraft:oak_fence_gate[facing={F}]")], (0, 0, 0)),
              ("minecraft:chest", [(0, 0, 0, "minecraft:chest[facing={F}]")], (0, 0, 0)),
              ("minecraft:barrel", [(0, 0, 0, "minecraft:barrel[facing=up]")], (0, 0, 0))]
    for i, (bid, blocks, at) in enumerate(kinds):
        for cows in (0, 8):
            f = FACINGS[i % 4]

            async def see(c, sc, bid=bid):
                await asyncio.sleep(2.5)
                res = await c.hub.bot_call("nearby", {"radius": 16})
                told = altron.near_text(res.get("msg", ""))
                ok = bid in told
                return ok, "видит" if ok else "не видит; ему сказано: " + told.replace("\n", " | ")[:300]
            out.append(Sc("see", "see_%s_%s" % (bid.split(":")[1], "crowd" if cows else "alone"),
                          "видит %s в 10 бл.%s" % (bid, ", когда рядом 8 коров" if cows else ""), see, f,
                          [(a + 8, y, b, st) for a, y, b, st in blocks], bot=(0, 0, 0), host=(-2, 0, 2), cows=cows,
                          key=(at[0] + 8, at[1], at[2], bid)))
    return out


# ---------------------------------------------------------------------------- by voice through the AI (--llm)
def ai_family(course):
    out = []

    async def order(c, phrase, minutes=3):
        c.log("  %s: «%s»" % (COMMANDER, phrase))
        await c.hub.handle_phrase(COMMANDER, phrase)
        await wait_idle(c.hub, minutes)

    for lid in course.ladders[:2]:
        blocks = [(a, y, b, WALL) for a in (-3, -2, -1) for b in (-1, 0, 1) for y in range(6)]
        blocks += [(0, y, 0, lid + "[facing={F}]") for y in range(6)]

        async def ladder(c, sc):
            await order(c, "Альтрон, залезь по лестнице на башню.")
            p = await c.bot_at(sc)
            return p[1] >= 5.9, "залез" if p[1] >= 5.9 else "не залез (высота %.1f)" % p[1]
        out.append(Sc("ai", "ai_ladder_%s" % lid.split(":")[1], "голосом: «залезь по лестнице на башню» (%s)" % lid, ladder,
                      "north", blocks, bot=(3, 0, 1), host=(4, 0, -1), key=(0, 0, 0, lid), timeout=240))

    async def lever(c, sc):
        await order(c, "Альтрон, нажми рычаг.")
        st = await c.block(c.w(sc, 1, 1, 0))
        return "powered=true" in st, "нажал" if "powered=true" in st else "не нажал"
    out.append(Sc("ai", "ai_lever", "голосом: «нажми рычаг»", lever, "north",
                  [(0, 0, 0, WALL), (0, 1, 0, WALL), (1, 1, 0, "minecraft:lever[face=wall,facing={F},powered=false]")],
                  bot=(4, 0, 1), host=(4, 0, -1), timeout=240))

    async def door(c, sc):
        await order(c, "Альтрон, открой дверь.")
        st = await c.block(c.w(sc, 0, 0, 0))
        return "open=true" in st, "открыл" if "open=true" in st else "не открыл"
    out.append(Sc("ai", "ai_door", "голосом: «открой дверь»", door, "north",
                  [(0, y, b, WALL) for b in (-2, -1, 1, 2) for y in range(3)] + [(0, 2, 0, WALL),
                   (0, 0, 0, "minecraft:oak_door[facing={F},half=lower,open=false]"),
                   (0, 1, 0, "minecraft:oak_door[facing={F},half=upper,open=false]")],
                  bot=(3, 0, 1), host=(3, 0, -1), timeout=240))

    async def fetch(c, sc):
        await order(c, "Альтрон, принеси мне хлеб.")
        got = await c.items("host", "minecraft:bread", 1, 5)
        return got >= 1, "принёс %d" % got if got else "не принёс"
    out.append(Sc("ai", "ai_fetch", "голосом: «принеси мне хлеб»", fetch, "north",
                  containers=[{"at": (7, 0, 3), "items": [["minecraft:bread", 5]]}], bot=(0, 0, 0), host=(-2, 0, -2), timeout=300))

    async def come(c, sc):
        await order(c, "Альтрон, иди сюда.")
        d = await c.dist_to_host()
        return d <= 3.5, "подошёл" if d <= 3.5 else "остался в %.1f бл." % d
    out.append(Sc("ai", "ai_come", "голосом: «иди сюда»", come, "north", bot=(-8, 0, 0), host=(8, 0, 0), timeout=240))

    async def follow(c, sc):
        await order(c, "Альтрон, иди за мной.", 1)
        await c.world(tp_host=c.ws(sc, 12, 0, 12))
        t, d = time.time(), 99.0
        while time.time() - t < 45 and d > 4.5:
            await asyncio.sleep(1)
            d = await c.dist_to_host()
        await c.hub.run_tool("stop", {}, 5)
        return d <= 4.5, "пошёл следом" if d <= 4.5 else "не пошёл (%.1f бл.)" % d
    out.append(Sc("ai", "ai_follow", "голосом: «иди за мной»", follow, "north", bot=(0, 0, 0), host=(2, 0, 0), timeout=240))

    # questions: he must ANSWER (with the real ingredients), not run off to make the thing, and not stay silent
    asks = [("Альтрон, что мне нужно, чтобы сделать коксовую печь?", r"кирпич|глин|песчаник"),
            ("Что нужно для дизельного генератора?", r"металл|генератор|радиатор|блок|сталь|двигател|трубоп"),
            ("Из чего делается порох?", r"селитр|нитрат|угол|угл|сер[аы]"),
            ("Как зарядить вертолёт?", r"станц|заряд|энерги|генератор"),
            ("Какие ресурсы нужны для доменной печи?", r"кирпич|огнеупор|блок"),
            ("Что нужно, чтобы построить нефтекачалку?", r"сталь|стальн|металл|блок|каркас|леса|труб|рабоч"),
            ("Из чего сделать железную кирку?", r"желез|палк|слит"),
            ("Где взять нефть?", r"нефт|качалк|скважин|залеж|вышк"),
            ("Сколько нужно железа на наковальню?", r"\d|желез")]
    for i, (q, want) in enumerate(asks):
        async def ask(c, sc, q=q, want=want):
            said = c.said
            n0 = len(said)
            p0 = await c.bot_at(sc)
            await order(c, q, 3)
            answer = " ".join(said[n0:])
            p1 = await c.bot_at(sc)
            moved = math.hypot(p1[0] - p0[0], p1[2] - p0[2])
            if not answer.strip():
                return False, "промолчал"
            if moved > 3 or c.hub.running is not None:
                return False, "вместо ответа начал делать (ушёл на %.0f бл.): «%s»" % (moved, answer[:140])
            ok = bool(re.search(want, answer.lower()))
            return ok, ("ответил: «%s»" if ok else "ответил не по делу: «%s»") % answer[:220]
        out.append(Sc("ask", "ask_%02d" % i, "вопрос: «%s»" % q, ask, "north", bot=(0, 0, 0), host=(2, 0, 1), timeout=240))
    return out


# ---------------------------------------------------------------------------- hard: several skills at once
def maze(seed, cells=7):
    """A perfect maze (recursive backtracker) of cells x cells rooms: walls as (a, b) block columns, centred on 0."""
    import random
    rnd = random.Random(seed)
    size = cells * 2 + 1
    wall = [[True] * size for _ in range(size)]
    stack = [(0, 0)]
    seen = {(0, 0)}
    wall[1][1] = False
    while stack:
        cx, cy = stack[-1]
        nbrs = [(cx + dx, cy + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                if 0 <= cx + dx < cells and 0 <= cy + dy < cells and (cx + dx, cy + dy) not in seen]
        if not nbrs:
            stack.pop()
            continue
        nx, ny = rnd.choice(nbrs)
        wall[ny * 2 + 1][nx * 2 + 1] = False
        wall[cy + ny + 1][cx + nx + 1] = False
        seen.add((nx, ny))
        stack.append((nx, ny))
    wall[1][0] = False                        # the way in (west side, first row)
    wall[size - 2][size - 1] = False          # the way out (east side, last row)
    half = size // 2
    return [(x - half, y - half) for y in range(size) for x in range(size) if wall[y][x]], half


def hard_family(course):
    out = []
    lad = course.ladders

    def reach_y(y_min, how):
        async def act(c, sc):
            r = await how(c, sc)
            p = await c.bot_at(sc)
            return p[1] >= y_min - 0.1, "высота %.1f из %d (%s)" % (p[1], y_min, r[:140])
        return act

    def climb_up(c, sc):
        return c.tool("climb", {"direction": "up"}, 120)

    def climb_down(c, sc):
        return c.tool("climb", {"direction": "down"}, 120)

    def goto(target, wait=180):
        def how(c, sc):
            return c.tool("goto", dict(zip("xyz", c.w(sc, *target))), wait)
        return how

    async def down_check(c, sc):
        r = await c.tool("climb", {"direction": "down"}, 120)
        p = await c.bot_at(sc)
        return -0.5 < p[1] <= 0.5, "высота %.1f (%s)" % (p[1], r[:140])

    # H1: a ladder in a narrow shaft with a doorway at the bottom, out onto a roof at the top (8 high)
    H = 8
    for lid in lad:
        for f in FACINGS:
            ring = [(a, y, b, WALL) for a in (-1, 0, 1) for b in (-1, 0, 1) for y in range(H)
                    if (a, b) != (0, 0) and not ((a, b) == (1, 0) and y < 2)]
            roof = [(a, H - 1, b, WALL) for a in (-3, -2) for b in (-1, 0, 1)]
            blocks = ring + roof + ladder_column(lid, H)
            tag = "%s_%s" % (short_name(lid), f)
            key = (0, 2, 0, lid)
            out.append(Sc("hard", "shaft_up_%s" % tag, "шахта 1x1 с %s: войти в проём внизу и вылезти на крышу (%s)" % (lid, f),
                          reach_y(H, climb_up), f, blocks, bot=(4, 0, 0), key=key, timeout=180))
            out.append(Sc("hard", "shaft_down_%s" % tag, "с крыши вниз по шахте с %s (%s)" % (lid, f),
                          down_check, f, blocks, bot=(-2, H, 0), key=key, timeout=180))
            out.append(Sc("hard", "shaft_goto_%s" % tag, "«иди на крышу»: сам находит шахту с %s и лезет (%s)" % (lid, f),
                          reach_y(H, goto((-2, H, 0))), f, blocks, bot=(4, 0, -3), key=key, timeout=240))
    # H2: two floors — a ladder onto a low tower, then another kind of ladder onto the high one behind it
    for i, l1 in enumerate(lad):
        l2 = lad[(i + 1) % len(lad)]
        for f in FACINGS:
            blocks = [(a, y, b, WALL) for a in (-3, -2, -1) for b in (-1, 0, 1) for y in range(5)]
            blocks += [(a, y, b, WALL) for a in (-6, -5, -4) for b in (-1, 0, 1) for y in range(10)]
            blocks += ladder_column(l1, 5) + ladder_column(l2, 5, a=-3, y0=5)
            out.append(Sc("hard", "twofloor_%s_%s_%s" % (short_name(l1), short_name(l2), f),
                          "«иди на верхнюю башню»: %s, потом %s (%s)" % (l1, l2, f),
                          reach_y(10, goto((-5, 10, 0))), f, blocks, bot=(4, 0, 1), key=(-3, 7, 0, l2), timeout=300))
    # H3: a shaft through a floor closed by a hatch: open it from the ladder (up) or from above (down)
    for lid in lad[:2] + [l for l in lad if l.endswith("_none")][:1]:
        for f in FACINGS:
            for td in ("oak_trapdoor", "spruce_trapdoor"):
                h = 6
                blocks = [(-1, y, 0, WALL) for y in range(h)] + ladder_column(lid, h)
                blocks += [(a, h, b, "minecraft:oak_planks") for a in range(-3, 2) for b in range(-2, 3) if (a, b) != (0, 0)]
                blocks += [(0, h, 0, "minecraft:%s[facing={F},half=top,open=false]" % td)]
                tag = "%s_%s_%s" % (short_name(lid), f, td.split("_")[0])
                out.append(Sc("hard", "hatch_up_%s" % tag, "вверх по %s, над головой закрытый люк %s — открыть и вылезти (%s)" % (lid, td, f),
                              reach_y(h + 1, climb_up), f, blocks, bot=(3, 0, 0), key=(0, h, 0, "minecraft:" + td), timeout=180))
                out.append(Sc("hard", "hatch_down_%s" % tag, "с пола вниз через закрытый люк %s по %s (%s)" % (td, lid, f),
                              down_check, f, blocks, bot=(-2, h + 1, 0), key=(0, h, 0, "minecraft:" + td), timeout=180))
    for f in FACINGS:
        h = 6
        blocks = [(-1, y, 0, WALL) for y in range(h)] + ladder_column("minecraft:ladder", h)
        blocks += [(a, h, b, "minecraft:oak_planks") for a in range(-3, 2) for b in range(-2, 3) if (a, b) != (0, 0)]
        blocks += [(0, h, 0, "minecraft:iron_trapdoor[facing={F},half=top,open=false]")]

        async def iron_hatch(c, sc):
            r = await c.tool("climb", {"direction": "up"}, 120)
            p = await c.bot_at(sc)
            ok = p[1] < 6.5 and ("люк" in r or "закрыт" in r)
            return ok, "упёрся и честно сказал: %s" % r[:140] if ok else "высота %.1f, ответ «%s»" % (p[1], r[:140])
        out.append(Sc("hard", "hatch_iron_%s" % f, "железный люк над лестницей не открыть рукой — честно сказать (%s)" % f,
                      iron_hatch, f, blocks, bot=(3, 0, 0), key=(0, h, 0, "minecraft:iron_trapdoor"), timeout=180))
    # covered IE ladder closed from the ground up: no way in from the side — he must say so, not stand forever
    for lid in [l for l in lad if covered(l)]:
        for f in FACINGS:
            blocks = [(a, y, b, WALL) for a in (-3, -2, -1) for b in (-1, 0, 1) for y in range(5)]
            blocks += [(0, y, 0, lid + "[facing={F}]") for y in range(5)]

            async def cage(c, sc):
                r = await c.tool("climb", {"direction": "up"}, 90)
                p = await c.bot_at(sc)
                ok = p[1] < 0.5 and "каркас" in r
                return ok, "честно: %s" % r[:140] if ok else "высота %.1f, ответ «%s»" % (p[1], r[:140])
            out.append(Sc("hard", "cage_%s_%s" % (short_name(lid), f), "покрытая лестница %s закрыта снизу — объяснить, что не войти (%s)" % (lid, f),
                          cage, f, blocks, bot=(3, 0, 0), key=(0, 0, 0, lid), timeout=120))

    def inside(target_a=-4, max_a=-2.55):
        async def act(c, sc):
            r = await c.tool("goto", dict(zip("xyz", c.w(sc, target_a, 0, 0))), 120)
            p = await c.bot_at(sc)
            ok = p[0] < max_a and abs(p[2]) < 1.9 and -0.5 < p[1] < 1.5
            return ok, "внутри" if ok else "стоит в %s (%s)" % ([round(v, 1) for v in p], r[:140])
        return act

    def room(a0=-6, a1=-2, door=None):
        # with a floor, as real bases have: no digging in under the wall
        blocks = [(a, y, b, "minecraft:obsidian") for a in range(a0, a1 + 1) for b in range(-2, 3) for y in range(-1, 4)
                  if a in (a0, a1) or b in (-2, 2) or y in (-1, 3)]
        return [bl for bl in blocks if not (bl[0] == a1 and bl[2] == 0 and 0 <= bl[1] < 2)]

    # H4: an iron door opened only by its button/lever outside
    for act_kind in ("lever", "stone_button", "oak_button"):
        for f in FACINGS:
            blocks = room() + [(-2, 0, 0, "minecraft:iron_door[facing={F},half=lower,open=false]"),
                               (-2, 1, 0, "minecraft:iron_door[facing={F},half=upper,open=false]"),
                               (-1, 1, 1, "minecraft:%s[face=wall,facing={F},powered=false]" % act_kind)]
            out.append(Sc("hard", "irondoor_%s_%s" % (act_kind, f), "в комнату за железной дверью — открыть её %s рядом (%s)" % (act_kind, f),
                          inside(), f, blocks, bot=(4, 0, 2), key=(-2, 0, 0, "minecraft:iron_door"), timeout=180))
    for f in FACINGS:
        blocks = room() + [(-2, 0, 0, "minecraft:iron_door[facing={F},half=lower,open=false]"),
                           (-2, 1, 0, "minecraft:iron_door[facing={F},half=upper,open=false]")]

        async def no_button(c, sc):
            r = await c.tool("goto", dict(zip("xyz", c.w(sc, -4, 0, 0))), 120)
            ok = "железной двер" in r and not r.startswith("ГОТОВО")
            return ok, "честно: %s" % r[:160] if ok else "ответ «%s»" % r[:160]
        out.append(Sc("hard", "irondoor_nobutton_%s" % f, "железная дверь без кнопки — честно объяснить, почему не войти (%s)" % f,
                      no_button, f, blocks, bot=(4, 0, 2), key=(-2, 0, 0, "minecraft:iron_door"), timeout=180))
    # H5: an airlock — two doors one after another
    for kind in ("oak_door", "spruce_door"):
        for f in FACINGS:
            blocks = room(-9, -2) + [(-5, y, b, "minecraft:obsidian") for b in (-1, 1) for y in range(3)] + [(-5, 2, 0, "minecraft:obsidian")]
            for a in (-2, -5):
                blocks += [(a, 0, 0, "minecraft:%s[facing={F},half=lower,open=false]" % kind),
                           (a, 1, 0, "minecraft:%s[facing={F},half=upper,open=false]" % kind)]
            out.append(Sc("hard", "airlock_%s_%s" % (kind.split("_")[0], f), "через шлюз из двух дверей %s (%s)" % (kind, f),
                          inside(-7, -5.55), f, blocks, bot=(4, 0, 1), key=(-5, 0, 0, "minecraft:" + kind), timeout=180))
    # H6: mazes — from the first room to the last one, closed all around: no way but through it
    for seed in range(1, 31):
        walls, half = maze(seed)
        walls += [(-half, -half + 1), (half, half - 1)]   # close the way in and out
        blocks = [(x, y, z, "minecraft:obsidian") for x, z in walls for y in range(3)]
        f = FACINGS[seed % 4]

        async def through(c, sc, half=half):
            target = (half - 1, 0, half - 1)
            r = await c.tool("goto", dict(zip("xyz", c.w(sc, *target))), 240)
            p = await c.bot_at(sc)
            ok = math.hypot(p[0] - target[0], p[2] - target[2]) <= 2.5
            return ok, "прошёл" if ok else "стоит в %s (%s)" % ([round(v, 1) for v in p], r[:140])
        out.append(Sc("hard", "maze_%02d" % seed, "пройти лабиринт 7x7 комнат №%d из угла в угол" % seed, through, f, blocks,
                      bot=(-half + 1, 0, -half + 1), timeout=300))
    # H7: a chest on top of a tower reachable by a ladder only: take bread and bring it down to the commander
    for lid in lad:
        for f in FACINGS[:2]:
            h = 6
            blocks = [(a, y, b, WALL) for a in (-3, -2, -1) for b in (-1, 0, 1) for y in range(h)] + ladder_column(lid, h)

            async def roof_chest(c, sc):
                pos = c.w(sc, -3, 6, 1)
                r = await c.use(pos, 180)
                if not r.startswith("ГОТОВО") or "Хлеб" not in r and "bread" not in r:
                    return False, "не открыл сундук на крыше: " + r[:160]
                await c.hub.bot_call("container_take", {"item": "minecraft:bread", "count": 5})
                await c.hub.bot_call("close_container", {})
                g = await c.tool("give", {"item": "minecraft:bread", "count": 5}, 180)
                got = await c.items("host", "minecraft:bread", 5, 8)
                return got >= 5, "залез, взял, спустился, отдал" if got >= 5 else "командир получил %d (%s)" % (got, g[:140])
            out.append(Sc("hard", "roofchest_%s_%s" % (short_name(lid), f), "сундук на крыше башни с %s: взять хлеб и принести вниз (%s)" % (lid, f),
                          roof_chest, f, blocks, containers=[{"at": (-3, 6, 1), "items": [["minecraft:bread", 5]]}],
                          bot=(4, 0, -2), host=(5, 0, 3), key=(0, 2, 0, lid), timeout=360))
    # H8: «иди сюда», and the commander is on a tower top; H9: «иди за мной», and he climbs onto it
    for lid in lad:
        for f in FACINGS:
            h = 6
            blocks = [(a, y, b, WALL) for a in (-3, -2, -1) for b in (-1, 0, 1) for y in range(h)] + ladder_column(lid, h)

            async def come_up(c, sc):
                r = await c.tool("come", {}, 180)
                d = await c.dist_to_host()
                return d <= 3.5, "поднялся ко мне" if d <= 3.5 else "остался в %.1f бл. (%s)" % (d, r[:140])
            out.append(Sc("hard", "come_tower_%s_%s" % (short_name(lid), f), "«иди сюда», а командир на башне с %s (%s)" % (lid, f),
                          come_up, f, blocks, bot=(4, 0, -3), host=(-2, h, 0), key=(0, 2, 0, lid), timeout=240))
        for f in FACINGS[:2]:
            async def follow_up(c, sc):
                await c.tool("follow", {}, 0)
                await asyncio.sleep(2)
                await c.world(tp_host=c.ws(sc, -2, 6, 0))
                t, d = time.time(), 99.0
                while time.time() - t < 90 and d > 4.5:
                    await asyncio.sleep(1)
                    d = await c.dist_to_host()
                await c.hub.run_tool("stop", {}, 5)
                return d <= 4.5, "поднялся следом за %.0f с" % (time.time() - t) if d <= 4.5 else "остался внизу (%.1f бл.)" % d
            out.append(Sc("hard", "follow_tower_%s_%s" % (short_name(lid), f), "«иди за мной», и командир поднимается на башню с %s (%s)" % (lid, f),
                          follow_up, f, [(a, y, b, WALL) for a in (-3, -2, -1) for b in (-1, 0, 1) for y in range(6)] + ladder_column(lid, 6),
                          bot=(4, 0, -2), host=(3, 0, 0), key=(0, 2, 0, lid), timeout=180))
    # H10: dangers on the way: a lava pool, a river to swim, a deep pit
    for f in FACINGS:
        async def safe(c, sc, target=(6, 0, 0)):
            r = await c.tool("goto", dict(zip("xyz", c.w(sc, *target))), 180)
            w = await c.world()
            p = c.local(sc, w["bot"]["pos"])
            ok = math.hypot(p[0] - target[0], p[2] - target[2]) <= 2.5 and w["bot"].get("hp", 20) >= 19.5
            return ok, "дошёл целым" if ok else "стоит в %s, здоровье %.0f (%s)" % ([round(v, 1) for v in p], w["bot"].get("hp", 0), r[:120])
        lava = [(a, -1, b, "minecraft:lava") for a in (-1, 0, 1) for b in range(-5, 6)]
        out.append(Sc("hard", "lava_%s" % f, "обойти лавовое озеро на пути (%s)" % f, safe, f, lava, bot=(-6, 0, 0), timeout=200))
        river = [(a, y, b, "minecraft:water") for a in range(-2, 3) for b in range(-16, 17) for y in (-1, -2, -3)]
        out.append(Sc("hard", "river_%s" % f, "переплыть реку шириной 5 (%s)" % f, safe, f, river, bot=(-6, 0, 0), timeout=200))
        pit = [(a, y, b, "minecraft:air") for a in range(-2, 3) for b in range(-2, 3) for y in (-1, -2, -3, -4, -5)]
        out.append(Sc("hard", "pit_%s" % f, "обойти яму глубиной 5 (%s)" % f, safe, f, pit, bot=(-6, 0, 0), timeout=200))
    # H11: a chest that cannot open (a block on its lid) — he must say it did not open
    for kind in ("chest", "trapped_chest"):
        for f in FACINGS[:2]:
            async def lid_blocked(c, sc):
                r = await c.use(c.w(sc, 3, 0, 0))
                ok = "Открыт" not in r and ("НЕ изменился" in r or not r.startswith("ГОТОВО"))
                return ok, "честно: не открылся" if ok else "ответ «%s»" % r[:160]
            out.append(Sc("hard", "lidblocked_%s_%s" % (kind, f), "%s придавлен блоком сверху — не откроется, честно сказать (%s)" % (kind, f),
                          lid_blocked, f, [(3, 1, 0, "minecraft:stone")],
                          containers=[{"at": (3, 0, 0), "block": "minecraft:%s[facing={B}]" % kind, "items": [["minecraft:bread", 3]]}],
                          bot=(0, 0, 0), key=(3, 0, 0, "minecraft:" + kind)))
    # H13: «сложи вещи в сундук»: the junk goes in, the tools and food stay with him
    for cont in ("minecraft:chest[facing={B}]", "minecraft:barrel[facing=up]"):
        for f in FACINGS[:2]:
            for what in ("all", "minecraft:iron_ingot"):
                async def stash(c, sc, what=what):
                    r = await c.hub.stash({"item": what})
                    c.log("  stash -> %s" % r[:200])
                    pos = c.w(sc, 5, 0, 2)
                    w = await c.world(inspect=[pos])
                    box = w["inventories"]["%d,%d,%d" % tuple(pos)]
                    bot = w["bot"]["items"]
                    if what == "all":
                        ok = box.get("minecraft:cobblestone", 0) == 20 and box.get("minecraft:iron_ingot", 0) == 5 \
                            and bot.get("minecraft:iron_pickaxe", 0) == 1 and bot.get("minecraft:bread", 0) == 3
                    else:
                        ok = box.get("minecraft:iron_ingot", 0) == 5 and bot.get("minecraft:cobblestone", 0) == 20
                    return ok, "сложил как надо" if ok else "в сундуке %s, у него %s (%s)" % (box, bot, r[:100])
                out.append(Sc("hard", "stash_%s_%s_%s" % (cont.split(":")[1].split("[")[0], f, what.split(":")[-1]),
                              "«сложи %s в %s»: инструмент и еду оставить себе (%s)" % (what, cont.split("[")[0], f), stash, f,
                              containers=[{"at": (5, 0, 2), "block": cont, "items": []}],
                              give_bot=[["minecraft:cobblestone", 20], ["minecraft:iron_ingot", 5], ["minecraft:iron_pickaxe", 1],
                                        ["minecraft:bread", 3]], bot=(0, 0, 0), key=(5, 0, 2, cont.split("[")[0]), timeout=180))
    # H14: a real base — a closed room, inside a ladder up to a gallery with a chest; bring bread to the commander outside
    for lid in lad:
        for f in FACINGS[:2]:
            blocks = [(a, y, b, "minecraft:obsidian") for a in range(-12, -1) for b in range(-5, 6) for y in range(-1, 10)
                      if a in (-12, -2) or b in (-5, 5) or y in (-1, 9)]
            blocks = [bl for bl in blocks if not (bl[0] == -2 and bl[2] == 0 and 0 <= bl[1] < 2)]
            blocks += [(-2, 0, 0, "minecraft:oak_door[facing={F},half=lower,open=false]"),
                       (-2, 1, 0, "minecraft:oak_door[facing={F},half=upper,open=false]")]
            blocks += [(a, y, b, WALL) for a in (-11, -10, -9) for b in (-4, -3, -2) for y in range(5)]
            blocks += [(a, y, b, st) for a, y, b, st in ladder_column(lid, 5, a=-8, b=-3)]

            async def base(c, sc):
                pos = c.w(sc, -10, 5, -4)
                r = await c.use(pos, 240)
                if not r.startswith("ГОТОВО") or ("Хлеб" not in r and "bread" not in r):
                    return False, "не добрался до сундука: " + r[:160]
                await c.hub.bot_call("container_take", {"item": "minecraft:bread", "count": 5})
                await c.hub.bot_call("close_container", {})
                g = await c.tool("give", {"item": "minecraft:bread", "count": 5}, 240)
                got = await c.items("host", "minecraft:bread", 5, 8)
                return got >= 5, "дверь, лестница, сундук, назад — принёс" if got >= 5 else "командир получил %d (%s)" % (got, g[:140])
            out.append(Sc("hard", "base_%s_%s" % (short_name(lid), f),
                          "база: закрытая дверь, внутри %s на галерею, сундук — принести хлеб наружу (%s)" % (lid, f), base, f, blocks,
                          containers=[{"at": (-10, 5, -4), "items": [["minecraft:bread", 5]]}],
                          bot=(5, 0, -3), host=(5, 0, 3), key=(-8, 2, -3, lid), timeout=480))
    return out


# ============================================================================ the pack's mods: items, crafting, guns, machines
MOD_FAMILIES = ("names", "craft", "smelt", "tacz", "shoot", "gui", "food", "vehicle", "construct", "use", "work")
RAW_NAME = re.compile(r"^[a-z0-9_.:\-]+$")


async def prepare_mods(c, log):
    """What there is to try: asked from the server (guns, machines, foods, vehicles) and from Altron himself
    (the name of every item as he knows it, the recipes he sees)."""
    from knowledge import Knowledge
    k = Knowledge.__new__(Knowledge)
    k.disabled, k.disabled_re = set(), []
    k.load_disabled(c.hub.cfg, log)
    usable = lambda i: not k.is_disabled(i)   # noqa: E731 — Item Obliterator: not in the game at all
    cache = BRAIN_DIR / "logs" / "mods_catalog.json"
    if cache.exists() and not getattr(c, "recatalog", False):
        # the pack does not change between runs: the catalog is read once (it takes minutes over 7800 items)
        c.mods = json.loads(cache.read_text(encoding="utf-8"))
        log("Каталог модов взят из %s" % cache.name)
        return
    cat = (await c.world(catalog=True)).get("catalog", {})
    # what the pack's Item Obliterator removed cannot exist in the game at all: nothing to try (a disabled
    # SuperbWarfare gun vanished from his hands and he fought the targets bare-handed)
    for key in ("sbw_guns", "foods", "gui_blocks", "usable_items", "vehicles"):
        dropped = [i for i in cat.get(key, []) if not usable(i)]
        cat[key] = [i for i in cat.get(key, []) if usable(i)]
        if dropped:
            log("Отключено в сборке (%s): %d — %s" % (key, len(dropped), ", ".join(dropped[:12])))
    cat["tacz_guns"] = [g for g in cat.get("tacz_guns", []) if usable(g[0]) and usable(g[1])]
    names = (await c.hub.bot_call("item_names", {})).get("names", {})
    tacz = (await c.hub.bot_call("tacz_recipes", {})).get("recipes", [])
    m = {"names": {i: n for i, n in names.items() if usable(i)}, "cat": cat,
         "tacz": [r for r in tacz if usable(r["output"]) and all(usable(x) for x in r["inputs"])]}
    by_ns = {}
    for i in m["names"]:
        if not i.startswith("minecraft:"):
            by_ns.setdefault(i.split(":")[0], []).append(i)
    craft, smelt = [], []
    for ns, ids in sorted(by_ns.items()):
        nc = ns_ = 0
        for i in ids:
            rd = await c.hub.bot_call("recipe_data", {"item": i})
            recs = [r for r in rd.get("crafting", []) if all(usable(x) for x in r["ingredients"])]
            if recs:
                craft.append((i, recs[0]))
                nc += 1
            src = [s for s in rd.get("smelting", []) if usable(s)]
            if src:
                smelt.append((i, src[0]))
                ns_ += 1
    m["craft"], m["smelt"] = craft, smelt
    m["multiblocks"] = (await c.hub.bot_call("multiblocks_data", {})).get("multiblocks", [])
    c.mods = m
    cache.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    log("Моды: предметов %d, крафт %d, плавка %d, рецептов оружейного стола %d, стволов TaCZ %d, SuperbWarfare %d, "
        "машин с окном %d, еды %d, техники %d" % (len(m["names"]), len(craft), len(smelt), len(m["tacz"]),
                                                 len(cat.get("tacz_guns", [])), len(cat.get("sbw_guns", [])),
                                                 len(cat.get("gui_blocks", [])), len(cat.get("foods", [])),
                                                 len(cat.get("vehicles", []))))


def reps(items, key, n):
    """A few of each kind, not all of them: once one rifle is crafted, the next rifle proves nothing new."""
    seen, out = {}, []
    for it in items:
        k = key(it)
        if seen.get(k, 0) < n:
            seen[k] = seen.get(k, 0) + 1
            out.append(it)
    return out


def sample_mods(m):
    """The commander's rule: one of each type is enough (one pistol, one rifle, one scope...), then move on."""
    m = dict(m)
    cat = dict(m["cat"])
    ns = lambda i: i.split(":")[0]   # noqa: E731
    gun_type = {g[0]: g[3] for g in cat.get("tacz_guns", [])}

    def tacz_kind(r):
        path = r["id"].split(":")[-1]
        group, _, rest = path.partition("/")
        if group == "gun":
            return "gun:" + gun_type.get("tacz:" + rest, rest)
        if group == "attachments":
            return "att:" + rest.split("_")[0]
        return group
    m["tacz"] = reps(m.get("tacz", []), tacz_kind, 1)
    m["craft"] = reps(m.get("craft", []), lambda x: ns(x[0]), 3)
    m["smelt"] = reps(m.get("smelt", []), lambda x: ns(x[0]), 2)
    cat["foods"] = reps(cat.get("foods", []), ns, 2)
    cat["gui_blocks"] = reps(cat.get("gui_blocks", []), ns, 8)
    cat["usable_items"] = reps(cat.get("usable_items", []), ns, 8)
    cat["tacz_guns"] = reps(cat.get("tacz_guns", []), lambda g: g[3], 1)
    kinds = re.compile(r"(heli|uh.?60|mh.?60|mi.?\d|ah.?6|f.?\d|su.?\d|mig|b.?2|j.?20|plane|jet)|(boat|ship|zumwalt)|"
                       r"(m.?777|type.?63|mk.?42|hpj|mle|bl.?132|mortar|tow|ags|kornet)")

    def vkind(v):
        k = kinds.search(v.split(":")[-1])
        return ns(v) + ":" + ("air" if k and k.group(1) else "boat" if k and k.group(2) else "gun" if k and k.group(3) else "ground")
    cat["vehicles"] = reps(cat.get("vehicles", []), vkind, 2)
    m["cat"] = cat
    return m


def mods_family(c):
    m = getattr(c, "mods", None)
    if not m:
        return []
    if not getattr(c, "all_items", False):
        m = sample_mods(m)
    out = []
    names = m["names"]
    shared = {}
    for i, n in names.items():
        shared[n.lower()] = shared.get(n.lower(), 0) + 1
    # names: does he know every item by its name in the game (what the commander will call it)?
    by_ns = {}
    for i, n in sorted(names.items()):
        by_ns.setdefault(i.split(":")[0], []).append((i, n))
    for ns, items in by_ns.items():
        async def know(c, sc, items=items, ns=ns):
            good = total = 0
            for i, n in items:
                if not n.strip() or RAW_NAME.match(n) or shared.get(n.lower(), 0) > 6:
                    c.results.append({"family": "names", "name": "name_" + i, "title": "понимает название %s" % i, "ok": None,
                                      "detail": "у предмета нет своего названия в игре (%r)" % n, "sec": 0})
                    continue
                r = await c.hub.bot_call("find_item", {"query": n})
                ok = ("= %s" % i) in r.get("msg", "")
                total += 1
                good += ok
                c.results.append({"family": "names", "name": "name_" + i, "title": "понимает «%s»" % n, "ok": ok,
                                  "detail": "узнал" if ok else "по «%s» нашёл другое: %s" % (n, r.get("msg", "")[:160].replace("\n", " | ")),
                                  "sec": 0})
            return "batch", "%s: понял %d из %d" % (ns, good, total)
        out.append(Sc("names", "names_%s" % ns, "названия предметов мода %s" % ns, know, stage=False, timeout=1800))
    # crafting on a crafting table / in hand
    for i, rec in m["craft"]:
        async def craft(c, sc, i=i):
            r = await c.tool("craft", {"item": i, "count": 1}, 90)
            got = await c.items("bot", i, 1, 3)
            return got >= 1, "сделал" if got >= 1 else "не сделал: " + r[:160]
        out.append(Sc("craft", "craft_%s" % i.replace(":", "_"), "скрафтить %s (%s)" % (names.get(i, i), i), craft, "north",
                      [(2, 0, 1, "minecraft:crafting_table")], give_bot=[[x, n] for x, n in rec["ingredients"].items()],
                      bot=(0, 0, 0), timeout=150))
    # smelting in a furnace
    for i, src in m["smelt"]:
        async def smelt(c, sc, i=i, src=src):
            # the tool takes what to smelt (the ore), the result is checked in his inventory
            r = await c.tool("smelt", {"item": src, "count": 1}, 120)
            got = await c.items("bot", i, 1, 5)
            return got >= 1, "переплавил" if got >= 1 else "не вышло: " + r[:160]
        out.append(Sc("smelt", "smelt_%s" % i.replace(":", "_"), "переплавить в %s (%s)" % (names.get(i, i), i), smelt, "north",
                      [(2, 0, 1, "minecraft:furnace[facing={B}]")], give_bot=[[src, 1], ["minecraft:coal", 3]],
                      bot=(0, 0, 0), timeout=200))
    # the TaCZ gunsmith table: every gun, ammo and attachment recipe
    for r in m["tacz"]:
        path = r["id"].split(":")[-1]

        async def gunsmith(c, sc, r=r, path=path):
            # he sets up the workshop himself: every TaCZ bench (gun table, ammo bench, attachment bench), as items
            # that know their type — each bench makes only its own recipes
            inv = (await c.hub.bot_call("inventory_ids", {})).get("items", {})
            k = 0
            for t_id, n in inv.items():
                if t_id.startswith("tacz:") and ("gun_smith_table" in t_id or "workbench" in t_id):
                    for _ in range(n):
                        spot = c.w(sc, 3, 0, -3 + 3 * k)
                        k += 1
                        await c.tool("place_block", {"item": t_id, "x": spot[0], "y": spot[1], "z": spot[2]}, 30)
            await c.hub.bot_call("look_around", {})
            t = await c.tool("craft", {"item": path, "count": 1}, 150)
            got = await c.items("bot", r["output"], 1, 3)
            return got >= 1, "поставил верстаки и сделал %s" % r["name"] if got >= 1 else "не сделал: " + t[:160]
        out.append(Sc("tacz", "tacz_%s" % path, "верстаки TaCZ: сделать %s" % r["name"], gunsmith, "north",
                      give_bot=[[x, n] for x, n in r["inputs"].items()], bot=(0, 0, 0),
                      extra={"give_tacz_tables": True}, timeout=200))
    # shooting: every gun at two targets 9 blocks away
    targets = [(9, 0, -1, "minecraft:husk"), (9, 0, 1, "minecraft:husk")]

    async def fight(c, sc):
        w0 = await c.world()
        a0, i0 = w0["bot"].get("gun_ammo", 0), sum(n for k, n in w0["bot"]["items"].items() if "ammo" in k)
        r = await c.tool("attack", {"target": "minecraft:husk", "radius": 24, "seconds": 60}, 90)
        w1 = await c.world(entities=[c.o[0], c.o[1], c.o[2], 30])
        alive = [e for e in w1.get("entities", []) if e["type"] == "minecraft:husk" and e["alive"]]
        a1, i1 = w1["bot"].get("gun_ammo", 0), sum(n for k, n in w1["bot"]["items"].items() if "ammo" in k)
        shot = bool(a1 < a0 or i1 < i0 or sc.info.get("sbw"))
        kills = int((re.search(r"уничтожено: (\d+)", r) or [0, 0])[1])
        # both targets down (a husk sometimes calls a third one in: the two it was given count)
        ok = (not alive or kills >= 2) and shot and w1["bot"].get("hp", 0) > 0
        return ok, ("обе цели сняты, патронов %d→%d" % (a0, a1)) if ok else "живых целей %d, патроны %d→%d, запас %d→%d, у него было: %s (%s)" % (
            len(alive), a0, a1, i0, i1, ",".join(w0["bot"]["items"])[:120], r[:120])
    for gun, ammo, mag, kind in m["cat"].get("tacz_guns", []):
        sc = Sc("shoot", "shoot_tacz_%s" % gun.split(":")[-1], "стрельба из %s (%s, патроны %s)" % (gun, kind, ammo), fight,
                "north", bot=(0, 0, 0), mobs=targets, extra={"give_tacz": [[gun, ammo, max(mag, 1) * 2]]}, timeout=150)
        out.append(sc)
    box = "superbwarfare:creative_ammo_box" if "superbwarfare:creative_ammo_box" in names else None
    for gun in m["cat"].get("sbw_guns", []):
        if "vehicle" in gun:   # a vehicle's weapon, not a hand gun (fired by hand it breaks the server and gets him kicked)
            continue
        sc = Sc("shoot", "shoot_sbw_%s" % gun.split(":")[-1], "стрельба из %s (SuperbWarfare)" % names.get(gun, gun), fight,
                "north", bot=(0, 0, 0), mobs=targets, give_bot=[[gun, 1]] + ([[box, 1]] if box else []), timeout=150)
        sc.info["sbw"] = True
        out.append(sc)
    # machines: every block with a window opens for him
    for b in m["cat"].get("gui_blocks", []):
        async def machine(c, sc, b=b):
            # like a player: place it from the item (big machines build their whole body then, HBM's for one),
            # then open it; if the item cannot be placed that way, the block is set for him
            pos = c.w(sc, 3, 0, 0)
            placed = "поставил сам"
            r0 = await c.tool("place_block", {"item": b, "x": pos[0], "y": pos[1], "z": pos[2]}, 40)
            await asyncio.sleep(1)
            here = await c.block(pos)
            if here.startswith("minecraft:air"):
                # maybe the machine went up around the spot (its core elsewhere): anything of it near there?
                w = await c.world(blocks=[c.w(sc, a, y, bb) for a in (2, 3, 4) for y in (0, 1) for bb in (-1, 0, 1)])
                parts = [k for k, v in w.get("blocks", {}).items() if not v.startswith("minecraft:air")]
                if parts:
                    pos = [int(v) for v in parts[0].split(",")]
                else:
                    placed = "поставить предметом не вышло (%s) — блок поставлен ему" % r0[:80]
                    await c.world(set=[pos + [b]])
                    await asyncio.sleep(1)
            r = await c.use(pos, 40)
            await c.hub.bot_call("close_container", {})
            ok = "Открыт" in r or "открылось окно" in r
            if not ok and "НЕ изменился" in r:
                return None, "%s; окна нет (нужно условие: книга, топливо, собранная постройка...) — честно сказал: %s" % (placed, r[:100])
            return ok, ("%s, окно открылось" % placed) if ok else "%s; %s" % (placed, r[:160])
        out.append(Sc("gui", "gui_%s" % b.replace(":", "_"), "поставить и открыть %s (%s)" % (names.get(b, b), b), machine,
                      "north", give_bot=[[b, 1]], bot=(0, 0, 0), timeout=120))
    # food: hungry, he eats it
    for f in m["cat"].get("foods", []):
        if f not in names:
            continue

        async def eat(c, sc, f=f):
            r = await c.tool("eat", {}, 40)
            w = await c.world()
            food = w["bot"].get("food", 0)
            return food > 4, "поел (сытость %d)" % food if food > 4 else "не поел: " + r[:160]
        out.append(Sc("food", "food_%s" % f.replace(":", "_"), "съесть %s" % names.get(f, f), eat, "north",
                      give_bot=[[f, 3]], bot=(0, 0, 0), extra={"hunger": 4}, timeout=60))
    # constructions: every IE / Immersive Petroleum multiblock — build it by its blueprint and form it with the hammer
    for mb in m.get("multiblocks", []):
        name = mb["name"]
        short = re.sub(r"[^a-z0-9_]+", "_", name.split("(")[-1].strip(")").split(":")[-1].lower()).strip("_")

        async def construct(c, sc, name=name):
            # by its exact id: «Экскаватор» by name found «Ковш экскаватора» first
            key = name[name.rfind("(") + 1:].rstrip(")") if "(" in name else name
            r = await c.tool("build_multiblock", {"name": key}, 900)
            ok = r.startswith("ГОТОВО") and "собрал" in r
            return ok, r[:200]
        # + the hammer, and throwaway blocks Baritone pillars up with to reach the top of tall ones
        mats = [[i, n] for i, n in mb["materials"].items()] + [["immersiveengineering:hammer", 1], ["minecraft:cobblestone", 64],
                                                                  ["minecraft:dirt", 64]]
        out.append(Sc("construct", "construct_%s" % short, "построить и собрать %s" % name, construct, "north",
                      give_bot=mats, bot=(0, 0, 0), timeout=1000))
    # does a built machine really WORK: build it, open it, load raw material and fuel, wait, take the product
    work = [("immersiveengineering:multiblocks/coke_oven", [["minecraft:coal", 3]], "immersiveengineering:coal_coke", 320),
            ("immersiveengineering:multiblocks/blast_furnace", [["minecraft:iron_ingot", 1], ["immersiveengineering:coal_coke", 3]],
             "immersiveengineering:ingot_steel", 200),
            ("immersiveengineering:multiblocks/alloy_smelter", [["minecraft:copper_ingot", 1], ["immersiveengineering:ingot_nickel", 1],
                                                                 ["immersiveengineering:coal_coke", 2]],
             "immersiveengineering:ingot_constantan", 120)]
    by_id = {mb["name"][mb["name"].rfind("(") + 1:].rstrip(")"): mb for mb in m.get("multiblocks", [])}
    for mb_id, inputs, product, wait_s in work:
        if mb_id not in by_id:
            continue

        async def run_machine(c, sc, mb_id=mb_id, inputs=inputs, product=product, wait_s=wait_s):
            r = await c.tool("build_multiblock", {"name": mb_id}, 600)
            at = re.search(r"открыть её: (-?\d+) (-?\d+) (-?\d+)", r) or re.search(r"в (-?\d+) (-?\d+) (-?\d+)", r)
            if not r.startswith("ГОТОВО") or not at:
                return False, "не построил: " + r[:140]
            pos = [int(v) for v in at.groups()]
            # its window: any block of the machine opens it (the corner of the blueprint is one)
            op = await c.use(pos, 60)
            if "Открыт" not in op:
                return False, "постройка собрана, но окно не открылось: " + op[:120]
            loaded = []
            for item, n in inputs:
                p = (await c.hub.bot_call("container_put", {"item": item, "count": n})).get("msg", "")
                loaded.append(p)
            await c.hub.bot_call("close_container", {})
            t = time.time()
            while time.time() - t < wait_s:
                await asyncio.sleep(15)
                op = await c.use(pos, 60)
                took = (await c.hub.bot_call("container_take", {"item": product, "count": 64})).get("msg", "")
                await c.hub.bot_call("close_container", {})
                if await c.items("bot", product) >= 1:
                    return True, "работает: загрузил %s — забрал %s через %.0f с" % ("; ".join(loaded)[:80], product, time.time() - t)
            return False, "за %d с продукта нет (загрузил: %s)" % (wait_s, "; ".join(loaded)[:120])
        mats = [[i, n] for i, n in by_id[mb_id]["materials"].items()] + [["immersiveengineering:hammer", 1],
                                                                          ["minecraft:cobblestone", 64]] + inputs
        out.append(Sc("work", "work_%s" % mb_id.rsplit("/", 1)[-1], "построить %s и проверить, что работает" % by_id[mb_id]["name"],
                      run_machine, "north", give_bot=mats, bot=(0, 0, 0), timeout=1200))
    # vehicles: get in, take off (helicopters, planes), drive 20 blocks
    air_words = re.compile(r"a10|ah6|mi28|mi24|mi8|uh60|mh60|ah64|v22|heli|copter|plane|jet|aircraft|wing|drone|tom6|^f\d{1,3}|^j\d{2}|"
                           r"mig|su\d|ka52|apache|cobra|blackhawk|chinook|c130|b2|b52|tu\d|yak|a4|p51|spitfire|bf109|zero|bird|littlebird",
                           re.I)

    class AirRe:   # ids like "uh_60", "f_16", "mig_15": compare without the underscores
        @staticmethod
        def search(v):
            return air_words.search(v.split(":")[-1].replace("_", "").replace("-", ""))
    air_re = AirRe
    # stationary guns (one sits at them and fires) and things that float
    gun_re = re.compile(r"m_?777|type_?63|mk_?42|hpj_?11|mle_?1934|bl_?132|mortar|tow|artillery|howitzer|cannon|turret|ciws|emplacement")
    boat_re = re.compile(r"boat|ship|zumwalt|destroyer|submarine|yacht|raft|speedboat")
    for v in m["cat"].get("vehicles", []):
        async def drive(c, sc, v=v):
            # vehicles of SuperbWarfare / Ash Vehicle run on energy and are spawned empty: note it, then charge them
            # (charging them himself is a separate mechanic)
            w0 = await c.world(entities=[c.o[0], c.o[1], c.o[2], 30])
            en = [e for e in w0.get("entities", []) if e["type"] == v]
            energy = "энергия %s/%s" % (en[0].get("energy"), en[0].get("max_energy")) if en and "max_energy" in en[0] else "без энергии"
            await c.world(charge=[c.o[0], c.o[1], c.o[2], 30])
            sc.info["energy"] = energy
            r = await c.tool("use_entity", {"target": v}, 40)
            w = await c.world(entities=[c.o[0], c.o[1], c.o[2], 30])
            if w["bot"].get("riding") != v:
                if air_re.search(v):
                    c.results.append({"family": "fly", "name": "fly_" + v.replace(":", "_"), "title": "взлететь на %s" % v,
                                      "ok": False, "detail": "не сел: " + r[:140], "sec": 0})
                return False, "не сел: " + r[:160]
            start = [e["pos"] for e in w["entities"] if e["type"] == v]
            if air_re.search(v) and start:
                # a helicopter or a plane: throttle up (the jump key, as a player does) and see if it leaves the ground
                t = time.time()
                top, how, charged = start[0][1], "", ""
                for keys in (("jump",), ("jump", "forward"), ("forward",), ("sprint", "forward"), ("forward", "jump", "sprint")):
                    for k in keys:
                        await c.hub.bot_call("press_key", {"key": k, "ticks": 120})
                    for _ in range(6):
                        await asyncio.sleep(1)
                        ww = await c.world(entities=[c.o[0], c.o[1], c.o[2], 90])
                        me = [e for e in ww["entities"] if e["type"] == v]
                        top = max([top] + [e["pos"][1] for e in me])
                        if me and not charged:
                            charged = "энергия %s/%s" % (me[0].get("energy"), me[0].get("max_energy"))
                    if top - start[0][1] >= 2:
                        how = "+".join(keys)
                        break
                if top - start[0][1] < 2:
                    # a plane: full throttle down a runway, then the nose up (planes follow the view, like a player's mouse)
                    await c.hub.bot_call("look_at", {"x": start[0][0] + 200, "y": start[0][1] + 1.6, "z": start[0][2]})
                    await c.hub.bot_call("press_key", {"key": "forward", "ticks": 260})
                    await c.hub.bot_call("press_key", {"key": "sprint", "ticks": 260})
                    for i in range(13):
                        await asyncio.sleep(1)
                        if i == 7:   # speed is up: pull the nose up
                            await c.hub.bot_call("look_at", {"x": start[0][0] + 200, "y": start[0][1] + 90, "z": start[0][2]})
                        ww = await c.world(entities=[c.o[0], c.o[1], c.o[2], 250])
                        top = max([top] + [e["pos"][1] for e in ww["entities"] if e["type"] == v])
                    if top - start[0][1] >= 2:
                        how = "разбег (вперёд+бег) и нос вверх"
                rose = top - start[0][1]
                sc.info["energy"] = "%s; после зарядки %s%s" % (sc.info.get("energy"), charged,
                                                                "; взлетел на: " + how if how else "")
                c.results.append({"family": "fly", "name": "fly_" + v.replace(":", "_"), "title": "взлететь на %s" % v,
                                  "ok": rose >= 2, "detail": "поднялся на %.1f бл. (при появлении: %s, перед полётом заряжен)"
                                  % (rose, sc.info.get("energy")), "sec": round(time.time() - t, 1)})
                await c.world(tp_bot=c.ws(sc, 0, 0, 0))   # back down for the drive check
                return True, "сел; взлёт — отдельной проверкой (%.1f бл.)" % rose
            goal = c.w(sc, 25, 0, 0)
            if gun_re.search(v):
                # a gun emplacement: one sits at it and fires, it does not drive anywhere
                return True, "сел за орудие (стационарное, не ездит)" + (" — " + r[:100] if "энерг" in r else "")
            r2 = await c.tool("drive", {"x": goal[0], "z": goal[2]}, 60)
            w2 = await c.world(entities=[c.o[0], c.o[1], c.o[2], 60])
            now = [e["pos"] for e in w2["entities"] if e["type"] == v]
            moved = math.dist(start[0], now[0]) if start and now else 0
            return moved >= 8, "сел и проехал %.0f бл." % moved if moved >= 8 else "сел, но проехал %.0f бл.: %s" % (moved, r2[:140])
        boat = boat_re.search(v)
        # boats and ships on water: a pool across the stage, 3 deep
        water = [(a, y, b, "minecraft:water") for a in range(-6, 27) for b in range(-6, 7) for y in (-1, -2, -3)] if boat else []
        out.append(Sc("vehicle", "vehicle_%s" % v.replace(":", "_"), "сесть в %s и %s" % (v, "поплыть" if boat else "поехать"),
                      drive, "north", water, bot=(0, 0, -8) if boat else (0, 0, 0), mobs=[(4, 0, 0, v)], timeout=150))
    # items with a right-click mechanic: use it (in the air, then on a block) and see what really changed in the world
    def snapshot(w, it, pos):
        b = w["bot"]
        return {"count": b["items"].get(it, 0), "items": sum(b["items"].values()), "hp": round(b.get("hp", 0)),
                "food": b.get("food", 0), "effects": tuple(sorted(b.get("effects", []))), "screen": b.get("screen_open"),
                "cooldown": b.get("cooldowns"), "entities": len(w.get("entities", [])),
                "block": w.get("blocks", {}).get("%d,%d,%d" % tuple(pos), "")}

    def diff(a, b):
        what = {"count": "предмет потратился/изменился", "items": "инвентарь изменился", "hp": "здоровье изменилось",
                "food": "сытость изменилась", "effects": "появился эффект", "screen": "открылось окно",
                "cooldown": "перезарядка предмета", "entities": "в мире что-то появилось", "block": "блок изменился"}
        return [what[k] for k in a if a[k] != b[k]]

    for it in m["cat"].get("usable_items", []):
        if it not in names:
            continue

        async def use(c, sc, it=it):
            pos = c.w(sc, 2, 0, 0)
            probe = {"entities": [c.o[0], c.o[1], c.o[2], 20], "blocks": [pos]}
            s0 = snapshot(await c.world(**probe), it, pos)
            r = await c.tool("use_item", {"item": it, "ticks": 20}, 30)
            await asyncio.sleep(1.5)
            changed = diff(s0, snapshot(await c.world(**probe), it, pos))
            if not changed:   # maybe it works on a block (a wrench, a hammer, a placer)
                await c.hub.bot_call("close_container", {})
                r = await c.tool("use_block", {"x": pos[0], "y": pos[1], "z": pos[2], "item": it}, 30)
                await asyncio.sleep(1.5)
                changed = diff(s0, snapshot(await c.world(**probe), it, pos))
            await c.hub.bot_call("close_container", {})
            said = re.search(r"сервер написал: (.{1,80})", r)
            if said:   # a dosimeter, a scanner, a rangefinder answer with a message
                changed.append("показал: " + said.group(1))
            if r.startswith("ОШИБКА") and not changed:
                return False, r[:160]
            return (True if changed else None), ("сработал: " + ", ".join(changed)) if changed else \
                "видимых изменений нет (нужна особая цель или условие): " + r[:120]
        out.append(Sc("use", "use_%s" % it.replace(":", "_"), "применить %s (%s)" % (names.get(it, it), it), use, "north",
                      [(2, 0, 0, "minecraft:stone")], give_bot=[[it, 3]], bot=(0, 0, 0), timeout=90))
    # charging a vehicle himself: he places a charging station next to it (the creative one here: it needs no power
    # generator); the vehicle's energy must really go up
    station = "superbwarfare:creative_charging_station"
    vs = [x for x in m["cat"].get("vehicles", []) if x.startswith(("superbwarfare:", "ashvehicle:")) and not gun_re.search(x)]
    for v in (vs if station in names else []):
        async def charge(c, sc, v=v):
            w0 = await c.world(entities=[c.o[0], c.o[1], c.o[2], 30])
            e0 = [e.get("energy", -1) for e in w0.get("entities", []) if e["type"] == v]
            if not e0 or e0[0] < 0:
                return None, "у этой техники нет энергии как таковой"
            spot = c.w(sc, 1, 0, 2)
            r = await c.tool("place_block", {"item": station, "x": spot[0], "y": spot[1], "z": spot[2]}, 40)
            e1 = e0
            for _ in range(10):
                await asyncio.sleep(1)
                w1 = await c.world(entities=[c.o[0], c.o[1], c.o[2], 30])
                e1 = [e.get("energy", -1) for e in w1.get("entities", []) if e["type"] == v] or [-1]
                if e1[0] > e0[0]:
                    break
            ok = e1[0] > e0[0]
            return ok, "поставил станцию, заряжается: %d→%d" % (e0[0], e1[0]) if ok else "энергия %s→%s (%s)" % (e0[0], e1[0], r[:120])
        out.append(Sc("vehicle", "charge_%s" % v.replace(":", "_"), "зарядить %s: поставить рядом зарядную станцию" % v, charge,
                      "north", give_bot=[[station, 1]], bot=(0, 0, 0), mobs=[(4, 0, 0, v)], timeout=90))
    # what the commander cares about first: shooting, vehicles, machines, the gunsmith table; then the rest
    order = {"shoot": 0, "vehicle": 1, "gui": 2, "use": 3, "tacz": 4, "construct": 5, "work": 5, "craft": 6, "smelt": 7, "food": 8, "names": 9}
    out.sort(key=lambda s: order.get(s.family, 9))
    for s in out:
        s.extra.setdefault("protect", True)   # no exploding vehicle or grenade lays the players down
        if s.family == "use":
            s.extra.setdefault("hurt", 10)    # a medkit must have something to heal...
            s.extra.setdefault("regen", False)   # ...and natural healing must not pass for it
    return out


def all_scenarios(course, use_llm):
    out = (see_family(course) + toggle_family() + walk_through_family() + container_family() + move_family()
           + ladder_family(course) + hard_family(course) + mods_family(course))
    return out + (ai_family(course) if use_llm else [])


# ---------------------------------------------------------------------------- running
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


async def wait_idle(hub, minutes, quiet_sec=6):
    quiet, t = 0, time.time()
    while time.time() - t < minutes * 60:
        await asyncio.sleep(1)
        quiet = 0 if hub_busy(hub) else quiet + 1
        if quiet >= quiet_sec:
            return True
    return False


async def run_one(c, sc):
    t = time.time()
    try:
        broken = await c.stage(sc) if sc.stage else ""
        if broken:
            ok, detail = None, broken
        else:
            ok, detail = await asyncio.wait_for(sc.act(c, sc), sc.timeout)
    except asyncio.TimeoutError:
        ok, detail = False, "завис дольше %d с" % sc.timeout
    except Exception as e:
        ok, detail = False, "сбой проверки: %r" % e
    if ok == "batch":   # many checks at once: each already went into the results by itself
        return None, detail
    c.results.append({"family": sc.family, "name": sc.name, "title": sc.title, "ok": ok, "detail": detail,
                      "sec": round(time.time() - t, 1)})
    return ok, detail


def report(course, world, total):
    res = course.results
    fams = {}
    for r in res:
        f = fams.setdefault(r["family"], [0, 0, 0])
        f[0 if r["ok"] else 1 if r["ok"] is False else 2] += 1
    passed = sum(1 for r in res if r["ok"])
    failed = [r for r in res if r["ok"] is False]
    lines = ["Полигон Альтрона — %s (мир %s)" % (time.strftime("%d.%m.%Y %H:%M"), world),
             "Сценариев: %d из %d запланированных. Пройдено %d, провалено %d, не построилось %d." % (
                 len(res), total, passed, len(failed), len(res) - passed - len(failed)), "", "По семействам:"]
    for fam, (ok, bad, skip) in fams.items():
        lines.append("  %-9s %4d OK  %4d FAIL%s" % (fam, ok, bad, "  %d пропущено" % skip if skip else ""))
    if failed:
        lines += ["", "Провалы:"]
        lines += ["  [%s] %s\n        %s" % (r["name"], r["title"], r["detail"]) for r in failed]
    lines += ["", "Все сценарии:"]
    lines += ["  [%s] %-44s %5.0f с  %s" % ({True: "OK  ", False: "FAIL", None: "--  "}[r["ok"]], r["name"], r["sec"], r["detail"][:160])
              for r in res]
    text = "\n".join(lines)
    (BRAIN_DIR.parent / "polygon_report.txt").write_text(text + "\n", encoding="utf-8")
    (BRAIN_DIR / "logs" / "polygon_results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return text, len(failed)


def cleanup(cfg, world, log):
    """The test world and everything remembered about it go away."""
    targets = [rel(cfg.get("host_dir", "../host")) / "saves" / world, rel(cfg["memory_dir"])]
    mem = rel(cfg["bot_dir"]) / "altron_memory"
    if mem.exists():
        targets += [d for d in mem.iterdir() if d.is_dir() and world.lower() in d.name.lower()]
    for d in targets:
        for _ in range(10):
            if not d.exists():
                break
            shutil.rmtree(d, ignore_errors=True)
            time.sleep(1)
        log("Удалил %s%s" % (d, " (не до конца: файл занят)" if d.exists() else ""))


async def main(argv):
    use_llm = "--llm" in argv
    keep = "--keep" in argv
    wanted = [a for a in argv if not a.startswith("--")]
    # --mods: the pack's items, crafting, guns, machines, food, vehicles (only them, unless more is named); --all: everything
    use_mods = "--mods" in argv or "--all" in argv or any(w.split("_")[0] in MOD_FAMILIES for w in wanted)
    if "--mods" in argv and not wanted:
        wanted = list(MOD_FAMILIES)
    if "--list" in argv:
        c = Course(None, print)
        c.ladders += ["enderio:dark_steel_ladder", "immersiveengineering:metal_ladder_alu", "immersiveengineering:metal_ladder_none",
                      "immersiveengineering:metal_ladder_steel", "securitycraft:reinforced_ladder"]
        scs = [s for s in all_scenarios(c, use_llm) if not wanted or any(s.name.startswith(w) or s.family == w for w in wanted)]
        fams = {}
        for s in scs:
            fams[s.family] = fams.get(s.family, 0) + 1
        print("Сценариев: %d — %s" % (len(scs), ", ".join("%s %d" % kv for kv in fams.items())))
        return 0
    cfg = json.loads((BRAIN_DIR / "config.json").read_text(encoding="utf-8"))
    world = "AltronTest_Polygon_%s" % time.strftime("%m%d_%H%M")
    cfg.update({"brain_port": TEST_PORT, "chat_replies": False, "bot_render_always": False,
                "memory_dir": "memory_test/" + world})   # tests never write into Altron's real memory
    hub = altron.Hub(cfg)
    hub.loop = asyncio.get_running_loop()
    course = Course(hub, hub.log)
    # everything he says aloud is kept: the question checks read his answers
    course.said = []
    spoken = hub.say

    async def say_and_note(text):
        course.said.append(text)
        await spoken(text)
    hub.say = say_and_note
    llm_proc = None
    server = await altron.listen(hub, cfg["brain_port"])
    loops = [asyncio.create_task(server.serve_forever())]
    if use_llm:
        llm_proc = altron.start_llm(cfg, hub.log)

        def load():
            from knowledge import Knowledge
            hub.knowledge = Knowledge.load(cfg, hub.log)

        await asyncio.to_thread(load)
        loops.append(asyncio.create_task(hub.agent_loop()))
        await altron.wait_llm(cfg, hub.log)

    host_proc = launch_host(cfg, world, COMMANDER, hub.log)
    failed = 1
    try:
        if not await wait_for(lambda: hub.host is not None, 420):
            raise RuntimeError("игра-хозяин не подключилась к мозгу")
        hub.log("Хозяин на связи, жду Альтрона в мире...")
        if not await wait_for(lambda: hub.joined, 600):
            raise RuntimeError("Альтрон не зашёл в мир")
        await asyncio.sleep(5)
        await course.prepare()
        if use_mods:
            course.recatalog = "--recatalog" in argv
            course.all_items = "--all-items" in argv   # otherwise one or two of each kind
            await prepare_mods(course, hub.log)
        scs = [s for s in all_scenarios(course, use_llm) if not wanted or any(s.name.startswith(w) or s.family == w for w in wanted)]
        hub.log("СЦЕНАРИЕВ: %d" % len(scs))
        t0 = time.time()
        for i, sc in enumerate(scs, 1):
            if hub.bot is None:
                hub.log("Клиент Альтрона отключился — жду его обратно...")
                if not await wait_for(lambda: hub.bot is not None, 300):
                    break
            ok, detail = await run_one(course, sc)
            hub.log("[%d/%d] %s %s — %s   (%.0f мин)" % (i, len(scs), {True: "OK  ", False: "FAIL", None: "--  "}[ok],
                                                          sc.name, detail[:200], (time.time() - t0) / 60))
            if i % 25 == 0:
                report(course, world, len(scs))   # a report on disk as it goes
        text, failed = report(course, world, len(scs))
        print("\n" + text.split("\nВсе сценарии:")[0], flush=True)
    finally:
        hub.stop_bot()
        host_proc.terminate()
        if llm_proc is not None:
            llm_proc.terminate()
        for t in loops:
            t.cancel()
        await asyncio.sleep(8)   # the games let go of the world's files
        if not keep:
            cleanup(cfg, world, hub.log)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(main(sys.argv[1:])))
