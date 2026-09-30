package com.altron.bot.tasks;

import com.altron.bot.Nav;
import com.altron.bot.Bot;
import com.altron.bot.Legs;
import com.altron.bot.Task;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.ButtonBlock;
import net.minecraft.world.level.block.DoorBlock;
import net.minecraft.world.level.block.LeverBlock;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.DoubleBlockHalf;
import net.minecraft.world.phys.Vec3;

import java.util.ArrayList;
import java.util.List;

/**
 * Walk to a position with his own legs, or to a player who may keep moving.
 * What the legs cannot do, it does like a player: climbs mod ladders, and opens an iron door with the button or
 * lever next to it and walks through at once. If it makes no progress it tries a new path, then gives up with the
 * reason instead of standing forever.
 */
public class GotoTask extends Task {
    private BlockPos target;
    private final int range;
    private final String player;   // walking to a player: follow where he is now
    private boolean started;
    private int idle;
    private int stuck;
    private int repaths;
    private Vec3 lastPos;
    private Task climb;
    private int climbs;
    private BlockPos approach;     // an iron door on the way: its doorstep, where to press the button from
    private BlockPos activator;
    private int approachTicks;
    private Task opener;           // pressing the button/lever of an iron door on the way
    private List<Vec3> passing;    // ...then straight through the doorway
    private int passTicks;
    private int doors;
    private String blockedBy = "";

    public GotoTask(String name, BlockPos target, int range) {
        this(name, target, range, null);
    }

    public GotoTask(String name, BlockPos target, int range, String player) {
        super(name);
        this.target = target;
        this.range = range;
        this.player = player;
    }

    /**
     * The way goes up or down, and the legs cannot find it (it knows vanilla ladders only): climb a ladder that
     * leads there like a player, then walk on. At most a few ladders per trip.
     */
    private boolean climbLadder() {
        int dy = target.getY() - p().getBlockY();
        if (climbs >= 3 || Math.abs(dy) <= 2 || ClimbTask.ladderNear(12, target.getY(), dy > 0) == null) return false;
        climbs++;
        idle = 0;
        stuck = 0;
        Nav.cancel();
        climb = new ClimbTask(dy > 0, target.getY(), true);
        return true;
    }

    /**
     * An iron door (one that does not open by hand) between us and the target: press its button or lever on our
     * side, then walk straight through — a button keeps it open only a second.
     */
    private boolean openIronDoor() {
        return openIronDoor(6);
    }

    /** slack: how far off the straight way the door may be (small when looking ahead, before the legs gives up). */
    private boolean openIronDoor(double slack) {
        if (doors >= 3) return false;
        var p = p();
        BlockPos me = p.blockPosition();
        double direct = Math.sqrt(me.distSqr(target));
        BlockPos door = null;
        double best = Double.MAX_VALUE;
        for (BlockPos bp : BlockPos.betweenClosed(me.offset(-7, -2, -7), me.offset(7, 2, 7))) {
            BlockState st = Bot.level().getBlockState(bp);
            if (!(st.getBlock() instanceof DoorBlock db) || db.type().canOpenByHand() || st.getValue(DoorBlock.OPEN)
                    || st.getValue(DoorBlock.HALF) != DoubleBlockHalf.LOWER) continue;
            double via = Math.sqrt(bp.distSqr(me)) + Math.sqrt(bp.distSqr(target));
            if (via > direct + slack || via >= best) continue;
            best = via;
            door = bp.immutable();
        }
        if (door == null) return false;
        doors++;
        BlockPos act = null;
        best = Double.MAX_VALUE;
        for (BlockPos bp : BlockPos.betweenClosed(door.offset(-2, -1, -2), door.offset(2, 2, 2))) {
            Block b = Bot.level().getBlockState(bp).getBlock();
            if (!(b instanceof ButtonBlock || b instanceof LeverBlock)) continue;
            double d = bp.distSqr(me);   // the nearest one is on our side of the wall
            if (d < best) {
                best = d;
                act = bp.immutable();
            }
        }
        if (act == null) {
            blockedBy = "путь закрыт железной дверью в " + Bot.pos(door) + ", а рядом нет ни кнопки, ни рычага — её открывает"
                    + " только сигнал (кнопка, рычаг, нажимная плита) или командир";
            return false;
        }
        // like a person: step up to the door, press, and walk straight through (a stone button holds it a second)
        Direction f = Bot.level().getBlockState(door).getValue(DoorBlock.FACING);
        Direction ours = me.distSqr(door.relative(f)) < me.distSqr(door.relative(f.getOpposite())) ? f : f.getOpposite();
        approach = door.relative(ours);
        approachTicks = 0;
        activator = act;
        passing = new ArrayList<>(List.of(Vec3.atBottomCenterOf(door), Vec3.atBottomCenterOf(door.relative(ours.getOpposite())),
                Vec3.atBottomCenterOf(door.relative(ours.getOpposite(), 2))));
        Nav.cancel();
        return true;
    }

