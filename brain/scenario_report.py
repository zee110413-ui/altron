"""The report over scenarios.py results: how many passed in each group, which checks fail most and why, how fast the AI
answered — and every conversation in short, to be read through (review.txt)."""
import gzip
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path


def load(folder):
    rows = {}
    for f in sorted(Path(folder).rglob("results*.jsonl*")):
        data = gzip.decompress(f.read_bytes()).decode("utf-8") if f.suffix == ".gz" else f.read_text(encoding="utf-8")
        for line in data.splitlines():
            if line.strip():
                r = json.loads(line)
                rows[r["id"]] = r
    return sorted(rows.values(), key=lambda r: r["id"])


def short(text, n=160):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n - 1] + "…"


def review_text(r):
    out = ["=== %s [%s] %s — %s" % (r["id"], r["group"], r.get("title", ""), "OK" if r.get("passed") else "FAIL")]
    for s in r.get("steps", []):
        if "say" in s:
            out.append(">> %s: %s" % (s.get("who", "командир"), s["say"]))
        else:
            out.append(">> %s" % short(json.dumps(s, ensure_ascii=False), 200))
    for e in r.get("events", []):
        if e["kind"] == "say":
            out.append("   АЛЬТРОН: %s" % e["text"])
        elif e["kind"] == "tool":
            out.append("   [%s]" % short(e["text"], 140))
        elif e["kind"] == "step":
            out.append("   --")
    if r.get("error"):
        out.append("   ОШИБКА: %s" % r["error"])
    fails = [c for c in r.get("checks", []) if not c["ok"]]
    for c in fails:
        out.append("   ✗ %s: %s" % (c["name"], c["why"]))
    llm = [x for x in r.get("llm", []) if "sec" in x]
    if llm:
        out.append("   (ходов ИИ %d, %.0f с)" % (len(llm), sum(x["sec"] for x in llm)))
    return "\n".join(out)


def write(folder):
    folder = Path(folder)
    rows = load(folder)
    if not rows:
        return "нет результатов в %s" % folder
    by_group = defaultdict(list)
    for r in rows:
        by_group[r["group"]].append(r)
    fails = Counter()
    why = defaultdict(list)
    for r in rows:
        for c in r.get("checks", []):
            if not c["ok"]:
                fails[c["name"]] += 1
                why[c["name"]].append("%s «%s»: %s" % (r["id"], short(" / ".join(s.get("say", "") for s in r["steps"]), 70),
                                                       short(c["why"], 150)))
    turns = [x["sec"] for r in rows for x in r.get("llm", []) if "sec" in x and "error" not in x]
    said = [s for r in rows for s in r.get("said", [])]
    lines = ["# Сценарии Альтрона: %d" % len(rows), "",
             "Прошли: **%d из %d** (%.0f%%)" % (sum(1 for r in rows if r.get("passed")), len(rows),
                                               100 * sum(1 for r in rows if r.get("passed")) / len(rows)), "",
             "| Группа | Прошли | Всего |", "|---|---|---|"]
    for g, rs in sorted(by_group.items()):
        lines.append("| %s | %d | %d |" % (g, sum(1 for r in rs if r.get("passed")), len(rs)))
    lines += ["", "## Чаще всего не проходит", ""]
    for name, n in fails.most_common():
        lines.append("### %s — %d" % (name, n))
        lines += ["- " + w for w in why[name][:12]]
        lines.append("")
    if turns:
        lines += ["## Скорость ИИ на этой машине", "",
                  "Ходов ИИ: %d; медиана %.1f с, 90%% — до %.1f с" % (len(turns), statistics.median(turns),
                                                                   sorted(turns)[int(len(turns) * 0.9) - 1]), ""]
    if said:
        common = Counter(said).most_common(15)
        lines += ["## Фразы, которые повторяются в разных сценариях", ""]
        lines += ["- %d× %s" % (n, short(s, 140)) for s, n in common if n > 2]
        lines.append("")
    (folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (folder / "review.txt").write_text("\n\n".join(review_text(r) for r in rows) + "\n", encoding="utf-8")
    return "%s, %s" % (folder / "report.md", folder / "review.txt")
