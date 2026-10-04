"""A small Minecraft world for Altron's scenario runs (scenarios.py): no game, but his body answers every command the way
the mod does — the same texts, the same coordinates and directions, his hands acting tick by tick (walking, jumping,
breaking with the right tool, placing, hitting, eating, following a creature with the mouse).

It is coarse on purpose: flat ground at y=63, a few trees, stones and ores, animals, monsters and players placed by the
scenario. What it must get right is what the AI sees and what its hands can and cannot do.
"""
import math
import re

GROUND = 63            # the top grass block; the bot stands at y=64
EYE = 1.62
REACH = 4.5
HIT_REACH = 3.0

NAMES = {
    "minecraft:air": "воздух", "minecraft:grass_block": "Блок травы", "minecraft:dirt": "Земля",
    "minecraft:stone": "Камень", "minecraft:cobblestone": "Булыжник", "minecraft:bedrock": "Коренная порода",
    "minecraft:oak_log": "Дубовое бревно", "minecraft:oak_planks": "Дубовые доски", "minecraft:oak_leaves": "Дубовая листва",
    "minecraft:coal_ore": "Угольная руда", "minecraft:iron_ore": "Железная руда", "minecraft:diamond_ore": "Алмазная руда",
    "minecraft:crafting_table": "Верстак", "minecraft:furnace": "Печь", "minecraft:chest": "Сундук",
    "minecraft:sand": "Песок", "minecraft:gravel": "Гравий", "minecraft:water": "Вода", "minecraft:torch": "Факел",
    "minecraft:glass": "Стекло", "minecraft:oak_door": "Дубовая дверь", "minecraft:ladder": "Лестница",
    "minecraft:white_bed": "Белая кровать", "minecraft:wheat": "Пшеница",
    "minecraft:wooden_pickaxe": "Деревянная кирка", "minecraft:stone_pickaxe": "Каменная кирка",
    "minecraft:iron_pickaxe": "Железная кирка", "minecraft:diamond_pickaxe": "Алмазная кирка",
    "minecraft:wooden_axe": "Деревянный топор", "minecraft:stone_axe": "Каменный топор", "minecraft:iron_axe": "Железный топор",
    "minecraft:iron_shovel": "Железная лопата", "minecraft:wooden_sword": "Деревянный меч", "minecraft:stone_sword": "Каменный меч",
    "minecraft:iron_sword": "Железный меч", "minecraft:diamond_sword": "Алмазный меч", "minecraft:bow": "Лук",
    "minecraft:arrow": "Стрела", "minecraft:shield": "Щит", "minecraft:stick": "Палка", "minecraft:coal": "Уголь",
    "minecraft:raw_iron": "Необработанное железо", "minecraft:iron_ingot": "Железный слиток", "minecraft:diamond": "Алмаз",
    "minecraft:bread": "Хлеб", "minecraft:cooked_beef": "Стейк", "minecraft:beef": "Сырая говядина",
    "minecraft:apple": "Яблоко", "minecraft:rotten_flesh": "Гнилая плоть", "minecraft:bone": "Кость",
    "minecraft:string": "Нить", "minecraft:gunpowder": "Порох", "minecraft:leather": "Кожа", "minecraft:wheat_seeds": "Семена пшеницы",
    "minecraft:iron_helmet": "Железный шлем", "minecraft:iron_chestplate": "Железный нагрудник",
    "minecraft:oak_sapling": "Саженец дуба", "minecraft:flint_and_steel": "Огниво", "minecraft:water_bucket": "Ведро воды",
    "minecraft:bucket": "Ведро", "minecraft:porkchop": "Сырая свинина", "minecraft:white_wool": "Белая шерсть",
    "minecraft:feather": "Перо", "minecraft:chicken": "Сырая курица", "minecraft:emerald": "Изумруд",
}
SOLID = {"minecraft:grass_block", "minecraft:dirt", "minecraft:stone", "minecraft:cobblestone", "minecraft:bedrock",
         "minecraft:oak_log", "minecraft:oak_planks", "minecraft:oak_leaves", "minecraft:coal_ore", "minecraft:iron_ore",
         "minecraft:diamond_ore", "minecraft:crafting_table", "minecraft:furnace", "minecraft:chest", "minecraft:sand",
         "minecraft:gravel", "minecraft:glass", "minecraft:oak_door", "minecraft:white_bed"}
CONTAINERS = {"minecraft:chest": 27, "minecraft:furnace": 3, "minecraft:crafting_table": 10}
FOOD = {"minecraft:bread": 5, "minecraft:cooked_beef": 8, "minecraft:apple": 4, "minecraft:beef": 3,
        "minecraft:rotten_flesh": 4, "minecraft:porkchop": 3, "minecraft:chicken": 2}
