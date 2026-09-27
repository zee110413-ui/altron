package com.altron.bot.tasks;

import com.altron.bot.Bot;
import com.altron.bot.Creative;
import com.altron.bot.Info;
import com.altron.bot.Inv;
import com.altron.bot.Task;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.world.inventory.AbstractContainerMenu;
import net.minecraft.world.inventory.ClickType;
import net.minecraft.world.inventory.Slot;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.entity.BlockEntity;
import net.minecraft.world.level.block.state.BlockState;

import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Make an item in a mod machine standing in the world (HBM press, HBM assembly machine...), like a player:
 * walk to it, open it by any side he can see (a big machine is a shell around its core), choose the recipe on machines
 * with a recipe list, put in exactly the inputs (stamp, fuel, materials), give it power if it has none, wait, take the products.
 */
public class MachineTask extends Task {
    /**
     * What goes in: slot = the machine's own slot number (-1: any slot that takes it); back = take the rest back at the end;
     * alts = things of the same kind the machine may already hold in that slot (its own plate stamp) — then it uses those.
     */
    public record Input(Item item, int count, int slot, boolean back, Set<Item> alts) {
    }

    private final Set<Block> machines;
    private final Item product;
    private final int count;
    private final ResourceLocation recipe;
    private final List<Input> inputs;
    private Task sub;
    private int stage;          // 0 find, 1 walking, 2 opening, 3 recipe, 4 loading, 5 working
    private BlockPos core;
    private int mark;
    private int idleSince;
    private int lastMade;
    private int retries;
    private int startCount = -1;
    private boolean powerTried;
    private final List<String> notes = new ArrayList<>();
    private final List<Input> removed = new ArrayList<>();   // the machine's own things he had to take out: they go back
    private final Map<Item, Integer> baseline = new HashMap<>();    // the machine's own ingredients before he loaded it
    private final Map<Item, Integer> invBefore = new HashMap<>();   // his own stock of them before loading
    private boolean finishOk;
    private boolean baselined;
    private boolean inspect;   // only look inside: what it holds and its gauges, then close
    private String finishMsg = "";

    public MachineTask(Set<Block> machines, Item product, int count, ResourceLocation recipe, List<Input> inputs) {
        super("machine");
        this.machines = machines;
        this.product = product;
        this.count = Math.max(1, count);
        this.recipe = recipe;
        this.inputs = inputs;
    }

    /** Open one machine or store, read what is inside and its gauges (energy, heat, recipe), close it. */
    public static MachineTask inspect(BlockPos pos) {
        Block b = Bot.level().getBlockState(pos).getBlock();
        MachineTask t = new MachineTask(Set.of(b), Items.AIR, 1, null, List.of());
        t.inspect = true;
        t.core = pos;
        t.stage = 1;
        t.sub = new GotoTask("walk", pos, 3);
        return t;
    }

