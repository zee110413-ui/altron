package com.altron.bot.tasks;

import com.altron.bot.Baritone;
import com.altron.bot.Bot;
import com.altron.bot.Inv;
import com.altron.bot.Task;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.item.Item;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.Vec3;

/**
 * Walk up to a block and break it by hand, like a player holding left click.
 * Only what is in plain sight can be hit: a block behind a wall is reached by digging toward it first.
 */
public class BreakTask extends Task {
    private final BlockPos pos;
    private BlockPos current;      // the block being dug right now (the target, or something in the way)
    private int phase;
    private int walkTicks;
    private int tunnel;            // blocks dug to get a line of sight
    private int aimWait;
    private Direction face;
    private Block target;
    /** The item the block drops as (to pick it up later). */
    public Item blockItem;
    /** The block that was actually broken (null if nothing was). */
    public Block broken;

    public BreakTask(BlockPos pos) {
        super("break_block");
        this.pos = pos;
    }

    public BlockPos pos() {
        return pos;
    }

    @Override
    protected Status run() {
        if (phase == 0) {
            // Walk there first: the bot only learns what is there when it can see it up close
            if (Bot.eyeDistTo(pos) > 4.2) {
                if (walkTicks == 0) Baritone.gotoNear(pos, 2);
                if (++walkTicks > 20 * 120) return fail("не смог подойти к блоку " + Bot.pos(pos));
                if (walkTicks > 20 && walkTicks % 20 == 0 && !Baritone.busy()) Baritone.gotoNear(pos, 1);
                return Status.RUNNING;
            }
            Baritone.cancel();
            BlockState state = Bot.level().getBlockState(pos);
            if (state.isAir()) return done("там уже пусто");
            if (state.getDestroySpeed(Bot.level(), pos) < 0) return fail("этот блок неразрушим");
            if (target == null) {
                target = state.getBlock();
                blockItem = target.asItem();
            }
            // something in the way? dig through it first (never reach through walls)
            BlockPos inTheWay = Bot.obstruction(pos);
            if (inTheWay != null) {
                BlockState ws = Bot.level().getBlockState(inTheWay);
                if (++tunnel > 6 || ws.getDestroySpeed(Bot.level(), inTheWay) < 0) {
                    return fail("блок в " + Bot.pos(pos) + " за стеной, не могу до него добраться");
                }
                current = inTheWay;
            } else {
                current = pos;
            }
            Bot.closeContainer();
            Inv.holdBestTool(p(), Bot.level().getBlockState(current));
            face = Bot.seenFace(current);
            Bot.lookAt(Vec3.atCenterOf(current));
            if (!Bot.aimed(Vec3.atCenterOf(current), 15) && ++aimWait < 12) return Status.RUNNING;   // turn to it first
            aimWait = 0;
            Bot.mc().gameMode.startDestroyBlock(current, face);
            phase = 1;
            return Status.RUNNING;
        }
        BlockState now = Bot.level().getBlockState(current);
        if (current.equals(pos)) {
            if (now.getBlock() != target) {
                broken = target;
                return done("сломал " + Bot.id(target) + " в " + Bot.pos(pos));
            }
        } else if (now.isAir() || !now.getFluidState().isEmpty()) {
            phase = 0;   // the way is clear: look at the target again
            return Status.RUNNING;
        }
        if (age > 20 * 90 + walkTicks) return fail("слишком долго ломаю " + Bot.pos(current) + " (нужен инструмент получше?)");
        Bot.lookAt(Vec3.atCenterOf(current));
        Bot.mc().gameMode.continueDestroyBlock(current, face);
        p().swing(InteractionHand.MAIN_HAND);
        return Status.RUNNING;
    }

    @Override
    public void stop() {
        if (phase == 1) Bot.mc().gameMode.stopDestroyBlock();
        else Baritone.cancel();
    }

    @Override
    public void pause() {
        stop();
    }

    @Override
    public void resume() {
        phase = 0;
        walkTicks = 0;
    }
}
