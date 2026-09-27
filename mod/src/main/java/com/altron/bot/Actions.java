package com.altron.bot;

import com.altron.AltronMod;
import com.altron.J;
import com.altron.bot.tasks.AttackTask;
import com.altron.bot.tasks.BreakTask;
import com.altron.bot.tasks.BuildMultiblockTask;
import com.altron.bot.tasks.DriveTask;
import com.altron.bot.tasks.CollectTask;
import com.altron.bot.tasks.CraftTask;
import com.altron.bot.tasks.EatTask;
import com.altron.bot.tasks.FollowTask;
import com.altron.bot.tasks.GiveTask;
import com.altron.bot.tasks.GotoTask;
import com.altron.bot.tasks.HoldKeyTask;
import com.altron.bot.tasks.MachineTask;
import com.altron.bot.tasks.MineTask;
import com.altron.bot.tasks.PlaceTask;
import com.altron.bot.tasks.SmeltTask;
import com.altron.bot.tasks.TaczCraftTask;
import com.altron.bot.tasks.TransportTask;
import com.altron.bot.tasks.UseBlockTask;
import com.altron.bot.tasks.UseEntityTask;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import net.minecraft.client.KeyMapping;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.inventory.AbstractContainerMenu;
import net.minecraft.world.inventory.ClickType;
import net.minecraft.world.inventory.Slot;
import net.minecraft.world.item.ArmorItem;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.phys.Vec3;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.function.Predicate;

/** Commands from the brain: {"type":"cmd","id":1,"name":"mine","args":{...}}. */
public final class Actions {
    private Actions() {
    }

    public static void handle(JsonObject msg) {
        String type = J.str(msg, "type", "");
        if (type.equals("config")) {
            BotClient.owner = J.str(msg, "owner", BotClient.owner);
            BotClient.worldName = J.str(msg, "world", BotClient.worldName);
            return;
        }
        if (!type.equals("cmd")) return;
        int id = J.num(msg, "id", 0);
        String name = J.str(msg, "name", "");
        JsonObject args = msg.has("args") && msg.get("args").isJsonObject() ? msg.getAsJsonObject("args") : new JsonObject();
        if (name.equals("screenshot") && BotClient.deferScreenshot(id)) return;   // answered when the picture is drawn
        JsonObject res;
        try {
            res = run(name, args);
        } catch (Exception e) {
            AltronMod.LOG.error("[Altron] command {} failed", name, e);
            res = err("ошибка при выполнении " + name + ": " + e);
        }
        res.addProperty("type", "result");
        res.addProperty("id", id);
        Bot.send(res);
    }

    private static JsonObject ok(String msg) {
        return J.obj("ok", true, "msg", msg);
    }

    private static JsonObject err(String msg) {
        return J.obj("ok", false, "msg", msg);
    }

    private static JsonObject start(Task t) {
        int id = BotClient.setTask(t);
        return J.obj("ok", true, "msg", "начал: " + t.name(), "task_id", id);
    }

    private static BlockPos pos(JsonObject a) {
        return BlockPos.containing(J.dbl(a, "x", 0), J.dbl(a, "y", 0), J.dbl(a, "z", 0));
    }

    private static List<String> list(JsonObject a, String key) {
        List<String> out = new ArrayList<>();
        JsonElement e = a.get(key);
        if (e == null || e.isJsonNull()) return out;
        if (e.isJsonArray()) {
            for (JsonElement x : e.getAsJsonArray()) out.add(x.getAsString());
        } else {
            for (String s : e.getAsString().split(",")) if (!s.isBlank()) out.add(s.trim());
        }
        return out;
    }

    private static Item item(JsonObject a) {
        return Names.item(J.str(a, "item", ""));
    }

    private static String who(JsonObject a) {
        String w = J.str(a, "player", "");
        return w.isBlank() ? BotClient.owner : w;
    }

