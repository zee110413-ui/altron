"""Self-test: build the modpack encyclopedia and run searches / plans.
Usage: selftest_knowledge.py "query" ... ; prefix a query with plan: to expand a recipe tree, ctx: for auto-context."""
import json
import sys
import time

from knowledge import Knowledge
from launcher import BRAIN_DIR

cfg = json.loads((BRAIN_DIR / "config.json").read_text(encoding="utf-8"))
t = time.time()
k = Knowledge.load(cfg)
named = sum(1 for e in k.entries.values() if e["ru"] or e["en"])
print("Справочник: %d предметов/блоков с названиями, %d рецептов, %d построек, %d страниц руководства, %d модов (%.1f с)"
      % (named, len(k.recipes), len(k.multiblocks), len(k.manual), len(k.mods), time.time() - t))
for q in sys.argv[1:]:
    print("\n==========", q)
    if q.startswith("plan:"):
        print(k.plan(q[5:]))
    elif q.startswith("ctx:"):
        print(k.context_for(q[4:]) or "(нет подсказок)")
    else:
        print(k.search(q, 3))
