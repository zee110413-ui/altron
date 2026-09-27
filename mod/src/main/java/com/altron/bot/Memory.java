package com.altron.bot;

import com.altron.AltronMod;
import it.unimi.dsi.fastutil.longs.Long2ObjectMap;
import it.unimi.dsi.fastutil.longs.Long2ObjectOpenHashMap;
import net.minecraft.client.Minecraft;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.tags.BlockTags;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.ExperienceOrb;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.animal.Animal;
import net.minecraft.world.entity.item.ItemEntity;
import net.minecraft.world.entity.npc.AbstractVillager;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.entity.projectile.Projectile;
import net.minecraft.world.inventory.AbstractContainerMenu;
import net.minecraft.world.inventory.CraftingMenu;
import net.minecraft.world.inventory.Slot;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.ClipContext;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Random;
import java.util.Set;
import java.util.TreeMap;

/**
 * The bot's eyes and memory. Every tick it casts rays from its eyes (like looking around);
 * notable blocks it actually sees (ores, logs, containers, machines, modded blocks) are remembered.
 * It also remembers what was inside every container it opened, and where it saw players, animals,
 * villagers and vehicles. Nothing is known through walls. Memory is saved per world.
 */
public final class Memory {
    // 50 rays a tick = 1000 a second: a new place is looked over within a few seconds, at half the CPU of 100.
    // The launch mode sets it: economy 25, balanced 50, maximum 100
    private static final int RAYS_PER_TICK = Math.max(5, Integer.getInteger("altron.rays", 50));
    private static final double VIEW_RANGE = 48;
    private static final int MAX_ENTRIES = 300_000;
    private static final int MAX_SIGHTINGS = 400;

    /** What the bot saw inside a container: item id -> count. */
    private record Stash(String block, Map<String, Integer> items, long time) {
    }

    /** Where the bot saw a creature or a vehicle. */
    private record Sighting(String dim, String type, String name, BlockPos pos, long time) {
    }

    private static final Map<String, Long2ObjectOpenHashMap<Block>> DIMS = new HashMap<>();
    private static final Map<String, Long2ObjectOpenHashMap<Stash>> STASHES = new HashMap<>();
    private static final List<Sighting> SIGHTINGS = new ArrayList<>();
    private static final Map<Block, Boolean> INTERESTING = new HashMap<>();
    private static final Random RNG = new Random();
    private static String worldKey = "";
    private static boolean dirty;       // seen blocks changed
    private static boolean dirtyExtra;  // containers or sightings changed
    private static int ticks;
    /** The block the bot right-clicked last: a container that opens belongs to it. */
    public static BlockPos opened;

    private Memory() {
    }

    public static boolean interesting(BlockState s) {
        if (s.isAir()) return false;
        return INTERESTING.computeIfAbsent(s.getBlock(), b -> {
            ResourceLocation id = BuiltInRegistries.BLOCK.getKey(b);
            String p = id.getPath();
            if (s.hasBlockEntity()) return true;                 // chests, barrels, furnaces, machines...
            if (!id.getNamespace().equals("minecraft")) {
                // modded: remember everything except plain decoration-like stone/dirt variants
                return !(p.endsWith("_stone") || p.endsWith("_bricks") || p.endsWith("_slab") || p.endsWith("_stairs"));
            }
            return p.contains("ore") || p.contains("debris") || s.is(BlockTags.LOGS) || p.equals("crafting_table")
                    || p.contains("anvil") || p.contains("table") || p.contains("bed") || p.equals("lava")
                    || p.contains("obsidian") || p.contains("melon") || p.contains("pumpkin") || p.contains("sugar_cane")
                    || p.contains("wheat") || p.contains("carrots") || p.contains("potatoes") || p.contains("amethyst")
                    || p.equals("clay") || p.equals("gravel") || p.equals("sand") || p.contains("spawner");
        });
    }

    private static String dimKey() {
        ClientLevel level = Bot.level();
        return level == null ? "none" : level.dimension().location().toString();
    }

    private static Long2ObjectOpenHashMap<Block> dim() {
        return DIMS.computeIfAbsent(dimKey(), k -> new Long2ObjectOpenHashMap<>());
    }

    private static Long2ObjectOpenHashMap<Stash> stashes() {
        return STASHES.computeIfAbsent(dimKey(), k -> new Long2ObjectOpenHashMap<>());
    }

