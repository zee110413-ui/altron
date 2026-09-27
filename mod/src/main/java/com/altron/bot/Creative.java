package com.altron.bot;

import net.minecraft.client.player.LocalPlayer;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.GameType;

/** Creative mode: take items from the creative menu, like a player does there (the game allows it only in creative). */
public final class Creative {
    private Creative() {
    }

    public static boolean on() {
        return Bot.player() != null && Bot.mc().gameMode != null && Bot.mc().gameMode.getPlayerMode() == GameType.CREATIVE;
    }

    /** Puts `count` of the item into the inventory (topping up his stacks, then free slots); returns how many he took. */
    public static int take(Item item, int count) {
        LocalPlayer p = Bot.player();
        if (!on() || count <= 0) return 0;
        int taken = 0;
        for (int pass = 0; pass < 2 && taken < count; pass++) {
            for (int i = 0; i < 36 && taken < count; i++) {
                ItemStack s = p.getInventory().getItem(i);
                int room;
                ItemStack next;
                if (pass == 0 && s.is(item) && !s.hasTag() && s.getCount() < s.getMaxStackSize()) {
                    room = Math.min(s.getMaxStackSize() - s.getCount(), count - taken);
                    next = new ItemStack(item, s.getCount() + room);
                } else if (pass == 1 && s.isEmpty()) {
                    room = Math.min(item.getMaxStackSize(), count - taken);
                    next = new ItemStack(item, room);
                } else {
                    continue;
                }
                p.getInventory().setItem(i, next);
                Bot.mc().gameMode.handleCreativeModeItemAdd(next.copy(), Inv.menuSlot(i));
                taken += room;
            }
        }
        return taken;
    }
}
