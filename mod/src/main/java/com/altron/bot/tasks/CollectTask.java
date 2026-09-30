package com.altron.bot.tasks;

import com.altron.bot.Nav;
import com.altron.bot.Bot;
import com.altron.bot.Info;
import com.altron.bot.Inv;
import com.altron.bot.Task;
import net.minecraft.core.BlockPos;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.item.ItemEntity;

import java.util.List;
import java.util.Map;

/** Walk over dropped items nearby to pick them up. */
public class CollectTask extends Task {
    private final double radius;
    private final BlockPos center;
    private Map<String, Integer> before;
    private Entity current;
    private int stuck;

    public CollectTask(double radius, BlockPos center) {
        super("collect_items");
        this.radius = radius;
        this.center = center;
    }

    @Override
    protected Status run() {
        if (before == null) before = Inv.snapshot(p());
        if (current == null || !current.isAlive()) {
            List<Entity> items = Info.entities(radius + 16, e -> e instanceof ItemEntity && e.isAlive()
                    && (center == null || e.blockPosition().distSqr(center) <= radius * radius));
            if (items.isEmpty() || age > 20 * 90) {
                Nav.cancel();
                return done("собрал: " + Inv.diff(before, Inv.snapshot(p())));
            }
            current = items.get(0);
            stuck = 0;
            Nav.gotoNear(current.blockPosition(), 0);
        }
        if (++stuck > 20 * 15) {
            current = null; // unreachable, try the next one
        } else if (stuck % 40 == 0 && !Nav.busy() && current != null) {
            Nav.gotoNear(current.blockPosition(), 0);
        }
        return Status.RUNNING;
    }

    @Override
    public void stop() {
        Nav.cancel();
    }
}
