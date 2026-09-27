package com.altron.bot.tasks;

import com.altron.bot.Bot;
import com.altron.bot.Info;
import com.altron.bot.Inv;
import com.altron.bot.Task;
import net.minecraft.core.BlockPos;
import net.minecraft.tags.ItemTags;
import net.minecraft.world.SimpleContainer;
import net.minecraft.world.inventory.AbstractFurnaceMenu;
import net.minecraft.world.inventory.ClickType;
import net.minecraft.world.inventory.Slot;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.item.crafting.RecipeType;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraftforge.common.ForgeHooks;

import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * Smelt items like a player: go back to a furnace the bot has seen (one that can smelt this item:
 * a smoker cannot smelt ore), or craft and place one. Puts in exactly as much input and fuel as needed,
 * tops them up for big batches, waits and takes the results.
 */
public class SmeltTask extends Task {
    /** Remembered furnaces this far away are worth walking back to, instead of building another one. */
    private static final int WALK_BACK = 96;
    private static BlockPos lastFurnace;

    private final Item input;
    private final int count;
    private Set<Block> stations;
    private Task sub;
    private int stage;      // 0 plan, 1 crafting furnace, 2 placing furnace, 3 opening, 4 loading, 5 smelting
    private int loaded;
    private int taken;
    private int idle;
    private int retries;
    private int refuels;
    private BlockPos furnace;

    public SmeltTask(Item input, int count) {
        super("smelt");
        this.input = input;
        this.count = Math.max(1, count);
    }

    /** Furnace blocks that have a recipe for this item. */
    private Set<Block> stationsFor() {
        var level = Bot.level();
        var rm = level.getRecipeManager();
        SimpleContainer box = new SimpleContainer(new ItemStack(input));
        Set<Block> out = new HashSet<>();
        if (rm.getRecipeFor(RecipeType.SMELTING, box, level).isPresent()) out.add(Blocks.FURNACE);
        if (rm.getRecipeFor(RecipeType.BLASTING, box, level).isPresent()) out.add(Blocks.BLAST_FURNACE);
        if (rm.getRecipeFor(RecipeType.SMOKING, box, level).isPresent()) out.add(Blocks.SMOKER);
        return out;
    }

