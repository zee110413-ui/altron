package com.altron.bot;

import com.altron.AltronMod;
import net.minecraft.core.BlockPos;

import java.lang.reflect.Method;
import java.util.HashMap;
import java.util.Map;
import java.util.Optional;

/** Baritone (pathfinding/mining) accessed by reflection so the mod works without it. */
public final class Baritone {
    private static Object primary;
    private static boolean missing;
    private static final Map<String, Method> METHODS = new HashMap<>();

    private Baritone() {
    }

    private static Object get() {
        if (primary != null || missing) return primary;
        try {
            Class<?> api = Class.forName("baritone.api.BaritoneAPI");
            Object provider = api.getMethod("getProvider").invoke(null);
            primary = m("baritone.api.IBaritoneProvider", "getPrimaryBaritone").invoke(provider);
        } catch (Throwable t) {
            missing = true;
            AltronMod.LOG.warn("[Altron] Baritone not available: {}", t.toString());
        }
        return primary;
    }

    private static Method m(String cls, String name, Class<?>... types) throws Exception {
        String key = cls + "#" + name;
        Method method = METHODS.get(key);
        if (method == null) {
            method = Class.forName(cls).getMethod(name, types);
            METHODS.put(key, method);
        }
        return method;
    }

    public static boolean available() {
        return get() != null;
    }

    /** Run a Baritone chat command without the # prefix, e.g. "mine diamond_ore". */
    public static boolean command(String cmd) {
        Object b = get();
        if (b == null) return false;
        try {
            Object cm = m("baritone.api.IBaritone", "getCommandManager").invoke(b);
            Object r = m("baritone.api.command.manager.ICommandManager", "execute", String.class).invoke(cm, cmd);
            return !(r instanceof Boolean ok) || ok;
        } catch (Throwable t) {
            AltronMod.LOG.warn("[Altron] baritone command '{}' failed: {}", cmd, t.toString());
            return false;
        }
    }

    public static void cancel() {
        Object b = get();
        if (b == null) return;
        try {
            Object pb = m("baritone.api.IBaritone", "getPathingBehavior").invoke(b);
            m("baritone.api.behavior.IPathingBehavior", "cancelEverything").invoke(pb);
        } catch (Throwable t) {
            command("stop");
        }
    }

    /** True while Baritone is pathing or any of its processes is in control. */
    public static boolean busy() {
        Object b = get();
        if (b == null) return false;
        try {
            Object pb = m("baritone.api.IBaritone", "getPathingBehavior").invoke(b);
            boolean pathing = (Boolean) m("baritone.api.behavior.IPathingBehavior", "isPathing").invoke(pb);
            if (pathing) return true;
            Object pcm = m("baritone.api.IBaritone", "getPathingControlManager").invoke(b);
            Optional<?> p = (Optional<?>) m("baritone.api.pathing.calc.IPathingControlManager", "mostRecentInControl").invoke(pcm);
            return p.isPresent();
        } catch (Throwable t) {
            return false;
        }
    }

    /** True while Baritone is actually walking a path (not just "following, standing next to the player"). */
    public static boolean pathing() {
        Object b = get();
        if (b == null) return false;
        try {
            Object pb = m("baritone.api.IBaritone", "getPathingBehavior").invoke(b);
            return (Boolean) m("baritone.api.behavior.IPathingBehavior", "isPathing").invoke(pb);
        } catch (Throwable t) {
            return false;
        }
    }

    /** Walk to within {@code range} blocks of pos (digging/bridging as needed). */
    public static boolean gotoNear(BlockPos pos, int range) {
        Object b = get();
        if (b == null) return false;
        try {
            Class<?> goalCls = Class.forName("baritone.api.pathing.goals.Goal");
            Object goal = Class.forName("baritone.api.pathing.goals.GoalNear")
                    .getConstructor(BlockPos.class, int.class).newInstance(pos, range);
            Object cgp = m("baritone.api.IBaritone", "getCustomGoalProcess").invoke(b);
            m("baritone.api.process.ICustomGoalProcess", "setGoalAndPath", goalCls).invoke(cgp, goal);
            return true;
        } catch (Throwable t) {
            AltronMod.LOG.warn("[Altron] baritone goto failed: {}", t.toString());
            return command("goto " + pos.getX() + " " + pos.getY() + " " + pos.getZ());
        }
    }

