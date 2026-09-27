"""Altron's long-term memory: everything that was said and done, facts he was asked to remember and named
places. It is saved to disk (brain/memory) and survives restarts; with every phrase the model gets the
memories that fit it, like a person recalling things."""
import json
import os
import re
import time
from pathlib import Path

JOURNAL_IN_RAM = 5000       # newest journal entries kept in memory for recall
FACTS_ALWAYS_CHARS = 1500   # up to this size all facts go into every prompt

STOP = set("""и в во на не что это как так мне меня мой моя мое мои ты тебе тебя твой ты я мы нас нам вы вас он она оно они
его ее их там тут здесь где когда кто чем чего или а но да нет же ли бы то вот уже еще ещё только очень все всё всех
для из от до по под над при про без за у к ко с со о об же быть был была были будет есть сейчас потом тоже давай
пожалуйста альтрон альтрона альтрону альтроном the a an to of and is are
где видел видела видели видишь лежит лежат лежал лежали помнишь помню помнит вспомни запомни говорил говорила сказал
делали делал было какой какая какие какое сколько""".split())

ENDINGS = ("иями", "ями", "ами", "ого", "его", "ому", "ему", "ыми", "ими", "ией", "ий", "ый", "ой", "ая", "яя", "ое", "ее",
           "ов", "ев", "ей", "ам", "ям", "ах", "ях", "ом", "ем", "ую", "юю", "а", "я", "о", "е", "ы", "и", "у", "ю", "ь")


def stem(word):
    w = word.lower().replace("ё", "е")
    for end in ENDINGS:
        if len(w) - len(end) >= 3 and w.endswith(end):
            w = w[:-len(end)]
            break
    return w[:7]


def stems(text):
    return {stem(w) for w in re.findall(r"[a-zа-яё0-9_]+", (text or "").lower()) if len(w) >= 3 and w not in STOP}


def keywords(text, limit=3):
    """The most telling word stems of a question (for looking things up in the bot's memory)."""
    return sorted(stems(text), key=len, reverse=True)[:limit]


def when(t):
    return time.strftime("%d.%m %H:%M", time.localtime(t))


