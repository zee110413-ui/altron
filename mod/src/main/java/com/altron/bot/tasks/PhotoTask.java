package com.altron.bot.tasks;

import com.altron.bot.Bot;
import com.altron.bot.BotClient;
import com.altron.bot.Task;
import com.mojang.blaze3d.platform.NativeImage;
import net.minecraft.client.Screenshot;
import net.minecraft.core.BlockPos;
import net.minecraft.world.level.ClipContext;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;

import java.io.File;

/**
 * A picture looking straight at a point, saved as a file (brain and body run on one PC; a picture is too big for a
 * message). With `auto` (he is a spectator for a shoot) he first finds a spot like a photographer would: free, a few
 * blocks away and a little above, from where the target is seen with nothing in between — not behind a wall.
 * The window is drawn for a few frames first (it is drawn only when needed), without the hotbar, with a wider view.
 */
public class PhotoTask extends Task {
    private final Vec3 target;
    private final String file;
    private final boolean auto;
    private final int maxR;
    private boolean wasForced;
    private boolean wasHidden;
    private int wasFov = -1;
    private String where = "";

    public PhotoTask(Vec3 target, String file, boolean auto, int maxR) {
        super("photo");
        this.target = target;
        this.file = file;
        this.auto = auto;
        this.maxR = Math.max(4, Math.min(maxR, 16));
    }

    /** A free spot with a clear view of the target, preferring ~5 blocks away and 2-3 above it. */
    private Vec3 findCamera() {
        var level = Bot.level();
        BlockPos tp = BlockPos.containing(target);
        Vec3 best = null;
        double bestScore = Double.MAX_VALUE;
        for (int r = 3; r <= maxR; r++) {
            for (int dy = 1; dy <= 5; dy++) {
                for (int k = 0; k < 24; k++) {
                    double ang = 2 * Math.PI * k / 24;
                    BlockPos c = BlockPos.containing(target.x + Math.cos(ang) * r, target.y + dy, target.z + Math.sin(ang) * r);
                    if (!level.getBlockState(c).getCollisionShape(level, c).isEmpty()
                            || !level.getBlockState(c.above()).getCollisionShape(level, c.above()).isEmpty()) continue;
                    Vec3 eye = new Vec3(c.getX() + .5, c.getY() + 1.62, c.getZ() + .5);
                    BlockHitResult hit = level.clip(new ClipContext(eye, target, ClipContext.Block.OUTLINE, ClipContext.Fluid.NONE, p()));
                    boolean clear = hit.getType() == HitResult.Type.MISS || hit.getBlockPos().distSqr(tp) <= 3;
                    if (!clear) continue;
                    // light matters too: a dark nook makes a black picture
                    int light = level.getMaxLocalRawBrightness(c);
                    double score = Math.abs(r - 5.5) + Math.abs(dy - 2.5) * .6 + (15 - light) * .15;
                    if (score < bestScore) {
                        bestScore = score;
                        best = new Vec3(c.getX() + .5, c.getY(), c.getZ() + .5);
                    }
                }
            }
            if (best != null && r >= 6) break;   // good enough, no need to look farther
        }
        return best;
    }

    private void aim() {
        var p = p();
        double dx = target.x - p.getX(), dy = target.y - p.getEyeY(), dz = target.z - p.getZ();
        float yaw = (float) (Math.toDegrees(Math.atan2(dz, dx)) - 90);
        float pitch = (float) -Math.toDegrees(Math.atan2(dy, Math.hypot(dx, dz)));
        p.setYRot(yaw);
        p.setXRot(pitch);
        p.yRotO = yaw;
        p.xRotO = pitch;
        p.setYHeadRot(yaw);
        p.yHeadRotO = yaw;
        Bot.turnOnRequest(40);   // no idle looking around while the picture is taken
    }

    @Override
    protected Status run() {
        var p = p();
        if (age == 1) {
            var o = Bot.mc().options;
            wasForced = BotClient.renderForced;
            BotClient.renderForced = true;
            wasHidden = o.hideGui;
            o.hideGui = true;
            wasFov = o.fov().get();
            o.fov().set(85);
            if (auto) {   // photos.py makes him a spectator for the shoot (the client may not know it yet)
                Vec3 cam = findCamera();
                if (cam != null) {
                    p.setPos(cam.x, cam.y, cam.z);
                    p.setDeltaMovement(Vec3.ZERO);
                    where = String.format(" (снимал из %.0f %.0f %.0f)", cam.x, cam.y, cam.z);
                } else {
                    where = " (свободного места с видом не нашёл — снимал откуда стоял)";
                }
            }
        }
        if (auto) p.setDeltaMovement(Vec3.ZERO);
        aim();
        if (age < 16) return Status.RUNNING;   // a few frames with the new view (and the chunks around drawn)
        try (NativeImage img = Screenshot.takeScreenshot(Bot.mc().getMainRenderTarget())) {
            File out = new File(file);
            if (out.getParentFile() != null) out.getParentFile().mkdirs();
            img.writeToFile(out);
            return done("снимок: " + out.getAbsolutePath() + where);
        } catch (Exception e) {
            return fail("не вышло снять: " + e);
        } finally {
            restore();
        }
    }

    private void restore() {
        var o = Bot.mc().options;
        BotClient.renderForced = wasForced;
        o.hideGui = wasHidden;
        if (wasFov > 0) o.fov().set(wasFov);
        wasFov = -1;
    }

    @Override
    public void stop() {
        if (wasFov > 0) restore();
    }
}
