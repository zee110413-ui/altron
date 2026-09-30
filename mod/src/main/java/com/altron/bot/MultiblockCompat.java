package com.altron.bot;

import net.minecraft.core.BlockPos;
import net.minecraft.network.chat.Component;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.levelgen.structure.templatesystem.StructureTemplate;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Immersive Engineering multiblocks (also used by Immersive Petroleum: pumpjack, distillation tower...).
 * Reads the real blueprint from the mod: what goes where, and whether it all stands in place.
 */
public final class MultiblockCompat {
    private MultiblockCompat() {
    }

    public static List<Object> all() {
        try {
            Class<?> h = Class.forName("blusunrize.immersiveengineering.api.multiblocks.MultiblockHandler");
            return new ArrayList<>((List<?>) h.getMethod("getMultiblocks").invoke(null));
        } catch (Throwable t) {
            return new ArrayList<>();
        }
    }

    private static Object call(Object mb, String method, Class<?>[] types, Object... args) throws Exception {
        Class<?> iface = Class.forName("blusunrize.immersiveengineering.api.multiblocks.MultiblockHandler$IMultiblock");
        return iface.getMethod(method, types).invoke(mb, args);
    }

    public static String name(Object mb) {
        try {
            Component c = (Component) call(mb, "getDisplayName", new Class<?>[0]);
            String id = String.valueOf(call(mb, "getUniqueName", new Class<?>[0]));
            return c.getString() + " (" + id + ")";
        } catch (Exception e) {
            return String.valueOf(mb);
        }
    }

    private static String norm(String s) {
        return s.toLowerCase(Locale.ROOT).replace('ё', 'е').replaceAll("[^\\p{L}\\p{N}]", "");
    }

    public static Object find(String query) {
        String q = norm(query);
        for (Object mb : all()) if (norm(name(mb)).contains(q)) return mb;
        return null;
    }

    public static String list() {
        StringBuilder sb = new StringBuilder();
        for (Object mb : all()) sb.append(sb.length() > 0 ? "; " : "").append(name(mb));
        return sb.length() == 0 ? "мод Immersive Engineering не найден" : sb.toString();
    }

    /** Non-air blocks of the blueprint, positions relative to the blueprint origin. */
    @SuppressWarnings("unchecked")
    public static List<StructureTemplate.StructureBlockInfo> structure(Object mb) throws Exception {
        List<StructureTemplate.StructureBlockInfo> out = new ArrayList<>();
        for (StructureTemplate.StructureBlockInfo info : (List<StructureTemplate.StructureBlockInfo>)
                call(mb, "getStructure", new Class<?>[]{net.minecraft.world.level.Level.class}, Bot.level())) {
            if (!info.state().isAir()) out.add(info);
        }
        return out;
    }

    public static BlockPos trigger(Object mb) throws Exception {
        return (BlockPos) call(mb, "getTriggerOffset", new Class<?>[0]);
    }

    /** Items needed (block item -> count). */
    public static Map<net.minecraft.world.item.Item, Integer> materials(List<StructureTemplate.StructureBlockInfo> blocks) {
        Map<net.minecraft.world.item.Item, Integer> need = new LinkedHashMap<>();
        for (StructureTemplate.StructureBlockInfo b : blocks) need.merge(b.state().getBlock().asItem(), 1, Integer::sum);
        return need;
    }

    /** True if every blueprint block is in place at origin (non-rotated build). */
    public static List<BlockPos> wrongBlocks(List<StructureTemplate.StructureBlockInfo> blocks, BlockPos origin) {
        List<BlockPos> wrong = new ArrayList<>();
        for (StructureTemplate.StructureBlockInfo b : blocks) {
            BlockPos at = origin.offset(b.pos());
            Block have = Bot.level().getBlockState(at).getBlock();
            if (have != b.state().getBlock()) wrong.add(at);
        }
        return wrong;
    }

    /** Is the space for the blueprint free (air, grass, flowers...)? */
    public static boolean spaceFree(List<StructureTemplate.StructureBlockInfo> blocks, BlockPos origin) {
        for (StructureTemplate.StructureBlockInfo b : blocks) {
            BlockState s = Bot.level().getBlockState(origin.offset(b.pos()));
            if (!s.canBeReplaced() && s.getBlock() != Blocks.AIR) return false;
        }
        return true;
    }
}
