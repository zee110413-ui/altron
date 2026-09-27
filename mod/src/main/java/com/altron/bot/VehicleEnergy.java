package com.altron.bot;

import net.minecraft.world.entity.Entity;

/**
 * SuperbWarfare vehicles (and Ash Vehicle's, built on them) run on energy (FE) and are spawned empty: a helicopter
 * with no charge does not start. Read from the vehicle itself (its public getEnergy/getMaxEnergy).
 */
public final class VehicleEnergy {
    private VehicleEnergy() {
    }

    /** {energy, max} of the vehicle (or the vehicle a seat belongs to), or null if it does not use energy. */
    public static int[] of(Entity e) {
        if (e == null) return null;
        Entity v = e.getRootVehicle();
        for (Entity x : new Entity[]{e, v}) {
            try {
                int max = (Integer) x.getClass().getMethod("getMaxEnergy").invoke(x);
                if (max <= 0) continue;
                int now = (Integer) x.getClass().getMethod("getEnergy").invoke(x);
                return new int[]{now, max};
            } catch (Throwable ignored) {
            }
        }
        return null;
    }

    /** "" if it has energy (or needs none), else what to tell the commander. */
    public static String problem(Entity e) {
        int[] en = of(e);
        if (en == null || en[0] > 0) return "";
        return "у техники нет энергии (0/" + en[1] + ") — она не заведётся. Её заряжает зарядная станция SuperbWarfare"
                + " (superbwarfare:charging_station), поставленная рядом, — станции нужно питание (генератор, FE). Аккумуляторы"
                + " SuperbWarfare выдаются пустыми и заряжаются там же";
    }

    public static String describe(Entity e) {
        int[] en = of(e);
        return en == null ? "" : " (энергия " + en[0] + "/" + en[1] + ")";
    }
}
