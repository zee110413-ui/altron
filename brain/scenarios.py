"""Altron's real brain against a simulated world: hundreds of phrases, dialogues and situations, one after another.

Everything of the brain is real — the hub (altron.Hub: how a phrase becomes a request, the state, the memory, the
feelings, the goals), the agent with its prompt and 37 tools, the character, the AI server. Only the body is replaced:
sim_world.World answers the commands the way the mod does. Every scenario starts from a fresh brain and a fresh world.

    python scenarios.py --list                         how many scenarios of which group
    python scenarios.py --shard 3/20 --out results     every 20th scenario starting with the 3rd
    python scenarios.py --only talk --limit 10         a few of one group
    python scenarios.py --report results               the report over all result files in the folder

The results are JSON lines: what was said and done, every tool call with its answer, the AI's raw replies with their
timings, and the checks of the scenario.
"""
import argparse
import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

BRAIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BRAIN))

import altron  # noqa: E402
from launcher import primary_language  # noqa: E402
from lang import set_ui  # noqa: E402
import scenario_catalog  # noqa: E402

OWNER = "MJreggich"
STEP_TIMEOUT = float(os.environ.get("ALTRON_STEP_TIMEOUT", 900))   # one phrase on a slow processor
KNOWLEDGE = None
GAME_TOOLS = {"control", "view", "status", "inventory", "nearby", "find_block", "stop", "gui", "click_slot",
              "build_structure", "build_multiblock", "chat", "look", "mark_place", "render"}
TALK_TOOLS = {"reply", "ask_player", "ignore", "feel", "relation", "moment", "feedback", "remember", "recall", "forget",
              "persona", "goal", "remind", "friends", "listen_mode", "wiki", "web_search", "plan", "recipe", "find_item",
              "item_info", "watch_me"}


class TrackedQueue(asyncio.Queue):
    """The hub's request queue that knows when the agent waits for the next request (the brain is idle)."""
    waiting = False

    async def get(self):
        self.waiting = True
        try:
            return await super().get()
        finally:
            self.waiting = False


class SimHub(altron.Hub):
    """altron.Hub with the simulated body: commands go to the World, not to the game over a socket."""

    def __init__(self, cfg, world, rec):
        self.rec = rec
        super().__init__(cfg)
        self.world = world
        self.bot = object()            # "the body is in the game"
        self.joined = True
        self.owner = OWNER
        self.requests = TrackedQueue()
        self.state = world.state()
        self.knowledge = KNOWLEDGE

    def log(self, text):
        self.rec.event("log", text)
        m = re.match(r"(Альтрон|Altron)(?: \(сразу\)| \(at once\))?: (.*)", text, re.S)
        if m:
            self.rec.event("say", m.group(2))

    def send(self, writer, obj):
        return True

    async def bot_call(self, name, args):
        if name == "recipe" and self.knowledge is not None:
            res = await asyncio.to_thread(self.knowledge.search, str(args.get("item", "")), 3)
            out = {"ok": bool(res), "msg": res or "не знаю такой предмет: %s" % args.get("item", "")}
        else:
            out = self.world.call(name, args)
        self.state = self.world.state()
        return out

    async def start_task(self, name, args, wait_sec):
        if name == "control":
            ok, msg = self.world.control(args)
            self.state = self.world.state()
            return ("ГОТОВО: " if ok else "НЕ УДАЛОСЬ: ") + msg
        res = await self.bot_call(name, args)
        return res.get("msg", "") if res.get("ok") else "ОШИБКА: " + res.get("msg", "")

    async def run_tool(self, name, args, wait_sec):
        t = time.time()
        try:
            result = await super().run_tool(name, args, wait_sec)
        except Exception as e:
            result = "ОШИБКА: %r" % e
        self.rec.tool(name, args, result, time.time() - t)
        return result

    def note_llm_failure(self, hard=False, why=""):
        # a slow answer is the processor of the machine, not his fault: only errors count as failures
        self.rec.event("llm_slow" if why.startswith("ответ шёл") else "llm_failure", why)

    def request_restart(self, reason, llm=False):
        self.rec.event("restart", reason)
        raise RuntimeError("brain restart requested: " + reason)

    async def drain(self, timeout=STEP_TIMEOUT):
        """Wait until every request (the phrase and the events it caused) is handled."""
        end = time.time() + timeout
        quiet = 0
        while time.time() < end:
            await asyncio.sleep(0.05)
            if self.requests.empty() and self.requests.waiting and not self.busy:
                quiet += 1
                if quiet >= 3:
                    return True
            else:
                quiet = 0
        return False


