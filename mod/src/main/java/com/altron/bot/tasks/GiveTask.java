package com.altron.bot.tasks;

import com.altron.bot.Nav;
import com.altron.bot.Bot;
import com.altron.bot.Inv;
import com.altron.bot.Task;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.inventory.ClickType;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;

/** Walk to a player and throw items to them (or just drop them if player is null). */
public class GiveTask extends Task {
    private final String who;
    private final Item item;
    private final int count;
    private int walk;
    private int aimWait;
    private Task walker;

    public GiveTask(String who, Item item, int count) {
        super(who == null ? "drop" : "give");
        this.who = who;
        this.item = item;
        this.count = count;
    }

    @Override
    protected Status run() {
        var p = p();
        Player target = who == null ? null : Bot.findPlayer(who);
        if (who != null) {
            if (target == null) return fail("не вижу игрока " + who);
            if (target.distanceTo(p) > 2.2) {
                // to him the smart way (up a mod ladder to his tower, through an iron door...), close: thrown from further away the things fall short
                if (walker == null) walker = new GotoTask("give", target.blockPosition(), 1, who);
                Status s = walker.tick();
                if (s == Status.RUNNING && ++walk < 20 * 180) return s;
                walker.stop();
                String why = walker.result();
                walker = null;
                if (target.distanceTo(p) > 3.0) return fail("не смог подойти к " + who + (why.isEmpty() ? "" : ": " + why));
            }
            if (walker != null) {
                walker.stop();
                walker = null;
            }
            Nav.cancel();
            Bot.lookAt(target.getEyePosition());
            if (!Bot.aimed(target.getEyePosition(), 15) && ++aimWait < 12) return Status.RUNNING;   // face him, then hand it over
        }
        Bot.closeContainer();
        int left = count <= 0 ? Integer.MAX_VALUE : count;
        int thrown = 0;
        var menu = p.inventoryMenu;
        for (int i = 0; i < 36 && left > 0; i++) {
            ItemStack s = p.getInventory().getItem(i);
            if (s.isEmpty() || (item != null && !s.is(item))) continue;
            int slot = Inv.menuSlot(i);
            if (s.getCount() <= left) {
                int n = s.getCount();
                Inv.click(menu, slot, 1, ClickType.THROW);
                left -= n;
                thrown += n;
            } else {
                while (left > 0 && !p.getInventory().getItem(i).isEmpty()) {
                    Inv.click(menu, slot, 0, ClickType.THROW);
                    left--;
                    thrown++;
                }
            }
        }
        if (thrown == 0) return fail("нет такого предмета" + (item != null ? ": " + Bot.id(item) : ""));
        return done((who != null ? "отдал " + who + ": " : "выбросил: ") + thrown + " шт." + (item != null ? " " + Bot.id(item) : ""));
    }

    @Override
    public void stop() {
        if (walker != null) walker.stop();
        if (walk > 0) Nav.cancel();
    }
}
