"""Tests of Altron's brain that need neither Minecraft nor the AI: python -m unittest discover -s brain/tests"""
import asyncio
import base64
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest

import httpx
import numpy as np

BRAIN = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRAIN))

import agent  # noqa: E402
import agent_en  # noqa: E402
import altron  # noqa: E402
import dataset  # noqa: E402
import feelings  # noqa: E402
import field_test  # noqa: E402
import knowledge  # noqa: E402
import lang  # noqa: E402
import launcher  # noqa: E402
import memory  # noqa: E402
import persona  # noqa: E402
import speech  # noqa: E402
import structures  # noqa: E402


# One event loop for all the tests: on Python 3.9 (the brain's own venv) the hub's queues belong to the loop that was
# current when the hub was made, so asyncio.run with a fresh loop per test would break them
LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(LOOP)


def run(coro):
    return LOOP.run_until_complete(coro)


def make_hub(**cfg_over):
    cfg = json.loads((BRAIN / "config.json").read_text(encoding="utf-8"))
    cfg["memory_dir"] = tempfile.mkdtemp()
    cfg["brain_log"] = ""   # tests do not write into brain/logs/brain.log
    cfg.update(cfg_over)
    hub = altron.Hub(cfg)
    hub.owner = "Egor"
    return hub


class Languages(unittest.TestCase):
    def test_guess_by_alphabet(self):
        self.assertEqual(lang.guess_lang("иди за мной", ["ru", "en"], "ru"), "ru")
        self.assertEqual(lang.guess_lang("follow me", ["ru", "en"], "ru"), "en")
        self.assertEqual(lang.guess_lang("іди сюди", ["ru", "uk", "en"], "ru"), "uk")
        self.assertEqual(lang.guess_lang("123", ["ru", "en"], "ru"), "ru")

    def test_phrases_fall_back_to_english(self):
        self.assertEqual(lang.phrase("commander", "de"), "Kommandant")
        self.assertEqual(lang.phrase("creeper", "ja"), lang.PHRASES["creeper"]["en"])

    def test_the_code_says_almost_nothing_by_itself(self):
        # everything he says is the AI's own words; the code keeps only the creeper reflex and the opt-in instant "yes"
        self.assertEqual(set(lang.PHRASES), {"acks", "creeper", "commander"})

    def test_window_language(self):
        lang.set_ui("en")
        self.assertEqual(lang.ui("да", "yes"), "yes")
        lang.set_ui("uk")
        self.assertEqual(lang.ui("да", "yes"), "да")


class Orders(unittest.TestCase):
    def test_orders_and_questions(self):
        self.assertTrue(agent.ACTION_WORDS.search("Altron, follow me"))
        self.assertTrue(agent.ACTION_WORDS.search("иди за мной"))
        self.assertFalse(agent.ACTION_WORDS.search("gold is nice"))   # "go" is a whole word only
        self.assertTrue(agent.is_question("where is my chest?"))
        self.assertFalse(agent.is_question("go get me gold"))

    def test_english_tools_cover_every_tool(self):
        names = {t["function"]["name"] for t in agent.TOOLS}
        self.assertEqual(names, set(agent_en.TOOLS_EN))
        en = agent.tools_for("en")
        self.assertIs(en, agent.tools_for("de"))        # everyone but the Russian family thinks in English
        self.assertIs(agent.tools_for("ru"), agent.TOOLS)
        self.assertTrue(en[0]["function"]["description"].startswith("Only answer"))

    def test_system_prompt_per_language(self):
        class K:
            mods = {"tacz": {}, "securitycraft": {}}

            def mods_line(self):
                return "TaCZ, SecurityCraft"

        class H:
            owner, lang, knowledge = "Bob", "en", K()

        a = agent.Agent.__new__(agent.Agent)
        a.cfg, a.hub = {"bot_name": "altron", "languages": ["en", "ru"]}, H()
        en = a._system()
        self.assertIn("You are Altron", en)
        self.assertIn("TaCZ: guns", en)                  # a hint for an installed mod
        self.assertNotIn("SuperbWarfare:", en)           # not for a missing one
        H.lang = "ru"
        self.assertEqual(a._system(), en)                # the commander's language does not rewrite the rules
        H.persona = "teammate"
        self.assertIn("teammate", a._system())           # the manner of speaking is the persona's
        self.assertNotIn("theatrical", a._system())
        H.persona = "altron"
        b = agent.Agent.__new__(agent.Agent)
        b.cfg, b.hub = {"bot_name": "altron", "languages": ["ru", "en"]}, H()
        self.assertIn("Ты — Альтрон", b._system())