    @Override
    protected Status run() {
        var p = p();
        if (startCount < 0) startCount = Inv.count(p, s -> s.is(product));
        if (sub != null) {
            Status s = sub.tick();
            if (s == Status.RUNNING) return s;
            sub.stop();
            Task finished = sub;
            sub = null;
            if (stage == 1) {
                // near it: click a side of the machine he can see
                BlockPos click = clickSpot();
                if (click == null) return fail("подошёл к " + machineName() + " в " + Bot.pos(core) + ", но не вижу его — он за стеной"
                        + (s == Status.FAILED ? " (" + finished.result() + ")" : ""));
                stage = 2;
                sub = new UseBlockTask(click);
                return Status.RUNNING;
            }
            if (stage == 2) {
                if (p.containerMenu == p.inventoryMenu) {
                    if (++retries <= 2) {
                        stage = 0;
                        return Status.RUNNING;
                    }
                    return fail(machineName() + " в " + Bot.pos(core) + " не открылся" + (s == Status.FAILED ? ": " + finished.result() : ""));
                }
                stage = 3;
                mark = age;
                return Status.RUNNING;
            }
        }
        if (stage == 0 && inspect) {   // one exact block to look into: back to it
            stage = 1;
            sub = new GotoTask("walk", core, 3);
            return Status.RUNNING;
        }
        if (stage == 0) {
            List<BlockPos> at = Info.findBlocks(machines, 256, 1);
            if (at.isEmpty()) return fail("не знаю, где стоит " + names() + " — сначала найди её (explore)");
            core = at.get(0);
            stage = 1;
            sub = new GotoTask("walk", core, 3);
            return Status.RUNNING;
        }
        AbstractContainerMenu menu = p.containerMenu;
        if (menu == p.inventoryMenu) {
            // the window closed (a hit, someone else): open it again
            if (++retries <= 3) {
                stage = 0;
                return Status.RUNNING;
            }
            return fail("окно " + machineName() + " закрылось");
        }
        if (stage == 3) {
            if (age - mark < 6) return Status.RUNNING;   // let the server fill the slots
            if (inspect) {
                String what = Info.container() + "\nПоказатели: " + diagnose(menu);
                Bot.closeContainer();
                return done(what);
            }
            dropCarried(menu);
            clearOutputs(menu);
            if (recipe != null) {
                String err = chooseRecipe(menu);
                if (err != null) {
                    Bot.closeContainer();
                    return fail(err);
                }
            }
            stage = 4;
            mark = age;
            return Status.RUNNING;
        }
        if (stage == 4) {
            if (age - mark < 10) return Status.RUNNING;   // the machine takes the recipe first, then its ingredients
            // what lay in the machine before is not his work: only what it makes from now on counts
            // (counted now: the server moves the old products into his inventory a few ticks after the clicks)
            // its own leftover ingredients stay its own: at the end he takes back only what he put in beyond them
            // (only the first time: after the window was reopened, what is inside is partly his already)
            if (!baselined) {
                baselined = true;
                startCount = Inv.count(p, s -> s.is(product));
                for (Input in : inputs) {
                    baseline.put(in.item(), insideAll(menu, in.item()));
                    invBefore.put(in.item(), Inv.count(p, s -> s.is(in.item())));
                }
            }
            for (Input in : inputs) {
                String err = load(menu, in);
                if (err != null) return finish(menu, false, err + " (" + diagnose(menu) + ")");
            }
            stage = 5;
            idleSince = age;
            return Status.RUNNING;
        }
        if (stage == 6) {
            // the server has confirmed what went back: now the report of what was really used
            if (age - mark < 10) return Status.RUNNING;
            Bot.closeContainer();
            String msg = finishMsg + used();
            return finishOk ? done(msg) : fail(msg);
        }
        if (age % 20 != 0) return Status.RUNNING;
        takeProducts(menu);
        int made = Inv.count(p, s -> s.is(product)) - startCount;
        if (made >= count) return finish(menu, true, "сделал в " + machineName() + ": " + made + "x " + Bot.id(product));
        if (made > lastMade || busy(menu)) idleSince = age;
        lastMade = Math.max(lastMade, made);
        topUpFuel(menu);
        // no power: put in a battery (in creative he takes a creative one from the menu)
        if (!powerTried && age - idleSince >= 60 && energy(menu) == 0) {
            powerTried = true;
            String err = power(menu);
            if (err != null) return finish(menu, false, err);
            idleSince = age;
        }
        if (age - idleSince > 20 * 45) {
            String why = diagnose(menu);
            return made > 0 ? finish(menu, true, "сделал " + made + " из " + count + "x " + Bot.id(product) + ", дальше " + machineName() + " встал (" + why + ")")
                    : finish(menu, false, machineName() + " не делает " + Bot.id(product) + ": " + why);
        }
        if (age > 20 * 60 * 20) {
            return made > 0 ? finish(menu, true, "ждал 20 минут, сделано " + made + " из " + count)
                    : finish(menu, false, "ждал 20 минут, " + machineName() + " ничего не сделал");
        }
        return Status.RUNNING;
    }

