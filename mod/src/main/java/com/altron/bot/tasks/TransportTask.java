package com.altron.bot.tasks;

import com.altron.bot.Bot;
import com.altron.bot.Inv;
import com.altron.bot.Task;
import net.minecraft.core.BlockPos;

/** Break a block (e.g. a full oil barrel), pick it up and place it somewhere else. */
public class TransportTask extends Task {
    private final BlockPos from;
    private final BlockPos to;
    private Task current;
    private BreakTask breaking;
    private int step;

    public TransportTask(BlockPos from, BlockPos to) {
        super("transport_block");
        this.from = from;
        this.to = to;
    }

    @Override
    protected Status run() {
        if (current == null) {
            switch (step) {
                case 0 -> current = breaking = new BreakTask(from);
                case 1 -> current = new CollectTask(4, from);
                case 2 -> {
                    if (breaking.blockItem == null || Inv.find(p(), s -> s.is(breaking.blockItem)) < 0) {
                        return fail("сломал блок, но не смог его подобрать");
                    }
                    current = new PlaceTask(breaking.blockItem, to);
                }
                default -> {
                    return done("перенёс " + Bot.id(breaking.blockItem) + " из " + Bot.pos(from) + " в " + Bot.pos(to));
                }
            }
        }
        Status s = current.tick();
        if (s == Status.RUNNING) return s;
        current.stop();
        if (s == Status.FAILED) return fail(current.result());
        if (step == 0 && breaking.broken == null) return fail("в " + Bot.pos(from) + " ничего нет");
        current = null;
        step++;
        return Status.RUNNING;
    }

    @Override
    public void stop() {
        if (current != null) current.stop();
    }

    @Override
    public void pause() {
        if (current != null) current.pause();
    }

    @Override
    public void resume() {
        if (current != null) current.resume();
    }

    @Override
    public String progress() {
        return new String[]{"иду ломать", "подбираю", "ставлю", "готово"}[Math.min(step, 3)];
    }
}
