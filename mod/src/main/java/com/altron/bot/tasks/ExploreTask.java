package com.altron.bot.tasks;

import com.altron.bot.Nav;
import com.altron.bot.Bot;
import com.altron.bot.Memory;
import com.altron.bot.Names;
import com.altron.bot.Task;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.tags.BlockTags;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.phys.Vec3;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * Search the land like a person looking for something: walk out in a widening spiral, turn the head all around at
 * every stop (line of sight only), go INTO the buildings met on the way (machines or doors seen: step inside, look
 * around every room) and stop as soon as the wanted thing is seen. "assembler" means any assembler-like machine
 * (hbm_m:machine_assembler...), not one exact id. With a biome ("desert") it first heads for that biome.
 * With neither: a survey — every building around is looked through (to learn a base: what stands where).
 */
public class ExploreTask extends Task {
    private final Set<Block> targets = new HashSet<>();
    private final String biome;
    private final int radius;
    private final List<Vec3> points = new ArrayList<>();
    private final Set<Long> visited = new HashSet<>();
    private BlockPos center;
    private BlockPos inside;       // a building being looked through
    private int point = -1;
    private int walkTicks;
    private int idle;
    private int turn;             // head turning all around at a stop
    private boolean inBiome;

    public ExploreTask(List<String> wanted, String biome, int radius) {
        super("explore");
        for (String w : wanted) targets.addAll(match(w));
        this.biome = biome == null ? "" : biome.toLowerCase(Locale.ROOT).trim();
        this.radius = Math.max(32, Math.min(radius, 600));
    }

    /** Every block that is what the word means: by name, and by the id's words ("assembler" -> machine_assembler, ...). */
    static Set<Block> match(String word) {
        Set<Block> out = new HashSet<>(Names.blocks(List.of(word)));
        String w = word.toLowerCase(Locale.ROOT).trim();
        String path = w.contains(":") ? w.substring(w.indexOf(':') + 1) : w;
        String ns = w.contains(":") ? w.substring(0, w.indexOf(':')) : "";
        String core = path.replace("machine_", "").replace("_machine", "");
        if (core.length() >= 4) {
            String stem = core.length() > 6 ? core.substring(0, Math.max(5, core.length() - 3)) : core;   // assembler ~ assembly
            for (Block b : BuiltInRegistries.BLOCK) {
                var id = BuiltInRegistries.BLOCK.getKey(b);
                if (!ns.isEmpty() && !id.getNamespace().equals(ns)) continue;
                if (id.getPath().contains(stem) && b.defaultBlockState().hasBlockEntity()) out.add(b);
            }
        }
        return out;
    }

    private void plan(BlockPos c) {
        center = c;
        points.clear();
        for (int ring = 1; ring * 20 <= radius; ring++) {
            int n = 6 + ring * 2;
            for (int i = 0; i < n; i++) {
                double a = 2 * Math.PI * i / n + ring * 0.4;
                points.add(new Vec3(c.getX() + Math.cos(a) * ring * 20, c.getY(), c.getZ() + Math.sin(a) * ring * 20));
            }
        }
        point = -1;
    }

    private boolean biomeAt(BlockPos p) {
        return Bot.level().getBiome(p).unwrapKey().map(k -> k.location().getPath().contains(biome)).orElse(false);
    }

    private BlockPos findBiome() {
        BlockPos me = p().blockPosition();
        BlockPos best = null;
        double bestD = Double.MAX_VALUE;
        for (int dx = -96; dx <= 96; dx += 16) {
            for (int dz = -96; dz <= 96; dz += 16) {
                BlockPos at = me.offset(dx, 0, dz);
                if (!Bot.level().hasChunkAt(at) || !biomeAt(at)) continue;
                double d = at.distSqr(me);
                if (d < bestD) {
                    bestD = d;
                    best = at;
                }
            }
        }
        return best;
    }

    private String found() {
        if (targets.isEmpty()) return "";
        List<BlockPos> f = Memory.find(targets, radius * 2, 6);
        if (f.isEmpty()) return "";
        StringBuilder sb = new StringBuilder();
        for (BlockPos bp : f) {
            sb.append(sb.length() > 0 ? "; " : "").append(Bot.id(Bot.level().getBlockState(bp).getBlock())).append(" в ").append(Bot.pos(bp))
                    .append(" (").append(Math.round(Bot.distTo(bp))).append(" бл.)");
        }
        return sb.toString();
    }

    /** A building worth stepping into: mod machines or doors seen near, in a place not looked through yet. */
    private BlockPos building() {
        var p = p();
        BlockPos me = p.blockPosition();
        BlockPos best = null;
        double bestD = 40 * 40;
        for (BlockPos bp : BlockPos.betweenClosed(me.offset(-24, -6, -24), me.offset(24, 10, 24))) {
            if (visited.contains(cell(bp))) continue;
            var st = Bot.level().getBlockState(bp);
            boolean machine = st.hasBlockEntity() && !BuiltInRegistries.BLOCK.getKey(st.getBlock()).getNamespace().equals("minecraft");
            if (!machine && !st.is(BlockTags.DOORS)) continue;
            if (!Bot.canSee(bp) && bp.distSqr(me) > 36) continue;   // only what he can see (no x-ray)
            double d = bp.distSqr(me);
            if (d < bestD) {
                bestD = d;
                best = bp.immutable();
            }
        }
        return best;
    }

