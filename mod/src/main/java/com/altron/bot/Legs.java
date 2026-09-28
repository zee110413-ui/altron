package com.altron.bot;

import com.altron.AltronMod;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.tags.BlockTags;
import net.minecraft.tags.FluidTags;
import net.minecraft.util.Mth;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.DoorBlock;
import net.minecraft.world.level.block.FenceGateBlock;
import net.minecraft.world.level.block.LadderBlock;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.Vec3;
import net.minecraft.world.phys.shapes.VoxelShape;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.PriorityQueue;

/**
 * Altron's own legs: finding a way and walking it like a player, without Baritone.
 *
 * <p>A* over the cells his feet can stand in: walking (also diagonally), stepping up a block with a jump, dropping
 * down up to three blocks (any height into water), ladders and vines, swimming, and wooden doors and fence gates he
 * opens by hand on the way. He never breaks or places anything while walking: a guest does not dig through walls.
 * The search runs a few thousand cells per tick so the game never stutters; a far goal is reached in pieces (the part
 * of the world that is loaded, then onwards).
 *
 * <p>"legs" from the brain's config: "own" — these legs, with Baritone as a backup when it is installed and these
 * find no way; "own_only" — never Baritone; "baritone" — Baritone as before. Baritone.java routes walking here.
 */
public final class Legs {
    public enum State {IDLE, SEARCHING, WALKING, FOLLOWING, BACKUP}

    private static final int PER_TICK = 2500;        // cells searched per tick
    private static final int PER_SEARCH = 40000;     // cells per search before a partial way is taken
    private static final int MAX_DROP = 3;

    public static volatile String mode = "own";

    private static State state = State.IDLE;
    private static Goal goal;
    private static Search search;
    private static List<BlockPos> path = List.of();
    private static int next;                     // index of the node he is heading for
    private static boolean partial;              // the way leads closer, not all the way: plan again at its end
    private static int partials;
    private static String follow;                // following this player (null: going to a goal)
    private static int followRange;
    private static BlockPos followPlanned;       // where the followed player was when the way was planned
    private static Vec3 lastPos;
    private static int still;                    // ticks without getting anywhere
    private static int replans;
    private static int doorWait;
    private static long retryAt;                 // following someone out of reach: when to look for a way again
    private static String lastFailure = "";

    private Legs() {
    }

    // ------------------------------------------------------------------ what the rest of the mod asks for
    public static boolean enabled() {
        return !"baritone".equalsIgnoreCase(mode) || !Baritone.installed();
    }

    public static boolean gotoNear(BlockPos pos, int range) {
        start(new Near(pos.immutable(), Math.max(0, range)), null, 0);
        return true;
    }

    public static boolean gotoXZ(int x, int z) {
        start(new XZ(x, z), null, 0);
        return true;
    }

    public static boolean follow(String player, int range) {
        Player t = Bot.findPlayer(player);
        if (t == null) return false;
        start(new Near(t.blockPosition(), range), player, range);
        followPlanned = t.blockPosition();
        return true;
    }

    /** Going somewhere or following someone (also while standing next to the one he follows). */
    public static boolean busy() {
        return state != State.IDLE && (state != State.BACKUP || Baritone.rawBusy());
    }

    /** Really on the way: searching or walking, not standing next to the one he follows. */
    public static boolean pathing() {
        return state == State.SEARCHING || state == State.WALKING || (state == State.BACKUP && Baritone.rawPathing());
    }

    public static String lastFailure() {
        return lastFailure;
    }

    public static void cancel() {
        if (state == State.BACKUP) Baritone.rawCancel();
        state = State.IDLE;
        search = null;
        path = List.of();
        follow = null;
        keys(false, false, false, false);
    }

    private static void start(Goal g, String player, int range) {
        if (state == State.BACKUP) Baritone.rawCancel();
        goal = g;
        follow = player;
        followRange = range;
        partials = 0;
        replans = 0;
        lastFailure = "";
        plan();
    }

    private static void plan() {
        LocalPlayer p = Bot.player();
        if (p == null) {
            state = State.IDLE;
            return;
        }
        keys(false, false, false, false);
        search = new Search(feet(p), goal);
        path = List.of();
        state = State.SEARCHING;
    }

