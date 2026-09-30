package com.altron.bot;

import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.tags.BlockTags;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.FallingBlock;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraftforge.common.Tags;

/**
 * Getting about, for every task: Altron's own legs (Legs) walk, and a few rules of a guest in someone's world — what
 * is natural ground he may dig through, and what he may put under his feet. No outside pathfinding mod.
 */
public final class Nav {
    private Nav() {
    }

    /** Walk to within {@code range} blocks of pos. */
    public static boolean gotoNear(BlockPos pos, int range) {
        return Legs.gotoNear(pos, range);
    }

    /** Walk to a column x,z (any height: over hills, into valleys). */
    public static boolean gotoXZ(int x, int z) {
        return Legs.gotoXZ(x, z);
    }

    /** Follow a player, staying about three blocks behind. */
    public static boolean follow(String player) {
        return Legs.follow(player, 3);
    }

    /** Going somewhere or following someone. */
    public static boolean busy() {
        return Legs.busy();
    }

    /** Really on the way (not standing next to the one he follows). */
    public static boolean pathing() {
        return Legs.pathing();
    }

    public static void cancel() {
        Legs.cancel();
    }

    /**
     * Natural ground a miner may dig through: stone, dirt, sand, gravel, ores, snow, ice, tree leaves, plants.
     * Not logs: a log may be the wall of a house (MineTask cuts only logs that stand in a tree). Nothing with contents.
     */
    public static boolean natural(Block b) {
        BlockState st = b.defaultBlockState();
        if (st.hasBlockEntity()) return false;
        if (st.is(BlockTags.BASE_STONE_OVERWORLD) || st.is(BlockTags.BASE_STONE_NETHER) || st.is(BlockTags.DIRT)
                || st.is(BlockTags.SAND) || st.is(BlockTags.LEAVES) || st.is(BlockTags.SNOW) || st.is(BlockTags.ICE)
                || st.is(Tags.Blocks.ORES) || st.is(Tags.Blocks.GRAVEL) || st.is(BlockTags.REPLACEABLE)
                || st.is(BlockTags.SMALL_FLOWERS)) {
            return true;
        }
        String path = BuiltInRegistries.BLOCK.getKey(b).getPath();
        return path.equals("clay") || path.equals("moss_block") || path.equals("end_stone") || path.equals("calcite")
                || path.equals("dripstone_block") || path.equals("magma_block") || path.equals("soul_sand") || path.equals("soul_soil")
                || path.equals("sandstone") || path.equals("red_sandstone") || path.equals("mud");
    }

    /** A block he may put under his feet to climb (a pillar) and take back later: plain natural ground that stays put. */
    public static boolean filler(Block b) {
        BlockState st = b.defaultBlockState();
        return natural(b) && !(b instanceof FallingBlock) && !st.is(Tags.Blocks.ORES) && !st.is(BlockTags.LEAVES)
                && !st.is(BlockTags.REPLACEABLE) && !st.is(BlockTags.ICE) && st.isCollisionShapeFullBlock(Bot.level(), BlockPos.ZERO);
    }

    /** Lava next to this block: digging it out would let the lava in. */
    public static boolean nearLava(BlockPos pos) {
        for (var d : net.minecraft.core.Direction.values()) {
            if (Bot.level().getFluidState(pos.relative(d)).is(net.minecraft.tags.FluidTags.LAVA)) return true;
        }
        return Bot.level().getFluidState(pos).is(net.minecraft.tags.FluidTags.LAVA);
    }

    /**
     * Y level to branch-mine at: ores at their usual depth (1.18+ world generation), everything else (stone, clay...)
     * right where he is, instead of digging a shaft down.
     */
    public static int mineLevel(String blockPath, int currentY) {
        if (!blockPath.contains("ore") && !blockPath.contains("debris")) {
            boolean rock = blockPath.endsWith("stone") || blockPath.contains("andesite") || blockPath.contains("diorite")
                    || blockPath.contains("granite") || blockPath.contains("deepslate") || blockPath.contains("tuff");
            return rock ? currentY - 3 : currentY;   // rock: just under the soil
        }
        if (blockPath.contains("diamond") || blockPath.contains("redstone")) return -58;
        if (blockPath.contains("gold")) return -16;
        if (blockPath.contains("lapis")) return 0;
        if (blockPath.contains("copper")) return 48;
        if (blockPath.contains("coal")) return Math.max(0, Math.min(currentY - 8, 96));
        if (blockPath.contains("emerald")) return 120;
        if (blockPath.contains("debris")) return 15;
        return 16;   // iron and modded ores
    }
}
