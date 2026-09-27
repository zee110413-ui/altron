"""Tests of Altron's brain that need neither Minecraft nor the AI: python -m unittest discover -s brain/tests"""
import asyncio
import base64
import json
import os
import pathlib
import sys
import tempfile
import unittest

import httpx
import numpy as np

BRAIN = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRAIN))

import agent  # noqa: E402
import agent_en  # noqa: E402
import altron  # noqa: E402
import knowledge  # noqa: E402
import lang  # noqa: E402
import launcher  # noqa: E402
import speech  # noqa: E402
import structures  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def make_hub(**cfg_over):
    cfg = json.loads((BRAIN / "config.json").read_text(encoding="utf-8"))
    cfg["memory_dir"] = tempfile.mkdtemp()
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
        self.assertEqual(lang.phrase("online", "de"), "Altron ist online. Ich warte auf Befehle.")
        self.assertEqual(lang.phrase("stuck", "ja"), lang.PHRASES["stuck"]["en"])

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
        a.cfg, a.hub = {"bot_name": "altron"}, H()
        en = a._system()
        self.assertIn("You are Altron", en)
        self.assertIn("TaCZ: guns", en)                  # a hint for an installed mod
        self.assertNotIn("SuperbWarfare:", en)           # not for a missing one
        H.lang = "ru"
        self.assertIn("Ты — Альтрон", a._system())


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
        t.speed, t.moods = 1.0, True
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
            return [(await hub.requests.get())[2] for _ in range(hub.requests.qsize())]
        events = run(go())
        self.assertEqual(said[0][1], "alert")
        self.assertIn("guard", tools)
        self.assertEqual(len(events), 1)                  # a stranger's advancement is not news
        self.assertIn("Командир погиб", events[0])

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


if __name__ == "__main__":
    unittest.main()
