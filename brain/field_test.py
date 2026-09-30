"""Altron's field test: his hands on the keyboard and the mouse, talks with the AI, events of the world — and everything he did, said
and thought written into one report, test-reports/field_test_<date>.md, for a person or the Claude session working
on Altron to read.

It needs a running session: the brain, the game and Altron's body. Best in a COPY of a world (session.py makes one):
    window 1:  .venv\\Scripts\\python.exe session.py "My World"
    window 2:  .venv\\Scripts\\python.exe field_test.py
Parts (all by default, about 25 minutes):  --only hands,talk,goals,events
The course is a set of glass-walled lanes built high in the air above the commander (y 230): it replaces whatever is
in that box, moves Altron and the commander there and takes it all down at the end (--keep-course leaves it).
"""
import argparse
import datetime
import json
import platform
import re
import socket
import subprocess
import sys
import time
import traceback

from launcher import BRAIN_DIR, rel

ROOT = BRAIN_DIR.parent
REPORT_DIR = ROOT / "test-reports"
Y = 230                      # the course floor stands at Y-1
LANES = ["flat", "steps", "door", "ladder", "water"]
SECRET_KEYS = ("llm_api_key", "game_key")


# ---------------------------------------------------------------------- talking to the running brain
class Brain:
    def __init__(self, port):
        self.port = port

    def call(self, msg, timeout=300):
        with socket.create_connection(("127.0.0.1", self.port), timeout=timeout) as s:
            f = s.makefile("rwb")
            for m in ({"type": "hello", "role": "console"}, msg):
                f.write((json.dumps(m, ensure_ascii=False) + "\n").encode("utf-8"))
            f.flush()
            line = f.readline()
        if not line:
            raise ConnectionError("the brain closed the connection")
        return json.loads(line.decode("utf-8")).get("result")

    def state(self):
        return self.call({"type": "state"}, 30)

    def say(self, text):
        return self.call({"type": "say", "text": text}, 30)

    def task(self, name, args=None, wait=120):
        return self.call({"type": "task", "name": name, "args": args or {}, "wait": wait}, wait + 60)

    def bot(self, name, args=None):
        return self.call({"type": "bot", "name": name, "args": args or {}}, 60)

    def probe(self, **args):
        return self.call({"type": "probe", "args": args}, 60)

    def set(self, key, value):
        return self.call({"type": "set", "key": key, "value": value}, 30)


class BrainLog:
    """New lines of brain/logs/brain.log since the last look."""

    def __init__(self, path):
        self.path = path
        self.pos = path.stat().st_size if path.exists() else 0

    def mark(self):
        self.pos = self.path.stat().st_size if self.path.exists() else 0

    def since_mark(self):
        if not self.path.exists():
            return []
        with open(self.path, "rb") as f:
            f.seek(self.pos)
            data = f.read()
        return data.decode("utf-8", errors="replace").splitlines()


def spoken(lines):
    return [re.sub(r"^.*?(Альтрон|Altron)(?: \((сразу|at once)\))?: ", "", ln) for ln in lines
            if re.search(r"\d\d:\d\d:\d\d (Альтрон|Altron)( \((сразу|at once)\))?: ", ln)]


def tools_called(lines):
    out = []
    for ln in lines:
        m = re.search(r"  -> (\w+) ", ln)
        if m:
            out.append(m.group(1))
    return out