# seconds to break by hand, and which tool makes it faster / is needed for a drop
HARD = {"minecraft:grass_block": (0.9, "shovel", None), "minecraft:dirt": (0.75, "shovel", None),
        "minecraft:sand": (0.75, "shovel", None), "minecraft:gravel": (0.9, "shovel", None),
        "minecraft:oak_log": (3.0, "axe", None), "minecraft:oak_planks": (3.0, "axe", None),
        "minecraft:crafting_table": (3.75, "axe", None), "minecraft:chest": (3.75, "axe", None),
        "minecraft:oak_door": (4.5, "axe", None), "minecraft:ladder": (0.6, "axe", None),
        "minecraft:oak_leaves": (0.35, None, None), "minecraft:glass": (0.45, None, None),
        "minecraft:stone": (7.5, "pickaxe", 1), "minecraft:cobblestone": (10.0, "pickaxe", 1),
        "minecraft:coal_ore": (15.0, "pickaxe", 1), "minecraft:iron_ore": (15.0, "pickaxe", 2),
        "minecraft:diamond_ore": (15.0, "pickaxe", 3), "minecraft:furnace": (17.5, "pickaxe", 1),
        "minecraft:bedrock": (None, None, None), "minecraft:torch": (0.05, None, None),
        "minecraft:wheat": (0.05, None, None), "minecraft:white_bed": (0.3, None, None),
        "minecraft:water": (None, None, None)}
TIER = {"wooden": (1, 2.0), "stone": (2, 4.0), "iron": (3, 6.0), "diamond": (4, 8.0)}
DROPS = {"minecraft:grass_block": "minecraft:dirt", "minecraft:stone": "minecraft:cobblestone",
         "minecraft:coal_ore": "minecraft:coal", "minecraft:iron_ore": "minecraft:raw_iron",
         "minecraft:diamond_ore": "minecraft:diamond", "minecraft:oak_leaves": None, "minecraft:glass": None}
DAMAGE = {"sword": {"wooden": 4, "stone": 5, "iron": 6, "diamond": 7}, "axe": {"wooden": 7, "stone": 9, "iron": 9, "diamond": 9},
          "pickaxe": {"wooden": 2, "stone": 3, "iron": 4, "diamond": 5}, "shovel": {"wooden": 2.5, "stone": 3.5, "iron": 4.5, "diamond": 5.5}}

MOBS = {   # type: (name, hp, hostile, height, drops)
    "zombie": ("Зомби", 20, True, 1.95, ["minecraft:rotten_flesh"]),
    "skeleton": ("Скелет", 20, True, 1.99, ["minecraft:bone", "minecraft:arrow"]),
    "creeper": ("Крипер", 20, True, 1.7, ["minecraft:gunpowder"]),
    "spider": ("Паук", 16, True, 0.9, ["minecraft:string"]),
    "enderman": ("Эндермен", 40, True, 2.9, []),
    "cow": ("Корова", 10, False, 1.4, ["minecraft:beef", "minecraft:leather"]),
    "pig": ("Свинья", 10, False, 0.9, ["minecraft:porkchop"]),
    "sheep": ("Овца", 8, False, 1.3, ["minecraft:white_wool"]),
    "chicken": ("Курица", 4, False, 0.7, ["minecraft:feather", "minecraft:chicken"]),
    "villager": ("Житель", 20, False, 1.95, []),
    "wolf": ("Волк", 8, False, 0.85, []),
}
KEYS = {"forward", "back", "left", "right", "jump", "sneak", "sprint", "inventory", "drop", "attack", "use",
        "swaphands", "chat", "playerlist", "pickitem", "command", "screenshot", "togglePerspective", "smoothCamera",
        "fullscreen", "spectatorOutlines", "advancements", "hotbar.1", "hotbar.2", "hotbar.3", "hotbar.4", "hotbar.5",
        "hotbar.6", "hotbar.7", "hotbar.8", "hotbar.9", "saveToolbarActivator", "loadToolbarActivator", "reload"}
KEY_ALIASES = {"w": "forward", "s": "back", "a": "left", "d": "right", "space": "jump", "shift": "sneak", "ctrl": "sprint",
               "e": "inventory", "q": "drop", "вперед": "forward", "вперёд": "forward", "назад": "back", "прыжок": "jump",
               "key.forward": "forward", "key.jump": "jump", "key.sneak": "sneak", "key.sprint": "sprint",
               "key.back": "back", "key.left": "left", "key.right": "right", "key.inventory": "inventory",
               "key.drop": "drop", "r": "reload", "key.reload": "reload"}


def name(i):
    return NAMES.get(i, i.split(":")[-1].replace("_", " "))


def bpos(x, y, z):
    return "%d %d %d" % (x, y, z)


def direction(yaw):
    d = (round(yaw / 90) % 4)
    return {0: "юг (z+)", 1: "запад (x-)", 2: "север (z-)", 3: "восток (x+)"}[d]


class Ent:
    _n = 100

    def __init__(self, kind, pos, name_=None, hp=None, player=False, held="ничего"):
        Ent._n += 1
        self.uid = Ent._n
        self.kind = kind
        self.player = player
        info = MOBS.get(kind, (kind, 20, False, 1.8, []))
        self.name = name_ or info[0]
        self.hp = float(hp if hp is not None else info[1])
        self.hostile = info[2] and not player
        self.height = 1.8 if player else info[3]
        self.drops = [] if player else info[4]
        self.pos = [float(v) for v in pos]
        self.held = held
        self.fuse = 0

    @property
    def type_id(self):
        return "minecraft:player" if self.player else "minecraft:" + self.kind

    def dist(self, p):
        return math.dist(self.pos, p)