    @Override
    protected Status run() {
        var p = p();
        if (sub != null) {
            Status s = sub.tick();
            if (s == Status.RUNNING) return s;
            sub.stop();
            Task finished = sub;
            sub = null;
            if (s == Status.FAILED) return fail(finished.result());
            if (stage == 1) {
                stage = 2;
                sub = new PlaceTask(Items.FURNACE, null);
            } else if (stage == 2) {
                stage = 3;
                furnace = ((PlaceTask) finished).placedAt();
                sub = new UseBlockTask(furnace);
            } else if (stage == 3) {
                stage = 4;
            }
            return Status.RUNNING;
        }
        if (stage == 0) {
            if (Inv.count(p, st -> st.is(input)) == 0) return fail("нет в инвентаре: " + Bot.id(input));
            if (stations == null) stations = stationsFor();
            if (stations.isEmpty()) return fail(Bot.id(input) + " не переплавляется в печи (нужна машина мода?)");
            // the furnace used last, then the nearest one it has seen (walking back to the base like a player)
            furnace = null;
            if (lastFurnace != null && lastFurnace.closerToCenterThan(p.position(), WALK_BACK)
                    && (!Bot.level().hasChunkAt(lastFurnace) || stations.contains(Bot.level().getBlockState(lastFurnace).getBlock()))) {
                furnace = lastFurnace;
            } else {
                List<BlockPos> known = Info.findBlocks(stations, WALK_BACK, 1);
                if (!known.isEmpty()) furnace = known.get(0);
            }
            if (furnace != null) {
                stage = 3;
                sub = new UseBlockTask(furnace);
            } else if (Inv.find(p, st -> stations.contains(Block.byItem(st.getItem()))) >= 0) {
                stage = 2;
                Item own = p.getInventory().getItem(Inv.find(p, st -> stations.contains(Block.byItem(st.getItem())))).getItem();
                sub = new PlaceTask(own, null);
            } else if (stations.contains(Blocks.FURNACE)) {
                stage = 1;
                sub = new CraftTask(Items.FURNACE, 1, 1);
            } else {
                return fail("для " + Bot.id(input) + " нужна плавильная или коптильная печь, а её нет");
            }
            return Status.RUNNING;
        }
        if (!(p.containerMenu instanceof AbstractFurnaceMenu menu)) {
            if (++retries <= 2) {   // the click did not open it (lag, someone in the way): try again
                if (furnace != null && furnace.equals(lastFurnace)) lastFurnace = null;
                stage = 0;
                return Status.RUNNING;
            }
            return fail("печка не открылась");
        }
        if (stage == 4) {
            // slots: 0 input, 1 fuel, 2 result, 3.. player inventory
            ItemStack in = menu.getSlot(0).getItem();
            if (!in.isEmpty() && !in.is(input)) Inv.click(menu, 0, 0, ClickType.QUICK_MOVE);   // someone else's input: take it out
            if (!menu.getSlot(2).getItem().isEmpty()) Inv.click(menu, 2, 0, ClickType.QUICK_MOVE); // old results
            loaded = menu.getSlot(0).getItem().is(input) ? menu.getSlot(0).getItem().getCount() : 0;
            loadInput(menu);
            if (!menu.getSlot(0).getItem().is(input)) {
                p.closeContainer();
                return fail("в эту печь не кладётся " + Bot.id(input) + " (" + describe(menu) + ")");
            }
            if (!addFuel(menu) && !menu.isLit()) {
                takeBackInput(menu);
                p.closeContainer();
                return fail("нет топлива: нужен уголь, брёвна или доски");
            }
            if (furnace != null) lastFurnace = furnace;
            stage = 5;
            return Status.RUNNING;
        }
        if (age % 20 == 0) {
            ItemStack res = menu.getSlot(2).getItem();
            boolean took = !res.isEmpty();
            if (took) {
                taken += res.getCount();
                Inv.click(menu, 2, 0, ClickType.QUICK_MOVE);
                idle = 0;
            }
            if (taken >= count || (taken >= loaded && Inv.count(p, st -> st.is(input)) == 0)) {
                takeBackInput(menu);
                p.closeContainer();
                return done("переплавил " + taken + " шт.");
            }
            loadInput(menu);   // big batches: top up the input as it goes
            if (!menu.isLit() && !menu.getSlot(0).getItem().isEmpty()) {
                // out of fuel (or it never caught): add some more, a few times at most
                if (menu.getSlot(1).getItem().isEmpty() && refuels < 5 && addFuel(menu)) {
                    refuels++;
                    idle = 0;
                } else if (++idle > 3) {
                    String why = describe(menu);
                    takeBackInput(menu);
                    p.closeContainer();
                    return taken > 0 ? done("переплавил " + taken + " шт., дальше печь не горит (" + why + ")")
                            : fail("печь не плавит (" + why + ")");
                }
            } else if (menu.getSlot(0).getItem().isEmpty() && !took) {
                if (++idle > 3) {
                    takeBackInput(menu);
                    p.closeContainer();
                    return taken > 0 ? done("переплавил " + taken + " шт.") : fail("печь ничего не выдала (" + describe(menu) + ")");
                }
            } else {
                idle = 0;
            }
        }
        if (age > 20 * 60 * 15) {
            takeBackInput(menu);
            p.closeContainer();
            return done("ждал слишком долго, забрал " + taken + " шт.");
        }
        return Status.RUNNING;
    }

    /** Put in exactly as many as are still to smelt, like a player: whole stacks, then single items with right clicks. */
    private void loadInput(AbstractFurnaceMenu menu) {
        var p = p();
        ItemStack slot0 = menu.getSlot(0).getItem();
        if (!slot0.isEmpty() && !slot0.is(input)) return;
        int inside = slot0.getCount();
        int want = Math.min(count - taken, input.getMaxStackSize()) - inside;
        for (Slot s : menu.slots) {
            if (want <= 0) break;
            if (!Inv.isPlayerSlot(p, s) || !s.getItem().is(input)) continue;
            int n = s.getItem().getCount();
            if (n <= want) {
                Inv.click(menu, s.index, 0, ClickType.QUICK_MOVE);
            } else {
                Inv.click(menu, s.index, 0, ClickType.PICKUP);                          // stack on the cursor
                for (int i = 0; i < want; i++) Inv.click(menu, 0, 1, ClickType.PICKUP);  // one by one into the input
                Inv.click(menu, s.index, 0, ClickType.PICKUP);                          // the rest goes back
            }
            int now = menu.getSlot(0).getItem().is(input) ? menu.getSlot(0).getItem().getCount() : 0;
            loaded += now - inside;
            want -= now - inside;
            if (now == inside) break;   // the furnace does not take it
            inside = now;
        }
    }

