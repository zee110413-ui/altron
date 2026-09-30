package com.altron.bot.tasks;

import com.altron.bot.Bot;
import com.altron.bot.Info;
import com.altron.bot.Inv;
import com.altron.bot.Task;
import it.unimi.dsi.fastutil.ints.IntArrayList;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.world.entity.player.StackedContents;
import net.minecraft.world.inventory.AbstractContainerMenu;
import net.minecraft.world.inventory.ClickType;
import net.minecraft.world.inventory.CraftingMenu;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.crafting.CraftingRecipe;
import net.minecraft.world.item.crafting.Ingredient;
import net.minecraft.world.item.crafting.RecipeType;
import net.minecraft.world.level.block.Blocks;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Craft an item with the recipe book, like a player clicking a recipe: in the 2x2 grid of the inventory, or in a
 * crafting table the AI has opened. Making the missing parts and finding a table is the AI's own business.
 */
public class CraftTask extends Task {
    private final Item item;
    private final int count;
    private CraftingRecipe recipe;
    private int startCount;
    private int phase;
    private int wait;
    private int loops;

    public CraftTask(Item item, int count) {
        super("craft");
        this.item = item;
        this.count = Math.max(1, count);
    }

    public static List<CraftingRecipe> recipesFor(Item item) {
        var level = Bot.level();
        List<CraftingRecipe> out = new ArrayList<>();
        for (CraftingRecipe r : level.getRecipeManager().getAllRecipesFor(RecipeType.CRAFTING)) {
            if (r.isSpecial()) continue;
            ItemStack res;
            try {
                res = r.getResultItem(level.registryAccess());
            } catch (Exception e) {
                continue;
            }
            if (res != null && res.is(item)) out.add(r);
        }
        return out;
    }

    static boolean canCraft(LocalPlayer p, CraftingRecipe r) {
        StackedContents sc = new StackedContents();
        p.getInventory().fillStackedContents(sc);
        return sc.canCraft(r, new IntArrayList());
    }

    /** Ingredient -> how many are missing (for one craft). */
    static Map<Ingredient, Integer> missing(LocalPlayer p, CraftingRecipe r) {
        Map<Ingredient, Integer> need = new LinkedHashMap<>();
        List<Ingredient> uniq = new ArrayList<>();
        for (Ingredient ing : r.getIngredients()) {
            if (ing.isEmpty()) continue;
            Ingredient same = null;
            for (Ingredient u : uniq) if (sameIngredient(u, ing)) same = u;
            if (same == null) {
                uniq.add(ing);
                need.put(ing, 1);
            } else {
                need.merge(same, 1, Integer::sum);
            }
        }
        Map<Ingredient, Integer> miss = new LinkedHashMap<>();
        for (Map.Entry<Ingredient, Integer> e : need.entrySet()) {
            int have = Inv.count(p, e.getKey());
            if (have < e.getValue()) miss.put(e.getKey(), e.getValue() - have);
        }
        return miss;
    }

    private static int total(Map<Ingredient, Integer> m) {
        return m.values().stream().mapToInt(Integer::intValue).sum();
    }

    private static boolean sameIngredient(Ingredient a, Ingredient b) {
        ItemStack[] x = a.getItems(), y = b.getItems();
        if (x.length != y.length) return false;
        for (int i = 0; i < x.length; i++) if (!ItemStack.isSameItem(x[i], y[i])) return false;
        return true;
    }

    private int have() {
        return Inv.count(p(), s -> s.is(item));
    }

    @Override
    protected Status run() {
        LocalPlayer p = p();
        switch (phase) {
            case 0 -> {
                List<CraftingRecipe> all = recipesFor(item);
                if (all.isEmpty()) {
                    return fail("нет рецепта верстака для " + Bot.id(item) + ". " + Info.recipes(item, 4));
                }
                recipe = null;
                for (CraftingRecipe r : all) if (canCraft(p, r)) recipe = r;
                if (recipe == null) {
                    // what is missing for the recipe that is closest to being possible (making the parts is the AI's job)
                    Map<Ingredient, Integer> best = null;
                    for (CraftingRecipe r : all) {
                        Map<Ingredient, Integer> m = missing(p, r);
                        if (best == null || total(m) < total(best)) best = m;
                    }
                    StringBuilder miss = new StringBuilder();
                    for (Map.Entry<Ingredient, Integer> m : best.entrySet()) {
                        ItemStack[] o = m.getKey().getItems();
                        if (o.length > 0) miss.append(miss.length() > 0 ? ", " : "").append(m.getValue()).append("x ").append(Bot.id(o[0].getItem()));
                    }
                    return fail("не хватает для " + Bot.id(item) + ": " + (miss.length() == 0 ? "ингредиентов" : miss));
                }
                startCount = have();
                if (recipe.canCraftInDimensions(2, 2)) {
                    Bot.closeContainer();   // the 2x2 grid of his own inventory is enough
                } else if (!(p.containerMenu instanceof CraftingMenu)) {
                    List<BlockPos> tables = Info.findBlocks(Set.of(Blocks.CRAFTING_TABLE), 96, 1);
                    return fail(Bot.id(item) + " делается на верстаке (3x3): сначала открой верстак (use_block)"
                            + (tables.isEmpty() ? " — поставь свой или найди" : " — ближайший, что видел: " + Bot.pos(tables.get(0))));
                }
                phase = 2;
            }
            case 2 -> {
                if (wait > 0) {
                    wait--;
                    return Status.RUNNING;
                }
                int made = have() - startCount;
                if (made >= count || ++loops > 400) return finish(made);
                AbstractContainerMenu menu = p.containerMenu;
                if (!(menu instanceof CraftingMenu) && menu != p.inventoryMenu) return fail("окно крафта закрылось");
                ItemStack result = menu.getSlot(0).getItem();
                if (result.isEmpty()) {
                    if (!canCraft(p, recipe)) return made > 0 ? finish(made) : fail("кончились ингредиенты");
                    Bot.mc().gameMode.handlePlaceRecipe(menu.containerId, recipe, false);
                    wait = 3;
                } else {
                    Inv.click(menu, 0, 0, ClickType.QUICK_MOVE);
                    wait = 2;
                }
            }
            default -> {
                return fail("?");
            }
        }
        return Status.RUNNING;
    }

    private Status finish(int made) {
        LocalPlayer p = p();
        // Return anything left in the crafting grid
        AbstractContainerMenu menu = p.containerMenu;
        int gridEnd = menu instanceof CraftingMenu ? 9 : 4;
        for (int i = 1; i <= gridEnd && i < menu.slots.size(); i++) {
            if (!menu.getSlot(i).getItem().isEmpty()) Inv.click(menu, i, 0, ClickType.QUICK_MOVE);
        }
        if (menu instanceof CraftingMenu) p.closeContainer();
        return made > 0 ? done("скрафтил " + made + "x " + Bot.id(item)) : fail("не получилось скрафтить " + Bot.id(item));
    }

    @Override
    public void stop() {
    }

    @Override
    public String progress() {
        return recipe == null ? "" : (have() - startCount) + "/" + count;
    }
}