    // ------------------------------------------------------------------ every client tick
    public static void tick() {
        LocalPlayer p = Bot.player();
        if (p == null || state == State.IDLE) return;
        if (state == State.BACKUP) {
            if (!Baritone.rawBusy()) state = State.IDLE;
            return;
        }
        if (follow != null && Bot.level().getGameTime() % 10 == 0) {
            Player t = Bot.findPlayer(follow);
            if (t != null) {
                boolean near = t.distanceTo(p) <= followRange + 1.0;
                if (near && state == State.WALKING) {
                    stopWalking(State.FOLLOWING);
                } else if (!near && Bot.level().getGameTime() >= retryAt
                        && (state == State.FOLLOWING || t.blockPosition().distSqr(followPlanned) > 9)) {
                    goal = new Near(t.blockPosition(), followRange);
                    followPlanned = t.blockPosition();
                    plan();
                }
            }
        }
        if (state == State.FOLLOWING) return;
        if (state == State.SEARCHING) {
            Search s = search;
            if (s == null || !s.step(PER_TICK)) return;
            search = null;
            List<BlockPos> found = s.result();
            if (found == null) {
                failed("не нашёл пути: " + s.why());
                return;
            }
            path = found;
            partial = !s.complete;
            next = 1;
            still = 0;
            lastPos = p.position();
            state = State.WALKING;
        }
        walk(p);
    }

    private static void stopWalking(State then) {
        keys(false, false, false, false);
        path = List.of();
        state = then;
    }

    private static void arrived() {
        keys(false, false, false, false);
        if (partial && partials++ < 40) {   // a piece of a long way: on with the next piece
            replans = 0;
            plan();
            return;
        }
        path = List.of();
        state = follow != null ? State.FOLLOWING : State.IDLE;
    }

    private static void failed(String why) {
        keys(false, false, false, false);
        path = List.of();
        lastFailure = why;
        if (follow != null) {
            // someone he follows is out of reach for now (across water, up a cliff): wait and look again
            state = State.FOLLOWING;
            retryAt = Bot.level().getGameTime() + 60;
            if ("own".equalsIgnoreCase(mode) && Baritone.installed() && Baritone.rawFollow(follow)) state = State.BACKUP;
            return;
        }
        if ("own".equalsIgnoreCase(mode) && Baritone.installed() && goal != null && goal.handOver()) {
            AltronMod.LOG.info("[Altron] own legs found no way ({}), Baritone takes over", why);
            state = State.BACKUP;
            return;
        }
        AltronMod.LOG.info("[Altron] no way: {}", why);
        state = State.IDLE;
    }

    // ------------------------------------------------------------------ walking the way
    private static void walk(LocalPlayer p) {
        if (next >= path.size()) {
            arrived();
            return;
        }
        // the node he stands at or has passed (an overshoot, a fall onto a later part of the way)
        for (int j = Math.min(path.size() - 1, next + 3); j >= next; j--) {
            if (reached(p, path.get(j))) {
                next = j + 1;
                still = 0;
                break;
            }
        }
        if (next >= path.size()) {
            arrived();
            return;
        }
        if (goal != null && goal.at(feet(p)) && !partial) {
            arrived();
            return;
        }
        BlockPos t = path.get(next);
        BlockPos from = path.get(next - 1);
        // thrown off the way (knocked back, fell): plan again from here
        if (horiz(p, from) > 2.5 && horiz(p, t) > 2.5 || p.getY() < Math.min(from.getY(), t.getY()) - 2.5) {
            replan("сбился с пути");
            return;
        }
        // not getting anywhere for 5 s (bumping into something the search did not see): plan again, then give up
        Vec3 now = p.position();
        if (lastPos != null && now.distanceToSqr(lastPos) < 0.01) still++;
        else still = 0;
        lastPos = now;
        if (still > 100) {
            replan("застрял у " + Bot.pos(t));
            return;
        }
        if (openDoor(p, t)) return;

        boolean water = p.isInWater();
        boolean climbing = t.getX() == from.getX() && t.getZ() == from.getZ();
        double dx = t.getX() + 0.5 - p.getX(), dz = t.getZ() + 0.5 - p.getZ();
        double hd = Math.sqrt(dx * dx + dz * dz);
        if (climbing && hd < 0.35) {
            if (t.getY() > from.getY()) {
                // up a ladder: into the wall it hangs on; swimming up: just rise
                BlockState at = Bot.level().getBlockState(from);
                if (at.getBlock() instanceof LadderBlock) face(p, at.getValue(LadderBlock.FACING).getOpposite());
                keys(at.getBlock() instanceof LadderBlock, true, false, false);
            } else {
                keys(false, false, false, water);   // down: let go of the ladder, or sink
            }
            return;
        }
        float want = (float) Math.toDegrees(Math.atan2(dz, dx)) - 90f;
        float diff = Mth.wrapDegrees(want - p.getYRot());
        float turned = p.getYRot() + Mth.clamp(diff, -35f, 35f);
        p.setYRot(turned);
        p.setYHeadRot(turned);
        p.setXRot(Mth.clamp(p.getXRot() * 0.8f, -20f, 20f));   // eyes on the way, not the sky
        boolean forward = Math.abs(diff) < 70;
        boolean up = t.getY() > from.getY() || (t.getY() == from.getY() && floorTop(t) > p.getY() - t.getY() + 0.6);
        boolean jump = (up && hd < 1.4 && p.onGround()) || (p.horizontalCollision && p.onGround() && forward)
                || (water && t.getY() >= from.getY());
        boolean sprint = forward && !water && Math.abs(diff) < 15 && p.getFoodData().getFoodLevel() > 6 && straight(next, 4);
        keys(forward, jump, sprint, false);
    }