    /** How good a fuel is for the bot: coal first, then wood; never tools, weapons or armor. -1 = do not burn. */
    private int fuelRank(ItemStack st) {
        if (st.isEmpty() || st.is(input) || st.isDamageableItem() || ForgeHooks.getBurnTime(st, null) <= 0) return -1;
        if (st.is(Items.COAL) || st.is(Items.CHARCOAL) || st.is(Items.COAL_BLOCK)) return 0;
        if (st.is(ItemTags.LOGS_THAT_BURN)) return 1;
        if (st.is(ItemTags.PLANKS)) return 2;
        if (st.is(Items.STICK)) return 3;
        if (st.is(Items.CRAFTING_TABLE) || st.is(Items.CHEST) || st.is(Items.BARREL)) return -1;   // work blocks
        return 4;
    }

    /** Fuel for what is left to smelt (a whole stack would be wasted); returns false if there is none. */
    private boolean addFuel(AbstractFurnaceMenu menu) {
        var p = p();
        ItemStack have = menu.getSlot(1).getItem();
        int ticksNeeded = (count - taken) * 200;
        if (!have.isEmpty()) {
            int haveTicks = have.getCount() * ForgeHooks.getBurnTime(have, null);
            if (haveTicks >= ticksNeeded) return true;
        }
        Slot best = null;
        int bestRank = Integer.MAX_VALUE;
        for (Slot s : menu.slots) {
            if (!Inv.isPlayerSlot(p, s)) continue;
            ItemStack st = s.getItem();
            if (!have.isEmpty() && !ItemStack.isSameItemSameTags(st, have)) continue;   // one kind of fuel per slot
            int r = fuelRank(st);
            if (r >= 0 && r < bestRank) {
                bestRank = r;
                best = s;
            }
        }
        if (best == null) return !have.isEmpty();
        int burn = ForgeHooks.getBurnTime(best.getItem(), null);
        int haveTicks = have.isEmpty() ? 0 : have.getCount() * burn;
        int pieces = Math.max(1, (int) Math.ceil((ticksNeeded - haveTicks) / (double) burn));
        if (pieces >= best.getItem().getCount()) {
            Inv.click(menu, best.index, 0, ClickType.QUICK_MOVE);
        } else {
            Inv.click(menu, best.index, 0, ClickType.PICKUP);
            for (int i = 0; i < pieces; i++) Inv.click(menu, 1, 1, ClickType.PICKUP);
            Inv.click(menu, best.index, 0, ClickType.PICKUP);
        }
        return !menu.getSlot(1).getItem().isEmpty();
    }

    private static String describe(AbstractFurnaceMenu menu) {
        return "вход: " + Bot.describe(menu.getSlot(0).getItem()) + ", топливо: " + Bot.describe(menu.getSlot(1).getItem())
                + ", горит: " + (menu.isLit() ? "да" : "нет");
    }

    /** Whatever was not smelted goes back into the inventory, so the next job still finds it. */
    private static void takeBackInput(AbstractFurnaceMenu menu) {
        if (!menu.getSlot(0).getItem().isEmpty()) Inv.click(menu, 0, 0, ClickType.QUICK_MOVE);
        if (!menu.getSlot(2).getItem().isEmpty()) Inv.click(menu, 2, 0, ClickType.QUICK_MOVE);
    }

    @Override
    public void stop() {
        if (sub != null) sub.stop();
        if (p() != null && p().containerMenu instanceof AbstractFurnaceMenu menu) takeBackInput(menu);
    }

    @Override
    public String progress() {
        return taken + "/" + count;
    }
}