    private static JsonObject run(String name, JsonObject a) {
        LocalPlayer p = Bot.player();
        switch (name) {
            // ---------- information ----------
            case "status":
                return ok(Info.status());
            case "inventory":
                return ok(Info.inventory());
            case "inventory_ids": {
                // machine-readable inventory for the brain's crafting planner
                JsonObject items = new JsonObject();
                Inv.snapshot(p).forEach(items::addProperty);
                return J.obj("ok", true, "msg", "инвентарь", "items", items, "creative", Creative.on());
            }
            case "creative_take": {
                // creative mode: take from the creative menu what a player there would take instead of mining it
                if (!Creative.on()) return err("я не в творческом режиме — из меню брать нельзя, это нужно добыть");
                Item it = item(a);
                if (it == null) return err("не знаю предмет " + J.str(a, "item", ""));
                int n = Creative.take(it, Math.max(1, J.num(a, "count", 1)));
                return n > 0 ? ok("взял из творческого меню " + n + "x " + Bot.id(it)) : err("инвентарь полон — некуда взять " + Bot.id(it));
            }
            case "machine": {
                // make an item in a mod machine standing in the world (HBM press, HBM assembly machine...)
                Item product = item(a);
                if (product == null) return err("не знаю предмет " + J.str(a, "item", ""));
                Set<Block> ms = new HashSet<>();
                for (String id : list(a, "machines")) {
                    ResourceLocation rl = ResourceLocation.tryParse(id);
                    if (rl != null && BuiltInRegistries.BLOCK.containsKey(rl)) ms.add(BuiltInRegistries.BLOCK.get(rl));
                }
                if (ms.isEmpty()) return err("не знаю машины: " + list(a, "machines"));
                List<MachineTask.Input> ins = new ArrayList<>();
                if (a.has("inputs") && a.get("inputs").isJsonArray()) {
                    for (JsonElement e : a.getAsJsonArray("inputs")) {
                        JsonObject o = e.getAsJsonObject();
                        Item it = Names.item(J.str(o, "item", ""));
                        if (it == null) return err("не знаю предмет " + J.str(o, "item", ""));
                        Set<Item> alts = new HashSet<>();
                        if (o.has("alts") && o.get("alts").isJsonArray()) {
                            for (JsonElement x : o.getAsJsonArray("alts")) {
                                Item alt = Names.item(x.getAsString());
                                if (alt != null) alts.add(alt);
                            }
                        }
                        ins.add(new MachineTask.Input(it, J.num(o, "count", 1), J.num(o, "slot", -1),
                                !o.has("back") || o.get("back").getAsBoolean(), alts));
                    }
                }
                String rid = J.str(a, "recipe", "");
                return start(new MachineTask(ms, product, J.num(a, "count", 1), rid.isBlank() ? null : ResourceLocation.tryParse(rid), ins));
            }
            case "nearby":
                return ok(Info.nearby(J.num(a, "radius", 32)));
            case "recall_items":
                return ok(Memory.recallItems(J.str(a, "query", ""), J.num(a, "limit", 8)));
            case "recall_entities":
                return ok(Memory.recallEntities(J.str(a, "query", ""), J.num(a, "limit", 8)));
            case "memory":
                return ok(Memory.summary());
            case "find_item": {
                List<Item> items = Names.items(J.str(a, "query", J.str(a, "item", "")), 8);
                if (items.isEmpty()) return err("ничего не найдено");
                StringBuilder sb = new StringBuilder();
                for (Item i : items) sb.append("- ").append(new ItemStack(i).getHoverName().getString()).append(" = ").append(Bot.id(i)).append('\n');
                return ok(sb.toString().trim());
            }
            // ---------- for the test course: what he knows about the pack ----------
            case "item_names": {
                // every item and the name he knows it by (the game's own language)
                String ns = J.str(a, "namespace", "");
                JsonObject names = new JsonObject();
                for (Item it : BuiltInRegistries.ITEM) {
                    ResourceLocation id = BuiltInRegistries.ITEM.getKey(it);
                    if (!ns.isBlank() && !id.getNamespace().equals(ns)) continue;
                    names.addProperty(id.toString(), new ItemStack(it).getHoverName().getString());
                }
                return J.obj("ok", true, "msg", "названия", "names", names);
            }
            case "recipe_data": {
                // the ingredients of the item's crafting and smelting recipes, as item ids
                Item it = item(a);
                if (it == null) return err("не знаю предмет");
                com.google.gson.JsonArray crafting = new com.google.gson.JsonArray();
                for (var r : CraftTask.recipesFor(it)) {
                    JsonObject ing = new JsonObject();
                    boolean ok = true;
                    for (var in : r.getIngredients()) {
                        if (in.isEmpty()) continue;
                        ItemStack[] opts = in.getItems();
                        if (opts.length == 0) {
                            ok = false;
                            break;
                        }
                        String id = Bot.id(opts[0].getItem());
                        ing.addProperty(id, (ing.has(id) ? ing.get(id).getAsInt() : 0) + 1);
                    }
                    if (!ok) continue;
                    boolean big = r instanceof net.minecraft.world.item.crafting.ShapedRecipe sr ? sr.getWidth() > 2 || sr.getHeight() > 2
                            : r.getIngredients().size() > 4;
                    crafting.add(J.obj("ingredients", ing, "count", r.getResultItem(Bot.level().registryAccess()).getCount(), "big", big));
                }
                com.google.gson.JsonArray smelting = new com.google.gson.JsonArray();
                for (var r : Bot.level().getRecipeManager().getAllRecipesFor(net.minecraft.world.item.crafting.RecipeType.SMELTING)) {
                    if (!r.getResultItem(Bot.level().registryAccess()).is(it) || r.getIngredients().isEmpty()) continue;
                    ItemStack[] opts = r.getIngredients().get(0).getItems();
                    if (opts.length > 0) smelting.add(Bot.id(opts[0].getItem()));
                }
                return J.obj("ok", true, "msg", "рецепты", "crafting", crafting, "smelting", smelting);
            }
            case "multiblocks_data": {
                // every IE / Immersive Petroleum structure: its name and the blocks it takes
                com.google.gson.JsonArray out = new com.google.gson.JsonArray();
                for (Object mb : MultiblockCompat.all()) {
                    JsonObject mats = new JsonObject();
                    try {
                        MultiblockCompat.materials(MultiblockCompat.structure(mb)).forEach((it, n) -> mats.addProperty(Bot.id(it), n));
                    } catch (Exception e) {
                        continue;
                    }
                    out.add(J.obj("name", MultiblockCompat.name(mb), "materials", mats));
                }
                return J.obj("ok", true, "msg", "постройки", "multiblocks", out);
            }
            case "tacz_recipes": {
                com.google.gson.JsonArray out = new com.google.gson.JsonArray();
                for (var r : Bot.level().getRecipeManager().getRecipes()) {
                    if (!TaczCompat.isTableRecipe(r)) continue;
                    JsonObject ing = new JsonObject();
                    boolean ok = true;
                    for (Object[] in : TaczCompat.inputs(r)) {
                        ItemStack[] opts = ((net.minecraft.world.item.crafting.Ingredient) in[0]).getItems();
                        if (opts.length == 0) {
                            ok = false;
                            break;
                        }
                        String id = Bot.id(opts[0].getItem());
                        ing.addProperty(id, (ing.has(id) ? ing.get(id).getAsInt() : 0) + (Integer) in[1]);
                    }
                    ItemStack res = TaczCompat.output(r);
                    if (!ok || res.isEmpty()) continue;
                    out.add(J.obj("id", r.getId().toString(), "output", Bot.id(res.getItem()), "name", res.getHoverName().getString(),
                            "count", res.getCount(), "inputs", ing));
                }
                return J.obj("ok", true, "msg", "рецепты оружейного стола", "recipes", out);
            }
            case "explore": {
                // search the land: blocks to find (machines...) and/or a biome to reach ("desert")
                // looking for machines: at least 150 blocks around (the model tends to ask for 30 and give up)
                int r = J.num(a, "radius", 160);
                if (!list(a, "blocks").isEmpty()) r = Math.max(r, 150);
                return start(new com.altron.bot.tasks.ExploreTask(list(a, "blocks"), J.str(a, "biome", ""), r));
            }
            case "known_blocks": {
                // machines and stores he has seen around (for learning a base): id, name, where
                int radius = Math.min(J.num(a, "radius", 64), 256);
                Set<Block> kinds = new HashSet<>();
                // walls, wires and belts that happen to have a block entity (a SecurityCraft base is hundreds of
                // reinforced blocks) would crowd out the machines and chests
                java.util.regex.Pattern junk = java.util.regex.Pattern.compile(
                        "reinforced|connector|relay|cable|wire|conveyor|disguise|fence|lamp|light|sign|banner|bed$|door|"
                                + "camera|scanner|_part$|dummy|pipe|conduit|duct|skull|head$|candle|pot$|spawner");
                for (Block b : BuiltInRegistries.BLOCK) {
                    var st = b.defaultBlockState();
                    if (st.hasBlockEntity() && Memory.interesting(st) && !junk.matcher(Bot.id(b)).find()) kinds.add(b);
                }
                com.google.gson.JsonArray out = new com.google.gson.JsonArray();
                // around a given spot (the commander's base), not only around where he happens to stand
                BlockPos center = J.has(a, "x") ? BlockPos.containing(J.dbl(a, "x", 0), J.dbl(a, "y", 0), J.dbl(a, "z", 0)) : p.blockPosition();
                for (BlockPos bp : Memory.find(kinds, radius + (int) Math.sqrt(p.blockPosition().distSqr(center)), J.num(a, "limit", 120) * 3)) {
                    if (bp.distSqr(center) > (double) radius * radius) continue;
                    boolean loaded = Bot.level().hasChunkAt(bp) && !Bot.level().getBlockState(bp).isAir();
                    var st = loaded ? Bot.level().getBlockState(bp) : null;
                    Block b = loaded ? st.getBlock() : Memory.remembered(bp);   // far away now: as he remembers it
                    if (b == null || !kinds.contains(b)) continue;   // gone since
                    // does it open a window (a machine or a store), or is it a conveyor, a cable, a wall block...
                    // (not known for a part of the world not loaded now: the brain decides by its kind)
                    Object gui = !loaded ? null : (Object) (Bot.level().getBlockEntity(bp) instanceof net.minecraft.world.MenuProvider
                            || st.getMenuProvider(Bot.level(), bp) != null);
                    JsonObject o = J.obj("id", Bot.id(b), "name", b.getName().getString(), "pos", J.arr(bp.getX(), bp.getY(), bp.getZ()),
                            "dist", Math.round(Bot.distTo(bp)));
                    if (gui != null) o.addProperty("gui", (Boolean) gui);
                    out.add(o);
                    if (out.size() >= J.num(a, "limit", 120)) break;
                }
                return J.obj("ok", true, "msg", "знаю " + out.size() + " машин и хранилищ рядом", "blocks", out);
            }
            case "production_map": {
                // how a base is laid out, as he has seen it: everything that holds, makes or moves things (machines,
                // chests, hoppers, conveyors, droppers, pipes), which way each one faces, and whether it opens a window
                int radius = Math.min(J.num(a, "radius", 48), 128);
                BlockPos center = J.has(a, "x") ? BlockPos.containing(J.dbl(a, "x", 0), J.dbl(a, "y", 0), J.dbl(a, "z", 0)) : p.blockPosition();
                java.util.regex.Pattern skip = java.util.regex.Pattern.compile(
                        "reinforced|disguise|sign|banner|bed$|door|lamp|light|camera|scanner|skull|head$|candle|pot$|spawner|"
                                + "fence|_ore$|log$|leaves|grass|sand$|gravel|glass|concrete|brick|stair|slab|wall$|carpet|wool");
                java.util.regex.Pattern mover = java.util.regex.Pattern.compile("hopper|conveyor|chute|pipe|duct|conduit|dropper|dispenser|"
                        + "funnel|belt|tube|cable|wire|connector");
                com.google.gson.JsonArray out = new com.google.gson.JsonArray();
                for (BlockPos bp : Memory.around(center, radius)) {
                    boolean loaded = Bot.level().hasChunkAt(bp);
                    var st = loaded ? Bot.level().getBlockState(bp) : null;
                    Block b = loaded ? st.getBlock() : Memory.remembered(bp);
                    if (b == null || b.defaultBlockState().isAir()) continue;
                    String id = Bot.id(b);
                    boolean be = b.defaultBlockState().hasBlockEntity();
                    if (skip.matcher(id).find() || !(be || mover.matcher(id).find())) continue;
                    JsonObject o = J.obj("id", id, "name", b.getName().getString(), "pos", J.arr(bp.getX(), bp.getY(), bp.getZ()));
                    if (loaded) {
                        // which way it faces (hoppers, conveyors, droppers push that way), and the rest of its look
                        JsonObject props = new JsonObject();
                        for (var prop : st.getProperties()) {
                            String pn = prop.getName();
                            if (pn.matches("facing|horizontal_facing|axis|type|half|shape|direction|mode|enabled|powered|north|south|east|west|up|down")) {
                                props.addProperty(pn, st.getValue(prop).toString());
                            }
                        }
                        if (props.size() > 0) o.add("props", props);
                        var bent = Bot.level().getBlockEntity(bp);
                        if (bent != null) {
                            // IE conveyors keep their direction in the block entity
                            try {
                                Object f = bent.getClass().getMethod("getFacing").invoke(bent);
                                if (f != null) o.addProperty("be_facing", f.toString());
                            } catch (Exception ignored) {
                            }
                        }
                        o.addProperty("gui", bent instanceof net.minecraft.world.MenuProvider || st.getMenuProvider(Bot.level(), bp) != null);
                    }
                    out.add(o);
                    if (out.size() >= 1500) break;
                }
                return J.obj("ok", true, "msg", "схема: " + out.size() + " блоков", "blocks", out);
            }
            case "inspect": {
                BlockPos at = BlockPos.containing(J.dbl(a, "x", 0), J.dbl(a, "y", 0), J.dbl(a, "z", 0));
                if (Bot.level().getBlockState(at).isAir()) return err("в " + Bot.pos(at) + " пусто");
                return start(MachineTask.inspect(at));
            }
            case "look_around":
                return ok("осмотрелся: " + Memory.lookAround() + " точек");
            case "find_block": {
                List<Block> blocks = Names.blocks(list(a, "block"));
                if (blocks.isEmpty()) return err("не знаю такой блок");
                int radius = Math.min(J.num(a, "radius", 64), 128);
                List<BlockPos> found = Info.findBlocks(new HashSet<>(blocks), radius, 8);
                if (found.isEmpty()) {
                    Memory.lookAround();   // not remembered: look around first, like a person would
                    found = Info.findBlocks(new HashSet<>(blocks), radius, 8);
                }
                if (found.isEmpty()) return ok("рядом (в загруженных чанках) не нашёл: " + ids(blocks));
                StringBuilder sb = new StringBuilder("Нашёл " + ids(blocks) + ":\n");
                for (BlockPos bp : found) sb.append("- ").append(Bot.pos(bp)).append(" (").append(Math.round(Bot.distTo(bp))).append(" бл.)\n");
                return ok(sb.toString().trim());
            }
            case "stations": {
                // work blocks the bot remembers nearby (furnace, crafting table...): the planner does not build new ones
                JsonObject found = new JsonObject();
                int radius = Math.min(J.num(a, "radius", 96), 400);   // mod machines seen far away are worth walking back to
                for (String id : list(a, "blocks")) {
                    ResourceLocation rl = ResourceLocation.tryParse(id);
                    if (rl == null || !BuiltInRegistries.BLOCK.containsKey(rl)) continue;
                    List<BlockPos> at = Info.findBlocks(Set.of(BuiltInRegistries.BLOCK.get(rl)), radius, 1);
                    if (!at.isEmpty()) found.addProperty(id, Math.round(Bot.distTo(at.get(0))));
                }
                return J.obj("ok", true, "msg", "станки рядом: " + found, "found", found);
            }
            case "recipe": {
                String q = J.str(a, "item", "");
                StringBuilder sb = new StringBuilder();
                for (var r : TaczCompat.find(q, 5)) {
                    sb.append("- [оружейный верстак TaCZ] ").append(TaczCompat.output(r).getHoverName().getString())
                            .append(" <- ").append(TaczCompat.ingredients(r)).append('\n');
                }
                Item it = item(a);
                if (it != null) sb.append(Info.recipes(it, 6));
                if (sb.length() == 0) return err("не знаю такой предмет: " + q);
                return ok(sb.toString().trim());
            }
            case "container":
                return ok(Info.container());

            // ---------- movement ----------
            case "stop":
                BotClient.setTask(null);
                Baritone.cancel();
                Input.releaseAll();
                Guns.ceaseFire();
                return ok("остановился");
            case "follow":
                return start(new FollowTask(who(a), false));
            case "guard":
                return start(new FollowTask(who(a), true));
            case "goto":
                return start(new GotoTask("goto", pos(a), J.num(a, "range", 1)));
            case "come": {
                Player t = Bot.findPlayer(who(a));
                if (t == null) return err("не вижу игрока " + who(a));
                return start(new GotoTask("come", t.blockPosition(), 2, t.getGameProfile().getName()));
            }
            case "turn": {
                // "turn your head right / look at me / turn around": the head turns and stays there a few seconds
                String dir = J.str(a, "direction", "player").toLowerCase(java.util.Locale.ROOT).trim();
                int deg = J.num(a, "degrees", 90);
                int hold = Math.max(1, J.num(a, "seconds", 5)) * 20;
                Bot.turnOnRequest(hold);
                float yaw = p.getYRot(), pitch = 0;
                switch (dir) {
                    case "right", "направо", "вправо" -> yaw += deg;
                    case "left", "налево", "влево" -> yaw -= deg;
                    case "back", "around", "назад", "кругом" -> yaw += 180;
                    case "up", "вверх" -> pitch = -45;
                    case "down", "вниз" -> pitch = 45;
                    case "forward", "вперед", "вперёд", "прямо" -> { }
                    default -> {
                        String whom = dir.equals("player") || dir.equals("me") || dir.equals("меня") || dir.isBlank() ? who(a) : J.str(a, "direction", "");
                        Player t = Bot.findPlayer(whom);
                        if (t == null) return err("не вижу игрока " + whom);
                        Bot.lookAndHold(t, hold);
                        return ok("смотрю на " + t.getGameProfile().getName());
                    }
                }
                Bot.lookAndHold(p.getEyePosition().add(Vec3.directionFromRotation(pitch, yaw).scale(10)), hold);
                return ok("повернул голову: " + dir);
            }
            case "attention":
                // someone spoke to Altron: turn to him, like a person who hears his name
                BotClient.attention(who(a), J.num(a, "ticks", 100));
                return ok("смотрю на " + who(a));
            case "climb": {
                // up or down the nearest ladder (any mod's), like a player: for when walking there does not work
                boolean up = !J.str(a, "direction", "up").toLowerCase(java.util.Locale.ROOT).matches("down|вниз|спуст.*");
                int y = J.has(a, "y") ? J.num(a, "y", 0) : p.getBlockY() + (up ? 64 : -64);
                return start(new com.altron.bot.tasks.ClimbTask(up, y));
            }
            case "photo": {
                // a picture from where he is, looking straight at a point, saved to a file (for working a base out)
                String file = J.str(a, "file", "photos/photo.png");
                return start(new com.altron.bot.tasks.PhotoTask(new Vec3(J.dbl(a, "x", 0), J.dbl(a, "y", 0), J.dbl(a, "z", 0)), file,
                        a.has("auto") && a.get("auto").getAsBoolean(), J.num(a, "max_r", 8)));
            }
            case "look_at":
                Bot.lookAt(new Vec3(J.dbl(a, "x", 0), J.dbl(a, "y", 0), J.dbl(a, "z", 0)));
                return ok("смотрю туда");

            // ---------- resources ----------
            case "mine": {
                List<Block> blocks = Names.blocks(list(a, "blocks"));
                if (blocks.isEmpty()) return err("не понял, какие блоки копать: " + list(a, "blocks"));
                return start(new MineTask(blocks, J.num(a, "count", 8)));
            }
            case "collect_items":
                return start(new CollectTask(J.num(a, "radius", 12), null));
            case "break_block":
                return start(new BreakTask(pos(a)));
            case "place_block": {
                Item it = item(a);
                if (it == null) return err("не знаю предмет " + J.str(a, "item", ""));
                return start(new PlaceTask(it, J.has(a, "x") ? pos(a) : null));
            }
            case "transport_block":
                return transport(a);

            // ---------- combat ----------
            case "attack":
                return start(new AttackTask(J.str(a, "target", "hostile"), J.num(a, "radius", 32), J.num(a, "seconds", 180)));
            case "reload":
                Guns.reload(p);
                return ok("перезаряжаюсь");

            // ---------- items ----------
            case "equip":
                return equip(a);
            case "give": {
                Item it = item(a);
                if (it == null) return err("не знаю предмет " + J.str(a, "item", ""));
                return start(new GiveTask(who(a), it, J.num(a, "count", 0)));
            }
            case "drop": {
                Item it = item(a);
                if (it == null) return err("не знаю предмет " + J.str(a, "item", ""));
                return start(new GiveTask(null, it, J.num(a, "count", 0)));
            }
            case "craft": {
                String q = J.str(a, "item", "");
                Item it = item(a);
                boolean vanilla = it != null && !CraftTask.recipesFor(it).isEmpty();
                if (!vanilla) {
                    // guns, ammo and attachments are made at the TaCZ gunsmith table
                    var tacz = TaczCompat.find(q, 1);
                    if (!tacz.isEmpty()) return start(new TaczCraftTask(tacz.get(0), J.num(a, "count", 1)));
                }
                if (it == null) return err("не знаю предмет " + q);
                return start(new CraftTask(it, J.num(a, "count", 1), 0));
            }
            case "smelt": {
                Item it = item(a);
                if (it == null) return err("не знаю предмет " + J.str(a, "item", ""));
                return start(new SmeltTask(it, J.num(a, "count", 1)));
            }
            case "eat":
                return start(new EatTask());
            case "use_item": {
                Item it = J.str(a, "item", "").isBlank() ? null : item(a);
                if (!J.str(a, "item", "").isBlank() && it == null) return err("не знаю предмет " + J.str(a, "item", ""));
                return start(new com.altron.bot.tasks.UseItemTask(it, J.num(a, "ticks", 5)));
            }

            // ---------- blocks & containers ----------
            case "use_block": {
                Item it = J.str(a, "item", "").isBlank() ? null : item(a);
                if (!J.str(a, "item", "").isBlank() && it == null) return err("не знаю предмет " + J.str(a, "item", ""));
                boolean sneak = a.has("sneak") && a.get("sneak").getAsBoolean();
                return start(new UseBlockTask(pos(a), it, sneak, J.num(a, "ticks", 0)));
            }
            case "build_multiblock": {
                String q = J.str(a, "name", "");
                Object mb = MultiblockCompat.find(q);
                if (mb == null) return err("не знаю постройку \"" + q + "\". Есть: " + MultiblockCompat.list());
                return start(new BuildMultiblockTask(mb, J.has(a, "x") ? pos(a) : null));
            }
            case "multiblocks":
                return ok("Многоблочные постройки: " + MultiblockCompat.list());
            case "drive":
                return start(new DriveTask(J.dbl(a, "x", 0), J.dbl(a, "z", 0)));
            case "item_info": {
                Item it = item(a);
                if (it == null) return err("не знаю предмет " + J.str(a, "item", ""));
                int idx = Inv.find(p, s -> s.is(it));
                ItemStack st = idx >= 0 ? p.getInventory().getItem(idx) : new ItemStack(it);
                StringBuilder sb = new StringBuilder();
                for (var line : st.getTooltipLines(p, net.minecraft.world.item.TooltipFlag.Default.ADVANCED)) {
                    sb.append(line.getString()).append('\n');
                }
                return ok(sb.toString().trim());
            }
            // ---------- any screen, like a mouse and keyboard ----------
            case "render": {
                // draw the world all the time (to watch Altron's window) or only when needed (saves GPU and CPU)
                BotClient.renderForced = a.has("on") && a.get("on").getAsBoolean();
                return ok(BotClient.renderForced ? "окно рисуется постоянно" : "окно рисуется только по надобности");
            }
            case "screen":
                return ok(ScreenControl.info());
            case "screenshot":
                return screenshot();
            case "gui_click":
                return ok(ScreenControl.click(J.dbl(a, "x", 0), J.dbl(a, "y", 0), J.num(a, "button", 0)));
            case "gui_widget":
                return ok(ScreenControl.clickWidget(J.num(a, "widget", -1)));
            case "gui_type":
                return ok(ScreenControl.type(J.str(a, "text", "")));
            case "gui_key":
                return ok(ScreenControl.key(J.str(a, "key", "")));
            case "click_slot": {
                // works on the open container, or on the player's own inventory when nothing is open
                ClickType type = switch (J.str(a, "type", "pickup").toLowerCase()) {
                    case "quick_move", "shift" -> ClickType.QUICK_MOVE;
                    case "swap" -> ClickType.SWAP;
                    case "throw" -> ClickType.THROW;
                    case "pickup_all" -> ClickType.PICKUP_ALL;
                    default -> ClickType.PICKUP;
                };
                Inv.click(p.containerMenu, J.num(a, "slot", 0), J.num(a, "button", 0), type);
                return ok("клик по слоту " + J.num(a, "slot", 0) + " (" + type + ")");
            }
            case "container_take":
                return moveItems(a, false);
            case "container_put":
                return moveItems(a, true);
            case "close_container":
                Bot.closeContainer();
                return ok("закрыл");
            case "container_button": {
                // vanilla-style GUI buttons (enchanting table, stonecutter, many mod machines)
                if (p.containerMenu == p.inventoryMenu) return err("окно не открыто");
                Bot.mc().gameMode.handleInventoryButtonClick(p.containerMenu.containerId, J.num(a, "id", 0));
                return ok("нажал кнопку " + J.num(a, "id", 0));
            }
            case "use_entity": {
                // with an item: apply it to the creature (a SecurityCraft remote to a sentry, a lead, shears...)
                Item it = J.str(a, "item", "").isBlank() ? null : item(a);
                if (!J.str(a, "item", "").isBlank() && it == null) return err("не знаю предмет " + J.str(a, "item", ""));
                boolean sneak = a.has("sneak") && a.get("sneak").getAsBoolean();
                return start(new UseEntityTask(J.str(a, "target", ""), J.num(a, "ticks", 5), false).withItem(it, sneak));
            }
            case "revive":
                return start(new UseEntityTask(who(a), J.num(a, "ticks", 200), true));

            // ---------- raw control ----------
            case "press_key": {
                KeyMapping km = Input.find(J.str(a, "key", ""));
                if (km == null) return err("нет такой клавиши: " + J.str(a, "key", ""));
                Input.hold(km.getKey(), J.num(a, "ticks", 2));
                return ok("нажал " + km.getName());
            }
            case "chat":
                Bot.chat(J.str(a, "text", ""));
                return ok("отправил");
            case "baritone": {
                String cmd = J.str(a, "command", "").replaceFirst("^#", "");
                String unfair = Baritone.checkFair(cmd);
                if (unfair != null) return err(unfair);
                return Baritone.command(cmd) ? ok("baritone: " + cmd) : err("baritone не принял команду: " + cmd);
            }
            default:
                return err("неизвестная команда: " + name);
        }
    }

