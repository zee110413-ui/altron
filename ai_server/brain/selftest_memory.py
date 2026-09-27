"""Self-test of the long-term memory: facts, places, journal, recall and surviving a restart."""
import sys
import tempfile
import time

from memory import LongMemory, stems

d = tempfile.mkdtemp(prefix="altron_mem_test_")
ok = True


def check(name, cond, info=""):
    global ok
    ok &= bool(cond)
    print(("OK   " if cond else "FAIL ") + name + ("" if cond else "  -> " + str(info)))


m = LongMemory(d)
m.world = "local_0_0"
check("stems: forms of a word match", stems("алмазы") == stems("алмазов") == stems("алмаз"), stems("алмазов"))
check("stems: база/базу/базе", stems("база") == stems("базу") == stems("базе"), (stems("база"), stems("базу")))

print(m.remember("Егор живёт в доме у озера"))
print(m.remember("Мои алмазы лежат в сундуке на базе"))
print(m.remember("Егор живет в доме у озера"))   # the same fact again: updated, not duplicated
check("remember: no duplicates", len(m.facts) == 2, m.facts)

print(m.set_place("база", [100.4, 64, -20.6], "minecraft:overworld"))
print(m.set_place("шахта", [150, 12, -40], "minecraft:overworld"))
print(m.set_place("база", [101, 65, -21], "minecraft:overworld"))  # moved: replaced
check("places: replaced by name", len(m.world_places()) == 2 and m.place("базу")["pos"] == [101, 65, -21], m.places)
check("places: fuzzy name", m.place("на шахту") is not None and m.place("на шахту")["name"] == "шахта")

m.log("Egor", "Альтрон, добудь 10 алмазов для брони")
m.log("Альтрон", "Принял, иду в шахту за алмазами")
m.log("событие", "obtain 10x Алмаз — ГОТОВО: в инвентаре 10x Алмаз")
hits = m.search("где алмазы?")
print("search:", hits)
check("search: finds the fact and the journal", any("сундуке" in h for h in hits) and any("добудь 10" in h for h in hits), hits)

ctx = m.context_for("пойдём на базу", session_start=time.time() + 1)
print("context:\n" + ctx)
check("context: facts and places", "Егор жив" in ctx and "«база»" in ctx, ctx)

print(m.forget("шахта"))
check("forget: place removed", m.place("шахта") is None)

# restart: everything is read back from disk
m2 = LongMemory(d)
m2.world = "local_0_0"
check("restart: facts kept", len(m2.facts) == 2, m2.facts)
check("restart: places kept", m2.place("база") is not None)
check("restart: journal kept", len(m2.journal) == 3, m2.journal)
last = m2.last_conversation()
print("last conversation:\n" + last)
check("restart: last conversation", "добудь 10 алмазов" in last, last)
m2.world = "other_world"
check("places are per world", m2.place("база") is None)
print(m2.overview())

print("\nВСЁ ХОРОШО" if ok else "\nЕСТЬ ОШИБКИ")
sys.exit(0 if ok else 1)
