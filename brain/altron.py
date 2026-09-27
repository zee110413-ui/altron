"""Altron's brain: links the host game (voice), the bot client (body), speech and the local LLM."""
import asyncio
import base64
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import numpy as np

from agent import ACTION_WORDS, Agent, NOTIFY_DONE, TASK_TOOLS, _said_before, is_question, is_recipe_question, needs_thinking
from launcher import BRAIN_DIR, PROFILES, apply_profile, install_new_mod, launch_bot, rel, server_address
from memory import LongMemory, keywords, stems
from speech import loud_enough

# "стой", "останови все задачи", "прекрати" must stop him at once (they went to the AI as orders before)
STOP_RE = re.compile(r"\b(стоп|стой|хватит|останови\w*|прекрати\w*|отмена|отмени\w*|отбой|замри|stop)\b", re.I)
ENDLESS = {"follow", "guard"}  # modes: a new task simply replaces them
MOVES = {"come", "goto", "goto_place"}   # just walking: a new order replaces it instead of waiting behind it
MEMORY_TOOLS = {"remember", "forget", "recall", "mark_place", "goto_place"}
VOICED_RMS = 400        # a 20 ms voice frame louder than this is speech (s16 scale)
SILENCE_END = 0.6       # this long without speech ends a phrase (shorter would cut phrases at pauses)
# Said at once to an order (synthesized in advance), so the commander hears an answer without waiting for the AI
ACKS = ("Есть, командир.", "Принял.", "Сделаю.", "Понял, командир.")
SPEECH_CHARS = 260               # long answers go to chat; only the start is spoken
SPEECH_CHARS_VOICE_ONLY = 420    # without chat replies the voice is the only channel: speak more of it
# work blocks that are not used up: if one stands nearby, the supply chain does not build another
STATIONS = ["minecraft:crafting_table", "minecraft:furnace"]
REPEAT_SEC = 25                  # the same words are not said again within this time
ROUTINES = {"fetch": "принести предмет из сундука", "stash": "сложить вещи в сундук",
            "study": "изучение производства", "load_machine": "перенос материала в машину"}
# "куда это положить?", "что делать с рудой?" — answered from the production map he learned
WHERE_PUT_RE = re.compile(r"куда\s+(?:мне\s+|нам\s+|его\s+|её\s+|их\s+)?(?:положить|класть|ложить|девать|деть|отнести|сунуть|кинуть|"
                          r"засунуть|отправить)|что\s+(?:мне\s+)?делать\s+с|где\s+(?:переработать|переплавить|сделать)", re.I)
# How sure Whisper must be (mean log-probability of the words) to act on a phrase said without "Альтрон".
# Measured on the commander's real microphone: right phrases -0.3..-0.9, garbage mostly below -1.0
STT_MIN_CONF = -1.0
STT_SURE = -0.5                  # a one-word order must be heard clearly
SHORT_ANSWERS = {"да", "нет", "ага", "угу", "ок", "окей", "ладно", "хорошо", "давай", "конечно", "неа", "вот", "это", "тот"}
# bare exclamations: not meant for Altron even from the commander
INTERJECTIONS = {"блин", "ой", "ай", "ммм", "мм", "эм", "эээ", "хм", "ну", "ах", "ох", "ух", "фух", "бля", "капец", "жесть",
                 "вау", "опа", "оп", "о", "а", "э", "так"}