    private static boolean reached(LocalPlayer p, BlockPos n) {
        return horiz(p, n) < 0.45 && p.getY() >= n.getY() - 0.4 && p.getY() < n.getY() + 1.2;
    }

    private static double horiz(LocalPlayer p, BlockPos n) {
        return Math.hypot(p.getX() - (n.getX() + 0.5), p.getZ() - (n.getZ() + 0.5));
    }

    /** The next few steps go the same way: worth running. */
    private static boolean straight(int from, int n) {
        if (from + n >= path.size()) return false;
        BlockPos a = path.get(from - 1), b = path.get(from);
        int sx = b.getX() - a.getX(), sz = b.getZ() - a.getZ();
        for (int i = from; i < from + n; i++) {
            BlockPos c = path.get(i), d = path.get(i + 1);
            if (d.getX() - c.getX() != sx || d.getZ() - c.getZ() != sz || d.getY() != c.getY()) return false;
        }
        return true;
    }

    private static void replan(String why) {
        if (++replans > 4) {
            failed(why);
            return;
        }
        plan();
    }

    /** A closed wooden door or fence gate on the next step: open it by hand, like a player. */
    private static boolean openDoor(LocalPlayer p, BlockPos t) {
        for (BlockPos c : new BlockPos[]{t, t.above()}) {
            BlockState st = Bot.level().getBlockState(c);
            boolean closed = (st.getBlock() instanceof DoorBlock db && db.type().canOpenByHand() && !st.getValue(DoorBlock.OPEN))
                    || (st.getBlock() instanceof FenceGateBlock && !st.getValue(FenceGateBlock.OPEN));
            if (!closed) continue;
            if (Bot.eyeDistTo(c) > 3.5) return false;   // walk up to it first
            keys(false, false, false, false);
            Bot.lookAt(Vec3.atCenterOf(c));
            if (doorWait > 0) {
                doorWait--;
                return true;
            }
            Minecraft mc = Bot.mc();
            if (mc.gameMode != null) {
                mc.gameMode.useItemOn(p, InteractionHand.MAIN_HAND, Bot.hit(c, Bot.faceToward(c)));
                p.swing(InteractionHand.MAIN_HAND);
            }
            doorWait = 10;
            return true;
        }
        doorWait = 0;
        return false;
    }

    private static void face(LocalPlayer p, Direction d) {
        float yaw = d.toYRot();
        p.setYRot(yaw);
        p.setYHeadRot(yaw);
    }

    private static void keys(boolean forward, boolean jump, boolean sprint, boolean sneak) {
        Minecraft mc = Minecraft.getInstance();
        if (mc.options == null) return;
        mc.options.keyUp.setDown(forward);
        mc.options.keyJump.setDown(jump);
        mc.options.keySprint.setDown(sprint);
        mc.options.keyShift.setDown(sneak);
    }

    static BlockPos feet(LocalPlayer p) {
        // standing on a slab or a carpet the feet are inside its cell; on a full block, in the cell above it
        return BlockPos.containing(p.getX(), p.getY() + 0.05, p.getZ());
    }

    // ------------------------------------------------------------------ what the world is like for walking
    private static VoxelShape shape(BlockPos c) {
        return Bot.level().getBlockState(c).getCollisionShape(Bot.level(), c);
    }