class World:
    """The world, the bot's body and the commander. All positions are [x, y, z] floats (feet)."""

    def __init__(self, owner="MJreggich", night=False):
        self.blocks = {}                  # (x, y, z) -> block id, over the flat ground
        self.ents = []
        self.ground_items = []            # [pos, item, count]
        self.owner = owner
        self.bot = {"pos": [0.5, 64.0, 0.5], "yaw": 0.0, "pitch": 0.0, "hp": 20.0, "food": 20, "dim": "minecraft:overworld"}
        self.inv = [None] * 36             # [item, count]; 0-8 is the hotbar
        self.selected = 0
        self.armor = []
        self.screen = None                 # ("chest", pos) / ("inventory", None)
        self.contents = {}                 # container pos -> list of [item, count] or None
        self.night = night
        self.seen = set()                  # blocks the body has looked at (find_block remembers only these)
        self.chat_log = []
        self.task = None
        self.dead = False
        self.owner_ent = self.add_player(owner, [3.5, 64, 2.5])

    # ------------------------------------------------------------------ building the scene
    def add_player(self, nick, pos, held="ничего"):
        e = Ent("player", pos, nick, 20, player=True, held=held)
        self.ents.append(e)
        return e

    def add_mob(self, kind, pos, hp=None):
        e = Ent(kind, pos, hp=hp)
        self.ents.append(e)
        return e

    def set(self, x, y, z, block):
        self.blocks[(int(x), int(y), int(z))] = block

    def tree(self, x, z, h=5):
        for y in range(GROUND + 1, GROUND + 1 + h):
            self.set(x, y, z, "minecraft:oak_log")
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for y in (GROUND + h - 1, GROUND + h):
                    if (dx or dz) and self.get(x + dx, y, z + dz) == "minecraft:air":
                        self.set(x + dx, y, z + dz, "minecraft:oak_leaves")
        self.set(x, GROUND + h + 1, z, "minecraft:oak_leaves")

    def container(self, block, x, y, z, items=()):
        self.set(x, y, z, block)
        slots = [None] * CONTAINERS[block]
        for i, it in enumerate(items):
            slots[i] = list(it)
        self.contents[(x, y, z)] = slots

    def give(self, item, count=1, slot=None):
        stack = 1 if any(w in item for w in ("pickaxe", "axe", "sword", "shovel", "bow", "shield", "helmet", "chestplate",
                                              "bucket", "flint")) else 64
        if slot is not None and self.inv[slot - 1] is None:
            self.inv[slot - 1] = [item, min(count, stack)]
            count -= min(count, stack)
        for i, s in enumerate(self.inv):
            if count <= 0:
                break
            if s and s[0] == item and s[1] < stack:
                n = min(stack - s[1], count)
                s[1] += n
                count -= n
        for i, s in enumerate(self.inv):
            if count <= 0:
                break
            if s is None:
                self.inv[i] = [item, min(count, stack)]
                count -= min(count, stack)
        return count <= 0

    def count(self, item):
        return sum(s[1] for s in self.inv if s and s[0] == item)

    # ------------------------------------------------------------------ blocks
    def get(self, x, y, z):
        k = (int(math.floor(x)), int(math.floor(y)), int(math.floor(z)))
        if k in self.blocks:
            return self.blocks[k]
        y = k[1]
        if y > GROUND:
            return "minecraft:air"
        if y == GROUND:
            return "minecraft:grass_block"
        if y >= GROUND - 3:
            return "minecraft:dirt"
        if y <= 0:
            return "minecraft:bedrock"
        return "minecraft:stone"

    def solid(self, x, y, z):
        return self.get(x, y, z) in SOLID

    # ------------------------------------------------------------------ the body's senses
    @property
    def held(self):
        return self.inv[self.selected]

    def held_text(self):
        s = self.held
        return "%dx %s (%s)" % (s[1], name(s[0]), s[0]) if s else "ничего"

    def look_vec(self):
        y, p = math.radians(self.bot["yaw"]), math.radians(self.bot["pitch"])
        return (-math.sin(y) * math.cos(p), -math.sin(p), math.cos(y) * math.cos(p))

    def eye(self):
        x, y, z = self.bot["pos"]
        return (x, y + EYE, z)

    def aim(self):
        """What the crosshair is on: ("entity", e, dist) / ("block", (x, y, z), face, dist) / None."""
        ex, ey, ez = self.eye()
        dx, dy, dz = self.look_vec()
        last = None
        t = 0.0
        while t <= REACH:
            px, py, pz = ex + dx * t, ey + dy * t, ez + dz * t
            if t <= HIT_REACH:
                for e in self.ents:
                    if e.hp <= 0:
                        continue
                    if abs(px - e.pos[0]) < 0.4 and abs(pz - e.pos[2]) < 0.4 and e.pos[1] <= py <= e.pos[1] + e.height:
                        return ("entity", e, t)
            cell = (math.floor(px), math.floor(py), math.floor(pz))
            if cell != last:
                b = self.get(*cell)
                if b not in ("minecraft:air", "minecraft:water"):
                    face = "up"
                    if last is not None:
                        d = [cell[i] - last[i] for i in range(3)]
                        face = {(1, 0, 0): "west", (-1, 0, 0): "east", (0, 1, 0): "down", (0, -1, 0): "up",
                                (0, 0, 1): "north", (0, 0, -1): "south"}.get(tuple(d), "up")
                    self.seen.add(cell)
                    return ("block", cell, face, t, last)
                last = cell
            t += 0.05
        return None

    def look_around(self):
        """Blocks he can see from where he stands (a person glancing about): find_block remembers these."""
        x, y, z = (int(math.floor(v)) for v in self.bot["pos"])
        for (bx, by, bz), b in self.blocks.items():
            if abs(bx - x) <= 32 and abs(bz - z) <= 32 and abs(by - y) <= 12 and b != "minecraft:air":
                if any(self.get(bx + d[0], by + d[1], bz + d[2]) in ("minecraft:air", "minecraft:water")
                       for d in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))):
                    self.seen.add((bx, by, bz))

    def view(self):
        b = self.bot
        x, y, z = b["pos"]
        on = "в воде" if self.get(x, y, z) == "minecraft:water" else ("на земле" if self.solid(x, y - 0.01, z) else "в воздухе")
        out = "Ты: %.1f %.1f %.1f, смотришь на %s (поворот %.0f°, наклон %.0f°: + вниз), %s; здоровье %.0f/20, еда %d/20" % (
            x, y, z, direction(b["yaw"]), b["yaw"], b["pitch"], on, b["hp"], b["food"])
        s = self.held
        out += "\nВ руке (слот %d): %s" % (self.selected + 1, name(s[0]) if s else "пусто")
        out += "\nХотбар:" + "".join(" %d=%s%s" % (i + 1, name(s[0]), " x%d" % s[1] if s[1] > 1 else "")
                                    for i, s in enumerate(self.inv[:9]) if s)
        a = self.aim()
        if a is None:
            out += "\nПрицел: ничего в досягаемости"
        elif a[0] == "entity":
            out += "\nПрицел: существо %s, %.1f бл." % (a[1].name, a[2])
        else:
            out += "\nПрицел: блок %s в %s, грань %s, %.1f бл." % (name(self.get(*a[1])), bpos(*a[1]), a[2], a[3])
        fx, fz = self.facing()
        fx_, fy_, fz_ = int(math.floor(x)), int(math.floor(y)), int(math.floor(z))

        def side(word, dx, dz):
            return "%s: %s / %s" % (word, name(self.get(fx_ + dx, fy_, fz_ + dz)), name(self.get(fx_ + dx, fy_ + 1, fz_ + dz)))
        out += "\nВокруг (ноги / голова): " + "; ".join([
            side("впереди", fx, fz), side("слева", fz, -fx), side("справа", -fz, fx), side("сзади", -fx, -fz)])
        out += "; под ногами: %s; над головой: %s" % (name(self.get(fx_, fy_ - 1, fz_)), name(self.get(fx_, fy_ + 2, fz_)))
        return out

    def facing(self):
        d = round(self.bot["yaw"] / 90) % 4
        return {0: (0, 1), 1: (-1, 0), 2: (0, -1), 3: (1, 0)}[d]

    def status(self):
        x, y, z = (int(math.floor(v)) for v in self.bot["pos"])
        out = "Позиция: %d %d %d (%s)\nЗдоровье: %d/20, еда: %d/20\nВ руке: %s\nБроня: %s\nЗадача: %s\nВремя: %s" % (
            x, y, z, self.bot["dim"], round(self.bot["hp"]), self.bot["food"], self.held_text(),
            ", ".join(self.armor) or "нет", self.task or "нет", "ночь" if self.night else "день")
        o = self.owner_ent
        out += "\nИгрок %s: %s, расстояние %d" % (self.owner, bpos(*(int(math.floor(v)) for v in o.pos)),
                                                round(o.dist(self.bot["pos"])))
        return out

    def inventory(self):
        counts = {}
        for s in self.inv:
            if s:
                counts[s[0]] = counts.get(s[0], 0) + s[1]
        if not counts:
            return "Инвентарь пуст."
        return "Инвентарь:" + "".join("\n- %s (%s) x%d" % (name(i), i, n) for i, n in counts.items()) + \
            "\nВ руке: " + self.held_text()

    def nearby(self, radius=32):
        me = self.bot["pos"]
        lines = []
        for e in sorted((e for e in self.ents if e.hp > 0 and e.dist(me) <= radius), key=lambda e: e.dist(me)):
            kind = "игрок" if e.player else "враг" if e.hostile else "существо"
            lines.append("- %s: %s (%s), %d бл., %s, hp %d" % (kind, e.name, e.type_id, round(e.dist(me)),
                                                              bpos(*(int(math.floor(v)) for v in e.pos)), round(e.hp)))
        items = sum(1 for g in self.ground_items if math.dist(g[0], me) <= radius)
        if items:
            lines.append("- предметов на земле: %d" % items)
        usable = sorted(((math.dist(k, me), k, b) for k, b in self.blocks.items()
                         if b in CONTAINERS or b in ("minecraft:oak_door", "minecraft:ladder", "minecraft:white_bed")),
                        key=lambda t: t[0])
        usable = [u for u in usable if u[0] <= min(16, radius)][:12]
        out = "\n".join(lines)
        if usable:
            out += ("\n" if out else "") + "Блоки рядом, которые можно использовать (use_block по координатам):\n" + "\n".join(
                "- %s (%s) %s, %d бл." % (name(b), b, bpos(*k), round(d)) for d, k, b in usable)
        return out or "Рядом никого не вижу."

    def find_block(self, words, radius=64):
        want = set()
        for w in words:
            w = str(w).lower().strip()
            w = {"дерево": "бревно", "дерева": "бревно", "деревья": "бревно", "древесина": "бревно", "руда": "руда",
                 "руду": "руда", "булыжник": "булыжник", "земля": "земля", "землю": "земля"}.get(w, w)
            for i, n in NAMES.items():
                if i in SOLID and (w == i or w == i.split(":")[1] or w in n.lower() or (len(w) >= 4 and w[:-1] in n.lower())
                                   or w.replace("_", " ") in i.replace("_", " ")):
                    want.add(i)
        if not want:
            return {"ok": False, "msg": "не знаю такой блок"}
        self.look_around()
        me = self.bot["pos"]
        found = sorted((math.dist(k, me), k) for k in self.seen if self.get(*k) in want and math.dist(k, me) <= radius)
        ids = ", ".join(sorted(want))
        if not found:
            return {"ok": True, "msg": "рядом (в загруженных чанках) не нашёл: " + ids}
        return {"ok": True, "msg": "Нашёл %s:\n" % ids + "\n".join("- %s (%d бл.)" % (bpos(*k), round(d)) for d, k in found[:8])}

    def find_item(self, query):
        q = str(query).lower()
        hits = [(n, i) for i, n in NAMES.items() if q in n.lower() or q in i or (len(q) >= 4 and q[:-1] in n.lower())]
        if not hits:
            return {"ok": False, "msg": "ничего не найдено"}
        return {"ok": True, "msg": "\n".join("- %s = %s" % h for h in hits[:8])}

    def container_text(self):
        if self.screen is None:
            return "Контейнер не открыт."
        kind, pos = self.screen
        if kind == "inventory":
            return "Открыт: Инвентарь (твои вещи)\n" + self.inventory()
        slots = self.contents.get(pos, [])
        used = sum(1 for s in slots if s)
        out = "Открыт: %s (занято %d из %d)\n" % (name(self.get(*pos)), used, len(slots))
        out += "".join("- слот %d: %dx %s (%s)\n" % (i, s[1], name(s[0]), s[0]) for i, s in enumerate(slots) if s)
        if not used:
            return out + "(пусто)"
        return out + "Твои слоты начинаются с %d." % len(slots)

    # ------------------------------------------------------------------ the hands (control)
    def control(self, a):
        """One control call: keys held for ticks, the mouse aimed, the buttons pressed. Returns (ok, text)."""
        if self.dead:
            return False, "ты мёртв"
        keys = []
        for k in a.get("keys") or []:
            k = KEY_ALIASES.get(str(k).lower(), str(k))
            if k not in KEYS:
                return False, "нет такой клавиши: %s" % k
            keys.append(k)
        ticks = max(1, min(int(a.get("ticks") or 5), 200))
        did = []
        if keys and keys != ["inventory"] or a.get("left") or a.get("right"):
            self.screen = None
        if "inventory" in keys:
            self.screen = None if self.screen else ("inventory", None)
        slot = int(a.get("slot") or 0)
        if 1 <= slot <= 9:
            self.selected = slot - 1
            did.append("слот %d" % slot)
        b = self.bot
        want_yaw = b["yaw"] + float(a.get("turn") or 0)
        want_pitch = float(a["pitch"]) if a.get("pitch") is not None else b["pitch"] + float(a.get("tilt") or 0)
        if a.get("x") is not None and a.get("z") is not None:
            # like Actions.aimPoint: whole numbers are the centre of a block; no y — at eye level
            def centre(v):
                v = float(v)
                return v + 0.5 if v == math.floor(v) else v
            ty = centre(a["y"]) if a.get("y") is not None else b["pos"][1] + EYE
            want_yaw, want_pitch = self.rotation_to((centre(a["x"]), ty, centre(a["z"])))
        track = None
        if a.get("track"):
            track = self.track_target(str(a["track"]))
            did.append("веду прицел за %s (%.1f бл.)" % (track.name, track.dist(b["pos"])) if track else "не вижу " + a["track"])
        b["pitch"] = max(-90.0, min(90.0, want_pitch))
        dig, dig_t, left_done, right_done, ate = None, 0.0, False, False, 0
        for t in range(ticks):
            if track is not None and track.hp > 0:
                want_yaw, want_pitch = self.rotation_to((track.pos[0], track.pos[1] + track.height * 0.8, track.pos[2]))
                b["pitch"] = max(-90.0, min(90.0, want_pitch))
            dy = ((want_yaw - b["yaw"] + 180) % 360) - 180
            b["yaw"] = (b["yaw"] + max(-40, min(40, dy)))
            aimed = abs(dy) <= 40
            self.move(keys, track)
            h = self.aim()
            left = a.get("left")
            if left and aimed and (left == "hold" or not left_done):
                left_done = True
                if h and h[0] == "entity":
                    e = h[1]
                    if t % 12 == 0 or left == "click":
                        self.hit(e)
                        if not any(d.startswith("ударил") for d in did):
                            did.append("ударил " + e.name)
                elif h and h[0] == "block":
                    if h[1] != dig:
                        dig, dig_t = h[1], 0.0
                    dig_t += 0.05
                    need = self.break_time(self.get(*dig))
                    if need is not None and dig_t >= need:
                        self.break_block(dig)
                        dig, dig_t = None, 0.0
                        if left == "click":
                            pass
            right = a.get("right")
            if right and aimed and not right_done:
                right_done = True
                did_r = self.use(h)
                if did_r:
                    did.append(did_r)
            if right == "hold" and self.held and self.held[0] in FOOD:
                ate += 1
                if ate == 32:
                    self.eat()
                    did.append("съел " + name(self.held[0]) if self.held else "съел")
            self.tick_world()
            if self.dead:
                return False, "ты погиб: %s" % self.death_cause
        b["yaw"] = ((b["yaw"] + 180) % 360) - 180
        return True, ((", ".join(did) + "\n") if did else "") + self.view()

    def rotation_to(self, p):
        ex, ey, ez = self.eye()
        dx, dy, dz = p[0] - ex, p[1] - ey, p[2] - ez
        yaw = math.degrees(math.atan2(-dx, dz))
        pitch = -math.degrees(math.atan2(dy, math.hypot(dx, dz)))
        return yaw, pitch

    def track_target(self, what):
        w = what.lower().strip()
        me = self.bot["pos"]
        alive = [e for e in self.ents if e.hp > 0]
        if w in ("", "hostile", "monsters", "враги", "мобы"):
            cands = [e for e in alive if e.hostile]
        elif w.startswith("player:"):
            cands = [e for e in alive if e.player and e.name.lower() == w[7:]]
        else:
            cands = [e for e in alive if not (e.player and e.name.lower() == self.owner.lower())
                     and (w in e.type_id or w in e.name.lower() or (len(w) >= 4 and w[:-2] in e.name.lower()))]
        cands = [e for e in cands if e.dist(me) <= 40]
        return min(cands, key=lambda e: e.dist(me)) if cands else None

    def move(self, keys, track):
        b = self.bot
        f = (1 if "forward" in keys else 0) - (1 if "back" in keys else 0)
        s = (1 if "right" in keys else 0) - (1 if "left" in keys else 0)
        speed = 0.065 if "sneak" in keys else 0.28 if ("sprint" in keys and f > 0) else 0.216
        x, y, z = b["pos"]
        if f or s:
            yaw = math.radians(b["yaw"])
            fx, fz = -math.sin(yaw), math.cos(yaw)
            rx, rz = -fz, fx   # right of the look
            dx, dz = (fx * f + rx * s) * speed, (fz * f + rz * s) * speed
            if track is not None and f > 0 and track.dist(b["pos"]) < 1.0:
                dx = dz = 0.0
            nx, nz = x + dx, z + dz
            if not self.solid(nx, y, nz) and not self.solid(nx, y + 1, nz):
                x, z = nx, nz
            elif "jump" in keys and not self.solid(nx, y + 1, nz) and not self.solid(nx, y + 2, nz) \
                    and not self.solid(x, y + 2, z) and self.solid(x, y - 0.01, z):
                x, y, z = nx, math.floor(y) + 1.0, nz
            elif not self.solid(x + dx, y, z) and not self.solid(x + dx, y + 1, z):
                x += dx
            elif not self.solid(x, y, z + dz) and not self.solid(x, y + 1, z + dz):
                z += dz
        elif "jump" in keys and self.solid(x, y - 0.01, z) and not self.solid(x, y + 2, z):
            y += 1.2   # a jump in place: he is in the air at the end of a short press
            if not self.solid(x, y - 1.2, z):
                pass
        fell = 0
        while not self.solid(x, y - 0.01, z) and y > 1 and fell < 300 and self.get(x, y, z) != "minecraft:water":
            if "jump" in keys and fell == 0 and y - math.floor(y) > 0.1:
                break   # still in the jump
            y = math.floor(y - 0.01) if y - math.floor(y) > 0.01 else y - 1
            fell += 1
        if fell > 3:
            self.hurt(fell - 3, "упал с высоты")
        b["pos"] = [x, y, z]
        for g in list(self.ground_items):
            if math.dist(g[0], b["pos"]) <= 1.6 and self.give(g[1], g[2]):
                self.ground_items.remove(g)

    def break_time(self, block):
        hard, tool, tier = HARD.get(block, (1.0, None, None))
        if hard is None:
            return None
        s = self.held
        mult, ok_tier = 1.0, 0
        if s:
            for t, (lvl, sp) in TIER.items():
                if s[0].startswith("minecraft:%s_" % t) and tool and s[0].endswith("_" + tool):
                    mult, ok_tier = sp, lvl
        if tier and ok_tier < tier:
            return hard * 3.3   # the wrong tool: very slow and nothing drops
        return hard / mult

    def break_block(self, cell):
        block = self.get(*cell)
        hard, tool, tier = HARD.get(block, (1.0, None, None))
        s = self.held
        ok_tier = max([lvl for t, (lvl, _) in TIER.items() if s and s[0].startswith("minecraft:%s_pickaxe" % t)] or [0])
        self.blocks[tuple(cell)] = "minecraft:air"
        if tier and ok_tier < tier:
            return
        drop = DROPS.get(block, block)
        if drop:
            self.ground_items.append([[cell[0] + 0.5, cell[1], cell[2] + 0.5], drop, 1])
        if block in CONTAINERS:
            for it in self.contents.pop(tuple(cell), []):
                if it:
                    self.ground_items.append([[cell[0] + 0.5, cell[1], cell[2] + 0.5], it[0], it[1]])
        if block == "minecraft:oak_leaves" and hash(cell) % 9 == 0:
            self.ground_items.append([[cell[0] + 0.5, cell[1], cell[2] + 0.5], "minecraft:apple", 1])

    def hit(self, e):
        s = self.held
        dmg = 1
        if s:
            for kind, table in DAMAGE.items():
                for t, d in table.items():
                    if s[0] == "minecraft:%s_%s" % (t, kind):
                        dmg = d
        e.hp -= dmg
        if e.hp <= 0:
            for d in e.drops:
                self.ground_items.append([list(e.pos), d, 1])
        elif not e.hostile and not e.player:
            e.pos[0] += 2   # an animal runs off a little

    def use(self, h):
        s = self.held
        if h and h[0] == "block":
            cell = h[1]
            block = self.get(*cell)
            if block in CONTAINERS:
                self.screen = (block.split(":")[1], cell)
                return "открыл " + name(block)
            if block == "minecraft:white_bed":
                return "лечь можно только ночью" if not self.night else "лёг спать"
            if s and s[0] in SOLID and h[4] is not None:
                place = h[4]
                me = self.bot["pos"]
                if (math.floor(me[0]), math.floor(me[2])) == (place[0], place[2]) and place[1] in (math.floor(me[1]),
                                                                                                 math.floor(me[1]) + 1):
                    return ""
                self.blocks[tuple(place)] = s[0]
                if s[0] in CONTAINERS:
                    self.contents[tuple(place)] = [None] * CONTAINERS[s[0]]
                s[1] -= 1
                if s[1] <= 0:
                    self.inv[self.selected] = None
                return ""
        if h and h[0] == "entity" and h[1].kind == "villager":
            return "открыл торговлю с жителем"
        return ""

    def eat(self):
        s = self.held
        if not s or s[0] not in FOOD or self.bot["food"] >= 20:
            return
        self.bot["food"] = min(20, self.bot["food"] + FOOD[s[0]])
        s[1] -= 1
        if s[1] <= 0:
            self.inv[self.selected] = None

    def hurt(self, dmg, cause):
        self.bot["hp"] -= dmg
        if self.bot["hp"] <= 0:
            self.dead = True
            self.death_cause = cause

    def tick_world(self):
        """Monsters come at him (and at the commander) and hit; a creeper next to him blows up."""
        me = self.bot["pos"]
        self.tick_n = getattr(self, "tick_n", 0) + 1
        for e in self.ents:
            if e.hp <= 0 or not e.hostile:
                continue
            d = e.dist(me)
            if d <= 32 and d > 1.2 and e.kind != "creeper" or (e.kind == "creeper" and 1.5 < d <= 16):   # a zombie follows from 35
                k = 0.1 / d
                e.pos[0] += (me[0] - e.pos[0]) * k
                e.pos[2] += (me[2] - e.pos[2]) * k
            if e.kind == "creeper":
                e.fuse = e.fuse + 1 if d <= 3 else 0
                if e.fuse >= 30:
                    e.hp = 0
                    self.hurt(max(0, 22 - d * 5), "взорвал крипер")
                    for dx in (-1, 0, 1):
                        for dz in (-1, 0, 1):
                            self.blocks[(int(e.pos[0]) + dx, GROUND, int(e.pos[2]) + dz)] = "minecraft:air"
            elif d <= 1.6 and self.tick_n % 20 == 0:
                self.hurt(3 if e.kind != "enderman" else 7, "убил %s" % e.name)

    # ------------------------------------------------------------------ the mod's other commands
    def call(self, cmd, a):
        """A command of the brain to the body, answered like Actions.java does: {"ok", "msg", ...}."""
        if cmd == "status":
            return {"ok": True, "msg": self.status()}
        if cmd == "inventory":
            return {"ok": True, "msg": self.inventory()}
        if cmd == "inventory_ids":
            items = {}
            for s in self.inv:
                if s:
                    items[s[0]] = items.get(s[0], 0) + s[1]
            return {"ok": True, "msg": "инвентарь", "items": items, "creative": False}
        if cmd == "nearby":
            return {"ok": True, "msg": self.nearby(float(a.get("radius") or 32))}
        if cmd == "view":
            return {"ok": True, "msg": self.view()}
        if cmd == "find_block":
            words = a.get("block")
            words = words if isinstance(words, list) else re.split(r"[,;]", str(words or ""))
            return self.find_block(words, int(a.get("radius") or 64))
        if cmd == "find_item":
            return self.find_item(a.get("query") or a.get("item") or "")
        if cmd in ("screen", "container"):
            return {"ok": True, "msg": self.container_text()}
        if cmd == "stop":
            self.task = None
            return {"ok": True, "msg": "остановился"}
        if cmd == "chat":
            text = str(a.get("text", ""))
            self.chat_log.append(text)
            if text.startswith("/"):
                return {"ok": True, "msg": "команда отправлена: " + text}
            return {"ok": True, "msg": "написал в чат"}
        if cmd in ("recall_items", "recall_entities"):
            return {"ok": True, "msg": "не помню такого"}
        if cmd == "memory":
            return {"ok": True, "msg": ""}
        if cmd in ("multiblocks", "multiblocks_data"):
            return {"ok": True, "msg": "В этой сборке нет многоблочных машин (Immersive Engineering не установлен).",
                    "multiblocks": []}
        if cmd == "build_multiblock":
            return {"ok": False, "msg": "нет такого чертежа: в этой сборке нет Immersive Engineering"}
        if cmd == "build_plan":
            fx, fz = self.facing()
            x, y, z = (int(math.floor(v)) for v in self.bot["pos"])
            ox = int(a.get("x", x + fx * 3))
            oz = int(a.get("z", z + fz * 3))
            return {"ok": True, "msg": "место есть", "origin": [ox, int(a.get("y", y)), oz]}
        if cmd == "render":
            return {"ok": True, "msg": "окно рисуется" if str(a.get("on", True)).lower() not in ("false", "0") else "не рисую"}
        if cmd == "item_info":
            i = str(a.get("item", ""))
            hits = self.find_item(i)
            return {"ok": True, "msg": hits["msg"] if hits["ok"] else "не знаю такой предмет: " + i}
        if cmd == "screenshot":
            return {"ok": False, "msg": "в этом прогоне глаз нет (мир подставной)"}
        if cmd == "click_slot":
            return self.click_slot(a)
        if cmd in ("gui_click", "gui_widget", "gui_type", "gui_key"):
            if self.screen is None:
                return {"ok": False, "msg": "окно не открыто"}
            if cmd == "gui_key" and str(a.get("key", "")).lower() in ("escape", "esc", "e"):
                self.screen = None
                return {"ok": True, "msg": "закрыл окно"}
            return {"ok": True, "msg": "нажал; " + self.container_text()}
        if cmd == "recipe":
            return {"ok": False, "msg": "не знаю такой предмет: " + str(a.get("item", ""))}
        return {"ok": False, "msg": "неизвестная команда: " + cmd}

    def click_slot(self, a):
        if self.screen is None or self.screen[0] == "inventory":
            return {"ok": False, "msg": "контейнер не открыт"}
        slots = self.contents.get(self.screen[1], [])
        i = int(a.get("slot", -1))
        typ = str(a.get("type", "pickup"))
        if 0 <= i < len(slots):
            s = slots[i]
            if s is None:
                return {"ok": True, "msg": "слот %d пуст\n%s" % (i, self.container_text())}
            if typ in ("quick_move", "pickup", "throw"):
                if typ != "throw":
                    self.give(s[0], s[1])
                slots[i] = None
                return {"ok": True, "msg": "взял %dx %s\n%s" % (s[1], name(s[0]), self.container_text())}
        j = i - len(slots)
        if 0 <= j < 36 and self.inv[j] is not None:
            s = self.inv[j]
            for k, c in enumerate(slots):
                if c is None:
                    slots[k] = s
                    self.inv[j] = None
                    return {"ok": True, "msg": "положил %dx %s\n%s" % (s[1], name(s[0]), self.container_text())}
            return {"ok": False, "msg": "контейнер полон"}
        return {"ok": False, "msg": "нет такого слота: %d" % i}

    def state(self):
        """What the body reports to the brain every second (Hub.state)."""
        o = self.owner_ent
        return {"pos": list(self.bot["pos"]), "hp": round(self.bot["hp"]), "food": self.bot["food"],
                "held": self.held_text(), "owner_pos": list(o.pos), "dim": self.bot["dim"],
                "task": self.task or "", "owner_look": getattr(self, "owner_look", ""), "night": self.night}
