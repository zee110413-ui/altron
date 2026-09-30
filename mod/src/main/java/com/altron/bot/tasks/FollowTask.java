package com.altron.bot.tasks;

import com.altron.bot.Nav;
import com.altron.bot.Bot;
import com.altron.bot.Combat;
import com.altron.bot.Info;
import com.altron.bot.Task;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.player.Player;

/** Follow a player; in guard mode also shoot hostiles that come close to either of us. */
public class FollowTask extends Task {
    private final String who;
    private final boolean guard;
    private final Combat combat;
    private boolean fighting;
    private boolean following;
    private int lost;
    private Task walker;
    private int behind;
    private double lastDist = 999;

    public FollowTask(String who, boolean guard) {
        super(guard ? "guard" : "follow");
        this.who = who;
        this.guard = guard;
        this.combat = new Combat(e -> Info.isHostile(e) && threatens(e), 24);
    }

    private boolean threatens(Entity e) {
        Player owner = Bot.findPlayer(who);
        double dBot = e.distanceTo(p());
        double dOwner = owner == null ? 999 : e.distanceTo(owner);
        return dBot < 16 || dOwner < 12;
    }

    private void startFollow() {
        following = Nav.follow(who);
    }

    @Override
    protected Status run() {
        if (age == 1) {
            if (Bot.findPlayer(who) == null) return fail("не вижу игрока " + who + " рядом");
            startFollow();
            if (!following) return fail("не могу идти следом за " + who);
        }
        if (guard) {
            boolean engaged = combat.tick();
            if (engaged && !fighting) {
                fighting = true;
                Nav.cancel();
            } else if (!engaged && fighting) {
                fighting = false;
                combat.stop();
                startFollow();
            }
            if (fighting) return Status.RUNNING;
        }
        if (walker != null) {   // catching up the smart way: up a mod ladder, through an iron door
            Status s = walker.tick();
            if (s == Status.RUNNING && walker.age() < 20 * 90) return Status.RUNNING;
            walker.stop();
            walker = null;
            behind = 0;
            startFollow();
            return Status.RUNNING;
        }
        if (age % 40 == 0) {
            Player o = Bot.findPlayer(who);
            if (o == null) {
                if (++lost > 15) return fail("потерял игрока " + who + " из виду");
            } else {
                lost = 0;
                // his legs follow, but he is not getting closer (a ladder or a door they cannot use)
                double dist = o.distanceTo(p());
                behind = dist > 5 && dist > lastDist - 0.5 ? behind + 1 : 0;
                lastDist = dist;
                if (behind >= 3) {
                    Nav.cancel();
                    walker = new GotoTask("follow", o.blockPosition(), 2, who);
                    return Status.RUNNING;
                }
                if (!Nav.busy()) startFollow();
            }
        }
        return Status.RUNNING;
    }

    @Override
    public void stop() {
        if (walker != null) walker.stop();
        combat.stop();
        Nav.cancel();
    }

    @Override
    public void pause() {
        Nav.cancel();
    }

    @Override
    public void resume() {
        startFollow();
    }

    @Override
    public String progress() {
        return (guard ? "охраняю " : "иду за ") + who + (combat.kills > 0 ? ", убито " + combat.kills : "");
    }
}
