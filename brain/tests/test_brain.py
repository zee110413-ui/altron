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


    def test_the_plan_is_told_to_his_hands(self):
        plan = structures.plan("wall", 3, 1, 2, "minecraft:cobblestone")
        text = structures.describe(plan, (100, 64, -5))
        self.assertEqual(text, "y 64, cobblestone: z -5 x 100..102\ny 65, cobblestone: z -5 x 100..102")


class Hands(unittest.TestCase):
    def test_no_scripted_routines_only_hands(self):
        names = {t["function"]["name"] for t in agent.TOOLS}
        self.assertTrue({"control", "view"} <= names)
        self.assertFalse(names & {"baritone", "obtain", "smelt", "fetch", "stash", "autonomy", "assist", "sleep"})
        # nothing acts in the world but the keyboard and the mouse (control; gui and click_slot in windows)
        self.assertFalse(names & {"mine", "goto", "follow", "attack", "craft", "use_block", "place_block", "break_block",
                                  "give", "eat", "turn", "press_key", "emote", "explore", "container_take"})


class Planner(unittest.TestCase):
    def setUp(self):
        k = knowledge.Knowledge()
        for i, kind in [("thermal:machine_pulverizer", "block"), ("thermal:iron_dust", "item"),
                        ("minecraft:raw_iron", "item")]:
            k.entries[i] = {"id": i, "kind": kind, "mod": i.split(":")[0], "ru": "", "en": i, "desc": []}
        k.recipes = [{"id": "thermal:pulv_iron", "type": "thermal:pulverizer", "in": [["minecraft:raw_iron", 1]],
                      "out": [["thermal:iron_dust", 2]]}]
        self.k = k

    def test_a_machine_of_any_mod_is_known(self):
        # the machine's recipes are knowledge for the AI: which block makes iron dust
        self.assertEqual(self.k.machine("thermal:pulverizer")["blocks"], ["thermal:machine_pulverizer"])


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
    """One voice, Maxim: from Windows when it is installed, otherwise from Amazon Polly; no other voice at all."""

    def fake(self, windows=None, polly_keys=None):
        import polly
        import sapi
        said = []
        saved = sapi.find, sapi.speak, polly.credentials, polly.speak
        self.addCleanup(lambda: [setattr(sapi, "find", saved[0]), setattr(sapi, "speak", saved[1]),
                                 setattr(polly, "credentials", saved[2]), setattr(polly, "speak", saved[3])])
        tone = (np.sin(np.arange(2205) / 5) * 8000).astype(np.int16)
        sapi.find = lambda name: windows if windows and name.lower() in windows.lower() else None
        sapi.speak = lambda voice, text, rate=0: said.append(("sapi", voice, text, rate)) or (tone, 22050)
        polly.credentials = lambda: polly_keys
        polly.speak = lambda voice, text, rate=100: said.append(("polly", voice, text, rate)) or (tone, 16000)
        return said

    def test_maxim_from_windows_sentence_by_sentence(self):
        said = self.fake(windows="IVONA 2 Maxim")
        t = speech.TTS({}, persona.voice_settings({}, "teammate"))
        self.assertEqual(t.describe(), "IVONA 2 Maxim (Windows)")
        chunks = list(t.synth("Докладываю. У нас минус дом!", "ru"))
        self.assertEqual([(e, v) for e, v, _, _ in said], [("sapi", "IVONA 2 Maxim")] * 2)
        self.assertEqual(said[0][3], 1)                    # 1.15 of the pace is +1 on Windows' scale
        self.assertEqual(len(chunks), 2)
        list(t.synth("Report: we are down one house.", "en"))
        self.assertEqual(said[-1][1], "IVONA 2 Maxim")     # English too: the same voice
        alert = list(t.synth("Враг!", "ru", "alert"))
        self.assertTrue(alert and said[-1][3] > 1)          # faster in a fight, still Maxim

    def test_maxim_from_polly_when_windows_has_none(self):
        said = self.fake(polly_keys=("AK", "SK", "eu-central-1"))
        t = speech.TTS({}, persona.voice_settings({}, "teammate"))
        self.assertEqual(t.describe(), "Maxim (Amazon Polly)")
        pcm = np.frombuffer(b"".join(t.synth("Алмазы нашёл.", "ru")), "<i2")
        self.assertTrue(len(pcm) and np.isfinite(pcm).all())
        self.assertEqual(said[0][:2], ("polly", "Maxim"))
        self.assertEqual(said[0][3], 115)

    def test_free_pavel_when_there_is_no_maxim(self):
        said = self.fake(windows="Microsoft Pavel - Russian (Russia)")
        t = speech.TTS({"voice": ["Maxim", "Pavel"]}, persona.voice_settings({}, "teammate"))
        self.assertEqual(t.describe(), "Microsoft Pavel - Russian (Russia) (Windows)")
        list(t.synth("Report: we are down one house.", "en"))
        self.assertEqual(said[0][:2], ("sapi", "Microsoft Pavel - Russian (Russia)"))   # English too, the same voice

    def test_pavel_is_brought_closer_to_maxim(self):
        said = self.fake(windows="Microsoft Pavel")
        t = speech.TTS({}, {"speed": 1.0})
        self.assertEqual((t.key, t.pitch), ("pavel", 0.85))              # a deeper tone than his own
        plain = speech.TTS({"voice_tuning": {"pavel": {"pitch": 1.0}}}, {"speed": 1.0})
        low = b"".join(t.synth("Докладываю.", "ru"))
        same = b"".join(plain.synth("Докладываю.", "ru"))
        self.assertGreater(len(low), len(same))                          # read slower = lower...
        self.assertGreater(said[0][3], said[1][3])                       # ...so he speaks faster first: same pace
        maxim = self.fake(windows="IVONA 2 Maxim")
        self.assertEqual(speech.TTS({}, {}).pitch, 1.0)                  # Maxim himself as he is
        self.assertEqual(maxim, [])

    def test_maxim_first_when_both_are_there(self):
        import sapi
        self.fake()
        sapi.find = lambda name: {"maxim": "IVONA 2 Maxim", "pavel": "Microsoft Pavel"}.get(name.lower())
        self.assertEqual(speech.TTS({}, {}).voice, "IVONA 2 Maxim")

    def test_no_other_voice(self):
        self.fake(windows="Microsoft Irina Desktop - Russian")
        t = speech.TTS({}, {})
        self.assertFalse(t.ready)                            # Irina is not Maxim, and there is no Piper to fall back on
        self.assertEqual(list(t.synth("Привет.", "ru")), [])
        self.assertIn("Maxim / Pavel", t.describe())

    def test_polly_signature(self):
        import datetime
        import polly
        # AWS Signature V4, checked against AWS's own examples: the signing key and the get-vanilla canonical request
        k = polly._hmac(polly._hmac(polly._hmac(polly._hmac(b"AWS4wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", "20120215"),
                                                "us-east-1"), "iam"), "aws4_request")
        self.assertEqual(k.hex(), "f4780e2d9f65fa895f9c67b32ce1baf0b0d8a43505a000a1a9e090d414db404d")
        auth = polly.sign("GET", "example.amazonaws.com", "/", {"host": "example.amazonaws.com", "x-amz-date": "20150830T123600Z"},
                          b"", "AKIDEXAMPLE", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLE", "us-east-1", "service",
                          datetime.datetime(2015, 8, 30, 12, 36))
        self.assertTrue(auth.startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20150830/us-east-1/service/aws4_request, "
                                        "SignedHeaders=host;x-amz-date, Signature="))

    def test_personas(self):
        self.assertEqual(persona.find("говори как тиммейт"), "teammate")
        self.assertEqual(persona.find("верни обычный голос"), "altron")
        cfg = {"tts_personas": {"teammate": {"speed": 1.2, "voices": {"ru": "b.onnx"}}}}
        self.assertEqual(persona.voice_settings(cfg, "teammate"), {"speed": 1.2})   # only the pace: one voice
        self.assertEqual(persona.voice_settings({}, "")["speed"], 1.15)             # the teammate by default