    /** Walk to a column x,z (any height: over hills, into valleys). */
    public static boolean gotoXZ(int x, int z) {
        Object b = get();
        if (b == null) return false;
        try {
            Class<?> goalCls = Class.forName("baritone.api.pathing.goals.Goal");
            Object goal = Class.forName("baritone.api.pathing.goals.GoalXZ").getConstructor(int.class, int.class).newInstance(x, z);
            Object cgp = m("baritone.api.IBaritone", "getCustomGoalProcess").invoke(b);
            m("baritone.api.process.ICustomGoalProcess", "setGoalAndPath", goalCls).invoke(cgp, goal);
            return true;
        } catch (Throwable t) {
            return command("goto " + x + " " + z);
        }
    }

    public static void setup() {
        if (!available()) return;
        String[] settings = {
                "set chatDebug false",
                "set allowSprint true",
                "set allowInventory true",
                "set followRadius 3",
                "set mineScanDroppedItems true",
                // Fair play: mine only ores the bot can actually see, branch-mining like a player
                "set legitMine true",
                "set legitMineIncludeDiagonals true",
                // Don't litter the world with cobblestone pillars and bridges while walking or digging
                "set allowPlace false",
                // A guest does not dig through the walls of someone's base: walking only walks (doors, stairs,
                // around); breaking is switched on only while mining or building on purpose (setAllowBreak)
                "set allowBreak false",
        };
        for (String s : settings) command(s);
        protectBlocks();
    }

    /**
     * Blocks Baritone must not break even while mining: everything that is not natural ground (planks, bricks,
     * glass, concrete, mod blocks, roads) and everything with contents, doors, ladders. A set behind a list:
     * Baritone asks contains() for every block of every path, with thousands of kinds that must stay quick.
     * The blocks the current job is after (the ore it mines) are let through.
     */
    static final class Protected extends java.util.AbstractList<net.minecraft.world.level.block.Block> {
        private final java.util.Set<net.minecraft.world.level.block.Block> all = new java.util.HashSet<>();
        private java.util.Set<net.minecraft.world.level.block.Block> allowed = java.util.Set.of();
        private java.util.List<net.minecraft.world.level.block.Block> view = new java.util.ArrayList<>();

        @Override
        public net.minecraft.world.level.block.Block get(int i) {
            return view.get(i);
        }

        @Override
        public int size() {
            return view.size();
        }

        @Override
        public boolean contains(Object o) {
            return all.contains(o) && !allowed.contains(o);
        }

        void allow(java.util.Set<net.minecraft.world.level.block.Block> targets) {
            allowed = java.util.Set.copyOf(targets);
            java.util.List<net.minecraft.world.level.block.Block> v = new java.util.ArrayList<>();
            for (var b : all) if (!allowed.contains(b)) v.add(b);
            view = v;
        }
    }

    private static final Protected PROTECTED = new Protected();

    private static java.util.List<net.minecraft.world.item.Item> defaultThrowaway;

    /**
     * Blocks he may put under his feet to climb out of a hole: Baritone's usual (dirt, cobblestone...) plus the cheap
     * natural ones he carries (sand, gravel...). An empty list puts the usual ones back.
     */
    @SuppressWarnings("unchecked")
    public static void setThrowaway(java.util.List<net.minecraft.world.item.Item> extra) {
        try {
            Object settings = Class.forName("baritone.api.BaritoneAPI").getMethod("getSettings").invoke(null);
            Object setting = settings.getClass().getField("acceptableThrowawayItems").get(settings);
            java.lang.reflect.Field value = setting.getClass().getField("value");
            if (defaultThrowaway == null) {
                defaultThrowaway = new java.util.ArrayList<>((java.util.List<net.minecraft.world.item.Item>) value.get(setting));
            }
            java.util.List<net.minecraft.world.item.Item> list = new java.util.ArrayList<>(defaultThrowaway);
            for (var i : extra) if (!list.contains(i)) list.add(i);
            value.set(setting, list);
        } catch (Throwable t) {
            AltronMod.LOG.warn("[Altron] could not change Baritone's throwaway blocks: {}", t.toString());
        }
    }

    /**
     * Natural ground a miner may dig through: stone, dirt, sand, gravel, ores, snow, ice, tree leaves, plants.
     * Not logs: a log may be the wall of a house (MineTask cuts only logs that stand in a tree).
     */
    public static boolean natural(net.minecraft.world.level.block.Block b) {
        var st = b.defaultBlockState();
        if (st.hasBlockEntity()) return false;
        if (st.is(net.minecraft.tags.BlockTags.BASE_STONE_OVERWORLD) || st.is(net.minecraft.tags.BlockTags.BASE_STONE_NETHER)
                || st.is(net.minecraft.tags.BlockTags.DIRT) || st.is(net.minecraft.tags.BlockTags.SAND)
                || st.is(net.minecraft.tags.BlockTags.LEAVES) || st.is(net.minecraft.tags.BlockTags.SNOW)
                || st.is(net.minecraft.tags.BlockTags.ICE) || st.is(net.minecraftforge.common.Tags.Blocks.ORES)
                || st.is(net.minecraftforge.common.Tags.Blocks.GRAVEL) || st.is(net.minecraft.tags.BlockTags.REPLACEABLE)
                || st.is(net.minecraft.tags.BlockTags.SMALL_FLOWERS)) {
            return true;
        }
        String path = net.minecraft.core.registries.BuiltInRegistries.BLOCK.getKey(b).getPath();
        return path.equals("clay") || path.equals("moss_block") || path.equals("end_stone") || path.equals("calcite")
                || path.equals("dripstone_block") || path.equals("magma_block") || path.equals("soul_sand") || path.equals("soul_soil")
                || path.equals("sandstone") || path.equals("red_sandstone") || path.equals("mud");
    }