    /** Take back what is his, then (a moment later, stage 6) close and report with what was really used. */
    private Status finish(AbstractContainerMenu menu, boolean ok, String msg) {
        takeBack(menu);
        finishOk = ok;
        finishMsg = msg;
        stage = 6;
        mark = age;
        return Status.RUNNING;
    }

    private String used() {
        List<String> out = new ArrayList<>();
        for (var e : invBefore.entrySet()) {
            int d = e.getValue() - Inv.count(p(), s -> s.is(e.getKey()));
            if (d > 0) out.add(d + "x " + Bot.id(e.getKey()));
        }
        if (invBefore.isEmpty()) return "";
        return out.isEmpty() ? "; из моих материалов ничего не ушло (машина работала на том, что в ней уже лежало)"
                : "; израсходовал своих: " + String.join(", ", out);
    }

    // ------------------------------------------------------------------ opening

    /** Big machines: the core sits inside its shell; any part of it he can see opens the same window. */
    private BlockPos clickSpot() {
        var level = Bot.level();
        String ns = Bot.id(level.getBlockState(core).getBlock()).split(":")[0];
        List<BlockPos> cands = new ArrayList<>();
        for (BlockPos bp : BlockPos.betweenClosed(core.offset(-3, -1, -3), core.offset(3, 4, 3))) {
            BlockState st = level.getBlockState(bp);
            if (st.isAir()) continue;
            String id = Bot.id(st.getBlock());
            boolean part = id.startsWith(ns + ":") && id.matches(".*(machine_part|dummy|multiblock_part|_part$).*");
            if (bp.equals(core) || part || machines.contains(st.getBlock())) cands.add(bp.immutable());
        }
        cands.removeIf(bp -> Bot.eyeDistTo(bp) > 4.5 || !Bot.canSee(bp));
        cands.sort(Comparator.comparingDouble(bp -> (bp.equals(core) ? -1 : 0) + Bot.eyeDistTo(bp)));
        return cands.isEmpty() ? null : cands.get(0);
    }

    private String chooseRecipe(AbstractContainerMenu menu) {
        BlockPos bp = blockEntity(menu) instanceof BlockEntity be ? be.getBlockPos() : core;
        try {
            // what the machine's recipe list sends when a player clicks a recipe in it
            Class<?> c = Class.forName("com.hbm_m.network.SetAssemblerRecipeC2SPacket");
            c.getMethod("sendToServer", BlockPos.class, ResourceLocation.class).invoke(null, bp, recipe);
            return null;
        } catch (ClassNotFoundException e) {
            return "не умею выбирать рецепт в " + machineName();
        } catch (Exception e) {
            return "не смог выбрать рецепт " + recipe + " в " + machineName() + ": " + e;
        }
    }

    // ------------------------------------------------------------------ slots

    private boolean machineSlot(Slot s) {
        return !Inv.isPlayerSlot(p(), s);
    }

    private void dropCarried(AbstractContainerMenu menu) {
        if (menu.getCarried().isEmpty()) return;
        for (Slot s : menu.slots) {
            if (!machineSlot(s) && s.getItem().isEmpty()) {
                Inv.click(menu, s.index, 0, ClickType.PICKUP);
                return;
            }
        }
    }

    /** Leftover products of someone else block the output: take them out (the ones like his order are not counted as made). */
    private void clearOutputs(AbstractContainerMenu menu) {
        for (Slot s : menu.slots) {
            ItemStack st = s.getItem();
            if (!machineSlot(s) || st.isEmpty() || s.mayPlace(st)) continue;
            String what = Bot.describe(st);
            Inv.click(menu, s.index, 0, ClickType.QUICK_MOVE);
            notes.add((st.is(product) ? "в машине уже лежало готовое (это не я сделал), забрал: " : "забрал из машины то, что там лежало: ") + what);
        }
    }