class BotClient(unittest.TestCase):
    def test_a_read_only_pack_file_does_not_stop_the_second_launch(self):
        # the pack's config/euphoria_patcher/.data.json is read-only: copy2 carried the flag into Altron's client and
        # on the next launch (the full client after a mod mismatch) Windows refused to overwrite it
        import stat
        tmp = pathlib.Path(tempfile.mkdtemp())
        pack = tmp / ".minecraft" / "versions" / "P"
        (pack / "mods").mkdir(parents=True)
        (pack / "config" / "euphoria_patcher").mkdir(parents=True)
        f = pack / "config" / "euphoria_patcher" / ".data.json"
        f.write_text("{}")
        os.chmod(f, 0o444)
        cfg = {"minecraft_dir": str(tmp / ".minecraft"), "pack_version": "P", "bot_dir": str(tmp / "bot")}
        launcher.prepare_bot_dir(cfg, log=lambda *a: None)
        copy = tmp / "bot" / "config" / "euphoria_patcher" / ".data.json"
        self.assertTrue(copy.stat().st_mode & stat.S_IWUSR)        # the flag is not carried into the client
        f.chmod(0o644)
        f.write_text('{"new": 1}')
        f.chmod(0o444)
        launcher.prepare_bot_dir(cfg, log=lambda *a: None)
        self.assertEqual(copy.read_text(), '{"new": 1}')
        self.assertTrue(copy.stat().st_mode & stat.S_IWUSR)        # the client's copy stays writable