    public static void see(BlockPos pos, BlockState state) {
        // the shell of a big mod machine (HBM's universal_machine_part...): a person sees the whole machine's model,
        // so the machine itself — the core inside, a few blocks away — counts as seen
        String path = BuiltInRegistries.BLOCK.getKey(state.getBlock()).getPath();
        if (path.contains("machine_part") || path.contains("dummy") || path.contains("multiblock_part")) {
            BlockPos core = coreNear(pos, state.getBlock());
            if (core != null) see(core, Bot.level().getBlockState(core));
        }
        Long2ObjectOpenHashMap<Block> m = dim();
        long key = pos.asLong();
        if (interesting(state)) {
            if (m.size() < MAX_ENTRIES && m.put(key, state.getBlock()) != state.getBlock()) dirty = true;
        } else if (m.remove(key) != null) {
            dirty = true;
        }
        if (!state.hasBlockEntity() && stashes().remove(key) != null) dirtyExtra = true;   // the container is gone
    }

    /** The machine a shell block belongs to: the nearest block of the same mod with its own block entity. */
    private static final Map<Long, Long> SHELL_CORE = new HashMap<>();   // a factory's walls are hit by many rays

    private static BlockPos coreNear(BlockPos pos, Block shell) {
        Long known = SHELL_CORE.get(pos.asLong());
        if (known != null) return known == Long.MIN_VALUE ? null : BlockPos.of(known);
        if (SHELL_CORE.size() > 20000) SHELL_CORE.clear();
        BlockPos core = findCore(pos, shell);
        SHELL_CORE.put(pos.asLong(), core == null ? Long.MIN_VALUE : core.asLong());
        return core;
    }

    private static BlockPos findCore(BlockPos pos, Block shell) {
        String ns = BuiltInRegistries.BLOCK.getKey(shell).getNamespace();
        BlockPos best = null;
        double bestD = Double.MAX_VALUE;
        for (BlockPos bp : BlockPos.betweenClosed(pos.offset(-4, -4, -4), pos.offset(4, 4, 4))) {
            BlockState st = Bot.level().getBlockState(bp);
            if (st.getBlock() == shell || !st.hasBlockEntity()) continue;
            var id = BuiltInRegistries.BLOCK.getKey(st.getBlock());
            if (!id.getNamespace().equals(ns) || id.getPath().contains("part") || id.getPath().contains("dummy")) continue;
            double d = bp.distSqr(pos);
            if (d < bestD) {
                bestD = d;
                best = bp.immutable();
            }
        }
        return best;
    }

    public static void forget(BlockPos pos) {
        if (dim().remove(pos.asLong()) != null) dirty = true;
    }

    /** Look around: called every tick. */
    public static void tick() {
        LocalPlayer p = Bot.player();
        ClientLevel level = Bot.level();
        if (p == null || level == null) return;
        Vec3 eye = p.getEyePosition();
        Vec3 look = p.getViewVector(1f);
        for (int i = 0; i < RAYS_PER_TICK; i++) {
            Vec3 dir;
            if (i % 3 == 0) {
                // a third of the rays go where the bot is looking
                dir = look.add(RNG.nextGaussian() * 0.35, RNG.nextGaussian() * 0.35, RNG.nextGaussian() * 0.35).normalize();
            } else {
                dir = new Vec3(RNG.nextGaussian(), RNG.nextGaussian(), RNG.nextGaussian()).normalize();
            }
            BlockHitResult hit = level.clip(new ClipContext(eye, eye.add(dir.scale(VIEW_RANGE)),
                    ClipContext.Block.VISUAL, ClipContext.Fluid.NONE, p));
            if (hit.getType() == HitResult.Type.BLOCK) see(hit.getBlockPos(), level.getBlockState(hit.getBlockPos()));
        }
        ++ticks;
        if (ticks % 10 == 0) rememberOpenContainer(p);
        if (ticks % 20 == 0) lookAtCreatures(p, level);
        if (ticks % (20 * 60) == 0) save();
    }

