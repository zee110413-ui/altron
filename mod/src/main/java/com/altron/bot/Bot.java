package com.altron.bot;

import com.altron.J;
import com.google.gson.JsonObject;
import net.minecraft.client.Minecraft;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.client.player.AbstractClientPlayer;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.util.Mth;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.Vec3;

/** Shared helpers for the bot's body. */
public final class Bot {
    private static final double RAD_TO_DEG = 180.0 / Math.PI;

    private Bot() {
    }

    public static Minecraft mc() {
        return Minecraft.getInstance();
    }

    public static LocalPlayer player() {
        return Minecraft.getInstance().player;
    }

    public static ClientLevel level() {
        return Minecraft.getInstance().level;
    }

    // ---------------------------------------------------------------- smooth looking, like a player moving the mouse
    private static Vec3 lookTarget;
    private static Entity lookEntity;
    private static int lookTicks;
    private static int lastStep = -1;
    private static int clock;

    /** {yaw, pitch} from the eyes to a point. */
    private static float[] rotationTo(Vec3 target) {
        Vec3 eye = player().getEyePosition();
        double dx = target.x - eye.x, dy = target.y - eye.y, dz = target.z - eye.z;
        double horiz = Math.sqrt(dx * dx + dz * dz);
        return new float[]{(float) (Mth.atan2(dz, dx) * RAD_TO_DEG) - 90f, Mth.clamp((float) -(Mth.atan2(dy, horiz) * RAD_TO_DEG), -90f, 90f)};
    }

    private static Vec3 aimPoint(Entity e) {
        // slightly below the eyes: head shots without overshooting small mobs
        return new Vec3(e.getX(), e.getY() + e.getBbHeight() * 0.8, e.getZ());
    }

    /** Big turns fast (a flick of the mouse), the last degrees slowly; never more than 35 degrees in a tick. */
    private static float step(float diff) {
        float s = diff * 0.45f;
        if (Math.abs(diff) <= 1.5f) return diff;
        if (Math.abs(s) < 1.5f) s = Math.copySign(1.5f, diff);
        return Mth.clamp(s, -35f, 35f);
    }

    private static void turnStep() {
        LocalPlayer p = player();
        if (p == null || lastStep == clock) return;   // one step a tick, however often the tasks ask
        Vec3 t = lookEntity != null ? (lookEntity.isAlive() ? aimPoint(lookEntity) : null) : lookTarget;
        if (t == null) return;
        lastStep = clock;
        float[] want = rotationTo(t);
        float yaw = p.getYRot() + step(Mth.wrapDegrees(want[0] - p.getYRot()));
        p.setYRot(yaw);
        p.setXRot(Mth.clamp(p.getXRot() + step(want[1] - p.getXRot()), -90f, 90f));
        p.setYHeadRot(yaw);
    }

    /** Turn the head toward a point: it gets there smoothly over a few ticks. */
    public static void lookAt(Vec3 target) {
        lookTarget = target;
        lookEntity = null;
        lookTicks = 20;
        turnStep();
    }

    public static void lookAt(Entity e) {
        lookEntity = e;
        lookTarget = null;
        lookTicks = 20;
        turnStep();
    }

    private static int holdTicks;
    private static int manualUntil;

    /** The commander asked him to turn: his own idle glances leave the head alone meanwhile. */
    public static void turnOnRequest(int ticks) {
        manualUntil = clock + ticks;
    }

    public static boolean turnedOnRequest() {
        return clock < manualUntil;
    }

    /** Turn the head there and keep it there for a while — also while following, when standing (asked to turn). */
    public static void lookAndHold(Vec3 target, int ticks) {
        lookAt(target);
        lookTicks = holdTicks = ticks;
    }

    public static void lookAndHold(Entity e, int ticks) {
        lookAt(e);
        lookTicks = holdTicks = ticks;
    }

    private static boolean walking() {
        return player().getDeltaMovement().horizontalDistanceSqr() > 0.003;
    }

    /** Called every client tick: keeps turning toward the last target (his legs steer the head while he walks). */
    static void tickLook() {
        clock++;
        if (holdTicks > 0) holdTicks--;
        if (lookTicks <= 0 || player() == null) return;
        if (Nav.busy() && (holdTicks <= 0 || walking() || Nav.pathing())) {   // never steer against a walk
            lookTicks = 0;
            return;
        }
        lookTicks--;
        turnStep();
    }

    /** Is the head turned at this point (within tolerance degrees)? Actions wait for it, like a player aiming. */
    public static boolean aimed(Vec3 target, float tolerance) {
        LocalPlayer p = player();
        float[] want = rotationTo(target);
        return Math.abs(Mth.wrapDegrees(want[0] - p.getYRot())) <= tolerance && Math.abs(want[1] - p.getXRot()) <= tolerance;
    }

    public static boolean aimed(Entity e, float tolerance) {
        return aimed(aimPoint(e), tolerance);
    }

    public static double distTo(BlockPos pos) {
        return player().position().distanceTo(Vec3.atCenterOf(pos));
    }

    public static double eyeDistTo(BlockPos pos) {
        return player().getEyePosition().distanceTo(Vec3.atCenterOf(pos));
    }

    /** The face of {@code pos} that points toward the player's eyes. */
    public static Direction faceToward(BlockPos pos) {
        Vec3 eye = player().getEyePosition();
        Vec3 c = Vec3.atCenterOf(pos);
        return Direction.getNearest(eye.x - c.x, eye.y - c.y, eye.z - c.z);
    }