class Recorder:
    def __init__(self):
        self.t0 = time.time()
        self.events = []
        self.tools = []
        self.llm = []

    def event(self, kind, text):
        self.events.append({"t": round(time.time() - self.t0, 2), "kind": kind, "text": text})

    def tool(self, name, args, result, sec):
        self.tools.append({"t": round(time.time() - self.t0, 2), "name": name, "args": args, "result": result[:1500],
                           "sec": round(sec, 2)})
        self.event("tool", "%s %s" % (name, json.dumps(args, ensure_ascii=False)))


def wrap_llm(agent, rec):
    """Every answer of the AI server, with its time and token counts."""
    chat = agent.llm.chat

    async def recorded(messages, **kw):
        t = time.time()
        try:
            out = await chat(messages, **kw)
        except Exception as e:
            rec.llm.append({"t": round(t - rec.t0, 2), "sec": round(time.time() - t, 2), "error": repr(e)[:300]})
            raise
        rec.llm.append({"t": round(t - rec.t0, 2), "sec": round(time.time() - t, 2),
                        "prompt": str(agent.llm.last_prompt_tokens), "think": bool(kw.get("think")),
                        "content": out.get("content", ""), "spoken": out.get("spoken", ""),
                        "reasoning": (out.get("reasoning") or "")[:2000],
                        "calls": [c.get("function", {}) for c in out.get("tool_calls") or []],
                        "seen": messages[-1]["content"][:4000] if messages[-1]["role"] == "user" else ""})
        return out
    agent.llm.chat = recorded


def base_cfg():
    cfg = json.loads((BRAIN / "config.json").read_text(encoding="utf-8"))
    cfg.update({"dataset": False, "brain_log": "", "idle_think_minutes": 0, "instant_ack": False,
                "llm_url": os.environ.get("ALTRON_LLM_URL", ""), "llm_host": "127.0.0.1"})
    if os.environ.get("ALTRON_LLM_PORT"):
        cfg["llm_port"] = int(os.environ["ALTRON_LLM_PORT"])
    return cfg


async def run_one(sc, cfg0):
    rec = Recorder()
    tmp = tempfile.mkdtemp(prefix="altron-sc-")
    cfg = dict(cfg0, memory_dir=tmp)
    set_ui(primary_language(cfg))
    world = scenario_catalog.build_world(sc)
    hub = SimHub(cfg, world, rec)
    hub.loop = asyncio.get_running_loop()
    for f in sc.get("friends", []):
        hub.friends.add(f.lower())
    if sc.get("persona"):
        hub.persona = sc["persona"]
    for g in sc.get("goals", []):
        hub.add_goal(g, until=0.0)
    if sc.get("wake_word_required"):
        hub.cfg["wake_word_required"] = True
    wrap_llm(hub.agent, rec)
    worker = asyncio.create_task(hub.agent_loop())
    timed_out = False
    try:
        for step in sc["steps"]:
            rec.event("step", json.dumps(step, ensure_ascii=False))
            if "say" in step:
                await hub.handle_phrase(step.get("who", OWNER), step["say"])
            elif "world_event" in step:
                await hub.on_world_event(dict(step["world_event"]))
            elif "event" in step:
                await hub.requests.put(("event", "", step["event"]))
            elif "observe" in step:
                await hub.requests.put(("event", "", scenario_catalog.observation(hub, step["observe"])))
            elif "do" in step:
                scenario_catalog.world_change(world, step["do"])
                hub.state = world.state()
                continue
            if not await hub.drain():
                timed_out = True
                rec.event("timeout", "шаг не закончился за %.0f с" % STEP_TIMEOUT)
                break
    finally:
        worker.cancel()
        try:
            await worker
        except BaseException:
            pass
        try:
            await hub.agent.llm.client.aclose()
        except Exception:
            pass
    out = {"id": sc["id"], "group": sc["group"], "title": sc.get("title", ""), "steps": sc["steps"],
           "said": [e["text"] for e in rec.events if e["kind"] == "say"], "tools": rec.tools, "llm": rec.llm,
           "events": rec.events, "timed_out": timed_out, "sec": round(time.time() - rec.t0, 1),
           "world_after": {"bot": [round(v, 1) for v in world.bot["pos"]], "hp": world.bot["hp"], "food": world.bot["food"],
                           "owner": [round(v, 1) for v in world.owner_ent.pos],
                           "inventory": world.inventory(), "dead": world.dead}}
    out["checks"] = scenario_catalog.check(sc, out, world, hub)
    out["passed"] = all(c["ok"] for c in out["checks"])
    shutil.rmtree(tmp, ignore_errors=True)
    return out


