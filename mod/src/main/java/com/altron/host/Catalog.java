package com.altron.host;

import com.altron.AltronMod;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.MenuProvider;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.Items;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.EntityBlock;
import net.minecraft.world.level.block.entity.BlockEntity;

/**
 * What the pack has, for the test course: every gun (TaCZ with its ammo, SuperbWarfare), every block with a machine
 * window, every food, every vehicle. Asked from the server once, then each one is tried by Altron in the world.
 */
final class Catalog {
    private Catalog() {
    }

    /** The item's own class (or a mod class above it) redefines use / useOn / interactLivingEntity. */
    private static boolean overridesUse(Item it) {
        for (Class<?> c = it.getClass(); c != null && c != Item.class; c = c.getSuperclass()) {
            if (c.getName().startsWith("net.minecraft.")) continue;   // vanilla bases (sword, bow...) are not the mod's mechanic
            for (var m : c.getDeclaredMethods()) {
                String n = m.getName();
                int k = m.getParameterCount();
                if ((k == 3 && (n.equals("m_7203_") || n.equals("use"))) || (k == 1 && (n.equals("m_6225_") || n.equals("useOn")))
                        || (k == 4 && (n.equals("m_6880_") || n.equals("interactLivingEntity")))) {
                    return true;
                }
            }
        }
        return false;
    }

    static JsonObject build(ServerLevel level) {
        JsonObject o = new JsonObject();
        o.add("tacz_guns", TaczCatalog.guns());
        o.add("tacz_tables", TaczCatalog.tables());
        Class<?> sbwGun = null;
        try {
            sbwGun = Class.forName("com.atsuishio.superbwarfare.item.gun.GunItem");
        } catch (Throwable ignored) {
        }
        JsonArray sbw = new JsonArray(), foods = new JsonArray(), gui = new JsonArray(), vehicles = new JsonArray(), usable = new JsonArray();
        Class<?> taczGun = null;
        try {
            taczGun = Class.forName("com.tacz.guns.api.item.IGun");
        } catch (Throwable ignored) {
        }
        int items = 0;
        for (Item it : BuiltInRegistries.ITEM) {
            items++;
            String id = BuiltInRegistries.ITEM.getKey(it).toString();
            boolean gun = (sbwGun != null && sbwGun.isInstance(it)) || (taczGun != null && taczGun.isInstance(it));
            if (sbwGun != null && sbwGun.isInstance(it)) sbw.add(id);
            if (it.isEdible()) foods.add(id);
            // items that do something on right click (in the air, on a block, on a creature): grenades, medkits,
            // tools, remotes, drones... — not guns, food and blocks (tested on their own), not world-wreckers
            if (!gun && !it.isEdible() && !(it instanceof net.minecraft.world.item.BlockItem) && !id.startsWith("minecraft:")
                    && !id.matches(".*(nuke|fatman|mirv|antimatter|black_hole|balefire|n2|tsar|schrabidium_bomb|creative|debug|admin|spawn_egg|detonator_nuclear|meteor).*")
                    && overridesUse(it)) {
                usable.add(id);
            }
        }
        for (Block b : BuiltInRegistries.BLOCK) {
            if (!(b instanceof EntityBlock eb) || b.asItem() == Items.AIR) continue;
            try {
                BlockEntity be = eb.newBlockEntity(BlockPos.ZERO, b.defaultBlockState());
                if (be instanceof MenuProvider) gui.add(BuiltInRegistries.BLOCK.getKey(b).toString());
            } catch (Throwable ignored) {
            }
        }
        for (EntityType<?> t : BuiltInRegistries.ENTITY_TYPE) {
            String id = BuiltInRegistries.ENTITY_TYPE.getKey(t).toString();
            if (id.startsWith("minecraft:")) continue;
            try {
                Entity e = t.create(level);
                if (e == null) continue;
                String cn = e.getClass().getName().toLowerCase(java.util.Locale.ROOT);
                // a vehicle to sit in — not the missiles, bombs and shells of the vehicle mods (they blow up)
                boolean projectile = e instanceof net.minecraft.world.entity.projectile.Projectile
                        || (cn + id).matches(".*(missile|rocket|bomb|bullet|shell|projectile|torpedo|flare|shrapnel|grenade"
                        + "|agm|aim9|aim120|r60|r73|hellfire|javelin|warhead|nuke|explos).*");
                if (cn.contains("vehicle") && !projectile && !(e instanceof net.minecraft.world.entity.Mob)) vehicles.add(id);
                e.discard();
            } catch (Throwable ignored) {
            }
        }
        o.add("sbw_guns", sbw);
        o.add("foods", foods);
        o.add("gui_blocks", gui);
        o.add("vehicles", vehicles);
        o.add("usable_items", usable);
        o.addProperty("items", items);
        AltronMod.LOG.info("[Altron] catalog: {} TaCZ guns, {} SBW guns, {} foods, {} machine blocks, {} vehicles",
                o.getAsJsonArray("tacz_guns").size(), sbw.size(), foods.size(), gui.size(), vehicles.size());
        return o;
    }
}
