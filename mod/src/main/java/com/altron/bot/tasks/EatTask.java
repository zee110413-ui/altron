package com.altron.bot.tasks;

import com.altron.bot.Bot;
import com.altron.bot.Input;
import com.altron.bot.Inv;
import com.altron.bot.Task;
import net.minecraft.world.food.FoodProperties;
import net.minecraft.world.item.ItemStack;

/** Eat the best food in the inventory by holding right click. */
public class EatTask extends Task {
    private int startFood;
    private boolean eating;

    public EatTask() {
        super("eat");
    }

    @Override
    protected Status run() {
        var p = p();
        if (!eating) {
            int best = -1;
            int bestNut = 0;
            for (int i = 0; i < 36; i++) {
                ItemStack s = p.getInventory().getItem(i);
                FoodProperties f = s.getItem().getFoodProperties(s, p);
                if (f == null || f.getNutrition() <= bestNut) continue;
                // avoid harmful food (rotten flesh, spider eyes...) unless nothing else
                if (!f.getEffects().isEmpty() && bestNut > 0) continue;
                best = i;
                bestNut = f.getNutrition();
            }
            if (best < 0) return fail("нет еды в инвентаре");
            if (!p.getFoodData().needsFood()) return done("я сыт");
            Bot.closeContainer();
            Inv.hold(p, best);
            startFood = p.getFoodData().getFoodLevel();
            Input.press(Bot.mc().options.keyUse.getKey());
            eating = true;
            return Status.RUNNING;
        }
        if (p.getFoodData().getFoodLevel() > startFood && !p.isUsingItem() || age > 60) {
            Input.release(Bot.mc().options.keyUse.getKey());
            return done("поел, сытость " + p.getFoodData().getFoodLevel() + "/20");
        }
        return Status.RUNNING;
    }

    @Override
    public void stop() {
        if (eating) Input.release(Bot.mc().options.keyUse.getKey());
    }
}