class Buildings(unittest.TestCase):
    def test_every_kind_has_a_plan(self):
        for kind in structures.DEFAULTS:
            args = structures.plan_args({"kind": kind})
            self.assertTrue(args["blocks"], kind)
            self.assertTrue(all(b[3].startswith("minecraft:") for b in args["blocks"]))

    def test_house_has_a_door_and_its_own_roof(self):
        h = structures.plan("house", 7, 7, 4, "minecraft:cobblestone", "minecraft:oak_planks")
        cells = {(x, y, z): b for x, y, z, b in h}
        self.assertNotIn((3, 1, 0), cells)               # the door
        self.assertEqual(cells[(0, 5, 0)], "minecraft:oak_planks")
        self.assertEqual(cells[(0, 0, 0)], "minecraft:cobblestone")

    def test_limits_and_unknown_kinds(self):
        big = structures.plan_args({"kind": "platform", "width": 500, "length": 500})
        self.assertLessEqual(len(big["blocks"]), 32 * 32)
        with self.assertRaises(ValueError):
            structures.plan_args({"kind": "castle"})


class Planner(unittest.TestCase):
    def setUp(self):
        k = knowledge.Knowledge()
        for i, kind in [("thermal:machine_pulverizer", "block"), ("thermal:iron_dust", "item"),
                        ("minecraft:raw_iron", "item")]:
            k.entries[i] = {"id": i, "kind": kind, "mod": i.split(":")[0], "ru": "", "en": i, "desc": []}
        k.recipes = [{"id": "thermal:pulv_iron", "type": "thermal:pulverizer", "in": [["minecraft:raw_iron", 1]],
                      "out": [["thermal:iron_dust", 2]]}]
        self.k = k

    def test_a_seen_machine_of_any_mod_is_used(self):
        _, steps, unresolved = self.k.acquire("thermal:iron_dust", 4, {"minecraft:raw_iron": 2},
                                              stations={"thermal:machine_pulverizer"})
        self.assertEqual(unresolved, [])
        self.assertEqual(steps[0][0], "machine")
        self.assertEqual(steps[0][1]["machines"], ["thermal:machine_pulverizer"])

    def test_an_unseen_machine_is_asked_for(self):
        _, steps, unresolved = self.k.acquire("thermal:iron_dust", 4, {"minecraft:raw_iron": 2}, stations=set())
        self.assertEqual(steps, [])
        self.assertTrue(unresolved)


class Install(unittest.TestCase):
    def test_finds_minecraft_pack_and_java(self):
        mc = pathlib.Path(tempfile.mkdtemp()) / ".minecraft"
        for name in ("A-pack", "B-pack"):
            d = mc / "versions" / name
            (d / "mods").mkdir(parents=True)
            (d / (name + ".json")).write_text("{}")
        (mc / "versions" / "B-pack" / "saves" / "My World").mkdir(parents=True)
        java = mc / "runtime/java-runtime-gamma/linux/java-runtime-gamma/bin"
        java.mkdir(parents=True)
        (java / "java").write_text("")
        old = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(mc.parent)
        try:
            cfg = {"minecraft_dir": "", "pack_version": "", "java": ""}
            self.assertEqual(launcher.resolve_install(dict(cfg)), "A-pack")
            self.assertEqual(launcher.resolve_install(dict(cfg), world="My World"), "B-pack")
            done = dict(cfg)
            launcher.resolve_install(done)
            self.assertTrue(done["java"].endswith("java"))
        finally:
            if old is None:
                os.environ.pop("APPDATA", None)
            else:
                os.environ["APPDATA"] = old


