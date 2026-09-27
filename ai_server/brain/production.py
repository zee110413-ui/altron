"""Working out how a base produces things, from what Altron has seen: which block passes items to which (hoppers,
conveyors, droppers, pipes, chests next to machines), the production lines this makes, what each line makes, where to
put its raw materials and where its products end up, and which building each line is in.

    lines = analyze(blocks, contents, knowledge)   # blocks: the bot's production_map, contents: {pos: [(n, id)]}
    html = map_html(lines, blocks, title)          # a top-down map with every line in its own colour
"""
import html as _html
import re
from collections import defaultdict

DIRS = {"north": (0, 0, -1), "south": (0, 0, 1), "east": (1, 0, 0), "west": (-1, 0, 0), "up": (0, 1, 0), "down": (0, -1, 0)}
POWER_RE = re.compile(r"connector|relay|capacitor|transformer|generator|cable|wire|battery|accumulator|solar|dynamo|"
                      r"energy_cell|power")
STORE_RE = re.compile(r"chest|barrel|crate|drawer|shulker|cabinet|storage|safe|locker")
TANK_RE = re.compile(r"fluid_cell|tank|drum|canister")
PIPE_RE = re.compile(r"conduit|pipe|duct|tube|chute")
PART_RE = re.compile(r"_part$|dummy|multiblock_part")
ITEM_RE = re.compile(r"(\d+)x [^()]*\(([a-z0-9_.-]+:[a-z0-9_/.-]+)\)")


def add(p, d):
    return (p[0] + d[0], p[1] + d[1], p[2] + d[2])


def sub(p, d):
    return (p[0] - d[0], p[1] - d[1], p[2] - d[2])


def kind_of(b):
    bid = b["id"]
    if "hopper" in bid:
        return "hopper"
    if "conveyor" in bid or "belt" in bid:
        for t in ("vertical", "dropper", "extract", "splitter"):
            if t in bid:
                return "conveyor_" + t
        return "conveyor"
    if PART_RE.search(bid):
        return "part"
    if POWER_RE.search(bid):
        return "power"
    if PIPE_RE.search(bid):
        return "pipe"
    if STORE_RE.search(bid):
        return "store"
    if TANK_RE.search(bid):
        return "tank"
    return "machine"


class Node:
    def __init__(self, b):
        self.id = b["id"]
        self.name = b.get("name", self.id)
        self.pos = tuple(int(v) for v in b["pos"])
        self.kind = kind_of(b)
        facing = b.get("be_facing") or (b.get("props") or {}).get("facing")
        self.facing = DIRS.get(str(facing).lower()) if facing else None
        self.contents = []     # [(count, item id)] as last seen inside
        self.zone = None
        self.conduit = b.get("conduit") or {}      # EnderIO: {"item": {"north": "extract", ...}, "fluid": ..., "energy": ...}
        self.sides = b.get("sides") or {}          # Thermal: {"down": "output", ...}
        self.auto_out = b.get("auto_out")
        self.auto_in = b.get("auto_in")
        self.fluids = [(f[0], int(f[1])) for f in (b.get("fluids") or [])]   # [(name, mB)]
        if self.kind == "pipe" and self.conduit and set(self.conduit) <= {"energy", "redstone"}:
            self.kind = "power"   # a pipe that only carries power or signal

    def where(self):
        return "%d %d %d" % self.pos


def _union_find(keys):
    parent = {k: k for k in keys}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    return find, union


