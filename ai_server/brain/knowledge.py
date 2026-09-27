"""Modpack encyclopedia built from the real mod files: names (ru/en), descriptions, recipes of every type,
tags, TaCZ guns and ammo, Immersive Engineering / Petroleum multiblocks and manual pages. Searchable offline."""
import gzip
import io
import json
import os
import re
import struct
import zipfile
from collections import defaultdict
from pathlib import Path

from launcher import BRAIN_DIR, rel

CACHE = BRAIN_DIR / "knowledge_cache.json"
CACHE_VERSION = 8


# ---------------------------------------------------------------------------- helpers

def loads_lenient(text):
    """JSON with // and /* */ comments and trailing commas (TaCZ gun packs use them)."""
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        else:
            out.append(c)
        i += 1
    s = re.sub(r",(\s*[}\]])", r"\1", "".join(out)).lstrip("﻿")
    try:
        return json.loads(s, strict=False)
    except json.JSONDecodeError:
        # hand-edited lang files forget commas between lines ("a": "x"\n "b": "y"; Immersive Petroleum's ru_ru)
        s = re.sub(r'(["}\]]|\d|true|false|null)(\s*\n\s*)"', r"\1,\2" + '"', s)
        return json.loads(s, strict=False)


def clean(s):
    return re.sub(r"§.", "", str(s)).strip()


def stem(w):
    for e in ("ами", "ями", "ого", "его", "ому", "ему", "ыми", "ими", "ов", "ев", "ей", "ам", "ям", "ах", "ях",
              "ой", "ый", "ий", "ая", "яя", "ое", "ее", "ые", "ие", "ую", "юю", "ы", "и", "а", "я", "у", "ю", "е", "о", "s"):
        if len(w) - len(e) >= 3 and w.endswith(e):
            return w[: -len(e)]
    return w


# how people say it vs how the item is named: an "anti-bunker missile" is the Bunker Buster, "балистическая" is a typo
SYNONYMS = [(re.compile(r"противобункерн"), "бункерн"), (re.compile(r"балистич"), "баллистич")]


def tokens(s):
    s = str(s).lower().replace("ё", "е")
    for rx, to in SYNONYMS:
        s = rx.sub(to, s)
    return [stem(w) for w in re.findall(r"[a-zа-я0-9]+", s)]


def read_nbt(data):
    """Minimal NBT reader (gzip or raw). Returns python dicts/lists."""
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    f = io.BytesIO(data)

    def r(fmt):
        return struct.unpack(">" + fmt, f.read(struct.calcsize(fmt)))[0]

    def rstr():
        return f.read(r("H")).decode("utf-8", "replace")

    def payload(t):
        if t == 1: return r("b")
        if t == 2: return r("h")
        if t == 3: return r("i")
        if t == 4: return r("q")
        if t == 5: return r("f")
        if t == 6: return r("d")
        if t == 7: return f.read(r("i"))
        if t == 8: return rstr()
        if t == 9:
            et, ln = r("b"), r("i")
            return [payload(et) for _ in range(ln)]
        if t == 10:
            d = {}
            while True:
                tt = r("b")
                if tt == 0:
                    return d
                key = rstr()  # read the name before the value (d[rstr()] = ... would evaluate the value first)
                d[key] = payload(tt)
        if t == 11: return [r("i") for _ in range(r("i"))]
        if t == 12: return [r("q") for _ in range(r("i"))]
        raise ValueError("bad nbt tag %d" % t)

    t = r("b")
    rstr()
    return payload(t)


class Source:
    """Uniform access to files inside a jar/zip or a folder."""

    def __init__(self, path):
        self.path = Path(path)
        self.zip = zipfile.ZipFile(path) if self.path.is_file() else None

    def names(self):
        if self.zip:
            return [n for n in self.zip.namelist() if not n.endswith("/")]
        return [p.relative_to(self.path).as_posix() for p in self.path.rglob("*") if p.is_file()]

    def read(self, name):
        if self.zip:
            return self.zip.read(name)
        return (self.path / name).read_bytes()


# ---------------------------------------------------------------------------- the encyclopedia