class Voice(unittest.TestCase):
    def test_styles_and_moods(self):
        t = speech.TTS.__new__(speech.TTS)
        t.pitch, t.comb, t.chorus, t.drive, t.hall = speech.TTS.STYLES["ultron"]
        t.speed, t.moods, t.band = 1.0, True, None
        configs = []
        t._config = lambda **k: configs.append(k) or k

        class Chunk:
            sample_rate = 22050
            audio_int16_array = (np.sin(np.arange(22050) / 10) * 8000).astype(np.int16)

        class Voice_:
            def synthesize(self, text, syn):
                yield Chunk()

        t._voice = lambda _lang: Voice_()
        calm = b"".join(t.synth("Test.", None, None))
        alert = b"".join(t.synth("Test.", None, "alert"))
        self.assertTrue(np.isfinite(np.frombuffer(calm, "<i2")).all())
        self.assertLess(configs[1]["length_scale"], configs[0]["length_scale"])   # faster when alert
        self.assertGreater(len(calm), 0)
        self.assertGreater(len(alert), 0)
        t.cfg = {"tts_voice": "none.onnx", "tts_speed": 1.0}
        t.use(persona.voice_settings({"tts_voices": {"ru": "none.onnx"}}, "teammate"))
        self.assertEqual((t.style, t.pitch, t.band), ("synth", 1.0, (300, 5000)))   # the plain, narrow synthesizer
        self.assertEqual(t.paths, {})                     # a voice file that is not there: the common voice is used
        synth = np.frombuffer(b"".join(t.synth("Test.", None, "cold")), "<i2")
        self.assertTrue(len(synth) and np.isfinite(synth).all())

    def test_personas(self):
        self.assertEqual(persona.find("говори как тиммейт"), "teammate")
        self.assertEqual(persona.find("верни обычный голос"), "altron")
        cfg = {"tts_style": "robot", "tts_voices": {"ru": "a.onnx"}, "tts_personas": {"teammate": {"voices": {"ru": "b.onnx"}}}}
        self.assertEqual(persona.voice_settings(cfg, "altron")["style"], "robot")    # config.json's own style stays
        self.assertEqual(persona.voice_settings(cfg, "teammate")["voices"]["ru"], "b.onnx")


class Streaming(unittest.TestCase):
    def _llm(self, chunks):
        def handler(req):
            body = "".join("data: %s\n\n" % json.dumps(c) for c in chunks) + "data: [DONE]\n\n"
            return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})
        llm = agent.LLM({"llm_port": 1})
        llm.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return llm

    @staticmethod
    def _text(t):
        return [{"choices": [{"delta": {"content": t[i:i + 5]}}]} for i in range(0, len(t), 5)]

    def test_sentences_are_spoken_while_written(self):
        said = []

        async def cb(s):
            said.append(s)
        llm = self._llm(self._text("The sun is going down. Humans fear the dark. Shall I place a bed?"))
        reply = run(llm.chat([{"role": "user", "content": "x"}], on_sentence=cb))
        self.assertEqual(said, ["The sun is going down.", "Humans fear the dark."])
        self.assertEqual(reply["spoken"], "The sun is going down. Humans fear the dark.")

    def test_words_with_a_tool_call_are_not_spoken(self):
        said = []

        async def cb(s):
            said.append(s)
        chunks = self._text("On it, doing that now. ") + [
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "follow", "arguments": "{}"}}]}}]}]
        reply = run(self._llm(chunks).chat([{"role": "user", "content": "x"}], on_sentence=cb))
        self.assertEqual(said, [])
        self.assertEqual(reply["tool_calls"][0]["function"]["name"], "follow")

    def test_a_reply_is_said_before_the_action_is_written(self):
        said = []

        async def cb(s):
            said.append(s)
        args = '{"text": "Иду, командир."}'
        chunks = [{"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "r1", "function": {"name": "reply", "arguments": ""}}]}}]}]
        chunks += [{"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": args[i:i + 4]}}]}}]}
                   for i in range(0, len(args), 4)]
        chunks += [{"choices": [{"delta": {"tool_calls": [{"index": 1, "id": "f1", "function": {"name": "follow", "arguments": "{}"}}]}}]}]
        reply = run(self._llm(chunks).chat([{"role": "user", "content": "x"}], on_sentence=cb))
        self.assertEqual(said, ["Иду, командир."])
        self.assertEqual(reply["spoken_calls"], ["r1"])
        self.assertEqual([c["function"]["name"] for c in reply["tool_calls"]], ["reply", "follow"])

    def test_calls_the_server_did_not_parse(self):
        xml = ('Хорошо, <tool_call>\n<function=explore>\n<parameter=radius>\n50\n</parameter>\n'
               '<parameter=blocks>\nminecraft:oak_log\n</parameter>\n</function>\n</tool_call>')
        calls = agent._parse_inline_tool_calls(xml)
        self.assertEqual(calls[0]["function"]["name"], "explore")
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {"radius": 50, "blocks": "minecraft:oak_log"})
        js = '<tool_call>{"name": "follow", "arguments": {}}</tool_call>'
        self.assertEqual(agent._parse_inline_tool_calls(js)[0]["function"]["name"], "follow")
        self.assertEqual(agent._strip_inline_calls("Пока ты отсутствовал, <tool_call><function=exp"), "Пока ты отсутствовал,")

        def handler(req):
            return httpx.Response(200, json={"choices": [{"message": {"content": "Хорошо, <tool_call><function=exp"}}]})
        llm = agent.LLM({"llm_port": 1})
        llm.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.assertEqual(run(llm.chat([{"role": "user", "content": "x"}]))["content"], "")   # an unfinished call is not said

    def test_online_service_gets_no_llama_fields(self):
        seen = {}

        def handler(req):
            seen.update(json.loads(req.content))
            return httpx.Response(200, json={"choices": [{"message": {"content": "Hi."}}]})
        llm = agent.LLM({"llm_url": "https://api.example.com/v1", "llm_model_name": "m", "llm_port": 1})
        llm.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        run(llm.chat([{"role": "user", "content": "x"}]))
        self.assertEqual(seen["model"], "m")
        self.assertNotIn("chat_template_kwargs", seen)