def dist(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


# ---------------------------------------------------------------------- the test run
class FieldTest:
    def __init__(self, brain, cfg, args):
        self.b = brain
        self.cfg = cfg
        self.args = args
        self.log = BrainLog(BRAIN_DIR / "logs" / "brain.log")
        self.results = []      # (part, name, verdict, seconds, note, details)
        self.origin = None     # the course's corner (x, z)
        self.home = None       # where the players stood before the test
        self.started = datetime.datetime.now()

    def say_out(self, text):
        print(time.strftime("%H:%M:%S"), text, flush=True)

    def add(self, part, name, verdict, seconds, note, details=""):
        mark = {"ok": "✅", "warn": "⚠️", "fail": "❌", "skip": "⏭️"}[verdict]
        self.results.append((part, name, mark, seconds, note, details))
        self.say_out("%s %s / %s (%.0f с): %s" % (mark, part, name, seconds, note))

    # ------------------------------------------------------------------ waiting for him
    def wait_idle(self, timeout=150, settle=4.0):
        """Until the AI is done with everything it was given (and has finished speaking)."""
        t0 = time.time()
        quiet = None
        started = False
        while time.time() - t0 < timeout:
            st = self.b.state()
            working = st["busy"] or st["requests"] > 0 or st["speaking"] or \
                st["running"] is not None
            if working:
                started = True
                quiet = None
            elif started or time.time() - t0 > 6:
                quiet = quiet or time.time()
                if time.time() - quiet >= settle:
                    return True
            time.sleep(0.5)
        return False

    def talk(self, text, timeout=150):
        """Say a phrase as the commander; what he said and did, how long the first word took."""
        self.log.mark()
        t0 = time.time()
        self.b.say(text)
        first = None
        while time.time() - t0 < 60 and first is None:
            if spoken(self.log.since_mark()):
                first = time.time() - t0
                break
            st = self.b.state()
            if not (st["busy"] or st["requests"]) and time.time() - t0 > 8:
                break
            time.sleep(0.3)
        self.wait_idle(timeout)
        lines = self.log.since_mark()
        return {"lines": lines, "said": spoken(lines), "tools": tools_called(lines), "first": first,
                "seconds": time.time() - t0}

    def where(self):
        r = self.b.probe()
        return r.get("bot", {}).get("pos"), r.get("host", {}).get("pos"), r

    # ------------------------------------------------------------------ the course
    def lane_z(self, i):
        return self.origin[1] + 2 + 4 * i

    def build_course(self):
        _, host, _ = self.where()
        if not host:
            raise RuntimeError("the commander's player is not in the world")
        self.home = host
        ox, oz = int(host[0]) - 16, int(host[2]) - 10
        self.origin = (ox, oz)
        x1, x2, z1, z2 = ox, ox + 32, oz, oz + 20
        fill = [[x1, Y - 3, z1, x2, Y + 9, z2, "minecraft:air"],
                [x1, Y - 1, z1, x2, Y - 1, z2, "minecraft:smooth_stone"],
                [x1, Y, z1, x2, Y + 8, z1, "minecraft:glass"], [x1, Y, z2, x2, Y + 8, z2, "minecraft:glass"],
                [x1, Y, z1, x1, Y + 8, z2, "minecraft:glass"], [x2, Y, z1, x2, Y + 8, z2, "minecraft:glass"]]
        for i in range(1, len(LANES)):
            fill.append([x1, Y, oz + 4 * i, x2, Y + 8, oz + 4 * i, "minecraft:glass"])   # walls between the lanes
        fill += [[x1, Y + 9, z1, x2, Y + 9, z2, "minecraft:glass"]]   # a glass roof: nothing gets over the walls
        # 1 "steps": a block to jump onto, a raised part two high, then a drop of two
        c = self.lane_z(1)
        fill += [[ox + 8, Y, c - 1, ox + 8, Y, c + 1, "minecraft:stone"],
                 [ox + 12, Y, c - 1, ox + 12, Y, c + 1, "minecraft:stone"],
                 [ox + 13, Y, c - 1, ox + 16, Y + 1, c + 1, "minecraft:stone"],
                 [ox + 22, Y, c - 1, ox + 22, Y, c + 1, "minecraft:oak_slab[type=bottom]"]]
        sets = []
        # 2 "door": a wall with a wooden door, then a fence with a gate
        c = self.lane_z(2)
        fill += [[ox + 12, Y, c - 1, ox + 12, Y + 8, c + 1, "minecraft:glass"],
                 [ox + 22, Y, c - 1, ox + 22, Y, c + 1, "minecraft:oak_fence"]]
        sets += [[ox + 12, Y, c, "minecraft:oak_door[facing=east,half=lower,hinge=left,open=false]"],
                 [ox + 12, Y + 1, c, "minecraft:oak_door[facing=east,half=upper,hinge=left,open=false]"],
                 [ox + 22, Y, c, "minecraft:oak_fence_gate[facing=east,open=false]"]]
        # 3 "ladder": a stone wall five high across the lane, a ladder up one side and down the other
        c = self.lane_z(3)
        fill += [[ox + 15, Y, c - 1, ox + 15, Y + 4, c + 1, "minecraft:stone"]]
        for dy in range(5):
            sets += [[ox + 14, Y + dy, c, "minecraft:ladder[facing=west]"],
                     [ox + 16, Y + dy, c, "minecraft:ladder[facing=east]"]]
        # 4 "water": a pool two deep across the lane, in a stone box (water must not run out under the course)
        c = self.lane_z(4)
        fill += [[ox + 11, Y - 3, c - 2, ox + 17, Y - 1, c + 2, "minecraft:smooth_stone"],
                 [ox + 12, Y - 2, c - 1, ox + 16, Y - 1, c + 1, "minecraft:water"]]
        r = self.b.probe(fill=fill, set=sets, heal=True, protect=True,
                         commands=["time set noon", "weather clear", "gamerule doMobSpawning false",
                                   "gamerule doDaylightCycle false"])
        if r.get("error"):
            raise RuntimeError("the course was not built: %s" % r["error"])
        return r

    def take_down(self):
        if self.origin is None:
            return
        ox, oz = self.origin
        cmds = ["gamerule doMobSpawning true", "gamerule doDaylightCycle true"]
        args = {"commands": cmds, "clear_entities": [ox + 16, Y, oz + 10, 24]}
        if not self.args.keep_course:
            args["fill"] = [[ox, Y - 3, oz, ox + 32, Y + 9, oz + 20, "minecraft:air"]]
        if self.home:
            args["tp_host"] = self.home
            args["tp_bot"] = [self.home[0] + 2, self.home[1], self.home[2]]
        self.b.probe(**args)

    def put(self, lane, bot_x=2, host_x=None):
        """Altron (and the commander, if host_x) to the start of a lane."""
        ox, _ = self.origin
        z = self.lane_z(lane) + 0.5
        args = {"tp_bot": [ox + bot_x + 0.5, Y, z], "heal": True, "protect": True,
                "clear_entities": [ox + 16, Y, self.origin[1] + 10, 24]}
        if host_x is not None:
            args["tp_host"] = [ox + host_x + 0.5, Y, z]
        self.b.probe(**args)
        time.sleep(2.5)   # the body sees the new place, the chunks come

    # ------------------------------------------------------------------ parts
    def control(self, **args):
        """One move of his hands straight through the body, the way the AI makes it (the tool control)."""
        return self.b.task("control", args, 30)

    def block_at(self, x, y, z):
        return (self.b.probe(blocks=[[x, y, z]]).get("blocks") or {}).get("%d,%d,%d" % (x, y, z), "?")

    def part_hands(self):
        """The keyboard and the mouse alone, no AI in between: walk, jump onto a block, break it, put it back."""
        ox, _ = self.origin
        c = self.lane_z(0)
        self.put(0, 2)
        before, _, _ = self.where()
        t0 = time.time()
        res = self.control(x=ox + 29, z=c, keys=["forward", "sprint"], ticks=60)
        after, _, _ = self.where()
        moved = dist(before, after) if before and after else 0
        self.add("руки", "идти (forward+sprint 3 с)", "ok" if moved >= 10 else ("warn" if moved >= 3 else "fail"),
                 time.time() - t0, "прошёл %.1f бл.; %s" % (moved, str(res)[:300]))
        c = self.lane_z(1)
        self.put(1, 6)
        t0 = time.time()
        res = self.control(x=ox + 8, y=Y, z=c, keys=["forward", "jump"], ticks=20)
        after, _, _ = self.where()
        up = after[1] - Y if after else -1
        self.add("руки", "запрыгнуть на блок", "ok" if up >= 0.9 else "fail", time.time() - t0,
                 "поднялся на %.1f бл.; %s" % (up, str(res)[:300]))
        # break the step with a pickaxe and put it back: slot 1 the pickaxe, slot 2 the stone
        self.put(1, 6)
        self.b.probe(clear_bot=True, give_bot=[["minecraft:iron_pickaxe", 1], ["minecraft:stone", 8]])
        time.sleep(1)
        t0 = time.time()
        res = self.control(x=ox + 8, y=Y, z=c, slot=1, left="hold", ticks=40)
        got = self.block_at(ox + 8, Y, c)
        self.add("руки", "сломать блок (left hold)", "ok" if "air" in got else "fail", time.time() - t0,
                 "на месте блока: %s; %s" % (got, str(res)[:300]))
        t0 = time.time()
        res = self.control(x=ox + 8, y=Y - 1, z=c, slot=2, right="click", ticks=5)
        got = self.block_at(ox + 8, Y, c)
        self.add("руки", "поставить блок (right click)", "ok" if "stone" in got else "fail", time.time() - t0,
                 "на месте блока: %s; %s" % (got, str(res)[:300]))
        # the mouse follows the commander: he stands at the end of the flat lane, Altron runs to him
        c = self.lane_z(0)
        self.put(0, 2, host_x=16)
        t0 = time.time()
        res = self.control(track="player:" + (self.b.state().get("owner") or ""), keys=["forward", "sprint"], ticks=50)
        bot, host, _ = self.where()
        d = dist(bot, host) if bot and host else 99
        self.add("руки", "бежать к командиру (track)", "ok" if d <= 4 else ("warn" if d <= 8 else "fail"), time.time() - t0,
                 "до командира %.1f бл.; %s" % (d, str(res)[:300]))

    def check_talk(self, name, phrase, want_tools=(), want_speech=True, part="разговор", english=False, timeout=150):
        r = self.talk(phrase, timeout)
        problems = []
        if want_speech and not r["said"]:
            problems.append("ничего не сказал")
        missing = [t for t in want_tools if t not in r["tools"]]
        if missing:
            problems.append("не вызвал: " + ", ".join(missing))
        if english and r["said"] and re.search(r"[а-яё]", " ".join(r["said"]), re.I):
            problems.append("ответил не по-английски")
        canned = [s for s in r["said"] if s.strip() in ("Есть, командир.", "Принял.", "Секунду, подумаю.", "Остановился.")]
        if canned:
            problems.append("заготовленная фраза: " + canned[0])
        verdict = "ok" if not problems else ("warn" if r["said"] or r["tools"] else "fail")
        note = ("первое слово через %.1f с; " % r["first"] if r["first"] is not None else "") + \
               ("сказал: «%s»" % " / ".join(r["said"])[:300] if r["said"] else "молчал") + \
               ("; инструменты: %s" % ", ".join(r["tools"]) if r["tools"] else "") + \
               ("; " + "; ".join(problems) if problems else "")
        self.add(part, name, verdict, r["seconds"], note, "\n".join(r["lines"][-60:]))
        return r

    def part_talk(self):
        self.put(0, 3, host_x=6)
        self.check_talk("привет", "Альтрон, привет! Как дела?")
        self.check_talk("вкусы", "Альтрон, а что ты любишь больше всего в этом мире?")
        self.put(0, 3, host_x=20)
        r = self.check_talk("иди ко мне", "Альтрон, иди ко мне", want_tools=(), want_speech=False)
        self.wait_idle(120)   # he walks with his own keys: move after move
        bot, host, _ = self.where()
        d = dist(bot, host) if bot and host else 99
        self.add("разговор", "иди ко мне: дошёл?", "ok" if d <= 5 else "fail", 0,
                 "до командира %.1f бл., инструменты: %s" % (d, ", ".join(r["tools"]) or "нет"))
        self.check_talk("похвала", "Молодец, Альтрон, отлично сделал", want_tools=("feedback",))
        self.check_talk("тиммейт", "Альтрон, говори как тиммейт", want_tools=("persona",))
        st = self.b.state()
        self.add("разговор", "тиммейт: включился?", "ok" if st.get("persona") == "teammate" else "fail", 0,
                 "persona = %s" % st.get("persona"))
        self.check_talk("шутка тиммейта", "Альтрон, расскажи, как прошёл твой день")
        self.check_talk("свой голос", "Альтрон, верни свой обычный голос", want_tools=("persona",))
        self.check_talk("английский", "Altron, how are you doing today?", english=True)
        self.check_talk("снова русский", "Альтрон, а теперь по-русски: где мы сейчас?")
        # stop: the body stops at once, the words are his own
        self.b.say("Альтрон, иди за мной")
        self.wait_idle(60)
        self.log.mark()
        t0 = time.time()
        self.b.say("стоп")
        time.sleep(1.5)
        st = self.b.state()
        stopped = st["running"] is None
        self.wait_idle(60)
        lines = self.log.since_mark()
        self.add("разговор", "стоп", "ok" if stopped else "fail", time.time() - t0,
                 "остановился за 1.5 с: %s; сказал: «%s»" % ("да" if stopped else "НЕТ", " / ".join(spoken(lines))[:200]),
                 "\n".join(lines[-30:]))

    def part_goals(self):
        ox, oz = self.origin
        self.put(0, 3, host_x=6)
        self.b.probe(give_bot=[["minecraft:iron_sword", 1], ["minecraft:bread", 8]])
        self.check_talk("охрана: цель", "Альтрон, охраняй меня, пока я тут стою", part="цели")
        st = self.b.state()
        self.add("цели", "охрана: записал цель?", "ok" if st.get("goals") or (st["running"] or [0, ""])[1] == "guard"
                 else "warn", 0, "цели: %s; задача: %s" % (st.get("goals"), st.get("running")))
        # two husks (they do not burn in the sun) at the far end of the lane
        z = self.lane_z(0)
        self.log.mark()
        t0 = time.time()
        self.b.probe(commands=["summon minecraft:husk %d %d %d" % (ox + 26, Y, z), "summon minecraft:husk %d %d %d" % (ox + 27, Y, z)])
        alive = 2
        while time.time() - t0 < 90 and alive:
            time.sleep(5)
            r = self.b.probe(entities=[ox + 16, Y, z, 20])
            alive = sum(1 for e in r.get("entities", []) if "husk" in e["type"] and e["alive"])
        lines = self.log.since_mark()
        self.add("цели", "охрана: враги", "ok" if not alive else "fail", time.time() - t0,
                 "осталось живых: %d; инструменты: %s" % (alive, ", ".join(tools_called(lines)) or "нет"),
                 "\n".join(lines[-50:]))
        self.check_talk("охрана: снять", "Альтрон, всё, можно больше не охранять", part="цели")
        st = self.b.state()
        self.add("цели", "охрана: снял цель?", "ok" if not st.get("goals") else "warn", 0, "цели: %s" % st.get("goals"))
        self.b.task("stop", {}, 5)
        # quiet: with idle thinking every minute, he gets a chance to talk by himself
        old = self.cfg.get("idle_think_minutes", 4)
        self.b.set("idle_think_minutes", 1)
        self.log.mark()
        self.say_out("Тишина 100 с: смотрю, заговорит ли он сам...")
        time.sleep(100)
        self.wait_idle(60)
        lines = self.log.since_mark()
        thoughts = [ln for ln in lines if "Наблюдение" in ln]
        self.add("цели", "тишина", "ok" if thoughts else "fail", 100,
                 "наблюдений: %d; сказал: «%s»" % (len(thoughts), " / ".join(spoken(lines))[:200] or "ничего (решил молчать)"),
                 "\n".join(lines[-30:]))
        self.b.set("idle_think_minutes", old)

    def part_events(self):
        self.put(0, 3, host_x=6)
        for name, cmds, back in (("ночь", ["time set 13000"], ["time set noon"]),
                                 ("гроза", ["weather thunder"], ["weather clear"])):
            self.log.mark()
            t0 = time.time()
            self.b.probe(commands=cmds)
            time.sleep(12)
            self.wait_idle(90)
            time.sleep(max(0.0, 35 - (time.time() - t0)))   # quiet events come at most every 30 s
            lines = self.log.since_mark()
            heard = any("событие мира" in ln or "world event" in ln for ln in lines)
            self.add("события", name, "ok" if heard else "warn", time.time() - t0,
                     "событие дошло: %s; сказал: «%s»" % ("да" if heard else "нет", " / ".join(spoken(lines))[:200] or "ничего"),
                     "\n".join(lines[-30:]))
            self.b.probe(commands=back)

    # ------------------------------------------------------------------ the report
    def environment(self):
        info = {"дата": self.started.strftime("%Y-%m-%d %H:%M"), "python": platform.python_version(),
                "ОС": platform.platform()}
        try:
            info["код"] = subprocess.run(["git", "log", "-1", "--format=%h %s"], cwd=str(ROOT), capture_output=True,
                                         text=True, timeout=10).stdout.strip()
        except Exception:
            pass
        try:
            info["видеокарта"] = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used",
                                                 "--format=csv,noheader"], capture_output=True, text=True,
                                                timeout=10).stdout.strip()
        except Exception:
            info["видеокарта"] = "нет nvidia-smi"
        cfg = {k: ("***" if k in SECRET_KEYS and v else v) for k, v in self.cfg.items()
               if k not in ("wake_words", "extra_bot_mods")}
        return info, cfg

    def mod_log(self, folder):
        """[Altron] lines and errors of a game's log written during the test."""
        path = rel(self.cfg.get(folder, "../" + folder.replace("_dir", ""))) / "logs" / "latest.log"
        if not path.exists():
            return "(нет %s)" % path
        since = self.started.strftime("%H:%M:%S")
        out = []
        with open(path, encoding="utf-8", errors="replace") as f:
            for ln in f:
                m = re.match(r"\[(\d\d:\d\d:\d\d)\]", ln)
                if m and m.group(1) < since:
                    continue
                if "[Altron]" in ln or "Exception" in ln or "/ERROR]" in ln or "\tat com.altron" in ln:
                    out.append(ln.rstrip())
        return "\n".join(out[-150:]) or "(ничего)"

    def write(self, crash=""):
        REPORT_DIR.mkdir(exist_ok=True)
        path = REPORT_DIR / ("field_test_%s.md" % self.started.strftime("%Y%m%d_%H%M"))
        info, cfg = self.environment()
        counts = {m: sum(1 for r in self.results if r[2] == m) for m in ("✅", "⚠️", "❌")}
        out = ["# Полевой тест Альтрона — %s" % info["дата"], "",
               "✅ %d · ⚠️ %d · ❌ %d" % (counts["✅"], counts["⚠️"], counts["❌"]), ""]
        out += ["| часть | проверка | итог | с | что было |", "| --- | --- | --- | --- | --- |"]
        for part, name, mark, sec, note, _ in self.results:
            out.append("| %s | %s | %s | %.0f | %s |" % (part, name, mark, sec, note.replace("|", "/").replace("\n", " ")))
        if crash:
            out += ["", "## Тест прервался", "```", crash, "```"]
        out += ["", "## Подробности"]
        for part, name, mark, sec, note, details in self.results:
            if details:
                out += ["", "### %s %s / %s" % (mark, part, name), "```", details, "```"]
        errors = [ln for ln in BrainLog(self.log.path).since_start(self.started) if re.search(r"Ошибка|Error|Traceback|ПЕРЕЗАПУСК", ln)]
        out += ["", "## Ошибки мозга за время теста", "```", "\n".join(errors[-80:]) or "(нет)", "```"]
        out += ["", "## Журнал тела (bot/logs/latest.log)", "```", self.mod_log("bot_dir"), "```"]
        out += ["", "## Журнал игры командира (host/logs/latest.log)", "```", self.mod_log("host_dir"), "```"]
        try:
            import dataset
            out += ["", "## Опыт для обучения", dataset.stats(str(BRAIN_DIR / "logs" / "dataset"))]
        except Exception as e:
            out += ["", "## Опыт для обучения", "не прочитал: %s" % e]
        out += ["", "## Окружение", "```", json.dumps(info, ensure_ascii=False, indent=1), "```",
                "", "## config.json (ключи скрыты)", "```", json.dumps(cfg, ensure_ascii=False, indent=1), "```"]
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
        return path