def _edit_distance(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def has_wake_word(text, wake_words):
    """«Альтрон» at the start of a phrase, also as Whisper mishears it («Алтрон», «Альтран», «Олтрон», «Альтрона»)."""
    low = (text or "").lower().replace("ё", "е")
    if any(w in low for w in wake_words):
        return True
    for w in re.findall(r"[а-яa-z]+", low)[:3]:
        if 5 <= len(w) <= 10 and min(_edit_distance(w, "альтрон"), _edit_distance(w[:7], "альтрон")) <= 2:
            return True
    return False


def near_text(msg, creatures=8):
    """What is around, for the AI: creatures cut to a few lines, but ladders, levers, doors and chests always stay
    (they came after the creatures, and with a few cows around the ladder used to be cut off)."""
    ents, sep, blocks = msg.partition("Блоки рядом")
    text = "\n".join(ents.strip().splitlines()[:creatures])
    return (text + "\n" + sep + blocks).strip() if sep else text


class Hub:
    def __init__(self, cfg):
        self.cfg = cfg
        self.owner = ""
        self.host = None          # StreamWriter of the player's game
        self.bot = None           # StreamWriter of the bot's game
        self.bot_proc = None
        self.state = {}
        self.pending = {}         # cmd id -> Future(result)
        self.task_waiters = {}    # task id -> Future(event)
        self.agent_tasks = set()  # task ids started by the agent that we did not wait for
        self.running = None       # (task id, tool name) of the agent's task in progress
        self.running_args = None
        self.queue = []           # [(tool name, args)] waiting for the running task
        self.queue_log = []       # results of a queue run, reported together at the end
        self.last_question = None  # (text, time) of the last ask_player without an answer yet
        self.macro_task = None     # running obtain (automatic supply chain)
        self.macro_args = None
        self.recorder = None       # demo video recorder (records every spoken line)
        self.setup_result = None   # reply of the host to a demo "setup" request
        self.host_waiters = {}     # test course: probe id -> Future(what the world really is)
        self.joined = False
        self.director_lines = set()  # demo orders already delivered directly (their chat echo is ignored)
        self.next_id = 1
        self.requests = asyncio.Queue()
        self.voice = {}           # speaker uuid -> {"name","chunks","last","dist"}
        self.windows = {}         # speaker name -> time until which no wake word is needed
        self.heard = 0            # voice phrases addressed to Altron so far
        self.acks = []            # [(text, pcm)] instant acknowledgements, synthesized once
        self.ack_n = 0
        self.acked = False        # the current order was already acknowledged aloud
        self.said_recently = []   # [(time, text)]: no saying the same thing twice in a row
        self.bot_lite = True      # the bot's memory-saving client; off after the server refused it
        self.bot_server = 25566   # the commander's LAN port, or "ip:port" of a server (Radmin VPN)
        self.remote = False       # Altron plays on someone else's server: he hears and speaks through his own client
        self.mic_warned = False
        self.world_name = ""      # the host's world (save folder): memories are kept per world
        self.mic_failed = None    # the demo microphone could not speak into the voice chat
        self.stt = None
        self.tts = None
        self.knowledge = None
        self.memory = LongMemory(rel(cfg.get("memory_dir", "memory")))
        self.resumed = False       # the talk before the last restart was already recalled
        self.agent = Agent(cfg, self)
        self.loop = None
        self.busy = False

    # ------------------------------------------------------------------ utils
    def log(self, text):
        print(time.strftime("%H:%M:%S"), text, flush=True)

    def send(self, writer, obj):
        if writer is None:
            return False
        try:
            writer.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
            return True
        except Exception:
            return False

    async def say(self, text):
        text = text.strip()
        if not text:
            return
        now = time.time()
        self.said_recently = [(t, s) for t, s in self.said_recently if now - t < REPEAT_SEC]
        # short acknowledgements ("Есть, командир") answer each new order and may repeat
        if len(stems(text)) > 3 and _said_before(text, [s for _, s in self.said_recently], 0.8):
            # "Командир, я не понял" five times in a row helps nobody
            self.log("(повтор, не говорю) " + text)
            return
        self.said_recently.append((now, text))
        self.log("Альтрон: " + text)
        self.memory.log("Альтрон", text)
        if self.owner:
            # the commander may answer without saying the name
            self.windows[self.owner] = time.time() + self.cfg.get("conversation_window_sec", 20) + 10
        chat = self.cfg.get("chat_replies", False)
        if chat and self.bot is not None:
            flat = re.sub(r"[*_#`]", "", re.sub(r"\s+", " ", text))
            for i in range(0, min(len(flat), 750), 250):   # chat lines are limited to 256 characters
                self.send(self.bot, {"type": "cmd", "id": 0, "name": "chat", "args": {"text": flat[i:i + 250]}})
        elif chat and self.host is not None and self.bot is None:
            self.send(self.host, {"type": "notify", "text": text})
        if self.tts is None or (self.host is None and self.recorder is None and not (self.remote and self.bot)):
            return
        spoken = text
        limit = SPEECH_CHARS if chat else SPEECH_CHARS_VOICE_ONLY
        if len(spoken) > limit:
            # speak the first sentences only (with chat replies on, the full text is in the game chat)
            cut = max(spoken.rfind(". ", 0, limit), spoken.rfind("! ", 0, limit), spoken.rfind("? ", 0, limit))
            spoken = spoken[:cut + 1] if cut > 40 else spoken[:limit]
        # sentence by sentence: the first one sounds while the next ones are still being synthesized
        gen = self.tts.synth(spoken)
        chunks, at = [], None
        while True:
            pcm = await asyncio.to_thread(next, gen, None)
            if pcm is None:
                break
            chunks.append(pcm)
            if at is None and self.recorder is not None:
                at = self.recorder.elapsed()
            await self.play(pcm)
        if self.recorder is not None and chunks:
            self.recorder.add(b"".join(chunks), "Альтрон", spoken, at=at)

    async def play(self, pcm):
        """Send speech (48 kHz s16le PCM) into the voice chat: in the commander's world the host's plugin plays it
        from Altron's head; on someone else's server Altron's own client says it like a player's microphone."""
        target = self.bot if self.remote else self.host
        if target is None:
            return
        self.send(target, {"type": "speak", "pcm": base64.b64encode(pcm).decode("ascii")})
        await target.drain()

    async def acknowledge(self):
        """Instant answer to an order, before the AI has even thought: a short phrase synthesized in advance."""
        if not self.acks and self.tts is not None:
            def make():
                return [(t, b"".join(self.tts.synth(t))) for t in ACKS]
            self.acks = await asyncio.to_thread(make)
        if not self.acks:
            return
        text, pcm = self.acks[self.ack_n % len(self.acks)]
        self.ack_n += 1
        self.log("Альтрон (сразу): " + text)
        if self.recorder is not None:
            self.recorder.add(pcm, "Альтрон", text)
        await self.play(pcm)

    def state_text(self):
        s = self.state
        if not self.bot:
            return "Альтрон ещё не в игре (игрок должен ввести /altron)."
        if not s:
            return "Альтрон в игре."
        pos = s.get("pos", [0, 0, 0])
        text = "Альтрон: x=%d y=%d z=%d, здоровье %s/20, еда %s/20, в руке: %s" % (
            pos[0], pos[1], pos[2], s.get("hp"), s.get("food"), s.get("held"))
        if s.get("task"):
            text += ", задача: %s %s" % (s.get("task"), s.get("progress", ""))
        if s.get("owner_pos"):
            text += "; командир %s: x=%d y=%d z=%d" % ((self.owner,) + tuple(int(v) for v in s["owner_pos"]))
        if s.get("owner_look"):
            # "вот этот", "вот сюда", "вот тот сундук" — this is what he means
            text += "; командир СМОТРИТ на: %s" % s["owner_look"]
        return text

    # ------------------------------------------------------------------ bot commands
    async def bot_call(self, name, args):
        """Send one command to the bot and return its raw result dict."""
        cid = self.next_id
        self.next_id += 1
        fut = self.loop.create_future()
        self.pending[cid] = fut
        self.send(self.bot, {"type": "cmd", "id": cid, "name": name, "args": args})
        try:
            return await asyncio.wait_for(fut, 20)
        except asyncio.TimeoutError:
            self.pending.pop(cid, None)
            return {"ok": False, "msg": "нет ответа от бота"}

    async def probe(self, **what):
        """Test course: ask the host's server how the world really is (blocks, players, inventories); may also
        move the players or reset blocks first. See HostSetup.probe."""
        rid = self.next_id
        self.next_id += 1
        fut = self.loop.create_future()
        self.host_waiters[rid] = fut
        self.send(self.host, dict(what, type="probe", rid=rid))
        try:
            return await asyncio.wait_for(fut, 20)
        except asyncio.TimeoutError:
            self.host_waiters.pop(rid, None)
            return {"error": "мир не ответил"}

    async def recipe_answer(self, text, targets):
        """Material for answering "what do I need to make X / what is X made of / how to build X": the whole recipe
        down to base resources, or, for a multiblock structure (a coke oven, a pumpjack...), its blocks."""
        k = self.knowledge
        obj = re.sub(r"(?i)^.*?(что|чего|какие|сколько|из\s+чего|как|где|рецепт)\b", "", text)
        obj = re.sub(r"(?i)\b(же|мне|нам|тебе|для|этого|нужно|надо|нужны|нужна|потребуется|необходимо|чтобы|сделать|"
                     r"скрафтить|получить|добыть|построить|собрать|приготовить|выплавить|сварить|делается|делают|"
                     r"взять|найти|достать|у|на|альтрон)\b|[?!.,]", " ", obj).strip()
        out = ""
        # a multiblock structure: its blocks (the body knows their names in the game's language)
        if self.bot is not None and obj:
            data = (await self.bot_call("multiblocks_data", {})).get("multiblocks", [])

            def stem(w):   # "коксовой печи" and "Коксовая печь": the same words without their endings
                return w[:max(3, len(w) - 2)]
            stems = [stem(w) for w in re.findall(r"[а-яёa-z0-9]+", obj.lower()) if len(w) >= 3]
            # what people call the machines that have no Russian name in the game (Immersive Petroleum's)
            aliases = {"pumpjack": r"качалк|нефтян\w* насос|насос\w* для нефти|скважин", "derrick": r"буров|вышк|деррик",
                       "distillationtower": r"дистилл|ректифик|колонн", "cokerunit": r"кокер|коксов\w* установк",
                       "oiltank": r"нефтян\w* (бак|резервуар|цистерн)|резервуар для нефти", "crusher": r"дробилк",
                       "hydrotreater": r"гидроочист|высокого давления", "excavator": r"экскаватор"}
            for mb in data:
                name = mb.get("name", "").split(" (")[0].lower()
                words = [stem(w) for w in re.findall(r"[а-яёa-z0-9]+", name) if len(w) >= 3]
                mb_id = mb.get("name", "").rsplit("/", 1)[-1].rstrip(")")
                alias = aliases.get(mb_id)
                if (alias and re.search(alias, obj.lower())) or (
                        words and stems and all(any(s.startswith(w) or w.startswith(s) for s in stems) for w in words)):
                    mats = ", ".join("%dx %s" % (n, k.name(i) if k else i) for i, n in mb.get("materials", {}).items())
                    out += ("\n[Постройка %s — это многоблочная машина: блоки для неё] %s; собирается ударом инженерного "
                            "молота (immersiveengineering:hammer) по нужному блоку." % (mb["name"], mats))
        if not out and k is not None:
            found = targets[:2] or ([(k.find_id(obj), obj)] if obj and k.find_id(obj) else [])
            for i, n in found:
                tree = await asyncio.to_thread(k.plan, i, 1, None)
                out += "\n[Как сделать %s — рецепт до базовых ресурсов]\n%s" % (k.name(i) or n, tree[:2500])
        out += ("\n[Подсказка] Это ВОПРОС, а не приказ: ответь командиру коротко и по делу — главные ингредиенты с количеством "
                "и нужные станки/машины. Ничего не делай и не вызывай obtain. Если в справочнике нет — поищи (wiki, "
                "web_search) или честно скажи, что не знаешь.")
        return out

    async def look(self, question):
        info = await self.bot_call("screen", {})
        shot = await self.bot_call("screenshot", {})
        if not shot.get("ok"):
            return "ОШИБКА: " + shot.get("msg", "")
        try:
            answer = await self.agent.llm.vision(question or "Что ты видишь?", info.get("msg", ""), shot["image"],
                                                 shot.get("width", 0), shot.get("height", 0))
        except Exception as e:
            return "Зрение недоступно (%s). Данные окна: %s" % (e, info.get("msg", ""))
        return "Вижу: %s\n\nДанные окна: %s" % (answer, info.get("msg", ""))

    GUI_ACTIONS = {"info": "screen", "click": "gui_click", "widget": "gui_widget", "type": "gui_type", "key": "gui_key"}
    POSITION_TOOLS = {"use_block", "break_block", "place_block", "transport_block"}   # goto may go anywhere

    def too_far(self, name, args):
        """The small model sometimes passes screen pixels (from gui/look) as world coordinates and walks off for
        minutes. A block to use or break this far away is almost surely such a mix-up: refuse and say why."""
        if name not in self.POSITION_TOOLS or "x" not in args or not self.state.get("pos"):
            return ""
        try:
            x, y, z = float(args["x"]), float(args.get("y", self.state["pos"][1])), float(args["z"])
        except (TypeError, ValueError):
            return ""
        px, py, pz = self.state["pos"]
        d = ((x - px) ** 2 + (y - py) ** 2 + (z - pz) ** 2) ** 0.5
        limit = 300 if name == "transport_block" else 120
        if d > limit or not -64 <= y <= 320:
            return ("ОШИБКА: точка %d %d %d в %d блоках от тебя (или вне высоты мира) — это не похоже на нужное место. "
                    "Пиксели экрана из gui/look — не координаты мира; бери координаты из [Состояние], [Рядом], find_block."
                    % (x, y, z, d))
        return ""

    async def run_tool(self, name, args, wait_sec):
        if name in MEMORY_TOOLS:
            return await self.memory_tool(name, args, wait_sec)
        if name == "web_search":
            import web
            return await web.search(str(args.get("query", "")))
        if name in ("wiki", "plan"):
            if self.knowledge is None:
                return "Справочник ещё загружается, попробуй через минуту."
            if name == "wiki":
                return await asyncio.to_thread(self.knowledge.search, str(args.get("query", "")), 5)
            have = None
            if self.bot is not None:
                res = await self.bot_call("inventory_ids", {})
                if res.get("ok"):
                    have = res.get("items") or {}
            return await asyncio.to_thread(self.knowledge.plan, str(args.get("item", "")), args.get("count", 1) or 1, have)
        if self.bot is None:
            return "Альтрон ещё не в игре. Попроси игрока ввести команду /altron в своём мире."
        far = self.too_far(name, args)
        if far:
            return far
        if name == "look":
            return await self.look(args.get("question", ""))
        if name == "gui":
            name = self.GUI_ACTIONS.get(args.get("action", "info"), "screen")
        if name == "build_multiblock" and str(args.get("name", "")).lower() in ("", "list", "список"):
            name = "multiblocks"
        macro_busy = self.macro_task is not None and not self.macro_task.done()
        if name in ENDLESS and self.running is not None and self.running[1] == name and self.running_args == args:
            # "иди за мной" while already following: restarting the path every phrase made him stumble
            return "уже выполняю %s — продолжаю, не перезапускаю" % name
        if name == "stop":
            self.queue.clear()
            self.queue_log.clear()
            self.running = None
            if macro_busy:
                self.macro_task.cancel()
        elif name in TASK_TOOLS and macro_busy:
            if (name, args) in self.queue or (name == "obtain" and args == self.macro_args):
                return "Уже в очереди — не повторяй. Закончи ход и жди [Событие]."
            self.queue.append((name, args))
            return ("Поставил в очередь (№%d): сейчас я выполняю obtain. О результате придёт [Событие]. "
                    "Не повторяй эту команду." % len(self.queue))
        elif name == "obtain":
            return await self.start_obtain(args)
        elif name in ROUTINES:
            self.macro_args = args
            self.macro_task = asyncio.create_task(self.routine(name, args))
            return "Начал %s. Делаю сам по шагам; о результате придёт [Событие]." % ROUTINES[name]
        elif name in TASK_TOOLS and self.running is not None and self.running[1] not in ENDLESS | MOVES and name not in ENDLESS:
            # a job in progress (mining, smelting...): the new one waits its turn. Walking somewhere is not a job:
            # a new order replaces it at once, like a person who stops walking to do what he was just asked
            same = [n for n, a in self.queue if n == name and a == args]
            if same or (self.running[1] == name and self.running_args == args):
                return "Это уже выполняется или стоит в очереди — не повторяй. Закончи ход и жди [Событие]."
            self.queue.append((name, args))
            return ("Поставил в очередь (№%d): сейчас выполняется %s. Начну сам, когда закончу; "
                    "о результатах всей очереди придёт одно [Событие]. Не повторяй эту команду." % (len(self.queue), self.running[1]))
        elif name in ENDLESS:
            self.queue.clear()
        return await self.start_task(name, args, wait_sec)

    # ------------------------------------------------------------------ long-term memory
    async def memory_tool(self, name, args, wait_sec):
        m = self.memory
        if name == "remember":
            return m.remember(str(args.get("text", "")))
        if name == "forget":
            return m.forget(str(args.get("text", "")))
        if name == "mark_place":
            key = "owner_pos" if str(args.get("where", "me")).lower() in ("player", "owner", "игрок", "командир") else "pos"
            pos = self.state.get(key)
            if not pos:
                return "не знаю, где это: %s сейчас не вижу" % ("командира" if key == "owner_pos" else "себя")
            return m.set_place(str(args.get("name", "место")), pos, self.state.get("dim", "minecraft:overworld"))
        if name == "goto_place":
            p = m.place(str(args.get("name", "")))
            if p is None:
                known = "; ".join(m.place_line(x) for x in m.world_places()) or "пока ни одного"
                return "не знаю места «%s». Известные места: %s" % (args.get("name", ""), known)
            x, y, z = p["pos"]
            return "иду к месту %s — %s" % (m.place_line(p), await self.run_tool("goto", {"x": x, "y": y, "z": z}, wait_sec))
        # recall: everything the bot and the brain remember about it
        query = str(args.get("query", "")).strip()
        if re.search(r"прошл|вчера|последн|раньше|недавно|до перезапуск", query.lower()):
            last = m.last_conversation(12) or "журнал пуст"
            return "Последние записи журнала (до этого запуска):\n" + last
        if not query or query.lower() in ("все", "всё", "all", "*"):
            parts = [m.overview()]
            if self.bot is not None:
                parts.append((await self.bot_call("memory", {})).get("msg", ""))
            return "\n".join(p for p in parts if p)
        parts = m.search(query, 8, older_than=time.time() - 60)   # not the question that was just asked
        seen = await self.recall_world(query)
        if seen:
            parts.append(seen)
        return "\n".join(parts) if parts else "ничего не помню про «%s» (блоки ищи через find_block)" % query

    async def recall_world(self, query):
        """What the body remembers about it: items in containers, players/animals/vehicles it met."""
        if self.bot is None:
            return ""
        parts = []
        for cmd, title in (("recall_items", "В сундуках и машинах"), ("recall_entities", "Встречал")):
            lines = []
            for word in keywords(query):
                r = await self.bot_call(cmd, {"query": word, "limit": 6})
                if r.get("ok") and not r.get("msg", "").startswith(("не помню", "я ещё", "пока никого")):
                    lines += [x for x in r["msg"].splitlines() if x not in lines]
            if lines:
                parts.append("%s:\n%s" % (title, "\n".join(lines[:8])))
        return "\n".join(parts)

    async def start_task(self, name, args, wait_sec):
        res = await self.bot_call(name, args)
        msg = res.get("msg", "")
        if not res.get("ok"):
            return "ОШИБКА: " + msg
        tid = res.get("task_id")
        if tid is None:
            return msg
        self.running = (tid, name)
        self.running_args = args
        if wait_sec <= 0:
            self.agent_tasks.add(tid)
            return msg + " (выполняется в фоне, о результате придёт [Событие])"
        wfut = self.loop.create_future()
        self.task_waiters[tid] = wfut
        try:
            ev = await asyncio.wait_for(wfut, wait_sec)
        except asyncio.TimeoutError:
            self.task_waiters.pop(tid, None)
            self.agent_tasks.add(tid)
            return "задача ещё выполняется, о результате придёт [Событие]"
        status = {"task_done": "ГОТОВО", "task_failed": "НЕ УДАЛОСЬ", "task_cancelled": "ОТМЕНЕНО"}.get(ev.get("event"), "")
        return "%s: %s" % (status, ev.get("msg", ""))

    # ------------------------------------------------------------------ obtain: automatic supply chain
    async def stations(self):
        """Work blocks Altron remembers nearby: the plan uses them instead of building new ones. Mod machines he has
        seen (HBM press, assembly machine) count from farther away: he walks back to them."""
        res = await self.bot_call("stations", {"blocks": STATIONS, "radius": 96})
        found = set((res.get("found") or {}).keys()) if res.get("ok") else set()
        if self.knowledge is not None:
            res = await self.bot_call("stations", {"blocks": self.knowledge.MACHINE_BLOCKS, "radius": 320})
            found |= set((res.get("found") or {}).keys()) if res.get("ok") else set()
        return found

    async def start_obtain(self, args):
        item, count = str(args.get("item", "")), int(args.get("count", 1) or 1)
        if self.knowledge is None:
            return "ОШИБКА: справочник ещё загружается"
        inv = await self.bot_call("inventory_ids", {})
        have = inv.get("items") or {}
        root, steps, unresolved = await asyncio.to_thread(self.knowledge.acquire, item, count, have, await self.stations(),
                                                          bool(inv.get("creative")), self.memory.bad_recipes())
        if root is None:
            return "ОШИБКА: " + "; ".join(unresolved)
        if unresolved:
            return ("Сам сделать не могу: %s. Это нужно спросить у командира (ask_player) или сначала построить/найти машину."
                    % "; ".join(unresolved))
        self.macro_args = args
        self.macro_task = asyncio.create_task(self.obtain_macro(root, count))
        plan = "; ".join("%s %s" % (n, a.get("item") or ",".join(a.get("blocks", [])) or a.get("target", "")) for n, a in steps)
        return ("Начал obtain %dx %s. План: %s. Выполняю сам по шагам; о результате придёт [Событие]."
                % (count, self.knowledge.name(root), plan or "всё уже есть"))

    def learn_from(self, name, args, msg, ok):
        """Experience from every finished job: where the machines he found stand, what failed here and why,
        which recipe a machine refused (the planner goes another way next time)."""
        m = self.memory
        if ok and name == "explore":
            seen = set()
            for bid, x, y, z in re.findall(r"(?:нашёл|вижу):? ([a-z0-9_]+:[a-z0-9_/]+) в (-?\d+) (-?\d+) (-?\d+)", msg):
                if bid in seen:
                    continue
                seen.add(bid)
                title = ((self.knowledge.entries.get(bid, {}).get("ru") if self.knowledge else "") or bid).lower()
                m.set_place(title, (int(x), int(y), int(z)), self.state.get("dim", "minecraft:overworld"))
                self.log("(запомнил место) %s: %s %s %s" % (title, x, y, z))
        if not ok:
            blocks = args.get("blocks", [])
            what = args.get("item") or args.get("target") or (",".join(blocks) if isinstance(blocks, list) else str(blocks))
            m.learn("failure", "%s %s" % (name, what), "%s %s: %s" % (name, what, msg[:220]))
            if name == "machine" and args.get("recipe_id") and re.search(r"не делает|не принимает|некуда положить", msg):
                m.learn("bad_recipe", args["recipe_id"], "%s в %s: %s" % (args["recipe_id"], ",".join(args.get("machines", [])),
                                                                          msg[:200]), world=False)

    async def obtain_macro(self, root, count):
        k = self.knowledge
        t_start = time.time()
        done_log, result = [], ""
        failures = set()   # steps that already failed once
        start = (await self.bot_call("inventory_ids", {})).get("items") or {}
        goal = start.get(root, 0) + count   # stop as soon as this many are in the inventory
        try:
            for _ in range(4):
                inv = await self.bot_call("inventory_ids", {})
                have = inv.get("items") or {}
                left = goal - have.get(root, 0)
                if left <= 0:
                    result = "ГОТОВО: в инвентаре %dx %s" % (have.get(root, 0), k.name(root))
                    break
                _, steps, unresolved = await asyncio.to_thread(k.acquire, root, left, have, await self.stations(),
                                                               bool(inv.get("creative")), self.memory.bad_recipes())
                if unresolved:
                    result = "не смог сам: " + "; ".join(unresolved)
                    break
                if not steps:
                    result = "ГОТОВО: в инвентаре %dx %s" % (have.get(root, 0), k.name(root))
                    break
                failed = None
                for name, args in steps:
                    r = await self.start_task(name, args, 2400 if name == "explore" else 1200)   # searching buildings is slow
                    self.log("  [obtain] %s %s -> %s" % (name, json.dumps(args, ensure_ascii=False), r[:400]))
                    done_log.append("%s: %s" % (name, r[:120]))
                    self.learn_from(name, args, r, not r.startswith(("ОШИБКА", "НЕ УДАЛОСЬ", "ОТМЕНЕНО", "задача ещё")))
                    # finished ones that already lay in the machine are not his work: the order still wants its own
                    for n_found in re.findall(r"уже лежало готовое[^:]*: (\d+)x [^(]*\(%s\)" % re.escape(root), r):
                        goal += int(n_found)
                        self.log("  [obtain] в машине уже было %s шт. — это не считается, делаю свою" % n_found)
                    if name == "craft" and "не хватает" in r:
                        break  # mobs/ores gave less than expected: re-plan and gather more
                    if r.startswith(("ОШИБКА", "НЕ УДАЛОСЬ", "ОТМЕНЕНО", "задача ещё")):
                        failed = "%s %s: %s" % (name, args.get("item") or args.get("target") or ",".join(args.get("blocks", [])), r)
                        break
                if failed:
                    # a hiccup (window did not open, path blocked) is not the end: re-plan from what is in the
                    # inventory now and try again; stop on the same failure twice or on a hopeless one
                    key = failed.split(":")[0]
                    hopeless = re.search(r"нет рецепта|не могу|нужн|не установлен|ОТМЕНЕНО", failed)
                    if hopeless or key in failures:
                        result = "шаг не удался — " + failed
                        break
                    failures.add(key)
                    self.log("  [obtain] шаг не удался, пересчитываю план и пробую снова")
                    continue
                after = (await self.bot_call("inventory_ids", {})).get("items") or {}
                if after == have:
                    result = "за круг ничего не добыл (нет нужных руд/мобов рядом?). Осталось: " + \
                             "; ".join("%s %s" % (n, a.get("item") or a.get("target") or ",".join(a.get("blocks", []))) for n, a in steps)
                    break
                # otherwise re-plan: mobs and ores give uneven amounts
            else:
                result = "не удалось собрать всё за 4 круга"
        except asyncio.CancelledError:
            result = "остановлено"
        finally:
            self.macro_task = None
        # experience: how this job went, for the next time (the planner and the AI both see it)
        minutes = (time.time() - t_start) / 60
        if result.startswith("ГОТОВО"):
            made = [d.split(":")[0] + " " + re.sub(r"^ГОТОВО: ", "", d.split(": ", 1)[1])[:60] for d in done_log
                    if d.startswith(("machine", "craft", "explore"))]
            self.memory.learn("success", root, "Сделал %dx %s за %.0f мин: %s" % (count, k.name(root), minutes, "; ".join(made[-6:])))
        elif result != "остановлено":
            self.memory.learn("failure", root, "Не смог сделать %s (%.0f мин): %s" % (k.name(root), minutes, result[:220]))
        text = "[Событие] obtain %dx %s — %s.\nСделано: %s" % (count, k.name(root), result, " | ".join(done_log[-8:]) or "—")
        if result != "остановлено":
            await self.requests.put(("event", "", text + "\nСообщи командиру коротко; если это шаг большого задания — продолжай."))
        if self.queue and self.running is None:
            await self.run_queue()

    # ------------------------------------------------------------------ ready-made routines (done by code, not by the AI)
    async def routine(self, name, args):
        """A common job done step by step by the code: the small model only has to choose it, not to carry it out."""
        handlers = {"fetch": self.fetch, "study": self.study, "stash": self.stash, "load_machine": self.load_machine}
        try:
            result = await handlers[name](args)
        except asyncio.CancelledError:
            result = "остановлено"
        except Exception as e:
            result = "ошибка: %s" % e
        finally:
            self.macro_task = None
        self.log("  [%s] %s" % (name, result))
        if result != "остановлено":
            await self.requests.put(("event", "", "[Событие] %s — %s\nСообщи командиру коротко." % (ROUTINES[name], result)))
        if self.queue and self.running is None:
            await self.run_queue()

    async def study(self, args):
        """«Изучи производство»: look through the buildings around the commander, open every machine and store, learn
        what each one makes and what goes in, what lies where — all into long memory: the production map of this world."""
        k = self.knowledge
        radius = max(24, min(int(args.get("radius", 64) or 64), 128))
        owner, me = self.state.get("owner_pos"), self.state.get("pos")
        if owner and me and sum((a - b) ** 2 for a, b in zip(owner, me)) > 24 ** 2:
            await self.start_task("goto", {"x": int(owner[0]), "y": int(owner[1]), "z": int(owner[2])}, 300)   # his base
        center = [int(v) for v in (owner or self.state.get("pos") or [0, 0, 0])]
        known = (await self.bot_call("known_blocks", {"radius": radius, "limit": 60, "x": center[0], "y": center[1],
                                                      "z": center[2]})).get("blocks") or []
        if len(known) >= 15 and not args.get("walk"):
            r = "эти места я уже обходил (помню %d машин и хранилищ вокруг)" % len(known)
        else:
            r = await self.start_task("explore", {"radius": radius}, 1800)
            # back to the middle of the base: from there he walks to each machine (and its part of the world is loaded)
            await self.start_task("goto", {"x": center[0], "y": center[1], "z": center[2], "range": 3}, 300)
        self.log("  [study] обход: %s" % r[:300])
        found = []
        for _ in range(3):
            kb = await self.bot_call("known_blocks", {"radius": radius + 16, "limit": 400, "x": center[0], "y": center[1],
                                                      "z": center[2]})
            found = kb.get("blocks") or []
            if found:
                break
            self.log("  [study] список машин пуст (%s), пробую ещё раз" % str(kb)[:200])
            await asyncio.sleep(3)
        # a formed multiblock is many blocks of one kind: one of them is enough; conveyors, cables, wall blocks with a
        # block entity and the shells of big machines are not what is looked into
        store_re = re.compile(r"chest|barrel|crate|shulker|drawer|cabinet|storage|safe|locker|bin$")
        skip_re = re.compile(r"_part$|dummy|conveyor|connector|relay|cable|wire|pipe|conduit|duct|reinforced|disguise|sign|"
                             r"banner|bed$|door|lamp|light|fence|camera|scanner")
        kept = []
        for b in sorted(found, key=lambda b: b["dist"]):
            # gui unknown (that part of the world is not loaded around him now): judged by its kind
            useful = (b.get("gui", True) or store_re.search(b["id"]) or (k and k.block_recipe_types(b["id"]))) \
                and not skip_re.search(b["id"])
            if useful and not any(o["id"] == b["id"] and sum((p - q) ** 2 for p, q in zip(o["pos"], b["pos"])) <= 25 for o in kept):
                kept.append(b)
        stores = [b for b in kept if store_re.search(b["id"]) and not (k and k.block_recipe_types(b["id"]))]
        machines = [b for b in kept if b not in stores]
        todo = machines[:25] + stores[:15]
        self.log("  [study] вижу %d машин и %d хранилищ, осматриваю %d" % (len(machines), len(stores), len(todo)))
        lines, shut, names, far = [], 0, {}, 0
        for b in todo:
            x, y, z = b["pos"]
            res = await self.start_task("inspect", {"x": x, "y": y, "z": z}, 120)
            if "Я ЗАПЕРТ" in res or (far >= 3 and "дойти" in res):
                # shut in (a door only the owner opens) or no way further: say so instead of "25 did not open"
                where = "в %s" % " ".join(str(int(v)) for v in self.state.get("pos", [])) if self.state.get("pos") else ""
                await self.say("Командир, я застрял %s: выход закрыт, а ломать твоё я не буду. Открой мне, пожалуйста." % where)
                self.log("  [study] застрял: " + res[:200])
                return ("изучение прервано: застрял — %s. Успел осмотреть: %s"
                        % (res[:160], "; ".join(lines) or "ничего"))
            far = far + 1 if "дойти" in res else 0
            role = k.machine_role(b["id"]) if k else ""
            if res.startswith("ГОТОВО"):
                items = [re.sub(r"^- слот \d+: ", "", ln) for ln in res.splitlines() if ln.startswith("- слот")]
                inside = ", ".join(items[:14]) + (" и ещё %d" % (len(items) - 14) if len(items) > 14 else "") if items else "пусто"
                energy = re.search(r"энергия (\d+)/(\d+)", res)
                if energy:
                    inside += "; энергия %s/%s%s" % (energy.group(1), energy.group(2), " — НЕТ ПИТАНИЯ" if energy.group(1) == "0" else "")
            else:
                shut += 1
                inside = "не открылся: " + res[:100]
            kind = "хранилище" if b in stores else "машина"
            # what is inside first (it is what tells what the line really does), the reference role after it
            text = "%s «%s» (%s) в %d %d %d. Внутри: %s%s" % (kind, b["name"], b["id"], x, y, z, inside,
                                                             ". По справочнику " + role if role else "")
            self.memory.learn("production", "%s@%d,%d,%d" % (b["id"], x, y, z), text)
            self.log("  [study] " + text[:300])
            if kind == "машина":
                names[b["name"]] = names.get(b["name"], 0) + 1
                self.memory.set_place(b["name"].lower() + ("" if names[b["name"]] == 1 else " %d" % names[b["name"]]),
                                      (x, y, z), self.state.get("dim", "minecraft:overworld"))
            lines.append("%s (%d %d %d)%s" % (b["name"], x, y, z, " — " + role.split(";")[0] if role else ""))
        if not todo:
            return "обошёл округу, но машин и хранилищ не увидел. " + r[:200]
        return ("изучил %d машин и %d хранилищ%s; всё записал в память (что где стоит, что делает, что внутри). Главное: %s"
                % (min(len(machines), 25), min(len(stores), 15), ", не открылись %d" % shut if shut else "", "; ".join(lines[:8])))

    def where_answer(self, text):
        """«Куда положить X?»: the machines of the learned production map that take X, and the stores that already hold it."""
        k = self.knowledge
        if k is None or not WHERE_PUT_RE.search(text):
            return ""
        prod = [les for les in self.memory.lessons_here() if les["kind"] == "production"]
        if not prod:
            return ("\n[Подсказка] Производство в этом мире ты ещё не изучал. Скажи, что можешь изучить его (инструмент study) "
                    "и тогда ответишь точно.")
        item = k.find_id(WHERE_PUT_RE.sub(" ", text))
        if not item:
            return ""
        types = set(k.consumers(item))
        # machines that take it by their recipes, and machines that already hold it (an auto-crafter loaded with iron
        # ingots is where iron ingots go, even though it runs plain crafting recipes)
        machines = [les["text"] for les in prod if les["text"].startswith("машина")
                    and ("(%s)" % item in les["text"].split("Внутри:")[-1]
                         or types & set(k.block_recipe_types(les["key"].split("@")[0])))]
        machines.sort(key=lambda t: "(%s)" % item not in t.split("Внутри:")[-1])   # the ones already working with it first
        stores = [les["text"] for les in prod if les["text"].startswith("хранилище") and "(%s)" % item in les["text"]]
        out = "\n[Подсказка: куда класть %s — по изученному производству]" % k.name(item)
        out += "\nМашины, которые его берут: " + ("\n- " + "\n- ".join(m[:260] for m in machines[:4]) if machines else "среди изученных нет")
        if stores:
            out += "\nУже лежит в: " + "; ".join(s.split(". Внутри")[0] for s in stores[:3])
        return out

    def item_id(self, query):
        return (self.knowledge.find_id(query) if self.knowledge else None) or query

    async def fetch(self, args):
        """«Принеси X»: from the inventory, or from the chests where he saw it, then hand it to the commander."""
        item = self.item_id(str(args.get("item", "")))
        count = max(1, int(args.get("count", 1) or 1))
        who = str(args.get("player") or self.owner)
        have = lambda inv: inv.get(item, 0)   # noqa: E731
        inv = (await self.bot_call("inventory_ids", {})).get("items") or {}
        if have(inv) < count:
            seen = (await self.bot_call("recall_items", {"query": item, "limit": 5})).get("msg", "")
            spots = re.findall(r"(-?\d+) (-?\d+) (-?\d+) \(", seen)
            # then the chests around that he has not looked into yet: a person would look around and check them
            await self.bot_call("look_around", {})
            near = (await self.bot_call("find_block", {"block": "chest,barrel,trapped_chest", "radius": 32})).get("msg", "")
            spots += [s for s in re.findall(r"(-?\d+) (-?\d+) (-?\d+) \(", near) if s not in spots]
            for x, y, z in spots[:6]:
                opened = await self.start_task("use_block", {"x": int(x), "y": int(y), "z": int(z)}, 90)
                if not opened.startswith("ГОТОВО"):
                    continue
                await self.bot_call("container_take", {"item": item, "count": count - have(inv)})
                await self.bot_call("close_container", {})
                inv = (await self.bot_call("inventory_ids", {})).get("items") or {}
                if have(inv) >= count:
                    break
            if have(inv) == 0:
                return ("не нашёл %s ни у себя, ни в сундуках рядом. Можно добыть (obtain) или спросить командира, "
                        "где лежит" % (self.knowledge.name(item) if self.knowledge else item))
        n = min(count, have(inv))
        given = await self.start_task("give", {"item": item, "count": n, "player": who}, 120)
        return given + ("" if n >= count else " (нашёл только %d из %d)" % (n, count))

    async def stash(self, args):
        """«Сложи в сундук»: into the nearest chest or barrel he has seen; tools, weapons, armor and food stay with him."""
        item = str(args.get("item") or "all")
        await self.bot_call("look_around", {})
        found = (await self.bot_call("find_block", {"block": "chest,barrel", "radius": 48})).get("msg", "")
        spots = re.findall(r"(-?\d+) (-?\d+) (-?\d+) \(", found)
        if not spots:
            return "не видел рядом ни сундука, ни бочки — покажи, куда складывать, или поставлю свой (place_block chest)"
        for x, y, z in spots[:3]:
            opened = await self.start_task("use_block", {"x": int(x), "y": int(y), "z": int(z)}, 90)
            if not opened.startswith("ГОТОВО"):
                continue
            put = await self.bot_call("container_put", {"item": "all" if item in ("all", "всё", "все", "*") else self.item_id(item),
                                                         "keep_gear": True})
            await self.bot_call("close_container", {})
            if put.get("ok"):
                return "%s в сундук %s %s %s" % (put.get("msg", "положил"), x, y, z)
        return "не получилось открыть сундук рядом"

    async def load_machine(self, args):
        """«Перенеси/загрузи/насыпь X в машину»: заберёт ВЕСЬ материал из сундуков/бочек, где его видел, за один
        обход, дойдёт до названной машины и положит всё разом — один поход, а не по стаку за раз."""
        item = str(args.get("item") or "all")
        item_id = None if item in ("all", "всё", "все", "*") else self.item_id(item)
        machine_query = str(args.get("machine", "")).strip()
        machine_id = self.item_id(machine_query) if machine_query else None
        await self.bot_call("look_around", {})
        found = (await self.bot_call("find_block", {"block": "chest,barrel,trapped_chest", "radius": 48})).get("msg", "")
        spots = re.findall(r"(-?\d+) (-?\d+) (-?\d+) \(", found)
        for x, y, z in spots[:6]:
            opened = await self.start_task("use_block", {"x": int(x), "y": int(y), "z": int(z)}, 90)
            if not opened.startswith("ГОТОВО"):
                continue
            await self.bot_call("container_take", {"item": item_id or "all"})   # без count — весь предмет, не один стек
            await self.bot_call("close_container", {})
        inv = (await self.bot_call("inventory_ids", {})).get("items") or {}
        have_total = sum(n for i, n in inv.items() if item_id is None or i == item_id)
        if have_total <= 0:
            return ("не нашёл %s ни у себя, ни в сундуках рядом"
                    % (self.knowledge.name(item_id) if self.knowledge and item_id else item))
        where = (await self.bot_call("find_block", {"block": machine_id or machine_query, "radius": 96})).get("msg", "") \
            if (machine_id or machine_query) else ""
        spot = re.search(r"(-?\d+) (-?\d+) (-?\d+) \(", where)
        if not spot:
            return ("взял %d шт., но не вижу рядом %s — подойди к машине сам или скажи точные координаты"
                    % (have_total, ("«%s»" % machine_query) if machine_query else "нужную машину"))
        mx, my, mz = (int(v) for v in spot.groups())
        opened = await self.start_task("use_block", {"x": mx, "y": my, "z": mz}, 90)
        if not opened.startswith("ГОТОВО"):
            return "взял %d шт., но не смог открыть машину в %d %d %d: %s" % (have_total, mx, my, mz, opened)
        put = await self.bot_call("container_put", {"item": item_id or "all"})   # без count — весь предмет разом
        await self.bot_call("close_container", {})
        return "%s в машину %d %d %d" % (put.get("msg", "положил"), mx, my, mz)

    async def run_queue(self):
        """Start queued tasks one after another; returns when one is running in the background or the queue is empty."""
        while self.queue and self.running is None and (self.macro_task is None or self.macro_task.done()):
            name, args = self.queue.pop(0)
            if name == "obtain":
                self.log("  -> (из очереди) obtain: " + (await self.start_obtain(args))[:200])
                continue
            result = await self.start_task(name, args, 0)
            if self.running is None:  # finished instantly or failed to start
                self.queue_log.append("%s: %s" % (name, result))
                if result.startswith("ОШИБКА"):
                    break
            else:
                self.log("  -> (из очереди) %s %s: %s" % (name, json.dumps(args, ensure_ascii=False), result[:200]))

    # ------------------------------------------------------------------ connections
    async def on_client(self, reader, writer):
        role = None
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode("utf-8"))
                except Exception:
                    continue
                t = msg.get("type")
                if t == "hello":
                    role = msg.get("role")
                    if role == "host":
                        self.host = writer
                        self.log("Игра игрока подключена.")
                    elif role == "bot":
                        self.bot = writer
                        self.log("Тело Альтрона подключено.")
                        self.send(writer, {"type": "config", "owner": self.owner, "world": self.world_name})
                    continue
                if role == "host":
                    await self.on_host(msg)
                elif role == "bot":
                    await self.on_bot(msg)
                elif role == "console":
                    # a local tool (brain/console.py): speak as the commander, or ask the body something
                    if t == "say":
                        asyncio.create_task(self.handle_phrase(self.owner or msg.get("who", "командир"), str(msg.get("text", ""))))
                        self.send(writer, {"type": "reply", "result": "сказано"})
                    elif t == "bot":
                        res = await self.bot_call(str(msg.get("name", "status")), msg.get("args") or {})
                        self.send(writer, {"type": "reply", "result": res})
                    elif t == "probe":   # the host's server: how the world really is (see HostSetup.probe)
                        res = await self.probe(**(msg.get("args") or {}))
                        self.send(writer, {"type": "reply", "result": res})
                    elif t == "task":   # start a task and wait for its result
                        res = await self.start_task(str(msg.get("name", "")), msg.get("args") or {}, int(msg.get("wait", 120)))
                        self.send(writer, {"type": "reply", "result": res})
                    elif t == "state":
                        self.send(writer, {"type": "reply", "result": {"state": self.state, "running": self.running,
                                                                       "macro": self.macro_task is not None}})
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            if role == "host" and self.host is writer:
                self.host = None
                self.log("Игра игрока отключилась.")
            if role == "bot" and self.bot is writer:
                self.bot = None
                self.state = {}
                self.log("Тело Альтрона отключилось.")
            try:
                writer.close()
            except Exception:
                pass

    async def game_link(self, address):
        """Everything of Altron runs on this (second) PC and the commander's game on another one: connect to that game
        (its mod waits on port 47810 for a brain with the key) and keep the link, reconnecting when it drops."""
        # only the IP matters: the game's door for the brain is always 47810, and the world's own port comes by itself
        # with /altron — a port typed after the IP (say the world's 25566) is ignored
        host = address.partition(":")[0].strip()
        port = int(self.cfg.get("game_port", 47810))
        warned = False
        while True:
            try:
                reader, writer = await asyncio.open_connection(host, port, limit=LINE_LIMIT)
                writer.write((json.dumps({"type": "auth", "key": self.cfg.get("game_key", "")}) + "\n").encode("utf-8"))
                await writer.drain()
                self.log("Подключился к игре на %s." % host)
                warned = False
                await self.on_client(reader, writer)
                self.log("Связь с игрой на %s прервалась — переподключаюсь..." % host)
            except OSError as e:
                if not warned:
                    warned = True
                    self.log("Не могу подключиться к игре на %s:%d (%s). Нужно: игра запущена (с новой версией мода), "
                             "Radmin VPN подключён, брандмауэр игрового ПК пускает Java. Пробую снова каждые 3 с..."
                             % (host, port, e))
            await asyncio.sleep(3)

    async def on_host(self, msg):
        t = msg.get("type")
        if t == "voice":
            self.on_voice(msg)
        elif t == "host_ready":
            self.owner = msg.get("owner", self.owner)
            self.world_name = msg.get("world", self.world_name)
            if self.bot is not None:
                self.send(self.bot, {"type": "config", "owner": self.owner, "world": self.world_name})
            if self.bot_proc is not None and self.bot_proc.poll() is None:
                self.send(self.host, {"type": "notify", "text": "Альтрон уже запущен."})
                return
            try:
                self.bot_server = int(msg.get("port", 25566))
                if self.cfg.get("game_pc"):
                    # the commander's world is on the gaming PC: Altron's body (here) joins it over the network
                    self.bot_server = "%s:%d" % (self.cfg["game_pc"].partition(":")[0], self.bot_server)
                self.remote = False
                self.cfg["bot_voice"] = False   # in his own world the host's plugin carries the voices
                self.bot_proc = launch_bot(self.cfg, self.bot_server, self.log, lite=self.bot_lite)
            except Exception as e:
                self.log("Не удалось запустить клиент бота: %s" % e)
                self.send(self.host, {"type": "notify", "text": "Не удалось запустить клиент Альтрона: %s" % e})
        elif t == "bot_stop":
            self.stop_bot()
        elif t == "setup_done":
            self.setup_result = msg
            self.log("Мир подготовлен: поставлено блоков %s" % msg.get("placed"))
        elif t == "probe_result":
            fut = self.host_waiters.pop(msg.get("rid"), None)
            if fut and not fut.done():
                fut.set_result(msg)
        elif t == "mic_failed":
            self.mic_failed = msg.get("msg", "ошибка")
            self.log("Микрофон демо не смог говорить в голосовой чат: %s" % self.mic_failed)
        elif t == "text":
            who = msg.get("from", "игрок")
            self.owner = self.owner or who
            self.windows[who] = time.time() + self.cfg.get("conversation_window_sec", 20)
            asyncio.create_task(self.handle_phrase(who, msg.get("text", "")))

    async def on_bot(self, msg):
        t = msg.get("type")
        if t == "voice":
            self.on_voice(msg)   # someone else's server: a player near Altron spoke
            return
        if t == "mic_failed":
            if not self.mic_warned:
                self.mic_warned = True
                self.log("Альтрон не может говорить в голосовой чат (%s) — буду отвечать в чат игры." % msg.get("msg", ""))
                self.cfg["chat_replies"] = True
            return
        if t == "result":
            fut = self.pending.pop(msg.get("id"), None)
            if fut and not fut.done():
                fut.set_result(msg)
        elif t == "state":
            self.state = msg
        elif t == "event":
            # never block the reader: handlers may wait for further messages from the bot
            asyncio.create_task(self.on_event(msg))
        elif t == "chat":
            text = msg.get("text", "")
            sender = msg.get("from", "")
            if text in self.director_lines:
                return
            explicit = text.startswith("!")
            if explicit:
                self.windows[sender] = time.time() + self.cfg.get("conversation_window_sec", 20)
                text = text[1:]
            # typed chat is heard exactly, but talk to other players is not meant for Altron either
            if self.addressed(sender, text) and (explicit or self.worth_hearing(text, 0.0, sender)):
                self.log("Чат %s: %s" % (sender, text))
                asyncio.create_task(self.handle_phrase(sender, text))

    async def on_event(self, msg):
        ev = msg.get("event")
        if ev in ("task_done", "task_failed", "task_cancelled"):
            tid = msg.get("task_id")
            task_args = self.running_args if self.running is not None and self.running[0] == tid else {}
            if self.running is not None and self.running[0] == tid:
                self.running = None
            fut = self.task_waiters.pop(tid, None)
            if fut and not fut.done():
                fut.set_result(msg)
                return
            if tid not in self.agent_tasks or ev == "task_cancelled":
                return
            self.agent_tasks.discard(tid)
            task = msg.get("task", "")
            if ev != "task_cancelled":
                self.learn_from(task, task_args or {}, msg.get("msg", ""), ev == "task_done")
            status = "завершена" if ev == "task_done" else "НЕ удалась"
            self.queue_log.append("%s %s: %s" % (task, status, msg.get("msg", "")))
            if ev == "task_done" and self.queue:
                await self.run_queue()          # quietly continue with the next queued task
                if self.running is not None:
                    return
            if ev == "task_failed" and self.queue:
                self.queue_log.append("очередь отменена: " + ", ".join(n for n, _ in self.queue))
                self.queue.clear()
            report = self.queue_log[:]
            self.queue_log.clear()
            if ev == "task_done" and len(report) == 1 and task not in NOTIFY_DONE:
                return
            await self.requests.put(("event", "", "[Событие] Результаты:\n- %s\nЕсли цель ещё не достигнута — молча вызови "
                                     "следующий инструмент. Говори, только если цель достигнута или нужна помощь командира."
                                     % "\n- ".join(report)))
        elif ev == "connect_failed":
            reason = msg.get("msg", "")
            self.log("Сервер не пустил Альтрона: " + reason)
            if re.search(r"verify|провер|licen|лиценз|authent|session|сесси", reason, re.I):
                self.log("Сервер проверяет лицензии, а у Альтрона офлайн-аккаунт. Хозяин мира должен открыть его командой "
                         "/altron lan (или на сервере online-mode=false в server.properties).")
            elif re.search(r"refused|отказ|timed out|время ожидания|unknown host|no route|unreachable", reason, re.I) and self.remote:
                self.log("Сервер %s не отвечает: проверь, что Radmin VPN подключён к той же сети, мир открыт "
                         "и адрес с портом верные." % server_address(self.bot_server))
            if self.bot_lite and re.search(r"mod|мод|channel|канал|registr|реестр|missing|отсутств|mismatch", reason, re.I):
                # the lite client lacks something this server wants: start again with every mod of the pack
                self.bot_lite = False
                self.log("Перезапускаю клиент Альтрона со всеми модами сборки.")
                self.stop_bot()
                await asyncio.sleep(3)
                self.bot_proc = launch_bot(self.cfg, self.bot_server, self.log, lite=False)
        elif ev == "need":
            await self.requests.put(("event", "", "[Событие] Альтрону нужно: %s Коротко попроси командира через ask_player."
                                     % msg.get("msg", "")))
        elif ev == "joined":
            self.joined = True
            self.memory.world = msg.get("world", "")
            self.log(msg.get("msg", ""))
            await self.say("Альтрон на связи. Жду приказов.")
        elif ev == "death":
            pos = self.state.get("pos")
            dim = self.state.get("dim", "")
            self.memory.log("событие", "Альтрон погиб" + (" в %d %d %d" % tuple(int(v) for v in pos) if pos else ""))
            await self.say("Меня уничтожили. Перезагружаюсь.")
            if pos:
                asyncio.create_task(self.recover_death_drop([round(v) for v in pos], dim))
        elif ev == "low_health":
            await self.say("Внимание, мои системы повреждены, здоровья мало.")

    async def recover_death_drop(self, pos, dim):
        """After dying, go back for the dropped items myself, like a player, before they despawn."""
        await asyncio.sleep(3)   # the client waits ~30 ticks before it closes the death screen and respawns
        if not self.joined:
            return
        if dim and self.state.get("dim") and self.state.get("dim") != dim:
            await self.requests.put(("event", "", "[Событие] Погиб в измерении %s на %d %d %d, а возродился в другом "
                                     "измерении — сам туда не дойти. Сообщи командиру, что вещи остались там."
                                     % (dim, *pos)))
            return
        await self.requests.put(("event", "", "[Событие] Только что погиб и возродился. Вещи выпали на месте смерти "
                                 "%d %d %d. Молча дойди туда (goto) и подбери их (collect_items, radius 4-6), пока они "
                                 "не пропали — обычно 5 минут с момента смерти, часть времени уже прошла. Если по пути "
                                 "явно опасно (лава, враги) или на месте вещей уже нет — сообщи командиру и не рискуй."
                                 % tuple(pos)))

    # ------------------------------------------------------------------ voice
    def on_voice(self, msg):
        maxd = self.cfg.get("hear_distance", 0)
        if maxd and 0 <= msg.get("dist", -1) and msg["dist"] > maxd:
            return
        pcm = np.frombuffer(base64.b64decode(msg["pcm"]), dtype="<i2")
        v = self.voice.setdefault(msg["uuid"], {"name": msg.get("name", ""), "chunks": [], "last": 0, "voiced": 0})
        v["chunks"].append(pcm)
        now = time.time()
        v["last"] = now
        if len(pcm) and float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2))) > VOICED_RMS:
            v["voiced"] = now

    async def voice_loop(self):
        while True:
            await asyncio.sleep(0.05)
            now = time.time()
            for uid, v in list(self.voice.items()):
                # the phrase is over when the packets stop, or when only quiet packets come: the voice chat keeps
                # sending ~0.5 s of near-silence after speech, no need to wait for it
                ended = now - v["last"] > 0.3 or (v["voiced"] and now - v["voiced"] > SILENCE_END)
                if v["chunks"] and ended:
                    audio = np.concatenate(v["chunks"])
                    v["chunks"] = []
                    v["voiced"] = 0
                    if len(audio) < 48000 * 0.35 or self.stt is None:
                        continue
                    if not loud_enough(audio):
                        continue   # background noise opened the voice chat: Whisper would only make words up
                    t0 = time.time()
                    text, conf = await asyncio.to_thread(self.stt.transcribe_ex, audio)
                    self.keep_voice(audio, v["name"], text, conf)
                    if text:
                        self.log("%s сказал (распознал за %.1f с, уверенность %.2f): %s" % (
                            v["name"], time.time() - t0, conf, text))
                        if not self.worth_hearing(text, conf, v["name"]):
                            self.log("  (это не мне или не разобрал — пропускаю)")
                            continue
                        if self.addressed(v["name"], text):
                            self.heard += 1
                        await self.handle_phrase(v["name"], text)

    def name_required(self, speaker):
        """The commander is heard without "Альтрон" (config wake_word_required=false); other players say his name,
        or Altron would butt into their talk with the commander."""
        if self.cfg.get("wake_word_required", False):
            return True
        return bool(self.owner) and speaker.lower() != self.owner.lower()

    def worth_hearing(self, text, conf, speaker=""):
        """A phrase with Altron's name always counts. From the commander (no name needed) — everything but noise and
        bare exclamations ("блин", "ммм"); a short "да / вот / ага" only while they are talking (he spoke recently).
        From others without the name — only a clear order, "stop" or question inside the talk window."""
        if has_wake_word(text, self.cfg["wake_words"]):
            return True
        if conf < STT_MIN_CONF:
            return False
        low = text.lower().replace("ё", "е")
        words = re.findall(r"[а-яa-z0-9]+", low)
        if not words:
            return False
        if STOP_RE.search(low):
            return True
        pending = self.last_question is not None and time.time() - self.last_question[1] < 120
        talking = pending or time.time() < self.windows.get(speaker, 0)
        if not self.name_required(speaker):
            if all(w in INTERJECTIONS for w in words):
                return False
            if len(words) <= 2 and set(words) & SHORT_ANSWERS:
                return talking
            return True
        if len(words) <= 2 and set(words) & SHORT_ANSWERS:
            return pending
        if ACTION_WORDS.search(low):
            return len(words) >= 2 or conf >= STT_SURE
        return is_question(text) and len(words) >= 3

    def keep_voice(self, audio, who, text, conf):
        """The last phrases as sound files (brain/logs/voice): to tune speech recognition on real voices."""
        if not self.cfg.get("stt_keep_audio", True):
            return
        try:
            import wave
            folder = BRAIN_DIR / "logs" / "voice"
            folder.mkdir(parents=True, exist_ok=True)
            name = time.strftime("%Y%m%d_%H%M%S")
            pcm16 = np.convolve(audio.astype(np.float32), np.ones(3) / 3, mode="same")[::3].astype("<i2")
            with wave.open(str(folder / (name + ".wav")), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(pcm16.tobytes())
            with open(folder / "index.txt", "a", encoding="utf-8") as f:
                f.write("%s.wav\t%s\t%.2f\t%s\n" % (name, who, conf, text))
            for old in sorted(folder.glob("*.wav"))[:-40]:
                old.unlink()
        except Exception as e:
            self.log("не сохранил запись голоса: %s" % e)

    def note_question(self, question):
        """Allow a question to the player only if none is pending (asked < 2 min ago and not answered)."""
        now = time.time()
        if self.last_question is not None and now - self.last_question[1] < 120:
            return False
        self.last_question = (question, now)
        return True

    def addressed(self, speaker, text):
        if has_wake_word(text, self.cfg["wake_words"]):
            self.windows[speaker] = time.time() + self.cfg.get("conversation_window_sec", 20)
            return True
        if not self.name_required(speaker):
            return True   # the commander does not have to call him by name
        return time.time() < self.windows.get(speaker, 0)

    async def handle_phrase(self, speaker, text):
        if not self.addressed(speaker, text):
            return
        if not self.owner:
            self.owner = speaker
        if self.bot is not None:
            # like a person who hears his name: turn to the one speaking (when not busy with a job)
            self.send(self.bot, {"type": "cmd", "id": 0, "name": "attention", "args": {"player": speaker}})
        self.memory.log(speaker, text)
        self.last_question = None  # the player spoke: any pending question is answered
        # a plain "стоп / стой / хватит"; "стой тут и охраняй меня" is an order with a stop word in it, not a stop
        pure_stop = STOP_RE.search(text) and not ACTION_WORDS.search(STOP_RE.sub(" ", text))
        if pure_stop and len(re.findall(r"\w+", text)) <= 6:
            self.agent.cancelled = True
            while not self.requests.empty():
                self.requests.get_nowait()
            if self.bot is not None:
                await self.run_tool("stop", {}, 0)
                self.send(self.bot, {"type": "cmd", "id": 0, "name": "close_container", "args": {}})
            # the AI must not pick the cancelled job up again from the conversation history
            self.agent.note("[Командир сказал: «%s». Всё остановлено. Прежнее задание ОТМЕНЕНО — не продолжай его, "
                            "пока командир снова не попросит.]" % text)
            await self.say("Остановился.")
            return
        # really talking to him: his name, or a conversation with him going on. The commander's microphone also carries
        # what he says to others in the room ("сюда мы берём") — no instant "Принял" and no rules from that
        talking = has_wake_word(text, self.cfg["wake_words"]) or time.time() < self.windows.get(speaker, 0)
        self.windows[speaker] = time.time() + self.cfg.get("conversation_window_sec", 20)
        if talking and speaker == self.owner and self.memory.rule_from(text):
            self.log("(запомнил правило командира) " + text)
        acked = False
        if talking and ACTION_WORDS.search(text) and not is_question(text) and self.tts is not None:
            await self.acknowledge()   # an order: answer at once, the AI will act (quietly) right after
            acked = True
        await self.requests.put(("user", speaker, text, acked))

    # ------------------------------------------------------------------ agent worker
    async def agent_loop(self):
        while True:
            item = await self.requests.get()
            kind, speaker, text = item[:3]
            acked = len(item) > 3 and item[3]
            if kind == "user":
                targets = []
                prompt ="[%s говорит]: %s\n[Состояние] %s" % (speaker, text, self.state_text())
                if self.bot is not None:
                    # what is around him right now (players, mobs, vehicles, turrets with their ids): no guessing
                    near = await self.bot_call("nearby", {"radius": 16})
                    if near.get("ok") and not near.get("msg", "").startswith("Рядом никого"):
                        prompt += "\n[Рядом]\n" + near_text(near["msg"])
                if self.knowledge is not None:
                    ctx = await asyncio.to_thread(self.knowledge.context_for, text)
                    if ctx:
                        prompt += "\n[Справочник по сборке]\n" + ctx
                    targets = await asyncio.to_thread(self.knowledge.find_targets, text)
                    where = self.where_answer(text)
                    if re.search(r"изуч|осмотр|обойд|разбер", text, re.I) and \
                            re.search(r"производств|баз|завод|цех|здани|машин|механизм|фабрик", text, re.I):
                        prompt += ("\n[Подсказка] Для этого вызови study — он сам обойдёт здания, откроет все машины "
                                   "и хранилища и запомнит, что куда класть.")
                    if where:
                        prompt += where
                        targets = []
                    elif is_recipe_question(text):
                        # "what do I need for X?": the answer, not a start of making it
                        prompt += await self.recipe_answer(text, targets)
                        targets = []
                    if targets:
                        # small models tend to start mining by hand; point them straight at the supply chain
                        prompt += ("\n[Подсказка] Для этой просьбы вызови obtain: %s. Не копай и не крафти вручную — "
                                   "obtain сам сделает всю цепочку с нуля." % ", ".join(
                                       "item=%s (%s)" % (i, n) for i, n in targets))
                # the last half hour of this session is still in the model's history; recall only what is older
                mem = self.memory.context_for(text, session_start=max(self.memory.started, time.time() - 1800),
                                              ids=[i for i, _ in targets])
                if not self.resumed:
                    self.resumed = True
                    last = self.memory.last_conversation()
                    if last:
                        mem += "\nПоследний разговор перед перезапуском:\n" + last
                if is_question(text) or re.search(r"\bгде\b", text.lower()):
                    # a question: recall what was seen before answering, like a person would
                    seen = await self.recall_world(text)
                    if seen:
                        mem += "\nИз увиденного тобой:\n" + seen
                if mem.strip():
                    prompt += "\n[Память]\n" + mem.strip()
            else:
                self.memory.log("событие", text.replace("[Событие]", "").split("\nЕсли цель")[0].split("\nСообщи")[0][:500])
                prompt = "%s\n[Состояние] %s" % (text, self.state_text())
            self.busy = True
            # he thinks before every answer to the commander (a few seconds); on real "how/why" questions he says so
            think = kind == "user" and self.cfg.get("llm_think_user", True)
            if think and needs_thinking(text) and not acked:
                await self.say("Секунду, подумаю.")
            try:
                order = kind == "user" and bool(ACTION_WORDS.search(text)) and not is_question(text)
                await self.agent.run(prompt, kind, question=(kind == "user" and is_question(text)), acked=acked,
                                     think=think, order=order)
            except Exception as e:
                self.log("Ошибка агента: %r" % e)
            finally:
                self.busy = False
            if speaker:
                self.windows[speaker] = time.time() + self.cfg.get("conversation_window_sec", 20)

    def stop_bot(self):
        if self.bot_proc is not None and self.bot_proc.poll() is None:
            self.log("Выключаю клиент Альтрона.")
            self.bot_proc.terminate()
        self.bot_proc = None

    def console_thread(self):
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            if line in ("/quit", "/exit"):
                self.stop_bot()
                self.loop.call_soon_threadsafe(self.loop.stop)
                return
            who = self.owner or "консоль"
            self.windows[who] = time.time() + 60
            asyncio.run_coroutine_threadsafe(self.handle_phrase(who, line), self.loop)


def llm_is_remote(cfg):
    return cfg.get("llm_host", "127.0.0.1") not in ("127.0.0.1", "localhost", "")


def llm_headers(cfg):
    return {"Authorization": "Bearer " + cfg["llm_api_key"]} if cfg.get("llm_api_key") else {}


def start_llm(cfg, log):
    if llm_is_remote(cfg):
        # the neural network runs on the second PC (ai_server kit): nothing to start here
        log("Нейросеть на другом ПК: %s:%d" % (cfg["llm_host"], cfg["llm_port"]))
        return None
    exe = BRAIN_DIR.parent / "tools" / "llama" / "llama-server.exe"
    model = rel(cfg["llm_model"])
    url = "http://127.0.0.1:%d/health" % cfg["llm_port"]
    try:
        if httpx.get(url, timeout=1, trust_env=False).status_code == 200:
            log("ИИ-сервер уже запущен.")
            return None
    except Exception:
        pass
    (BRAIN_DIR / "logs").mkdir(exist_ok=True)
    logf = open(BRAIN_DIR / "logs" / "llama-server.log", "w", encoding="utf-8", errors="replace")
    args = [str(exe), "-m", str(model), "-c", str(cfg["llm_context"]),
            "--host", "127.0.0.1", "--port", str(cfg["llm_port"]), "--jinja", "-np", "1",
            # the prompt cache in RAM is 8 GB by default: 1 GB is plenty for one Altron
            "-cram", str(cfg.get("llm_cache_ram_mb", 1024))]
    if cfg.get("llm_fit"):
        # a model bigger than the video card (the 35B on the second PC): llama.cpp spreads it over the card and the RAM
        # itself, leaving room on the card for speech recognition and Altron's own game window
        args += ["--fit", "on", "--fit-ctx", str(cfg.get("llm_fit_ctx", 24576)),
                 "--fit-target", str(cfg.get("llm_fit_margin_mb", 2048))]
    else:
        args += ["-ngl", str(cfg["llm_gpu_layers"])]
        if int(cfg["llm_gpu_layers"]) >= 99:
            # the whole model lives on the GPU: read it once instead of keeping the 5.5 GB file mapped in RAM
            args += ["-lm", "none"]
    mmproj = rel(cfg.get("llm_mmproj", "")) if cfg.get("llm_mmproj") else None
    if mmproj is not None and mmproj.exists():
        args += ["--mmproj", str(mmproj)]  # vision: Altron can look at the screen
    log("Запускаю ИИ (%s)..." % model.name)
    return subprocess.Popen(args, stdout=logf, stderr=subprocess.STDOUT)


async def wait_llm(cfg, log):
    url = "http://%s:%d/health" % (cfg.get("llm_host", "127.0.0.1"), cfg["llm_port"])
    remote = llm_is_remote(cfg)
    # the AI server (here or on the second PC): never through a Windows proxy
    async with httpx.AsyncClient(trust_env=False, headers=llm_headers(cfg)) as c:
        for i in range(600 if remote else 300):
            try:
                r = await c.get(url, timeout=3)
                if r.status_code == 200:
                    log("ИИ готов%s." % (" (на другом ПК)" if remote else ""))
                    return True
                if r.status_code == 401:
                    log("Второй ПК не пустил: неверный пароль. api_key.txt в папке ai_server должен совпадать с llm_api_key.")
            except Exception:
                if remote and i in (5, 60, 300):
                    log("Жду нейросеть на %s:%d — на втором ПК должен работать 2_запустить_ИИ.bat, а Windows там "
                        "должна разрешить доступ (брандмауэр)." % (cfg["llm_host"], cfg["llm_port"]))
            await asyncio.sleep(1)
    log("ИИ не отвечает, смотри brain/logs/llama-server.log" if not remote else
        "Нейросеть на другом ПК так и не ответила: проверь адрес, Radmin VPN и окно 2_запустить_ИИ.bat.")
    return False


LAST_LAUNCH = BRAIN_DIR / "last_launch.json"
# One JSON message per line; a screenshot for the eyes is a few hundred KB of base64 (asyncio's default limit is 64 KB,
# above it the connection with the body was dropped)
LINE_LIMIT = 64 * 1024 * 1024


async def listen(hub, port):
    """The brain's TCP port: the commander's game and Altron's body connect to it."""
    return await asyncio.start_server(hub.on_client, "127.0.0.1", port, limit=LINE_LIMIT)


def choose_launch(cfg, argv):
    """Launch mode and where Altron plays, asked in the brain's window (Enter keeps the last choice).
    Command line: --profile eco|balanced|max  --server IP:port (empty = own world)  --owner Nick"""
    import argparse
    ap = argparse.ArgumentParser(description="Мозг Альтрона")
    ap.add_argument("--profile", help="eco | balanced | max")
    ap.add_argument("--server", help="адрес сервера Radmin VPN IP:порт; пусто — свой мир через /altron")
    ap.add_argument("--owner", help="ник командира в игре")
    ap.add_argument("--ai", help="адрес второго ПК с нейросетью (ai_server); пусто — на этом ПК")
    ap.add_argument("--pack", help="другая сборка (имя папки в .minecraft\\versions): Альтрон берёт её моды")
    args = ap.parse_args(argv)
    if args.pack:
        # another modpack of the same Minecraft/Forge: his body takes that pack's mods (plus Altron's own and Baritone)
        # from its own folder, so the Total War setup is not touched; the encyclopedia is read from that pack
        if not (rel(cfg["minecraft_dir"]) / "versions" / args.pack / (args.pack + ".json")).exists():
            raise SystemExit("Нет такой сборки: %s" % args.pack)
        cfg["pack_version"] = args.pack
        cfg["bot_dir"] = "../bot_" + (re.sub(r"[^A-Za-z0-9]+", "_", args.pack).strip("_")[:24] or "other")
        cfg["other_pack"] = True
    try:
        last = json.loads(LAST_LAUNCH.read_text(encoding="utf-8"))
    except Exception:
        last = {}
    ask = sys.stdin is not None and sys.stdin.isatty()

    if cfg.get("game_pc_mode"):
        # Altron runs on the second PC (the ai_server kit): the only question is where the commander's game is
        game = args.server
        if game is None and ask:
            print("Где твоя игра? Введи адрес игрового ПК: IP из Radmin VPN (26.x.x.x) или домашней сети (192.168.x.x).")
            if last.get("game"):
                print("  «=» или Enter — прошлый: %s" % last["game"])
            answer = input("Адрес игрового ПК: ").strip()
            game = last.get("game", "") if answer in ("", "=") else answer
        game = (game or "").strip()
        # the mode only sets Altron's body (a light client: this PC also carries the big model); the model's own
        # settings from config.json stay
        keep = {k: cfg[k] for k in ("llm_context", "llm_cache_ram_mb", "history_chars") if k in cfg}
        profile = apply_profile(cfg, cfg.get("profile", "eco"))
        cfg.update(keep)
        if game:
            cfg["game_pc"] = game
        try:
            LAST_LAUNCH.write_text(json.dumps(dict(last, game=game or last.get("game", "")), ensure_ascii=False, indent=1),
                                   encoding="utf-8")
        except Exception:
            pass
        return profile, "", ""

    profile = args.profile or last.get("profile") or cfg.get("profile", "balanced")
    if ask and not args.profile:
        print("Режим работы Альтрона (меняется только его клиент и ИИ, твоя игра не трогается):")
        for i, (key, p) in enumerate(PROFILES.items(), 1):
            print("  %d — %s%s\n      %s" % (i, p["title"], "  <- прошлый" if key == profile else "", p["about"]))
        answer = input("Режим [1/2/3, Enter — прошлый]: ").strip()
        profile = answer or profile
    profile = apply_profile(cfg, profile)

    server = args.server
    if server is None and ask:
        print("\nКуда пустить Альтрона?")
        print("  Enter — в свой мир: зайди в него и напиши в чате /altron")
        print("  или адрес сервера из Radmin VPN: IP:порт, например 26.12.34.56:25565")
        print("  (хозяин мира открывает его для Альтрона командой /altron lan и называет порт)")
        if last.get("server"):
            print("  «=» — прошлый сервер %s" % last["server"])
        answer = input("Адрес: ").strip()
        server = last.get("server") if answer == "=" else answer
    server = server_address(server) if server else ""

    owner = args.owner or last.get("owner") or cfg.get("owner") or ""
    if server and ask and not args.owner:
        answer = input("Твой ник в игре (командир)%s: " % (" [Enter — %s]" % owner if owner else "")).strip()
        owner = answer or owner

    # the neural network on another PC (advanced: --ai IP); normally it runs here, or everything runs on the second PC
    ai = (args.ai or "").strip()
    if ai:
        host, _, port = ai.partition(":")
        cfg["llm_host"] = host
        if port.isdigit():
            cfg["llm_port"] = int(port)
        # the big model on the second PC has a 32k context: it may keep a longer conversation
        cfg["history_chars"] = max(int(cfg.get("history_chars", 0)), 54000)
    try:
        LAST_LAUNCH.write_text(json.dumps({"profile": profile, "server": server or last.get("server", ""),
                                           "owner": owner, "ai": ai or last.get("ai", "")},
                                          ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass
    return profile, server, owner


async def main():
    cfg = json.loads((BRAIN_DIR / "config.json").read_text(encoding="utf-8"))
    profile, server, owner = choose_launch(cfg, sys.argv[1:])
    hub = Hub(cfg)
    hub.loop = asyncio.get_running_loop()
    print("=" * 60)
    print(" АЛЬТРОН — ИИ-напарник для Minecraft. Режим: %s" % PROFILES[profile]["title"])
    if llm_is_remote(cfg):
        print(" Нейросеть на втором ПК: %s:%d" % (cfg["llm_host"], cfg["llm_port"]))
    if cfg.get("game_pc"):
        print(" Альтрон работает на этом ПК, игра — на %s." % cfg["game_pc"])
        print(" На игровом ПК: зайди в свой мир и напиши в чате /altron — Альтрон придёт сам.")
    elif server:
        print(" Альтрон заходит на сервер %s сам (1-2 минуты)." % server)
        print(" Командир: %s. Говори в Voice Chat рядом с ним: «Альтрон, иди за мной»" % (owner or "первый, кто позовёт"))
    else:
        print(" 1) Запусти сборку в TLauncher и зайди в свой мир")
        print(" 2) Напиши в чате игры: /altron")
        print(" 3) Говори в Voice Chat: «Альтрон, иди за мной»")
    print(" Здесь можно печатать команды текстом. /quit — выход.")
    print("=" * 60)
    if cfg.get("other_pack"):
        hub.log("Сборка: %s (моды Альтрона — в его папке, сама сборка не меняется)." % cfg["pack_version"])
    else:
        install_new_mod(cfg, hub.log)
    llm_proc = start_llm(cfg, hub.log)

    def load_models():
        from knowledge import Knowledge
        from speech import STT, TTS
        hub.knowledge = Knowledge.load(cfg, hub.log)
        hub.log("Справочник по сборке готов: %d рецептов." % len(hub.knowledge.recipes))
        hub.log("Загружаю распознавание речи и голос...")
        hub.tts = TTS(cfg)
        hub.stt = STT(cfg, hub.log)
        hub.log("Слух и голос готовы (распознавание речи: %s)." % hub.stt.device)

    loader = threading.Thread(target=load_models, daemon=True)
    loader.start()
    listener = await listen(hub, cfg["brain_port"])
    hub.log("Мозг слушает порт %d." % cfg["brain_port"])
    threading.Thread(target=hub.console_thread, daemon=True).start()
    if cfg.get("game_pc"):
        asyncio.create_task(hub.game_link(cfg["game_pc"]))   # the commander's game is on the other PC
    if server:
        # someone else's server (Radmin VPN): no /altron there, Altron goes in by himself and uses his own voice chat
        hub.remote = True
        hub.bot_server = server
        hub.owner = owner
        hub.world_name = "server_" + server.replace(":", "_")   # memories of this server are kept apart
        cfg["bot_voice"] = True
        try:
            hub.bot_proc = launch_bot(cfg, server, hub.log, lite=hub.bot_lite)
        except Exception as e:
            hub.log("Не удалось запустить клиент Альтрона: %s" % e)
    await wait_llm(cfg, hub.log)
    try:
        # warm up the model so the first command is fast
        await hub.agent.llm.chat([{"role": "system", "content": hub.agent._system()},
                                  {"role": "user", "content": "[Проверка связи] Ответь одним словом: готов."}])
    except Exception as e:
        hub.log("Проверка ИИ не прошла: %s" % e)
    try:
        async with listener:
            await asyncio.gather(listener.serve_forever(), hub.voice_loop(), hub.agent_loop())
    finally:
        hub.stop_bot()
        if llm_proc is not None:
            llm_proc.terminate()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
