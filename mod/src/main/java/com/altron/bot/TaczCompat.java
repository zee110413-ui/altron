package com.altron.bot;

import net.minecraft.client.player.LocalPlayer;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.crafting.Ingredient;
import net.minecraft.world.item.crafting.Recipe;
import net.minecraftforge.network.simple.SimpleChannel;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/** TaCZ gunsmith table: guns, ammo and attachments are crafted there, not on a crafting table. */
public final class TaczCompat {
    private static final String RECIPE_CLASS = "com.tacz.guns.crafting.GunSmithTableRecipe";
    private static final String MENU_CLASS = "com.tacz.guns.inventory.GunSmithTableMenu";

    private TaczCompat() {
    }

    public static boolean isTableRecipe(Recipe<?> r) {
        return r.getClass().getName().equals(RECIPE_CLASS);
    }

    public static boolean isTableMenuOpen(LocalPlayer p) {
        return p.containerMenu.getClass().getName().equals(MENU_CLASS);
    }

    private static String norm(String s) {
        return s.toLowerCase(Locale.ROOT).replace('ё', 'е').replaceAll("[^\\p{L}\\p{N}]", "");
    }

    public static ItemStack output(Recipe<?> r) {
        try {
            return r.getResultItem(Bot.level().registryAccess());
        } catch (Exception e) {
            return ItemStack.EMPTY;
        }
    }

    /** Gunsmith recipes whose result name or id matches the query ("ak47", "7.62", "глушитель"). */
    public static List<Recipe<?>> find(String query, int limit) {
        List<Recipe<?>> out = new ArrayList<>();
        String q = norm(query);
        if (q.isEmpty()) return out;
        for (Recipe<?> r : Bot.level().getRecipeManager().getRecipes()) {
            if (!isTableRecipe(r)) continue;
            String name = norm(output(r).getHoverName().getString());
            String id = norm(r.getId().getPath());
            if (name.contains(q) || id.contains(q)) {
                out.add(r);
                if (out.size() >= limit) break;
            }
        }
        return out;
    }

    /** [ingredient, count] pairs of a gunsmith recipe. */
    public static List<Object[]> inputs(Recipe<?> r) {
        List<Object[]> out = new ArrayList<>();
        try {
            List<?> list = (List<?>) r.getClass().getMethod("getInputs").invoke(r);
            for (Object in : list) {
                Ingredient ing = (Ingredient) in.getClass().getMethod("getIngredient").invoke(in);
                int n = (Integer) in.getClass().getMethod("getCount").invoke(in);
                out.add(new Object[]{ing, n});
            }
        } catch (Exception ignored) {
        }
        return out;
    }

    public static String ingredients(Recipe<?> r) {
        StringBuilder sb = new StringBuilder();
        for (Object[] in : inputs(r)) {
            ItemStack[] opts = ((Ingredient) in[0]).getItems();
            if (opts.length == 0) continue;
            sb.append(sb.length() > 0 ? ", " : "").append(in[1]).append("x ").append(opts[0].getHoverName().getString())
                    .append(" (").append(Bot.id(opts[0].getItem())).append(")");
        }
        return sb.length() == 0 ? "(нет данных)" : sb.toString();
    }

    /** What is missing for one craft, or "" if everything is in the inventory. */
    public static String missing(LocalPlayer p, Recipe<?> r) {
        StringBuilder sb = new StringBuilder();
        for (Object[] in : inputs(r)) {
            Ingredient ing = (Ingredient) in[0];
            int need = (Integer) in[1];
            int have = Inv.count(p, ing);
            if (have < need) {
                ItemStack[] opts = ing.getItems();
                String name = opts.length > 0 ? opts[0].getHoverName().getString() : "?";
                sb.append(sb.length() > 0 ? ", " : "").append(need - have).append("x ").append(name);
            }
        }
        return sb.toString();
    }

    /** Ask the server to craft (the gunsmith table screen does the same). */
    public static boolean craft(LocalPlayer p, Recipe<?> r) {
        try {
            ResourceLocation id = r.getId();
            Object msg = Class.forName("com.tacz.guns.network.message.ClientMessageCraft")
                    .getConstructor(ResourceLocation.class, int.class).newInstance(id, p.containerMenu.containerId);
            SimpleChannel ch = (SimpleChannel) Class.forName("com.tacz.guns.network.NetworkHandler").getField("CHANNEL").get(null);
            ch.sendToServer(msg);
            return true;
        } catch (Exception e) {
            return false;
        }
    }
}