class Companion(unittest.TestCase):
    def test_strangers_cannot_give_orders(self):
        hub = make_hub()
        hub.bot = object()

        async def start_task(name, args, wait):
            return "ГОТОВО: " + name
        hub.start_task = start_task

        async def go():
            hub.speaker = "Petya"
            refused = await hub.run_tool("follow", {}, 0)
            hub.speaker = "Egor"
            await hub.run_tool("friends", {"action": "add", "player": "Petya"}, 0)
            hub.speaker = "Petya"
            allowed = await hub.run_tool("follow", {}, 0)
            return refused, allowed
        refused, allowed = run(go())
        self.assertTrue(refused.startswith("ОТКАЗ"))
        self.assertEqual(allowed, "ГОТОВО: follow")
        self.assertIn("petya", make_hub(memory_dir=hub.cfg["memory_dir"]).friends)   # kept between launches

    def test_a_failed_building_is_reported(self):
        hub = make_hub()
        hub.bot = object()
        hub.agent_tasks.add(7)
        hub.running, hub.running_args = (7, "build_plan"), {"blocks": [[0, 0, 0, "minecraft:stone"]], "what": "стену"}
        run(hub.on_event({"event": "task_failed", "task_id": 7, "task": "build_plan",
                          "msg": "для постройки стену не хватает: 20x minecraft:cobblestone"}))
        self.assertIn("не хватает", run(hub.requests.get())[2])   # the AI hears it and can tell the commander

    def test_death_becomes_his_goal(self):
        hub = make_hub()
        hub.joined, hub.bot = True, object()
        run(hub.after_death([10, 64, -16], ""))
        self.assertIn("10 64 -16", hub.goals_text())          # the goal is in front of the AI at every turn
        self.assertIn("реши сам", run(hub.requests.get())[2])  # how to get the things back is its own decision

    def test_goals(self):
        hub = make_hub()
        r = hub.goal_tool({"action": "add", "text": "Охранять базу у 100 64 200 от мобов и чужих"})
        self.assertIn("№1", r)
        hub.goal_tool({"action": "add", "text": "Поднимать раненых друзей", "minutes": 30})
        self.assertIn("Охранять базу", hub.goals_text())
        self.assertIn("осталось", hub.goals_text())
        self.assertIn("убрал", hub.goal_tool({"action": "done", "text": "охранять базу"}))
        self.assertNotIn("Охранять базу", hub.goals_text())
        again = make_hub(memory_dir=hub.cfg["memory_dir"])
        again.memory.world = hub.memory.world
        again.load_goals()
        self.assertIn("Поднимать раненых", again.goals_text())   # kept for the world between launches

    def test_names_said_aloud_match_nicks(self):
        self.assertEqual(altron.nick_key("Вася"), altron.nick_key("Vasya"))
        hub = make_hub()
        hub.friends.add("вася")
        self.assertTrue(hub.is_friend("Vasya"))
        self.assertTrue(hub.is_friend("Vasya_2010"))
        self.assertFalse(hub.is_friend("Petya"))

    def test_no_promise_to_a_stranger(self):
        hub = make_hub(instant_ack=True)
        acks = []

        async def acknowledge(speaker=""):
            acks.append(speaker)
        hub.acknowledge, hub.tts = acknowledge, object()
        run(hub.handle_phrase("Petya", "Альтрон, иди за мной"))
        self.assertEqual(acks, [])                          # no "Принял" for an order he will not carry out
        self.assertEqual(hub.requests.qsize(), 1)           # but the AI hears him and answers in its own words
        run(hub.handle_phrase("Egor", "Альтрон, иди за мной"))
        self.assertEqual(acks, ["Egor"])

    def test_his_own_words_by_default(self):
        hub = make_hub()
        acks = []

        async def acknowledge(speaker=""):
            acks.append(speaker)
        hub.acknowledge, hub.tts = acknowledge, object()
        run(hub.handle_phrase("Egor", "Альтрон, иди за мной"))
        self.assertEqual(acks, [])                         # no canned "Есть, командир": the AI answers itself
        self.assertEqual(hub.requests.qsize(), 1)

    def test_tags_and_half_phrases_are_not_said(self):
        hub = make_hub()
        spoken = []

        class Tts:
            def synth(self, text, lang=None, mood=None):
                spoken.append(text)
                yield b"\x00\x00"
        hub.tts, hub.host = Tts(), type("W", (), {"write": lambda self, b: None, "drain": lambda self: asyncio.sleep(0)})()
        run(hub.say("[Наблюдение] Командир смотрит на снег."))
        self.assertEqual(spoken, ["Командир смотрит на снег."])

    def test_stop_is_a_reflex_and_the_words_are_his(self):
        hub = make_hub()
        hub.bot = object()
        said, tools = [], []

        async def say(text, mood=None):
            said.append(text)

        async def run_tool(name, args, wait):
            tools.append(name)
            return "ok"
        hub.say, hub.run_tool, hub.send = say, run_tool, lambda *a: True
        run(hub.handle_phrase("Egor", "стоп"))
        self.assertEqual(tools, ["stop"])                  # the body stops at once
        self.assertEqual(said, [])                         # but no canned "Остановился"
        self.assertIn("ОТМЕНЕНО", run(hub.requests.get())[2])

    def test_world_events(self):
        hub = make_hub(assist=True)
        hub.joined, hub.bot = True, object()
        said, tools = [], []

        async def say(text, mood=None):
            said.append((text, mood))

        async def run_tool(name, args, wait):
            tools.append(name)
            return "ok"
        hub.say, hub.run_tool = say, run_tool

        async def go():
            await hub.on_world_event({"kind": "danger", "who": "Egor", "what": "creeper"})
            await hub.on_world_event({"kind": "player_died", "who": "Egor", "text": "fell", "pos": [1, 2, 3]})
            await hub.on_world_event({"kind": "advancement", "who": "Stranger", "title": "x"})
            await hub.on_world_event({"kind": "downed", "who": "Egor", "pos": [4, 5, 6], "seconds": 60})
            return [(await hub.requests.get())[2] for _ in range(hub.requests.qsize())]
        events = run(go())
        self.assertEqual(said[0][1], "alert")               # the creeper: a reflex, said at once
        self.assertEqual(tools, [])                         # nothing is done without the AI deciding
        self.assertEqual(len(events), 2)                    # a stranger's advancement is not news
        self.assertIn("Командир погиб", events[0])
        self.assertIn("4 5 6", events[1])                   # where the downed commander lies: the AI decides
        self.assertNotIn("give", events[0] + events[1])     # facts, not orders: no "give him food" written for it
        self.assertIn("Egor погиб", hub.memory.moments[0]["text"])   # a moment of their life together

    def test_the_commander_can_interrupt(self):
        hub = make_hub()
        sent = []

        class Writer:
            def write(self, b):
                sent.append(json.loads(b.decode())["type"])

            async def drain(self):
                pass

        class Tts:
            def synth(self, text, lang=None, mood=None):
                for _ in range(3):
                    yield b"\x00\x00" * 48000

        hub.host, hub.tts = Writer(), Tts()

        async def go():
            task = asyncio.create_task(hub.say("One. Two. Three."))
            await asyncio.sleep(0.05)
            loud = base64.b64encode((np.ones(960) * 3000).astype("<i2").tobytes()).decode()
            for _ in range(30):
                hub.on_voice({"uuid": "u", "name": "Egor", "pcm": loud})
                await asyncio.sleep(0.02)
            await task
        run(go())
        self.assertEqual(sent[-1], "speak_stop")

    def test_reminders_and_done_lines(self):
        hub = make_hub()

        async def go():
            answer = hub.remind({"minutes": 5, "text": "check the furnace"})
            waiting = len(hub.reminders)
            for t in hub.reminders:
                t.cancel()
            return answer, waiting
        answer, waiting = run(go())
        self.assertIn("5", answer)
        self.assertEqual(waiting, 1)
        self.assertEqual(altron.done_line("[Событие] Результаты:\n- mine завершена: добыл 12 угля\nЕсли цель ..."),
                         "mine завершена: добыл 12 угля")


