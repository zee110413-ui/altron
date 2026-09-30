package com.altron.bot.tasks;

import com.altron.bot.Nav;
import com.altron.bot.Bot;
import com.altron.bot.Task;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.tags.BlockTags;
import net.minecraft.world.level.block.DoorBlock;
import net.minecraft.world.level.block.TrapDoorBlock;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.BlockStateProperties;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.Vec3;

import java.util.ArrayList;
import java.util.List;

/**
 * Climb a ladder like a player, with the movement keys only: walk up to its open side (or, going down, to the edge
 * above it), step exactly into its column, then hold forward+jump to go up, or let go to slide down. A closed hatch
 * (trapdoor) over the shaft is opened on the way, like a player does.
 * Works with any climbable block of any mod (EnderIO, Immersive Engineering covered ladders, SecurityCraft...):
 * His legs walk only to an ordinary standing spot next to it.
 */
public class ClimbTask extends Task {
    private static final Direction[] SIDES = {Direction.NORTH, Direction.SOUTH, Direction.WEST, Direction.EAST};
    private final boolean up;
    private final int targetY;
    private final boolean explicitY;
    private BlockPos column;      // a ladder block of the column (x, z of the column)
    private int bottom, top;      // the column's lowest and highest climbable block
    private Direction away;       // the side the ladder faces (its open side), null if unknown
    private BlockPos entry;       // the cell to step into, at feet height
    private BlockPos stand;       // where to stand before stepping in
    private Vec3 aim;             // the exact spot inside the entry cell to walk to
    private Direction step;       // direction of the step from stand into entry
    private Task helper;          // opening a hatch on the way
    private int phase;
    private int walk;
    private int offLadder;
    private int tries;
    private int still;
    private double lastY;
    private int hatches;
    private int settled;
    private boolean climbing;

    public ClimbTask(boolean up, int targetY) {
        this(up, targetY, false);
    }

    /** explicitY: climb to that height (from goto), choosing a ladder that reaches it. */
    public ClimbTask(boolean up, int targetY, boolean explicitY) {
        super("climb");
        this.up = up;
        this.targetY = targetY;
        this.explicitY = explicitY;
    }

    public static boolean climbable(BlockState st, BlockPos pos) {
        return st.is(BlockTags.CLIMBABLE) || st.isLadder(Bot.level(), pos, Bot.player());
    }

    private static boolean climbableAt(BlockPos pos) {
        return climbable(Bot.level().getBlockState(pos), pos);
    }

    /** The nearest climbable block within r blocks. */
    public static BlockPos ladderNear(int r) {
        return ladderNear(r, Integer.MIN_VALUE, true);
    }

    /** The nearest ladder column within r blocks that reaches height y (going up) or comes down to it. */
    public static BlockPos ladderNear(int r, int y, boolean up) {
        var p = Bot.player();
        BlockPos me = p.blockPosition();
        BlockPos best = null, partial = null;
        double bestD = Double.MAX_VALUE, partialD = Double.MAX_VALUE;
        for (BlockPos bp : BlockPos.betweenClosed(me.offset(-r, -4, -r), me.offset(r, 3, r))) {
            if (!climbableAt(bp)) continue;
            double d = bp.distSqr(me);
            if (y != Integer.MIN_VALUE) {
                int[] span = span(bp);
                if (up ? span[1] + 2 < y - 1 : span[0] > y + 1) {
                    // does not lead all the way, but at least in the right direction (one floor of several)
                    boolean onward = up ? span[1] + 1 > me.getY() + 1 : span[0] < me.getY() - 2;
                    if (onward && d < partialD) {
                        partialD = d;
                        partial = bp.immutable();
                    }
                    continue;
                }
            }
            if (d < bestD) {
                bestD = d;
                best = bp.immutable();
            }
        }
        return best != null ? best : partial;
    }

