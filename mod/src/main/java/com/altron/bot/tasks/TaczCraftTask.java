package com.altron.bot.tasks;

import com.altron.bot.Bot;
import com.altron.bot.Info;
import com.altron.bot.Inv;
import com.altron.bot.Task;
import com.altron.bot.TaczCompat;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.crafting.Recipe;
import net.minecraft.world.level.block.Block;

import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * Craft guns / ammo / attachments at a TaCZ workbench the bot has seen. TaCZ has several kinds (gun table, ammo
 * bench, attachment bench), each making only its own recipes: like a player, he tries the benches he knows one by
 * one, and counts a craft only when the thing is really in his inventory.
 */
public class TaczCraftTask extends Task {
    private final Recipe<?> recipe;
    private final int times;
    private Task sub;
    private int done;
    private int wait;
    private boolean opened;
    private List<BlockPos> tables;
    private int tableIndex;
    private int before = -1;
    private int tries;

    public TaczCraftTask(Recipe<?> recipe, int count) {
        super("craft");
        this.recipe = recipe;
        int per = Math.max(1, TaczCompat.output(recipe).getCount());
        this.times = Math.max(1, (int) Math.ceil((double) Math.max(1, count) / per));
    }

    private static Set<Block> tableBlocks() {
        Set<Block> out = new HashSet<>();
        for (Block b : BuiltInRegistries.BLOCK) {
            var id = BuiltInRegistries.BLOCK.getKey(b);
            if (id.getNamespace().equals("tacz") && (id.getPath().contains("gun_smith_table") || id.getPath().contains("workbench"))) out.add(b);
        }
        return out;
    }

    /** How many of the recipe's result he has: same item, and the same gun/ammo/attachment kind (TaCZ keeps it in NBT). */
    private int have() {
        ItemStack out = TaczCompat.output(recipe);
        return Inv.count(p(), s -> {
            if (!s.is(out.getItem())) return false;
            if (!out.hasTag()) return true;
            for (String k : new String[]{"GunId", "AmmoId", "AttachmentId", "BlockId"}) {
                if (out.getTag().contains(k) && (!s.hasTag() || !out.getTag().getString(k).equals(s.getTag().getString(k)))) return false;
            }
            return true;
        });
    }

    /** Next workbench to try, or fail when none is left. */
    private Status nextTable(String why) {
        if (Bot.player().containerMenu != Bot.player().inventoryMenu) Bot.player().closeContainer();
        opened = false;
        if (tables == null) {
            tables = Info.findBlocks(tableBlocks(), 64, 6);
            if (tables.isEmpty()) return fail("не видел поблизости оружейного верстака TaCZ. Покажи мне его или поставь.");
        }
        if (tableIndex >= tables.size()) {
            String name = TaczCompat.output(recipe).getHoverName().getString();
            return done > 0 ? done("сделал " + done + " раз(а) " + name + (why.isEmpty() ? "" : ", дальше: " + why))
                    : fail("ни один из верстаков TaCZ рядом (" + tables.size() + ") не делает " + name
                    + " — у каждого верстака свои рецепты (оружейный стол, верстак патронов, верстак обвесов)" + (why.isEmpty() ? "" : ": " + why));
        }
        sub = new UseBlockTask(tables.get(tableIndex++));
        return Status.RUNNING;
    }

    @Override
    protected Status run() {
        var p = p();
        String name = TaczCompat.output(recipe).getHoverName().getString();
        if (sub != null) {
            Status s = sub.tick();
            if (s == Status.RUNNING) return s;
            sub.stop();
            sub = null;
            if (s == Status.FAILED || !TaczCompat.isTableMenuOpen(p)) return nextTable("");
            opened = true;
            tries = 0;
        }
        if (!opened) {
            String miss = TaczCompat.missing(p, recipe);
            if (!miss.isEmpty()) return fail("для " + name + " не хватает: " + miss);
            return nextTable("");
        }
        if (!TaczCompat.isTableMenuOpen(p)) return nextTable("окно верстака закрылось");
        if (wait > 0) {
            // the server's answer: did the thing really appear in the inventory?
            if (have() > before) {
                done++;
                wait = 0;
                before = -1;
            } else if (--wait == 0) {
                // this bench does not make it: try the next one
                before = -1;
                return nextTable("");
            }
            return Status.RUNNING;
        }
        if (done >= times) {
            p.closeContainer();
            return done("сделал на оружейном верстаке: " + name + " x" + done * Math.max(1, TaczCompat.output(recipe).getCount()));
        }
        String miss = TaczCompat.missing(p, recipe);
        if (!miss.isEmpty()) {
            p.closeContainer();
            return done > 0 ? done("сделал " + done + " раз(а) " + name + ", дальше не хватает: " + miss)
                    : fail("для " + name + " не хватает: " + miss);
        }
        before = have();
        if (!TaczCompat.craft(p, recipe)) return fail("TaCZ не принял команду крафта");
        wait = 20;
        return Status.RUNNING;
    }

    @Override
    public void stop() {
        if (sub != null) sub.stop();
    }

    @Override
    public String progress() {
        return done + "/" + times;
    }
}