class InnerLife(unittest.TestCase):
    def test_mood_is_his_own_and_fades(self):
        d = tempfile.mkdtemp()
        f = feelings.Feelings(d)
        self.assertIn("ОШИБКА", f.feel("hangry"))
        f.feel("sad", "погиб в лаве")
        self.assertEqual(f.voice_mood(), "sad")               # the voice follows the mood
        self.assertIn("погиб в лаве", f.text(["Egor"]))
        self.assertEqual(feelings.Feelings(d).current(), "sad")  # kept between launches
        f.since -= feelings.FADE_SEC + 1
        self.assertEqual(f.current(), "calm")                  # a mood without a new reason passes

    def test_attitude_builds_up(self):
        f = feelings.Feelings(tempfile.mkdtemp())
        f.relate("Vasya", 3, "подарил алмазы")
        f.relate("Vasya", 2, "спас от крипера")
        self.assertIn("тепло", f.about("vasya"))
        self.assertIn("спас от крипера", f.text(["Vasya"]))
        f.relate("Petya", -9, "ударил")
        self.assertEqual(f.relations["petya"]["score"], -3)    # one deed moves it by 3 at most
        self.assertIn("wary", f.about("Petya", ru=False))

    def test_moments_come_back(self):
        m = memory.LongMemory(tempfile.mkdtemp())
        m.add_moment("Крипер снёс наш первый дом у озера")
        self.assertIn("уже есть", m.add_moment("крипер снёс наш первый дом у озера"))
        self.assertIsNone(m.old_moment(0))                     # nothing is old enough yet
        self.assertIn("Крипер снёс", m.old_moment(time.time() + 1))
        self.assertTrue(any("Крипер" in line for line in m.search("помнишь дом у озера?")))

    def test_quiet_moments_are_a_chance_to_talk(self):
        hub = make_hub(observe_tick_sec=0.01, idle_think_minutes=0.01)
        hub.joined, hub.bot = True, object()
        hub.state = {"pos": [0, 64, 0], "owner_pos": [3, 64, 0]}
        hub.last_talk = time.time() - 600
        hub.last_question = ("Командир, построить мост?", time.time() - 120)

        async def bot_call(name, args):
            return {"ok": True, "msg": "- игрок: Vasya (uuid1), 5 бл.\nБлоки рядом: -"}
        hub.bot_call = bot_call

        async def go():
            task = asyncio.create_task(hub.observe_loop())
            item = await asyncio.wait_for(hub.requests.get(), 5)
            task.cancel()
            return item[2]
        text = run(go())
        self.assertTrue(text.startswith("[Наблюдение]"))
        self.assertIn("Vasya", text)                           # a player came up: news
        self.assertIn("построить мост", text)                  # his question nobody answered
        self.assertIn("ignore", text)                          # talking is a chance, not an order

    def test_persona_switch(self):
        hub = make_hub()

        class Tts:
            paths = {}

            def use(self, settings):
                self.settings = settings
        hub.tts = Tts()
        self.assertIn("ОШИБКА", hub.set_persona("клоун"))
        r = hub.set_persona("teammate")
        self.assertEqual(hub.persona, "teammate")
        self.assertEqual(hub.tts.settings["style"], "synth")
        self.assertIn("нет", r)                                # its voice files are not downloaded here
        self.assertEqual(make_hub(memory_dir=hub.cfg["memory_dir"]).persona, "teammate")   # remembered