def _since_start(self, started):
    """Lines of the brain log from the start of the test (by their date and time)."""
    if not self.path.exists():
        return []
    stamp = started.strftime("%Y-%m-%d %H:%M:%S")
    with open(self.path, encoding="utf-8", errors="replace") as f:
        return [ln.rstrip() for ln in f if ln[:19] >= stamp]


BrainLog.since_start = _since_start


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", default="hands,talk,goals,events")
    ap.add_argument("--keep-course", action="store_true")
    ap.add_argument("--port", type=int, default=0)
    a = ap.parse_args(argv)
    cfg = json.loads((BRAIN_DIR / "config.json").read_text(encoding="utf-8"))
    b = Brain(a.port or cfg["brain_port"])
    t = FieldTest(b, cfg, a)
    t.say_out("Полевой тест Альтрона. Жду мозг и тело (до 10 минут)...")
    for _ in range(120):
        try:
            st = b.state()
            if st.get("joined") and st.get("state", {}).get("pos"):
                break
        except OSError:
            pass
        time.sleep(5)
    else:
        print("Мозг или тело Альтрона не на связи: запусти session.py (или start_altron.bat и /altron) и повтори.")
        return 1
    parts = [p.strip() for p in a.only.split(",") if p.strip()]
    crash = ""
    try:
        t.say_out("Строю полосу препятствий на высоте %d над командиром..." % Y)
        t.build_course()
        for p in parts:
            t.say_out("== часть: %s" % p)
            getattr(t, "part_" + p)()
    except KeyboardInterrupt:
        crash = "остановлен вручную (Ctrl+C)"
    except Exception:
        crash = traceback.format_exc()
        print(crash)
    finally:
        try:
            t.say_out("Разбираю полосу и возвращаю всех на место...")
            t.take_down()
        except Exception as e:
            print("не разобрал полосу: %s" % e)
    path = t.write(crash)
    print("\nОтчёт: %s" % path)
    print("Отправь его так (из папки altron):")
    print("  git switch -c test-report-%s" % t.started.strftime("%m%d-%H%M"))
    print("  git add -f test-reports")
    print('  git commit -m "Field test report"')
    print("  git push -u origin HEAD")
    print("  git switch -")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
