"""Altron's own experience as training data for his AI.

Every turn of the AI (what it saw, what it thought, which tools it called, what came back) is written to
brain/logs/dataset/turns-YYYYMMDD.jsonl. The commander rates turns by voice ("молодец" / "не так": the AI calls the
feedback tool) or here, in the console. Good turns become a fine-tuning set for the same model (see TRAINING.md).

    python dataset.py stats                      how much there is and how it is rated
    python dataset.py review                     rate the unrated turns one by one (y / n / s = skip / q = quit)
    python dataset.py export --out train.jsonl   the good turns in the chat format of transformers / Unsloth
        --unrated         also the unrated turns in which nothing failed
        --since 20260901  only turns from this day on
"""
import argparse
import glob
import hashlib
import json
import os
import re
import sys
import time

FAIL_RE = re.compile(r"^(ОШИБКА|НЕ УДАЛОСЬ|ОТКАЗ|НЕ выполнено|не выполнено)", re.M)
CONTEXT_MESSAGES = 6     # earlier messages kept with a turn, so it can be understood on its own


class DatasetLog:
    def __init__(self, folder):
        self.dir = str(folder)
        self.last_id = None      # the last finished turn: the one "молодец" / "не так" is about
        self._known = set()      # system prompts and tool lists already written

    def _blob(self, kind, obj):
        """System prompts and tool lists are long and rarely change: kept once, by their hash."""
        text = json.dumps(obj, ensure_ascii=False, sort_keys=True)
        h = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
        if h not in self._known:
            path = os.path.join(self.dir, "%s-%s.json" % (kind, h))
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
            self._known.add(h)
        return h

    def _append(self, record):
        os.makedirs(self.dir, exist_ok=True)
        path = os.path.join(self.dir, time.strftime("turns-%Y%m%d.jsonl"))
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def turn(self, system, tools, context, messages, kind="user", lang="", persona=""):
        """One finished turn. context: the messages before it; messages: the turn itself (user, assistant, tool...)."""
        try:
            os.makedirs(self.dir, exist_ok=True)
            tid = "%d-%s" % (int(time.time() * 1000), hashlib.sha1(json.dumps(messages, ensure_ascii=False)
                                                                   .encode("utf-8")).hexdigest()[:6])
            self._append({"id": tid, "t": time.time(), "kind": kind, "lang": lang, "persona": persona,
                          "system": self._blob("system", system), "tools": self._blob("tools", tools),
                          "context": _whole_turns(context[-CONTEXT_MESSAGES:]), "turn": messages})
            self.last_id = tid
            return tid
        except OSError:
            return None

    def rate(self, good, note="", tid=None):
        tid = tid or self.last_id
        if not tid:
            return "оценивать пока нечего"
        try:
            self._append({"rate": tid, "good": bool(good), "note": str(note or "")[:200], "t": time.time()})
        except OSError as e:
            return "не записал оценку: %s" % e
        return "запомнил: это было %s — так и буду учиться" % ("хорошо" if good else "плохо")


def _whole_turns(msgs):
    """Context cut so that it starts at a user message (a tool result without its call confuses a model)."""
    for i, m in enumerate(msgs):
        if m.get("role") == "user":
            return msgs[i:]
    return []


# ---------------------------------------------------------------------- reading it back
def load(folder, since=""):
    turns, rates = {}, {}
    for path in sorted(glob.glob(os.path.join(folder, "turns-*.jsonl"))):
        if since and os.path.basename(path)[6:14] < since:
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if "rate" in r:
                    rates[r["rate"]] = r
                elif "id" in r:
                    turns[r["id"]] = r
    return turns, rates


def failed(turn):
    return any(m.get("role") == "tool" and FAIL_RE.search(str(m.get("content") or "")) for m in turn["turn"])


def _blob_load(folder, kind, h, cache):
    if h not in cache:
        with open(os.path.join(folder, "%s-%s.json" % (kind, h)), encoding="utf-8") as f:
            cache[h] = json.load(f)
    return cache[h]