    /** Lowest and highest climbable block of the column through pos. */
    private static int[] span(BlockPos pos) {
        int lo = pos.getY(), hi = pos.getY();
        while (lo > pos.getY() - 128 && climbableAt(pos.atY(lo - 1))) lo--;
        while (hi < pos.getY() + 128 && climbableAt(pos.atY(hi + 1))) hi++;
        return new int[]{lo, hi};
    }

    private static boolean free(BlockPos pos) {
        return Bot.level().getBlockState(pos).getCollisionShape(Bot.level(), pos).isEmpty();
    }

    /** Feet fit here: something to stand on below, and two free cells. */
    private static boolean standable(BlockPos pos) {
        var level = Bot.level();
        BlockPos below = pos.below();
        BlockState under = level.getBlockState(below);
        boolean ground = !under.getCollisionShape(level, below).isEmpty() && !climbable(under, below);
        return ground && free(pos) && free(pos.above());
    }

    /** A player standing (feet) at this spot would not be inside any block. */
    private static boolean fits(Vec3 at) {
        AABB box = new AABB(at.x - 0.3, at.y + 0.02, at.z - 0.3, at.x + 0.3, at.y + 1.78, at.z + 0.3);
        return Bot.level().noCollision(Bot.player(), box);
    }

    private static boolean hatch(BlockState st) {
        return (st.getBlock() instanceof TrapDoorBlock || st.getBlock() instanceof DoorBlock)
                && st.hasProperty(BlockStateProperties.OPEN) && !st.getValue(BlockStateProperties.OPEN);
    }

    /** The side a ladder faces: its FACING, or else the side opposite a solid wall next to it. */
    private static Direction openSide(BlockPos ladder) {
        BlockState st = Bot.level().getBlockState(ladder);
        if (st.hasProperty(BlockStateProperties.HORIZONTAL_FACING)) return st.getValue(BlockStateProperties.HORIZONTAL_FACING);
        for (Direction d : SIDES) {
            BlockPos w = ladder.relative(d);
            if (Bot.level().getBlockState(w).isFaceSturdy(Bot.level(), w, d.getOpposite())) return d.getOpposite();
        }
        return null;
    }

    /** "" when there is a way onto the ladder, or why there is none. */
    private String plan() {
        var p = p();
        BlockPos found = explicitY ? ladderNear(12, targetY, up) : null;
        if (found == null) found = ladderNear(8);
        if (found == null) return "рядом не вижу лестницы, по которой можно лезть";
        int[] s = span(found);
        bottom = s[0];
        top = s[1];
        int feet = p.getBlockY();
        away = openSide(found);
        // going up: step into the column at our feet (or right under its lowest rung and jump);
        // going down: into the column at our feet — the ladder itself, or the air (or a hatch) above its top
        entry = up ? found.atY(Math.max(Math.min(feet, top), bottom - 1)) : found.atY(Math.max(feet, bottom));
        column = found.atY(Math.max(bottom, Math.min(top, entry.getY())));
        // the spot to walk to: the middle of the cell, a little off the rungs (a covered ladder's cage is narrow)
        Vec3 mid = new Vec3(entry.getX() + 0.5, entry.getY(), entry.getZ() + 0.5);
        aim = away != null ? mid.add(away.getStepX() * 0.07, 0, away.getStepZ() * 0.07) : mid;
        // where to stand first: its open side if there is ground there, else any side with ground (the edge above it)
        List<Direction> order = new ArrayList<>();
        if (away != null) {
            order.add(away);
            order.add(away.getClockWise());
            order.add(away.getCounterClockWise());
            order.add(away.getOpposite());
        } else {
            order.addAll(List.of(SIDES));
        }
        if (!up) {
            // going down from above: step to the edge from our side, not walk around the hole
            BlockPos me = p.blockPosition();
            order.sort(java.util.Comparator.comparingDouble(d -> entry.relative(d).distSqr(me)));
        }
        stand = null;
        boolean blocked = false;
        for (Direction d : order) {
            BlockPos at = entry.relative(d);
            if (!standable(at)) continue;
            // the way in from this side must be open (not a cage wall, a fence...)
            Vec3 half = new Vec3((at.getX() + 0.5 + aim.x) / 2, entry.getY(), (at.getZ() + 0.5 + aim.z) / 2);
            if (up && !(fits(aim) && fits(half))) {
                blocked = true;
                continue;
            }
            stand = at;
            step = d.getOpposite();
            break;
        }
        if (stand == null) {
            return blocked ? "в эту лестницу не войти сбоку: она закрыта каркасом или стенкой (как покрытая лестница IE) — "
                    + "в такую входят сверху или через открытый участок внизу"
                    : "не могу подойти к лестнице в " + Bot.pos(column) + ": рядом с ней не на что встать";
        }
        return "";
    }

