package com.altron.bot.tasks;

import com.altron.bot.Bot;
import com.altron.bot.Inv;
import com.altron.bot.MultiblockCompat;
import com.altron.bot.Task;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.world.item.Item;
import net.minecraft.world.level.levelgen.structure.templatesystem.StructureTemplate;

import java.util.List;
import java.util.Map;

/**
 * An Immersive Engineering / Immersive Petroleum multiblock from the mod's own blueprint: check the materials, find
 * a free place and tell the AI which blocks go where and where to strike with the hammer — its own hands do the rest.
 */
public class BuildMultiblockTask extends Task {
    private final Object multiblock;
    private BlockPos origin;
    private List<StructureTemplate.StructureBlockInfo> blocks;
    private BlockPos trigger;
    private int phase;
    private Task sub;

    public BuildMultiblockTask(Object multiblock, BlockPos origin) {
        super("build_multiblock");
        this.multiblock = multiblock;
        this.origin = origin;
    }

    private static Item hammer() {
        ResourceLocation id = new ResourceLocation("immersiveengineering", "hammer");
        return BuiltInRegistries.ITEM.containsKey(id) ? BuiltInRegistries.ITEM.get(id) : null;
    }

    @Override
    protected Status run() {
        var p = p();
        if (sub != null) {
            Status s = sub.tick();
            if (s == Status.RUNNING) return s;
            sub.stop();
            sub = null;
        }
        switch (phase) {
            case 0 -> {
                try {
                    blocks = MultiblockCompat.structure(multiblock);
                    trigger = MultiblockCompat.trigger(multiblock);
                } catch (Exception e) {
                    return fail("не смог прочитать чертёж: " + e);
                }
                if (blocks.isEmpty()) return fail("чертёж пустой (зайди в мир заново, чтобы мод прислал чертежи)");
                StringBuilder miss = new StringBuilder();
                for (Map.Entry<Item, Integer> e : MultiblockCompat.materials(blocks).entrySet()) {
                    int have = Inv.count(p, s -> s.is(e.getKey()));
                    if (have < e.getValue()) miss.append(miss.length() > 0 ? ", " : "").append(e.getValue() - have).append("x ").append(Bot.id(e.getKey()));
                }
                Item hammer = hammer();
                if (hammer == null || Inv.find(p, s -> s.is(hammer)) < 0) {
                    miss.append(miss.length() > 0 ? ", " : "").append("1x immersiveengineering:hammer (инженерный молот)");
                }
                if (miss.length() > 0) return fail("для постройки " + MultiblockCompat.name(multiblock) + " не хватает: " + miss);
                if (origin == null) {
                    // find free space near the bot
                    BlockPos me = p.blockPosition();
                    search:
                    for (int r = 3; r <= 12; r += 3) {
                        for (int dx = -r; dx <= r; dx += 3) {
                            for (int dz = -r; dz <= r; dz += 3) {
                                BlockPos c = me.offset(dx, 0, dz);
                                if (MultiblockCompat.spaceFree(blocks, c)) {
                                    origin = c;
                                    break search;
                                }
                            }
                        }
                    }
                    if (origin == null) return fail("рядом нет свободного места под постройку, отведи меня на ровную площадку");
                }
                phase = 1;
            }
            case 1 -> {
                // the blocks are put in place by the AI's own hands (place_block, control); this job only checks the
                // blueprint and forms it with the hammer once every block stands where it should
                List<BlockPos> left = MultiblockCompat.wrongBlocks(blocks, origin);
                if (!left.isEmpty()) {
                    StringBuilder plan = new StringBuilder();
                    int n = 0;
                    for (StructureTemplate.StructureBlockInfo b : blocks) {
                        BlockPos at = origin.offset(b.pos());
                        if (!left.contains(at)) continue;
                        if (n++ >= 60) break;
                        plan.append(n > 1 ? "; " : "").append(Bot.pos(at)).append(" ").append(Bot.id(b.state().getBlock()));
                    }
                    return fail("чертёж " + MultiblockCompat.name(multiblock) + " в " + Bot.pos(origin) + ": поставь сам ещё "
                            + left.size() + " блоков (снизу вверх; place_block или control), потом снова build_multiblock с "
                            + "x y z = " + Bot.pos(origin) + " — соберу молотом. Блоки: " + plan
                            + (left.size() > 60 ? "; и ещё " + (left.size() - 60) : ""));
                }
                phase = 2;
            }
            case 2 -> {
                // forming it is a right click with the Engineer's Hammer — the AI's own hand does that
                return done("все блоки " + MultiblockCompat.name(multiblock) + " на месте. Собрать: молот (immersiveengineering:hammer) "
                        + "в руку и правый клик по " + Bot.pos(origin.offset(trigger)) + "; не собралась — блоки с направлением "
                        + "(конвейеры) поверни правым кликом молота и ударь снова");
            }
            default -> {
            }
        }
        return Status.RUNNING;
    }

    @Override
    public void stop() {
        if (sub != null) sub.stop();
    }

    @Override
    public String progress() {
        return new String[]{"готовлюсь", "сверяю чертёж", "проверяю"}[Math.min(phase, 2)];
    }
}