class Learning(unittest.TestCase):
    def test_turns_ratings_and_export(self):
        d = tempfile.mkdtemp()
        log = dataset.DatasetLog(d)
        turn = [{"role": "user", "content": "иди за мной"},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "follow", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "c1", "content": "иду за Egor"}]
        before = [{"role": "tool", "tool_call_id": "x", "content": "old"}, {"role": "user", "content": "привет"},
                  {"role": "assistant", "content": "Привет."}]
        t1 = log.turn("SYSTEM", [{"type": "function"}], before, turn, "user", "ru", "altron")
        log.turn("SYSTEM", [{"type": "function"}], [], [{"role": "user", "content": "x"},
                 {"role": "assistant", "content": "", "tool_calls": [{"id": "c2", "function": {"name": "mine", "arguments": "{}"}}]},
                 {"role": "tool", "tool_call_id": "c2", "content": "ОШИБКА: нет кирки"}])
        log.rate(False, "не то")                                # "не так" is about the last turn
        log.rate(True, "", t1)
        out = os.path.join(d, "train.jsonl")
        self.assertEqual(dataset.export(d, out), 1)            # only the good one
        with open(out, encoding="utf-8") as f:
            ex = json.loads(f.read())
        self.assertEqual(ex["messages"][0], {"role": "system", "content": "SYSTEM"})
        self.assertEqual(ex["messages"][1]["content"], "привет")   # the context starts at a user message
        self.assertEqual(ex["messages"][4]["tool_calls"][0]["function"]["arguments"], {})
        self.assertIn("хороших: 1, плохих: 1", dataset.stats(d))

    def test_a_turn_speaks_its_reply_once_and_is_kept(self):
        replies = [
            {"role": "assistant", "content": "", "spoken_calls": ["r1"], "tool_calls": [
                {"id": "r1", "type": "function", "function": {"name": "reply", "arguments": '{"text": "Иду."}'}},
                {"id": "f1", "type": "function", "function": {"name": "follow", "arguments": "{}"}}]},
            {"role": "assistant", "content": ""}]

        class Llm:
            last_prompt_tokens = "10"

            async def chat(self, messages, **kw):
                return dict(replies.pop(0))

        said, ran = [], []

        class Hub:
            owner, lang, knowledge, persona = "Egor", "ru", None, "altron"
            cut_speech = False
            dataset = dataset.DatasetLog(tempfile.mkdtemp())

            async def say(self, text, mood=None):
                said.append(text)

            async def run_tool(self, name, args, wait):
                ran.append(name)
                return "иду за Egor"

            def log(self, text):
                pass

            def note_question(self, q):
                return True
        a = agent.Agent({"bot_name": "altron", "languages": ["ru"], "llm_port": 1}, Hub())
        a.llm = Llm()
        run(a.run("[Egor (командир) говорит]: где мой сундук? и иди за мной", "user"))
        self.assertEqual(said, [])                  # "Иду." was already said while streamed: not twice
        self.assertEqual(ran, ["follow"])           # a phrase with a question in it may still lead to action
        turns, _ = dataset.load(Hub.dataset.dir)
        self.assertEqual(len(turns), 1)
        self.assertEqual([m["role"] for m in list(turns.values())[0]["turn"]], ["user", "assistant", "tool", "tool", "assistant"])