    private void keys(boolean forward, boolean back, boolean left, boolean right, boolean jump) {
        var o = Bot.mc().options;
        o.keyUp.setDown(forward);
        o.keyDown.setDown(back);
        o.keyLeft.setDown(left);
        o.keyRight.setDown(right);
        o.keyJump.setDown(jump);
    }

    private void release() {
        keys(false, false, false, false, false);
        Bot.mc().options.keyShift.setDown(false);
    }

    /** Look horizontally at the wall the ladder hangs on. */
    private void faceWall() {
        var p = p();
        Direction wall = away != null ? away.getOpposite() : step;
        Vec3 c = Vec3.atCenterOf(column);
        Bot.lookAt(new Vec3(c.x + wall.getStepX() * 3, p.getEyeY(), c.z + wall.getStepZ() * 3));
    }

    /**
     * Step to an exact spot with the movement keys, like a player: face the step's direction and press
     * forward/back/strafe with the braking distance in mind; the last half block sneaking, for precision
     * (going up only: sneaking would keep us from dropping onto the rungs going down). True when there.
     */
    private boolean steer(Vec3 to) {
        var p = p();
        Vec3 dir = new Vec3(step.getStepX(), 0, step.getStepZ());
        Vec3 side = new Vec3(-dir.z, 0, dir.x);   // to the right of the direction
        Bot.lookAt(new Vec3(p.getX() + dir.x * 5, p.getEyeY(), p.getZ() + dir.z * 5));
        Vec3 off = new Vec3(to.x - p.getX(), 0, to.z - p.getZ());
        Vec3 v = p.getDeltaMovement();
        double along = off.dot(dir), vAlong = v.x * dir.x + v.z * dir.z;
        double across = off.dot(side), vAcross = v.x * side.x + v.z * side.z;
        boolean slow = up && off.length() < 0.6;
        Bot.mc().options.keyShift.setDown(slow);
        double brake = p.onGround() ? (slow ? 1.5 : 2.5) : 6;   // ticks it takes to stop
        double tol = up ? 0.05 : 0.08;
        boolean fwd = along - vAlong * brake > tol;
        boolean back = !fwd && along - vAlong * brake < -tol;
        boolean right = across - vAcross * brake > tol;
        boolean left = !right && across - vAcross * brake < -tol;
        keys(fwd, back, left, right, false);
        return Math.abs(along) < 0.1 && Math.abs(across) < 0.1 && v.horizontalDistance() < 0.04;
    }

    /** Use a hatch/door in the way (a sub-task: walks up to it if needed, and reports if it did not open). */
    private boolean openHatch(BlockPos pos) {
        if (hatches++ >= 3) return false;
        release();
        helper = new UseBlockTask(pos);
        return true;
    }