    private static boolean openable(BlockState st) {
        return (st.getBlock() instanceof DoorBlock db && db.type().canOpenByHand()) || st.getBlock() instanceof FenceGateBlock;
    }

    private static boolean harmful(BlockState st) {
        return st.getFluidState().is(FluidTags.LAVA) || st.is(BlockTags.FIRE) || st.is(BlockTags.CAMPFIRES)
                || st.is(Blocks.MAGMA_BLOCK) || st.is(Blocks.CACTUS) || st.is(Blocks.SWEET_BERRY_BUSH)
                || st.is(Blocks.WITHER_ROSE) || st.is(Blocks.POWDER_SNOW);
    }

    /** Low things the feet stand inside: slabs, carpets, snow layers, paths (up to a step high). */
    private static boolean low(BlockPos c) {
        VoxelShape s = shape(c);
        return !s.isEmpty() && s.max(Direction.Axis.Y) <= 0.6 && s.min(Direction.Axis.Y) <= 0.01;
    }

    /** How high the floor inside this cell is (0 for air, 0.5 for a slab). */
    private static double floorTop(BlockPos c) {
        VoxelShape s = shape(c);
        return s.isEmpty() ? 0 : s.max(Direction.Axis.Y);
    }

    private static final AABB BODY = new AABB(0.2, 0, 0.2, 0.8, 1, 0.8);   // a player in the middle of a cell

    /** Nothing in the way of a player's body here: empty, or only thin things at its sides (a ladder on the wall, an
     *  open trapdoor), or a door or gate he opens by hand. */
    private static boolean free(BlockPos c) {
        BlockState st = Bot.level().getBlockState(c);
        if (harmful(st)) return false;
        if (openable(st)) return true;
        VoxelShape s = st.getCollisionShape(Bot.level(), c);
        if (s.isEmpty()) return true;
        for (AABB box : s.toAabbs()) {
            if (box.intersects(BODY)) return false;
        }
        return true;
    }

    private static boolean climbable(BlockPos c) {
        BlockState st = Bot.level().getBlockState(c);
        return st.is(BlockTags.CLIMBABLE) || st.isLadder(Bot.level(), c, Bot.player());
    }

    private static boolean water(BlockPos c) {
        return Bot.level().getBlockState(c).getFluidState().is(FluidTags.WATER);
    }

    /** Room for a player: two free cells (the feet cell may hold a slab or a carpet). */
    private static boolean body(BlockPos c) {
        if (!Bot.level().isLoaded(c)) return false;
        return (free(c) || (low(c) && !harmful(Bot.level().getBlockState(c)))) && free(c.above());
    }

    /** Something to stand on (or hold on to, or swim in) at this cell. */
    private static boolean support(BlockPos c) {
        if (low(c) || climbable(c) || water(c)) return true;
        BlockPos below = c.below();
        BlockState st = Bot.level().getBlockState(below);
        if (harmful(st)) return false;
        VoxelShape s = st.getCollisionShape(Bot.level(), below);
        if (s.isEmpty()) return false;
        double top = s.max(Direction.Axis.Y);
        return top >= 0.8 && top <= 1.0;   // not the top of a fence or a wall: nobody stands there
    }

    private static boolean standable(BlockPos c) {
        return body(c) && support(c);
    }

    // ------------------------------------------------------------------ goals
    interface Goal {
        boolean at(BlockPos n);

        double h(BlockPos n);

        /** Hand this goal over to Baritone (a backup when these legs find no way); false if it cannot take it. */
        boolean handOver();
    }

    record Near(BlockPos pos, int range) implements Goal {
        public boolean at(BlockPos n) {
            return n.distSqr(pos) <= (double) range * range + 0.01;
        }

        public double h(BlockPos n) {
            return Math.max(0, octile(n.getX() - pos.getX(), n.getZ() - pos.getZ()) + Math.abs(n.getY() - pos.getY()) - range);
        }

        public boolean handOver() {
            return Baritone.rawGotoNear(pos, range);
        }
    }

    record XZ(int x, int z) implements Goal {
        public boolean at(BlockPos n) {
            return n.getX() == x && n.getZ() == z;
        }

        public double h(BlockPos n) {
            return octile(n.getX() - x, n.getZ() - z);
        }

        public boolean handOver() {
            return Baritone.rawGotoXZ(x, z);
        }
    }

    static double octile(int dx, int dz) {
        int a = Math.abs(dx), b = Math.abs(dz);
        return Math.max(a, b) + 0.414 * Math.min(a, b);
    }

