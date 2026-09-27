package com.altron.bot.tasks;

import com.altron.bot.Task;

import java.util.ArrayList;
import java.util.List;
import java.util.function.Supplier;

/** Run steps one after another; each step is created when it starts. */
public class SeqTask extends Task {
    private final List<Supplier<Task>> steps;
    private final List<String> log = new ArrayList<>();
    private int index = -1;
    private Task current;

    public SeqTask(String name, List<Supplier<Task>> steps) {
        super(name);
        this.steps = steps;
    }

    @Override
    protected Status run() {
        if (current == null) {
            if (++index >= steps.size()) return done(String.join("; ", log));
            current = steps.get(index).get();
            if (current == null) return fail(String.join("; ", log) + (log.isEmpty() ? "" : "; ") + "шаг " + (index + 1) + " невозможен");
        }
        Status s = current.tick();
        if (s == Status.RUNNING) return s;
        current.stop();
        log.add(current.name() + ": " + current.result());
        Task finished = current;
        current = null;
        if (s == Status.FAILED) return fail(String.join("; ", log));
        onStepDone(finished);
        return Status.RUNNING;
    }

    /** Hook for subclasses that pass data between steps. */
    protected void onStepDone(Task finished) {
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
        return "шаг " + (index + 1) + "/" + steps.size() + (current != null ? " " + current.name() + " " + current.progress() : "");
    }
}