    @Override
    protected Status run() {
        var p = p();
        if (helper != null) {
            Status s = helper.tick();
            if (s == Status.RUNNING) return s;
            helper.stop();
            String r = helper.result();
            helper = null;
            if (s == Status.FAILED || r.contains("НЕ изменился")) {
                release();
                return fail("путь по лестнице закрыт люком, и он не открылся: " + r);
            }
            if (phase == 3) still = 0;
            return Status.RUNNING;
        }
        if (phase == 0) {
            String why = plan();
            if (!why.isEmpty()) return fail(why);
            if (!up) {
                // going down through a closed hatch in the floor: open it first
                for (int y = entry.getY() - 1; y > top; y--) {
                    BlockPos c = entry.atY(y);
                    if (hatch(Bot.level().getBlockState(c))) {
                        if (openHatch(c)) return Status.RUNNING;
                        return fail("вход на лестницу закрыт люком в " + Bot.pos(c));
                    }
                    BlockState cs = Bot.level().getBlockState(c);
                    boolean open = cs.hasProperty(BlockStateProperties.OPEN) && cs.getValue(BlockStateProperties.OPEN);
                    if (!free(c) && !open) return fail("над лестницей в " + Bot.pos(column) + " нет прохода — закрыто блоком " + Bot.pos(c));
                }
            }
            phase = 1;
            walk = 0;
            if (horizontal(stand) > 0.45 || Math.abs(p.getY() - stand.getY()) > 0.6) Nav.gotoNear(stand, 0);
            return Status.RUNNING;
        }
        if (phase == 1) {   // walking to the spot next to it
            if (p.onClimbable() && up && Math.abs(p.getY() - entry.getY()) < 0.6) {
                // walked onto the rungs on the way: still line up in the column first (a head over the edge of the
                // shaft bumps into the floor above)
                Nav.cancel();
                phase = 2;
                walk = 0;
                return Status.RUNNING;
            }
            boolean there = horizontal(stand) < 0.5 && Math.abs(p.getY() - stand.getY()) < 0.6;
            if (there || (walk > 20 && !Nav.busy())) {
                Nav.cancel();
                if (!there && horizontal(stand) > 1.6) {
                    if (++tries > 2) return fail("не смог подойти к лестнице в " + Bot.pos(column));
                    phase = 0;   // look again from here
                    return Status.RUNNING;
                }
                phase = 2;
                walk = 0;
                return Status.RUNNING;
            }
            if (++walk > 20 * 40) return fail("не смог подойти к лестнице в " + Bot.pos(column));
            return Status.RUNNING;
        }
        if (phase == 2) {   // stepping into the column
            boolean onIt = p.onClimbable();
            if (!up && (onIt || (!p.onGround() && p.getY() < entry.getY() - 0.2))) {
                release();   // dropped onto the rungs: let go, like a player sliding down
                phase = 3;
                return Status.RUNNING;
            }
            boolean there = steer(aim);
            if (up && onIt && there) {
                release();
                phase = 3;
                lastY = p.getY();
                return Status.RUNNING;
            }
            if (up && there && entry.getY() < bottom) keys(false, false, false, false, true);   // the rungs start above: jump
            if (up && there && entry.getY() >= bottom && !onIt) {   // standing in it but not holding on: just climb
                release();
                phase = 3;
                lastY = p.getY();
                return Status.RUNNING;
            }
            if (++walk > 20 * 8) {
                release();
                if (++tries > 2) return fail("не смог встать на лестницу в " + Bot.pos(column));
                phase = 0;
            }
            return Status.RUNNING;
        }
        // phase 3: on the ladder
        boolean on = p.onClimbable();
        if (on) climbing = true;
        offLadder = on ? 0 : offLadder + 1;
        if (up) {
            faceWall();
            // hold on in the middle of the column: strafe back if we slid toward a side
            Direction wall = away != null ? away.getOpposite() : step;
            Vec3 side = new Vec3(-wall.getStepZ(), 0, wall.getStepX());
            double across = (column.getX() + 0.5 - p.getX()) * side.x + (column.getZ() + 0.5 - p.getZ()) * side.z;
            double vAcross = p.getDeltaMovement().x * side.x + p.getDeltaMovement().z * side.z;
            double lean = across - vAcross * 3;
            keys(true, false, lean < -0.06, lean > 0.06, true);
            // not rising: what is the head bumping into? A closed hatch — open it like a player
            if (p.getY() > lastY + 0.05) {
                still = 0;
                lastY = p.getY();
            } else if (++still > 12 && on) {
                BlockPos bump = headBump();
                if (bump != null && hatch(Bot.level().getBlockState(bump))) {
                    if (openHatch(bump)) return Status.RUNNING;
                }
                if (still > 20 * 6) {
                    release();
                    String what = bump == null ? "" : ": над головой " + Bot.level().getBlockState(bump).getBlock().getName().getString()
                            + " в " + Bot.pos(bump);
                    return fail("не долез: упёрся на высоте " + p.getBlockY() + what);
                }
            }
            if (p.getBlockY() >= targetY || (climbing && offLadder > 8)) {
                keys(true, false, false, false, false);   // a last step forward onto the floor at the top
                if (offLadder > 14 || p.getBlockY() >= targetY + 1) {
                    release();
                    return done("поднялся по лестнице до высоты " + p.getBlockY());
                }
            }
        } else {
            // sliding down: hands off; only keep inside the column if the step carried us toward its open side
            Vec3 mid = new Vec3(column.getX() + 0.5, p.getY(), column.getZ() + 0.5);
            double drift = (p.getX() - mid.x) * step.getStepX() + (p.getZ() - mid.z) * step.getStepZ();
            double v = p.getDeltaMovement().x * step.getStepX() + p.getDeltaMovement().z * step.getStepZ();
            Bot.lookAt(new Vec3(p.getX() + step.getStepX() * 5, p.getEyeY(), p.getZ() + step.getStepZ() * 5));
            // pressing back into the rungs would climb up again: only when really drifting off the open side
            keys(false, !p.onGround() && drift + v * 6 > 0.25, false, false, false);
            boolean bottomReached = p.getBlockY() <= Math.max(targetY, bottom);
            // not going lower any more (some mods' ladders hold you in place instead of letting you slide)
            settled = Math.abs(p.getY() - lastY) < 0.02 ? settled + 1 : 0;
            lastY = p.getY();
            if ((p.onGround() && (bottomReached || offLadder > 4)) || (bottomReached && settled > 10)) {
                release();
                return done("спустился по лестнице до высоты " + p.getBlockY());
            }
        }
        if (age > 20 * 90) {
            release();
            return fail((up ? "не долез" : "не спустился") + ": застрял на высоте " + p.getBlockY());
        }
        return Status.RUNNING;
    }

    /** The block the head bumps into (just above the player's box), or null. */
    private BlockPos headBump() {
        var p = p();
        AABB above = p.getBoundingBox().move(0, 0.3, 0);
        BlockPos best = null;
        for (BlockPos bp : BlockPos.betweenClosed(BlockPos.containing(above.minX, above.maxY - 0.5, above.minZ),
                BlockPos.containing(above.maxX, above.maxY, above.maxZ))) {
            var shape = Bot.level().getBlockState(bp).getCollisionShape(Bot.level(), bp);
            if (shape.isEmpty()) continue;
            if (shape.bounds().move(bp).intersects(above)) {
                // the column's own cell first (a hatch right over us), else the first thing found
                if (best == null || (bp.getX() == column.getX() && bp.getZ() == column.getZ())) best = bp.immutable();
            }
        }
        return best;
    }

    private double horizontal(BlockPos pos) {
        var p = p();
        double dx = p.getX() - (pos.getX() + 0.5), dz = p.getZ() - (pos.getZ() + 0.5);
        return Math.sqrt(dx * dx + dz * dz);
    }

    @Override
    public void stop() {
        if (helper != null) helper.stop();
        release();
        Nav.cancel();
    }
}
