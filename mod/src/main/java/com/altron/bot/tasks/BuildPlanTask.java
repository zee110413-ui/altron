package com.altron.bot.tasks;

import com.altron.bot.Baritone;
import com.altron.bot.Bot;
import com.altron.bot.Inv;
import com.altron.bot.MultiblockCompat;
import com.altron.bot.Task;
import net.minecraft.core.BlockPos;
import net.minecraft.world.item.Item;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.levelgen.structure.templatesystem.StructureTemplate;

import java.io.File;
import java.util.List;
import java.util.Map;

/**
 * Build any plan of blocks the brain drew up (a house, a wall, a tower, a bridge...): check the materials, find a free
 * spot near the bot where the whole box of the building is empty (nothing of the players' gets broken), and let
 * Baritone place the blocks, as for a mod's multiblock.
 */
public class BuildPlanTask extends Task {
    private final List<StructureTemplate.StructureBlockInfo> blocks;
    private final String what;
    private BlockPos origin;
    private int phase;
    private int idle;
    private int stale;
    private int lastWrong = Integer.MAX_VALUE;

    public BuildPlanTask(List<StructureTemplate.StructureBlockInfo> blocks, BlockPos origin, String what) {
        super("build_plan");
        this.blocks = blocks;
        this.origin = origin;
        this.what = what;
    }

    /** The whole box of the plan (its empty rooms too) must be air or plants: Baritone clears the air of a plan. */
    private boolean boxFree(BlockPos at) {
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
        // standing on something: no building in the air or over water
        for (BlockPos p : BlockPos.betweenClosed(at.offset(0, -1, 0), at.offset(w, -1, l))) {
            if (!Bot.level().getBlockState(p).isSolidRender(Bot.level(), p)) return false;
        }
        return true;
    }

    @Override
    protected Status run() {
        var p = p();
        if (phase == 0) {
            if (blocks.isEmpty()) return fail("пустой план постройки");
            StringBuilder miss = new StringBuilder();
            for (Map.Entry<Item, Integer> e : MultiblockCompat.materials(blocks).entrySet()) {
                int have = Inv.count(p, s -> s.is(e.getKey()));
                if (have < e.getValue()) miss.append(miss.length() > 0 ? ", " : "").append(e.getValue() - have).append("x ").append(Bot.id(e.getKey()));
            }
            if (miss.length() > 0) return fail("для постройки " + what + " не хватает: " + miss + " (добудь — obtain — и строй снова)");
            if (origin == null) {
                BlockPos me = p.blockPosition();
                search:
                for (int r = 2; r <= 16; r += 2) {
                    for (int dx = -r; dx <= r; dx += 2) {
                        for (int dz = -r; dz <= r; dz += 2) {
                            for (int dy = -2; dy <= 2; dy++) {
                                BlockPos c = me.offset(dx, dy, dz);
                                if (boxFree(c)) {
                                    origin = c;
                                    break search;
                                }
                            }
                        }
                    }
                }
                if (origin == null) return fail("рядом нет ровного свободного места под " + what + " — отведи меня на поляну");
            } else if (!boxFree(origin)) {
                return fail("место в " + Bot.pos(origin) + " занято или неровное — выбери другое");
            }
            try {
                MultiblockCompat.writeSchematic(blocks, new File(Bot.mc().gameDirectory, "schematics/altron_plan.schem"));
            } catch (Exception e) {
                return fail("не смог записать план: " + e);
            }
            Baritone.setAllowPlace(true);
            if (!Baritone.command("build altron_plan.schem " + origin.getX() + " " + origin.getY() + " " + origin.getZ())) {
                Baritone.setAllowPlace(false);
                return fail("Baritone не принял постройку");
            }
            phase = 1;
            return Status.RUNNING;
        }
        // no progress for a minute: stop and say how much is left
        if (age % 100 == 0) {
            int wrong = MultiblockCompat.wrongBlocks(blocks, origin).size();
            if (wrong < lastWrong) {
                lastWrong = wrong;
                stale = 0;
            } else if (++stale >= 12) {
                Baritone.cancel();
                Baritone.setAllowPlace(false);
                return fail("стройка встала: " + wrong + " блоков не на месте");
            }
        }
        if (age > 60 && !Baritone.busy()) {
            if (++idle > 40) {
                Baritone.setAllowPlace(false);
                int wrong = MultiblockCompat.wrongBlocks(blocks, origin).size();
                return wrong == 0 ? done("построил " + what + " в " + Bot.pos(origin))
                        : fail("не достроил " + what + ": " + wrong + " блоков не на месте");
            }
        } else {
            idle = 0;
        }
        if (age > 20 * 60 * 30) {
            Baritone.cancel();
            Baritone.setAllowPlace(false);
            return fail("строил слишком долго");
        }
        return Status.RUNNING;
    }

    @Override
    public void stop() {
        if (phase == 1) {
            Baritone.cancel();
            Baritone.setAllowPlace(false);
        }
    }

    @Override
    public String progress() {
        return phase == 0 ? "ищу место" : "строю " + what;
    }
}
