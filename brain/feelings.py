"""Altron's inner state: his mood and how he feels about each player.

Both are the AI's own: it sets its mood (the "feel" tool) and changes its attitude to a player (the "relation" tool)
when something touches it. The code only keeps them, lets a mood fade with time, shows them to the AI at every turn
and lets the voice follow the mood. It never decides what he feels."""
import json
import os
import re
import time

# mood -> how the voice sounds with it (speech.TTS.MOODS), and its name for the AI in both languages
MOODS = {
    "calm": (None, "спокоен", "calm"),
    "happy": ("excited", "рад", "happy"),
    "excited": ("excited", "в азарте", "excited"),
    "proud": ("excited", "гордишься", "proud"),
    "amused": ("excited", "тебя это забавляет", "amused"),
    "bored": ("bored", "скучаешь", "bored"),
    "annoyed": ("cold", "раздражён", "annoyed"),
    "offended": ("cold", "обижен", "offended"),
    "sad": ("sad", "грустишь", "sad"),
    "worried": ("alert", "встревожен", "worried"),
    "tired": ("sad", "устал", "tired"),
}
FADE_SEC = 15 * 60          # a mood without a new reason fades back to calm
SCORE_MIN, SCORE_MAX = -10, 10
ATTITUDE = [                # score -> what it means, ru / en
    (7, "очень ему доверяешь, он тебе как близкий друг", "you trust them a lot, a close friend"),
    (3, "относишься тепло", "you like them"),
    (-2, "пока нейтрально", "neutral so far"),
    (-6, "относишься настороженно", "you are wary of them"),
    (SCORE_MIN, "не доверяешь ему", "you do not trust them"),
]


def _key(name):
    return re.sub(r"[^a-zа-яё0-9]", "", str(name or "").lower())


class Feelings:
    def __init__(self, folder):
        self.file = os.path.join(str(folder), "feelings.json")
        self.mood, self.why, self.since = "calm", "", 0.0
        self.relations = {}    # key -> {"name", "score", "notes": [(t, delta, why)]}
        try:
            with open(self.file, encoding="utf-8") as f:
                data = json.load(f)
            self.mood, self.why, self.since = data.get("mood", "calm"), data.get("why", ""), data.get("since", 0.0)
            self.relations = data.get("relations", {})
        except (OSError, ValueError):
            pass

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.file), exist_ok=True)
            tmp = self.file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"mood": self.mood, "why": self.why, "since": self.since, "relations": self.relations},
                          f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.file)
        except OSError:
            pass

    # ------------------------------------------------------------------ mood
    def current(self, now=None):
        now = now or time.time()
        if self.mood != "calm" and now - self.since > FADE_SEC:
            self.mood, self.why = "calm", ""
        return self.mood

    def feel(self, mood, why=""):
        mood = str(mood or "").strip().lower()
        if mood not in MOODS:
            return "ОШИБКА: настроение — одно из: %s" % ", ".join(MOODS)
        self.mood, self.why, self.since = mood, str(why or "").strip()[:160], time.time()
        self._save()
        return "настроение: %s%s" % (MOODS[mood][1], " (%s)" % self.why if self.why else "")

    def voice_mood(self):
        """How the voice should sound now; None: as usual."""
        return MOODS[self.current()][0]

    # ------------------------------------------------------------------ players
    def relate(self, name, delta, why=""):
        name = str(name or "").strip()
        k = _key(name)
        if not k:
            return "ОШИБКА: player — ник игрока"
        try:
            delta = max(-3, min(3, int(round(float(delta)))))
        except (TypeError, ValueError):
            return "ОШИБКА: change — число от -3 до 3"
        r = self.relations.setdefault(k, {"name": name, "score": 0, "notes": []})
        r["name"] = name
        r["score"] = max(SCORE_MIN, min(SCORE_MAX, r["score"] + delta))
        if why:
            r["notes"] = (r["notes"] + [[time.time(), delta, str(why)[:120]]])[-5:]
        self._save()
        return "отношение к %s: %+d (%s)" % (name, r["score"], self.attitude(r["score"], True))

    @staticmethod
    def attitude(score, ru=True):
        for bound, text_ru, text_en in ATTITUDE:
            if score >= bound:
                return text_ru if ru else text_en
        return ATTITUDE[-1][1 if ru else 2]

    def about(self, name, ru=True):
        r = self.relations.get(_key(name))
        if not r:
            return ""
        notes = "; ".join(n[2] for n in r["notes"][-3:])
        return "%s: %s%s" % (r["name"], self.attitude(r["score"], ru), " (%s)" % notes if notes else "")

    # ------------------------------------------------------------------ for the AI
    def text(self, players=(), ru=True):
        """[Ты сейчас] block: his mood and his attitude to the players involved."""
        mood = self.current()
        if ru:
            line = "настроение: %s%s" % (MOODS[mood][1], " — %s" % self.why if self.why and mood != "calm" else "")
        else:
            line = "mood: %s%s" % (MOODS[mood][2], " — %s" % self.why if self.why and mood != "calm" else "")
        rel = [a for a in (self.about(p, ru) for p in dict.fromkeys(players) if p) if a]
        if rel:
            line += ("; к игрокам: " if ru else "; players: ") + "; ".join(rel)
        return line