    // ------------------------------------------------------------------ the search
    static final class Node {
        final BlockPos pos;
        double g, f;
        Node parent;
        boolean closed;

        Node(BlockPos pos) {
            this.pos = pos;
        }
    }

    static final class Search {
        private final Goal goal;
        private final Map<Long, Node> nodes = new HashMap<>();
        private final PriorityQueue<Node> open = new PriorityQueue<>((a, b) -> Double.compare(a.f, b.f));
        private final Node start;
        private Node best;
        private Node end;
        private int expanded;
        boolean complete;

        Search(BlockPos from, Goal goal) {
            this.goal = goal;
            // standing in the air for a moment (a jump, a ledge): search from the ground below
            BlockPos s = from;
            for (int i = 0; i < 3 && !standable(s) && Bot.level().getBlockState(s.below()).getCollisionShape(Bot.level(), s.below()).isEmpty(); i++) {
                s = s.below();
            }
            start = new Node(s);
            start.f = goal.h(s);
            nodes.put(s.asLong(), start);
            open.add(start);
            best = start;
        }

        /** Search on for up to budget cells; true when the search is over (found, impossible or out of budget). */
        boolean step(int budget) {
            while (budget-- > 0) {
                Node n = open.poll();
                if (n == null) return true;
                if (n.closed) continue;
                n.closed = true;
                if (goal.at(n.pos)) {
                    end = n;
                    complete = true;
                    return true;
                }
                double h = n.f - n.g;
                if (h < best.f - best.g || (h == best.f - best.g && n.g < best.g)) best = n;
                if (++expanded >= PER_SEARCH) return true;
                expand(n);
            }
            return false;
        }

        private void add(Node from, BlockPos to, double cost) {
            long key = to.asLong();
            Node m = nodes.get(key);
            double g = from.g + cost;
            if (m == null) {
                m = new Node(to);
                nodes.put(key, m);
            } else if (m.closed || g >= m.g) {
                return;
            }
            m.g = g;
            m.f = g + goal.h(to);
            m.parent = from;
            open.add(m);
        }

        private void expand(Node n) {
            BlockPos c = n.pos;
            boolean inWater = water(c);
            for (Direction d : Direction.Plane.HORIZONTAL) {
                BlockPos side = c.relative(d);
                if (standable(side)) {
                    add(n, side, water(side) || inWater ? 2.5 : 1);
                } else if (body(side.above()) && support(side.above()) && free(c.above(2))) {
                    add(n, side.above(), 2);   // a step up with a jump
                } else if (body(side)) {
                    // over the edge: fall to the first ground below (three blocks at most, any height into water)
                    for (int drop = 1; drop <= 24; drop++) {
                        BlockPos land = side.below(drop);
                        if (!Bot.level().isLoaded(land)) break;
                        if (water(land) && body(land)) {
                            add(n, land, 1 + drop * 0.5);
                            break;
                        }
                        if (standable(land)) {
                            if (drop <= MAX_DROP) add(n, land, 1 + drop * 0.5);
                            break;
                        }
                        if (!body(land)) break;
                    }
                }
            }
            // diagonals, only where both corners are open (no cutting through the corner of a wall)
            for (int dx = -1; dx <= 1; dx += 2) {
                for (int dz = -1; dz <= 1; dz += 2) {
                    BlockPos diag = c.offset(dx, 0, dz);
                    if (standable(diag) && body(c.offset(dx, 0, 0)) && body(c.offset(0, 0, dz)) && !water(diag)) {
                        add(n, diag, 1.414);
                    }
                }
            }
            // up and down ladders and vines, and swimming up and down
            if ((climbable(c) || inWater) && body(c.above())) add(n, c.above(), 1.5);
            BlockPos down = c.below();
            if ((climbable(down) || water(down)) && body(down)) add(n, down, 1.5);
        }

        /** The way found: to the goal, or (out of budget) to the cell nearest to it — if that is any nearer. */
        List<BlockPos> result() {
            Node last = end;
            if (last == null) {
                if (best == start || best.f - best.g >= start.f - 1) return null;
                last = best;
            }
            List<BlockPos> out = new ArrayList<>();
            for (Node n = last; n != null; n = n.parent) out.add(0, n.pos);
            return out;
        }

        String why() {
            return expanded >= PER_SEARCH ? "слишком далеко или запутанно" : "всё вокруг закрыто (стены, обрывы, вода или лава)";
        }
    }
}
