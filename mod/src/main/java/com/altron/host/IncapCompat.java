package com.altron.host;

import net.minecraft.world.entity.player.Player;
import net.minecraftforge.common.capabilities.Capability;

import java.lang.reflect.Method;

/**
 * The Incapacitated mod (a "killed" player first lies downed and can be got up by someone crouching next to him):
 * is this player downed, and how long until he bleeds out. By reflection — packs without the mod simply get "no".
 */
public final class IncapCompat {
    private static boolean tried;
    private static Capability<?> cap;
    private static Method isDowned, ticksLeft;

    private IncapCompat() {
    }

    private static boolean ready() {
        if (!tried) {
            tried = true;
            try {
                Class<?> holder = Class.forName("com.cartoonishvillain.incapacitated.capability.PlayerCapability");
                cap = (Capability<?>) holder.getField("INSTANCE").get(null);
                Class<?> api = Class.forName("com.cartoonishvillain.incapacitated.capability.IPlayerCapability");
                isDowned = api.getMethod("getIsIncapacitated");
                ticksLeft = api.getMethod("getTicksUntilDeath");
            } catch (Throwable t) {
                cap = null;
            }
        }
        return cap != null;
    }

    private static Object of(Player p) {
        if (!ready()) return null;
        return p.getCapability(cap).resolve().orElse(null);
    }

    public static boolean downed(Player p) {
        try {
            Object c = of(p);
            return c != null && (boolean) isDowned.invoke(c);
        } catch (Throwable t) {
            return false;
        }
    }

    /** Seconds until a downed player bleeds out (-1 if unknown). */
    public static int secondsLeft(Player p) {
        try {
            Object c = of(p);
            return c == null ? -1 : ((int) ticksLeft.invoke(c)) / 20;
        } catch (Throwable t) {
            return -1;
        }
    }
}