    /** A slot that is not picky (takes even dirt) is the last choice for an ingredient. */
    private static boolean picky(Slot s) {
        return !s.mayPlace(new ItemStack(Items.DIRT));
    }

    private Slot target(AbstractContainerMenu menu, Input in, ItemStack stack) {
        Slot best = null;
        for (Slot t : menu.slots) {
            if (!machineSlot(t) || (in.slot() >= 0 && t.getContainerSlot() != in.slot())) continue;
            ItemStack cur = t.getItem();
            if (!cur.isEmpty() && ItemStack.isSameItemSameTags(cur, stack)) {
                if (cur.getCount() < t.getMaxStackSize(cur)) return t;
                continue;
            }
            if (!cur.isEmpty() || !t.mayPlace(stack)) continue;
            if (best == null || (picky(t) && !picky(best))) best = t;
        }
        return best;
    }

    private int insideAll(AbstractContainerMenu menu, Item item) {
        int n = 0;
        for (Slot s : menu.slots) if (machineSlot(s) && s.getItem().is(item)) n += s.getItem().getCount();
        return n;
    }

    /** Take k items out of a machine slot (all of it with a shift-click, part of it by hand); returns how many came out. */
    private int takeSome(AbstractContainerMenu menu, Slot s, int k) {
        int n = s.getItem().getCount();
        if (k <= 0) return 0;
        if (k >= n) {
            Inv.click(menu, s.index, 0, ClickType.QUICK_MOVE);
            int left = s.getItem().isEmpty() ? 0 : s.getItem().getCount();
            if (left < n) return n - left;
        }
        Inv.click(menu, s.index, 0, ClickType.PICKUP);                                  // the stack on the cursor
        for (int i = 0; i < n - k; i++) Inv.click(menu, s.index, 1, ClickType.PICKUP);    // the machine's part back
        int carried = menu.getCarried().getCount();
        putCarried(menu);
        if (!menu.getCarried().isEmpty()) {
            carried -= menu.getCarried().getCount();
            Inv.click(menu, s.index, 0, ClickType.PICKUP);   // no room: back into the machine
        }
        return carried;
    }

    /** What is on the cursor goes into his inventory: onto the same items first, then into free slots. */
    private void putCarried(AbstractContainerMenu menu) {
        for (int pass = 0; pass < 2 && !menu.getCarried().isEmpty(); pass++) {
            for (Slot d : menu.slots) {
                if (menu.getCarried().isEmpty()) break;
                if (!Inv.isPlayerSlot(p(), d)) continue;
                ItemStack st = d.getItem();
                boolean fits = pass == 0 ? !st.isEmpty() && ItemStack.isSameItemSameTags(st, menu.getCarried()) && st.getCount() < st.getMaxStackSize()
                        : st.isEmpty();
                if (fits) Inv.click(menu, d.index, 0, ClickType.PICKUP);
            }
        }
    }

    private int inside(AbstractContainerMenu menu, Input in) {
        int n = 0;
        for (Slot s : menu.slots) {
            if (machineSlot(s) && s.getItem().is(in.item()) && (in.slot() < 0 || s.getContainerSlot() == in.slot())) n += s.getItem().getCount();
        }
        return n;
    }