class ModVersion(unittest.TestCase):
    def test_the_current_jar_replaces_the_old_one(self):
        # 0.2.0 was built as altron-0.2.0.jar while the brain still looked for altron-0.1.0.jar: his body kept the
        # old mod (the field test: "неизвестная команда: control")
        self.assertEqual(launcher.MOD_NAME, "altron-%s.jar" % launcher._mod_version())
        self.assertNotEqual(launcher.MOD_NAME, "altron-0.1.0.jar")
        tmp = pathlib.Path(tempfile.mkdtemp())
        built = tmp / "libs" / launcher.MOD_NAME
        built.parent.mkdir()
        built.write_bytes(b"new")
        mods = tmp / ".minecraft" / "versions" / "P" / "mods"
        mods.mkdir(parents=True)
        (mods / "altron-0.1.0.jar").write_bytes(b"old")
        (mods / "other-mod-1.0.jar").write_bytes(b"x")
        cfg = {"minecraft_dir": str(tmp / ".minecraft"), "pack_version": "P", "bot_dir": str(tmp / "bot")}
        old = launcher.BUILT_MOD
        launcher.BUILT_MOD = built
        try:
            launcher.install_new_mod(cfg, log=lambda *a: None)
            self.assertEqual(sorted(p.name for p in mods.glob("*.jar")), [launcher.MOD_NAME, "other-mod-1.0.jar"])
            self.assertEqual((mods / launcher.MOD_NAME).read_bytes(), b"new")
            (mods / "altron-0.1.0.jar").write_bytes(b"old")        # an old one left behind: the body still skips it
            launcher.prepare_bot_dir(cfg, log=lambda *a: None)
            self.assertEqual(sorted(p.name for p in (tmp / "bot" / "mods").glob("*.jar")),
                             [launcher.MOD_NAME, "other-mod-1.0.jar"])
        finally:
            launcher.BUILT_MOD = old


