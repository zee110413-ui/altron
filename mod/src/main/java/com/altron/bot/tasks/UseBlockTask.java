package com.altron.bot.tasks;

import com.altron.bot.Nav;
import com.altron.bot.Bot;
import com.altron.bot.Info;
import com.altron.bot.Input;
import com.altron.bot.Inv;
import com.altron.bot.Memory;
import com.altron.bot.Task;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.MenuProvider;
import net.minecraft.world.item.Item;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.Vec3;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

/**
 * Right-click a block: open a chest/machine/crafting table, press a button, pull a lever...
 * Optionally with an item in hand (bucket, designator, wrench, hammer), sneaking, or holding right click.
 */
public class UseBlockTask extends Task {
    private final BlockPos pos;
    private final Item item;
    private final boolean sneak;
    private final int holdTicks;
    private int phase;
    private int walkTicks;
    private int wait;
    private int reposition;
    private int repoTicks;
    private int clickedAt;
    private int aimWait;
    private BlockState before;
    private boolean changed;
    private Task walker;
    private int rewalks;
    private int patience = 30;

    public UseBlockTask(BlockPos pos) {
        this(pos, null, false, 0);
    }

    public UseBlockTask(BlockPos pos, Item item, boolean sneak, int holdTicks) {
        super("use_block");
        this.pos = pos;
        this.item = item;
        this.sneak = sneak;
        this.holdTicks = holdTicks;
    }

    @Override
    protected Status run() {
        var p = p();
        if (phase == 0) {
            if (repoTicks > 0) {   // walking to a spot with a clear view
                if (--repoTicks > 0 && (repoTicks > 180 || Nav.busy())) return Status.RUNNING;
                repoTicks = 0;
                Nav.cancel();
            }
            if (Bot.eyeDistTo(pos) > 4.2) {
                // walking there the smart way: up mod ladders, through iron doors by their buttons
                if (walker == null) walker = new GotoTask("walk", pos, 2);
                Status s = walker.tick();
                if (s == Status.RUNNING && ++walkTicks < 20 * 180) return s;
                walker.stop();
                String why = walker.result();
                walker = null;
                if (Bot.eyeDistTo(pos) > 4.2) {
                    if (++rewalks > 1) return fail("не смог подойти к " + Bot.pos(pos) + (why.isEmpty() ? "" : ": " + why));
                    return Status.RUNNING;   // one more try from where he got to
                }
            }
            Nav.cancel();
            if (Bot.level().getBlockState(pos).isAir()) return fail("в " + Bot.pos(pos) + " пусто. " + around(pos));
            if (!Bot.canSee(pos)) {
                // like a player: no clicking through walls, walk around to get a clear view
                if (++reposition > 3) return fail("не вижу " + Bot.pos(pos) + " — он за стеной, сначала нужен проход");
                Nav.gotoNear(pos, reposition == 1 ? 1 : 0);
                repoTicks = 200;
                return Status.RUNNING;
            }
            Bot.closeContainer();
            if (item != null && !Inv.holdMatching(p, s -> s.is(item))) return fail("нет в инвентаре: " + Bot.id(item));
            Bot.mc().options.keyShift.setDown(sneak);
            phase = 1;
            wait = sneak ? 3 : 1; // the server must know we sneak before the click
            return Status.RUNNING;
        }
        if (phase == 1) {
            if (--wait > 0) return Status.RUNNING;
            Direction face = Bot.seenFace(pos);
            Bot.lookAt(Vec3.atCenterOf(pos));
            if (!Bot.aimed(Vec3.atCenterOf(pos), 15) && ++aimWait < 12) return Status.RUNNING;   // look at it, then click
            Memory.opened = pos;   // a container opening now belongs to this block: remember what is inside
            before = Bot.level().getBlockState(pos);
            Bot.mc().gameMode.useItemOn(p, InteractionHand.MAIN_HAND, Bot.hit(pos, face));
            p.swing(InteractionHand.MAIN_HAND);
            if (holdTicks > 0) Input.press(Bot.mc().options.keyUse.getKey());
            phase = 2;
            wait = 0;
            clickedAt = age;
            // a chest or machine answers with its window; a busy server may take a few seconds
            patience = Bot.level().getBlockEntity(pos) instanceof MenuProvider ? 80 : 30;
            return Status.RUNNING;
        }
        // a button springs back in a second: any change while waiting counts — but only one the server confirmed
        // (the client draws a door open at once; a door that is not ours snaps back a tick later)
        BlockState cur = Bot.level().getBlockState(pos);
        if (cur != before && age - clickedAt >= 3) changed = true;
        if (changed && cur != before && age - clickedAt >= 4 && holdTicks == 0 && !(Bot.level().getBlockEntity(pos) instanceof MenuProvider)) {
            // a lever flipped, a door or a button went: no need to wait (the door of a button is open only a second)
            Bot.mc().options.keyShift.setDown(false);
            BlockState now = Bot.level().getBlockState(pos);
            return done("использовал " + now.getBlock().getName().getString() + " (" + Bot.id(now.getBlock()) + ")"
                    + (item != null ? " с " + Bot.id(item) : "") + " — состояние изменилось");
        }
        if (holdTicks > 0 && ++wait < holdTicks) return Status.RUNNING;
        if (holdTicks > 0) Input.release(Bot.mc().options.keyUse.getKey());
        Bot.mc().options.keyShift.setDown(false);
        if (p.containerMenu != p.inventoryMenu || Bot.mc().screen != null) {
            if (++wait < holdTicks + 3) return Status.RUNNING; // let the server fill the slots
            return done(p.containerMenu != p.inventoryMenu ? Info.container() : "открылось окно: " + Bot.mc().screen.getTitle().getString());
        }
        if (age - clickedAt > patience + holdTicks) {
            BlockState now = Bot.level().getBlockState(pos);
            String what = now.getBlock().getName().getString() + " (" + Bot.id(now.getBlock()) + ")" + (item != null ? " с " + Bot.id(item) : "");
            if (!changed && item == null) {
                // a lever that did not flip, a door that did not open: say so, it is not "done"
                return done("нажал ПКМ по " + what + ", но блок НЕ изменился — возможно, он заперт или чужой (SecurityCraft"
                        + " слушается только владельца и тех, кто в белом списке), или открывается только сигналом"
                        + " (железная дверь — кнопкой или рычагом рядом)");
            }
            return done("использовал " + what + (changed ? now != before ? " — состояние изменилось"
                    : " — сработал и вернулся обратно (кнопка)" : ""));
        }
        return Status.RUNNING;
    }

