package com.altron.bot.tasks;

import com.altron.bot.Bot;
import com.altron.bot.Inv;
import com.altron.bot.MultiblockCompat;
import net.minecraft.core.BlockPos;
import net.minecraft.world.item.Item;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.levelgen.structure.templatesystem.StructureTemplate;

import java.util.List;
import java.util.Map;

/**
 * A plan of blocks the brain drew up (a house, a wall, a tower, a bridge...): what materials are missing and a free,
 * level place for it near the bot (where nothing of the players' is in the way). The building itself is done by the
 * AI's own hands, block by block.
 */
public final class BuildPlanTask {
    private BuildPlanTask() {
    }

    /** The whole box of the plan must be air or plants, and stand on solid ground. */
    static boolean boxFree(List<StructureTemplate.StructureBlockInfo> blocks, BlockPos at) {
        int w = 0, h = 0, l = 0;
        for (StructureTemplate.StructureBlockInfo b : blocks) {
            w = Math.max(w, b.pos().getX());
            h = Math.max(h, b.pos().getY());
            l = Math.max(l, b.pos().getZ());
        }
        for (BlockPos p : BlockPos.betweenClosed(at, at.offset(w, h, l))) {
            BlockState s = Bot.level().getBlockState(p);
            if (!s.isAir() && !s.canBeReplaced()) return false;
        }
        for (BlockPos p : BlockPos.betweenClosed(at.offset(0, -1, 0), at.offset(w, -1, l))) {
            if (!Bot.level().getBlockState(p).isSolidRender(Bot.level(), p)) return false;
        }
        return true;
    }

    /** "" and the corner in out[0] when it can be built; otherwise why not. */
    public static String site(List<StructureTemplate.StructureBlockInfo> blocks, BlockPos origin, BlockPos[] out) {
        if (blocks.isEmpty()) return "пустой план постройки";
        var p = Bot.player();
        StringBuilder miss = new StringBuilder();
        for (Map.Entry<Item, Integer> e : MultiblockCompat.materials(blocks).entrySet()) {
            int have = Inv.count(p, s -> s.is(e.getKey()));
            if (have < e.getValue()) miss.append(miss.length() > 0 ? ", " : "").append(e.getValue() - have).append("x ").append(Bot.id(e.getKey()));
        }
        if (miss.length() > 0) return "не хватает материала: " + miss;
        if (origin != null) {
            if (!boxFree(blocks, origin)) return "место в " + Bot.pos(origin) + " занято или неровное";
            out[0] = origin;
            return "";
        }
        BlockPos me = p.blockPosition();
        for (int r = 2; r <= 16; r += 2) {
            for (int dx = -r; dx <= r; dx += 2) {
                for (int dz = -r; dz <= r; dz += 2) {
                    for (int dy = -2; dy <= 2; dy++) {
                        BlockPos c = me.offset(dx, dy, dz);
                        if (boxFree(blocks, c)) {
                            out[0] = c;
                            return "";
                        }
                    }
                }
            }
        }
        return "рядом нет ровного свободного места";
    }
}
