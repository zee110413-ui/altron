package com.altron.bot.tasks;

import com.altron.bot.Bot;
import com.altron.bot.Info;
import com.altron.bot.Input;
import com.altron.bot.Legs;
import com.altron.bot.Task;
import net.minecraft.client.KeyMapping;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.util.Mth;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.projectile.ProjectileUtil;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.EntityHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;

import java.util.ArrayList;
import java.util.List;
import java.util.function.Predicate;

/**
 * The AI's own hands on the keyboard and the mouse: hold some keys for a while, turn the head, click or hold the left
 * or the right mouse button, pick a hotbar slot — exactly what a player's fingers do, nothing decided here. Afterwards
 * the AI gets what it sees: where it stands and looks, what is under the crosshair, what is around its feet and head.
 */
public class ControlTask extends Task {
    private final List<KeyMapping> keys;
    private final int ticks;
    private final float turn;        // degrees, + to the right
    private final float tilt;        // degrees, + down
    private final Float pitch;       // or an absolute pitch
    private final String left;       // "", "click", "hold"
    private final String right;
    private final int slot;          // 1-9, or 0: keep
    private final Vec3 at;           // look at this point (the hand moves the mouse there)
    private final Predicate<Entity> track;   // or keep the crosshair on the nearest such creature
    private final String trackName;
    private float wantYaw, wantPitch;
    private BlockPos digging;
    private boolean using;
    private boolean leftDone, rightDone;   // a click is one press
    private final List<String> did = new ArrayList<>();

    public ControlTask(List<KeyMapping> keys, int ticks, float turn, float tilt, Float pitch, String left, String right, int slot,
                       Vec3 at, Predicate<Entity> track, String trackName) {
        super("control");
        this.at = at;
        this.track = track;
        this.trackName = trackName;
        this.keys = keys;
        this.ticks = Math.max(1, Math.min(ticks, 200));
        this.turn = turn;
        this.tilt = tilt;
        this.pitch = pitch;
        this.left = left == null ? "" : left;
        this.right = right == null ? "" : right;
        this.slot = slot;
    }

    /** What the crosshair is on: a creature within reach first, then a block. */
    public static HitResult aim(LocalPlayer p) {
        Minecraft mc = Bot.mc();
        double reach = mc.gameMode != null ? mc.gameMode.getPickRange() : 4.5;
        HitResult block = p.pick(reach, 1f, false);
        Vec3 eye = p.getEyePosition();
        Vec3 look = p.getViewVector(1f);
        double max = block.getType() != HitResult.Type.MISS ? block.getLocation().distanceToSqr(eye) : reach * reach;
        AABB box = p.getBoundingBox().expandTowards(look.scale(reach)).inflate(1);
        EntityHitResult e = ProjectileUtil.getEntityHitResult(p, eye, eye.add(look.scale(reach)), box,
                en -> !en.isSpectator() && en.isPickable(), max);
        return e != null ? e : block;
    }

    /** {yaw, pitch} from the eyes to a point, the way the mouse would have to go. */
    private static float[] rotationTo(LocalPlayer p, Vec3 t) {
        Vec3 eye = p.getEyePosition();
        double dx = t.x - eye.x, dy = t.y - eye.y, dz = t.z - eye.z;
        return new float[]{(float) Math.toDegrees(Math.atan2(dz, dx)) - 90f,
                Mth.clamp((float) -Math.toDegrees(Math.atan2(dy, Math.sqrt(dx * dx + dz * dz))), -90f, 90f)};
    }

    private Entity tracked(LocalPlayer p) {
        Entity best = null;
        for (Entity e : Info.entities(48, e -> e != p && track.test(e) && Info.perceives(e))) {
            if (best == null || e.distanceTo(p) < best.distanceTo(p)) best = e;
        }
        return best;
    }

    private static String blockName(BlockPos pos) {
        BlockState st = Bot.level().getBlockState(pos);
        if (st.isAir()) return "воздух";
        if (!st.getFluidState().isEmpty() && st.getCollisionShape(Bot.level(), pos).isEmpty()) {
            return st.getFluidState().getType().getFluidType().getDescription().getString();
        }
        return st.getBlock().getName().getString();
    }