    /**
     * The middle of what is really there in the block's cell. A ladder, a lever, a button or a torch is a thin part
     * at the side of an otherwise empty cell: a ray to the cell's centre passes by it and hits the wall behind.
     */
    public static Vec3 shapeCenter(BlockPos pos) {
        var shape = level().getBlockState(pos).getShape(level(), pos);
        return shape.isEmpty() ? Vec3.atCenterOf(pos) : Vec3.atLowerCornerOf(pos).add(shape.bounds().getCenter());
    }

    private static BlockHitResult ray(Vec3 to) {
        LocalPlayer p = player();
        return level().clip(new net.minecraft.world.level.ClipContext(p.getEyePosition(), to,
                net.minecraft.world.level.ClipContext.Block.OUTLINE, net.minecraft.world.level.ClipContext.Fluid.NONE, p));
    }

    /** Ray from the eyes to the block: what is actually hit (a player cannot reach through walls). */
    public static BlockHitResult sightTo(BlockPos pos) {
        BlockHitResult toShape = ray(shapeCenter(pos));
        if (toShape.getType() != net.minecraft.world.phys.HitResult.Type.MISS && toShape.getBlockPos().equals(pos)) return toShape;
        BlockHitResult toCenter = ray(Vec3.atCenterOf(pos));
        return toCenter.getType() != net.minecraft.world.phys.HitResult.Type.MISS && toCenter.getBlockPos().equals(pos) ? toCenter : toShape;
    }

    public static boolean canSee(BlockPos pos) {
        BlockHitResult h = sightTo(pos);
        return h.getType() == net.minecraft.world.phys.HitResult.Type.MISS || h.getBlockPos().equals(pos);
    }

    /** The block standing between the eyes and pos, or null if pos is in plain sight. */
    public static BlockPos obstruction(BlockPos pos) {
        BlockHitResult h = sightTo(pos);
        if (h.getType() == net.minecraft.world.phys.HitResult.Type.MISS || h.getBlockPos().equals(pos)) return null;
        return h.getBlockPos();
    }

    /** The face of pos the eyes actually see (falls back to the nearest face). */
    public static Direction seenFace(BlockPos pos) {
        BlockHitResult h = sightTo(pos);
        return h.getType() != net.minecraft.world.phys.HitResult.Type.MISS && h.getBlockPos().equals(pos) ? h.getDirection() : faceToward(pos);
    }

    /** Can the eyes see this exact face of pos (for placing a block against it)? */
    public static boolean canSeeFace(BlockPos pos, Direction face) {
        LocalPlayer p = player();
        Vec3 target = Vec3.atCenterOf(pos).add(face.getStepX() * 0.5, face.getStepY() * 0.5, face.getStepZ() * 0.5);
        BlockHitResult h = level().clip(new net.minecraft.world.level.ClipContext(p.getEyePosition(), target,
                net.minecraft.world.level.ClipContext.Block.OUTLINE, net.minecraft.world.level.ClipContext.Fluid.NONE, p));
        return h.getType() == net.minecraft.world.phys.HitResult.Type.MISS
                || (h.getBlockPos().equals(pos) && h.getDirection() == face);
    }

    public static BlockHitResult hit(BlockPos pos, Direction face) {
        Vec3 c = Vec3.atCenterOf(pos).add(face.getStepX() * 0.5, face.getStepY() * 0.5, face.getStepZ() * 0.5);
        return new BlockHitResult(c, face, pos, false);
    }

    public static String id(Item item) {
        return BuiltInRegistries.ITEM.getKey(item).toString();
    }

    public static String id(Block block) {
        return BuiltInRegistries.BLOCK.getKey(block).toString();
    }

    public static String id(Entity e) {
        return BuiltInRegistries.ENTITY_TYPE.getKey(e.getType()).toString();
    }

    public static String describe(ItemStack s) {
        if (s.isEmpty()) return "ничего";
        return s.getCount() + "x " + s.getHoverName().getString() + " (" + id(s.getItem()) + ")";
    }

    public static String pos(BlockPos p) {
        return p.getX() + " " + p.getY() + " " + p.getZ();
    }

    public static Player findPlayer(String name) {
        if (name == null || name.isBlank()) return null;
        for (AbstractClientPlayer ap : level().players()) {
            if (ap.getGameProfile().getName().equalsIgnoreCase(name.trim())) return ap;
        }
        return null;
    }

    /** Send chat text or a /command as the bot. */
    public static void chat(String msg) {
        LocalPlayer p = player();
        if (p == null || msg == null || msg.isBlank()) return;
        if (msg.startsWith("/")) p.connection.sendCommand(msg.substring(1));
        else p.connection.sendChat(msg.length() > 250 ? msg.substring(0, 250) : msg);
    }

    /** Close any open container screen so inventory clicks go to the player inventory. */
    public static void closeContainer() {
        LocalPlayer p = player();
        if (p.containerMenu != p.inventoryMenu) p.closeContainer();
        else if (mc().screen != null) mc().setScreen(null);
    }

    public static void send(JsonObject o) {
        BotClient.LINK.send(o);
    }

    public static void event(String name, String msg) {
        send(J.obj("type", "event", "event", name, "msg", msg));
    }
}
