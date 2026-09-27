package com.altron.bot.tasks;

import com.altron.bot.Bot;
import com.altron.bot.BotClient;
import com.altron.bot.Task;
import net.minecraft.util.Mth;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.phys.Vec3;

/** Drive the vehicle/mount the bot sits in to x,z: steer with A/D and camera, hold W. */
public class DriveTask extends Task {
    private final double tx, tz;
    private Vec3 lastPos;
    private Vec3 start;
    private int reverse;

    public DriveTask(double tx, double tz) {
        super("drive");
        this.tx = tx;
        this.tz = tz;
    }

    private void keys(boolean up, boolean down, boolean left, boolean right) {
        var o = Bot.mc().options;
        o.keyUp.setDown(up);
        o.keyDown.setDown(down);
        o.keyLeft.setDown(left);
        o.keyRight.setDown(right);
    }

    @Override
    protected Status run() {
        Entity v = p().getVehicle();
        if (v == null) {
            keys(false, false, false, false);
            return fail("я не в транспорте (сначала use_entity, чтобы сесть)");
        }
        if (age == 1) {
            String empty = com.altron.bot.VehicleEnergy.problem(v);
            if (!empty.isEmpty()) {
                keys(false, false, false, false);
                return fail(empty);
            }
        }
        BotClient.keepRendering(40);   // vehicle mods may steer and move the camera while the game draws
        double dx = tx - v.getX(), dz = tz - v.getZ();
        double dist = Math.sqrt(dx * dx + dz * dz);
        if (dist < 4) {
            keys(false, false, false, false);
            return done("приехал, " + Math.round(dist) + " бл. до точки");
        }
        float targetYaw = (float) (Mth.atan2(dz, dx) * (180.0 / Math.PI)) - 90f;
        p().setYRot(targetYaw); // camera-steered mounts (horses) and many vehicles follow the view
        float diff = Mth.wrapDegrees(targetYaw - v.getYRot());
        if (reverse > 0) {
            reverse--;
            keys(false, true, diff > 0, diff < 0);
            return Status.RUNNING;
        }
        keys(true, false, diff < -10, diff > 10);
        if (start == null) start = v.position();
        if (age == 20 * 15 && v.position().distanceTo(start) < 1) {
            // not a metre in 15 seconds with the throttle held: it will not go this way
            keys(false, false, false, false);
            return fail("транспорт не двигается: нет топлива или энергии, или им управляют не так (как у игрока: W/A/S/D)");
        }
        if (age % 100 == 0) {
            Vec3 now = v.position();
            if (lastPos != null && now.distanceTo(lastPos) < 1.5) reverse = 30; // stuck: back up and turn
            lastPos = now;
        }
        if (age > 20 * 60 * 5) {
            keys(false, false, false, false);
            return fail("не доехал за 5 минут, осталось " + Math.round(dist) + " бл.");
        }
        return Status.RUNNING;
    }

    @Override
    public void stop() {
        keys(false, false, false, false);
    }

    @Override
    public String progress() {
        Entity v = p().getVehicle();
        return v == null ? "" : Math.round(Math.hypot(tx - v.getX(), tz - v.getZ())) + " бл. до точки";
    }
}
