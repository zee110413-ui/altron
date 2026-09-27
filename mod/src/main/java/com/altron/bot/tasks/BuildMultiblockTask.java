package com.altron.bot.tasks;

import com.altron.bot.Baritone;
import com.altron.bot.Bot;
import com.altron.bot.Inv;
import com.altron.bot.MultiblockCompat;
import com.altron.bot.Task;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.item.Item;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.levelgen.structure.templatesystem.StructureTemplate;
import net.minecraft.world.phys.Vec3;

import java.io.File;
import java.util.List;
import java.util.Map;

/**
 * Build an Immersive Engineering / Immersive Petroleum multiblock from the mod's own blueprint
 * (Baritone places the blocks) and form it with the Engineer's Hammer.
 */
public class BuildMultiblockTask extends Task {
    private static final Direction[] FACES = {Direction.SOUTH, Direction.NORTH, Direction.EAST, Direction.WEST, Direction.UP};
    private final Object multiblock;
    private BlockPos origin;
    private List<StructureTemplate.StructureBlockInfo> blocks;
    private BlockPos trigger;
    private BlockState triggerBefore;
    private int phase;
    private int idle;
    private int face;
    private int wait;
    private int lastWrong = Integer.MAX_VALUE;
    private int turns;
    private int stale;
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
                try {
                    File f = new File(Bot.mc().gameDirectory, "schematics/altron_multiblock.schem");
                    MultiblockCompat.writeSchematic(blocks, f);
                } catch (Exception e) {
                    return fail("не смог записать схему: " + e);
                }
                Baritone.setAllowPlace(true);   // building by blueprint is the one time placing blocks is wanted
                // the blueprint's exact states (fence connections, a hopper facing down, a conveyor's direction) cannot
                // be "placed" — Baritone then says materials are missing and pauses for good. The hammer checks
                // the blocks, the mod sets their connections itself: compare blocks, not their states
                Baritone.command("set buildIgnoreDirection true");
                Baritone.command("set buildIgnoreProperties east,north,south,west,up,down,waterlogged,enabled,powered,shape,"
                        + "half,axis,type,attached,in_wall,open,lit,occupied,part,hinge,rotation,facing");
                lastWrong = Integer.MAX_VALUE;
                if (!Baritone.command("build altron_multiblock.schem " + origin.getX() + " " + origin.getY() + " " + origin.getZ())) {
                    Baritone.setAllowPlace(false);
                    return fail("Baritone не принял постройку");
                }
                phase = 1;
            }
            case 1 -> {
                // no progress for a minute (Baritone "paused", something it cannot place): stop and say what is missing
                if (age % 100 == 0) {
                    int wrong = MultiblockCompat.wrongBlocks(blocks, origin).size();
                    if (wrong < lastWrong) {
                        lastWrong = wrong;
                        stale = 0;
                    } else if (++stale >= 12) {
                        Baritone.cancel();
                        Baritone.setAllowPlace(false);
                        List<BlockPos> left = MultiblockCompat.wrongBlocks(blocks, origin);
                        StringBuilder what = new StringBuilder();
                        for (int i = 0; i < Math.min(4, left.size()); i++) {
                            BlockPos at = left.get(i);
                            for (StructureTemplate.StructureBlockInfo b : blocks) {
                                if (origin.offset(b.pos()).equals(at)) what.append(i > 0 ? ", " : "").append(Bot.id(b.state().getBlock()));
                            }
                        }
                        return fail("стройка встала: осталось " + left.size() + " блоков не на месте (" + what + ") — не могу их поставить");
                    }
                }
                if (age > 60 && !Baritone.busy()) {
                    if (++idle > 40) {
                        phase = 2;
                        Baritone.setAllowPlace(false);
                    }
                } else {
                    idle = 0;
                }
                if (age > 20 * 60 * 25) {
                    Baritone.cancel();
                    return fail("строил слишком долго");
                }
            }
            case 2 -> {
                List<BlockPos> wrong = MultiblockCompat.wrongBlocks(blocks, origin);
                if (!wrong.isEmpty()) {
                    return fail("не достроил: " + wrong.size() + " блоков не на месте (первый: " + Bot.pos(wrong.get(0))
                            + ", нужен " + Bot.id(Bot.level().getBlockState(wrong.get(0)).getBlock()) + "?)");
                }
                // blocks standing the wrong way (conveyors!): Baritone placed them as it could; IE checks their
                // direction when forming — turn each with the Engineer's Hammer, like a player does
                if (turns < 40) {
                    for (StructureTemplate.StructureBlockInfo b : blocks) {
                        BlockPos at = origin.offset(b.pos());
                        BlockState have = Bot.level().getBlockState(at);
                        var prop = net.minecraft.world.level.block.state.properties.BlockStateProperties.HORIZONTAL_FACING;
                        var prop2 = net.minecraft.world.level.block.state.properties.BlockStateProperties.FACING;
                        var use = b.state().hasProperty(prop) && have.hasProperty(prop) ? prop : null;
                        boolean turnedWrong = use != null ? b.state().getValue(prop) != have.getValue(prop)
                                : b.state().hasProperty(prop2) && have.hasProperty(prop2) && b.state().getValue(prop2) != have.getValue(prop2);
                        if (!turnedWrong) continue;
                        if (Bot.eyeDistTo(at) > 4.2) {
                            sub = new GotoTask("goto", at, 2);
                            return Status.RUNNING;
                        }
                        Inv.holdMatching(p, s -> s.is(hammer()));
                        Bot.lookAt(Vec3.atCenterOf(at));
                        Bot.mc().gameMode.useItemOn(p, InteractionHand.MAIN_HAND, Bot.hit(at, Direction.UP));
                        p.swing(InteractionHand.MAIN_HAND);
                        turns++;
                        wait = 4;
                        phase = 4;   // wait a moment, then look again
                        return Status.RUNNING;
                    }
                }
                BlockPos t = origin.offset(trigger);
                if (Bot.eyeDistTo(t) > 4.2) {
                    sub = new GotoTask("goto", t, 2);
                    return Status.RUNNING;
                }
                trigger = t;
                triggerBefore = Bot.level().getBlockState(t);
                Inv.holdMatching(p, s -> s.is(hammer()));
                phase = 3;
            }
            case 3 -> {
                if (wait > 0) {
                    wait--;
                    return Status.RUNNING;
                }
                if (Bot.level().getBlockState(trigger) != triggerBefore) {
                    // the block the hammer formed it at: its window opens there (a corner of the blueprint may be inside)
                    return done("построил и собрал " + MultiblockCompat.name(multiblock) + " в " + Bot.pos(origin)
                            + "; открыть её: " + Bot.pos(trigger));
                }
                if (face >= FACES.length) {
                    return fail("постройка стоит в " + Bot.pos(origin) + ", но молот её не собрал (проверь по руководству IE)");
                }
                Direction d = FACES[face++];
                Bot.lookAt(Vec3.atCenterOf(trigger));
                Bot.mc().gameMode.useItemOn(p, InteractionHand.MAIN_HAND, Bot.hit(trigger, d));
                p.swing(InteractionHand.MAIN_HAND);
                wait = 6;
            }
            case 4 -> {   // after turning a block with the hammer
                if (wait > 0) {
                    wait--;
                    return Status.RUNNING;
                }
                phase = 2;
            }
            default -> {
            }
        }
        return Status.RUNNING;
    }

    @Override
    public void stop() {
        if (sub != null) sub.stop();
        if (phase == 1) {
            Baritone.cancel();
            Baritone.setAllowPlace(false);
        }
    }

    @Override
    public String progress() {
        return new String[]{"готовлюсь", "строю", "проверяю", "собираю молотом"}[Math.min(phase, 3)];
    }
}