    /**
     * Look all around, like a person turning in place: a dense fan of rays over the whole sphere (line of sight
     * only, nothing through walls). Everyday vision is a few random rays a tick; this is for "let me look".
     */
    public static int lookAround() {
        LocalPlayer p = Bot.player();
        ClientLevel level = Bot.level();
        if (p == null || level == null) return 0;
        Vec3 eye = p.getEyePosition();
        int n = 0;
        for (int pitch = -80; pitch <= 80; pitch += 2) {
            double cos = Math.cos(Math.toRadians(pitch));
            int step = Math.max(1, (int) Math.round(2 / Math.max(0.05, cos)));   // fewer rays near the poles
            for (int yaw = 0; yaw < 360; yaw += step) {
                Vec3 dir = Vec3.directionFromRotation(pitch, yaw);
                BlockHitResult hit = level.clip(new ClipContext(eye, eye.add(dir.scale(VIEW_RANGE)),
                        ClipContext.Block.VISUAL, ClipContext.Fluid.NONE, p));
                if (hit.getType() == HitResult.Type.BLOCK) {
                    see(hit.getBlockPos(), level.getBlockState(hit.getBlockPos()));
                    n++;
                }
            }
        }
        return n;
    }

    /** Remembered positions of these blocks, nearest first. */
    public static List<BlockPos> find(Set<Block> blocks, int radius, int limit) {
        LocalPlayer p = Bot.player();
        BlockPos me = p.blockPosition();
        double r2 = (double) radius * radius;
        List<BlockPos> out = new ArrayList<>();
        List<Long> stale = new ArrayList<>();
        for (Long2ObjectMap.Entry<Block> e : dim().long2ObjectEntrySet()) {
            if (!blocks.contains(e.getValue())) continue;
            BlockPos bp = BlockPos.of(e.getLongKey());
            double d = bp.distSqr(me);
            if (d > r2) continue;
            // right next to it, or in plain sight: the bot would see that it is gone
            if (d < 48 * 48 && Bot.level().hasChunkAt(bp) && Bot.level().getBlockState(bp).getBlock() != e.getValue()
                    && (d < 25 || Bot.canSee(bp))) {
                stale.add(e.getLongKey());
                continue;
            }
            out.add(bp);
        }
        for (long k : stale) dim().remove(k);
        out.sort(Comparator.comparingDouble(bp -> bp.distSqr(me)));
        return out.size() > limit ? new ArrayList<>(out.subList(0, limit)) : out;
    }

    /** Every remembered spot within a radius of a point (for working out how a base is laid out). */
    public static List<BlockPos> around(BlockPos center, int radius) {
        double r2 = (double) radius * radius;
        List<BlockPos> out = new ArrayList<>();
        for (long k : dim().keySet()) {
            BlockPos bp = BlockPos.of(k);
            if (bp.distSqr(center) <= r2) out.add(bp);
        }
        return out;
    }

    /** What he remembers standing there (also when that part of the world is not loaded around him now). */
    public static Block remembered(BlockPos pos) {
        return dim().get(pos.asLong());
    }

    public static int size() {
        return dim().size();
    }

    public static String worldKey() {
        return worldKey;
    }

    // ------------------------------------------------------------------ containers

    private static void rememberOpenContainer(LocalPlayer p) {
        AbstractContainerMenu menu = p.containerMenu;
        if (menu == p.inventoryMenu || opened == null || menu instanceof CraftingMenu) return;
        BlockState s = Bot.level().getBlockState(opened);
        if (!s.hasBlockEntity() || Bot.distTo(opened) > 8) return;
        Map<String, Integer> items = new TreeMap<>();
        for (Slot slot : menu.slots) {
            if (slot.container == p.getInventory() || !slot.hasItem()) continue;
            ItemStack st = slot.getItem();
            items.merge(BuiltInRegistries.ITEM.getKey(st.getItem()).toString(), st.getCount(), Integer::sum);
        }
        long key = opened.asLong();
        Stash old = stashes().get(key);
        if (old == null || !old.items().equals(items)) dirtyExtra = true;
        stashes().put(key, new Stash(BuiltInRegistries.BLOCK.getKey(s.getBlock()).toString(), items, System.currentTimeMillis()));
    }