    private static String side(String name, BlockPos feet) {
        return name + ": " + blockName(feet) + " / " + blockName(feet.above());
    }

    /** Everything the AI needs to steer by itself: position, facing, crosshair, hands, the cells around. */
    public static String view() {
        LocalPlayer p = Bot.player();
        BlockPos feet = Legs.feet(p);
        Direction f = p.getDirection();
        StringBuilder sb = new StringBuilder();
        sb.append(String.format("Ты: %.1f %.1f %.1f, смотришь на %s (поворот %.0f°, наклон %.0f°: + вниз), %s; здоровье %.0f/20, еда %d/20",
                p.getX(), p.getY(), p.getZ(), dirName(f), Mth.wrapDegrees(p.getYRot()), p.getXRot(),
                p.isInWater() ? "в воде" : p.onGround() ? "на земле" : "в воздухе", p.getHealth(), p.getFoodData().getFoodLevel()));
        ItemStack hand = p.getMainHandItem();
        sb.append("\nВ руке (слот ").append(p.getInventory().selected + 1).append("): ").append(hand.isEmpty() ? "пусто" : hand.getHoverName().getString());
        sb.append("\nХотбар:");
        for (int i = 0; i < 9; i++) {
            ItemStack s = p.getInventory().getItem(i);
            if (!s.isEmpty()) sb.append(" ").append(i + 1).append("=").append(s.getHoverName().getString()).append(s.getCount() > 1 ? " x" + s.getCount() : "");
        }
        HitResult h = aim(p);
        sb.append("\nПрицел: ");
        if (h instanceof EntityHitResult eh) {
            Entity en = eh.getEntity();
            sb.append(String.format("существо %s, %.1f бл.", en.getName().getString(), en.distanceTo(p)));
        } else if (h instanceof BlockHitResult bh && h.getType() == HitResult.Type.BLOCK) {
            sb.append(String.format("блок %s в %s, грань %s, %.1f бл.", blockName(bh.getBlockPos()), Bot.pos(bh.getBlockPos()),
                    bh.getDirection().getName(), bh.getLocation().distanceTo(p.getEyePosition())));
        } else {
            sb.append("ничего в досягаемости");
        }
        sb.append("\nВокруг (ноги / голова): ").append(side("впереди", feet.relative(f))).append("; ")
                .append(side("слева", feet.relative(f.getCounterClockWise()))).append("; ")
                .append(side("справа", feet.relative(f.getClockWise()))).append("; ")
                .append(side("сзади", feet.relative(f.getOpposite())))
                .append("; под ногами: ").append(blockName(feet.below())).append("; над головой: ").append(blockName(feet.above(2)));
        return sb.toString();
    }

    private static String dirName(Direction d) {
        return switch (d) {
            case NORTH -> "север (z-)";
            case SOUTH -> "юг (z+)";
            case EAST -> "восток (x+)";
            case WEST -> "запад (x-)";
            default -> d.getName();
        };
    }