    /** Put in exactly `count`: whole stacks, then single items with right clicks. */
    private String load(AbstractContainerMenu menu, Input in) {
        var p = p();
        if (in.slot() >= 0) {
            for (Slot s : menu.slots) {
                ItemStack st = s.getItem();
                if (!machineSlot(s) || s.getContainerSlot() != in.slot() || st.isEmpty() || st.is(in.item())) continue;
                // the machine's own stamp of the right kind, its own fuel: use them instead of throwing them out
                if (in.alts().contains(st.getItem()) || (!in.back() && s.mayPlace(st))) return null;
                // someone else's thing in the slot this input needs (a stamp of another kind, other material): out for now
                removed.add(new Input(st.getItem(), st.getCount(), in.slot(), true, Set.of()));
                notes.add("вынул из машины " + Bot.describe(st) + " и вернул на место после работы");
                Inv.click(menu, s.index, 0, ClickType.QUICK_MOVE);
            }
        }
        int want = in.count() - inside(menu, in);
        for (Slot s : menu.slots) {
            if (want <= 0) break;
            if (!Inv.isPlayerSlot(p, s) || !s.getItem().is(in.item())) continue;
            Slot t = target(menu, in, s.getItem());
            if (t == null) return "в " + machineName() + " некуда положить " + Bot.id(in.item());
            int before = t.getItem().is(in.item()) ? t.getItem().getCount() : 0;
            int n = s.getItem().getCount();
            Inv.click(menu, s.index, 0, ClickType.PICKUP);                          // the stack on the cursor
            if (n <= want) {
                Inv.click(menu, t.index, 0, ClickType.PICKUP);                      // all of it in
            } else {
                for (int i = 0; i < want; i++) Inv.click(menu, t.index, 1, ClickType.PICKUP);   // one by one
            }
            if (!menu.getCarried().isEmpty()) Inv.click(menu, s.index, 0, ClickType.PICKUP);   // the rest back
            dropCarried(menu);
            int after = t.getItem().is(in.item()) ? t.getItem().getCount() : 0;
            if (after == before) return machineName() + " не принимает " + Bot.id(in.item());
            want -= after - before;
        }
        if (want > 0) return "не хватает " + want + "x " + Bot.id(in.item()) + " для " + machineName();
        return null;
    }

    private void takeProducts(AbstractContainerMenu menu) {
        for (Slot s : menu.slots) {
            if (!machineSlot(s) || !s.getItem().is(product)) continue;
            int before = s.getItem().getCount();
            Inv.click(menu, s.index, 0, ClickType.QUICK_MOVE);
            if (s.getItem().is(product) && s.getItem().getCount() == before) {
                // the machine's shift-click does not move it: carry it over by hand
                Inv.click(menu, s.index, 0, ClickType.PICKUP);
                for (Slot d : menu.slots) {
                    if (menu.getCarried().isEmpty()) break;
                    if (Inv.isPlayerSlot(p(), d) && (d.getItem().isEmpty() || ItemStack.isSameItemSameTags(d.getItem(), menu.getCarried()))) {
                        Inv.click(menu, d.index, 0, ClickType.PICKUP);
                    }
                }
                if (!menu.getCarried().isEmpty()) Inv.click(menu, s.index, 0, ClickType.PICKUP);
            }
        }
    }

    /** Fuel burns away while it works (a burner press eats ten coal a pressing): keep the fuel slot filled from the inventory. */
    private void topUpFuel(AbstractContainerMenu menu) {
        for (Input in : inputs) {
            if (in.back() || in.slot() < 0 || inside(menu, in) >= 16) continue;
            int have = Inv.count(p(), s -> s.is(in.item()));
            if (have > 0) load(menu, new Input(in.item(), inside(menu, in) + Math.min(have, 48), in.slot(), false, in.alts()));
        }
    }

    /** What was not used goes back into the inventory (a stamp is a tool: he keeps it); fuel stays burning. */
    private void takeBack(AbstractContainerMenu menu) {
        for (Input in : inputs) {
            if (!in.back()) continue;
            int extra = insideAll(menu, in.item()) - baseline.getOrDefault(in.item(), 0);
            for (Slot s : menu.slots) {
                if (extra <= 0) break;
                if (machineSlot(s) && s.getItem().is(in.item())) extra -= takeSome(menu, s, Math.min(extra, s.getItem().getCount()));
            }
        }
        // and the machine's own things he took out go back where they were
        List<Input> back = new ArrayList<>(removed);
        removed.clear();
        for (Input r : back) load(menu, r);
    }

    // ------------------------------------------------------------------ power