    static JsonObject screenshot() {
        try {
            return J.obj("ok", true, "msg", "снимок экрана", "image", ScreenControl.screenshotBase64(),
                    "width", Bot.mc().getWindow().getWidth(), "height", Bot.mc().getWindow().getHeight());
        } catch (Exception e) {
            return err("не смог сделать снимок: " + e);
        }
    }

    private static String ids(List<Block> blocks) {
        List<String> s = new ArrayList<>();
        for (Block b : blocks) s.add(Bot.id(b));
        return String.join(", ", s);
    }

    private static JsonObject equip(JsonObject a) {
        LocalPlayer p = Bot.player();
        Item it = item(a);
        if (it == null) return err("не знаю предмет " + J.str(a, "item", ""));
        int idx = Inv.find(p, s -> s.is(it));
        if (idx < 0) return err("нет в инвентаре: " + Bot.id(it));
        if (it instanceof ArmorItem) {
            Bot.closeContainer();
            Inv.click(p.inventoryMenu, Inv.menuSlot(idx), 0, ClickType.QUICK_MOVE);
            return ok("надел " + Bot.id(it));
        }
        Inv.hold(p, idx);
        return ok("взял в руку " + Bot.id(it));
    }

    /** Shift-click items between an open container and the inventory. */
    private static JsonObject moveItems(JsonObject a, boolean put) {
        LocalPlayer p = Bot.player();
        AbstractContainerMenu menu = p.containerMenu;
        if (menu == p.inventoryMenu) return err("сначала открой контейнер (use_block)");
        String q = J.str(a, "item", "");
        Item it = q.isBlank() || q.equals("*") || q.equals("all") ? null : Names.item(q);
        if (!q.isBlank() && !q.equals("*") && !q.equals("all") && it == null) return err("не знаю предмет " + q);
        int want = J.num(a, "count", 0);
        if (want <= 0) want = Integer.MAX_VALUE;
        // "put everything away" keeps what he works and fights with: tools, weapons, armor, guns, food
        boolean keepGear = put && a.has("keep_gear") && a.get("keep_gear").getAsBoolean();
        Predicate<ItemStack> gear = s -> s.isDamageableItem() || Guns.isGun(s) || s.getItem().getFoodProperties(s, p) != null;
        Predicate<ItemStack> f = s -> !s.isEmpty() && (it == null || s.is(it)) && !(keepGear && it == null && gear.test(s));
        int moved = 0;
        for (Slot s : menu.slots) {
            if (moved >= want) break;
            if (Inv.isPlayerSlot(p, s) != put || !f.test(s.getItem())) continue;
            int before = s.getItem().getCount();
            Inv.click(menu, s.index, 0, ClickType.QUICK_MOVE);
            int after = s.getItem().isEmpty() ? 0 : s.getItem().getCount();
            if (after == before) {
                if (it == null) continue;   // this kind does not fit (or does not go in): try the next one
                break;                      // the target is full
            }
            moved += before - after;
        }
        if (moved == 0) return err(put ? "не получилось положить (нет предмета или некуда)" : "не получилось взять (нет предмета или инвентарь полон)");
        return ok((put ? "положил " : "взял ") + moved + " шт." + (it != null ? " " + Bot.id(it) : ""));
    }

    /** Break a block, pick it up (with its contents, e.g. a full barrel) and place it elsewhere. */
    private static JsonObject transport(JsonObject a) {
        BlockPos from = BlockPos.containing(J.dbl(a, "x", 0), J.dbl(a, "y", 0), J.dbl(a, "z", 0));
        BlockPos to = BlockPos.containing(J.dbl(a, "to_x", 0), J.dbl(a, "to_y", 0), J.dbl(a, "to_z", 0));
        return start(new TransportTask(from, to));
    }
}
