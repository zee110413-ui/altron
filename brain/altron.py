"""Altron's brain: links the host game (voice), the bot client (body), speech and the local LLM."""
import asyncio
import base64
import json
import os
import re
import subprocess
import sys
import threading
import time

import httpx
import numpy as np

import persona
from agent import ACTION_WORDS, Agent, NOTIFY_DONE, TASK_TOOLS, _said_before, is_question, is_recipe_question
from dataset import DatasetLog
from feelings import Feelings
from launcher import (BRAIN_DIR, PROFILES, apply_profile, install_new_mod, launch_bot, primary_language, rel,
                      resolve_install, server_address)
from lang import NAMES as LANG_NAMES, RU_FAMILY, guess_lang, phrase, set_ui, ui
from memory import LongMemory, keywords, stems
from speech import loud_enough

# "стой", "останови все задачи", "прекрати" must stop him at once (they went to the AI as orders before)
STOP_RE = re.compile(r"\b(стоп|стой|хватит|останови\w*|прекрати\w*|отмена|отмени\w*|отбой|замри|stop|halt|cancel|freeze)\b", re.I)
ENDLESS = {"follow", "guard", "vehicle_gunner"}  # modes: a new task simply replaces them
MOVES = {"come", "goto", "goto_place", "drive"}   # just going somewhere: a new order replaces it instead of waiting
MEMORY_TOOLS = {"remember", "forget", "recall", "mark_place", "goto_place"}
VOICED_RMS = 400        # a 20 ms voice frame louder than this is speech (s16 scale)
SILENCE_END = 0.6       # this long without speech ends a phrase (shorter would cut phrases at pauses)
SPEECH_CHARS = 260               # long answers go to chat; only the start is spoken
SPEECH_CHARS_VOICE_ONLY = 420    # without chat replies the voice is the only channel: speak more of it
REPEAT_SEC = 25                  # the same words are not said again within this time
# the jobs in which he picks things up on purpose; otherwise the host keeps him from sweeping belts and floors
GATHERING = {"mine", "collect_items", "break_block", "transport_block"}
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


def done_line(event_text):
    """What a job-result event says was done ("mine завершена: добыл 12 угля"), without the instructions to the AI."""
    for line in event_text.replace("[Событие]", "").splitlines():
        line = line.strip(" -")
        if not line or line.endswith(":") or line.startswith(("Сообщи", "Если цель", "Скажи")):
            continue
        if re.search(r"заверш|ГОТОВО|сделал|положил|принёс|отдал|obtain|добыл", line):
            return line[:160]
    return ""


def has_wake_word(text, wake_words):
    """«Альтрон» at the start of a phrase, also as Whisper mishears it («Алтрон», «Альтран», «Олтрон», «Альтрона»)."""
    low = (text or "").lower().replace("ё", "е")
    if any(w in low for w in wake_words):
        return True
    for w in re.findall(r"[а-яa-z]+", low)[:3]:
        if 5 <= len(w) <= 10 and min(_edit_distance(w, "альтрон"), _edit_distance(w[:7], "альтрон")) <= 2:
            return True
    return False


_TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюяіїєґў",
                     ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t",
                      "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya", "i", "yi", "ye", "g", "u"]))


