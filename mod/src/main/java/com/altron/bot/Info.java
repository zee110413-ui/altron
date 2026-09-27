package com.altron.bot;

import com.altron.J;
import com.google.gson.JsonObject;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.item.ItemEntity;
import net.minecraft.world.entity.monster.Enemy;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.inventory.AbstractContainerMenu;
import net.minecraft.world.inventory.Slot;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.crafting.Ingredient;
import net.minecraft.world.item.crafting.Recipe;
import net.minecraft.world.level.block.Block;
import net.minecraft.tags.BlockTags;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.function.Predicate;

/** Text descriptions of the world for the brain. */
public final class Info {
    private Info() {
    }

    public static JsonObject state() {
        LocalPlayer p = Bot.player();
        Task t = BotClient.currentTask();
        JsonObject s = J.obj("type", "state",
                "pos", J.arr(p.getX(), p.getY(), p.getZ()),
                "dim", p.level().dimension().location().toString(),
                "hp", Math.round(p.getHealth()), "food", p.getFoodData().getFoodLevel(),
                "held", Bot.describe(p.getMainHandItem()),
                "task", t == null ? "" : t.name(), "progress", t == null ? "" : t.progress(),
                "busy", Baritone.busy());
        Player o = BotClient.owner.isBlank() ? null : Bot.findPlayer(BotClient.owner);
        if (o != null) {
            s.add("owner_pos", J.arr(o.getX(), o.getY(), o.getZ()));
            String look = ownerLook(o);
            if (!look.isEmpty()) s.addProperty("owner_look", look);
        }
        return s;
    }

    /**
     * What the commander is looking at, seen from his head's turn: "вот этот рычаг", "вот сюда", "вот этот моб"
     * mean exactly that. The ray from his eyes is cast in Altron's own copy of the world.
     */
    public static String ownerLook(Player o) {
        Vec3 eye = o.getEyePosition();
        Vec3 end = eye.add(o.getViewVector(1f).scale(24));
        Entity best = null;
        double bestD = Double.MAX_VALUE;
        for (Entity e : o.level().getEntities(o, o.getBoundingBox().inflate(24), en -> en.isAlive() && !(en instanceof ItemEntity))) {
            var hit = e.getBoundingBox().inflate(0.3).clip(eye, end);
            if (hit.isPresent() && eye.distanceTo(hit.get()) < bestD) {
                bestD = eye.distanceTo(hit.get());
                best = e;
            }
        }
        HitResult h = o.pick(24, 1f, false);
        double blockD = h.getType() == HitResult.Type.BLOCK ? eye.distanceTo(h.getLocation()) : Double.MAX_VALUE;
        if (best != null && bestD < blockD) {
            return "существо/объект " + best.getName().getString() + " (" + Bot.id(best) + ") в " + Bot.pos(best.blockPosition())
                    + ", " + Math.round(bestD) + " бл. от командира";
        }
        if (h instanceof BlockHitResult bh && h.getType() == HitResult.Type.BLOCK) {
            BlockPos bp = bh.getBlockPos();
            Block b = o.level().getBlockState(bp).getBlock();
            return "блок " + b.getName().getString() + " (" + Bot.id(b) + ") в " + Bot.pos(bp) + ", " + Math.round(blockD) + " бл. от командира";
        }
        return "";
    }