    private static long cell(BlockPos p) {
        return BlockPos.asLong(p.getX() >> 3, p.getY() >> 3, p.getZ() >> 3);
    }

    private String seenMachines() {
        Map<String, BlockPos> seen = new LinkedHashMap<>();
        for (Block b : BuiltInRegistries.BLOCK) {
            var id = BuiltInRegistries.BLOCK.getKey(b);
            if (id.getNamespace().equals("minecraft") || !b.defaultBlockState().hasBlockEntity()) continue;
            List<BlockPos> at = Memory.find(Set.of(b), radius * 2, 1);
            if (!at.isEmpty() && seen.size() < 15) seen.put(id.toString(), at.get(0));
        }
        if (seen.isEmpty()) return "";
        StringBuilder sb = new StringBuilder(" По пути видел: ");
        seen.forEach((id, bp) -> sb.append(id).append(" (").append(Bot.pos(bp)).append("), "));
        return sb.substring(0, sb.length() - 2);
    }

    /** Turn the head all around, like a person at a crossroads (the rays go where he looks too). */
    private boolean lookingAround() {
        if (turn <= 0) return false;
        var p = p();
        double a = Math.toRadians((40 - turn) * 9);
        Bot.lookAt(new Vec3(p.getX() + Math.cos(a) * 8, p.getEyeY() + (turn % 20 < 10 ? -1 : 1.5), p.getZ() + Math.sin(a) * 8));
        if (--turn % 10 == 0) Memory.lookAround();
        return true;
    }

    @Override
    protected Status run() {
        var p = p();
        if (age == 1) {
            Memory.lookAround();
            String f = found();
            if (!f.isEmpty()) return done("уже вижу: " + f);
            plan(p.blockPosition());
            turn = 40;
        }
        if (lookingAround()) return Status.RUNNING;
        if (age % 20 == 0) {
            Memory.lookAround();
            String f = found();
            if (!f.isEmpty()) {
                Nav.cancel();
                return done("нашёл: " + f + "." + seenMachines());
            }
        }
        if (!biome.isEmpty() && !inBiome) {
            if (biomeAt(p.blockPosition())) {
                inBiome = true;
                Nav.cancel();
                plan(p.blockPosition());
                if (targets.isEmpty()) return done("я в биоме " + biome + " в " + Bot.pos(p.blockPosition()) + "." + seenMachines());
            } else if (age % 100 == 2) {
                BlockPos b = findBiome();
                if (b != null && (center == null || b.distSqr(center) > 64)) {
                    Nav.gotoXZ(b.getX(), b.getZ());
                    walkTicks = 0;
                    center = b;
                    return Status.RUNNING;
                }
            }
        }
        // a building on the way: go in, look through it, then carry on
        if (inside != null) {
            double d = Math.sqrt(p.blockPosition().distSqr(inside));
            idle = Nav.busy() ? 0 : idle + 1;
            if (d > 2.5 && idle < 40 && ++walkTicks < 20 * 40) return Status.RUNNING;
            visited.add(cell(inside));
            inside = null;
            turn = 40;   // inside: look around every side
            return Status.RUNNING;
        }
        // looking for something, or surveying (no target, no biome: "look through the buildings around")
        if (age % 60 == 30 && (!targets.isEmpty() || biome.isEmpty())) {
            BlockPos b = building();
            if (b != null) {
                inside = b;
                Nav.gotoNear(b, 2);
                walkTicks = 0;
                idle = 0;
                return Status.RUNNING;
            }
        }
        if (point >= 0 && point < points.size()) {
            Vec3 goal = points.get(point);
            double d = Math.hypot(p.getX() - goal.x, p.getZ() - goal.z);
            idle = Nav.busy() ? 0 : idle + 1;
            if (d > 6 && idle < 60 && ++walkTicks < 20 * 60) return Status.RUNNING;
            turn = 40;   // at the stop: look all around before going on
        }
        if (++point >= points.size()) {
            Nav.cancel();
            return targets.isEmpty() ? done("обошёл округу радиусом " + radius + " бл." + seenMachines())
                    : fail("обошёл округу радиусом " + radius + " бл., но не нашёл то, что искал." + seenMachines());
        }
        Vec3 next = points.get(point);
        Nav.gotoXZ((int) next.x, (int) next.z);
        walkTicks = 0;
        idle = 0;
        return Status.RUNNING;
    }

    @Override
    public void stop() {
        Nav.cancel();
    }

    @Override
    public String progress() {
        return inside != null ? "осматриваю здание у " + Bot.pos(inside) : "точка " + Math.max(0, point) + "/" + points.size();
    }
}
