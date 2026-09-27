package com.altron.bot.tasks;

import com.altron.bot.Baritone;
import com.altron.bot.BotClient;
import com.altron.bot.Combat;
import com.altron.bot.Task;

/** Fight matching entities until none are left nearby. */
public class AttackTask extends Task {
    private final Combat combat;
    private final int maxTicks;
    private int quiet;

    public AttackTask(String what, double radius, int maxSeconds) {
        super("attack");
        this.combat = new Combat(Combat.filterFor(what, BotClient.owner), radius);
        this.maxTicks = maxSeconds * 20;
    }

    @Override
    protected Status run() {
        if (combat.tick()) {
            quiet = 0;
        } else if (++quiet > 40) {
            return combat.kills > 0 ? done("бой окончен, уничтожено: " + combat.kills) : done("целей рядом нет");
        }
        if (age > maxTicks) return done("время боя вышло, уничтожено: " + combat.kills);
        return Status.RUNNING;
    }

    @Override
    public void stop() {
        combat.stop();
        Baritone.cancel();
    }

    @Override
    public String progress() {
        return "убито " + combat.kills;
    }
}
