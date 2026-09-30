package com.altron.bot.tasks;

import com.altron.bot.Nav;
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
import net.minecraft.world.item.Items;
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
 * Craft an item via the recipe book (works for modded crafting recipes too).
 * Crafts missing intermediate parts (sticks, planks...) and finds/places a crafting table.
 */
public class CraftTask extends Task {
    private final Item item;
    private final int count;
    private final int depth;
    private CraftingRecipe recipe;
    private int startCount;
    private int phase;
    private int wait;
    private int loops;
    private int subCrafts;
    private Task sub;
    private BlockPos table;
    /** The crafting table used last (remembered like a player remembers his base). */
    private static BlockPos lastTable;
    /** Crafting tables this far away are worth walking back to, instead of making another one. */
    private static final int WALK_BACK = 96;

    public CraftTask(Item item, int count, int depth) {
        super("craft");
        this.item = item;
        this.count = Math.max(1, count);
        this.depth = depth;
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

    /** Every missing ingredient of r can itself be crafted from the inventory right now. */
    static boolean craftableInOneStep(LocalPlayer p, CraftingRecipe r) {
        for (Ingredient ing : missing(p, r).keySet()) {
            boolean ok = false;
            for (ItemStack opt : ing.getItems()) {
                for (CraftingRecipe r2 : recipesFor(opt.getItem())) {
                    if (canCraft(p, r2)) {
                        ok = true;
                        break;
                    }
                }
                if (ok) break;
            }
            if (!ok) return false;
        }
        return true;
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
        if (sub != null) {
            Status s = sub.tick();
            if (s == Status.RUNNING) return s;
            sub.stop();
            if (s == Status.FAILED) return fail(sub.result());
            sub = null;
            if (phase == 0 || phase == 1) return Status.RUNNING; // re-plan / continue with the table
        }
        switch (phase) {
            case 0 -> {
                if (depth > 3) return fail("слишком длинная цепочка крафта для " + Bot.id(item));
                List<CraftingRecipe> all = recipesFor(item);
                if (all.isEmpty()) {
                    return fail("нет рецепта верстака для " + Bot.id(item) + ". " + Info.recipes(item, 4));
                }
                recipe = null;
                for (CraftingRecipe r : all) if (canCraft(p, r)) recipe = r;
                if (recipe == null) {
                    // Craft a missing ingredient first: from what is in the inventory now,
                    // or one step further down (never an intermediate that needs the very thing we lack)
                    if (++subCrafts <= 6) {
                        for (int pass = 0; pass < (depth < 2 ? 2 : 1); pass++) {
                            for (CraftingRecipe r : all) {
                                for (Map.Entry<Ingredient, Integer> m : missing(p, r).entrySet()) {
                                    for (ItemStack opt : m.getKey().getItems()) {
                                        for (CraftingRecipe r2 : recipesFor(opt.getItem())) {
                                            if (pass == 0 ? canCraft(p, r2) : craftableInOneStep(p, r2)) {
                                                int times = (int) Math.ceil((double) count / Math.max(1, r.getResultItem(Bot.level().registryAccess()).getCount()));
                                                sub = new CraftTask(opt.getItem(), m.getValue() * times, depth + 1);
                                                return Status.RUNNING;
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                    // Report what is missing for the recipe that is closest to being possible
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
                phase = recipe.canCraftInDimensions(2, 2) ? 2 : 1;
                if (phase == 2) Bot.closeContainer();
            }
            case 1 -> {
                // Need a crafting table
                if (p.containerMenu instanceof CraftingMenu) {
                    phase = 2;
                    wait = 2;
                    loops = 0;
                    return Status.RUNNING;
                }
                if (++loops > 12) return fail("не получилось открыть верстак");
                if (table == null) {
                    List<BlockPos> tables = Info.findBlocks(Set.of(Blocks.CRAFTING_TABLE), 24, 1);
                    List<BlockPos> farther = tables.isEmpty() ? Info.findBlocks(Set.of(Blocks.CRAFTING_TABLE), WALK_BACK, 1) : tables;
                    if (!tables.isEmpty()) {
                        table = tables.get(0);
                    } else if (Inv.find(p, s -> s.is(Items.CRAFTING_TABLE)) >= 0) {
                        BlockPos spot = PlaceTask.freeSpotNear(p.blockPosition());
                        if (spot == null) return fail("некуда поставить верстак");
                        sub = new PlaceTask(Items.CRAFTING_TABLE, spot);
                        table = spot;
                        return Status.RUNNING;
                    } else if (lastTable != null && lastTable.closerToCenterThan(p.position(), WALK_BACK)
                            && (!Bot.level().hasChunkAt(lastTable) || Bot.level().getBlockState(lastTable).is(Blocks.CRAFTING_TABLE))) {
                        table = lastTable;   // the table it placed earlier: walk back to it (e.g. up from a mine)
                    } else if (!farther.isEmpty()) {
                        table = farther.get(0);   // a table it has seen at the base: no need to build another one
                    } else {
                        if (item == Items.CRAFTING_TABLE) return fail("не могу сделать верстак");
                        sub = new CraftTask(Items.CRAFTING_TABLE, 1, depth + 1);
                        return Status.RUNNING;
                    }
                }
                if (!Bot.level().getBlockState(table).is(Blocks.CRAFTING_TABLE)
                        && Bot.level().hasChunkAt(table) && Bot.distTo(table) < 6) {
                    if (table.equals(lastTable)) lastTable = null;   // gone
                    table = null;
                    return Status.RUNNING;
                }
                lastTable = table;
                sub = new UseBlockTask(table);
                return Status.RUNNING;
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
        if (sub != null) sub.stop();
        Nav.cancel();
    }

    @Override
    public String progress() {
        return recipe == null ? "" : (have() - startCount) + "/" + count;
    }
}