def build_graph(blocks):
    """Nodes by position and the item flows between them: directed edges (hoppers, conveyors) and plain links
    (pipes, chests touching machines). Shell parts of big machines stand for their machine."""
    nodes = {}
    for b in blocks:
        n = Node(b)
        nodes.setdefault(n.pos, n)
    # a part of a big machine's shell is the machine itself (a conveyor may feed any side of it)
    cores = [n for n in nodes.values() if n.kind == "machine"]
    at = dict(nodes)
    for n in list(nodes.values()):
        if n.kind != "part":
            continue
        ns = n.id.split(":")[0]
        near = [c for c in cores if c.id.split(":")[0] == ns and max(abs(a - b) for a, b in zip(c.pos, n.pos)) <= 3]
        if near:
            at[n.pos] = min(near, key=lambda c: sum((a - b) ** 2 for a, b in zip(c.pos, n.pos)))
    edges, links = set(), set()

    def get(p):
        return at.get(p)

    for n in nodes.values():
        if n.kind in ("power", "part"):
            continue
        f = n.facing
        if n.kind == "hopper":
            src = get(add(n.pos, DIRS["up"]))
            if src and src.kind not in ("power",):
                edges.add((src.pos, n.pos))
            dst = get(add(n.pos, f or DIRS["down"]))
            if dst:
                edges.add((n.pos, dst.pos))
        elif n.kind.startswith("conveyor") and f:
            if n.kind == "conveyor_vertical":
                # up the shaft; at the top it hands over forward
                nxts = [get(add(n.pos, DIRS["up"])) or get(add(add(n.pos, DIRS["up"]), f)), get(add(n.pos, f))]
                below = get(add(n.pos, DIRS["down"]))
                if below and not below.kind.startswith("conveyor"):
                    edges.add((below.pos, n.pos))
            elif n.kind == "conveyor_dropper":
                # drops into what is under it, and passes on along the row what does not fit there
                nxts = [get(add(n.pos, DIRS["down"])), get(add(n.pos, f))]
            else:
                nxts = [get(add(n.pos, f)) or get(add(add(n.pos, f), DIRS["down"]))]
            for nxt in nxts:
                if nxt and nxt.kind not in ("power",) and nxt is not n:
                    edges.add((n.pos, nxt.pos))
            behind = get(sub(n.pos, f))
            if behind and behind.kind in ("machine", "store", "tank") and \
                    (n.kind == "conveyor_extract" or behind.kind == "machine"):
                edges.add((behind.pos, n.pos))   # pulled out of it / put out onto the belt by the machine
        if n.kind == "machine":
            # a hopper or a belt right under a machine (1-3 blocks, what is between he may not have made out):
            # that is where its products fall out
            for dy in (1, 2, 3):
                m = get((n.pos[0], n.pos[1] - dy, n.pos[2]))
                if m is not None:
                    if m.kind == "hopper" or m.kind.startswith("conveyor"):
                        edges.add((n.pos, m.pos))
                    break
        if n.kind in ("pipe", "store", "tank", "machine"):
            for d in DIRS.values():
                m = get(add(n.pos, d))
                if not m or m is n or m.kind in ("power", "part"):
                    continue
                if n.kind == "pipe" and m.kind in ("pipe", "machine", "store", "tank", "hopper"):
                    links.add(tuple(sorted((n.pos, m.pos))))
                elif n.kind in ("store", "tank") and m.kind == "machine":
                    links.add(tuple(sorted((n.pos, m.pos))))
    # EnderIO pipes: each channel (items, liquids) is its own network; what its ends pull out of goes to what its ends
    # push into. Power and signal channels are left out (they are not what a line makes)
    for channel in ("item", "fluid"):
        pipes = [n for n in nodes.values() if n.kind == "pipe" and channel in n.conduit]
        if not pipes:
            continue
        find, union = _union_find([n.pos for n in pipes])
        on = {n.pos for n in pipes}
        for n in pipes:
            for d in DIRS.values():
                q = add(n.pos, d)
                if q in on:
                    union(n.pos, q)
        nets = defaultdict(lambda: ([], []))
        for n in pipes:
            src, dst = nets[find(n.pos)]
            for dname, mode in n.conduit[channel].items():
                m = get(add(n.pos, DIRS.get(dname, (0, 0, 0))))
                if not m or m.kind in ("pipe", "power"):
                    continue
                if mode in ("extract", "both"):
                    src.append(m.pos)
                if mode in ("insert", "both"):
                    dst.append(m.pos)
        for src, dst in nets.values():
            for a in set(src):
                for b in set(dst):
                    if a != b:
                        edges.add((a, b))
    # Thermal machines: what their output sides touch gets their products (a hopper pulls them anyway, a chest
    # only if the machine pushes by itself); input sides take from a chest when it pulls by itself
    for n in nodes.values():
        if n.kind != "machine" or not n.sides:
            continue
        for dname, mode in n.sides.items():
            m = get(add(n.pos, DIRS.get(dname, (0, 0, 0))))
            if not m or m is n or m.kind in ("power", "part"):
                continue
            if mode in ("output", "both") and (n.auto_out or m.kind in ("hopper", "pipe") or m.kind.startswith("conveyor")):
                edges.add((n.pos, m.pos))
            if mode in ("input", "both") and n.auto_in and m.kind in ("store", "tank", "machine"):
                edges.add((m.pos, n.pos))
    return nodes, at, edges, links