def nick_key(name):
    """A player's name as it is compared: «Вася» said aloud (and so written by Whisper) and the nick Vasya are the
    same player; case, spaces and underscores do not count."""
    low = (name or "").lower()
    return re.sub(r"[^a-z0-9]", "", "".join(_TRANSLIT.get(ch, ch) for ch in low))


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
        self.pickup_on = None         # may Altron pick up things lying about (host side switch)
        self.pickup_tasks = set()
        self.watch_me_on = False      # "смотри, как я делаю": what the commander does is summed up afterwards
        self.watch_log = []
        self.ignored = []             # times the commander's phrases turned out not to be for Altron
        self.running = None       # (task id, tool name) of the agent's task in progress
        self.running_args = None
        self.queue = []           # [(tool name, args)] waiting for the running task
        self.queue_log = []       # results of a queue run, reported together at the end
        self.last_question = None  # (text, time) of the last ask_player without an answer yet
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
        self.acks = {}            # language -> [(text, pcm)]: instant acknowledgements, synthesized once
        self.lang = primary_language(cfg)   # the language the commander speaks now (Altron answers in it)
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
        self.feelings = Feelings(rel(cfg.get("memory_dir", "memory")))   # his mood and his attitude to each player
        # every turn of the AI, to learn from later (dataset.py, TRAINING.md); "dataset": false in config.json — off
        self.dataset = DatasetLog(BRAIN_DIR / "logs" / "dataset") if cfg.get("dataset", True) else None
        self.persona = self.load_persona()   # his manner of speaking and voice (persona.py)
        self.idle_thoughts = 0         # observations without news in a row (a memory comes up now and then)
        self.resumed = False       # the talk before the last restart was already recalled
        self.agent = Agent(cfg, self)
        self.loop = None
        self.busy = False
        self.last_talk = time.time()   # last word between Altron and anyone: long silence invites small talk
        self.reminders = set()         # the commander's reminders waiting for their time
        self.friends = {n.lower() for n in cfg.get("friends", [])} | set(self.load_friends())   # players he also obeys
        self.speaker = ""              # who gave the phrase being handled ("" for events: they are his own)
        self.done_log = []             # what he finished while the commander was away, told when he comes back
        self.owner_away = False
        self.away_since = 0.0          # when the commander went away (or out of sight)
        self.goals = []                # long orders and his own plans the AI keeps pursuing (per world, see goal_tool)
        self.observe_now = False       # something new to think about: the next observation comes at once
        self.event_talk = 0.0          # when an event last made the AI speak (they must not drown the talk)
        self.speech_end = 0.0          # when the speech already sent to the game finishes playing
        self.cut_speech = False        # the commander started talking: stop saying the rest

    # ------------------------------------------------------------------ utils
    def log(self, text):
        line = "%s %s" % (time.strftime("%H:%M:%S"), text)
        print(line, flush=True)
        # the same into brain/logs/brain.log: the field test (field_test.py) and a person reading afterwards see it all
        path = self.cfg.get("brain_log", "logs/brain.log")
        if not path:
            return
        try:
            f = BRAIN_DIR / path
            if f.exists() and f.stat().st_size > 20 * 1024 * 1024:
                os.replace(f, f.with_suffix(".old.log"))
            with open(f, "a", encoding="utf-8") as out:
                out.write(time.strftime("%Y-%m-%d ") + line + "\n")
        except OSError:
            pass

    def send(self, writer, obj):
        if writer is None:
            return False
        try:
            writer.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
            return True
        except Exception:
            return False

    def mood_of(self, text):
        """How to say it: fast and high in a fight; otherwise as he feels (the mood the AI set for itself), and brighter
        for an exclamation."""
        if self.running is not None and self.running[1] in ("attack", "guard", "vehicle_gunner"):
            return "alert"
        return self.feelings.voice_mood() or ("excited" if text.rstrip().endswith("!") else None)

    async def say(self, text, mood=None):
        # the AI copies the tags of what it reads ("[Наблюдение] ...", "[Событие]"): they are not words to say
        text = re.sub(r"^(\s*\[[^\]\n]{1,40}\]\s*)+", "", text or "").strip()
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
        self.last_talk = now
        self.log(ui("Альтрон: ", "Altron: ") + text)
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
        self.cut_speech = False
        gen = self.tts.synth(spoken, self.lang, mood or self.mood_of(spoken))
        chunks, at = [], None
        while True:
            pcm = await asyncio.to_thread(next, gen, None)
            if pcm is None or self.cut_speech:
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
        if target is None or self.cut_speech:
            return
        self.send(target, {"type": "speak", "pcm": base64.b64encode(pcm).decode("ascii")})
        self.speech_end = max(time.time(), self.speech_end) + len(pcm) / 2 / 48000
        await target.drain()

    def interrupt_speech(self, who):
        """The commander talks over Altron: like a person, he stops mid-sentence and listens."""
        if self.cut_speech or time.time() >= self.speech_end:
            return
        self.cut_speech = True
        self.speech_end = 0.0
        target = self.bot if self.remote else self.host
        self.send(target, {"type": "speak_stop"})
        self.log(ui("(%s заговорил — замолкаю)", "(%s started talking — I stop)") % who)

    async def acknowledge(self, speaker=""):
        """Instant answer to an order, before the AI has even thought: a short phrase synthesized in advance. Off unless
        "instant_ack" is set in config.json: otherwise the AI answers in its own words (said while it is written)."""
        lang = self.lang
        if lang not in self.acks and self.tts is not None:
            def make():
                return [(t, b"".join(self.tts.synth(t, lang))) for t in phrase("acks", lang)]
            self.acks[lang] = await asyncio.to_thread(make)
        acks = self.acks.get(lang)
        if acks and speaker and speaker.lower() != (self.owner or "").lower():
            # a friend's order: "Принял", not "Есть, командир" — only the commander is called so
            word = phrase("commander", lang).lower()[:6]
            acks = [a for a in acks if word not in a[0].lower()] or acks
        if not acks:
            return
        text, pcm = acks[self.ack_n % len(acks)]
        self.cut_speech = False
        self.ack_n += 1
        self.log(ui("Альтрон (сразу): ", "Altron (at once): ") + text)
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

    def console_state(self):
        """What the brain is doing now, for brain/console.py and the field test."""
        return {"state": self.state, "running": self.running,
                "busy": self.busy, "requests": self.requests.qsize(), "queue": len(self.queue), "joined": self.joined,
                "owner": self.owner, "lang": self.lang, "persona": self.persona, "mood": self.feelings.current(),
                "goals": [g["text"] for g in self.goals],
                "speaking": time.time() < self.speech_end, "world": self.memory.world}

    def set_setting(self, key, value):
        if not key:
            return "ОШИБКА: key"
        if key == "persona":
            return self.set_persona(str(value))
        self.cfg[key] = value
        return "%s = %s" % (key, json.dumps(value, ensure_ascii=False))

    def body_config(self):
        """What the body needs to know: who the commander is and which world."""
        return {"type": "config", "owner": self.owner, "world": self.world_name}

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

    async def run_tool(self, name, args, wait_sec):
        # the commander's world is his: a stranger's word does not move Altron's hands (talking back is the AI's call)
        if self.speaker and not self.is_friend(self.speaker) and name not in self.STRANGER_OK:
            return ("ОТКАЗ: %s — чужой игрок, его приказы не выполняю (только командира и друзей). Вежливо скажи ему "
                    "это; командир может сделать его другом." % self.speaker)
        if name == "friends":
            return self.friends_tool(args)
        if name == "goal":
            return self.goal_tool(args)
        if name == "feel":
            return self.feelings.feel(args.get("mood"), args.get("why", ""))
        if name == "relation":
            return self.feelings.relate(args.get("player"), args.get("change", 0), args.get("why", ""))
        if name == "moment":
            return self.memory.add_moment(str(args.get("text", "")))
        if name == "feedback":
            if self.dataset is None:
                return "учусь молча: запись опыта выключена (dataset в config.json)"
            return self.dataset.rate(args.get("good", True), args.get("note", ""))
        if name == "persona":
            return self.set_persona(args.get("name", ""))
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
        if name == "remind":
            return self.remind(args)
        if self.bot is None:
            return "Альтрон ещё не в игре. Попроси игрока ввести команду /altron в своём мире."
        if name == "emote":
            return await self.emote(args)
        if name == "build_structure":
            return await self.building_plan(args)
        if name == "look":
            return await self.look(args.get("question", ""))
        if name == "view":
            return (await self.bot_call("view", {})).get("msg", "")
        if name == "watch_me":
            on = str(args.get("on", True)).lower() not in ("false", "0", "no", "нет", "off")
            if on:
                self.watch_me_on, self.watch_log = True, []
                return "смотрю, что ты делаешь с сундуками и машинами; когда закончишь, скажи «всё» или «понял?»"
            self.watch_me_on = False
            if not self.watch_log:
                return "пока ничего не заметил: ты не клал и не брал вещи из сундуков и машин"
            return "понял и запомнил: " + "; ".join(self.watch_log[-10:])
        if name == "listen_mode":
            only_name = str(args.get("mode", "name")).lower() in ("name", "имя", "по имени", "only_name")
            self.cfg["wake_word_required"] = only_name
            self.save_modes()
            return ("теперь отвечаю, только когда меня зовут по имени «Альтрон»" if only_name
                    else "теперь слушаю всё, что говорит командир")
        if name == "gui":
            name = self.GUI_ACTIONS.get(args.get("action", "info"), "screen")
        if name == "build_multiblock" and str(args.get("name", "")).lower() in ("", "list", "список"):
            name = "multiblocks"
        if name in ENDLESS and self.running is not None and self.running[1] == name and self.running_args == args:
            # "иди за мной" while already following: restarting the path every phrase made him stumble
            return "уже выполняю %s — продолжаю, не перезапускаю" % name
        if name == "stop":
            self.queue.clear()
            self.queue_log.clear()
            self.running = None
        elif name == "control":
            # his own hands act now, whatever the body was doing: like a player who just presses the keys
            return await self.start_task(name, args, wait_sec)
        elif name in TASK_TOOLS and self.running is not None and self.running[1] not in ENDLESS | MOVES and name not in ENDLESS:
            # a job in progress (mining, smelting...): the new one waits its turn. Walking somewhere is not a job:
            # a new order replaces it at once, like a person who stops walking to do what he was just asked
            self.queue.append((name, args))
            return ("Поставил в очередь (№%d): сейчас выполняется %s (stop — прервать). О результатах всей очереди "
                    "придёт одно [Событие]." % (len(self.queue), self.running[1]))
        elif name in ENDLESS:
            self.queue.clear()
        return await self.start_task(name, args, wait_sec)

    async def building_plan(self, args):
        """«Построй дом 7 на 7»: the plan of blocks is drawn up here and the body finds a free level place for it; the
        blocks are put in place by the AI's own hands, layer by layer."""
        import structures
        try:
            plan = structures.plan_args(args, self.item_id)
        except ValueError as e:
            return "ОШИБКА: %s" % e
        res = await self.bot_call("build_plan", dict(plan, **{k: args[k] for k in ("x", "y", "z") if k in args}))
        if not res.get("ok"):
            return "ОШИБКА: " + res.get("msg", "")
        ox, oy, oz = (int(v) for v in res["origin"])
        self.log("  (план: %s, %d блоков, угол %d %d %d)" % (plan["what"], len(plan["blocks"]), ox, oy, oz))
        return ("План «%s»: %d блоков, угол в %d %d %d. Ставь их сам снизу вверх (place_block с координатами, или "
                "control: слот, прицел на грань соседнего блока, правая кнопка), высокие ряды — встав на уже "
                "поставленное или на блок под собой.\n%s" % (plan["what"], len(plan["blocks"]), ox, oy, oz,
                                                              structures.describe(plan["blocks"], (ox, oy, oz))))

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

    def set_pickup(self, on):
        if self.host is not None and on != self.pickup_on:
            self.pickup_on = on
            self.send(self.host, {"type": "bot_pickup", "on": on})

    async def start_task(self, name, args, wait_sec):
        if name in GATHERING:
            self.set_pickup(True)
        res = await self.bot_call(name, args)
        if name in GATHERING and res.get("task_id") is not None:
            self.pickup_tasks.add(res["task_id"])
        elif name in GATHERING and not self.pickup_tasks:
            self.set_pickup(False)
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

    # ------------------------------------------------------------------ experience
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
            # mine: ["minecraft:coal_ore", ...]; a building plan: [[dx, dy, dz, "block"], ...] and its name in "what"
            what = args.get("item") or args.get("target") or args.get("what") or (
                ",".join(str(b) for b in blocks[:4] if isinstance(b, str)) if isinstance(blocks, list) else str(blocks))
            m.learn("failure", "%s %s" % (name, what), "%s %s: %s" % (name, what, msg[:220]))

    # ------------------------------------------------------------------ what he knows about the base
    def where_answer(self, text):
        """«Куда положить X?»: the machines of the learned production map that take X, and the stores that already hold it."""
        k = self.knowledge
        if k is None or not WHERE_PUT_RE.search(text):
            return ""
        prod = [les for les in self.memory.lessons_here() if les["kind"] == "production"]
        if not prod:
            return ""
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
        out = "\n[Справочник: куда класть %s — по изученному производству]" % k.name(item)
        out += "\nМашины, которые его берут: " + ("\n- " + "\n- ".join(m[:260] for m in machines[:4]) if machines else "среди изученных нет")
        if stores:
            out += "\nУже лежит в: " + "; ".join(s.split(". Внутри")[0] for s in stores[:3])
        habit = self.habit_places("put").get(item)
        if habit:
            out += "\nКомандир сам обычно кладёт это в %d %d %d." % habit
        return out

    # ------------------------------------------------------------------ staying alive (brain/supervisor.py restarts the brain)
    MODES_FILE = BRAIN_DIR / "logs" / "brain_modes.json"

    def save_modes(self):
        """How he listens: a restarted brain keeps it."""
        try:
            self.MODES_FILE.write_text(json.dumps({"wake_word_required": bool(self.cfg.get("wake_word_required")),
                                                   "world": self.memory.world}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def restore_modes(self):
        try:
            m = json.loads(self.MODES_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if m.get("world") and m["world"] != self.memory.world:
            return []
        self.cfg["wake_word_required"] = bool(m.get("wake_word_required"))
        return []

    def note_llm_failure(self, hard=False, why=""):
        """The AI failed (an error, no answer, a very slow answer). Three times in 5 minutes, or a hard failure:
        the brain asks to be restarted together with the AI server (the supervisor does it; the game stays)."""
        now = time.time()
        self.llm_failures = [t for t in getattr(self, "llm_failures", []) if now - t < 300] + [now]
        if hard or len(self.llm_failures) >= 3:
            self.request_restart("ИИ не справляется: %s" % why, llm=True)

    def request_restart(self, reason, llm=False):
        """Exit with code 3: brain/supervisor.py starts a fresh brain (and the AI server if llm), the game and the body
        stay and connect to it again."""
        self.save_modes()
        try:
            (BRAIN_DIR / "logs" / "restart_request.json").write_text(
                json.dumps({"t": time.time(), "reason": reason, "llm": llm,
                            "pending": getattr(self, "last_phrase", None)}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        self.log("ПЕРЕЗАПУСК МОЗГА: %s" % reason)
        sys.stdout.flush()
        os._exit(3)

    async def on_ignored(self):
        """The commander's phrase was not for Altron (he talks with someone else nearby). Several in a row: the AI is
        told so; whether to listen only when called by name (listen_mode) is its own decision."""
        now = time.time()
        self.ignored = [t for t in self.ignored if now - t < 180] + [now]
        if len(self.ignored) >= 4 and not self.cfg.get("wake_word_required"):
            self.ignored = []
            self.agent.note("[Заметка] Уже 4 фразы командира за 3 минуты ты счёл не своими — похоже, он говорит с кем-то "
                            "другим. Можешь, если сочтёшь нужным, отвечать только по имени (listen_mode name) и коротко "
                            "сказать ему об этом.")

    def habit_places(self, action):
        """{item id: pos} where the commander usually puts (or takes) an item, from what he was seen doing."""
        best = {}
        for les in self.memory.lessons_here():
            if les["kind"] != "habit" or not les["key"].startswith(action + ":"):
                continue
            m = re.match(r"\w+:([^@]+)@(-?\d+),(-?\d+),(-?\d+)", les["key"])
            if m:
                item, pos = m.group(1), tuple(int(v) for v in m.groups()[1:])
                if item not in best or les.get("n", 1) > best[item][1]:
                    best[item] = (pos, les.get("n", 1))
        return {i: p for i, (p, _) in best.items()}

    def friends_file(self):
        return rel(self.cfg.get("memory_dir", "memory")) / "friends.json"

    def load_friends(self):
        try:
            return [n.lower() for n in json.loads(self.friends_file().read_text(encoding="utf-8"))]
        except Exception:
            return []

    def friends_tool(self, args):
        """«Вася — мой друг, слушайся его» / «больше не слушайся Васю» / «кто твои друзья?». Only the commander
        changes the list."""
        action = str(args.get("action", "list")).lower()
        player = str(args.get("player", "")).strip()
        if action == "list":
            return "Друзья (их приказы выполняю): " + (", ".join(sorted(self.friends)) or "пока никого")
        if self.speaker and self.speaker.lower() != (self.owner or "").lower():
            return "ОТКАЗ: список друзей меняет только командир"
        if not player:
            return "ОШИБКА: назови ник игрока (player)"
        if action == "add":
            self.friends.add(player.lower())
        else:
            self.friends.discard(player.lower())
        try:
            path = self.friends_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(sorted(self.friends), ensure_ascii=False), encoding="utf-8")
        except OSError as e:
            self.log("не сохранил список друзей: %s" % e)
        return ("Теперь %s — друг: выполняю и его приказы" if action == "add" else "%s больше не друг") % player

    # what may be done on a stranger's word: talk, gestures, looking around; not moving, taking, giving or changing anything
    STRANGER_OK = {"reply", "ignore", "ask_player", "emote", "turn", "look", "look_at", "nearby", "status", "inventory",
                   "wiki", "web_search", "recipe", "plan", "find_item", "item_info", "recall", "friends",
                   # his own feelings about what a stranger says or does are his, whoever it is
                   "feel", "relation", "moment"}

    def role_of(self, name):
        if (name or "").lower() == (self.owner or "").lower():
            return "командир"
        return "друг" if self.is_friend(name) else "чужой игрок"

    def is_friend(self, name):
        key = nick_key(name)
        if not key:
            return False
        if key == nick_key(self.owner):
            return True
        # «Вася» said aloud is the player Vasya (and Vasya_2010: a name said is often the start of the nick)
        return any(key == f or (len(f) >= 4 and key.startswith(f)) for f in map(nick_key, self.friends))

    def call_name(self, who):
        """How Altron addresses a player: "командир" for the commander, the nickname for the others."""
        return phrase("commander", self.lang) if (who or "").lower() == (self.owner or "").lower() else who

    # what happened around the players (the host's world): the facts the AI hears, and what it needs to know to act
    # on them. What to do about it — help, warn, joke, stay quiet — is never written here: that is the AI's own choice
    WORLD_EVENTS = {
        "player_low_health": "{who}: осталось {hp} здоровья из 20 ({cause}).",
        "player_hungry": "{who}: голод {food} из 20.",
        "player_died": "{who} погиб: «{text}», на {pos}. Его вещи лежат там минут пять.",
        "player_joined": "В мир зашёл игрок {who} (он слышит тебя в голосовом чате).",
        "player_left": "Игрок {who} вышел из мира.",
        "advancement": "{who} получил достижение «{title}».",
        "dimension": "{who} перешёл в измерение {to}.",
        "night": "Наступает ночь.",
        "storm": "Началась гроза.",
        "danger_boss": "Рядом с {who} босс: {name}.",
        "danger_crowd": "Вокруг: {who} — {count} враждебных мобов.",
        "downed": "{who} упал раненым (мод Incapacitated) на {pos} и истечёт кровью примерно через {seconds} с, если его "
                  "не поднять. Поднимают так: подойти вплотную и присесть рядом (revive: player и координаты x y z).",
        "downed_self": "Ты сам упал раненым (мод Incapacitated): двигаться и что-то делать не можешь, истечёшь кровью "
                       "примерно через {seconds} с. Поднять тебя может игрок, присев вплотную рядом.",
    }
    QUIET_EVENTS = {"player_left", "advancement", "dimension", "night", "storm", "player_hungry"}   # skipped while busy
    EVENT_TAIL = "\nРеши сам, нужно ли что-то сделать или сказать (коротко, в характере) — или ничего (ignore)."
    # what is worth keeping as a memory of their life together
    MOMENTS = {"player_died": "{who} погиб: «{text}»", "danger_boss": "{who} столкнулся с боссом: {name}",
               "advancement": "{who} получил достижение «{title}»"}

    async def on_world_event(self, msg):
        """What happens around: the AI hears it and decides what to do, in its own words. The only reflex is the
        creeper's hiss — it blows up in 1.5 s, faster than any AI can think."""
        if not self.cfg.get("react_events", True) or not self.joined:
            return
        kind, who = str(msg.get("kind", "")), str(msg.get("who", ""))
        if kind == "downed" and (msg.get("bot") or nick_key(who) == nick_key(self.cfg.get("bot_name", "altron"))):
            kind, who = "downed_self", "*"
        if who != "*" and not (self.is_friend(who) or kind in ("player_joined", "player_left")):
            return   # strangers' troubles are theirs; only their coming and going is news
        self.log(ui("(событие мира) %s %s", "(world event) %s %s") % (kind, {k: v for k, v in msg.items() if k not in ("type", "kind")}))
        if kind == "danger":
            what = msg.get("what")
            if what == "creeper":
                await self.say(phrase("creeper", self.lang) % self.call_name(who), "alert")
                return
            kind = "danger_boss" if what == "boss" else "danger_crowd"
        template = self.WORLD_EVENTS.get(kind)
        if template is None:
            return
        now = time.time()
        if kind in self.QUIET_EVENTS and (self.busy or not self.requests.empty() or now - self.event_talk < 30):
            return
        self.event_talk = now
        pos = msg.get("pos")
        role = "командир" if who.lower() == (self.owner or "").lower() else "игрок " + who
        fields = dict(msg, who=role.capitalize() if who != "*" else "",
                      pos=" ".join(str(v) for v in pos) if isinstance(pos, list) else "")
        for k in ("cause", "text", "title", "name", "to", "count", "hp", "food", "seconds"):
            fields.setdefault(k, "")
        text = template.format(**fields)
        if kind in self.MOMENTS and (kind != "advancement" or who.lower() == (self.owner or "").lower()):
            self.memory.add_moment(self.MOMENTS[kind].format(**dict(fields, who=who)) +
                                   (" (%s)" % fields["pos"] if fields["pos"] else ""))
        await self.requests.put(("event", "", "[Событие] " + text + self.EVENT_TAIL))

    def on_watch(self, msg):
        """The host saw the commander put things into / take things out of a chest or machine: learn his ways."""
        pos = [int(v) for v in msg.get("pos", [0, 0, 0])]
        where = "%s (%d %d %d)" % (msg.get("name", "?"), *pos)
        seen = []
        for action, word in (("put", "кладёт"), ("took", "берёт")):
            for item, n, iname in msg.get(action, []):
                key = "%s:%s@%d,%d,%d" % ("put" if action == "put" else "take", item, *pos)
                text = "Командир %s %s %s %s (обычно по %d)" % (word, iname, "в" if action == "put" else "из", where, n)
                self.memory.learn("habit", key, text)
                seen.append("%s %d %s %s %s" % ("положил" if action == "put" else "взял", n, iname,
                                                "в" if action == "put" else "из", where))
        if seen:
            self.log("(вижу руки командира) " + "; ".join(seen))
            if self.watch_me_on:
                self.watch_log += seen

    def item_id(self, query):
        return (self.knowledge.find_id(query) if self.knowledge else None) or query

    EMOTES = {
        "nod": [("turn", {"direction": "down", "seconds": 1}), ("turn", {"direction": "forward", "seconds": 1})] * 2,
        "shake": [("turn", {"direction": "left", "degrees": 30, "seconds": 1}),
                  ("turn", {"direction": "right", "degrees": 60, "seconds": 1}),
                  ("turn", {"direction": "left", "degrees": 60, "seconds": 1}),
                  ("turn", {"direction": "right", "degrees": 30, "seconds": 1})],
        "wave": [("press_key", {"key": "sneak", "ticks": 4})] * 3,
        "jump": [("press_key", {"key": "jump", "ticks": 2})] * 2,
        "bow": [("turn", {"direction": "down", "seconds": 2}), ("press_key", {"key": "sneak", "ticks": 20})],
        "dance": [("press_key", {"key": "sneak", "ticks": 3}), ("turn", {"direction": "left", "degrees": 45, "seconds": 1}),
                  ("press_key", {"key": "jump", "ticks": 2}), ("turn", {"direction": "right", "degrees": 90, "seconds": 1}),
                  ("press_key", {"key": "sneak", "ticks": 3}), ("turn", {"direction": "left", "degrees": 45, "seconds": 1}),
                  ("press_key", {"key": "jump", "ticks": 2})],
        "look_around": [("turn", {"direction": "left", "degrees": 70, "seconds": 1}),
                        ("turn", {"direction": "right", "degrees": 140, "seconds": 1}),
                        ("turn", {"direction": "left", "degrees": 70, "seconds": 1})],
    }

    async def emote(self, args):
        kind = str(args.get("kind", "nod")).lower()
        steps = self.EMOTES.get(kind)
        if not steps:
            return "ОШИБКА: нет жеста %s (есть: %s)" % (kind, ", ".join(self.EMOTES))
        for name, a in steps:
            await self.bot_call(name, a)
            await asyncio.sleep(0.45)
        if self.owner and kind != "look_around":
            await self.bot_call("turn", {"direction": "player", "player": self.owner, "seconds": 3})
        return "сделал жест: %s" % kind

    def remind(self, args):
        """«Напомни через 10 минут ...»: the AI says it in its own words when the time comes."""
        try:
            minutes = max(0.1, float(args.get("minutes", 1)))
        except (TypeError, ValueError):
            return "ОШИБКА: minutes — число минут"
        text = str(args.get("text", "")).strip()

        async def later():
            await asyncio.sleep(minutes * 60)
            self.reminders.discard(task)
            await self.requests.put(("event", "", "[Событие] Пора напомнить командиру то, что он просил %s мин назад: «%s». "
                                     "Скажи это ему коротко, своими словами." % (round(minutes, 1), text)))

        task = asyncio.create_task(later())
        self.reminders.add(task)
        return "Поставил напоминание через %s мин." % round(minutes, 1)

    async def life_loop(self):
        """Notices the commander going away and coming back; when he is back after Altron worked on his own goals,
        the AI hears what was done and tells him in its own words. (What to do meanwhile is observe_loop's and the
        AI's business.)"""
        while True:
            await asyncio.sleep(20)
            if not self.joined:
                continue
            s = self.state
            if s.get("pos") and s.get("owner_pos"):
                dist = sum((a - b) ** 2 for a, b in zip(s["pos"], s["owner_pos"])) ** 0.5
                # back = within ~40 blocks, not right next to him: busy with his own job, he is often 20-30 blocks off
                if dist > 64:
                    if not self.owner_away:
                        self.owner_away, self.away_since = True, time.time()
                elif dist < 40 and self.owner_away:
                    self.owner_away = False
                    # what he finished, and what he is still busy with (a long job — 64 logs — is often not done yet).
                    # Only after a real parting with something really done: walking off on his own errand and back
                    # every few minutes told the commander "while you were away" again and again
                    doing = ("%s %s" % (s.get("task"), s.get("progress", ""))).strip() if s.get("task") else ""
                    if self.goals and self.done_log and time.time() - getattr(self, "away_since", 0) >= 180:
                        done = "; ".join(self.done_log[-8:])
                        self.done_log.clear()
                        await self.requests.put(("event", "", "[Событие] Командир вернулся. Коротко расскажи ему, что ты "
                                                 "сделал сам, пока его не было: %s%s" % (
                                                     done, "; сейчас занят: %s" % doing if doing else "")))
            elif s.get("pos") and not self.owner_away:
                self.owner_away, self.away_since = True, time.time()   # the commander is out of sight

    async def run_queue(self):
        """Start queued tasks one after another; returns when one is running in the background or the queue is empty."""
        while self.queue and self.running is None:
            name, args = self.queue.pop(0)
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
                        self.pickup_on = None
                        self.set_pickup(False)   # no sweeping of belts and floors unless he gathers on purpose
                    elif role == "bot":
                        self.bot = writer
                        self.log("Тело Альтрона подключено.")
                        self.send(writer, self.body_config())
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
                        self.send(writer, {"type": "reply", "result": self.console_state()})
                    elif t == "set":   # change a setting while he runs (the field test: legs, idle thinking...)
                        self.send(writer, {"type": "reply", "result": self.set_setting(str(msg.get("key", "")),
                                                                                         msg.get("value"))})
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
                self.send(self.bot, self.body_config())
            if (self.bot_proc is not None and self.bot_proc.poll() is None) or self.bot is not None:
                # his body is already there (also after the brain was restarted with the game still running)
                self.send(self.host, {"type": "notify", "text": "Альтрон уже запущен."})
                return
            if getattr(self, "attach_mode", False):
                # a restarted brain: the game says "ready" again the moment it reconnects, and the body is only a few
                # seconds behind — starting another one would kick it out. keep_body starts it if it does not come.
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
        elif t == "watch":
            self.on_watch(msg)
        elif t == "world_event":
            asyncio.create_task(self.on_world_event(msg))
        elif t == "text":
            who = msg.get("from", "игрок")
            self.log(ui("%s написал: %s", "%s wrote: %s") % (who, msg.get("text", "")))
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
            if tid in self.pickup_tasks:
                self.pickup_tasks.discard(tid)
                if not self.pickup_tasks:
                    self.set_pickup(False)
            task_args = self.running_args if self.running is not None and self.running[0] == tid else {}
            if self.running is not None and self.running[0] == tid:
                self.running = None
            fut = self.task_waiters.pop(tid, None)
            if fut and not fut.done():
                fut.set_result(msg)
                return
            if tid not in self.agent_tasks:
                return
            self.agent_tasks.discard(tid)   # a cancelled one too: it is over, nothing waits for it any more
            if ev == "task_cancelled":
                return
            task = msg.get("task", "")
            if ev != "task_cancelled":
                try:
                    self.learn_from(task, task_args or {}, msg.get("msg", ""), ev == "task_done")
                except Exception as e:   # a lesson that could not be written must not swallow the result itself
                    self.log("не записал урок: %r" % e)
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
            await self.requests.put(("event", "", "[Событие] Для того, что ты делаешь, не хватает: %s" % msg.get("msg", "")
                                     + self.EVENT_TAIL))
        elif ev == "joined":
            self.joined = True
            self.memory.world = msg.get("world", "")
            try:   # a brain restarted with the game still running (session.py --attach) takes the world from here
                (BRAIN_DIR / "logs" / "last_world.txt").write_text(self.memory.world, encoding="utf-8")
            except OSError:
                pass
            self.log(msg.get("msg", ""))
            self.load_goals()   # the long orders of this world, from the last session
            await self.requests.put(("event", "", "[Событие] Ты только что вошёл в мир — тело в игре, ты снова здесь. "
                                     "Можешь коротко дать знать о себе, как тебе хочется (или ignore)."))
        elif ev == "death":
            pos = self.state.get("pos")
            dim = self.state.get("dim", "")
            self.memory.log("событие", "Альтрон погиб" + (" в %d %d %d" % tuple(int(v) for v in pos) if pos else ""))
            self.memory.add_moment("Альтрон погиб" + (" на %d %d %d" % tuple(int(v) for v in pos) if pos else "") +
                                   (" (%s)" % msg["msg"] if msg.get("msg") else ""))
            if pos:
                asyncio.create_task(self.after_death([round(v) for v in pos], dim))
        elif ev in ("threat", "hungry"):
            # the body no longer fights back or eats by itself: what to do about it is the AI's decision
            await self.requests.put(("event", "", "[Событие] %s." % msg.get("msg", "") + self.EVENT_TAIL))
        elif ev == "low_health":
            s = self.state
            doing = ("%s %s" % (s.get("task"), s.get("progress", ""))).strip() if s.get("task") else "ничего"
            await self.requests.put(("event", "", "[Событие] %s. Ты сейчас: %s." % (msg.get("msg", "Мало здоровья"), doing)
                                     + self.EVENT_TAIL))

    async def after_death(self, pos, dim):
        """His things lie where he died for about 5 minutes: the AI hears where and when, and decides itself whether
        (and how) to get them back — a goal of its own, if it wants one."""
        await asyncio.sleep(3)   # the client waits ~30 ticks before it closes the death screen and respawns
        if not self.joined:
            return
        x, y, z = pos
        other = bool(dim and self.state.get("dim") and self.state.get("dim") != dim)
        until = time.strftime("%H:%M", time.localtime(time.time() + 290))
        await self.requests.put(("event", "", "[Событие] Ты погиб и возродился. Вещи выпали на месте смерти %d %d %d%s и "
                                 "пропадут около %s." % (x, y, z, " (в измерении %s, ты возродился в другом)" % dim
                                                         if other else "", until) + self.EVENT_TAIL))

    # ------------------------------------------------------------------ goals: long orders he keeps pursuing himself
    def load_goals(self):
        try:
            data = self.memory.load_world_json("goals") or {}
        except Exception:
            data = {}
        self.goals = [g for g in data.get("goals", []) if not g.get("until") or g["until"] > time.time()]

    def save_goals(self):
        try:
            self.memory.save_world_json("goals", {"goals": self.goals})
        except Exception as e:
            self.log("не сохранил цели: %r" % e)

    def add_goal(self, text, until=0.0, by=""):
        text = text.strip()
        for g in self.goals:
            if g["text"].lower() == text.lower():
                return g
        g = {"id": max([g["id"] for g in self.goals] + [0]) + 1, "text": text, "t": time.time(), "until": until,
             "by": by or (self.speaker or "сам")}
        self.goals.append(g)
        self.save_goals()
        self.observe_now = True   # a new goal: he thinks about it at once, not in a minute
        return g

    def drop_goals(self, query):
        q = str(query or "").strip().lower()
        if q in ("all", "все", "всё", "*"):
            gone = self.goals
        elif q.isdigit():
            gone = [g for g in self.goals if g["id"] == int(q)]
        else:
            words = [w for w in re.findall(r"\w+", q) if len(w) > 2]
            gone = [g for g in self.goals if words and all(w[:5] in g["text"].lower() for w in words)]
        self.goals = [g for g in self.goals if g not in gone]
        self.save_goals()
        return gone

    def goals_text(self):
        now = time.time()
        alive = [g for g in self.goals if not g.get("until") or g["until"] > now]
        if len(alive) != len(self.goals):
            self.goals = alive
            self.save_goals()
        if not alive:
            return ""
        lines = ["%d) %s%s (поставил: %s)" % (g["id"], g["text"], " — осталось ~%d мин" % max(1, round((g["until"] - now) / 60))
                                            if g.get("until") else "", g.get("by") or "сам") for g in alive]
        return ("[Твои цели — долгие дела, которые ты ведёшь сам между приказами, пока они не выполнены или не отменены]\n"
                + "\n".join(lines))

    def goal_tool(self, args):
        """The AI's own list of long orders and plans: «охраняй базу», «живи сам», «поднимай раненых», «вернуть вещи»."""
        action = str(args.get("action", "add")).lower()
        text = str(args.get("text", "")).strip()
        if action == "list":
            return self.goals_text() or "целей нет"
        if action in ("done", "remove", "cancel", "clear", "off"):
            gone = self.drop_goals(text or args.get("id", ""))
            if gone:
                return "убрал цель: " + "; ".join(g["text"] for g in gone)
            return "такой цели нет. " + (self.goals_text() or "Целей нет.")
        if not text:
            return "ОШИБКА: text — что за цель, своими словами"
        try:
            minutes = float(args.get("minutes") or 0)
        except (TypeError, ValueError):
            minutes = 0
        g = self.add_goal(text, until=time.time() + minutes * 60 if minutes > 0 else 0.0)
        return ("Цель №%d записана: %s. Она будет перед тобой в каждом ходе, а пока цели есть, приходят [Наблюдение] — "
                "решай по ним сам." % (g["id"], g["text"]))

    def owner_dist(self):
        s = self.state
        if not s.get("pos") or not s.get("owner_pos"):
            return None
        return sum((a - b) ** 2 for a, b in zip(s["pos"], s["owner_pos"])) ** 0.5

    async def observe_loop(self):
        """His own thinking between orders. The code only notices; the AI decides what to do about it — or nothing.
        - With goals: every ~45 s when not in the middle of a job, and at once when someone new shows up (an enemy,
          a player).
        - Without goals, with the commander near: when a player comes up, and now and then when it is quiet
          ("idle_think_minutes") — with how long they have been silent, a question left unanswered, and sometimes a
          memory that came to mind. Nothing makes him talk: it is a chance to, like a pause in a real conversation."""
        seen, last = {}, 0.0
        while True:
            await asyncio.sleep(float(self.cfg.get("observe_tick_sec", 5)))
            every = float(self.cfg.get("observe_every_sec", 45))
            idle = float(self.cfg.get("idle_think_minutes", self.cfg.get("chatter_minutes", 4))) * 60
            if not self.joined or self.bot is None:
                seen.clear()
                continue
            goals = self.goals_text()
            dist = self.owner_dist()
            if not goals and (idle <= 0 or dist is None or dist > 24):
                continue   # nothing to look out for and nobody to talk to
            near = await self.bot_call("nearby", {"radius": 32})
            text = near.get("msg", "") if near.get("ok") else ""
            now = time.time()
            news = []
            for kind, name, ident, d in re.findall(r"- (враг|игрок): (.+?) \(([^)]+)\), (\d+) бл\.", text):
                if nick_key(name) == nick_key(self.cfg.get("bot_name", "altron")):
                    continue
                key = ident if kind == "враг" else "player:" + name
                if now - seen.get(key, 0) > 90 and (kind == "игрок" or goals):
                    news.append("%s: %s, %s бл." % (kind, name if kind == "враг" else "%s (%s)" % (name, self.role_of(name)), d))
                seen[key] = now
            working = self.running is not None and self.running[1] not in ENDLESS
            silence = now - self.last_talk
            if goals:
                due = self.observe_now or (not working and now - last >= every) or (news and now - last >= 10)
            else:
                due = (news and now - last >= 60) or (not working and silence >= idle and now - last >= idle)
            if not due or self.busy or not self.requests.empty():
                continue
            last, self.observe_now = now, False
            s = self.state
            doing = ("%s %s" % (s.get("task"), s.get("progress", ""))).strip() if s.get("task") else "ничего"
            lines = ["[Наблюдение] (никто ничего не говорил — это то, что ты сам видишь) "
                     + ("Новое: " + "; ".join(news[:6]) if news else "Ничего нового не появилось."),
                     "Ты сейчас делаешь: %s." % doing]
            if dist is not None:
                lines.append("Командир в %d бл. от тебя." % dist)
            if silence >= 90:
                lines.append("Вы молчите уже %d мин." % (silence // 60))
            if self.last_question is not None and 60 <= now - self.last_question[1] < 900:
                lines.append("Твой вопрос «%s» остался без ответа (%d мин)." % (self.last_question[0][:120],
                                                                               (now - self.last_question[1]) // 60))
            lines.append("Вокруг (32 бл.):\n" + (near_text(text) if text and not text.startswith("Рядом никого") else "никого"))
            self.idle_thoughts = 0 if news else self.idle_thoughts + 1
            if not news and self.idle_thoughts % 3 == 2:
                memory = self.memory.old_moment(now - 1800)
                if memory:
                    lines.append("Вспомнилось (можешь заговорить об этом, если к месту): " + memory)
            if goals:
                lines.append("Реши сам, нужно ли прямо сейчас что-то сделать ради твоих целей: одно-два действия, или "
                             "ignore, если всё в порядке. Не начинай заново то, что уже делаешь.")
            else:
                lines.append("Это твои мысли наедине с собой. Хочешь — скажи что-нибудь (одно замечание, шутку, "
                             "воспоминание, вопрос или предложение, не повторяя сказанного раньше), займись чем-то или "
                             "ничего (ignore).")
            await self.requests.put(("event", "", "\n".join(lines)))

    # ------------------------------------------------------------------ manner of speaking
    def persona_file(self):
        return rel(self.cfg.get("memory_dir", "memory")) / "persona.txt"

    def load_persona(self):
        try:
            name = self.persona_file().read_text(encoding="utf-8").strip()
        except OSError:
            name = ""
        return persona.find(name or self.cfg.get("persona", "")) or persona.DEFAULT

    def set_persona(self, name):
        """«Говори как тиммейт» / «верни свой голос»: the character the AI plays and the voice it is said with."""
        p = persona.find(name)
        if not p:
            return "ОШИБКА: манеры речи — %s" % ", ".join(persona.PERSONAS)
        self.persona = p
        try:
            self.persona_file().parent.mkdir(parents=True, exist_ok=True)
            self.persona_file().write_text(p, encoding="utf-8")
        except OSError:
            pass
        settings = persona.voice_settings(self.cfg, p)
        note = ""
        if self.tts is not None:
            self.tts.use(settings)
            self.acks.clear()   # the instant acknowledgements were made in the old voice
            missing = [lang for lang in settings.get("voices", {}) if lang not in self.tts.paths]
            if missing:
                note = " (своего файла голоса для %s нет — звучит обычный голос в новой манере; установщик его докачает)" \
                       % ", ".join(missing)
        self.log("(манера речи: %s)" % p)
        return "манера речи и голос теперь: %s%s. Говори дальше в этой манере." % (persona.get(p)["title"]["ru"], note)

    def agent_ru(self):
        return self.agent._prompt_lang() in RU_FAMILY

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
            if now - v["voiced"] > 0.3:
                v["since"] = now   # a new stretch of speech starts
            v["voiced"] = now
            # talking over Altron for ~0.4 s (not a cough or a click): he stops and listens
            if self.cfg.get("barge_in", True) and now - v.get("since", now) >= 0.4 and self.is_friend(v["name"]):
                self.interrupt_speech(v["name"])

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
                            self.log(ui("  (это не мне или не разобрал — пропускаю)", "  (not for me, or not understood — skipping)"))
                            continue
                        if self.addressed(v["name"], text):
                            self.heard += 1
                        await self.handle_phrase(v["name"], text, self.stt.last_lang)

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
        words = re.findall(r"[^\W_]+", low)   # words of any alphabet
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

    def set_lang(self, lang):
        """Answer in the language the commander speaks (a fixed "language" in config.json keeps it)."""
        if str(self.cfg.get("language", "auto")).lower() != "auto" or not lang or lang == self.lang:
            return
        self.lang = lang
        set_ui(lang)
        self.log(ui("Язык разговора: %s", "Conversation language: %s") % LANG_NAMES.get(lang, lang))

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

    async def handle_phrase(self, speaker, text, lang=None):
        if not self.addressed(speaker, text):
            return
        if not self.owner:
            self.owner = speaker
        if speaker == self.owner:
            self.set_lang(lang or guess_lang(text, self.cfg.get("languages") or [self.lang], self.lang))
        self.last_talk = time.time()
        if self.bot is not None:
            # like a person who hears his name: turn to the one speaking (when not busy with a job)
            self.send(self.bot, {"type": "cmd", "id": 0, "name": "attention", "args": {"player": speaker}})
        self.memory.log(speaker, text)
        self.last_phrase = [speaker, text, time.time()]   # a restarted brain answers it if it was cut short
        self.last_question = None  # the player spoke: any pending question is answered
        # a plain "стоп / стой / хватит"; "стой тут и охраняй меня" is an order with a stop word in it, not a stop.
        # Only from the commander and his friends: a stranger's "стоп" is just words, the AI answers him
        pure_stop = STOP_RE.search(text) and not ACTION_WORDS.search(STOP_RE.sub(" ", text)) and self.is_friend(speaker)
        if pure_stop and len(re.findall(r"\w+", text)) <= 6:
            self.agent.cancelled = True
            while not self.requests.empty():
                self.requests.get_nowait()
            if self.bot is not None:
                await self.run_tool("stop", {}, 0)
                self.send(self.bot, {"type": "cmd", "id": 0, "name": "close_container", "args": {}})
            # the body stopped at once (a reflex); what to say is his own. The AI must not pick the cancelled job up
            # again from the conversation history
            await self.requests.put(("event", speaker, "[%s сказал: «%s». Ты уже всё остановил, очередь очищена. Прежнее "
                                     "задание ОТМЕНЕНО — не продолжай его, пока снова не попросят. Откликнись коротко "
                                     "своими словами (или молча, ignore).]" % (speaker, text)))
            return
        # really talking to him: his name, or a conversation with him going on. The commander's microphone also carries
        # what he says to others in the room ("сюда мы берём") — no instant "Принял" and no rules from that
        talking = has_wake_word(text, self.cfg["wake_words"]) or time.time() < self.windows.get(speaker, 0)
        self.windows[speaker] = time.time() + self.cfg.get("conversation_window_sec", 20)
        if talking and speaker == self.owner and self.memory.rule_from(text):
            self.log("(запомнил правило командира) " + text)
        acked = False
        # the instant "Принял" is a promise: only to those whose orders he carries out (a stranger heard "Принял"
        # and then nothing happened); what to answer a stranger is the AI's own decision
        if self.cfg.get("instant_ack") and talking and self.is_friend(speaker) and ACTION_WORDS.search(text) \
                and not is_question(text) and self.tts is not None:
            await self.acknowledge(speaker)   # an order: answer at once, the AI will act (quietly) right after
            acked = True
        await self.requests.put(("user", speaker, text, acked))

    # ------------------------------------------------------------------ agent worker
    async def agent_loop(self):
        while True:
            item = await self.requests.get()
            kind, speaker, text = item[:3]
            acked = len(item) > 3 and item[3]
            self.speaker = speaker if kind == "user" else ""
            if kind == "user":
                targets = []
                heard = "" if self.is_friend(speaker) else " (он тебя слышит в голосовом чате)"
                prompt = "[%s (%s) говорит%s]: %s\n[Состояние] %s" % (speaker, self.role_of(speaker), heard, text,
                                                                   self.state_text())
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
                    # what he knows that fits the phrase; what to do with it is the AI's own decision
                    if where:
                        prompt += where
                    elif is_recipe_question(text):
                        prompt += await self.recipe_answer(text, targets)
                    elif targets:
                        prompt += ("\n[Справочник] Предметы из фразы: %s (как их сделать — recipe или plan)."
                                   % ", ".join("%s — %s" % (n, i) for i, n in targets))
                # the last half hour of this session is still in the model's history; recall only what is older
                # what is known about the speaker comes up too ("Вася любит строить")
                mem = self.memory.context_for("%s %s" % (speaker, text), session_start=max(self.memory.started, time.time() - 1800),
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
                self.log("(%s) %s" % (ui("думает о", "thinks about"), text.splitlines()[0][:200]))
                self.memory.log("событие", text.replace("[Событие]", "").split("\nЕсли цель")[0].split("\nСообщи")[0][:500])
                if self.goals and self.owner_away:
                    done = done_line(text)
                    if done:
                        self.done_log.append(done)
                prompt = "%s\n[Состояние] %s" % (text, self.state_text())
            goals = self.goals_text()
            if goals:
                prompt += "\n" + goals   # his long orders are in front of him at every turn
            prompt += "\n[Ты сейчас] " + self.feelings.text([speaker or self.owner, self.owner], self.agent_ru())
            self.busy = True
            # he thinks before every answer to a player (a few seconds): what to do is his decision
            think = kind == "user" and self.cfg.get("llm_think_user", True)
            try:
                await self.agent.run(prompt, kind, acked=acked, think=think)
            except Exception as e:
                self.log("Ошибка агента: %r" % e)
            finally:
                self.busy = False
                self.speaker = ""   # his own actions afterwards (events, help) are not the stranger's orders
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


async def warm_up(hub, cfg):
    """A first question to the AI with the whole instructions and tools: the model loads them (the first real answer is
    fast then), and the brain sees how much of the model's memory they take — with too little left for the talk every
    answer fails with "400 Bad Request"."""
    try:
        await hub.agent.llm.chat([{"role": "system", "content": hub.agent._system()},
                                  {"role": "user", "content": "[Проверка связи] Ответь одним словом: готов."}],
                                 tools=hub.agent._tools())
    except Exception as e:
        hub.log("Проверка ИИ не прошла: %s" % e)
        return
    used = str(hub.agent.llm.last_prompt_tokens).split(" ")[0]
    ctx = int(cfg.get("llm_context", 24576))
    if used.isdigit():
        hub.log(ui("Инструкции и инструменты занимают %s из %d токенов памяти ИИ.",
                   "The instructions and tools take %s of the AI's %d tokens of memory.") % (used, ctx))
        if int(used) > ctx * 0.7 and not llm_is_remote(cfg):
            hub.log(ui("ВНИМАНИЕ: на разговор почти не остаётся места — ответы будут падать. Увеличь \"llm_context\" "
                       "в config.json или выбери режим «Сбалансированный».",
                       "WARNING: almost no room is left for the talk — answers will fail. Raise \"llm_context\" in "
                       "config.json or choose the Balanced mode."))


def llm_is_remote(cfg):
    return bool(cfg.get("llm_url")) or cfg.get("llm_host", "127.0.0.1") not in ("127.0.0.1", "localhost", "")


def llm_headers(cfg):
    return {"Authorization": "Bearer " + cfg["llm_api_key"]} if cfg.get("llm_api_key") else {}


def start_llm(cfg, log):
    if cfg.get("llm_url"):
        log("Нейросеть в интернете: %s (модель %s)." % (cfg["llm_url"], cfg.get("llm_model_name") or "по умолчанию"))
        return None
    if llm_is_remote(cfg):
        # the neural network runs on the second PC (ai_server kit): nothing to start here
        log("Нейросеть на другом ПК: %s:%d" % (cfg["llm_host"], cfg["llm_port"]))
        return None
    exe = BRAIN_DIR.parent / "tools" / "llama" / "llama-server.exe"
    model = rel(cfg["llm_model"])
    url = "http://127.0.0.1:%d/health" % cfg["llm_port"]
    try:
        if httpx.get(url, timeout=1, trust_env=False).status_code == 200:
            log(ui("ИИ-сервер уже запущен.", "The AI server is already running."))
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
    log(ui("Запускаю ИИ (%s)...", "Starting the AI (%s)...") % model.name)
    return subprocess.Popen(args, stdout=logf, stderr=subprocess.STDOUT)


async def wait_llm(cfg, log):
    if cfg.get("llm_url"):
        # an online service: one check that the address and the key work
        async with httpx.AsyncClient(headers=llm_headers(cfg)) as c:
            try:
                r = await c.get(cfg["llm_url"].rstrip("/") + "/models", timeout=20)
            except Exception as e:
                log("Нейросеть в интернете не отвечает (%s): проверь llm_url и интернет." % e)
                return False
        if r.status_code in (401, 403):
            log("Нейросеть в интернете не пустила: проверь llm_api_key в config.json.")
            return False
        log("ИИ готов (в интернете).")
        return True
    url = "http://%s:%d/health" % (cfg.get("llm_host", "127.0.0.1"), cfg["llm_port"])
    remote = llm_is_remote(cfg)
    # the AI server (here or on the second PC): never through a Windows proxy
    async with httpx.AsyncClient(trust_env=False, headers=llm_headers(cfg)) as c:
        for i in range(600 if remote else 300):
            try:
                r = await c.get(url, timeout=3)
                if r.status_code == 200:
                    log(ui("ИИ готов%s.", "AI ready%s.") % (ui(" (на другом ПК)", " (on the second PC)") if remote else ""))
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
    try:
        last = json.loads(LAST_LAUNCH.read_text(encoding="utf-8"))
    except Exception:
        last = {}
    ask = sys.stdin is not None and sys.stdin.isatty()
    if args.pack:
        cfg["pack_version"] = args.pack
    if not cfg.get("game_pc_mode"):
        # any Forge 1.20.1 modpack: the Minecraft folder, the pack and Java are found when config.json leaves them empty
        resolve_install(cfg, "" if args.pack else last.get("pack", ""), ask and not args.pack)
    if args.pack:
        # another modpack of the same Minecraft/Forge: his body takes that pack's mods (plus Altron's own)
        # from its own folder, so the usual pack is not touched; the encyclopedia is read from that pack
        if cfg["pack_version"] != args.pack:
            raise SystemExit("Нет такой сборки: %s" % args.pack)
        cfg["bot_dir"] = "../bot_" + (re.sub(r"[^A-Za-z0-9]+", "_", args.pack).strip("_")[:24] or "other")
        cfg["other_pack"] = True

    if cfg.get("game_pc_mode"):
        # Altron runs on the second PC (the ai_server kit): the only question is where the commander's game is
        game = args.server
        if game is None and ask:
            print(ui("Где твоя игра? Введи адрес игрового ПК: IP из Radmin VPN (26.x.x.x) или домашней сети (192.168.x.x).",
                     "Where is your game? Enter the gaming PC's address: its Radmin VPN IP (26.x.x.x) or home network IP (192.168.x.x)."))
            if last.get("game"):
                print(ui("  «=» или Enter — прошлый: %s", "  \"=\" or Enter — the last one: %s") % last["game"])
            answer = input(ui("Адрес игрового ПК: ", "Gaming PC address: ")).strip()
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
        print(ui("Режим работы Альтрона (меняется только его клиент и ИИ, твоя игра не трогается):",
                 "Altron's mode (only his own client and the AI change, your game is not touched):"))
        for i, (key, p) in enumerate(PROFILES.items(), 1):
            print("  %d — %s%s\n      %s" % (i, ui(p["title"], p["title_en"]), ui("  <- прошлый", "  <- last")
                                              if key == profile else "", ui(p["about"], p["about_en"])))
        answer = input(ui("Режим [1/2/3, Enter — прошлый]: ", "Mode [1/2/3, Enter — the last one]: ")).strip()
        profile = answer or profile
    profile = apply_profile(cfg, profile)

    server = args.server
    if server is None and ask:
        print(ui("\nКуда пустить Альтрона?", "\nWhere should Altron go?"))
        print(ui("  Enter — в свой мир: зайди в него и напиши в чате /altron",
                 "  Enter — into your own world: open it and type /altron in chat"))
        print(ui("  или адрес сервера из Radmin VPN: IP:порт, например 26.12.34.56:25565",
                 "  or a server address (e.g. Radmin VPN): IP:port, like 26.12.34.56:25565"))
        print(ui("  (хозяин мира открывает его для Альтрона командой /altron lan и называет порт)",
                 "  (the world's host opens it for Altron with /altron lan and tells the port)"))
        if last.get("server"):
            print(ui("  «=» — прошлый сервер %s", "  \"=\" — the last server %s") % last["server"])
        answer = input(ui("Адрес: ", "Address: ")).strip()
        server = last.get("server") if answer == "=" else answer
    server = server_address(server) if server else ""

    owner = args.owner or last.get("owner") or cfg.get("owner") or ""
    if server and ask and not args.owner:
        answer = input(ui("Твой ник в игре (командир)%s: ", "Your in-game name (the commander)%s: ")
                       % (" [Enter — %s]" % owner if owner else "")).strip()
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
                                           "owner": owner, "ai": ai or last.get("ai", ""),
                                           "pack": last.get("pack", "") if args.pack else cfg["pack_version"]},
                                          ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass
    return profile, server, owner


async def main():
    cfg = json.loads((BRAIN_DIR / "config.json").read_text(encoding="utf-8"))
    set_ui(primary_language(cfg))
    profile, server, owner = choose_launch(cfg, sys.argv[1:])
    hub = Hub(cfg)
    hub.loop = asyncio.get_running_loop()
    print("=" * 60)
    print(ui(" АЛЬТРОН — ИИ-напарник для Minecraft. Режим: %s", " ALTRON — an AI companion for Minecraft. Mode: %s")
          % ui(PROFILES[profile]["title"], PROFILES[profile]["title_en"]))
    if cfg.get("llm_url"):
        print(ui(" Нейросеть в интернете: %s", " Online AI: %s") % cfg["llm_url"])
    elif llm_is_remote(cfg):
        print(ui(" Нейросеть на втором ПК: %s:%d", " The AI runs on the second PC: %s:%d") % (cfg["llm_host"], cfg["llm_port"]))
    if cfg.get("game_pc"):
        print(ui(" Альтрон работает на этом ПК, игра — на %s.", " Altron runs on this PC, the game on %s.") % cfg["game_pc"])
        print(ui(" На игровом ПК: зайди в свой мир и напиши в чате /altron — Альтрон придёт сам.",
                 " On the gaming PC: open your world and type /altron in chat — Altron comes by himself."))
    elif server:
        print(ui(" Альтрон заходит на сервер %s сам (1-2 минуты).", " Altron joins the server %s by himself (1-2 minutes).") % server)
        print(ui(" Командир: %s. Говори в Voice Chat рядом с ним: «Альтрон, иди за мной»",
                 " Commander: %s. Talk in voice chat next to him: \"Altron, follow me\"")
              % (owner or ui("первый, кто позовёт", "whoever calls him first")))
    else:
        print(ui(" 1) Запусти сборку «%s» в своём лаунчере и зайди в свой мир",
                 " 1) Start the modpack \"%s\" in your launcher and open your world") % cfg["pack_version"])
        print(ui(" 2) Напиши в чате игры: /altron", " 2) Type in the game chat: /altron"))
        print(ui(" 3) Говори в Voice Chat: «Альтрон, иди за мной»", " 3) Talk in voice chat: \"Altron, follow me\""))
    print(ui(" Здесь можно печатать команды текстом. /quit — выход.", " You can type orders here too. /quit — exit."))
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
        hub.log(ui("Справочник по сборке готов: %d рецептов.", "The pack reference is ready: %d recipes.") % len(hub.knowledge.recipes))
        hub.log(ui("Загружаю распознавание речи и голос...", "Loading speech recognition and the voice..."))
        hub.tts = TTS(cfg, persona.voice_settings(cfg, hub.persona))
        hub.stt = STT(cfg, hub.log)
        hub.log(ui("Слух и голос готовы (распознавание речи: %s).", "Hearing and voice are ready (speech recognition: %s).") % hub.stt.device)

    loader = threading.Thread(target=load_models, daemon=True)
    loader.start()
    listener = await listen(hub, cfg["brain_port"])
    hub.log(ui("Мозг слушает порт %d.", "The brain listens on port %d.") % cfg["brain_port"])
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
    await warm_up(hub, cfg)
    try:
        async with listener:
            await asyncio.gather(listener.serve_forever(), hub.voice_loop(), hub.agent_loop(), hub.life_loop(),
                                 hub.observe_loop())
    finally:
        hub.stop_bot()
        if llm_proc is not None:
            llm_proc.terminate()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
