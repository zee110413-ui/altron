package com.altron.bot.tasks;

import com.altron.bot.Input;
import com.altron.bot.Task;
import com.mojang.blaze3d.platform.InputConstants;

/** Hold a key or mouse button for some ticks (use item, draw a bow, charge...). */
public class HoldKeyTask extends Task {
    private final InputConstants.Key key;
    private final int ticks;

    public HoldKeyTask(String name, InputConstants.Key key, int ticks) {
        super(name);
        this.key = key;
        this.ticks = Math.max(1, ticks);
    }

    @Override
    protected Status run() {
        if (age == 1) Input.press(key);
        if (age >= ticks) {
            Input.release(key);
            return done("готово");
        }
        return Status.RUNNING;
    }

    @Override
    public void stop() {
        Input.release(key);
    }
}
