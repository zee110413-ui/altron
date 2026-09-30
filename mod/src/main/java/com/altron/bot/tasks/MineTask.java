package com.altron.bot.tasks;

import com.altron.bot.Nav;
import com.altron.bot.Bot;
import com.altron.bot.Inv;
import com.altron.bot.Memory;
import com.altron.bot.Task;
import net.minecraft.core.BlockPos;
import net.minecraft.tags.BlockTags;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.state.BlockState;

import java.util.ArrayDeque;
import java.util.Deque;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * Mine the blocks the bot has seen and remembers (and trees for wood), one by one, like a player. Where to dig when
 * none is known — a shaft, a tunnel, a cave — is the AI's own choice, with its hands (control).
 */
public class MineTask extends Task {
    private static final Item[] PICKAXES = {Items.WOODEN_PICKAXE, Items.STONE_PICKAXE, Items.IRON_PICKAXE,
            Items.DIAMOND_PICKAXE, Items.NETHERITE_PICKAXE};
    private static final String[] PICKAXE_NAMES = {"деревянная", "каменная", "железная", "алмазная", "незеритовая"};

    private final Set<Block> blocks;
    private final int count;
    private Map<String, Integer> before;
    private Deque<BlockPos> remembered;
    private Task sub;
    private int mined;
    private int refreshedAt = -1;
    private boolean collected;
    private final boolean logs;   // cutting wood: only trees, never the logs of a house

    public MineTask(List<Block> blocks, int count) {
        super("mine");
        this.blocks = new HashSet<>(blocks);
        this.count = Math.max(1, count);
        this.logs = blocks.stream().allMatch(b -> b.defaultBlockState().is(BlockTags.LOGS));
    }

    /** A log with leaves over it is a tree; a log in a wall or a floor is somebody's house. */
    private static boolean inTree(BlockPos pos) {
        var level = Bot.level();
        for (BlockPos bp : BlockPos.betweenClosed(pos.offset(-3, 0, -3), pos.offset(3, 7, 3))) {
            if (level.getBlockState(bp).is(BlockTags.LEAVES)) return true;
        }
        return false;
    }

    /** Null if the bot has a tool that gets drops from these blocks, else what it needs. */
    private String missingTool() {
        var p = p();
        for (Block b : blocks) {
            BlockState s = b.defaultBlockState();
            if (!s.requiresCorrectToolForDrops()) return null;
            for (int i = 0; i < 36; i++) {
                if (p.getInventory().getItem(i).isCorrectToolForDrops(s)) return null;
            }
        }
        BlockState s = blocks.iterator().next().defaultBlockState();
        for (int i = 0; i < PICKAXES.length; i++) {
            if (new ItemStack(PICKAXES[i]).isCorrectToolForDrops(s)) {
                return "нужна " + PICKAXE_NAMES[i] + " кирка или лучше";
            }
        }
        return "нужен особый инструмент";
    }

    private String gained() {
        return Inv.diff(before, Inv.snapshot(p()));
    }

    /** Once, at the end: pick up what fell around (the last drops, logs from a tree top). */
    private boolean collectDrops() {
        if (collected) return false;
        collected = true;
        sub = new CollectTask(8, p().blockPosition());
        return true;
    }

    @Override
    protected Status run() {
        if (before == null) {
            String tool = missingTool();
            if (tool != null) {
                return fail("не могу добыть " + blocks.stream().map(Bot::id).collect(Collectors.joining(", ")) + ": " + tool
                        + ". Попроси командира дать инструмент.");
            }
            before = Inv.snapshot(p());
            Memory.lookAround();   // what is around that he has not looked at yet
            remembered = new ArrayDeque<>(Memory.find(blocks, 96, 64));
            if (logs) remembered.removeIf(bp -> Bot.level().hasChunkAt(bp) && !inTree(bp));
        }
        if (mined >= count && sub == null) {
            Nav.cancel();
            if (collectDrops()) return Status.RUNNING;
            return done("добыто блоков: " + mined + ". Получено: " + gained());
        }
        if (sub != null) {
            Status s = sub.tick();
            if (s == Status.RUNNING) return s;
            sub.stop();
            if (sub instanceof BreakTask bt) {
                Memory.forget(bt.pos());
                if (s == Status.DONE && bt.broken != null && blocks.contains(bt.broken)) {
                    mined++;
                    sub = new CollectTask(4, bt.pos());
                    return Status.RUNNING;
                }
            }
            sub = null;
            return Status.RUNNING;
        }
        // 1) Ores the bot has already seen
        while (!remembered.isEmpty()) {
            BlockPos next = remembered.poll();
            if (Bot.level().hasChunkAt(next) && !blocks.contains(Bot.level().getBlockState(next).getBlock())
                    && Bot.distTo(next) < 5) {
                Memory.forget(next); // standing next to it: it's gone
                continue;
            }
            sub = new BreakTask(next);
            return Status.RUNNING;
        }
        // digging opens up new ore: one more look around before giving up
        if (refreshedAt != mined) {
            refreshedAt = mined;
            Memory.lookAround();
            remembered = new ArrayDeque<>(Memory.find(blocks, 96, 64));
            if (logs) remembered.removeIf(bp -> Bot.level().hasChunkAt(bp) && !inTree(bp));
            if (!remembered.isEmpty()) return Status.RUNNING;
        }
        // 2) Nothing more he knows of: where to look (a shaft, a tunnel, a cave, another forest) is the AI's decision
        if (mined > 0 && collectDrops()) return Status.RUNNING;
        String what = logs ? "деревьев (брёвна в постройках не рублю)" : "таких блоков";
        return mined > 0 ? done("больше не вижу " + what + ". Добыто: " + mined + ". Получено: " + gained())
                : fail("не вижу и не помню " + what + " поблизости — нужно поискать: explore, или копай сам (control: смотри "
                + "на блок и держи левую кнопку), руда видна, когда откроется")
                ;
    }

    @Override
    public void stop() {
        if (sub != null) sub.stop();
        Nav.cancel();
    }

    @Override
    public void pause() {
        if (sub != null) sub.pause();
        Nav.cancel();
    }

    @Override
    public void resume() {
        if (sub != null) sub.resume();
    }

    @Override
    public String progress() {
        return mined + "/" + count;
    }
}
