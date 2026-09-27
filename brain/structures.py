"""Buildings Altron can put up from a description ("построй дом 7 на 7 из булыжника"): a plan of blocks for the body's
build_plan. Coordinates start at the building's corner: x along its width, z along its length, y up; 0 is the floor."""

LIMITS = {"width": (3, 32), "length": (3, 32), "height": (2, 20)}
DEFAULTS = {
    "house": {"width": 7, "length": 7, "height": 4},
    "shelter": {"width": 5, "length": 5, "height": 3},
    "wall": {"width": 10, "length": 1, "height": 3},
    "tower": {"width": 5, "length": 5, "height": 10},
    "platform": {"width": 8, "length": 8, "height": 1},
    "bridge": {"width": 12, "length": 3, "height": 1},
}
NAMES = {"house": "дом", "shelter": "укрытие", "wall": "стену", "tower": "башню", "platform": "площадку", "bridge": "мост"}


def _size(args, kind, key):
    lo, hi = LIMITS[key]
    try:
        v = int(args.get(key) or DEFAULTS[kind][key])
    except (TypeError, ValueError):
        v = DEFAULTS[kind][key]
    return max(lo if key != "length" or kind not in ("wall",) else 1, min(hi, v))


def plan(kind, width, length, height, material, roof=None):
    """[[x, y, z, block], ...] for one kind of building."""
    roof = roof or material
    blocks = {}

    def put(x, y, z, block=material):
        blocks[(x, y, z)] = block

    if kind in ("house", "shelter"):
        w, l, h = width, length, height
        door = {(w // 2, 1, 0), (w // 2, 2, 0)}
        windows = {(0, 2, l // 2), (w - 1, 2, l // 2), (w // 2, 2, l - 1)} if h >= 3 and kind == "house" else set()
        for x in range(w):
            for z in range(l):
                put(x, 0, z)                      # floor
                put(x, h + 1, z, roof)            # roof
                if x in (0, w - 1) or z in (0, l - 1):
                    for y in range(1, h + 1):
                        if (x, y, z) not in door and (x, y, z) not in windows:
                            put(x, y, z)
    elif kind == "wall":
        for x in range(width):
            for z in range(length):
                for y in range(height):
                    put(x, y, z)
    elif kind == "tower":
        w, l, h = width, length, height
        for y in range(h):
            for x in range(w):
                for z in range(l):
                    if (x in (0, w - 1) or z in (0, l - 1)) and not (x == w // 2 and z == 0 and y in (0, 1)):
                        put(x, y, z)
        for x in range(w):
            for z in range(l):
                put(x, h, z, roof)                # the top
                if (x in (0, w - 1) or z in (0, l - 1)) and (x + z) % 2 == 0:
                    put(x, h + 1, z)              # battlements
    elif kind == "platform":
        for x in range(width):
            for z in range(length):
                put(x, 0, z)
    elif kind == "bridge":
        for x in range(width):
            for z in range(length):
                put(x, 0, z)
                if z in (0, length - 1) and length >= 3:
                    put(x, 1, z)                  # rails
    else:
        raise ValueError("не знаю постройку %s (есть: %s)" % (kind, ", ".join(DEFAULTS)))
    return [[x, y, z, b] for (x, y, z), b in sorted(blocks.items(), key=lambda kv: (kv[0][1], kv[0][0], kv[0][2]))]


def plan_args(args, resolve=lambda q: q):
    """The tool's arguments ("kind", sizes, "material", "roof_material", optional x y z) -> the body's build_plan."""
    kind = str(args.get("kind", "house")).lower()
    if kind not in DEFAULTS:
        raise ValueError("не знаю постройку %s (есть: %s)" % (kind, ", ".join(DEFAULTS)))
    width, length, height = (_size(args, kind, k) for k in ("width", "length", "height"))
    material = _block_id(resolve(str(args.get("material") or "minecraft:cobblestone")))
    roof = _block_id(resolve(str(args["roof_material"]))) if args.get("roof_material") else None
    out = {"blocks": plan(kind, width, length, height, material, roof),
           "what": "%s %dx%d" % (NAMES[kind], width, length if kind != "wall" else height)}
    if all(k in args for k in ("x", "y", "z")):
        out.update(x=args["x"], y=args["y"], z=args["z"])
    return out


def _block_id(ref):
    ref = ref.strip().lower()
    return ref if ":" in ref else "minecraft:" + ref