    /** Containers where the bot saw items matching the query (name or id); empty query = all containers. */
    public static String recallItems(String query, int limit) {
        String q = norm(query);
        Set<String> ids = new HashSet<>();
        if (!q.isEmpty()) for (Item i : Names.items(query, 20)) ids.add(BuiltInRegistries.ITEM.getKey(i).toString());
        BlockPos me = Bot.player().blockPosition();
        List<Map.Entry<Long, Stash>> found = new ArrayList<>();
        for (Long2ObjectMap.Entry<Stash> e : stashes().long2ObjectEntrySet()) {
            if (q.isEmpty() || e.getValue().items().keySet().stream().anyMatch(id -> ids.contains(id) || matches(id, q))) {
                found.add(Map.entry(e.getLongKey(), e.getValue()));
            }
        }
        if (found.isEmpty()) {
            return q.isEmpty() ? "я ещё не заглядывал ни в один сундук в этом мире"
                    : "не помню, чтобы видел «" + query + "» в сундуках или машинах";
        }
        found.sort(Comparator.comparingDouble(e -> BlockPos.of(e.getKey()).distSqr(me)));
        StringBuilder sb = new StringBuilder();
        for (Map.Entry<Long, Stash> e : found.subList(0, Math.min(limit, found.size()))) {
            BlockPos bp = BlockPos.of(e.getKey());
            Stash st = e.getValue();
            sb.append("- ").append(blockName(st.block())).append(" ").append(Bot.pos(bp))
                    .append(" (").append(Math.round(Math.sqrt(bp.distSqr(me)))).append(" бл., заглядывал ").append(ago(st.time())).append("): ");
            List<String> parts = new ArrayList<>();
            for (Map.Entry<String, Integer> it : st.items().entrySet()) {
                if (q.isEmpty() || ids.contains(it.getKey()) || matches(it.getKey(), q) || parts.size() < 3) {
                    parts.add(it.getValue() + "x " + itemName(it.getKey()));
                }
                if (parts.size() >= 12) break;
            }
            sb.append(st.items().isEmpty() ? "пусто" : String.join(", ", parts)).append('\n');
        }
        return sb.toString().trim();
    }

    // ------------------------------------------------------------------ creatures and vehicles

    private static boolean notable(Entity e) {
        if (e instanceof Player || e instanceof Animal || e instanceof AbstractVillager) return true;
        if (e instanceof LivingEntity || e instanceof ItemEntity || e instanceof Projectile || e instanceof ExperienceOrb) return false;
        // boats, minecarts, modded tanks, cars, helicopters: big non-living entities
        return e.getBbWidth() >= 0.9f && e.getBbHeight() >= 0.5f;
    }

    private static void lookAtCreatures(LocalPlayer p, ClientLevel level) {
        String dim = dimKey();
        long now = System.currentTimeMillis();
        for (Entity e : level.entitiesForRendering()) {
            if (e == p || !notable(e) || e.distanceTo(p) > 64 || !Info.perceives(e)) continue;
            String type = BuiltInRegistries.ENTITY_TYPE.getKey(e.getType()).toString();
            String name = e.getName().getString();
            BlockPos pos = e.blockPosition();
            boolean player = e instanceof Player;
            // one entry per player, or per kind of creature/vehicle in an area
            SIGHTINGS.removeIf(s -> s.type().equals(type) && (player ? s.name().equals(name)
                    : s.dim().equals(dim) && s.pos().distSqr(pos) < 24 * 24));
            SIGHTINGS.add(new Sighting(dim, type, name, pos, now));
            dirtyExtra = true;
        }
        if (SIGHTINGS.size() > MAX_SIGHTINGS) {
            SIGHTINGS.sort(Comparator.comparingLong(Sighting::time));
            SIGHTINGS.subList(0, SIGHTINGS.size() - MAX_SIGHTINGS).clear();
        }
    }

    /** Where the bot last saw players/animals/villagers/vehicles matching the query (empty = everything). */
    public static String recallEntities(String query, int limit) {
        String q = norm(query);
        BlockPos me = Bot.player().blockPosition();
        String dim = dimKey();
        List<Sighting> found = new ArrayList<>();
        for (Sighting s : SIGHTINGS) {
            if (q.isEmpty() || matches(s.type(), q) || norm(s.name()).contains(q)) found.add(s);
        }
        if (found.isEmpty()) return q.isEmpty() ? "пока никого не встречал" : "не помню, чтобы видел «" + query + "»";
        found.sort(Comparator.comparingLong(s -> -s.time()));   // the freshest first
        StringBuilder sb = new StringBuilder();
        for (Sighting s : found.subList(0, Math.min(limit, found.size()))) {
            sb.append("- ").append(s.name()).append(" ").append(Bot.pos(s.pos()));
            if (s.dim().equals(dim)) sb.append(" (").append(Math.round(Math.sqrt(s.pos().distSqr(me)))).append(" бл.)");
            else sb.append(" (").append(s.dim()).append(")");
            sb.append(", видел ").append(ago(s.time())).append('\n');
        }
        return sb.toString().trim();
    }

