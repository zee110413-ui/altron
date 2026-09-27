package com.altron.bot;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.core.Direction;
import net.minecraft.world.level.block.entity.BlockEntity;
import net.minecraftforge.common.capabilities.ForgeCapabilities;

import java.lang.reflect.Method;
import java.util.Locale;

/**
 * What a player sees on a block of a factory when looking closely (a wrench or goggles would show it too): which
 * channels an EnderIO pipe carries and which of its ends pull out of a block or push into it, which sides of a Thermal
 * machine take things in or give them out, what liquid a tank holds. By reflection: those mods are not needed to build.
 */
public final class ProductionLook {
    private ProductionLook() {
    }

    public static void describe(BlockEntity be, JsonObject o) {
        try {
            conduit(be, o);
        } catch (Throwable ignored) {
        }
        try {
            sides(be, o);
        } catch (Throwable ignored) {
        }
        try {
            fluids(be, o);
        } catch (Throwable ignored) {
        }
    }

    private static Method method(Object obj, String name, Class<?>... args) {
        for (Class<?> c = obj.getClass(); c != null; c = c.getSuperclass()) {
            try {
                Method m = c.getMethod(name, args);
                m.setAccessible(true);
                return m;
            } catch (NoSuchMethodException ignored) {
            }
        }
        return null;
    }

    /** EnderIO: {"item": {"north": "extract", "east": "insert"}, "fluid": {...}, "energy": {...}} — block ends only. */
    private static void conduit(BlockEntity be, JsonObject o) throws Exception {
        if (!be.getClass().getName().contains("Conduit")) return;
        Method getBundle = method(be, "getBundle");
        if (getBundle == null) return;
        Object bundle = getBundle.invoke(be);
        Method getTypes = method(bundle, "getTypes");
        if (getTypes == null) return;
        JsonObject channels = new JsonObject();
        for (Object type : (Iterable<?>) getTypes.invoke(bundle)) {
            String kind = type.getClass().getSimpleName().replace("ConduitType", "").toLowerCase(Locale.ROOT);
            if (kind.isEmpty()) kind = "other";
            JsonObject ends = new JsonObject();
            Method state = null;
            for (Method m : bundle.getClass().getMethods()) {
                if (m.getName().equals("getConnectionState") && m.getParameterCount() == 2
                        && m.getParameterTypes()[0] == Direction.class && !m.getParameterTypes()[1].isPrimitive()) {
                    state = m;
                }
            }
            if (state != null) {
                for (Direction d : Direction.values()) {
                    Object s = state.invoke(bundle, d, type);
                    if (s == null || !s.getClass().getSimpleName().contains("Dynamic")) continue;   // a block is there
                    boolean ins = Boolean.TRUE.equals(method(s, "isInsert").invoke(s));
                    boolean ext = Boolean.TRUE.equals(method(s, "isExtract").invoke(s));
                    ends.addProperty(d.getName(), ins && ext ? "both" : ext ? "extract" : ins ? "insert" : "off");
                }
            }
            channels.add(kind, ends);
        }
        o.add("conduit", channels);
    }

    /** Thermal (CoFH): which sides take in / give out, and whether it pushes and pulls by itself. */
    private static void sides(BlockEntity be, JsonObject o) throws Exception {
        Method side = method(be, "getSideConfig", Direction.class);
        if (side == null) return;
        JsonObject sides = new JsonObject();
        for (Direction d : Direction.values()) {
            Object v = side.invoke(be, d);
            String name = v == null ? "" : v.toString().replace("SIDE_", "").toLowerCase(Locale.ROOT);
            if (!name.isEmpty() && !name.equals("none")) sides.addProperty(d.getName(), name);
        }
        if (sides.size() > 0) o.add("sides", sides);
        Method transfer = method(be, "transferControl");
        if (transfer != null) {
            Object t = transfer.invoke(be);
            Method in = method(t, "getTransferIn"), out = method(t, "getTransferOut");
            if (in != null) o.addProperty("auto_in", (Boolean) in.invoke(t));
            if (out != null) o.addProperty("auto_out", (Boolean) out.invoke(t));
        }
    }

    /** Liquids in its tanks, as the block shows them. */
    private static void fluids(BlockEntity be, JsonObject o) {
        be.getCapability(ForgeCapabilities.FLUID_HANDLER).ifPresent(h -> {
            JsonArray out = new JsonArray();
            for (int i = 0; i < h.getTanks(); i++) {
                var f = h.getFluidInTank(i);
                if (f.isEmpty()) continue;
                JsonArray e = new JsonArray();
                e.add(f.getDisplayName().getString());
                e.add(f.getAmount());
                e.add(net.minecraft.core.registries.BuiltInRegistries.FLUID.getKey(f.getFluid()).toString());
                out.add(e);
            }
            if (out.size() > 0) o.add("fluids", out);
        });
    }
}