def _plain(messages):
    """For training: tool-call arguments as objects (the chat templates of Qwen and others expect that), and no
    fields the templates do not know."""
    out = []
    for m in messages:
        m = {k: v for k, v in m.items() if k in ("role", "content", "tool_calls", "tool_call_id", "name")}
        if m.get("tool_calls"):
            calls = []
            for c in m["tool_calls"]:
                fn = dict(c.get("function") or {})
                try:
                    fn["arguments"] = json.loads(fn.get("arguments") or "{}")
                except (TypeError, ValueError):
                    fn["arguments"] = {}
                calls.append({"type": "function", "function": fn})
            m["tool_calls"] = calls
        out.append(m)
    return out


def export(folder, out, unrated=False, since=""):
    turns, rates = load(folder, since)
    cache_s, cache_t = {}, {}
    n = 0
    with open(out, "w", encoding="utf-8") as f:
        for tid, t in turns.items():
            r = rates.get(tid)
            ok = r["good"] if r else (unrated and not failed(t))
            if not ok:
                continue
            try:
                system = _blob_load(folder, "system", t["system"], cache_s)
                tools = _blob_load(folder, "tools", t["tools"], cache_t)
            except OSError:
                continue
            msgs = [{"role": "system", "content": system}] + _plain(t["context"] + t["turn"])
            f.write(json.dumps({"messages": msgs, "tools": tools}, ensure_ascii=False) + "\n")
            n += 1
    return n


def stats(folder):
    turns, rates = load(folder)
    good = sum(1 for tid in turns if tid in rates and rates[tid]["good"])
    bad = sum(1 for tid in turns if tid in rates and not rates[tid]["good"])
    fails = sum(1 for t in turns.values() if failed(t))
    return ("Ходов: %d; хороших: %d, плохих: %d, без оценки: %d (из них с ошибками инструментов: %d)."
            % (len(turns), good, bad, len(turns) - good - bad, fails))


def _show(t):
    lines = []
    for m in t["turn"]:
        role = m.get("role")
        if role == "user":
            lines.append(">> " + str(m.get("content", ""))[:600])
        elif role == "assistant":
            if m.get("content"):
                lines.append("AI: " + m["content"][:300])
            for c in m.get("tool_calls") or []:
                fn = c.get("function") or {}
                lines.append("   -> %s %s" % (fn.get("name"), str(fn.get("arguments"))[:200]))
        elif role == "tool":
            lines.append("   <- " + str(m.get("content", ""))[:200])
    return "\n".join(lines)


def review(folder):
    turns, rates = load(folder)
    log = DatasetLog(folder)
    todo = [t for tid, t in sorted(turns.items()) if tid not in rates]
    print("Без оценки: %d. y — хорошо, n — плохо, s — пропустить, q — выйти." % len(todo))
    for t in todo:
        print("\n" + "=" * 80 + "\n" + time.strftime("%d.%m %H:%M", time.localtime(t["t"])) + "\n" + _show(t))
        a = input("[y/n/s/q] > ").strip().lower()
        if a == "q":
            break
        if a in ("y", "n", "д", "н"):
            note = input("заметка (Enter — без неё): ").strip() if a in ("n", "н") else ""
            log.rate(a in ("y", "д"), note, t["id"])


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["stats", "review", "export"])
    ap.add_argument("--dir", default=os.path.join(here, "logs", "dataset"))
    ap.add_argument("--out", default="train.jsonl")
    ap.add_argument("--unrated", action="store_true")
    ap.add_argument("--since", default="")
    a = ap.parse_args(argv)
    if a.action == "stats":
        print(stats(a.dir))
    elif a.action == "review":
        review(a.dir)
    else:
        print("Записано примеров: %d -> %s" % (export(a.dir, a.out, a.unrated, a.since), a.out))


if __name__ == "__main__":
    sys.exit(main())
