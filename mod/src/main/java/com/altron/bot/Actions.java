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
import com.altron.bot.tasks.MachineTask;
import com.altron.bot.tasks.MineTask;
import com.altron.bot.tasks.PlaceTask;
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
import net.minecraft.network.protocol.game.ServerboundPlayerCommandPacket;
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
        leaveBed();
        int id = BotClient.setTask(t);
        return J.obj("ok", true, "msg", "начал: " + t.name(), "task_id", id);
    }

    /** A sleeping player can neither walk, mine nor fight: out of bed first. */
    private static void leaveBed() {
        LocalPlayer p = Bot.player();
        if (p != null && p.isSleeping()) {
            p.connection.send(new ServerboundPlayerCommandPacket(p, ServerboundPlayerCommandPacket.Action.STOP_SLEEPING));
        }
    }

    private static BlockPos pos(JsonObject a) {
        return BlockPos.containing(J.dbl(a, "x", 0), J.dbl(a, "y", 0), J.dbl(a, "z", 0));
    }

    /** Where control should move the mouse: x y z; whole numbers are a block, so the middle of it; no y — eye level. */
    private static Vec3 aimPoint(JsonObject a) {
        if (!J.has(a, "x") || !J.has(a, "z")) return null;
        double x = J.dbl(a, "x", 0), z = J.dbl(a, "z", 0);
        double y = J.has(a, "y") ? J.dbl(a, "y", 0) : Bot.player().getEyeY();
        return new Vec3(x == Math.floor(x) ? x + 0.5 : x, J.has(a, "y") && y == Math.floor(y) ? y + 0.5 : y,
                z == Math.floor(z) ? z + 0.5 : z);
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
            case "open_block": {
                // open a chest or a machine (by any side of it he can see) and leave its window open
                BlockPos at = BlockPos.containing(J.dbl(a, "x", 0), J.dbl(a, "y", 0), J.dbl(a, "z", 0));
                if (Bot.level().getBlockState(at).isAir()) return err("в " + Bot.pos(at) + " пусто");
                return start(MachineTask.open(at));
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
                leaveBed();
                BotClient.setTask(null);
                Nav.cancel();
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
                return start(new CraftTask(it, J.num(a, "count", 1)));
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
            case "build_plan": {
                // a plan the brain drew up: [[dx, dy, dz, "block id"], ...] from the building's corner
                JsonElement plan = a.get("blocks");
                if (plan == null || !plan.isJsonArray()) return err("нет плана (blocks)");
                List<net.minecraft.world.level.levelgen.structure.templatesystem.StructureTemplate.StructureBlockInfo> list = new ArrayList<>();
                for (JsonElement e : plan.getAsJsonArray()) {
                    var row = e.getAsJsonArray();
                    ResourceLocation id = ResourceLocation.tryParse(row.get(3).getAsString());
                    if (id == null || !BuiltInRegistries.BLOCK.containsKey(id)) return err("не знаю блок " + row.get(3).getAsString());
                    list.add(new net.minecraft.world.level.levelgen.structure.templatesystem.StructureTemplate.StructureBlockInfo(
                            new BlockPos(row.get(0).getAsInt(), row.get(1).getAsInt(), row.get(2).getAsInt()),
                            BuiltInRegistries.BLOCK.get(id).defaultBlockState(), null));
                    if (list.size() > 3000) return err("слишком большая постройка (больше 3000 блоков)");
                }
                // where it fits and what is missing; the AI puts the blocks in place with its own hands
                BlockPos[] at = new BlockPos[1];
                String why = com.altron.bot.tasks.BuildPlanTask.site(list, J.has(a, "x") ? pos(a) : null, at);
                if (!why.isEmpty()) return err(J.str(a, "what", "постройка") + ": " + why);
                JsonObject r = ok("место для " + J.str(a, "what", "постройки") + ": угол в " + Bot.pos(at[0]));
                r.add("origin", J.arr(at[0].getX(), at[0].getY(), at[0].getZ()));
                return r;
            }
            case "drive":
                // "езжай за мной": follow a player (the commander by default) instead of a point
                if (!J.has(a, "x") || J.str(a, "follow", "").equals("true")) return start(DriveTask.following(who(a)));
                return start(new DriveTask(J.dbl(a, "x", 0), J.dbl(a, "z", 0)));
            case "vehicle_gunner":
                // man the gun of the vehicle he sits in and shoot at hostile creatures he sees, until told to stop
                return start(new com.altron.bot.tasks.VehicleGunTask(J.dbl(a, "radius", 48), J.str(a, "target", "hostile")));
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
                return start(new UseEntityTask(who(a), J.num(a, "ticks", 200), true).near(J.has(a, "x") ? pos(a) : null));

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
            // ---------- the AI's own hands: keyboard and mouse ----------
            case "control": {
                List<KeyMapping> keys = new ArrayList<>();
                for (String k : list(a, "keys")) {
                    KeyMapping km = Input.find(k);
                    if (km == null) return err("нет такой клавиши: " + k);
                    keys.add(km);
                }
                return start(new com.altron.bot.tasks.ControlTask(keys, J.num(a, "ticks", 5), (float) J.dbl(a, "turn", 0),
                        (float) J.dbl(a, "tilt", 0), J.has(a, "pitch") ? (float) J.dbl(a, "pitch", 0) : null,
                        J.str(a, "left", ""), J.str(a, "right", ""), J.num(a, "slot", 0), aimPoint(a),
                        J.str(a, "track", "").isBlank() ? null : Combat.filterFor(J.str(a, "track", ""), BotClient.owner),
                        J.str(a, "track", "")));
            }
            case "view":
                return ok(com.altron.bot.tasks.ControlTask.view());
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