    /** Things to use around the bot that it can see: levers, buttons, doors, hatches, ladders, chests, machines. */
    public static String usableBlocks(int radius, int limit) {
        LocalPlayer p = Bot.player();
        BlockPos me = p.blockPosition();
        List<BlockPos> found = new ArrayList<>();
        for (BlockPos bp : BlockPos.betweenClosed(me.offset(-radius, -radius, -radius), me.offset(radius, radius, radius))) {
            var st = p.level().getBlockState(bp);
            if (st.isAir()) continue;
            Block b = st.getBlock();
            boolean usable = st.hasBlockEntity() || b instanceof net.minecraft.world.level.block.LeverBlock
                    || st.is(BlockTags.BUTTONS) || st.is(BlockTags.DOORS) || st.is(BlockTags.TRAPDOORS) || st.is(BlockTags.FENCE_GATES)
                    || st.is(BlockTags.CLIMBABLE) || b instanceof net.minecraft.world.level.block.DoorBlock
                    || b instanceof net.minecraft.world.level.block.TrapDoorBlock || b instanceof net.minecraft.world.level.block.LadderBlock
                    || b instanceof net.minecraft.world.level.block.ButtonBlock;
            if (usable && Bot.canSee(bp)) found.add(bp.immutable());
        }
        found.sort(Comparator.comparingDouble(bp -> bp.distSqr(me)));
        StringBuilder sb = new StringBuilder();
        Set<Long> columns = new HashSet<>();
        int n = 0;
        for (BlockPos bp : found) {
            var st = p.level().getBlockState(bp);
            Block b = st.getBlock();
            boolean climb = com.altron.bot.tasks.ClimbTask.climbable(st, bp);
            // a ladder or a door is many blocks: one line per column
            if ((climb || b instanceof net.minecraft.world.level.block.DoorBlock) && !columns.add(BlockPos.asLong(bp.getX(), 0, bp.getZ()))) continue;
            if (n++ >= limit) break;
            sb.append("- ").append(b.getName().getString()).append(" (").append(Bot.id(b)).append(") ");
            if (climb) {
                // where it starts and where it leads: "a ladder up to y=70" is what makes a way up obvious
                int lo = bp.getY(), hi = bp.getY();
                while (lo > bp.getY() - 64 && com.altron.bot.tasks.ClimbTask.climbable(p.level().getBlockState(bp.atY(lo - 1)), bp.atY(lo - 1))) lo--;
                while (hi < bp.getY() + 64 && com.altron.bot.tasks.ClimbTask.climbable(p.level().getBlockState(bp.atY(hi + 1)), bp.atY(hi + 1))) hi++;
                sb.append(Bot.pos(bp.atY(lo))).append(", по ней можно залезть от y=").append(lo).append(" до y=").append(hi + 1)
                        .append(" (climb)");
            } else {
                sb.append(Bot.pos(bp));
            }
            sb.append(", ").append(Math.round(Math.sqrt(bp.distSqr(me)))).append(" бл.\n");
        }
        return sb.toString().trim();
    }

    public static String status() {
        LocalPlayer p = Bot.player();
        StringBuilder sb = new StringBuilder();
        sb.append("Позиция: ").append(Bot.pos(p.blockPosition())).append(" (").append(p.level().dimension().location()).append(")\n");
        sb.append("Здоровье: ").append(Math.round(p.getHealth())).append("/").append(Math.round(p.getMaxHealth()))
                .append(", еда: ").append(p.getFoodData().getFoodLevel()).append("/20\n");
        sb.append("В руке: ").append(Bot.describe(p.getMainHandItem()));
        if (Guns.isTacz(p.getMainHandItem())) sb.append(", патронов в магазине: ").append(Guns.loadedAmmo(p.getMainHandItem()));
        sb.append('\n');
        StringBuilder armor = new StringBuilder();
        for (EquipmentSlot s : new EquipmentSlot[]{EquipmentSlot.HEAD, EquipmentSlot.CHEST, EquipmentSlot.LEGS, EquipmentSlot.FEET}) {
            ItemStack a = p.getItemBySlot(s);
            if (!a.isEmpty()) armor.append(armor.length() > 0 ? ", " : "").append(a.getHoverName().getString());
        }
        sb.append("Броня: ").append(armor.length() == 0 ? "нет" : armor).append('\n');
        Task t = BotClient.currentTask();
        sb.append("Задача: ").append(t == null ? "нет" : t.name() + " " + t.progress()).append('\n');
        long day = p.level().getDayTime() % 24000;
        sb.append("Время: ").append(day < 12500 ? "день" : "ночь");
        String owner = BotClient.owner;
        Player o = Bot.findPlayer(owner);
        if (o != null) sb.append("\nИгрок ").append(owner).append(": ").append(Bot.pos(o.blockPosition()))
                .append(", расстояние ").append(Math.round(o.distanceTo(p)));
        return sb.toString();
    }

