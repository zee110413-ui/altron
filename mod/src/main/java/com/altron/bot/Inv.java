package com.altron.bot;

import net.minecraft.client.player.LocalPlayer;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.entity.ai.attributes.AttributeModifier;
import net.minecraft.world.entity.ai.attributes.Attributes;
import net.minecraft.world.inventory.AbstractContainerMenu;
import net.minecraft.world.inventory.ClickType;
import net.minecraft.world.inventory.Slot;
import net.minecraft.world.item.DiggerItem;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.block.state.BlockState;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.function.Predicate;

/** Inventory helpers. Inventory index 0-8 = hotbar, 9-35 = main inventory. */
public final class Inv {
    private Inv() {
    }

    /** InventoryMenu slot index for an inventory index. */
    public static int menuSlot(int invIndex) {
        return invIndex < 9 ? 36 + invIndex : invIndex;
    }

    public static int count(LocalPlayer p, Predicate<ItemStack> f) {
        int n = 0;
        for (int i = 0; i < 36; i++) {
            ItemStack s = p.getInventory().getItem(i);
            if (!s.isEmpty() && f.test(s)) n += s.getCount();
        }
        ItemStack off = p.getOffhandItem();
        if (!off.isEmpty() && f.test(off)) n += off.getCount();
        return n;
    }

    /** First matching inventory index, hotbar first; -1 if none. */
    public static int find(LocalPlayer p, Predicate<ItemStack> f) {
        for (int i = 0; i < 36; i++) {
            ItemStack s = p.getInventory().getItem(i);
            if (!s.isEmpty() && f.test(s)) return i;
        }
        return -1;
    }

    public static Map<String, Integer> snapshot(LocalPlayer p) {
        Map<String, Integer> m = new LinkedHashMap<>();
        for (int i = 0; i < 36; i++) {
            ItemStack s = p.getInventory().getItem(i);
            if (!s.isEmpty()) m.merge(Bot.id(s.getItem()), s.getCount(), Integer::sum);
        }
        return m;
    }

    public static String diff(Map<String, Integer> before, Map<String, Integer> after) {
        StringBuilder sb = new StringBuilder();
        for (Map.Entry<String, Integer> e : after.entrySet()) {
            int d = e.getValue() - before.getOrDefault(e.getKey(), 0);
            if (d > 0) sb.append(sb.length() > 0 ? ", " : "").append(e.getKey()).append(" +").append(d);
        }
        return sb.length() == 0 ? "ничего нового" : sb.toString();
    }

    /**
     * Put an inventory item into the hand: a tool already in the hotbar is simply selected; anything else is brought
     * into slots 2-8, where it stays put.
     */
    public static void hold(LocalPlayer p, int invIndex) {
        if (invIndex < 0) return;
        ItemStack want = p.getInventory().getItem(invIndex);
        boolean tool = want.getItem() instanceof DiggerItem;
        if (invIndex < 9 && (tool || (invIndex >= 1 && invIndex <= 7))) {
            p.getInventory().selected = invIndex;
            return;
        }
        Bot.closeContainer();
        int target = -1;
        for (int i = 1; i <= 7 && target < 0; i++) if (p.getInventory().getItem(i).isEmpty()) target = i;
        if (target < 0) target = p.getInventory().selected >= 1 && p.getInventory().selected <= 7 ? p.getInventory().selected : 7;
        p.getInventory().selected = target;
        int menuSlot = invIndex < 9 ? 36 + invIndex : invIndex;   // inventory menu: hotbar is 36-44
        Bot.mc().gameMode.handleInventoryMouseClick(p.inventoryMenu.containerId, menuSlot, target, ClickType.SWAP, p);
    }

    public static boolean holdMatching(LocalPlayer p, Predicate<ItemStack> f) {
        if (f.test(p.getMainHandItem())) return true;
        int i = find(p, f);
        if (i < 0) return false;
        hold(p, i);
        return true;
    }

    public static void click(AbstractContainerMenu menu, int slot, int button, ClickType type) {
        LocalPlayer p = Bot.player();
        Bot.mc().gameMode.handleInventoryMouseClick(menu.containerId, slot, button, type, p);
    }

    public static boolean isPlayerSlot(LocalPlayer p, Slot s) {
        return s.container == p.getInventory();
    }

    public static double attackDamage(ItemStack s) {
        double d = 0;
        for (AttributeModifier m : s.getAttributeModifiers(EquipmentSlot.MAINHAND).get(Attributes.ATTACK_DAMAGE)) {
            d += m.getAmount();
        }
        return d;
    }

    /** Pick the best tool for a block and hold it. */
    public static void holdBestTool(LocalPlayer p, BlockState state) {
        int best = -1;
        float bestSpeed = p.getMainHandItem().getDestroySpeed(state);
        for (int i = 0; i < 36; i++) {
            ItemStack s = p.getInventory().getItem(i);
            if (s.isEmpty()) continue;
            float speed = s.getDestroySpeed(state);
            if (speed > bestSpeed + 0.01f) {
                bestSpeed = speed;
                best = i;
            }
        }
        if (best >= 0) hold(p, best);
    }
}
