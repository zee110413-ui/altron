package com.altron.host;

import com.altron.AltronMod;
import com.google.gson.JsonArray;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.world.item.ItemStack;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** TaCZ guns for the test course: the list of every gun with its ammo, and ready guns with rounds (by its public API). */
final class TaczCatalog {
    private TaczCatalog() {
    }

    /** [[gunId, ammoId, magazine, type]] of every TaCZ gun of the pack's gun packs. */
    static JsonArray guns() {
        JsonArray out = new JsonArray();
        try {
            Class<?> api = Class.forName("com.tacz.guns.api.TimelessAPI");
            Set<?> all = (Set<?>) api.getMethod("getAllCommonGunIndex").invoke(null);
            for (Object o : all) {
                Map.Entry<?, ?> e = (Map.Entry<?, ?>) o;
                Object index = e.getValue();
                Object data = index.getClass().getMethod("getGunData").invoke(index);
                JsonArray g = new JsonArray();
                g.add(String.valueOf(e.getKey()));
                g.add(String.valueOf(data.getClass().getMethod("getAmmoId").invoke(data)));
                g.add((Integer) data.getClass().getMethod("getAmmoAmount").invoke(data));
                g.add(String.valueOf(index.getClass().getMethod("getType").invoke(index)));
                out.add(g);
            }
        } catch (Throwable t) {
            AltronMod.LOG.warn("[Altron] TaCZ gun list failed: {}", t.toString());
        }
        return out;
    }

    /** [[tableType, itemId]] of every TaCZ workbench (gun table, ammo bench, attachment bench...). */
    static JsonArray tables() {
        JsonArray out = new JsonArray();
        for (Object[] t : tableStacks()) {
            JsonArray a = new JsonArray();
            a.add((String) t[0]);
            a.add(net.minecraft.core.registries.BuiltInRegistries.ITEM.getKey(((ItemStack) t[1]).getItem()).toString());
            out.add(a);
        }
        return out;
    }

    /** {tableType, the table's item with its type set}: placed by a player, such a table knows its recipes. */
    static List<Object[]> tableStacks() {
        List<Object[]> out = new ArrayList<>();
        try {
            Class<?> api = Class.forName("com.tacz.guns.api.TimelessAPI");
            Class<?> bb = Class.forName("com.tacz.guns.api.item.builder.BlockItemBuilder");
            for (Object o : (Set<?>) api.getMethod("getAllCommonBlockIndex").invoke(null)) {
                Map.Entry<?, ?> e = (Map.Entry<?, ?>) o;
                Object item = e.getValue().getClass().getMethod("getBlock").invoke(e.getValue());
                Object b = bb.getMethod("create", net.minecraft.world.level.ItemLike.class).invoke(null, item);
                b = bb.getMethod("setId", ResourceLocation.class).invoke(b, e.getKey());
                out.add(new Object[]{String.valueOf(e.getKey()), bb.getMethod("build").invoke(b)});
            }
        } catch (Throwable t) {
            AltronMod.LOG.warn("[Altron] TaCZ tables failed: {}", t.toString());
        }
        return out;
    }

    private static int magazine(String gunId) {
        try {
            Class<?> api = Class.forName("com.tacz.guns.api.TimelessAPI");
            var opt = (java.util.Optional<?>) api.getMethod("getCommonGunIndex", ResourceLocation.class).invoke(null, new ResourceLocation(gunId));
            if (opt.isEmpty()) return 0;
            Object data = opt.get().getClass().getMethod("getGunData").invoke(opt.get());
            return (Integer) data.getClass().getMethod("getAmmoAmount").invoke(data);
        } catch (Throwable t) {
            return 0;
        }
    }

    /** The gun with a full magazine, and `rounds` spare rounds of its ammo. */
    static List<ItemStack> build(String gunId, String ammoId, int rounds) {
        List<ItemStack> out = new ArrayList<>();
        try {
            Class<?> gb = Class.forName("com.tacz.guns.api.item.builder.GunItemBuilder");
            Object b = gb.getMethod("create").invoke(null);
            b = gb.getMethod("setId", ResourceLocation.class).invoke(b, new ResourceLocation(gunId));
            b = gb.getMethod("setAmmoCount", int.class).invoke(b, magazine(gunId));
            b = gb.getMethod("setAmmoInBarrel", boolean.class).invoke(b, true);
            out.add((ItemStack) gb.getMethod("build").invoke(b));
            if (rounds > 0 && !ammoId.isBlank()) {
                Class<?> ab = Class.forName("com.tacz.guns.api.item.builder.AmmoItemBuilder");
                while (rounds > 0) {
                    int n = Math.min(rounds, 60);
                    Object a = ab.getMethod("create").invoke(null);
                    a = ab.getMethod("setId", ResourceLocation.class).invoke(a, new ResourceLocation(ammoId));
                    a = ab.getMethod("setCount", int.class).invoke(a, n);
                    out.add((ItemStack) ab.getMethod("build").invoke(a));
                    rounds -= n;
                }
            }
        } catch (Throwable t) {
            AltronMod.LOG.warn("[Altron] TaCZ gun {} failed: {}", gunId, t.toString());
        }
        return out;
    }
}
