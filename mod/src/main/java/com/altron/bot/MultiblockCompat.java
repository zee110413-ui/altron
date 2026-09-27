package com.altron.bot;

import net.minecraft.SharedConstants;
import net.minecraft.commands.arguments.blocks.BlockStateParser;
import net.minecraft.core.BlockPos;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.nbt.NbtIo;
import net.minecraft.network.chat.Component;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.levelgen.structure.templatesystem.StructureTemplate;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Immersive Engineering multiblocks (also used by Immersive Petroleum: pumpjack, distillation tower...).
 * Reads the real blueprint from the mod, writes it as a schematic for Baritone to build.
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

    /** Write a Sponge (.schem v2) schematic that Baritone's "build" command understands. */
    public static void writeSchematic(List<StructureTemplate.StructureBlockInfo> blocks, File file) throws Exception {
        int w = 1, h = 1, l = 1;
        for (StructureTemplate.StructureBlockInfo b : blocks) {
            w = Math.max(w, b.pos().getX() + 1);
            h = Math.max(h, b.pos().getY() + 1);
            l = Math.max(l, b.pos().getZ() + 1);
        }
        Map<String, Integer> palette = new HashMap<>();
        palette.put("minecraft:air", 0);
        int[] data = new int[w * h * l];
        for (StructureTemplate.StructureBlockInfo b : blocks) {
            String key = BlockStateParser.serialize(b.state());
            int id = palette.computeIfAbsent(key, k -> palette.size());
            BlockPos p = b.pos();
            data[p.getX() + p.getZ() * w + p.getY() * w * l] = id;
        }
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        for (int v : data) {
            while ((v & ~0x7F) != 0) {
                bytes.write((v & 0x7F) | 0x80);
                v >>>= 7;
            }
            bytes.write(v);
        }
        CompoundTag root = new CompoundTag();
        root.putInt("Version", 2);
        root.putInt("DataVersion", SharedConstants.getCurrentVersion().getDataVersion().getVersion());
        root.putShort("Width", (short) w);
        root.putShort("Height", (short) h);
        root.putShort("Length", (short) l);
        CompoundTag pal = new CompoundTag();
        palette.forEach(pal::putInt);
        root.put("Palette", pal);
        root.putInt("PaletteMax", palette.size());
        root.putByteArray("BlockData", bytes.toByteArray());
        file.getParentFile().mkdirs();
        NbtIo.writeCompressed(root, file);
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