class LongMemory:
    def __init__(self, folder):
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.file = self.dir / "memory.json"
        self.journal_file = self.dir / "journal.jsonl"
        self.world = ""
        self.facts = []      # {"t", "text"}
        self.places = []     # {"t", "name", "world", "dim", "pos": [x, y, z]}
        # experience: what worked, what failed and why, the commander's rules — {"t", "kind", "key", "world", "text", "n"}
        #   kind: "rule" (the commander said never/always...), "success", "failure", "bad_recipe" (a recipe a machine refused)
        self.lessons = []
        self.journal = []    # {"t", "world", "who", "text"}
        self.started = time.time()
        self._load()

    # ------------------------------------------------------------------ storage
    def _load(self):
        if self.file.exists():
            try:
                data = json.loads(self.file.read_text(encoding="utf-8"))
                self.facts = data.get("facts", [])
                self.places = data.get("places", [])
                self.lessons = data.get("lessons", [])
            except Exception:
                # a damaged file must not erase the memories: keep a copy and start the index anew
                self.file.replace(self.dir / ("memory.broken.%d.json" % int(time.time())))
        if self.journal_file.exists():
            with open(self.journal_file, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()[-JOURNAL_IN_RAM:]
            for line in lines:
                try:
                    self.journal.append(json.loads(line))
                except Exception:
                    pass

    def _save(self):
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"facts": self.facts, "places": self.places, "lessons": self.lessons},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.file)

    def log(self, who, text):
        """Everything said to and by Altron, and what happened, goes to the journal."""
        text = re.sub(r"\s+", " ", text or "").strip()
        if not text:
            return
        entry = {"t": time.time(), "world": self.world, "who": who, "text": text[:600]}
        self.journal.append(entry)
        del self.journal[:-JOURNAL_IN_RAM]
        with open(self.journal_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------ facts and places
    def remember(self, text):
        text = re.sub(r"\s+", " ", text or "").strip()
        if not text:
            return "нечего запоминать"
        new = stems(text)
        for f in self.facts:
            old = stems(f["text"])
            if new and old and len(new & old) / len(new | old) > 0.8:   # the same thing again: update it
                f.update(t=time.time(), text=text)
                self._save()
                return "обновил в памяти: " + text
        self.facts.append({"t": time.time(), "text": text})
        self._save()
        return "запомнил навсегда: " + text

    def forget(self, query):
        q = stems(query)
        if not q:
            return "что именно забыть?"
        keep, gone = [], []
        for f in self.facts:
            (gone if len(q & stems(f["text"])) >= max(1, len(q) // 2) else keep).append(f)
        places = [p for p in self.places if not (p["world"] == self.world and stems(p["name"]) & q)]
        gone += [p for p in self.places if p not in places]
        if not gone:
            return "не нашёл в памяти ничего про «%s»" % query
        self.facts, self.places = keep, places
        self._save()
        return "забыл: " + "; ".join(g.get("text") or "место «%s»" % g["name"] for g in gone)

    def set_place(self, name, pos, dim):
        name = name.strip().lower()
        self.places = [p for p in self.places if not (p["world"] == self.world and p["name"] == name)]
        self.places.append({"t": time.time(), "name": name, "world": self.world, "dim": dim,
                            "pos": [int(round(v)) for v in pos]})
        self._save()
        return "запомнил место «%s»: %d %d %d" % ((name,) + tuple(int(round(v)) for v in pos))

    def world_places(self):
        return [p for p in self.places if p["world"] == self.world]

    def place(self, name):
        q = stems(name)
        best, score = None, 0
        for p in self.world_places():
            s = len(q & stems(p["name"])) + (2 if name.strip().lower() == p["name"] else 0)
            if s > score:
                best, score = p, s
        return best

    @staticmethod
    def place_line(p):
        x, y, z = p["pos"]
        dim = "" if p.get("dim", "minecraft:overworld") == "minecraft:overworld" else ", " + p["dim"]
        return "«%s»: %d %d %d%s" % (p["name"], x, y, z, dim)

    # ------------------------------------------------------------------ experience: learning from what happened
    RULE_RE = re.compile(r"\b(никогда|не\s+(?:надо|нужно|смей|ломай|трогай|бери|ходи|копай|стреляй|разбирай|лезь|делай)|"
                         r"перестань|хватит|запрещаю|нельзя|всегда|впредь|в следующий раз)\b", re.I)

    def learn(self, kind, key, text, world=True):
        """Remember a lesson. The same lesson again only counts up (and moves to the front): repeated failures weigh more.
        world=False: true in every world (a recipe, a rule); True: only here (where a machine stands, no trees here)."""
        text = re.sub(r"\s+", " ", text or "").strip()[:300]
        if not text:
            return None
        w = self.world if world else ""
        new = stems(text)
        for les in self.lessons:
            if les["kind"] == kind and les["key"] == key and les["world"] == w:
                old = stems(les["text"])
                if kind in ("success", "bad_recipe", "production") or (new and old and len(new & old) / len(new | old) > 0.6):
                    les.update(t=time.time(), text=text, n=les.get("n", 1) + 1)
                    self._save()
                    return les
        les = {"t": time.time(), "kind": kind, "key": key, "world": w, "text": text, "n": 1}
        self.lessons.append(les)
        del self.lessons[:-400]
        self._save()
        return les

    def rule_from(self, phrase):
        """The commander said how things must (not) be done: that is a rule for good, not just talk."""
        if phrase and self.RULE_RE.search(phrase) and len(phrase) < 250:
            return self.learn("rule", "", phrase, world=False)
        return None

    def lessons_here(self):
        return [les for les in self.lessons if les["world"] in ("", self.world)]

    def bad_recipes(self):
        """Recipes a machine refused here or anywhere: the planner takes another way if there is one."""
        return {les["key"] for les in self.lessons if les["kind"] == "bad_recipe"}

    def lessons_for(self, text, ids=(), limit=6):
        """The experience that fits a request: all the commander's rules, then lessons about the same items/words."""
        rules = [les for les in self.lessons_here() if les["kind"] == "rule"][-8:]
        q = stems(text)
        scored = []
        for les in self.lessons_here():
            if les["kind"] == "rule":
                continue
            s = (5 if les["key"] and les["key"] in ids else 0) + len(q & stems(les["text"] + " " + les["key"]))
            if s >= 2:
                scored.append((s + min(les.get("n", 1), 3), les["t"], les))
        scored.sort(key=lambda x: (-x[0], -x[1]))
        out = []
        if rules:
            out.append("Правила командира (соблюдай всегда):\n" + "\n".join("- " + r["text"] for r in rules))
        if scored:
            mark = {"success": "получилось", "failure": "НЕ получилось", "bad_recipe": "машина не приняла",
                    "production": "производство"}
            out.append("Твой опыт (учти, не повторяй ошибок):\n" + "\n".join(
                "- [%s%s] %s" % (mark.get(les["kind"], les["kind"]), ", %d раз" % les["n"] if les.get("n", 1) > 1 else "", les["text"])
                for _, _, les in scored[:limit]))
        return "\n".join(out)

    # ------------------------------------------------------------------ recall
    def search(self, query, limit=8, older_than=None):
        """Facts, places and journal lines that share words with the query, best first."""
        q = stems(query)
        if not q:
            return []
        scored = []
        for f in self.facts:
            s = len(q & stems(f["text"]))
            if s:
                scored.append((s * 3, f["t"], "факт: %s" % f["text"]))
        for p in self.world_places():
            s = len(q & stems(p["name"]))
            if s:
                scored.append((s * 3, p["t"], "место " + self.place_line(p)))
        for e in self.journal:
            if older_than is not None and e["t"] > older_than:
                continue
            s = len(q & stems(e["text"]))
            if e["who"] == "Альтрон":
                # his own words are weaker evidence than what the commander said and what really happened
                low = e["text"].lower()
                if "не помн" in low or "не зна" in low or "провер" in low:
                    continue
                s -= 1
            if s >= (1 if len(q) <= 2 else 2):
                scored.append((s, e["t"], "%s %s: %s" % (when(e["t"]), e["who"], e["text"][:220])))
        scored.sort(key=lambda x: (-x[0], -x[1]))
        return [line for _, _, line in scored[:limit]]

    def context_for(self, text, session_start=None, ids=()):
        """The [Память] block for a prompt: the commander's rules and fitting experience, facts, known places,
        related old conversations."""
        parts = []
        exp = self.lessons_for(text, ids)
        if exp:
            parts.append(exp)
        facts = self.facts
        if sum(len(f["text"]) for f in facts) > FACTS_ALWAYS_CHARS:
            related = [f for f in facts if stems(text) & stems(f["text"])]
            facts = related[:8] + [f for f in facts[-4:] if f not in related[:8]]
        if facts:
            parts.append("Факты, которые ты помнишь:\n" + "\n".join("- " + f["text"] for f in facts))
        places = self.world_places()
        if places:
            parts.append("Известные места: " + "; ".join(self.place_line(p) for p in places[-15:]))
        old = self.search(text, 4, older_than=session_start)
        old = [line for line in old if not line.startswith(("факт:", "место"))]
        if old:
            parts.append("Из прошлого (журнал):\n" + "\n".join("- " + line for line in old))
        return "\n".join(parts)

    def last_conversation(self, n=8):
        """What was said right before the restart: to pick up where we stopped."""
        lines = [e for e in self.journal if e["t"] < self.started][-n:]
        return "\n".join("- %s %s: %s" % (when(e["t"]), e["who"], e["text"][:200]) for e in lines)

    def overview(self):
        places = self.world_places()
        text = "В долгой памяти: %d фактов, %d мест в этом мире, %d уроков опыта, %d записей журнала (с %s)." % (
            len(self.facts), len(places), len(self.lessons_here()), len(self.journal),
            when(self.journal[0]["t"]) if self.journal else "—")
        if self.facts:
            text += "\nФакты: " + "; ".join(f["text"] for f in self.facts[-10:])
        if places:
            text += "\nМеста: " + "; ".join(self.place_line(p) for p in places[-10:])
        return text