class VoicePreview(unittest.TestCase):
    def test_writes_the_phrases_and_keeps_the_pace(self):
        import voice_preview
        tmp = pathlib.Path(tempfile.mkdtemp())
        (tmp / "brain").mkdir()
        (tmp / "brain" / "config.json").write_text(json.dumps({"persona": "teammate"}), encoding="utf-8")
        seen = []

        class FakeTTS:
            ready = True

            def __init__(self, cfg, settings):
                seen.append(settings)

            def describe(self):
                return "IVONA 2 Maxim (Windows)"

            def synth(self, text, lang):
                yield b"\x00\x00" * 480

        old = voice_preview.BRAIN_DIR, speech.TTS
        voice_preview.BRAIN_DIR, speech.TTS = tmp / "brain", FakeTTS
        try:
            voice_preview.main(["--speed", "1.2", "--save", "--no-play"])
        finally:
            voice_preview.BRAIN_DIR, speech.TTS = old
        self.assertEqual(seen[0]["speed"], 1.2)
        self.assertEqual(len(list((tmp / "test-reports" / "voice").glob("teammate_ru_*.wav"))), 3)
        cfg = json.loads((tmp / "brain" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["tts_personas"]["teammate"]["speed"], 1.2)


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

    def test_death_is_told_not_decided(self):
        hub = make_hub()
        hub.joined, hub.bot = True, object()
        run(hub.after_death([10, 64, -16], ""))
        self.assertEqual(hub.goals_text(), "")                 # no goal made for him: whether to go back is his call
        self.assertIn("10 64 -16", run(hub.requests.get())[2])  # he hears where his things lie

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
            ready = True

            def synth(self, text, lang=None, mood=None):
                spoken.append(text)
                yield b"\x00\x00"
        hub.tts, hub.host = Tts(), type("W", (), {"write": lambda self, b: None, "drain": lambda self: asyncio.sleep(0)})()
        run(hub.say("[Наблюдение] Командир смотрит на снег."))
        self.assertEqual(spoken, ["Командир смотрит на снег."])

    def test_without_maxim_the_words_go_to_the_chat(self):
        hub = make_hub()
        hub.bot, sent = object(), []
        hub.send = lambda to, msg: sent.append(msg)

        class NoVoice:
            ready = False

            def synth(self, *a, **k):
                raise AssertionError("no other voice may speak")
        hub.tts = NoVoice()
        run(hub.say("Докладываю: у нас минус дом."))
        self.assertEqual([m["args"]["text"] for m in sent if m.get("name") == "chat"], ["Докладываю: у нас минус дом."])

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
            ready = True

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
        self.assertEqual(hub.tts.settings, {"speed": 1.15})    # a manner sets the pace only: the voice is Maxim's
        self.assertIn("голос тот же", r)
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


class RepeatedCalls(unittest.TestCase):
    def test_the_same_call_again_is_done_and_he_is_told(self):
        call = {"id": "g", "type": "function", "function": {"name": "goal", "arguments": '{"action": "list"}'}}
        replies = [{"role": "assistant", "content": "", "tool_calls": [call]} for _ in range(3)] + \
                  [{"role": "assistant", "content": "Целей нет."}]

        class Llm:
            last_prompt_tokens = "10"

            async def chat(self, messages, **kw):
                return dict(replies.pop(0))

        ran = []

        class Hub:
            owner, lang, knowledge, persona = "Egor", "ru", None, "teammate"
            cut_speech = False
            dataset = None

            async def say(self, text, mood=None):
                pass

            async def run_tool(self, name, args, wait):
                ran.append(name)
                return "целей нет"

            def log(self, text):
                pass
        a = agent.Agent({"bot_name": "altron", "languages": ["ru"], "llm_port": 1}, Hub())
        a.llm = Llm()
        run(a.run("[Egor (командир) говорит]: хватит охранять", "user"))
        self.assertEqual(ran, ["goal"] * 2)                      # the second one runs, with a note...
        notes = [m["content"] for m in a.history if m.get("role") == "tool"]
        self.assertNotIn("уже был", notes[0])
        self.assertIn("уже был в этом ходе", notes[1])
        self.assertIn("НЕ ВЫПОЛНЕНО", notes[2])                  # ...the third, changing nothing, is not run

    def _turn(self, replies, run_tool, phrase="[Egor (командир) говорит]: Копай вниз"):
        said = []

        class Llm:
            last_prompt_tokens = "10"

            async def chat(self, messages, **kw):
                return dict(replies.pop(0))

        class Hub:
            owner, lang, knowledge, persona = "Egor", "ru", None, "teammate"
            cut_speech = False
            dataset = None

            async def say(self, text, mood=None):
                said.append(text)

            def log(self, text):
                pass
        Hub.run_tool = staticmethod(run_tool)
        a = agent.Agent({"bot_name": "altron", "languages": ["ru"], "llm_port": 1}, Hub())
        a.llm = Llm()
        run(a.run(phrase, "user"))
        return a, said

    def _call(self, name="control", args='{"pitch": 90, "left": "hold"}'):
        return {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c", "type": "function", "function": {"name": name, "arguments": args}}]}

    def test_digging_down_repeats_while_the_answer_changes(self):
        depth = [64]
        ran = []

        async def run_tool(name, args, wait):
            ran.append(name)
            depth[0] -= 2
            return "ГОТОВО: ты на высоте %d" % depth[0]
        replies = [self._call() for _ in range(4)] + [{"role": "assistant", "content": "Выкопал на 8 вниз."}]
        a, said = self._turn(replies, run_tool)
        self.assertEqual(len(ran), 4)
        self.assertEqual(said, ["Выкопал на 8 вниз."])

    def test_a_stuck_repeat_is_refused_and_the_turn_still_ends_with_words(self):
        async def run_tool(name, args, wait):
            return "ГОТОВО: Прицел: ничего в досягаемости"
        replies = [self._call() for _ in range(3)] + [{"role": "assistant", "content": "Не достаю — упёрся в пустоту."}]
        a, said = self._turn(replies, run_tool)
        self.assertEqual(said, ["Не достаю — упёрся в пустоту."])
        notes = [m["content"] for m in a.history if m.get("role") == "tool"]
        self.assertIn("НЕ ВЫПОЛНЕНО", notes[-1])

    def test_blind_walking_is_stopped_the_third_time_though_the_position_changes(self):
        pos = [0]

        async def run_tool(name, args, wait):
            pos[0] += 11
            return "ГОТОВО: Ты: 0 64 %d" % pos[0]
        walk = '{"keys": ["forward", "sprint"], "ticks": 40}'
        replies = [self._call(args=walk) for _ in range(3)] + [{"role": "assistant", "content": "Не знаю, где это."}]
        a, said = self._turn(replies, run_tool, "[Egor (командир) говорит]: Принеси брёвен")
        self.assertEqual(pos[0], 22)          # two steps were taken, the third was not
        self.assertEqual(said, ["Не знаю, где это."])
        self.assertIn("никуда не целясь", [m["content"] for m in a.history if m.get("role") == "tool"][-1])

    def _hub(self, pos):
        hub = altron.Hub.__new__(altron.Hub)
        hub.state = {"pos": pos}
        return hub

    def test_hints_say_how_far_the_point_is_and_that_he_passed_it(self):
        hub = self._hub([0.5, 64, 0.5])
        passed = hub.control_hint({"x": 4, "y": 64, "z": -4, "keys": ["forward", "sprint"], "ticks": 40},
                                  "ГОТОВО: Ты: 8.5 64.0 -8.0, смотришь на юг", [0.5, 64, 0.5])
        self.assertIn("ты прошёл мимо", passed)
        near = hub.control_hint({"x": 4, "y": 64, "z": -4, "keys": ["forward"], "ticks": 15},
                                "ГОТОВО: Ты: 4.4 64.0 -3.0, смотришь на север", [0.5, 64, 0.5])
        self.assertIn("ты у цели", near)

    def test_hints_walking_and_breaking_and_the_chest_with_the_left_button(self):
        hub = self._hub([0.5, 64, 0.5])
        self.assertIn("Ты шёл вперёд и ломал одним вызовом",
                      hub.control_hint({"keys": ["forward"], "left": "hold", "pitch": 90, "ticks": 20},
                                       "ГОТОВО: Ты: 0.5 64.0 4.8\nПрицел: блок Блок травы в 0 63 4"))
        self.assertIn("right click", hub.control_hint({"x": 4, "y": 64, "z": -4, "left": "click"},
                                                      "ГОТОВО: Ты: 4.4 64.0 -3.0\nПрицел: блок Сундук в 4 64 -4"))

    def test_a_call_written_as_text_is_not_said_and_a_detail_question_is_nudged(self):
        said = []
        replies = [{"role": "assistant", "content": "[control track=player:Egor keys=[forward]]"},
                   {"role": "assistant", "content": "Построю башню. Из какого материала?"},
                   self._call("view", "{}"),
                   {"role": "assistant", "content": "Строю."}]

        async def run_tool(name, args, wait):
            return "ГОТОВО"
        a, said = self._turn(replies, run_tool, "[Egor (командир) говорит]: Построй башню")
        self.assertEqual(said, ["Строю."])
        self.assertTrue(any(m["role"] == "user" and "Это мелочь, выбери сам" in m["content"] for m in a.history))

    def test_he_never_hits_the_commander(self):
        hub = altron.Hub.__new__(altron.Hub)
        hub.owner, hub.friends, hub.speaker, hub.bot = "Egor", set(), "", object()
        out = run(hub.run_tool("control", {"track": "player:Egor", "left": "click"}, 0))
        self.assertIn("ОТКАЗ", out)
        self.assertIn("бить его нельзя", out)

    def test_words_without_hands_get_one_reminder(self):
        replies = [{"role": "assistant", "content": "Иду."},
                   {"role": "assistant", "content": "", "tool_calls": [
                       {"id": "c", "type": "function", "function": {"name": "control",
                                                                    "arguments": '{"track": "player:Egor", "keys": ["forward"]}'}}]},
                   {"role": "assistant", "content": "На месте."}]

        class Llm:
            last_prompt_tokens = "10"

            async def chat(self, messages, **kw):
                return dict(replies.pop(0))
        ran, said = [], []

        class Hub:
            owner, lang, knowledge, persona = "Egor", "ru", None, "teammate"
            cut_speech = False
            dataset = None

            async def say(self, text, mood=None):
                said.append(text)

            async def run_tool(self, name, args, wait):
                ran.append(name)
                return "ГОТОВО: веду прицел за Egor"

            def log(self, text):
                pass
        a = agent.Agent({"bot_name": "altron", "languages": ["ru"], "llm_port": 1}, Hub())
        a.llm = Llm()
        run(a.run("[Egor (командир) говорит]: Иди ко мне\n[Состояние] ...", "user"))
        self.assertEqual(ran, ["control"])
        self.assertEqual(said, ["Иду.", "На месте."])
        self.assertTrue(any(m["role"] == "user" and "руки ничего не сделали" in m["content"] for m in a.history))

    def test_an_empty_answer_is_asked_again_without_thinking(self):
        thinks = []
        replies = [{"role": "assistant", "content": ""}, {"role": "assistant", "content": "Нормально. А ты?"}]

        class Llm:
            last_prompt_tokens = "10"

            async def chat(self, messages, **kw):
                thinks.append(kw.get("think"))
                return dict(replies.pop(0))
        said = []

        class Hub:
            owner, lang, knowledge, persona = "Egor", "ru", None, "teammate"
            cut_speech = False
            dataset = None

            async def say(self, text, mood=None):
                said.append(text)

            def log(self, text):
                pass
        a = agent.Agent({"bot_name": "altron", "languages": ["ru"], "llm_port": 1}, Hub())
        a.llm = Llm()
        run(a.run("[Egor (командир) говорит]: как дела?", "user", think=True))
        self.assertEqual(thinks, [True, False])
        self.assertEqual(said, ["Нормально. А ты?"])

    def test_control_hint_when_out_of_reach(self):
        hub = altron.Hub.__new__(altron.Hub)
        hub.state = {"pos": [0.5, 64, 0.5]}
        out = hub.control_hint({"x": -4, "y": 64, "z": -3, "left": "hold"}, "ГОТОВО: ...\nПрицел: ничего в досягаемости")
        self.assertIn("рука достаёт на 4.5", out)
        self.assertIn("Ты шёл туда, куда уже смотрел", hub.control_hint({"keys": ["forward"], "ticks": 60}, "ГОТОВО"))


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
                self.blocks = {}

            def write(self, *lines):
                with open(log_path, "a", encoding="utf-8") as f:
                    for ln in lines:
                        f.write("2026-01-01 12:00:00 " + ln + "\n")

            def call(self, msg, timeout=300):
                t = msg["type"]
                if t == "state":
                    return {"busy": False, "requests": 0, "speaking": False, "macro": False, "running": self.running,
                            "joined": True, "state": {"pos": self.bot_pos}, "persona": self.persona,
                            "goals": self.goals, "owner": "Egor"}
                if t == "say":
                    text = msg["text"]
                    if "тиммейт" in text:
                        self.persona = "teammate"
                        self.write("  -> persona {}: ok")
                    elif "как Альтрон" in text:
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
                    if msg["name"] == "control":   # the pretend hands do what the keys and the mouse say
                        if a.get("track"):
                            self.bot_pos = list(self.host_pos)
                        elif "jump" in a.get("keys", []):
                            self.bot_pos = [self.bot_pos[0], self.bot_pos[1] + 1, self.bot_pos[2]]
                        elif "forward" in a.get("keys", []):
                            self.bot_pos = [self.bot_pos[0] + 12, self.bot_pos[1], self.bot_pos[2]]
                        key = "%d,%d,%d" % (a.get("x", 0), a.get("y", 0) + (1 if a.get("right") else 0), a.get("z", 0))
                        if a.get("left"):
                            self.blocks[key] = "minecraft:air"
                        if a.get("right"):
                            self.blocks[key] = "minecraft:stone"
                    self.running = None
                    return "ГОТОВО: руки"
                if t == "probe":
                    a = msg["args"]
                    if "tp_host" in a:
                        self.host_pos = list(a["tp_host"])
                        self.bot_pos = [self.host_pos[0] - 2, self.host_pos[1], self.host_pos[2]]
                    if "tp_bot" in a:
                        self.bot_pos = list(a["tp_bot"])
                    if "commands" in a and any("time set 13000" in c for c in a["commands"]):
                        self.write("(событие мира) night {}")
                    got = {"%d,%d,%d" % tuple(b): self.blocks.get("%d,%d,%d" % tuple(b), "minecraft:stone") for b in a.get("blocks", [])}
                    return {"bot": {"pos": self.bot_pos}, "host": {"pos": self.host_pos}, "entities": [], "blocks": got}
                return "ok"

        old = field_test.time, field_test.REPORT_DIR
        field_test.time, field_test.REPORT_DIR = clock, tmp
        try:
            args = type("A", (), {"keep_course": False})()
            t = field_test.FieldTest(FakeBrain(), {"brain_port": 1, "bot_dir": str(tmp), "host_dir": str(tmp)}, args)
            t.log = field_test.BrainLog(log_path)
            t.build_course()
            for part in ("hands", "talk", "goals", "events"):
                getattr(t, "part_" + part)()
            t.take_down()
            report = t.write().read_text(encoding="utf-8")
        finally:
            field_test.time, field_test.REPORT_DIR = old
        marks = {r[1]: r[2] for r in t.results}
        self.assertEqual(marks["идти (forward+sprint 3 с)"], "✅")
        self.assertEqual(marks["сломать блок (left hold)"], "✅")
        self.assertEqual(marks["поставить блок (right click)"], "✅")
        self.assertEqual(marks["тиммейт: включился?"], "✅")
        self.assertEqual(marks["английский"], "✅")
        self.assertEqual(marks["ночь"], "✅")
        self.assertIn("| руки | бежать к командиру (track) | ✅ |", report)
        self.assertIn("## config.json", report)


class SimWorld(unittest.TestCase):
    """The simulated body of the scenario runs answers like the mod: walking, breaking, placing, following."""

    def world(self):
        import sim_world
        w = sim_world.World()
        w.give("minecraft:iron_pickaxe", 1, 1)
        w.give("minecraft:cobblestone", 8, 2)
        return sim_world, w

    def test_walk_and_view(self):
        _, w = self.world()
        ok, text = w.control({"keys": ["forward", "sprint"], "ticks": 60})
        self.assertTrue(ok)
        self.assertGreater(w.bot["pos"][2], 15)
        self.assertIn("смотришь на юг (z+)", text)
        self.assertIn("Хотбар: 1=Железная кирка 2=Булыжник x8", text)

    def test_break_needs_reach_and_the_right_tool(self):
        sw, w = self.world()
        w.set(0, 64, 2, "minecraft:stone")
        w.control({"x": 0, "y": 64, "z": 2, "left": "hold", "ticks": 40, "slot": 1})
        self.assertEqual(w.get(0, 64, 2), "minecraft:air")
        w.control({"keys": ["forward"], "ticks": 10})
        self.assertGreaterEqual(w.count("minecraft:cobblestone"), 9)   # picked up walking over it
        w.set(0, 64, -9, "minecraft:stone")
        w.control({"x": 0, "y": 64, "z": -9, "left": "hold", "ticks": 40})
        self.assertEqual(w.get(0, 64, -9), "minecraft:stone")          # out of reach

    def test_place_and_track(self):
        _, w = self.world()
        w.control({"x": 0, "y": 63, "z": 2, "right": "click", "slot": 2})
        self.assertEqual(w.get(0, 64, 2), "minecraft:cobblestone")
        w.owner_ent.pos = [12.5, 64.0, -6.5]
        ok, text = w.control({"keys": ["forward", "sprint"], "track": "player:MJreggich", "ticks": 80})
        self.assertIn("веду прицел за MJreggich", text)
        self.assertLess(w.owner_ent.dist(w.bot["pos"]), 2)


class ScenarioCatalog(unittest.TestCase):
    def test_a_thousand_scenarios_each_with_a_world(self):
        import scenario_catalog
        scs = scenario_catalog.all_scenarios()
        self.assertGreaterEqual(len(scs), 1000)
        self.assertEqual(len({s["id"] for s in scs}), len(scs))
        for s in scs[::37]:
            scenario_catalog.build_world(s)

    def test_checks(self):
        import scenario_catalog
        sc = {"steps": [{"say": "Иди ко мне"}], "expect": {"act": [{"tool": "control", "has": {"track": "player"}}],
                                                         "world": {"near_owner": 3}}}
        w = scenario_catalog.build_world(sc)
        out = {"tools": [{"name": "view", "args": {}}], "said": ["Иду!", "Иду!"], "timed_out": False, "events": []}
        failed = {c["name"] for c in scenario_catalog.check(sc, out, w, None) if not c["ok"]}
        self.assertEqual(failed, {"acted", "near_owner", "no_repeat"})


if __name__ == "__main__":
    unittest.main()