    public static String inventory() {
        LocalPlayer p = Bot.player();
        Map<String, Integer> counts = new LinkedHashMap<>();
        Map<String, String> names = new LinkedHashMap<>();
        for (int i = 0; i < 36; i++) {
            ItemStack s = p.getInventory().getItem(i);
            if (s.isEmpty()) continue;
            String id = Bot.id(s.getItem());
            counts.merge(id, s.getCount(), Integer::sum);
            String extra = "";
            if (Guns.isTacz(s)) extra = " [патронов: " + Guns.loadedAmmo(s) + "]";
            names.putIfAbsent(id, s.getHoverName().getString() + extra);
        }
        if (counts.isEmpty()) return "Инвентарь пуст.";
        StringBuilder sb = new StringBuilder("Инвентарь:");
        for (Map.Entry<String, Integer> e : counts.entrySet()) {
            sb.append("\n- ").append(names.get(e.getKey())).append(" (").append(e.getKey()).append(") x").append(e.getValue());
        }
        sb.append("\nВ руке: ").append(Bot.describe(p.getMainHandItem()));
        return sb.toString();
    }

    public static String nearby(double radius) {
        LocalPlayer p = Bot.player();
        List<Entity> list = new ArrayList<>(p.level().getEntities(p, p.getBoundingBox().inflate(radius),
                e -> e.isAlive() && (e instanceof Player || perceives(e))));
        list.sort(Comparator.comparingDouble(e -> e.distanceTo(p)));
        StringBuilder sb = new StringBuilder();
        int items = 0;
        int shown = 0;
        for (Entity e : list) {
            if (e instanceof ItemEntity) {
                items++;
                continue;
            }
            if (shown++ >= 20) break;
            String kind = e instanceof Player ? "игрок" : e instanceof Enemy ? "враг" : e instanceof LivingEntity ? "существо" : "объект";
            sb.append("- ").append(kind).append(": ").append(e.getName().getString()).append(" (").append(Bot.id(e)).append(")")
                    .append(", ").append(Math.round(e.distanceTo(p))).append(" бл., ").append(Bot.pos(e.blockPosition()));
            if (e instanceof LivingEntity le) sb.append(", hp ").append(Math.round(le.getHealth()));
            sb.append('\n');
        }
        if (items > 0) sb.append("- предметов на земле: ").append(items).append('\n');
        // 16 blocks: a ladder or a lever across the yard counts as "near" for a person too
        String blocks = usableBlocks(Math.min(16, (int) radius), 12);
        if (!blocks.isEmpty()) sb.append("Блоки рядом, которые можно использовать (use_block по координатам):\n").append(blocks).append('\n');
        return sb.length() == 0 ? "Рядом никого не вижу." : sb.toString().trim();
    }

    /** Positions of blocks the bot has actually seen (no x-ray), nearest first. */
    public static List<BlockPos> findBlocks(Set<Block> blocks, int radius, int limit) {
        return Memory.find(blocks, radius, limit);
    }

    /** Like a player: sees entities in line of sight, hears those very close. */
    public static boolean perceives(Entity e) {
        LocalPlayer p = Bot.player();
        double d = e.distanceTo(p);
        return d <= 6 || (d <= 96 && p.hasLineOfSight(e));
    }

    /** Recipes (of any type, including modded machines) that produce the item. */
    public static String recipes(Item item, int limit) {
        ClientLevel level = Bot.level();
        StringBuilder sb = new StringBuilder();
        int n = 0;
        for (Recipe<?> r : level.getRecipeManager().getRecipes()) {
            ItemStack out;
            try {
                out = r.getResultItem(level.registryAccess());
            } catch (Exception e) {
                continue;
            }
            if (out == null || !out.is(item)) continue;
            if (n++ >= limit) break;
            String type = String.valueOf(BuiltInRegistries.RECIPE_TYPE.getKey(r.getType()));
            sb.append("- [").append(type).append("] ").append(out.getCount()).append("x ").append(out.getHoverName().getString())
                    .append(" <- ").append(ingredients(r)).append('\n');
        }
        return n == 0 ? "Рецептов для " + Bot.id(item) + " не найдено (возможно, добывается или делается особым способом)."
                : "Рецепты " + Bot.id(item) + ":\n" + sb.toString().trim();
    }