def _products(k, machine, zone_items):
    """What a machine makes, by what is inside it and the pack's recipes: items with a recipe of the machine's kind
    made from things that are here, and outputs of its recipes whose ingredients are here."""
    if k is None:
        return [], []
    by_out = k._index[0] if k._index else {}
    types = set(k.block_recipe_types(machine.id))
    if "crafter" in machine.id or "workbench" in machine.id:
        types |= {"minecraft:crafting_shaped", "minecraft:crafting_shapeless"}
    inside = {i for _, i in machine.contents}
    here = inside | zone_items
    made, used = defaultdict(int), set()
    needs = defaultdict(set)   # product -> what it is made of (without stamps, templates and other tools)

    def ingredients(r):
        out = set()
        for ref, _ in r["in"]:
            out |= set(k.resolve_tag(ref)) if ref.startswith("#") else {ref}
        return out

    def real_inputs(r):
        # one item per ingredient slot (what is already here, else the simplest), tools like a press stamp left out
        out = []
        for ref, _ in r["in"]:
            if k.NOT_INGREDIENT.search(ref):
                continue
            opts = sorted(k.resolve_tag(ref)) if ref.startswith("#") else [ref]
            here_opts = [o for o in opts if o in here]
            out.append(here_opts[0] if here_opts else (opts[0] if opts else ref))
        return out

    for item in inside:
        for i in by_out.get(item, []):
            r = k.recipes[i]
            if r["type"] in types and ingredients(r) & here and item not in ingredients(r):
                made[item] += 3
                used |= ingredients(r) & here
                needs[item] |= set(real_inputs(r))
    if types and not ({"minecraft:crafting_shaped", "minecraft:crafting_shapeless"} & types):
        # machines with their own recipes (a centrifuge, a furnace): what they would make of what is inside them.
        # Not for crafters: iron ingots would "make" buckets, cauldrons and everything else of iron
        for r in k.recipes:
            if r["type"] not in types or not r["in"]:
                continue
            ing = ingredients(r)
            if inside and ing & inside and all((set(k.resolve_tag(ref)) if ref.startswith("#") else {ref}) & here
                                               for ref, _ in r["in"] if not k.NOT_INGREDIENT.search(ref)):
                for o, _ in r["out"]:
                    if o not in ing:
                        made[o] += 1
                        needs[o] |= set(real_inputs(r))
                used |= ing & here
    # what goes into another of its products is its raw material, not its product (ingots -> iron blocks)
    raw = set().union(*needs.values()) if needs else set()
    ranked = [i for i, _ in sorted(made.items(), key=lambda x: -x[1])]
    top = [i for i in ranked if i not in raw][:4] or ranked[:4]
    wanted = sorted(set().union(*(needs[t] for t in top)) - set(top)) if top else sorted(used)
    return top, wanted


