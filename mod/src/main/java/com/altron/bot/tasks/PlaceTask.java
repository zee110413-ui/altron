package com.altron.bot.tasks;

import com.altron.bot.Nav;
import com.altron.bot.Bot;
import com.altron.bot.Inv;
import com.altron.bot.Task;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.item.Item;
import net.minecraft.world.phys.AABB;

/** Place a block from the inventory at a position (clicking a neighbouring block, sneaking). */
public class PlaceTask extends Task {
    private final Item item;
    private BlockPos pos;
    private int phase;
    private int walkTicks;
    private int wait;
    private int reposition;
    private int repoTicks;
    private int aimWait;

    /** pos may be null: then a free spot next to the bot is chosen. */
    public PlaceTask(Item item, BlockPos pos) {
        super("place_block");
        this.item = item;
        this.pos = pos;
    }

    public BlockPos placedAt() {
        return pos;
    }

    /** Air with a solid block below, two blocks away from the bot. */
    public static BlockPos freeSpotNear(BlockPos me) {
        var level = Bot.level();
        for (int dist = 2; dist <= 3; dist++) {
            for (Direction d : Direction.Plane.HORIZONTAL) {
                for (int dy = 0; dy >= -1; dy--) {
                    BlockPos c = me.relative(d, dist).above(dy);
                    if (level.getBlockState(c).canBeReplaced() && level.getBlockState(c.above()).canBeReplaced()
                            && !level.getBlockState(c.below()).canBeReplaced()) return c;
                }
            }
        }
        return null;
    }

    @Override
    protected Status run() {
        var level = Bot.level();
        if (phase == 0 && pos == null) {
            pos = freeSpotNear(p().blockPosition());
            if (pos == null) return fail("рядом нет места, чтобы поставить " + Bot.id(item));
        }
        if (phase == 0) {
            if (!level.getBlockState(pos).canBeReplaced()) return fail("место " + Bot.pos(pos) + " занято блоком " + level.getBlockState(pos).getBlock().getName().getString());
            if (Inv.find(p(), s -> s.is(item)) < 0) return fail("нет в инвентаре: " + Bot.id(item));
            phase = 1;
        }
        if (phase == 1) {
            if (repoTicks > 0) {   // stepping aside to see the support block
                if (--repoTicks > 0 && (repoTicks > 180 || Nav.busy())) return Status.RUNNING;
                repoTicks = 0;
            }
            double d = Bot.eyeDistTo(pos);
            boolean inside = p().getBoundingBox().intersects(new AABB(pos));
            if (d > 4.2 || inside) {
                if (walkTicks == 0 || (walkTicks % 40 == 0 && !Nav.busy())) Nav.gotoNear(pos.relative(Direction.NORTH, inside ? 2 : 0), 2);
                if (++walkTicks > 20 * 120) return fail("не смог подойти к " + Bot.pos(pos));
                return Status.RUNNING;
            }
            Nav.cancel();
            Bot.closeContainer();
            Inv.holdMatching(p(), s -> s.is(item));
            Bot.mc().options.keyShift.setDown(true); // don't open chests/machines we click on
            phase = 2;
            wait = 2;
            return Status.RUNNING;
        }
        if (phase == 2) {
            if (--wait > 0) return Status.RUNNING;
            if (!p().getMainHandItem().is(item)) {
                Bot.mc().options.keyShift.setDown(false);
                return fail("не смог взять " + Bot.id(item) + " в руку");
            }
            boolean anySupport = false;
            for (Direction d : Direction.values()) {
                BlockPos support = pos.relative(d);
                if (level.getBlockState(support).canBeReplaced()) continue;
                anySupport = true;
                Direction face = d.getOpposite();
                if (!Bot.canSeeFace(support, face)) continue;   // no placing against faces hidden behind blocks
                Bot.lookAt(Bot.hit(support, face).getLocation());
                if (!Bot.aimed(Bot.hit(support, face).getLocation(), 15) && ++aimWait < 12) return Status.RUNNING;
                Bot.mc().gameMode.useItemOn(p(), InteractionHand.MAIN_HAND, Bot.hit(support, face));
                p().swing(InteractionHand.MAIN_HAND);
                phase = 3;
                wait = 4;
                return Status.RUNNING;
            }
            Bot.mc().options.keyShift.setDown(false);
            if (anySupport && ++reposition <= 2) {
                // the support is there but out of sight: step to another side and try again
                phase = 1;
                walkTicks = 1;
                repoTicks = 200;
                Nav.gotoNear(pos, reposition == 1 ? 1 : 3);
                return Status.RUNNING;
            }
            return fail(anySupport ? "не вижу, к чему прислонить блок в " + Bot.pos(pos) : "не к чему прислонить блок в " + Bot.pos(pos));
        }
        if (--wait > 0) return Status.RUNNING;
        Bot.mc().options.keyShift.setDown(false);
        return level.getBlockState(pos).canBeReplaced() ? fail("блок не поставился в " + Bot.pos(pos))
                : done("поставил " + Bot.id(item) + " в " + Bot.pos(pos));
    }

    @Override
    public void stop() {
        Bot.mc().options.keyShift.setDown(false);
        if (phase == 1) Nav.cancel();
    }
}