    /**
     * Never break on the way what a guest must not break. Asked on purpose (break_block, transport_block)
     * the bot still breaks a block with its own hands.
     */
    @SuppressWarnings("unchecked")
    private static void protectBlocks() {
        try {
            Object settings = Class.forName("baritone.api.BaritoneAPI").getMethod("getSettings").invoke(null);
            Object setting = settings.getClass().getField("blocksToDisallowBreaking").get(settings);
            java.lang.reflect.Field value = setting.getClass().getField("value");
            PROTECTED.all.addAll((java.util.List<net.minecraft.world.level.block.Block>) value.get(setting));
            for (net.minecraft.world.level.block.Block b : net.minecraft.core.registries.BuiltInRegistries.BLOCK) {
                var st = b.defaultBlockState();
                if (st.isAir() || !st.getFluidState().isEmpty()) continue;
                if (!natural(b)) PROTECTED.all.add(b);
            }
            PROTECTED.allow(java.util.Set.of());
            value.set(setting, PROTECTED);
            AltronMod.LOG.info("[Altron] Baritone will not break {} kinds of blocks (everything but natural ground)", PROTECTED.all.size());
        } catch (Throwable t) {
            AltronMod.LOG.warn("[Altron] could not protect blocks from Baritone: {}", t.toString());
        }
    }

    /**
     * Breaking on the way only while mining or building on purpose; `targets` are the blocks that job is after
     * (they may be broken even when they are not natural ground, e.g. obsidian or glass the commander asked for).
     */
    public static void setAllowBreak(boolean allow, java.util.Set<net.minecraft.world.level.block.Block> targets) {
        PROTECTED.allow(allow && targets != null ? targets : java.util.Set.of());
        command("set allowBreak " + allow);
    }

    /** Block placing (and clearing the natural ground of the site) only while building something on purpose (multiblock blueprints). */
    public static void setAllowPlace(boolean allow) {
        command("set allowPlace " + allow);
        setAllowBreak(allow, java.util.Set.of());
    }

    /**
     * Y level to branch-mine at in legit mode: ores at their usual depth (1.18+ world generation),
     * everything else (stone, logs, sand, clay...) right where the bot is, instead of digging a shaft down.
     */
    public static void setMineLevel(String blockPath, int currentY) {
        int y;
        if (!blockPath.contains("ore") && !blockPath.contains("debris")) {
            boolean rock = blockPath.endsWith("stone") || blockPath.contains("andesite") || blockPath.contains("diorite")
                    || blockPath.contains("granite") || blockPath.contains("deepslate") || blockPath.contains("tuff");
            y = rock ? currentY - 3 : currentY;   // rock: just under the soil
        } else if (blockPath.contains("diamond") || blockPath.contains("redstone")) y = -58;
        else if (blockPath.contains("gold")) y = -16;
        else if (blockPath.contains("lapis")) y = 0;
        else if (blockPath.contains("copper")) y = 48;
        else if (blockPath.contains("coal")) y = Math.max(0, Math.min(currentY - 8, 96));
        else if (blockPath.contains("emerald")) y = 120;
        else y = 16;   // iron and modded ores
        command("set legitMineYLevel " + y);
    }

    /** Raw commands that would reveal blocks through walls are not allowed. */
    public static String checkFair(String cmd) {
        String[] parts = cmd.trim().toLowerCase().split("\\s+");
        if (parts.length == 0) return null;
        String c = parts[0];
        if (c.equals("find") || c.equals("mine") || c.equals("legitmine") || c.equals("set")) {
            return "эта команда недоступна (честная игра): используй инструменты mine/find_block";
        }
        if ((c.equals("goto") || c.equals("path")) && parts.length > 1 && !parts[1].matches("[-~0-9.]+")) {
            return "goto к типу блока недоступен (честная игра): укажи координаты";
        }
        return null;
    }
}