class FieldTestDryRun(unittest.TestCase):
    """The field test itself, run against a pretend brain: every part and the report, without the game."""

    def test_every_part_and_the_report(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        log_path = tmp / "brain.log"

        class Clock:
            now = 1000.0

            def time(self):
                return self.now

            def sleep(self, s):
                self.now += max(s, 0.05)

            def strftime(self, *a):
                return time.strftime(*a)
        clock = Clock()

        class FakeBrain(field_test.Brain):
            def __init__(self):
                self.bot_pos, self.host_pos = [0.0, 64.0, 0.0], [3.0, 64.0, 0.0]
                self.persona, self.goals, self.running = "altron", [], None

            def write(self, *lines):
                with open(log_path, "a", encoding="utf-8") as f:
                    for ln in lines:
                        f.write("2026-01-01 12:00:00 " + ln + "\n")

            def call(self, msg, timeout=300):
                t = msg["type"]
                if t == "state":
                    return {"busy": False, "requests": 0, "speaking": False, "macro": False, "running": self.running,
                            "joined": True, "state": {"pos": self.bot_pos}, "persona": self.persona,
                            "goals": self.goals, "legs": "own"}
                if t == "say":
                    text = msg["text"]
                    if "тиммейт" in text:
                        self.persona = "teammate"
                        self.write("  -> persona {}: ok")
                    elif "обычный голос" in text:
                        self.persona = "altron"
                    elif "охраняй" in text:
                        self.goals = ["охранять командира"]
                        self.write("  -> goal {}: ok")
                    elif "не охранять" in text:
                        self.goals = []
                    elif "ко мне" in text:
                        self.bot_pos = list(self.host_pos)
                        self.write("  -> come {}: ok")
                    elif "Молодец" in text:
                        self.write("  -> feedback {}: ok")
                    self.write("Альтрон: " + ("Fine, thanks." if text.startswith("Altron") else "Хм, ладно."))
                    return "сказано"
                if t == "task":
                    a = msg["args"]
                    if msg["name"] == "goto":
                        self.bot_pos = [a["x"] + 0.5, a["y"], a["z"] + 0.5]
                    elif msg["name"] == "come":
                        self.bot_pos = list(self.host_pos)
                    self.running = None
                    return "ГОТОВО: пришёл"
                if t == "probe":
                    a = msg["args"]
                    if "tp_host" in a:
                        self.host_pos = list(a["tp_host"])
                        self.bot_pos = [self.host_pos[0] - 2, self.host_pos[1], self.host_pos[2]]
                    if "tp_bot" in a:
                        self.bot_pos = list(a["tp_bot"])
                    if "commands" in a and any("time set 13000" in c for c in a["commands"]):
                        self.write("(событие мира) night {}")
                    return {"bot": {"pos": self.bot_pos}, "host": {"pos": self.host_pos}, "entities": []}
                return "ok"

        old = field_test.time, field_test.REPORT_DIR
        field_test.time, field_test.REPORT_DIR = clock, tmp
        try:
            args = type("A", (), {"keep_course": False})()
            t = field_test.FieldTest(FakeBrain(), {"brain_port": 1, "bot_dir": str(tmp), "host_dir": str(tmp)}, args)
            t.log = field_test.BrainLog(log_path)
            t.build_course()
            for part in ("legs", "talk", "goals", "events"):
                getattr(t, "part_" + part)()
            t.take_down()
            report = t.write().read_text(encoding="utf-8")
        finally:
            field_test.time, field_test.REPORT_DIR = old
        marks = {r[1]: r[2] for r in t.results}
        self.assertEqual(marks["flat"], "✅")
        self.assertEqual(marks["тиммейт: включился?"], "✅")
        self.assertEqual(marks["английский"], "✅")
        self.assertEqual(marks["ночь"], "✅")
        self.assertIn("| ноги | ladder | ✅ |", report)
        self.assertIn("## config.json", report)


if __name__ == "__main__":
    unittest.main()