    static String ingredients(Recipe<?> r) {
        if (TaczCompat.isTableRecipe(r)) return TaczCompat.ingredients(r);
        Map<String, Integer> need = new LinkedHashMap<>();
        for (Ingredient ing : r.getIngredients()) {
            if (ing.isEmpty()) continue;
            ItemStack[] opts = ing.getItems();
            if (opts.length == 0) continue;
            String name = opts[0].getHoverName().getString() + " (" + Bot.id(opts[0].getItem()) + (opts.length > 1 ? " или др." : "") + ")";
            need.merge(name, Math.max(1, opts[0].getCount()), Integer::sum);
        }
        StringBuilder sb = new StringBuilder();
        for (Map.Entry<String, Integer> e : need.entrySet()) {
            sb.append(sb.length() > 0 ? ", " : "").append(e.getValue()).append("x ").append(e.getKey());
        }
        return sb.length() == 0 ? "(ингредиенты не указаны)" : sb.toString();
    }

    public static String container() {
        LocalPlayer p = Bot.player();
        AbstractContainerMenu menu = p.containerMenu;
        if (menu == p.inventoryMenu) return "Контейнер не открыт.";
        StringBuilder sb = new StringBuilder();
        String title = Bot.mc().screen != null ? Bot.mc().screen.getTitle().getString() : menu.getClass().getSimpleName();
        // how full it is: a full output chest is why a line backs up and spills onto its belts
        int total = 0, used = 0;
        for (Slot s : menu.slots) {
            if (Inv.isPlayerSlot(p, s)) continue;
            total++;
            if (!s.getItem().isEmpty()) used++;
        }
        sb.append("Открыт: ").append(title).append(" (занято ").append(used).append(" из ").append(total)
                .append(total > 0 && used >= total ? " — ПОЛОН" : "").append(")\n");
        // totals across ALL slots first: a stack is capped at 64, so one item often sits in several slots —
        // without this a "x64" on the first slot reads as the whole amount and container_take is called for
        // just that one stack, over and over, instead of taking everything in one go
        Map<String, Integer> totals = new LinkedHashMap<>();
        Map<String, String> names = new LinkedHashMap<>();
        StringBuilder slots = new StringBuilder();
        int shown = 0;
        for (Slot s : menu.slots) {
            if (Inv.isPlayerSlot(p, s)) continue;
            ItemStack st = s.getItem();
            if (st.isEmpty()) continue;
            shown++;
            String id = Bot.id(st.getItem());
            totals.merge(id, st.getCount(), Integer::sum);
            names.putIfAbsent(id, st.getHoverName().getString());
            slots.append("- слот ").append(s.index).append(": ").append(Bot.describe(st)).append('\n');
        }
        if (shown == 0) return sb.append("(пусто)").toString().trim();
        sb.append("Всего: ");
        boolean first = true;
        for (Map.Entry<String, Integer> e : totals.entrySet()) {
            if (!first) sb.append(", ");
            first = false;
            sb.append(names.get(e.getKey())).append(" (").append(e.getKey()).append(") x").append(e.getValue());
        }
        sb.append(" (container_take/container_put без count или с большим count берут/кладут СРАЗУ ВСЁ количество, "
                + "не только один стек)\n").append(slots);
        return sb.toString().trim();
    }

    public static boolean isHostile(Entity e) {
        return e instanceof Enemy && e.isAlive();
    }

    public static List<Entity> entities(double radius, Predicate<Entity> filter) {
        LocalPlayer p = Bot.player();
        AABB box = p.getBoundingBox().inflate(radius);
        List<Entity> list = new ArrayList<>(p.level().getEntities(p, box, filter));
        list.sort(Comparator.comparingDouble(e -> e.distanceTo(p)));
        return list;
    }
}