    /** Blocks in sight right around a spot (the model often misses the target by a block or two). */
    private static String around(BlockPos pos) {
        List<BlockPos> found = new ArrayList<>();
        for (BlockPos bp : BlockPos.betweenClosed(pos.offset(-2, -2, -2), pos.offset(2, 2, 2))) {
            BlockState st = Bot.level().getBlockState(bp);
            boolean plain = st.canBeReplaced() || st.is(Blocks.GRASS_BLOCK) || st.is(Blocks.DIRT) || st.is(Blocks.STONE)
                    || st.is(Blocks.DEEPSLATE);
            if (!plain && Bot.canSee(bp)) found.add(bp.immutable());
        }
        if (found.isEmpty()) return "Рядом с этим местом ничего заметного не вижу (используй look).";
        found.sort(Comparator.comparingDouble(bp -> bp.distSqr(pos)));
        StringBuilder sb = new StringBuilder("Рядом вижу: ");
        for (int i = 0; i < Math.min(6, found.size()); i++) {
            BlockPos bp = found.get(i);
            Block b = Bot.level().getBlockState(bp).getBlock();
            sb.append(i > 0 ? "; " : "").append(Bot.pos(bp)).append(" ").append(b.getName().getString())
                    .append(" (").append(Bot.id(b)).append(")");
        }
        return sb.toString();
    }

    @Override
    public void stop() {
        Bot.mc().options.keyShift.setDown(false);
        if (holdTicks > 0) Input.release(Bot.mc().options.keyUse.getKey());
        if (walker != null) walker.stop();
        if (phase == 0 && walkTicks > 0) Nav.cancel();
    }
}
