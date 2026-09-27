package com.altron.bot.tasks;

import com.altron.bot.Bot;
import com.altron.bot.Input;
import com.altron.bot.Inv;
import com.altron.bot.Task;
import net.minecraft.world.item.Item;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;

/**
 * Right-click with an item in the hand, "in the air" like a player: a remote control opens its window, food is eaten,
 * a bow is drawn. Takes the item into the hand first, and says what happened (a window opened, or nothing did).
 */
public class UseItemTask extends Task {
    private final Item item;
    private final int ticks;
    private int phase;
    private int wait;
    private int countBefore;

    public UseItemTask(Item item, int ticks) {
        super("use_item");
        this.item = item;
        this.ticks = Math.max(1, ticks);
    }

    @Override
    protected Status run() {
        var p = p();
        var mc = Bot.mc();
        switch (phase) {
            case 0 -> {
                Bot.closeContainer();
                if (item != null && !Inv.holdMatching(p, s -> s.is(item))) return fail("нет в инвентаре: " + Bot.id(item));
                if (p.getMainHandItem().isEmpty()) {
                    return fail("в руке ничего нет — скажи, какой предмет использовать (параметр item)");
                }
                countBefore = p.getMainHandItem().getCount();
                phase = 1;
            }
            case 1 -> {
                // "right-click in the air": if the crosshair rests on a block, look up a little first
                if (mc.hitResult != null && mc.hitResult.getType() != HitResult.Type.MISS && ++wait < 15) {
                    Vec3 up = p.getEyePosition().add(p.getLookAngle().multiply(1, 0, 1).normalize().scale(2)).add(0, 3, 0);
                    Bot.lookAt(up);
                    return Status.RUNNING;
                }
                Input.press(mc.options.keyUse.getKey());
                phase = 2;
                wait = 0;
            }
            case 2 -> {
                if (++wait < ticks) return Status.RUNNING;
                Input.release(mc.options.keyUse.getKey());
                phase = 3;
                wait = 0;
            }
            default -> {
                if (mc.screen != null) {
                    return done("открылось окно «" + mc.screen.getTitle().getString() + "» (" + mc.screen.getClass().getSimpleName()
                            + "). Кнопки и слоты — gui info, картинка — look.");
                }
                if (++wait < 12) return Status.RUNNING;   // the server answers a moment later
                var hand = p.getMainHandItem();
                if (hand.isEmpty() || !hand.is(item != null ? item : hand.getItem()) || hand.getCount() < countBefore) {
                    return done("использовал предмет (израсходован или изменился); сейчас в руке: " + Bot.describe(hand));
                }
                return done("нажал ПКМ с " + Bot.describe(hand) + ", но окно не открылось и ничего заметного не произошло");
            }
        }
        return Status.RUNNING;
    }

    @Override
    public void stop() {
        Input.release(Bot.mc().options.keyUse.getKey());
    }
}