async def main_async(a):
    global KNOWLEDGE
    scs = scenario_catalog.all_scenarios()
    if a.only:
        groups = set(a.only.split(","))
        scs = [s for s in scs if s["group"] in groups or s["id"] in groups]
    if a.shard:
        i, n = (int(v) for v in a.shard.split("/"))
        scs = scs[i - 1::n]
    if a.limit:
        scs = scs[:a.limit]
    cfg = base_cfg()
    if a.knowledge:
        from knowledge import Knowledge
        KNOWLEDGE = Knowledge.load(cfg, print)
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / ("results_%s.jsonl" % (a.shard.replace("/", "of") if a.shard else "all"))
    done = set()
    if path.exists():
        done = {json.loads(line)["id"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}
    deadline = time.time() + a.minutes * 60 if a.minutes else None
    print("сценариев: %d (уже есть: %d)" % (len(scs), len(done)), flush=True)
    for n, sc in enumerate(scs, 1):
        if sc["id"] in done:
            continue
        if deadline and time.time() > deadline:
            print("время вышло: остальное в следующий раз", flush=True)
            break
        try:
            r = await run_one(sc, cfg)
        except Exception as e:
            r = {"id": sc["id"], "group": sc["group"], "title": sc.get("title", ""), "steps": sc["steps"],
                 "error": repr(e)[:500], "passed": False, "checks": [], "said": [], "tools": []}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        fails = [c["name"] for c in r.get("checks", []) if not c["ok"]]
        print("%4d/%d %s %-28s %5.0f с | %s | %s%s" % (
            n, len(scs), "✅" if r["passed"] else "❌", sc["id"], r.get("sec", 0),
            " / ".join(r.get("said", []))[:160], ", ".join(t["name"] for t in r.get("tools", []))[:80],
            ("  ← " + ", ".join(fails)) if fails else ""), flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--only")
    ap.add_argument("--shard")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--minutes", type=float, help="stop starting new scenarios after this many minutes")
    ap.add_argument("--out", default=str(BRAIN.parent / "test-reports" / "scenarios"))
    ap.add_argument("--knowledge", action="store_true", help="load the pack reference (minecraft_dir in config.json)")
    ap.add_argument("--report", help="write the report over the result files in this folder")
    a = ap.parse_args(argv)
    if a.list:
        scs = scenario_catalog.all_scenarios()
        groups = {}
        for s in scs:
            groups[s["group"]] = groups.get(s["group"], 0) + 1
        for g, n in groups.items():
            print("%-12s %d" % (g, n))
        print("всего       %d" % len(scs))
        return 0
    if a.report:
        import scenario_report
        print(scenario_report.write(Path(a.report)))
        return 0
    asyncio.run(main_async(a))
    return 0


if __name__ == "__main__":
    sys.exit(main())
