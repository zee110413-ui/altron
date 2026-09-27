package com.altron.bot;

import net.minecraft.client.player.LocalPlayer;

/** A multi-tick action run on the client thread. */
public abstract class Task {
    public enum Status {RUNNING, DONE, FAILED}

    protected final String name;
    /** When it began: server messages since then belong to its result. */
    public final long startedMs = System.currentTimeMillis();
    protected int age;
    protected String result = "";

    protected Task(String name) {
        this.name = name;
    }

    public final Status tick() {
        age++;
        return run();
    }

    protected abstract Status run();

    /** Called once when the task ends for any reason (finished, failed, replaced). */
    public void stop() {
    }

    /** Temporarily interrupted (e.g. to eat or fight back). */
    public void pause() {
    }

    public void resume() {
    }

    public String name() {
        return name;
    }

    public int age() {
        return age;
    }

    public String result() {
        return result;
    }

    public String progress() {
        return "";
    }

    protected Status done(String msg) {
        result = msg;
        return Status.DONE;
    }

    protected Status fail(String msg) {
        result = msg;
        return Status.FAILED;
    }

    protected static LocalPlayer p() {
        return Bot.player();
    }
}