    /** How much the bot remembers in this world. */
    public static String summary() {
        int stashes = STASHES.values().stream().mapToInt(Map::size).sum();
        int blocks = DIMS.values().stream().mapToInt(Map::size).sum();
        return "Помню в этом мире: " + blocks + " увиденных блоков (руды, деревья, сундуки, машины), "
                + stashes + " сундуков/машин с содержимым, " + SIGHTINGS.size() + " встреч с игроками, животными и техникой.";
    }

    private static String norm(String s) {
        String q = s == null ? "" : s.trim().toLowerCase(Locale.ROOT).replace('ё', 'е');
        return q.equals("все") || q.equals("всех") || q.equals("all") || q.equals("*") ? "" : q;
    }

    private static boolean matches(String id, String q) {
        if (norm(id).contains(q)) return true;
        ResourceLocation rl = ResourceLocation.tryParse(id);
        if (rl == null) return false;
        String name = BuiltInRegistries.ITEM.containsKey(rl) ? itemName(id)
                : BuiltInRegistries.ENTITY_TYPE.containsKey(rl) ? BuiltInRegistries.ENTITY_TYPE.get(rl).getDescription().getString() : "";
        return norm(name).contains(q);
    }

    private static String itemName(String id) {
        ResourceLocation rl = ResourceLocation.tryParse(id);
        if (rl == null || !BuiltInRegistries.ITEM.containsKey(rl)) return id;
        return new ItemStack(BuiltInRegistries.ITEM.get(rl)).getHoverName().getString();
    }

    private static String blockName(String id) {
        ResourceLocation rl = ResourceLocation.tryParse(id);
        if (rl == null || !BuiltInRegistries.BLOCK.containsKey(rl)) return id;
        return BuiltInRegistries.BLOCK.get(rl).getName().getString();
    }

    private static String ago(long time) {
        long min = Math.max(0, (System.currentTimeMillis() - time) / 60000);
        if (min < 1) return "только что";
        if (min < 60) return min + " мин назад";
        if (min < 60 * 24) return (min / 60) + " ч назад";
        return (min / 60 / 24) + " дн назад";
    }

    // ------------------------------------------------------------------ persistence

    private static Path folder() {
        return Minecraft.getInstance().gameDirectory.toPath().resolve("altron_memory").resolve(worldKey);
    }

    /** Called on joining a world: memory is kept per world (its save folder name when known, else server + spawn). */
    public static void onJoin() {
        Minecraft mc = Minecraft.getInstance();
        String addr = mc.getCurrentServer() != null ? mc.getCurrentServer().ip : "local";
        BlockPos spawn = mc.level.getSharedSpawnPos();
        String world = BotClient.worldName;
        String key = world.isBlank() ? addr + "_" + spawn.getX() + "_" + spawn.getZ()
                : "world_" + world + "_" + Integer.toHexString(world.hashCode());
        // Cyrillic world names become underscores; the hash keeps such names apart
        key = key.replaceAll("[^A-Za-z0-9_.-]", "_");
        if (key.equals(worldKey)) return;
        save();
        worldKey = key;
        DIMS.clear();
        STASHES.clear();
        SIGHTINGS.clear();
        opened = null;
        load();
    }