    private void keys(boolean forward, boolean sprint) {
        Bot.mc().options.keyUp.setDown(forward);
        Bot.mc().options.keySprint.setDown(sprint);
    }

    @Override
    protected Status run() {
        var p = p();
        if (climb != null) {   // climbing a ladder the legs could not use: then walk on
            Status s = climb.tick();
            if (s == Status.RUNNING) return s;
            climb.stop();
            String why = climb.result();
            climb = null;
            if (s == Status.FAILED) blockedBy = why;
            stuck = 0;
            lastPos = p.position();
            Nav.gotoNear(target, range);
            return Status.RUNNING;
        }
        if (approach != null) {   // to the doorstep first
            if (approachTicks++ == 0) Nav.gotoNear(approach, 0);
            double hd = Math.hypot(p.getX() - (approach.getX() + 0.5), p.getZ() - (approach.getZ() + 0.5));
            if (hd < 0.7 || (approachTicks > 20 && !Nav.busy()) || approachTicks > 20 * 20) {
                Nav.cancel();
                approach = null;
                opener = new UseBlockTask(activator);
            }
            return Status.RUNNING;
        }
        if (opener != null) {
            Status s = opener.tick();
            if (s == Status.RUNNING) return s;
            opener.stop();
            String r = opener.result();
            opener = null;
            if (s == Status.FAILED || r.contains("НЕ изменился")) {
                passing = null;
                return fail("путь закрыт железной дверью: её кнопка/рычаг не сработали (" + r + ")");
            }
            passTicks = 1;
            return Status.RUNNING;
        }
        if (passTicks > 0) {   // through the open doorway, straight away
            while (!passing.isEmpty() && Math.hypot(p.getX() - passing.get(0).x, p.getZ() - passing.get(0).z) < 0.45) passing.remove(0);
            if (passing.isEmpty() || ++passTicks > 60) {
                keys(false, false);
                passTicks = 0;
                passing = null;
                idle = 0;
                stuck = 0;
                lastPos = p.position();
                Nav.gotoNear(target, range);
                return Status.RUNNING;
            }
            Vec3 next = passing.get(0);
            Bot.lookAt(new Vec3(next.x, p.getEyeY(), next.z));
            keys(Bot.aimed(new Vec3(next.x, p.getEyeY(), next.z), 30), true);
            return Status.RUNNING;
        }
        if (player != null) {
            Player t = Bot.findPlayer(player);
            if (t != null && t.blockPosition().distSqr(target) > 9) {
                target = t.blockPosition();   // he moved: head for where he is now
                if (started) Nav.gotoNear(target, range);
            }
        }
        double d = Bot.distTo(target);
        if (!started) {
            if (d <= range + 0.8) return done("уже на месте");
            if (!Nav.gotoNear(target, range)) return fail("не могу пойти к " + Bot.pos(target));
            started = true;
            lastPos = p.position();
            return Status.RUNNING;
        }
        // an iron door with its button right on the way: press it, as a person would, instead of letting the legs
        // dig under the wall of someone's house
        if (age % 40 == 5 && d > range + 2 && doors < 3 && openIronDoor(2.5)) {
            return Status.RUNNING;
        }
        // close enough — but while still walking, only when really there: through a door he used to stop in the doorway
        if (d <= range + 0.8 && (!Nav.busy() || d <= range + 0.3)) {
            Nav.cancel();
            return done("пришёл к " + Bot.pos(target));
        }
        if (age > 20 && !Nav.busy()) {
            if (++idle > 10) {
                // the legs stopped a little short: fine — but not a doorway short of a room
                if (d <= range + 1.2) return done("пришёл к " + Bot.pos(target));
                // the legs found no way: maybe up or down a mod's ladder, or through an iron door
                if (climbLadder() || openIronDoor()) return Status.RUNNING;
                int room = roomAround(p().blockPosition());
                // shut in, or the legs sees no way at all and he has not made a single step (a pit by conveyors
                // the legs will not walk on)
                // shut in (a pit, a closed room): how to get out — dig a step, put a block under the feet, open
                // something — is the AI's own choice, with its hands (control); here only what he sees is said
                return fail("не смог дойти до " + Bot.pos(target) + ", осталось " + Math.round(d) + " бл."
                        + (blockedBy.isEmpty() ? "" : " — " + blockedBy)
                        + (Legs.lastFailure().isEmpty() ? "" : " (" + Legs.lastFailure() + ")")
                        + (room < 30 ? " — Я ЗАПЕРТ: вокруг всего " + room + " свободных клеток, выход закрыт (яма, стены или"
                        + " дверь, которую не открыть)" : ""));
            }
        } else {
            idle = 0;
        }
        // the legs "busy" but the bot does not move (endless path search, blocked way): try again, then give up
        if (age % 20 == 0) {
            Vec3 now = p.position();
            if (lastPos != null && now.distanceToSqr(lastPos) < 0.25) {
                if (++stuck >= 15) {
                    stuck = 0;
                    if (climbLadder() || openIronDoor()) return Status.RUNNING;
                    if (++repaths > 2) {
                        Nav.cancel();
                        return fail("не могу пройти к " + Bot.pos(target) + " (застрял, осталось " + Math.round(d) + " бл.)"
                                + (blockedBy.isEmpty() ? "" : " — " + blockedBy));
                    }
                    Nav.cancel();
                    Nav.gotoNear(target, range + repaths);
                }
            } else {
                stuck = 0;
            }
            lastPos = now;
        }
        if (age > 20 * 600) return fail("слишком долгий путь, остановился в " + Math.round(d) + " бл. от цели");
        return Status.RUNNING;
    }