    private String power(AbstractContainerMenu menu) {
        var p = p();
        Item battery = null;
        int at = Inv.find(p, s -> Bot.id(s.getItem()).contains("battery"));
        if (at >= 0) battery = p.getInventory().getItem(at).getItem();
        if (battery == null && Creative.on()) {
            Item creative = BuiltInRegistries.ITEM.get(new ResourceLocation("hbm_m", "battery_creative"));
            if (creative != Items.AIR && Creative.take(creative, 1) > 0) {
                battery = creative;
                notes.add("взял творческую батарею из меню — у машины не было энергии");
            }
        }
        if (battery == null) return "у " + machineName() + " нет энергии (0), а батареи у меня нет — нужен генератор или заряженная батарея";
        Input in = new Input(battery, 1, -1, false, Set.of());
        String err = load(menu, in);
        return err == null ? null : "у " + machineName() + " нет энергии, а батарею положить не вышло: " + err;
    }

    // ------------------------------------------------------------------ the machine's own gauges (read like its window shows them)

    private static Object call(Object o, String method) {
        try {
            Method m = o.getClass().getMethod(method);
            return m.invoke(o);
        } catch (Exception e) {
            return null;
        }
    }

    private static Object blockEntity(AbstractContainerMenu menu) {
        try {
            return menu.getClass().getField("blockEntity").get(menu);
        } catch (Exception e) {
            return null;
        }
    }

    private static long energy(AbstractContainerMenu menu) {
        Object v = call(menu, "getEnergyLong");
        return v instanceof Number n ? n.longValue() : -1;
    }

    private static boolean busy(AbstractContainerMenu menu) {
        return Boolean.TRUE.equals(call(menu, "isCrafting"));
    }

    private String diagnose(AbstractContainerMenu menu) {
        List<String> out = new ArrayList<>();
        long e = energy(menu);
        if (e >= 0) out.add("энергия " + e + "/" + call(menu, "getMaxEnergyLong"));
        Object heated = call(menu, "isHeated");
        if (heated != null) out.add("нагрет: " + (Boolean.TRUE.equals(heated) ? "да" : "нет") + ", жар " + call(menu, "getBurnTime"));
        Object be = blockEntity(menu);
        if (be != null && recipe != null) out.add("выбран рецепт: " + call(be, "getSelectedRecipeId"));
        out.add("работает: " + (busy(menu) ? "да" : "нет"));
        StringBuilder slots = new StringBuilder();
        for (Slot s : menu.slots) {
            if (machineSlot(s) && !s.getItem().isEmpty()) {
                slots.append(slots.length() > 0 ? ", " : "").append(s.getContainerSlot()).append(": ").append(Bot.describe(s.getItem()));
            }
        }
        out.add("в машине: " + (slots.length() > 0 ? slots : "пусто"));
        return String.join("; ", out);
    }

    private String machineName() {
        if (core == null) return names();
        return Bot.level().getBlockState(core).getBlock().getName().getString();
    }

    private String names() {
        List<String> n = new ArrayList<>();
        for (Block b : machines) n.add(Bot.id(b));
        return String.join(" / ", n);
    }

    /** Every result says what he found in the machine and took out: the brain must not count that as made. */
    @Override
    protected Status done(String msg) {
        return super.done(msg + note());
    }

    @Override
    protected Status fail(String msg) {
        return super.fail(msg + note());
    }

    private String note() {
        return notes.isEmpty() ? "" : " (" + String.join("; ", notes) + ")";
    }

    @Override
    public void stop() {
        if (sub != null) sub.stop();
        var p = p();
        if (p != null && stage >= 4 && p.containerMenu != p.inventoryMenu) takeBack(p.containerMenu);
    }

    @Override
    public String progress() {
        var p = p();
        int made = p == null || startCount < 0 ? 0 : Inv.count(p, s -> s.is(product)) - startCount;
        return new String[]{"ищу машину", "иду к машине", "открываю", "выбираю рецепт", "закладываю", "жду"}[Math.min(stage, 5)]
                + " " + made + "/" + count;
    }
}
