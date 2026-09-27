package com.altron.bot;

import com.altron.AltronMod;
import net.minecraft.client.Minecraft;

import java.lang.reflect.Field;
import java.lang.reflect.Modifier;
import java.util.function.BooleanSupplier;

/**
 * Some mods act only for a player whose game window is in front (SuperbWarfare's trigger checks "window active and
 * mouse grabbed"). Altron's window is always in the background, so while he fires such a gun the game is told it is
 * in front. Both flags are set together, so the game never really grabs (or moves) the mouse of the PC.
 */
public final class Focus {
    private static Field active, grabbed;
    private static boolean tried, on, wasActive, wasGrabbed;

    private Focus() {
    }

    /** The boolean field of obj that the getter reports (found by flipping each one for an instant). */
    private static Field find(Object obj, BooleanSupplier getter) {
        for (Field f : obj.getClass().getDeclaredFields()) {
            if (f.getType() != boolean.class || Modifier.isStatic(f.getModifiers())) continue;
            try {
                f.setAccessible(true);
                boolean old = f.getBoolean(obj);
                boolean before = getter.getAsBoolean();
                f.setBoolean(obj, !old);
                boolean after = getter.getAsBoolean();
                f.setBoolean(obj, old);
                if (before != after) return f;
            } catch (Throwable ignored) {
            }
        }
        return null;
    }

    private static boolean init() {
        if (!tried) {
            tried = true;
            Minecraft mc = Bot.mc();
            active = find(mc, mc::isWindowActive);
            grabbed = find(mc.mouseHandler, mc.mouseHandler::isMouseGrabbed);
            AltronMod.LOG.info("[Altron] window focus flags: {}", active != null && grabbed != null ? "found" : "not found");
        }
        return active != null && grabbed != null;
    }

    /** Tell the game its window is in front (true) or put back how it really was (false). */
    public static void pretend(boolean want) {
        if (want == on || !init()) return;
        Minecraft mc = Bot.mc();
        try {
            if (want) {
                wasGrabbed = grabbed.getBoolean(mc.mouseHandler);
                wasActive = active.getBoolean(mc);
                grabbed.setBoolean(mc.mouseHandler, true);   // first: a grab with the window "active" would take the real mouse
                active.setBoolean(mc, true);
            } else {
                active.setBoolean(mc, wasActive);
                grabbed.setBoolean(mc.mouseHandler, wasGrabbed);
            }
            on = want;
        } catch (Throwable t) {
            AltronMod.LOG.warn("[Altron] focus flags: {}", t.toString());
        }
    }
}