def _buildings(nodes, gap=7):
    """Groups of blocks standing together: a gap of more than `gap` blocks (in plan) separates buildings."""
    pts = [n for n in nodes if n.kind != "power"]
    find, union = _union_find([n.pos for n in pts])
    cells = defaultdict(list)
    for n in pts:
        cells[(n.pos[0] // gap, n.pos[2] // gap)].append(n)
    for (cx, cz), group in cells.items():
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for m in cells.get((cx + dx, cz + dz), []):
                    for n in group:
                        if abs(n.pos[0] - m.pos[0]) <= gap and abs(n.pos[2] - m.pos[2]) <= gap:
                            union(n.pos, m.pos)
    groups = defaultdict(list)
    for n in pts:
        groups[find(n.pos)].append(n)
    return sorted(groups.values(), key=len, reverse=True)


NAME_RE = re.compile(r"\d+x ([^()]+?) \(([a-z0-9_.-]+:[a-z0-9_/.-]+)\)")


def analyze(blocks, contents, k=None, names=None):
    """The production lines of a base: [{title, building, machines, inputs, outputs, flow, todo, nodes}].
    names: {item id: name as seen in the game} for items the encyclopedia has no name for."""
    names = names or {}
    nodes, at, edges, links = build_graph(blocks)
    for pos, items in contents.items():
        if pos in nodes:
            nodes[pos].contents = items
    live = [n for n in nodes.values() if n.kind not in ("power", "part", "pipe")]
    # pipes with known channels made direct edges between the blocks they join (build_graph): those count
    find, union = _union_find([n.pos for n in live])
    # a line is what items pass through: hoppers, belts, chests at machines. Pipes (EnderIO conduits carry anything:
    # oil to one machine, power to another) do not make one line of two — they are only noted as a link
    for a, b in list(edges) + list(links):
        if a in nodes and b in nodes and nodes[a].kind not in ("power", "part", "pipe") \
                and nodes[b].kind not in ("power", "part", "pipe"):
            union(a, b)
    # a row of the same machines side by side is one battery (five centrifuges fed by one belt, a ring of stills)
    machines_all = [n for n in live if n.kind == "machine"]
    for i, a in enumerate(machines_all):
        for b in machines_all[i + 1:]:
            if a.id == b.id and max(abs(p - q) for p, q in zip(a.pos, b.pos)) <= 3:
                union(a.pos, b.pos)
    comps = defaultdict(list)
    for n in live:
        comps[find(n.pos)].append(n)
    # he looked into one machine of a battery: the others of the same kind there work on the same things
    for comp in comps.values():
        seen_inside = {}
        for n in comp:
            if n.kind == "machine" and n.contents:
                seen_inside.setdefault(n.id, n.contents)
        for n in comp:
            if n.kind == "machine" and not n.contents and n.id in seen_inside:
                n.contents = seen_inside[n.id]
                n.guessed = True
    out_e, in_e = defaultdict(set), defaultdict(set)
    for a, b in edges:
        out_e[a].add(b)
        in_e[b].add(a)
    linked = defaultdict(set)
    for a, b in links:
        linked[a].add(b)
        linked[b].add(a)

    buildings = _buildings(list(nodes.values()))
    building_of = {}
    for i, grp in enumerate(buildings, 1):
        for n in grp:
            building_of[n.pos] = i

    def name(i):
        n = k.name(i).split(" [")[0] if k else i
        return names.get(i, n) if (":" in n or n == i) else n
    lines = []
    for comp in comps.values():
        machines = [n for n in comp if n.kind == "machine"]
        stores = [n for n in comp if n.kind in ("store", "tank")]
        if not machines and len(stores) < 2:
            continue
        zone_items = {i for n in stores for _, i in n.contents}
        made, used = [], set()
        per_machine = []
        roles = {}
        for m in machines:
            top, ing = _products(k, m, zone_items)
            per_machine.append((m, top, ing))
            made += [t for t in top if t not in made]
            used |= set(ing)
            if k is not None and m.id not in roles:
                roles[m.id] = k.machine_role(m.id, 4)   # "делает: ...; кладут: ..." from the pack's recipes
        # where the raw materials go in: stores that give to the line (edges out, or touching a machine they feed),
        # and where the products end up: stores that only take
        sources, sinks = [], []
        for s in stores:
            s_items = {i for _, i in s.contents}
            gives = bool(out_e[s.pos]) or (linked[s.pos] and s_items & used)
            takes = bool(in_e[s.pos]) or (linked[s.pos] and s_items & set(made))
            if takes and not gives:
                sinks.append(s)
            elif gives:
                sources.append(s)
        belts = sum(1 for n in comp if n.kind.startswith("conveyor"))
        hoppers = sum(1 for n in comp if n.kind == "hopper")
        pipes = sum(1 for n in comp if n.kind == "pipe")
        title = ", ".join(name(i) for i in made[:3]) or ", ".join(sorted({m.name for m in machines}))[:60] or "склад"
        flow = []
        groups = defaultdict(list)
        for m, top, ing in per_machine:
            groups[(m.id, tuple(top), tuple(ing))].append((m, top, ing))
        for group in groups.values():
            m, top, ing = group[0]
            if len(group) > 1:
                ps = sorted(g[0].pos for g in group)
                s = "%d× %s (%s … %s)" % (len(group), m.name, "%d %d %d" % ps[0], "%d %d %d" % ps[-1])
            else:
                s = "%s (%s)" % (m.name, m.where())
            if m.fluids:
                s += " (в баке: %s)" % ", ".join("%s %d мВ" % f for f in m.fluids[:2])
            if not top and not m.contents and not m.fluids and roles.get(m.id):
                s += " — внутрь не заглянул; по справочнику " + roles[m.id].split(";")[0]
            if top:
                s += " делает %s" % ", ".join(name(i) for i in top[:3])
            if ing:
                s += " из %s" % ", ".join(name(i) for i in ing[:4])
            # follow the belts from the machine to where the things end up
            seen, frontier, ends, steps = {m.pos}, [m.pos], [], 0
            while frontier and steps < 200:
                nxt = []
                for p in frontier:
                    for q in out_e[p]:
                        if q in seen:
                            continue
                        seen.add(q)
                        steps += 1
                        if nodes[q].kind in ("store", "tank", "machine") and q != m.pos:
                            ends.append(nodes[q])
                        else:
                            nxt.append(q)
                frontier = nxt
            if ends:
                s += " → по конвейерам/воронкам в " + ", ".join("%s (%s)" % (e.name, e.where()) for e in ends[:3])
            flow.append(s)
        todo = []
        need = [i for i in used if i not in made]
        for s in sources:
            has = {i for _, i in s.contents}
            what = [name(i) for i in need if i in has] or [name(i) for i in list(has)[:3]]
            if not s.contents:
                todo.append("сундук %s пуст — класть сюда: %s" % (s.where(), ", ".join(name(i) for i in need[:4])
                                                                  or "сырьё этой линии (какое — узнаю, заглянув в машины)"))
            else:
                todo.append("вход: %s (%s) — сюда класть %s" % (s.name, s.where(), ", ".join(what[:4])))
        for s in sinks:
            got = ", ".join(name(i) for _, i in s.contents[:3]) or "пока пусто"
            todo.append("выход: %s (%s) — сюда приходит готовое (%s)" % (s.name, s.where(), got))
        empty_machines = [m for m in machines if not m.contents]
        if machines and len(empty_machines) == len(machines):
            todo.append("в машины не заглянул (или они пустые) — что им нужно, пока не знаю")
        bnums = sorted({building_of.get(n.pos) for n in comp if building_of.get(n.pos)})
        tanks = ["%s (%s): %s" % (t.name, t.where(), ", ".join("%s %d мВ" % f for f in t.fluids)) for t in stores if t.fluids]
        if tanks:
            todo.append("жидкости: " + "; ".join(tanks[:3]))
        lines.append({"sources": [{"pos": s.pos, "name": s.name} for s in sources],
                      "sinks": [{"pos": s.pos, "name": s.name} for s in sinks],
                      "machine_list": [{"pos": m.pos, "id": m.id, "name": m.name} for m in machines],
                      "title": title, "building": bnums[0] if bnums else 0, "machines": [m.name for m in machines],
                      "made": made, "needs": need, "flow": flow, "todo": todo, "belts": belts, "hoppers": hoppers,
                      "pipes": pipes, "nodes": [n.pos for n in comp],
                      "center": tuple(sum(n.pos[i] for n in comp) // len(comp) for i in range(3))})
    lines.sort(key=lambda z: (z["building"], -len(z["machines"]), -len(z["nodes"])))
    for i, z in enumerate(lines, 1):
        z["n"] = i
        for p in z["nodes"]:
            nodes[p].zone = i
    power = defaultdict(int)
    for n in nodes.values():
        if n.kind == "power":
            power[building_of.get(n.pos) or _nearest_building(n.pos, buildings)] += 1
    info = [{"n": i, "center": tuple(sum(n.pos[j] for n in grp) // len(grp) for j in range(3)), "size": len(grp),
             "power": power.get(i, 0)} for i, grp in enumerate(buildings, 1)]
    return {"lines": lines, "buildings": info, "nodes": nodes}


def _nearest_building(pos, buildings):
    best, bd = 0, 1e9
    for i, grp in enumerate(buildings, 1):
        for n in grp[::5]:
            d = sum((a - b) ** 2 for a, b in zip(n.pos, pos))
            if d < bd:
                best, bd = i, d
    return best


def describe(result, limit=12):
    """Lines as sentences, one per line (for memory and for speaking)."""
    out = []
    for z in result["lines"][:limit]:
        s = "Здание %d, линия %d «%s»: %s" % (z["building"], z["n"], z["title"], "; ".join(z["flow"])[:400])
        if z["todo"]:
            s += ". Что делать: " + "; ".join(z["todo"])[:300]
        out.append(s)
    return out


PALETTE = ["#e8590c", "#1c7ed6", "#2b8a3e", "#ae3ec9", "#f08c00", "#0b7285", "#d6336c", "#5c940d", "#7048e8", "#c92a2a",
           "#087f5b", "#4263eb"]
KIND_LETTER = {"machine": "М", "store": "С", "tank": "Б", "hopper": "В"}


def _near(p, c, r):
    return abs(p[0] - c[0]) <= r and abs(p[2] - c[2]) <= r


def _plan_svg(result, b, zones):
    """Top view of one building: of the blocks in one column the most telling one is drawn, in its line's colour."""
    esc = _html.escape
    nodes = result["nodes"]
    zone_ids = {z["n"] for z in zones}
    members = [n for n in nodes.values() if (n.zone in zone_ids) or (n.kind == "power" and _near(n.pos, b["center"], 30))]
    if not members:
        return ""
    xs, zs = [n.pos[0] for n in members], [n.pos[2] for n in members]
    x0, x1, z0, z1 = min(xs) - 1, max(xs) + 1, min(zs) - 1, max(zs) + 1
    cell = 14
    w, h = (x1 - x0 + 1) * cell, (z1 - z0 + 1) * cell
    rank = {"machine": 6, "store": 5, "tank": 4, "hopper": 3, "conveyor": 2, "pipe": 1, "power": 0}
    top = {}
    for n in members:
        key = (n.pos[0], n.pos[2])
        r = rank.get(n.kind.split("_")[0], 1) + (10 if n.zone else 0)
        if key not in top or r > top[key][0]:
            top[key] = (r, n)
    out = ['<svg viewBox="0 0 %d %d" width="%d" role="img" aria-label="План здания %d сверху">' % (w, h, w, b["n"]),
           '<rect x="0" y="0" width="%d" height="%d" class="floor"/>' % (w, h)]
    for x in range(x0, x1 + 1, 5):
        out.append('<line x1="%d" y1="0" x2="%d" y2="%d" class="grid"/>' % ((x - x0) * cell, (x - x0) * cell, h))
    for z in range(z0, z1 + 1, 5):
        out.append('<line x1="0" y1="%d" x2="%d" y2="%d" class="grid"/>' % ((z - z0) * cell, w, (z - z0) * cell))
    for (x, z), (_, n) in sorted(top.items(), key=lambda kv: kv[1][0]):
        cx, cy = (x - x0) * cell, (z - z0) * cell
        color = PALETTE[(n.zone - 1) % len(PALETTE)] if n.zone else "var(--power)"
        tip = esc("%s — %s%s" % (n.name, n.where(), (", линия %d" % n.zone) if n.zone else ""))
        k = n.kind
        if k.startswith("conveyor") and n.facing and n.facing[1] == 0:
            dx, dz = n.facing[0], n.facing[2]
            mx, my = cx + cell / 2, cy + cell / 2
            pts = [(mx + dx * cell * .42, my + dz * cell * .42),
                   (mx - dz * cell * .34 - dx * cell * .3, my + dx * cell * .34 - dz * cell * .3),
                   (mx + dz * cell * .34 - dx * cell * .3, my - dx * cell * .34 - dz * cell * .3)]
            out.append('<polygon points="%s" fill="%s"><title>%s</title></polygon>' % (
                " ".join("%.1f,%.1f" % p for p in pts), color, tip))
        elif k.startswith("conveyor"):
            out.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="none" stroke="%s" stroke-width="2"><title>%s</title></circle>' % (
                cx + cell / 2, cy + cell / 2, cell * .3, color, tip))
        elif k in ("pipe", "power"):
            out.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="1.5" fill="%s" opacity="%s"><title>%s</title></rect>' % (
                cx + cell * .32, cy + cell * .32, cell * .36, cell * .36, color, ".5" if k == "pipe" else ".9", tip))
        else:
            out.append('<g><rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="2" fill="%s"/>'
                       '<text x="%.1f" y="%.1f" class="lbl">%s</text><title>%s</title></g>' % (
                           cx + 1, cy + 1, cell - 2, cell - 2, color, cx + cell / 2, cy + cell * .7,
                           KIND_LETTER.get(k, "?"), tip))
    out.append("</svg>")
    return "".join(out)


def map_html(result, title="Производство", photos=None, subtitle=""):
    """The page: per building a top view where every production line has its own colour (belts as arrows pointing
    where they carry), a picture, and a card per line: what it makes, where its raw materials go in and where its
    products come out, what to do. photos: {"line03": [(src, caption)], "building1": (src, caption)}."""
    esc = _html.escape
    photos = photos or {}
    sections = []
    total = len(result["lines"])
    nb = len({z["building"] for z in result["lines"]})
    for b in result["buildings"]:
        zones = [z for z in result["lines"] if z["building"] == b["n"]]
        if not zones:
            continue
        cards = []
        for z in zones:
            color = PALETTE[(z["n"] - 1) % len(PALETTE)]
            shots = "".join('<figure class="shot"><img src="%s" alt="%s" loading="lazy"><figcaption>%s</figcaption></figure>' % (
                esc(src), esc(cap), esc(cap)) for src, cap in photos.get("line%02d" % z["n"], []))
            stats = " · ".join(s for s in ("%d машин" % len(z["machines"]) if z["machines"] else "",
                                           "%d конвейеров" % z["belts"] if z["belts"] else "",
                                           "%d воронок" % z["hoppers"] if z["hoppers"] else "") if s)
            cards.append(
                '<article class="line" style="--c:%s" id="line%d"><header><span class="swatch"></span>'
                '<span class="num">Линия %d</span><h3>%s</h3></header><p class="stats">%s</p>'
                '<h4>Что куда идёт</h4><ul>%s</ul>%s%s</article>' % (
                    color, z["n"], z["n"], esc(z["title"]), esc(stats),
                    "".join("<li>%s</li>" % esc(f) for f in z["flow"]),
                    ('<h4>Что делать</h4><ul class="todo">%s</ul>' % "".join("<li>%s</li>" % esc(t) for t in z["todo"]))
                    if z["todo"] else "",
                    '<div class="shots">%s</div>' % shots if shots else ""))
        over = photos.get("building%d" % b["n"])
        overview = ('<figure class="shot wide"><img src="%s" alt="%s"><figcaption>%s</figcaption></figure>' % (
            esc(over[0]), esc(over[1]), esc(over[1]))) if over else ""
        sections.append(
            '<section class="building" id="building%d"><div class="bhead"><h2>Здание %d</h2>'
            '<p class="coords">центр %d %d %d · линий: %d · блоков питания: %d</p></div>'
            '<div class="views"><figure class="plan"><div class="scroll">%s</div><figcaption>Вид сверху, клетка — один блок, '
            'линии сетки через 5 блоков. Наведи на значок, чтобы увидеть блок и координаты.</figcaption></figure>%s</div>'
            '<div class="lines">%s</div></section>' % (
                b["n"], b["n"], b["center"][0], b["center"][1], b["center"][2], len(zones), b["power"],
                _plan_svg(result, b, zones), overview, "".join(cards)))
    body = "".join(sections) or "<p>Производство ещё не изучено.</p>"
    return (PAGE.replace("{title}", esc(title)).replace("{subtitle}", esc(subtitle))
            .replace("{summary}", "%d линий в %d зданиях" % (total, nb)).replace("{body}", body))


PAGE = """<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Oswald:wght@500;600&family=IBM+Plex+Sans:wght@400;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root{--bg:#eceeec;--fg:#1c2226;--muted:#5f6b72;--card:#fbfcfb;--line:#d3d9da;--floor:#dfe4e3;--gridc:#cfd6d6;--power:#9aa6ab;--accent:#d9480f;
--display:"Oswald","Arial Narrow",sans-serif;--body:"IBM Plex Sans","Segoe UI",system-ui,sans-serif;--mono:"IBM Plex Mono",Consolas,monospace}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#14181b;--fg:#e4e9eb;--muted:#8d9aa1;--card:#1b2226;--line:#2c363c;--floor:#1a2125;--gridc:#252e33;--power:#4b575d;--accent:#ff7a3d}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#14181b;--fg:#e4e9eb;--muted:#8d9aa1;--card:#1b2226;--line:#2c363c;--floor:#1a2125;--gridc:#252e33;--power:#4b575d;--accent:#ff7a3d}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--fg);font:15px/1.55 var(--body);margin:0}
.wrap{max-width:1180px;margin:0 auto;padding-inline:16px;padding-block:28px 56px;display:grid;gap:40px}
.eyebrow{font:500 12px/1 var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--accent);margin:0 0 10px}
h1{font:600 clamp(30px,5vw,46px)/1.05 var(--display);letter-spacing:.01em;margin:0;text-wrap:balance}
.lede{max-width:65ch;color:var(--muted);margin:12px 0 0}
.legend{display:flex;flex-wrap:wrap;gap:8px 18px;margin-top:16px;font:13px var(--mono);color:var(--muted)}
.legend b{display:inline-grid;place-items:center;width:18px;height:18px;border-radius:3px;background:var(--power);color:#fff;font-weight:500;margin-right:6px}
.building{display:grid;gap:18px}
.bhead{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 16px;border-bottom:2px solid var(--fg);padding-bottom:8px}
h2{font:600 28px/1.1 var(--display);margin:0;text-transform:uppercase;letter-spacing:.02em}
.coords{font:13px var(--mono);color:var(--muted);margin:0;font-variant-numeric:tabular-nums}
.views{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(0,1fr);gap:16px;align-items:start}
@media (max-width:820px){.views{grid-template-columns:1fr}}
figure{margin:0}
.plan{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:10px}
.scroll{overflow-x:auto}
.plan svg{display:block;max-width:none;height:auto}
.floor{fill:var(--floor)}.grid{stroke:var(--gridc);stroke-width:1}
.lbl{font:600 9px var(--body);fill:#fff;text-anchor:middle}
figcaption{font-size:12.5px;color:var(--muted);margin-top:6px}
.shot img{display:block;width:100%;height:auto;border-radius:4px;border:1px solid var(--line)}
.lines{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,340px),1fr));gap:14px}
.line{background:var(--card);border:1px solid var(--line);border-top:4px solid var(--c);border-radius:6px;padding:14px 16px;display:grid;gap:8px;align-content:start}
.line header{display:grid;grid-template-columns:auto 1fr;align-items:center;column-gap:8px}
.swatch{width:12px;height:12px;border-radius:2px;background:var(--c);grid-row:span 2}
.num{font:500 11px var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
h3{font:600 19px/1.2 var(--display);margin:0;text-wrap:balance}
h4{font:500 11px var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin:4px 0 0}
.stats{margin:0;font:13px var(--mono);color:var(--muted)}
.line ul{margin:0;padding-left:18px;display:grid;gap:4px}
.todo li::marker{color:var(--c)}
.shots{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px;margin-top:4px}
</style>
<main class="wrap">
<header>
<p class="eyebrow">Карта производства · {summary}</p>
<h1>{title}</h1>
<p class="lede">{subtitle}</p>
<div class="legend"><span><b>М</b>машина</span><span><b>С</b>сундук</span><span><b>Б</b>бак</span><span><b>В</b>воронка</span><span>▲ конвейер, остриё — куда везёт</span><span>мелкие квадраты — трубы и питание</span></div>
</header>
{body}
</main>"""