    @Override
    protected Status run() {
        LocalPlayer p = p();
        Minecraft mc = Bot.mc();
        if (age == 1) {
            // a tap counts too (the inventory, drop, a mod's reload key): press it the way a keyboard does
            boolean world = !left.isEmpty() || !right.isEmpty();
            for (KeyMapping k : keys) {
                KeyMapping.click(k.getKey());
                if (k != mc.options.keyInventory) world = true;
            }
            if (world) Bot.closeContainer();   // hands in the world: an open window would swallow the input
            if (slot >= 1 && slot <= 9) {
                p.getInventory().selected = slot - 1;
                did.add("слот " + slot);
            }
            wantYaw = p.getYRot() + turn;
            wantPitch = Mth.clamp(pitch != null ? pitch : p.getXRot() + tilt, -90f, 90f);
            if (at != null) {
                float[] r = rotationTo(p, at);
                wantYaw = r[0];
                wantPitch = r[1];
            }
            Bot.turnOnRequest(ticks + 20);   // his idle glances leave the head alone meanwhile
        }
        if (track != null) {
            // the hand keeps the mouse on the creature while it moves
            Entity e = tracked(p);
            if (e != null) {
                float[] r = rotationTo(p, new Vec3(e.getX(), e.getY() + e.getBbHeight() * 0.8, e.getZ()));
                wantYaw = r[0];
                wantPitch = r[1];
                if (age == 1) did.add("веду прицел за " + e.getName().getString() + String.format(" (%.1f бл.)", e.distanceTo(p)));
            } else if (age == 1) {
                did.add("не вижу " + trackName);
            }
        }
        // the head turns like a hand moves a mouse: fast, but not in one frame
        float dy = Mth.wrapDegrees(wantYaw - p.getYRot());
        float dp = wantPitch - p.getXRot();
        if (Math.abs(dy) > 0.5f || Math.abs(dp) > 0.5f) {
            float yaw = p.getYRot() + Mth.clamp(dy, -40f, 40f);
            p.setYRot(yaw);
            p.setYHeadRot(yaw);
            p.setXRot(p.getXRot() + Mth.clamp(dp, -40f, 40f));
        }
        // the buttons act once the head is where it was told to look
        boolean aimed = Math.abs(dy) <= 40f && Math.abs(dp) <= 40f;
        for (KeyMapping k : keys) k.setDown(true);
        HitResult h = aim(p);
        if (!left.isEmpty() && aimed && (left.equals("hold") || !leftDone) && mc.gameMode != null) {
            leftDone = true;
            if (h instanceof EntityHitResult eh) {
                if (p.getAttackStrengthScale(0.5f) >= 0.9f || left.equals("click")) {
                    mc.gameMode.attack(p, eh.getEntity());
                    p.swing(InteractionHand.MAIN_HAND);
                    if (did.stream().noneMatch(s -> s.startsWith("ударил"))) did.add("ударил " + eh.getEntity().getName().getString());
                }
            } else if (h instanceof BlockHitResult bh && h.getType() == HitResult.Type.BLOCK) {
                if (!bh.getBlockPos().equals(digging)) {
                    mc.gameMode.startDestroyBlock(bh.getBlockPos(), bh.getDirection());
                    digging = bh.getBlockPos();
                } else {
                    mc.gameMode.continueDestroyBlock(bh.getBlockPos(), bh.getDirection());
                }
                p.swing(InteractionHand.MAIN_HAND);
            } else {
                p.swing(InteractionHand.MAIN_HAND);
            }
        }
        if (!right.isEmpty() && aimed && !rightDone && mc.gameMode != null) {
            rightDone = true;
            if (h instanceof EntityHitResult eh) {
                mc.gameMode.interact(p, eh.getEntity(), InteractionHand.MAIN_HAND);
            } else if (h instanceof BlockHitResult bh && h.getType() == HitResult.Type.BLOCK) {
                var r = mc.gameMode.useItemOn(p, InteractionHand.MAIN_HAND, bh);
                if (!r.consumesAction()) mc.gameMode.useItem(p, InteractionHand.MAIN_HAND);
            } else {
                mc.gameMode.useItem(p, InteractionHand.MAIN_HAND);
            }
            p.swing(InteractionHand.MAIN_HAND);
            if (right.equals("hold")) {
                mc.options.keyUse.setDown(true);   // eating, drawing a bow, charging: keep the button down
                using = true;
            }
        } else if (using) {
            mc.options.keyUse.setDown(true);
        }
        boolean clickPending = (!left.isEmpty() && !leftDone) || (!right.isEmpty() && !rightDone);
        if (age < ticks || (clickPending && age < ticks + 10)) return Status.RUNNING;   // a click waits for the turn
        release();
        return done((did.isEmpty() ? "" : String.join(", ", did) + "\n") + view());
    }

    private void release() {
        for (KeyMapping k : keys) k.setDown(false);
        Minecraft mc = Bot.mc();
        if (digging != null && mc.gameMode != null) mc.gameMode.stopDestroyBlock();
        if (using && mc.gameMode != null) {
            mc.options.keyUse.setDown(false);
            mc.gameMode.releaseUsingItem(p());
        }
        digging = null;
        using = false;
    }

    @Override
    public void stop() {
        release();
    }

    @Override
    public void pause() {
        release();
    }

    /** Key names the AI uses: forward, back, left, right, jump, sneak, sprint, or any binding (Input.find). */
    public static KeyMapping key(String name) {
        return Input.find(name);
    }

    @Override
    public String progress() {
        return "управляю руками (" + age + "/" + ticks + ")";
    }
}