    public static void save() {
        if (worldKey.isEmpty()) return;
        try {
            Path dir = folder();
            Files.createDirectories(dir);
            if (dirty) {
                for (Map.Entry<String, Long2ObjectOpenHashMap<Block>> d : DIMS.entrySet()) {
                    Path file = dir.resolve(d.getKey().replace(':', '@') + ".txt");
                    try (BufferedWriter w = Files.newBufferedWriter(file, StandardCharsets.UTF_8)) {
                        for (Long2ObjectMap.Entry<Block> e : d.getValue().long2ObjectEntrySet()) {
                            BlockPos bp = BlockPos.of(e.getLongKey());
                            w.write(bp.getX() + " " + bp.getY() + " " + bp.getZ() + " " + BuiltInRegistries.BLOCK.getKey(e.getValue()));
                            w.newLine();
                        }
                    }
                }
                dirty = false;
            }
            if (dirtyExtra) {
                // S dim x y z block time item=count,...   /   E dim type x y z time name
                try (BufferedWriter w = Files.newBufferedWriter(dir.resolve("extra.dat"), StandardCharsets.UTF_8)) {
                    for (Map.Entry<String, Long2ObjectOpenHashMap<Stash>> d : STASHES.entrySet()) {
                        for (Long2ObjectMap.Entry<Stash> e : d.getValue().long2ObjectEntrySet()) {
                            BlockPos bp = BlockPos.of(e.getLongKey());
                            Stash st = e.getValue();
                            List<String> items = new ArrayList<>();
                            st.items().forEach((id, n) -> items.add(id + "=" + n));
                            w.write("S " + d.getKey() + " " + bp.getX() + " " + bp.getY() + " " + bp.getZ() + " " + st.block()
                                    + " " + st.time() + " " + String.join(",", items));
                            w.newLine();
                        }
                    }
                    for (Sighting s : SIGHTINGS) {
                        w.write("E " + s.dim() + " " + s.type() + " " + s.pos().getX() + " " + s.pos().getY() + " " + s.pos().getZ()
                                + " " + s.time() + " " + s.name());
                        w.newLine();
                    }
                }
                dirtyExtra = false;
            }
        } catch (IOException e) {
            AltronMod.LOG.warn("[Altron] could not save memory: {}", e.toString());
        }
    }

    private static void load() {
        Path dir = folder();
        if (!Files.isDirectory(dir)) return;
        int n = 0;
        try (var files = Files.list(dir)) {
            for (Path file : (Iterable<Path>) files::iterator) {
                String name = file.getFileName().toString();
                if (!name.endsWith(".txt")) continue;
                String dimKey = name.substring(0, name.length() - 4).replace('@', ':');
                Long2ObjectOpenHashMap<Block> m = DIMS.computeIfAbsent(dimKey, k -> new Long2ObjectOpenHashMap<>());
                for (String line : Files.readAllLines(file, StandardCharsets.UTF_8)) {
                    String[] parts = line.trim().split(" ");
                    if (parts.length != 4) continue;
                    ResourceLocation id = ResourceLocation.tryParse(parts[3]);
                    if (id == null || !BuiltInRegistries.BLOCK.containsKey(id)) continue;
                    try {
                        BlockPos bp = new BlockPos(Integer.parseInt(parts[0]), Integer.parseInt(parts[1]), Integer.parseInt(parts[2]));
                        m.put(bp.asLong(), BuiltInRegistries.BLOCK.get(id));
                        n++;
                    } catch (NumberFormatException ignored) {
                    }
                }
            }
        } catch (IOException e) {
            AltronMod.LOG.warn("[Altron] could not load memory: {}", e.toString());
        }
        Path extra = dir.resolve("extra.dat");
        if (Files.exists(extra)) {
            try {
                for (String line : Files.readAllLines(extra, StandardCharsets.UTF_8)) {
                    String[] f = line.split(" ", 8);
                    try {
                        if (f[0].equals("S") && f.length >= 7) {
                            Map<String, Integer> items = new TreeMap<>();
                            if (f.length == 8 && !f[7].isBlank()) {
                                for (String kv : f[7].split(",")) {
                                    int eq = kv.lastIndexOf('=');
                                    if (eq > 0) items.put(kv.substring(0, eq), Integer.parseInt(kv.substring(eq + 1)));
                                }
                            }
                            BlockPos bp = new BlockPos(Integer.parseInt(f[2]), Integer.parseInt(f[3]), Integer.parseInt(f[4]));
                            STASHES.computeIfAbsent(f[1], k -> new Long2ObjectOpenHashMap<>())
                                    .put(bp.asLong(), new Stash(f[5], items, Long.parseLong(f[6])));
                        } else if (f[0].equals("E") && f.length == 8) {
                            BlockPos bp = new BlockPos(Integer.parseInt(f[3]), Integer.parseInt(f[4]), Integer.parseInt(f[5]));
                            SIGHTINGS.add(new Sighting(f[1], f[2], f[7], bp, Long.parseLong(f[6])));
                        }
                    } catch (RuntimeException ignored) {
                        // a damaged line: skip it
                    }
                }
            } catch (IOException e) {
                AltronMod.LOG.warn("[Altron] could not load memory extras: {}", e.toString());
            }
        }
        AltronMod.LOG.info("[Altron] remembered {} blocks, {} containers, {} sightings for {}", n,
                STASHES.values().stream().mapToInt(Map::size).sum(), SIGHTINGS.size(), worldKey);
    }
}