    /** How many spots he can walk to from here (up to 200): a handful means he is shut in. */
    static int roomAround(BlockPos start) {
        var level = Bot.level();
        java.util.function.Predicate<BlockPos> free = bp -> level.getBlockState(bp).getCollisionShape(level, bp).isEmpty();
        java.util.ArrayDeque<BlockPos> queue = new java.util.ArrayDeque<>();
        java.util.Set<BlockPos> seen = new java.util.HashSet<>();
        queue.add(start);
        seen.add(start);
        while (!queue.isEmpty() && seen.size() < 200) {
            BlockPos c = queue.poll();
            for (Direction dir : Direction.Plane.HORIZONTAL) {
                for (int dy = -1; dy <= 1; dy++) {
                    BlockPos n = c.relative(dir).above(dy);
                    if (seen.contains(n) || n.distSqr(start) > 12 * 12) continue;
                    if (free.test(n) && free.test(n.above()) && (dy <= 0 || free.test(c.above(2)))) {
                        seen.add(n);
                        queue.add(n);
                    }
                }
            }
        }
        return seen.size();
    }

    @Override
    public void stop() {
        if (climb != null) climb.stop();
        if (opener != null) opener.stop();
        if (passTicks > 0) keys(false, false);
        if (started) Nav.cancel();
    }

    @Override
    public void pause() {
        Nav.cancel();
    }

    @Override
    public void resume() {
        if (started) Nav.gotoNear(target, range);
    }

    @Override
    public String progress() {
        return Math.round(Bot.distTo(target)) + " бл. до цели";
    }
}