class Knowledge:
    def __init__(self):
        self.entries = {}          # id -> {"id","kind","mod","ru","en","desc":[...], ...}
        self.tags = defaultdict(set)
        self.recipes = []          # {"id","type","in":[[ref,n]],"out":[[ref,n]]}
        self.multiblocks = {}      # name -> {"mod","materials":{id:n},"size":[x,y,z]}
        self.manual = []           # {"mod","entry","title","text"}
        self.mods = {}             # modid -> {"name","desc"}
        self.disabled = set()      # items the pack switched off with Item Obliterator (exact ids)
        self.disabled_re = []      # ... and by regular expression ("!minecraft:.*_chestplate")
        self._index = None

    # ------------------------------------------------------------ items switched off in this pack
    def load_disabled(self, cfg, log=print):
        """Item Obliterator's blacklist: such items cannot be crafted or used in this pack, so Altron must not plan them."""
        path = rel(cfg["minecraft_dir"]) / "versions" / cfg["pack_version"] / "config" / "item_obliterator.json5"
        if not path.exists():
            return
        try:
            data = loads_lenient(path.read_text(encoding="utf-8", errors="replace"))
        except Exception as e:
            log("справочник: не прочитал item_obliterator.json5 (%s)" % e)
            return
        for v in data.get("blacklisted_items", []) + data.get("only_disable_recipes", []):
            if not isinstance(v, str) or v.startswith("examplemod:"):
                continue
            if v.startswith("!"):
                try:
                    self.disabled_re.append(re.compile(v[1:]))
                except re.error:
                    pass
            else:
                self.disabled.add(v)

    def is_disabled(self, id_):
        return id_ in self.disabled or any(r.fullmatch(id_) for r in self.disabled_re)

    def disabled_note(self, id_):
        return "%s ОТКЛЮЧЁН в этой сборке (мод Item Obliterator): его нельзя сделать и использовать" % self.name(id_)

    # ------------------------------------------------------------ building
    def entry(self, id_, kind="item"):
        e = self.entries.get(id_)
        if e is None:
            e = self.entries[id_] = {"id": id_, "kind": kind, "mod": id_.split(":")[0], "ru": "", "en": "", "desc": []}
        return e

    def add_lang(self, lang, data):
        field = "ru" if lang == "ru_ru" else "en"
        for key, text in data.items():
            if not isinstance(text, str):
                continue
            text = clean(text)
            parts = key.split(".")
            if len(parts) >= 3 and parts[0] in ("item", "block", "entity", "fluid", "fluid_type"):
                kind = {"fluid_type": "fluid"}.get(parts[0], parts[0])
                id_ = parts[1] + ":" + parts[2]
                e = self.entry(id_, kind if id_ not in self.entries else self.entries[id_]["kind"])
                if len(parts) == 3:
                    if not e[field] or field == "ru":
                        e[field] = text
                elif any(w in key for w in ("desc", "tooltip", "lore", "info", "hint", "usage")) and len(e["desc"]) < 6:
                    if text not in e["desc"]:
                        e["desc"].append(text)
            elif parts[0] == "help" and len(parts) >= 4 and parts[3] == "info":
                # SecurityCraft's own "how to use it" pages: help.securitycraft.remote_access_sentry.info
                e = self.entry(parts[1] + ":" + parts[2])
                if text not in e["desc"] and (field == "ru" or not any(len(d) > 150 for d in e["desc"])):
                    e["desc"].insert(0, text)
            elif parts[0] == "tacz" and len(parts) >= 4 and parts[1] in ("gun", "ammo", "attachment"):
                id_ = "tacz:" + parts[2]
                e = self.entry(id_, parts[1])
                if parts[3] == "name":
                    e[field] = text
                elif parts[3] in ("desc", "tooltip") and text not in e["desc"]:
                    e["desc"].append(text)

    def add_tag(self, ns, path, data):
        tag = "#%s:%s" % (ns, path)
        for v in data.get("values", []):
            if isinstance(v, dict):
                v = v.get("id", "")
            if v:
                self.tags[tag].add(v if not v.startswith("#") else v)

    def resolve_tag(self, tag, depth=0):
        out = []
        for v in sorted(self.tags.get(tag, ())):
            if v.startswith("#"):
                if depth < 4:
                    out.extend(self.resolve_tag(v, depth + 1))
            else:
                out.append(v)
        return out

    def _refs(self, obj, role, out_in, out_out):
        """Collect item/tag/fluid references from any recipe format."""
        if isinstance(obj, list):
            for v in obj:
                self._refs(v, role, out_in, out_out)
            return
        if not isinstance(obj, dict):
            return
        target = out_out if role == "out" else out_in
        chance = obj.get("chance", 1)
        if role == "out" and isinstance(chance, (int, float)) and chance < 1:
            return  # random by-products are not a reliable way to make an item
        count = obj.get("count", obj.get("amount", 1))
        if not isinstance(count, (int, float)):
            count = 1
        inner = obj.get("item") if isinstance(obj.get("item"), dict) else None
        if inner is not None:  # TaCZ: {"item": {"tag": ...}, "count": N}
            ref = inner.get("item") or ("#" + inner["tag"] if "tag" in inner else None)
            if ref:
                target.append([ref, int(count)])
            return
        for key in ("item", "tag", "fluid", "id"):
            if isinstance(obj.get(key), str) and (key != "id" or obj.get("type") in ("gun", "ammo", "attachment")):
                ref = obj[key] if key != "tag" else "#" + obj[key]
                target.append([ref, int(count)])
                return
        for k, v in obj.items():
            if k in ("type", "conditions", "pattern", "key", "count", "amount", "group", "category", "experience",
                     "cookingtime", "time", "energy"):
                continue
            sub_role = "out" if any(w in k.lower() for w in ("result", "output", "secondar")) else role
            if isinstance(v, str) and sub_role == "out" and ":" in v:
                out_out.append([v, 1])
            elif isinstance(v, str) and k.lower() in ("ingredient", "input", "base", "addition", "template", "mold",
                                                       "catalyst", "item") and ":" in v:
                out_in.append([v, 1])
            else:
                self._refs(v, sub_role, out_in, out_out)

    def add_recipe(self, rid, data):
        if not isinstance(data, dict):
            return
        # disassembly/recycling recipes (cabinet -> steel plates) are not a way to MAKE something
        if re.search(r"disassembl|breakdown|recycl|salvage", rid) or data.get("overlay") == "recycling":
            return
        rtype = data.get("type", "")
        if rtype == "forge:conditional":
            for r in data.get("recipes", []):
                self.add_recipe(rid, r.get("recipe"))
            return
        ins, outs = [], []
        if "pattern" in data and isinstance(data.get("key"), dict):
            counts = defaultdict(int)
            for row in data["pattern"]:
                for ch in row:
                    if ch != " ":
                        counts[ch] += 1
            for ch, n in counts.items():
                tmp = []
                self._refs(data["key"].get(ch), "in", tmp, [])
                if tmp:
                    ins.append([tmp[0][0], n])
            self._refs(data.get("result"), "out", ins, outs)
        elif "materials" in data:  # TaCZ gunsmith table
            self._refs(data["materials"], "in", ins, outs)
            self._refs(data.get("result"), "out", ins, outs)
        else:
            self._refs({k: v for k, v in data.items() if k != "type"}, "in", ins, outs)
        if outs:
            rec = {"id": rid, "type": rtype, "in": ins, "out": outs}
            if data.get("blueprint_pool"):
                rec["pool"] = data["blueprint_pool"]   # HBM: only with a blueprint folder in the machine
            self.recipes.append(rec)

    def add_structure(self, mod, name, data):
        try:
            nbt = read_nbt(data)
        except Exception:
            return
        palette = [p.get("Name", "?") for p in nbt.get("palette", [])]
        mats = defaultdict(int)
        for b in nbt.get("blocks", []):
            st = b.get("state", 0)
            n = palette[st] if st < len(palette) else "?"
            if n not in ("minecraft:air", "minecraft:structure_void", "?"):
                mats[n] += 1
        self.multiblocks[name] = {"mod": mod, "materials": dict(mats), "size": nbt.get("size", [])}

    def add_manual(self, mod, entry, text):
        lines = text.splitlines()
        title = clean(lines[0]) if lines else entry
        body = "\n".join(lines[2:]) if len(lines) > 2 else text
        body = re.sub(r"<link;[^;>]*;([^;>]*)(;[^>]*)?>", r"\1", body)
        body = re.sub(r"<np>", "\n", body)
        body = re.sub(r"<[^>]*>", "", body)
        body = clean(re.sub(r"[ \t]+", " ", body))
        self.manual.append({"mod": mod, "entry": entry, "title": title, "text": body})

    def scan_source(self, src, modid_hint=""):
        names = src.names()
        manual_ru = {n for n in names if "/manual/ru_ru/" in n}
        for n in names:
            try:
                m = re.match(r"assets/([^/]+)/lang/(ru_ru|en_us)\.json$", n)
                if m:
                    self.add_lang(m.group(2), loads_lenient(src.read(n).decode("utf-8", "replace")))
                    continue
                m = re.match(r"data/([^/]+)/recipes/(.+)\.json$", n)
                if m:
                    self.add_recipe(m.group(1) + ":" + m.group(2), loads_lenient(src.read(n).decode("utf-8", "replace")))
                    continue
                m = re.match(r"data/([^/]+)/tags/items/(.+)\.json$", n)
                if m:
                    self.add_tag(m.group(1), m.group(2), loads_lenient(src.read(n).decode("utf-8", "replace")))
                    continue
                m = re.match(r"data/([^/]+)/structures/multiblocks/(.+)\.nbt$", n)
                if m:
                    self.add_structure(m.group(1), m.group(2), src.read(n))
                    continue
                m = re.match(r"assets/([^/]+)/manual/(ru_ru|en_us)/(.+)\.txt$", n)
                if m:
                    ru_twin = n.replace("/en_us/", "/ru_ru/")
                    if m.group(2) == "en_us" and ru_twin in manual_ru:
                        continue
                    self.add_manual(m.group(1), m.group(3), src.read(n).decode("utf-8", "replace"))
                    continue
                m = re.match(r"data/([^/]+)/index/guns/(.+)\.json$", n)
                if m:
                    idx = loads_lenient(src.read(n).decode("utf-8", "replace"))
                    e = self.entry("%s:%s" % (m.group(1), m.group(2)), "gun")
                    e["gun_type"] = idx.get("type", "")
                    data_id = idx.get("data", "")
                    if ":" in data_id:
                        ns, p = data_id.split(":", 1)
                        dn = "data/%s/data/guns/%s.json" % (ns, p)
                        if dn in names:
                            d = loads_lenient(src.read(dn).decode("utf-8", "replace"))
                            e["ammo"] = d.get("ammo", "")
                            e["magazine"] = d.get("ammo_amount", "")
                            e["rpm"] = d.get("rpm", "")
                            bullet = d.get("bullet", {})
                            if isinstance(bullet, dict):
                                e["damage"] = bullet.get("damage", "")
                    continue
                if n == "META-INF/mods.toml":
                    t = src.read(n).decode("utf-8", "replace")
                    mid = re.search(r'modId\s*=\s*"([^"]+)"', t)
                    name = re.search(r'displayName\s*=\s*"([^"]+)"', t)
                    desc = re.search(r"description\s*=\s*(?:'''|\")([\s\S]*?)(?:'''|\")", t)
                    if mid:
                        self.mods[mid.group(1)] = {"name": name.group(1) if name else mid.group(1),
                                                   "desc": re.sub(r"\s+", " ", desc.group(1)).strip()[:200] if desc else ""}
            except Exception:
                continue

    def build(self, cfg, log=print):
        mc = rel(cfg["minecraft_dir"])
        pack = mc / "versions" / cfg["pack_version"]
        sources = [pack / (cfg["pack_version"] + ".jar")]
        forge = list((mc / "libraries" / "net" / "minecraftforge" / "forge").glob("*/forge-*-universal.jar"))
        sources += forge
        sources += sorted((pack / "mods").glob("*.jar"))
        if (pack / "tacz").exists():
            sources += [p for p in (pack / "tacz").iterdir() if p.is_dir() or p.suffix == ".zip"]
        for s in sources:
            try:
                self.scan_source(Source(s))
            except Exception as e:
                log("справочник: пропускаю %s (%s)" % (s.name, e))
        # vanilla Russian names live in the assets index
        try:
            idx = json.loads((mc / "assets" / "indexes" / "5.json").read_text(encoding="utf-8"))
            h = idx["objects"]["minecraft/lang/ru_ru.json"]["hash"]
            self.add_lang("ru_ru", json.loads((mc / "assets" / "objects" / h[:2] / h).read_text(encoding="utf-8")))
        except Exception as e:
            log("справочник: нет русских названий Minecraft (%s)" % e)
        self.mods.setdefault("minecraft", {"name": "Minecraft", "desc": ""})

    # ------------------------------------------------------------ cache
    @staticmethod
    def fingerprint(cfg):
        pack = rel(cfg["minecraft_dir"]) / "versions" / cfg["pack_version"]
        items = sorted("%s:%d" % (p.name, p.stat().st_size) for p in (pack / "mods").glob("*.jar"))
        return "%d|%s" % (CACHE_VERSION, "|".join(items))

    @classmethod
    def load(cls, cfg, log=print):
        fp = cls.fingerprint(cfg)
        k = cls()
        k.load_disabled(cfg, log)   # read every time: the pack's config may change without new mods
        if CACHE.exists():
            try:
                d = json.loads(CACHE.read_text(encoding="utf-8"))
                if d.get("fp") == fp:
                    k.entries, k.recipes, k.multiblocks, k.manual, k.mods = (
                        d["entries"], d["recipes"], d["multiblocks"], d["manual"], d["mods"])
                    k.tags = defaultdict(set, {t: set(v) for t, v in d["tags"].items()})
                    return k
            except Exception:
                pass
        log("Собираю справочник по модам (один раз, ~1 мин)...")
        k.build(cfg, log)
        CACHE.write_text(json.dumps({"fp": fp, "entries": k.entries, "recipes": k.recipes,
                                     "multiblocks": k.multiblocks, "manual": k.manual, "mods": k.mods,
                                     "tags": {t: sorted(v) for t, v in k.tags.items()}}, ensure_ascii=False),
                         encoding="utf-8")
        return k

    # ------------------------------------------------------------ search
    def _build_index(self):
        by_out, by_in = defaultdict(list), defaultdict(list)
        for i, r in enumerate(self.recipes):
            for ref, _ in r["out"]:
                by_out[ref].append(i)
            for ref, _ in r["in"]:
                by_in[ref].append(i)
        docs = []
        for e in self.entries.values():
            if not (e["ru"] or e["en"]):
                continue
            docs.append(("entry", e["id"], set(tokens(e["id"].replace(":", " ").replace("_", " "))),
                         set(tokens(e["ru"] + " " + e["en"])), set(tokens(" ".join(e["desc"])))))
        for name, mb in self.multiblocks.items():
            e = self.entries.get("%s:%s" % (mb["mod"], name), {})
            titles = "%s %s %s" % (name, e.get("ru", ""), e.get("en", ""))
            docs.append(("multiblock", name, set(tokens(name.replace("_", " "))), set(tokens(titles)), set()))
        for i, m in enumerate(self.manual):
            docs.append(("manual", i, set(tokens(m["entry"].replace("_", " "))), set(tokens(m["title"])),
                         set(tokens(m["text"][:1500]))))
        self._index = (by_out, by_in, docs)

    def name(self, ref):
        if ref.startswith("#"):
            items = self.resolve_tag(ref)
            if not items:
                return ref
            # show a vanilla example: "any of #minecraft:logs, e.g. Oak Log"
            example = sorted(items, key=lambda i: (not i.startswith("minecraft:"), i))[0]
            e = self.entries.get(example, {})
            return "ЛЮБОЙ предмет из тега %s (например %s [%s])" % (ref, e.get("ru") or e.get("en") or example, example)
        e = self.entries.get(ref)
        if e and (e["ru"] or e["en"]):
            return "%s [%s]" % (e["ru"] or e["en"], ref)
        return ref

    def recipe_text(self, r):
        ins = ", ".join("%dx %s" % (n, self.name(ref)) for ref, n in r["in"][:12])
        outs = ", ".join("%dx %s" % (n, self.name(ref)) for ref, n in r["out"][:3])
        kind = {"minecraft:crafting_shaped": "верстак", "minecraft:crafting_shapeless": "верстак",
                "minecraft:smelting": "печь", "minecraft:blasting": "плавильня", "minecraft:smoking": "коптильня",
                "minecraft:stonecutting": "камнерез", "tacz:gun_smith_table_crafting": "оружейный стол TaCZ"}.get(r["type"], r["type"])
        return "[%s] %s <- %s" % (kind, outs, ins or "?")

    def card(self, e):
        by_out, by_in, _ = self._index
        title = " / ".join(n for n in (e["ru"], e["en"]) if n) or e["id"]
        lines = ["%s  — id %s, мод %s, тип %s" % (title, e["id"], self.mods.get(e["mod"], {}).get("name", e["mod"]), e["kind"])]
        if self.is_disabled(e["id"]):
            lines.append("  ОТКЛЮЧЁН в этой сборке (Item Obliterator): не сделать и не использовать")
            return "\n".join(lines)
        if e.get("desc"):
            lines.append("  описание: " + " | ".join(e["desc"])[:800])
        if e["kind"] == "gun":
            lines.append("  патроны: %s, магазин %s, скорострельность %s/мин, урон %s, класс %s" % (
                self.name(e.get("ammo", "?")), e.get("magazine", "?"), e.get("rpm", "?"), e.get("damage", "?"), e.get("gun_type", "?")))
        made = by_out.get(e["id"], [])
        for i in made[:3]:
            lines.append("  как сделать: " + self.recipe_text(self.recipes[i]))
        if not made:
            lines.append("  как сделать: рецепта нет (добывается, выпадает или находится)")
        used = by_in.get(e["id"], [])
        if used:
            outs = []
            for i in used[:8]:
                for ref, _ in self.recipes[i]["out"][:1]:
                    outs.append(self.name(ref))
            lines.append("  используется для: " + "; ".join(dict.fromkeys(outs)))
        return "\n".join(lines)

    # Things a player mines, smelts or gets from mobs: the plan stops expanding here
    BASE = re.compile(r"(ingot|gem|dust|nugget|raw_|_ore|_log|log$|cobblestone|^minecraft:stone$|sand$|gravel|clay_ball"
                      r"|coal$|charcoal|diamond$|emerald$|lapis_lazuli|redstone$|quartz$|string|leather|gunpowder|bone$|slime_ball"
                      r"|ender_pearl|blaze_rod|obsidian|glass$|paper|feather|flint$|wool$|wheat$|sugar_cane"
                      r"|water$|lava$|milk$|crude_oil$|:oil$|ice$|snow)")
    # items that appear in recipe data only as icons/templates, never as real ingredients
    NOT_INGREDIENT = re.compile(r"(file_cabinet|fluid_identifier|:pain$|stamp_|stamps/|blueprint|template$)")

    RECIPE_RANK = {"minecraft:crafting_shaped": 0, "minecraft:crafting_shapeless": 0, "tacz:gun_smith_table_crafting": 1,
                   "minecraft:smelting": 2, "minecraft:blasting": 3}

    def find_id(self, query):
        """Best matching item/block id for a query (exact id allowed)."""
        if query in self.entries:
            return query
        if self._index is None:
            self._build_index()
        q = tokens(query)
        qs = set(q)
        best, best_s = None, 0
        for kind, key, id_toks, name_toks, _ in self._index[2]:
            if kind != "entry":
                continue
            s = sum(5 if t in name_toks or t in id_toks else 0 for t in q)
            if s:
                # "бункерная баллистическая ракета": the Bunker Buster (every word of its name said), not the Test
                # Ballistic Missile that only shares two words
                e = self.entries[key]
                if any(n and set(tokens(n)) <= qs for n in (e.get("ru"), e.get("en"))):
                    s += 8
            if s > best_s or (s == best_s and best and len(key) < len(best)):
                best, best_s = key, s
        return best if best_s else None

    def plan(self, query, count=1, have=None):
        """Expand the full recipe tree of an item down to base resources.
        With `have` (inventory id -> count) it uses what the bot already carries and lists only what is missing."""
        if self._index is None:
            self._build_index()
        by_out = self._index[0]
        root = self.find_id(query)
        if not root:
            return "Не знаю предмет: " + query
        if self.is_disabled(root):
            return self.disabled_note(root) + "."
        lines, raw, machines = [], defaultdict(int), set()
        stock = defaultdict(int, have or {})
        used = defaultdict(int)

        def from_stock(ref, n):
            """Take up to n from the inventory (any item of a tag); returns how many were taken."""
            took = 0
            for i in (self.resolve_tag(ref) if ref.startswith("#") else [ref]):
                t = min(n - took, stock[i])
                if t > 0:
                    stock[i] -= t
                    used[i] += t
                    took += t
                if took >= n:
                    break
            return took

        def pick(ref):
            recs = [self.recipes[i] for i in by_out.get(ref, [])]
            # avoid recipes that just unpack storage blocks back into the item
            recs = [r for r in recs if not any(ref.split(":")[-1] in i[0] for i in r["in"])] or recs
            recs.sort(key=lambda r: (self.RECIPE_RANK.get(r["type"], 5), len(r["in"])))
            return recs[0] if recs else None

        def expand(ref, n, depth, seen):
            if depth > 0 and have is not None:
                n -= from_stock(ref, n)
                if n <= 0:
                    return
            if ref.startswith("#"):
                items = sorted(self.resolve_tag(ref), key=lambda i: (not i.startswith("minecraft:"), i))
                ref = items[0] if items else ref
            r = None if (depth > 0 and self.BASE.search(ref)) or ref in seen or depth > 7 else pick(ref)
            if r is None:
                if have is not None:
                    # an ingot we lack but whose ore/raw form is in the inventory: smelt it
                    for i in by_out.get(ref, []):
                        sr = self.recipes[i]
                        if sr["type"] in ("minecraft:smelting", "minecraft:blasting") and sr["in"]:
                            took = from_stock(sr["in"][0][0], n)
                            if took:
                                lines.append("%s%dx %s  <- печь (переплавить %s)" % ("  " * depth, took, self.name(ref),
                                                                                 self.name(sr["in"][0][0])))
                                machines.add("печь")
                                n -= took
                                break
                if n > 0:
                    raw[ref] += n
                return
            out_n = next((k for o, k in r["out"] if o == ref), r["out"][0][1]) or 1
            times = -(-n // out_n)
            kind = self.recipe_text(r).split("]")[0].strip("[")
            machines.add(kind)
            lines.append("%s%dx %s  <- %s x%d" % ("  " * depth, n, self.name(ref), kind, times))
            for inp, k in r["in"]:
                if self.NOT_INGREDIENT.search(inp):
                    machines.add("нужен штамп/шаблон " + self.name(inp))
                    continue
                expand(inp, k * times, depth + 1, seen | {ref})

        expand(root, max(1, int(count)), 0, frozenset())
        base = ", ".join("%dx %s" % (n, self.name(i)) for i, n in sorted(raw.items(), key=lambda x: -x[1]))
        text = "План: %dx %s\n%s\n\n" % (count, self.name(root), "\n".join(lines[:40]))
        if have is not None:
            got = ", ".join("%dx %s" % (n, self.name(i)) for i, n in used.items() if n > 0)
            text += "Беру из инвентаря: %s\nНЕ ХВАТАЕТ сырья: %s\n" % (got or "ничего", base or "ничего — всё есть, можно крафтить")
        else:
            text += "ИТОГО сырья: %s\n" % base
        return text + "Нужные станки и машины: %s" % ", ".join(sorted(machines))

    # How a player gets base resources: (tool, args, how many units one "count" gives)
    ORES = lambda *ids: ["minecraft:%s" % i for i in ids]
    GATHER = {
        "minecraft:iron_ingot": [("mine", {"blocks": ORES("iron_ore", "deepslate_iron_ore")}, 1), ("smelt", {"item": "minecraft:raw_iron"}, 1)],
        "minecraft:copper_ingot": [("mine", {"blocks": ORES("copper_ore", "deepslate_copper_ore")}, 3), ("smelt", {"item": "minecraft:raw_copper"}, 1)],
        "minecraft:gold_ingot": [("mine", {"blocks": ORES("gold_ore", "deepslate_gold_ore")}, 1), ("smelt", {"item": "minecraft:raw_gold"}, 1)],
        "minecraft:raw_iron": [("mine", {"blocks": ORES("iron_ore", "deepslate_iron_ore")}, 1)],
        "minecraft:raw_copper": [("mine", {"blocks": ORES("copper_ore", "deepslate_copper_ore")}, 3)],
        "minecraft:raw_gold": [("mine", {"blocks": ORES("gold_ore", "deepslate_gold_ore")}, 1)],
        "minecraft:lapis_lazuli": [("mine", {"blocks": ORES("lapis_ore", "deepslate_lapis_ore")}, 5)],
        "minecraft:redstone": [("mine", {"blocks": ORES("redstone_ore", "deepslate_redstone_ore")}, 4)],
        "minecraft:coal": [("mine", {"blocks": ORES("coal_ore", "deepslate_coal_ore")}, 1)],
        "minecraft:diamond": [("mine", {"blocks": ORES("diamond_ore", "deepslate_diamond_ore")}, 1)],
        "minecraft:emerald": [("mine", {"blocks": ORES("emerald_ore", "deepslate_emerald_ore")}, 1)],
        "minecraft:cobblestone": [("mine", {"blocks": ORES("stone")}, 1)],
        "minecraft:cobbled_deepslate": [("mine", {"blocks": ORES("deepslate")}, 1)],
        "minecraft:sand": [("mine", {"blocks": ORES("sand")}, 1)],
        "minecraft:gravel": [("mine", {"blocks": ORES("gravel")}, 1)],
        "minecraft:clay_ball": [("mine", {"blocks": ORES("clay")}, 4)],
        "minecraft:dirt": [("mine", {"blocks": ORES("dirt", "grass_block")}, 1)],
        "minecraft:obsidian": [("mine", {"blocks": ORES("obsidian")}, 1)],
        "minecraft:glass": [("mine", {"blocks": ORES("sand")}, 1), ("smelt", {"item": "minecraft:sand"}, 1)],
        "minecraft:gunpowder": [("attack", {"target": "creeper", "radius": 48}, 0)],
        "minecraft:string": [("attack", {"target": "spider", "radius": 48}, 0)],
        "minecraft:bone": [("attack", {"target": "skeleton", "radius": 48}, 0)],
        "minecraft:rotten_flesh": [("attack", {"target": "zombie", "radius": 48}, 0)],
        "minecraft:leather": [("attack", {"target": "cow", "radius": 48}, 0)],
        "minecraft:feather": [("attack", {"target": "chicken", "radius": 48}, 0)],
        "minecraft:ender_pearl": [("attack", {"target": "enderman", "radius": 48}, 0)],
        "minecraft:slime_ball": [("attack", {"target": "slime", "radius": 48}, 0)],
    }

    # what "any item of this tag" should mean for a player starting from scratch
    PREFERRED = {"#minecraft:planks": "minecraft:oak_planks", "#minecraft:logs": "minecraft:oak_log",
                 "#minecraft:stone_tool_materials": "minecraft:cobblestone", "#minecraft:stone_crafting_materials": "minecraft:cobblestone",
                 "#forge:cobblestone": "minecraft:cobblestone", "#forge:cobblestone/normal": "minecraft:cobblestone",
                 "#minecraft:coals": "minecraft:coal", "#forge:rods/wooden": "minecraft:stick", "#minecraft:wooden_slabs": "minecraft:oak_slab",
                 "#forge:ingots/iron": "minecraft:iron_ingot", "#forge:ingots/copper": "minecraft:copper_ingot",
                 "#forge:gems/lapis": "minecraft:lapis_lazuli", "#forge:gunpowder": "minecraft:gunpowder",
                 "#forge:dusts/redstone": "minecraft:redstone", "#forge:storage_blocks/iron": "minecraft:iron_block",
                 "#hbm_m:stamps/plate": "hbm_m:stamp_iron_plate", "#hbm_m:stamps/wire": "hbm_m:stamp_iron_wire",
                 "#hbm_m:stamps/circuit": "hbm_m:stamp_iron_circuit"}

    # Mod machines the bot can work (MachineTask in the mod): recipe type -> the blocks that make it, and how to load them.
    # A burner press: stamp (a tool, it comes back) in slot 1, material in slot 2, fuel in slot 0 (about ten coal a pressing).
    # The advanced assembly machine: recipe chosen from its list, ingredients go where it takes them, power from a battery.
    MACHINES = {
        "hbm_m:assembler": {"blocks": ["hbm_m:advanced_assembly_machine"], "select": True},
        "hbm_m:press": {"blocks": ["hbm_m:press", "hbm_m:machine_press"], "stamp_slot": 1, "material_slot": 2,
                        "fuel_slot": 0, "fuel_per_op": 10},
    }
    MACHINE_BLOCKS = sorted({b for m in MACHINES.values() for b in m["blocks"]})

    PICKAXES = ["minecraft:wooden_pickaxe", "minecraft:stone_pickaxe", "minecraft:iron_pickaxe",
                "minecraft:diamond_pickaxe", "minecraft:netherite_pickaxe"]
    # minimal pickaxe tier (index in PICKAXES) to get drops from a block
    MINE_TIER = {"stone": 0, "cobblestone": 0, "deepslate": 0, "coal_ore": 0, "copper_ore": 1, "iron_ore": 1,
                 "lapis_ore": 1, "gold_ore": 2, "redstone_ore": 2, "diamond_ore": 2, "emerald_ore": 2, "obsidian": 3}

    def mine_tier(self, blocks):
        tier = -1
        for b in blocks:
            name = b.split(":")[-1].replace("deepslate_", "") if b.split(":")[-1] != "deepslate" else "deepslate"
            tier = max(tier, self.MINE_TIER.get(name, -1))
        return tier

    def acquire(self, query, count, have, stations=(), creative=False, avoid=(), stored=None):
        """Concrete steps [(tool, args)] to end up with `count` of an item, starting from inventory `have`.
        stations: work blocks (furnace, crafting table, mod machines...) already standing nearby — no need to make new ones;
        a mod machine among them (MACHINES) makes its recipes with a "machine" step.
        creative: he plays in creative mode — raw materials, tools and what needs a machine he has not found come from the
        creative menu (creative_take), like a player there would do; what has a recipe by hand or in a found machine is made.
        avoid: recipe ids a machine refused before (learned): another recipe is taken if there is one.
        stored: {item id: [(pos, count)]} — what lies in the chests and machines he has looked into: taken from there
        first (a take_stored step), like a player uses what the base already has before making it anew.
        Returns (root_id, steps, unresolved) — unresolved lists what the bot cannot get by itself."""
        if self._index is None:
            self._build_index()
        by_out = self._index[0]
        root = self.find_id(query)
        if not root:
            return None, [], ["не знаю предмет «%s»" % query]
        avoid = set(avoid or ())
        if self.is_disabled(root):
            return root, [], [self.disabled_note(root)]
        stock = defaultdict(int, have or {})
        steps, unresolved = [], []
        smelt_total = [0]
        hand_made = {"minecraft:crafting_shaped", "minecraft:crafting_shapeless", "tacz:gun_smith_table_crafting"}
        # tools and stations are not used up: remember what we have or will have made
        owned = {i for i, n in (have or {}).items() if n > 0} | set(stations or ())
        pending = set()

        missing = []   # machine types he can work but has not seen yet

        def machine_here(rtype):
            m = self.MACHINES.get(rtype)
            return bool(m) and any(b in owned for b in m["blocks"])

        def ensure(item_id):
            """Make sure a tool/station exists before the next step (crafting it from scratch if needed)."""
            if item_id in owned or item_id in pending:
                return
            if creative:
                steps.append(("creative_take", {"item": item_id, "count": 1}))
                owned.add(item_id)
                return
            pending.add(item_id)   # only counts as owned once its own steps are planned
            need(item_id, 1, 1, frozenset())
            pending.discard(item_id)
            owned.add(item_id)

        def ensure_pickaxe(tier):
            if tier < 0 or any(p in owned for p in self.PICKAXES[tier:]):
                return
            ensure(self.PICKAXES[tier])

        stored_left = {i: [[tuple(p), c] for p, c in spots] for i, spots in (stored or {}).items()}

        def take_stored(ref, n):
            took = 0
            for i in (self.resolve_tag(ref) if ref.startswith("#") else [ref]):
                for spot in stored_left.get(i, []):
                    t = min(n - took, spot[1])
                    if t > 0:
                        steps.append(("take_stored", {"item": i, "count": t, "from": list(spot[0])}))
                        spot[1] -= t
                        took += t
                    if took >= n:
                        break
                if took >= n:
                    break
            return took

        def from_stock(ref, n):
            took = 0
            for i in (self.resolve_tag(ref) if ref.startswith("#") else [ref]):
                t = min(n - took, stock[i])
                if t > 0:
                    stock[i] -= t
                    took += t
                if took >= n:
                    break
            return took

        def concrete(ref):
            """Pick the simplest member of a tag: already carried > known preferred > gatherable > plain vanilla."""
            if not ref.startswith("#"):
                return ref
            items = self.resolve_tag(ref)
            if not items:
                return ref
            for i in items:
                if stock[i] > 0:
                    return i
            if self.PREFERRED.get(ref) in items:
                return self.PREFERRED[ref]
            items = sorted(items, key=lambda i: (i not in self.GATHER, not i.startswith("minecraft:oak_"),
                                                 not i.startswith("minecraft:"), len(i), i))
            return items[0]

        def add_smelt(item, n):
            """Smelt step with a furnace and enough fuel (1 coal per 8 items)."""
            ensure("minecraft:furnace")
            before = -(-smelt_total[0] // 8)
            smelt_total[0] += n
            fuel = -(-smelt_total[0] // 8) - before
            fuel -= from_stock("#minecraft:coals", fuel) if "#minecraft:coals" in self.tags else from_stock("minecraft:coal", fuel)
            if fuel > 0:
                ensure_pickaxe(0)
                steps.append(("mine", {"blocks": ["minecraft:coal_ore", "minecraft:deepslate_coal_ore"], "count": fuel + 1}))
                stock["minecraft:coal"] += 1   # the spare coal counts for the next smelt
            steps.append(("smelt", {"item": item, "count": n}))

        def gather(ref, n):
            if creative:
                steps.append(("creative_take", {"item": concrete(ref), "count": n}))
                return
            # smelt raw forms already carried (raw iron -> ingot)
            for i in by_out.get(ref, []):
                r = self.recipes[i]
                if r["type"] in ("minecraft:smelting", "minecraft:blasting") and r["in"]:
                    took = from_stock(r["in"][0][0], n)
                    if took:
                        add_smelt(concrete(r["in"][0][0]), took)
                        n -= took
                        break
            if n <= 0:
                return
            key = ref
            if ref.endswith("_log") or ref == "#minecraft:logs":
                steps.append(("mine", {"blocks": ["#minecraft:logs"], "count": n}))
                return
            how = self.GATHER.get(key)
            if not how:
                unresolved.append("%dx %s (не знаю, как добыть самому)" % (n, self.name(ref)))
                return
            for tool, args, per in how:
                a = dict(args)
                if tool == "smelt":
                    add_smelt(a["item"], n)
                    continue
                if tool == "mine":
                    a["count"] = -(-n // per) + 1
                    ensure_pickaxe(self.mine_tier(a["blocks"]))
                steps.append((tool, a))

        def machine_step(ref, n, r, out_n, times, depth, seen):
            """Load a machine standing nearby: its ingredients first, then one "machine" step for the whole batch."""
            m = self.MACHINES[r["type"]]
            inputs = []
            for inp, k in r["in"]:
                if self.NOT_INGREDIENT.search(inp):
                    if "stamp" in inp:   # a press stamp is a tool: one is enough and it comes back
                        st = concrete(inp)
                        # a stamp of this kind already lies in a machine (the press has its own): it uses that one
                        members = set(self.resolve_tag(inp)) if inp.startswith("#") else {inp}
                        own = [i for i in members if stored_left.get(i)]
                        if own:
                            st = own[0]
                        else:
                            ensure(st)
                        inputs.append({"item": st, "count": 1, "slot": m.get("stamp_slot", -1), "back": True,
                                       "alts": self.resolve_tag(inp) if inp.startswith("#") else []})
                    continue
                item = concrete(inp)
                need(item, k * times, depth + 1, seen | {ref})
                inputs.append({"item": item, "count": k * times, "slot": m.get("material_slot", -1), "back": True})
            if m.get("fuel_per_op"):
                fuel = m["fuel_per_op"] * (times + 1)
                need("minecraft:coal", fuel, depth + 1, seen | {ref})
                inputs.append({"item": "minecraft:coal", "count": min(64, fuel), "slot": m["fuel_slot"], "back": False})
            steps.append(("machine", {"item": ref, "count": n, "machines": m["blocks"],
                                      "recipe": r["id"] if m.get("select") else "", "recipe_id": r["id"], "inputs": inputs}))
            stock[ref] += out_n * times - n

        def need(ref, n, depth, seen):
            if depth > 0:
                n -= from_stock(ref, n)
                if n <= 0:
                    return
                if stored_left:
                    n -= take_stored(ref, n)
                    if n <= 0:
                        return
            ref = concrete(ref)
            if self.is_disabled(ref):
                unresolved.append(self.disabled_note(ref))
                return
            # raw resources are gathered, never crafted (gunpowder from creepers, not from a modded sulfur recipe)
            if self.BASE.search(ref) and (depth > 0 or ref in self.GATHER) or ref in seen or depth > 7:
                gather(ref, n)
                return
            recs = [self.recipes[i] for i in by_out.get(ref, [])]
            recs = [r for r in recs if not any(ref.split(":")[-1] in i[0] for i in r["in"])] or recs
            recs = [r for r in recs if not any(self.is_disabled(i) for i, _ in r["in"])] or recs
            recs = [r for r in recs if not r.get("pool")] or recs   # blueprint recipes: he has no blueprint folder
            recs = [r for r in recs if r["id"] not in avoid] or recs   # learned: this one did not work
            # by hand first, then the machines standing nearby, then the rest
            recs.sort(key=lambda r: (self.RECIPE_RANK.get(r["type"], 4 if machine_here(r["type"]) else
                                                          4.5 if r["type"] in self.MACHINES else 5), len(r["in"])))
            if not recs:
                gather(ref, n)
                return
            r = recs[0]
            out_n = next((k for o, k in r["out"] if o == ref), r["out"][0][1]) or 1
            times = -(-n // out_n)
            if r["type"] in ("minecraft:smelting", "minecraft:blasting"):
                if creative and depth > 0:
                    steps.append(("creative_take", {"item": ref, "count": n}))
                    return
                need(r["in"][0][0], n, depth + 1, seen | {ref})
                add_smelt(concrete(r["in"][0][0]), n)
                return
            if r["type"] not in hand_made:
                if r["type"] in self.MACHINES:
                    # a machine he knows how to work: use it; not seen yet — the plan starts by searching for it
                    # (even in creative: the commander wants it made in the machines, not taken from the menu)
                    if not machine_here(r["type"]) and r["type"] not in missing:
                        missing.append(r["type"])
                    machine_step(ref, n, r, out_n, times, depth, seen)
                    return
                if creative and depth > 0:
                    # a machine he cannot work anyway (an anvil...): in creative a player just takes the part
                    steps.append(("creative_take", {"item": ref, "count": n}))
                    return
                unresolved.append("%dx %s — делается в машине %s (у меня её нет)" % (n, self.name(ref), r["type"]))
                return
            for inp, k in r["in"]:
                if not self.NOT_INGREDIENT.search(inp):
                    need(inp, k * times, depth + 1, seen | {ref})
            if r["type"] == "minecraft:crafting_shaped" and ref != "minecraft:crafting_table" and len(r["in"]) > 0 \
                    and sum(k for _, k in r["in"]) > 4:
                ensure("minecraft:crafting_table")   # 3x3 recipes need a table (the craft step places it)
            steps.append(("craft", {"item": ref, "count": n}))
            stock[ref] += out_n * times - n   # leftovers from crafting in batches

        need(root, max(1, int(count)), 0, frozenset())
        # gathering earlier never hurts: fold repeated mining of the same blocks into the first trip
        merged, first = [], {}
        for tool, a in steps:
            key = (tool, tuple(a.get("blocks", ()))) if tool == "mine" else (tool, a["item"]) if tool == "creative_take" else None
            if key is not None and key in first:
                merged[first[key]][1]["count"] += a["count"]
                continue
            if key is not None:
                first[key] = len(merged)
            merged.append((tool, dict(a)))
        # first go and find the machines he has not seen: look into the buildings around, like a person would
        finds = [("explore", {"blocks": self.MACHINES[t]["blocks"], "radius": 200}) for t in missing]
        return root, finds + merged, unresolved

    # vanilla work blocks and the recipe types they run
    VANILLA_STATIONS = {"minecraft:furnace": ["minecraft:smelting"], "minecraft:blast_furnace": ["minecraft:blasting"],
                        "minecraft:smoker": ["minecraft:smoking"], "minecraft:campfire": ["minecraft:campfire_cooking"],
                        "minecraft:crafting_table": ["minecraft:crafting_shaped", "minecraft:crafting_shapeless"],
                        "minecraft:stonecutter": ["minecraft:stonecutting"], "minecraft:smithing_table": ["minecraft:smithing_transform"]}

    def block_recipe_types(self, block_id):
        """Recipe types a machine block runs: known machines, vanilla stations, and by name (thermal:machine_furnace ->
        thermal:furnace, immersiveengineering:crusher -> immersiveengineering:crusher)."""
        out = [t for t, m in self.MACHINES.items() if block_id in m["blocks"]] + self.VANILLA_STATIONS.get(block_id, [])
        if out:
            return out
        ns, _, path = block_id.partition(":")
        core = re.sub(r"^(machine|block|electric|advanced|basic)_|_(machine|block|controller|core|master)$", "", path)
        if len(core) < 4:
            return []
        types = {r["type"] for r in self.recipes}
        for t in types:
            tns, _, tpath = t.partition(":")
            if tns != ns:
                continue
            tcore = tpath.replace("_recipe", "").replace("recipe_", "")
            common = len(os.path.commonprefix([tcore, core]))   # alloy_smelter ~ alloy_smelting
            if tcore == core or (len(tcore) >= 5 and (tcore in core or core in tcore)) \
                    or (common >= 6 and common >= 0.7 * min(len(tcore), len(core))):
                out.append(t)
        if "furnace" in core and "blast" not in core and "minecraft:smelting" not in out:
            out.append("minecraft:smelting")   # modded furnaces smelt what a furnace smelts
        return sorted(out)

    def machine_role(self, block_id, limit=5):
        """What a machine does, from the pack's recipes: what comes out and what is put in (the most common ones)."""
        types = set(self.block_recipe_types(block_id))
        if not types:
            return ""
        ins, outs = defaultdict(int), defaultdict(int)
        for r in self.recipes:
            if r["type"] not in types:
                continue
            for i, _ in r["in"]:
                if not self.NOT_INGREDIENT.search(i):
                    ins[i] += 1
            for o, _ in r["out"]:
                outs[o] += 1
        top = lambda d: ", ".join(self.name(i).split(" [")[0] for i, _ in sorted(d.items(), key=lambda x: -x[1])[:limit])  # noqa: E731
        return "делает: %s; кладут: %s" % (top(outs) or "?", top(ins) or "?")

    def consumers(self, item_id):
        """Recipe types that take this item in (where it can be put to use)."""
        out = defaultdict(int)
        for r in self.recipes:
            for i, _ in r["in"]:
                if i == item_id or (i.startswith("#") and item_id in self.resolve_tag(i)):
                    out[r["type"]] += 1
                    break
        return [t for t, _ in sorted(out.items(), key=lambda x: -x[1])]

    def search(self, query, limit=5, min_score=1):
        if self._index is None:
            self._build_index()
        exact = [w for w in re.findall(r"[a-z0-9_]+:[a-z0-9_/]+", query.lower()) if w in self.entries]
        if exact:  # exact ids first: "hbm_m:plate_steel" must not drown in other plates
            cards = [self.card(self.entries[w]) for w in dict.fromkeys(exact)][:limit]
            return "\n\n".join(cards)
        q = tokens(query)
        if not q:
            return "Пустой запрос."
        scored = []
        for kind, key, id_toks, name_toks, desc_toks in self._index[2]:
            s = 0
            for t in q:
                if t in name_toks or t in id_toks:
                    s += 5
                elif len(t) >= 3 and any(n.startswith(t) or t.startswith(n) for n in name_toks | id_toks if len(n) >= 3):
                    s += 3
                elif t in desc_toks:
                    s += 1
            if s:
                if kind == "manual":
                    s -= 1
                if s >= min_score:
                    scored.append((s, kind, key))
        scored.sort(key=lambda x: -x[0])
        out = []
        for s, kind, key in scored[:limit]:
            if kind == "entry":
                out.append(self.card(self.entries[key]))
            elif kind == "multiblock":
                mb = self.multiblocks[key]
                mats = ", ".join("%dx %s" % (n, self.name(i)) for i, n in sorted(mb["materials"].items(), key=lambda x: -x[1]))
                title = self.entries.get("%s:%s" % (mb["mod"], key), {}).get("ru") or key
                out.append("Постройка «%s» (%s, мод %s, размер %s): нужно %s. Строится инструментом build_multiblock (name=%s), "
                           "собирается инженерным молотом." % (title, key, mb["mod"], "x".join(map(str, mb["size"])), mats, key))
            else:
                m = self.manual[key]
                out.append("Руководство %s «%s»: %s" % (m["mod"], m["title"], m["text"][:900]))
        return "\n\n".join(out) if out else "В справочнике ничего не нашёл по запросу: " + query

    STOP = set(tokens("альтрон алтрон ультрон altron ultron сделай сделать скрафти скрафть крафти принеси найди добудь накопай "
                      "мне нам меня иди давай пожалуйста и в на с по для из к у же ну как что где это там тут нас всё все его её "
                      "их надо нужно хочу можешь потом сначала а ещё еще штук штуки несколько много немного быстро теперь "
                      "the a an of to and make get bring craft find"))

    def context_for(self, text, per_part=2, max_chars=3000):
        """Encyclopedia cards relevant to what the player said ('' if nothing specific).
        A phrase like "ammo for the AKM and a coke oven" is split so each part gets its own cards."""
        parts = re.split(r"\s+(?:и|а|потом|затем|then|and)\s+|[,.;!?]", text)
        seen, out = set(), []
        for part in parts:
            q = [t for t in tokens(part) if t not in self.STOP and not t.isdigit()]
            if not q:
                continue
            res = self.search(" ".join(q), per_part, min_score=5)
            if res.startswith("В справочнике"):
                continue
            for card in res.split("\n\n"):
                if card not in seen:
                    seen.add(card)
                    out.append(card)
        return "\n\n".join(out)[:max_chars]

    MATERIAL_WORDS = {"желез": "minecraft:iron_ingot", "медь": "minecraft:copper_ingot", "меди": "minecraft:copper_ingot",
                      "угол": "minecraft:coal", "угл": "minecraft:coal", "алмаз": "minecraft:diamond", "золот": "minecraft:gold_ingot",
                      "дерев": "minecraft:oak_log", "бревн": "minecraft:oak_log", "доск": "minecraft:oak_planks",
                      "булыжн": "minecraft:cobblestone", "камн": "minecraft:cobblestone", "камен": "minecraft:cobblestone",
                      "редстоун": "minecraft:redstone", "лазурит": "minecraft:lapis_lazuli", "порох": "minecraft:gunpowder",
                      "стал": "hbm_m:steel_ingot"}
    MAKE_RE = re.compile(r"(сдела|скрафт|крафт|изготов|собер|добуд|накопа|принес|достан|получ|нужн|make|craft|get|bring)", re.I)

    def find_targets(self, text, limit=2):
        """Items the player asks to make/get: [(id, name)]. Only confident matches (whole name or 2+ words)."""
        if self._index is None:
            self._build_index()
        if not self.MAKE_RE.search(text):
            return []
        out = []
        # a part is an order if it has a "make" verb itself or continues one with "и" ("АКМ и 90 патронов")
        pieces = re.split(r"(\s+(?:и|а|потом|затем|then|and)\s+|[,.;!?—:])", text)
        active = False
        for i in range(0, len(pieces), 2):
            part = pieces[i]
            sep_before = pieces[i - 1] if i > 0 else ""
            has_verb = bool(self.MAKE_RE.search(part))
            active = has_verb or (active and sep_before.strip() in ("и", "а", "and"))
            if not active:
                continue
            q = [t for t in tokens(part) if t not in self.STOP and not t.isdigit() and not self.MAKE_RE.match(t)]
            if not q:
                continue
            best, best_s = None, 0
            for t in q:   # plain words for raw materials: "железо", "медь", "уголь"...
                for prefix, item in self.MATERIAL_WORDS.items():
                    if t.startswith(prefix) and 10 > best_s:
                        best, best_s = item, 10
            qs = set(q)
            for kind, key, id_toks, name_toks, _ in self._index[2]:
                if kind != "entry":
                    continue
                hits = sum(1 for t in q if t in name_toks or t in id_toks)
                if not hits:
                    continue
                e = self.entries[key]
                # every word of the item's Russian or English name was said
                whole = any(n and set(tokens(n)) <= qs for n in (e["ru"], e["en"]))
                s = hits * 5 + (8 if whole else 0) - 0.01 * len(key)
                if s > best_s:
                    best, best_s = key, s
            if best and self.is_disabled(best):
                continue   # switched off in this pack: no "go and make it" hint (its card says so)
            if best and best_s >= 10 and best not in [b for b, _ in out]:
                e = self.entries[best]
                out.append((best, e["ru"] or e["en"]))
        return out[:limit]

    def mods_line(self):
        return ", ".join(sorted({v["name"] for v in self.mods.values()}))
