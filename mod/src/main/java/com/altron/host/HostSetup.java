package com.altron.host;

import com.altron.AltronMod;
import com.altron.Config;
import com.altron.J;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import net.minecraft.commands.arguments.blocks.BlockStateParser;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.tags.BlockTags;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.Container;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.entity.item.ItemEntity;
import net.minecraft.world.entity.monster.Monster;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.entity.MobSpawnType;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.GameRules;
import net.minecraft.world.level.GameType;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.LadderBlock;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.levelgen.Heightmap;
import net.minecraftforge.server.ServerLifecycleHooks;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Prepares a demo world on the host's integrated server, on request of the brain:
 * ore deposits, chests with items, mobs, time/weather, the host player as a spectator camera.
 */
public final class HostSetup {
    private HostSetup() {
    }

    public static void apply(JsonObject msg) {
        MinecraftServer server = ServerLifecycleHooks.getCurrentServer();
        if (server == null) return;
        server.execute(() -> {
            try {
                run(server, msg);
            } catch (Exception e) {
                AltronMod.LOG.error("[Altron] setup failed", e);
            }
        });
    }

    /** Solid ground height of a column (first free block above it), ignoring leaves and grass. */
    private static int ground(ServerLevel level, Map<Long, Integer> cache, int x, int z) {
        return cache.computeIfAbsent(BlockPos.asLong(x, 0, z), k -> level.getHeight(Heightmap.Types.MOTION_BLOCKING_NO_LEAVES, x, z));
    }

    /**
     * A flat grass clearing of the given radius at the typical ground level around the origin, so a demo
     * does not depend on the terrain the seed happens to give (mountain tops, ravines, lakes).
     * Everyone standing there is moved onto the new ground. Returns the new origin.
     */
    private static BlockPos flatten(ServerLevel level, BlockPos origin, int r, MinecraftServer server) {
        List<Integer> heights = new ArrayList<>();
        for (int dx = -r; dx <= r; dx += 2) {
            for (int dz = -r; dz <= r; dz += 2) {
                heights.add(level.getHeight(Heightmap.Types.MOTION_BLOCKING_NO_LEAVES, origin.getX() + dx, origin.getZ() + dz));
            }
        }
        Collections.sort(heights);
        int y0 = heights.get(heights.size() / 2);
        BlockPos.MutableBlockPos m = new BlockPos.MutableBlockPos();
        for (int dx = -r; dx <= r; dx++) {
            for (int dz = -r; dz <= r; dz++) {
                int x = origin.getX() + dx, z = origin.getZ() + dz;
                for (int y = y0; y <= y0 + 30; y++) {
                    m.set(x, y, z);
                    if (level.getBlockState(m).isAir()) continue;
                    // empty containers first: a removed chest would spill its loot all over the arena
                    if (level.getBlockEntity(m) instanceof Container c) c.clearContent();
                    level.setBlock(m, Blocks.AIR.defaultBlockState(), 2);
                }
                level.setBlock(m.set(x, y0 - 1, z), Blocks.GRASS_BLOCK.defaultBlockState(), 2);
                for (int y = y0 - 2; y >= y0 - 4; y--) level.setBlock(m.set(x, y, z), Blocks.DIRT.defaultBlockState(), 2);
                for (int y = y0 - 5; y >= y0 - 40; y--) {
                    m.set(x, y, z);
                    if (level.getBlockState(m).isSolidRender(level, m)) break;
                    level.setBlock(m, Blocks.STONE.defaultBlockState(), 2);
                }
            }
        }
        BlockPos o = new BlockPos(origin.getX(), y0, origin.getZ());
        // no leftovers in the clearing: dropped items and monsters
        AABB area = new AABB(origin.getX() - r, y0 - 8, origin.getZ() - r, origin.getX() + r + 1, y0 + 32, origin.getZ() + r + 1);
        level.getEntitiesOfClass(ItemEntity.class, area).forEach(Entity::discard);
        level.getEntitiesOfClass(Monster.class, area).forEach(Entity::discard);
        for (ServerPlayer p : server.getPlayerList().getPlayers()) {
            if (p.level() == level && p.blockPosition().distManhattan(origin) < r * 3) {
                p.teleportTo(level, p.getX(), y0, p.getZ(), p.getYRot(), p.getXRot());
            }
        }
        AltronMod.LOG.info("[Altron] demo arena: flattened radius {} at y={}", r, y0);
        return o;
    }

    /** "minecraft:lever[face=wall,facing=north]" or just "minecraft:stone"; null if there is no such block. */
    private static BlockState state(String s) {
        try {
            return BlockStateParser.parseForBlock(BuiltInRegistries.BLOCK.asLookup(), s, false).blockState();
        } catch (Exception e) {
            // a mod's ladder may have no "facing": then the block as it is
            return s.contains("[") ? state(s.substring(0, s.indexOf('['))) : null;
        }
    }

    /**
     * For the test course: move the players, reset blocks, and report what the world really is on the server
     * (block states, where the players stand, what they carry), not what Altron thinks it is.
     */
    public static void probe(JsonObject msg) {
        MinecraftServer server = ServerLifecycleHooks.getCurrentServer();
        if (server == null) return;
        server.execute(() -> {
            JsonObject r = new JsonObject();
            r.addProperty("type", "probe_result");
            r.add("rid", msg.get("rid"));
            try {
                probe(server, msg, r);
            } catch (Exception e) {
                AltronMod.LOG.error("[Altron] probe failed", e);
                r.addProperty("error", e.toString());
            }
            HostBridge.LINK.send(r);
        });
    }

    private static void probe(MinecraftServer server, JsonObject msg, JsonObject r) {
        ServerLevel level = server.overworld();
        ServerPlayer bot = server.getPlayerList().getPlayerByName(Config.BOT_NAME);
        ServerPlayer host = null;
        for (ServerPlayer p : server.getPlayerList().getPlayers()) {
            if (p != bot) {
                host = p;
                break;
            }
        }
        if (msg.has("fill")) {
            // [[x1,y1,z1,x2,y2,z2,"state"]]: clear or lay a box before a scenario (containers emptied first, no loot)
            for (JsonElement e : msg.getAsJsonArray("fill")) {
                JsonArray a = e.getAsJsonArray();
                BlockState st = state(a.get(6).getAsString());
                if (st == null) continue;
                BlockPos.MutableBlockPos m = new BlockPos.MutableBlockPos();
                for (int x = Math.min(a.get(0).getAsInt(), a.get(3).getAsInt()); x <= Math.max(a.get(0).getAsInt(), a.get(3).getAsInt()); x++) {
                    for (int y = Math.min(a.get(1).getAsInt(), a.get(4).getAsInt()); y <= Math.max(a.get(1).getAsInt(), a.get(4).getAsInt()); y++) {
                        for (int z = Math.min(a.get(2).getAsInt(), a.get(5).getAsInt()); z <= Math.max(a.get(2).getAsInt(), a.get(5).getAsInt()); z++) {
                            m.set(x, y, z);
                            if (level.getBlockState(m) == st) continue;
                            if (level.getBlockEntity(m) instanceof Container c) c.clearContent();
                            level.setBlock(m, st, 18);
                        }
                    }
                }
            }
        }
        if (msg.has("clear_entities")) {
            // [x,y,z,r]: dropped items and creatures of the last scenario go away
            JsonArray a = msg.getAsJsonArray("clear_entities");
            double rad = a.get(3).getAsDouble();
            AABB box = new AABB(a.get(0).getAsDouble() - rad, a.get(1).getAsDouble() - rad, a.get(2).getAsDouble() - rad,
                    a.get(0).getAsDouble() + rad, a.get(1).getAsDouble() + rad, a.get(2).getAsDouble() + rad);
            // everything but the players: dropped items, creatures, and the vehicles of the last scenario
            for (ServerPlayer p : server.getPlayerList().getPlayers()) p.stopRiding();
            level.getEntitiesOfClass(Entity.class, box, en -> !(en instanceof net.minecraft.world.entity.player.Player))
                    .forEach(Entity::discard);
        }
        if (msg.has("heal")) {
            for (ServerPlayer p : server.getPlayerList().getPlayers()) {
                // the Incapacitated mod lays a player "down" instead of killing him: stand him up
                try {
                    server.getCommands().performPrefixedCommand(server.createCommandSourceStack().withSuppressedOutput(),
                            "incap incapacitated " + p.getGameProfile().getName() + " false");
                } catch (Throwable ignored) {
                }
                p.setHealth(p.getMaxHealth());
                p.getFoodData().setFoodLevel(20);
                p.getFoodData().setSaturation(10);
                p.clearFire();
                if (p.isDeadOrDying()) continue;
                p.setAirSupply(p.getMaxAirSupply());
            }
        }
        if (msg.has("protect")) {
            // mod tests: exploding vehicles and grenades must not lay the players down (the Incapacitated mod
            // then keeps them out of the game and every following check fails)
            for (ServerPlayer p : server.getPlayerList().getPlayers()) {
                p.addEffect(new net.minecraft.world.effect.MobEffectInstance(net.minecraft.world.effect.MobEffects.DAMAGE_RESISTANCE, 20 * 120, 4, false, false));
                p.addEffect(new net.minecraft.world.effect.MobEffectInstance(net.minecraft.world.effect.MobEffects.FIRE_RESISTANCE, 20 * 120, 0, false, false));
                p.addEffect(new net.minecraft.world.effect.MobEffectInstance(net.minecraft.world.effect.MobEffects.WATER_BREATHING, 20 * 120, 0, false, false));
            }
        }
        if (msg.has("hurt") && bot != null) bot.setHealth(Math.max(1, J.num(msg, "hurt", 10)));
        if (msg.has("regen")) {
            // off while items are tried: natural healing must not pass for a medkit's effect
            server.getGameRules().getRule(GameRules.RULE_NATURAL_REGENERATION).set(msg.get("regen").getAsBoolean(), server);
        }
        if (msg.has("fall_damage")) {
            server.getGameRules().getRule(GameRules.RULE_FALL_DAMAGE).set(msg.get("fall_damage").getAsBoolean(), server);
        }
        if (msg.has("containers")) {
            // [{"pos":[x,y,z],"block":"minecraft:barrel[facing=up]","items":[["id",n]]}]: a filled chest/barrel
            for (JsonElement e : msg.getAsJsonArray("containers")) {
                JsonObject c = e.getAsJsonObject();
                BlockPos p = pos(c.get("pos"));
                BlockState st = state(J.str(c, "block", "minecraft:chest"));
                if (st == null) continue;
                if (level.getBlockEntity(p) instanceof Container old) old.clearContent();
                level.setBlock(p, st, 3);
                if (level.getBlockEntity(p) instanceof Container box) {
                    box.clearContent();
                    int slot = 0;
                    for (JsonElement it : c.getAsJsonArray("items")) {
                        JsonArray ia = it.getAsJsonArray();
                        Item item = BuiltInRegistries.ITEM.get(ResourceLocation.tryParse(ia.get(0).getAsString()));
                        int n = ia.get(1).getAsInt();
                        while (n > 0 && slot < box.getContainerSize()) {
                            int take = Math.min(n, item.getMaxStackSize());
                            box.setItem(slot++, new ItemStack(item, take));
                            n -= take;
                        }
                    }
                }
            }
        }
        if (msg.has("clear_bot") && bot != null) bot.getInventory().clearContent();
        if (msg.has("clear_host") && host != null) host.getInventory().clearContent();
        if (msg.has("give_bot") && bot != null) {
            for (JsonElement it : msg.getAsJsonArray("give_bot")) {
                JsonArray ia = it.getAsJsonArray();
                Item item = BuiltInRegistries.ITEM.get(ResourceLocation.tryParse(ia.get(0).getAsString()));
                bot.getInventory().add(new ItemStack(item, ia.get(1).getAsInt()));
            }
        }
        if (msg.has("set")) {
            for (JsonElement e : msg.getAsJsonArray("set")) {
                JsonArray a = e.getAsJsonArray();
                BlockState st = state(a.get(3).getAsString());
                if (st != null) level.setBlock(new BlockPos(a.get(0).getAsInt(), a.get(1).getAsInt(), a.get(2).getAsInt()), st, 18);
            }
        }
        if (msg.has("tp_bot") && bot != null) tp(bot, level, msg.getAsJsonArray("tp_bot"));
        if (msg.has("tp_host") && host != null) tp(host, level, msg.getAsJsonArray("tp_host"));
        if (msg.has("host_container") && host != null) {
            // test of learning from the commander: he opens a chest, puts in / takes out, closes — as if by hand
            JsonObject hc = msg.getAsJsonObject("host_container");
            JsonArray pa = hc.getAsJsonArray("pos");
            BlockPos cpos = new BlockPos(pa.get(0).getAsInt(), pa.get(1).getAsInt(), pa.get(2).getAsInt());
            var cbe = level.getBlockEntity(cpos);
            if (cbe instanceof net.minecraft.world.MenuProvider mp && cbe instanceof net.minecraft.world.Container single) {
                // a double chest is one container of two halves
                net.minecraft.world.Container box = single;
                var cst = level.getBlockState(cpos);
                if (cst.getBlock() instanceof net.minecraft.world.level.block.ChestBlock cb) {
                    var both = net.minecraft.world.level.block.ChestBlock.getContainer(cb, cst, level, cpos, true);
                    if (both != null) box = both;
                }
                HostServerEvents.INSTANCE.clicked(host, cpos);
                host.openMenu(mp);
                if (hc.has("put")) {
                    for (JsonElement it : hc.getAsJsonArray("put")) {
                        Item item = BuiltInRegistries.ITEM.get(new ResourceLocation(it.getAsJsonArray().get(0).getAsString()));
                        int left = it.getAsJsonArray().get(1).getAsInt();
                        for (int i = 0; i < host.getInventory().getContainerSize() && left > 0; i++) {
                            ItemStack s = host.getInventory().getItem(i);
                            if (!s.is(item)) continue;
                            int n = Math.min(left, s.getCount());
                            ItemStack rest = net.minecraft.world.level.block.entity.HopperBlockEntity.addItem(null, box, s.split(n), null);
                            s.grow(rest.getCount());
                            left -= n - rest.getCount();
                        }
                    }
                }
                if (hc.has("take")) {
                    for (JsonElement it : hc.getAsJsonArray("take")) {
                        Item item = BuiltInRegistries.ITEM.get(new ResourceLocation(it.getAsJsonArray().get(0).getAsString()));
                        int left = it.getAsJsonArray().get(1).getAsInt();
                        for (int i = 0; i < box.getContainerSize() && left > 0; i++) {
                            ItemStack s = box.getItem(i);
                            if (!s.is(item)) continue;
                            ItemStack got = s.split(Math.min(left, s.getCount()));
                            left -= got.getCount();
                            host.getInventory().add(got);
                        }
                        box.setChanged();
                    }
                }
                host.closeContainer();
            }
        }
        if (msg.has("bot_gamemode") && bot != null) {
            // for photographing a base from above and through walls (spectator), then back to how he played
            GameType mode = GameType.byName(msg.get("bot_gamemode").getAsString(), GameType.SURVIVAL);
            bot.setGameMode(mode);
            // a dark workshop would come out black: night vision for the shoot, off again after it
            if (mode == GameType.SPECTATOR) {
                bot.addEffect(new net.minecraft.world.effect.MobEffectInstance(net.minecraft.world.effect.MobEffects.NIGHT_VISION, 20 * 60 * 30, 0, false, false));
            } else {
                bot.removeEffect(net.minecraft.world.effect.MobEffects.NIGHT_VISION);
            }
        }
        if (msg.has("blocks")) {
            JsonObject b = new JsonObject();
            for (JsonElement e : msg.getAsJsonArray("blocks")) {
                JsonArray a = e.getAsJsonArray();
                BlockPos p = new BlockPos(a.get(0).getAsInt(), a.get(1).getAsInt(), a.get(2).getAsInt());
                b.addProperty(p.getX() + "," + p.getY() + "," + p.getZ(), BlockStateParser.serialize(level.getBlockState(p)));
            }
            r.add("blocks", b);
        }
        if (msg.has("inspect")) {
            // what really lies in these containers
            JsonObject all = new JsonObject();
            for (JsonElement e : msg.getAsJsonArray("inspect")) {
                JsonArray a = e.getAsJsonArray();
                BlockPos p = new BlockPos(a.get(0).getAsInt(), a.get(1).getAsInt(), a.get(2).getAsInt());
                JsonObject items = new JsonObject();
                if (level.getBlockEntity(p) instanceof Container c) {
                    for (int i = 0; i < c.getContainerSize(); i++) {
                        ItemStack s = c.getItem(i);
                        if (s.isEmpty()) continue;
                        String id = BuiltInRegistries.ITEM.getKey(s.getItem()).toString();
                        items.addProperty(id, (items.has(id) ? items.get(id).getAsInt() : 0) + s.getCount());
                    }
                }
                all.add(p.getX() + "," + p.getY() + "," + p.getZ(), items);
            }
            r.add("inventories", all);
        }
        if (msg.has("climbables")) {
            // every ladder-like block of the pack's mods: the course builds one of them next to the vanilla ladder
            JsonArray c = new JsonArray();
            for (Block b : BuiltInRegistries.BLOCK) {
                if (b.defaultBlockState().is(BlockTags.CLIMBABLE) || b instanceof LadderBlock) {
                    c.add(BuiltInRegistries.BLOCK.getKey(b).toString());
                }
            }
            r.add("climbables", c);
        }
        if (msg.has("hunger") && bot != null) bot.getFoodData().setFoodLevel(J.num(msg, "hunger", 20));
        if (msg.has("give_tacz") && bot != null) {
            // [[gunId, ammoId, rounds]]: a TaCZ gun with a full magazine, and spare rounds
            for (JsonElement e : msg.getAsJsonArray("give_tacz")) {
                JsonArray a = e.getAsJsonArray();
                for (ItemStack s : TaczCatalog.build(a.get(0).getAsString(), a.get(1).getAsString(), a.get(2).getAsInt())) {
                    if (!bot.getInventory().add(s)) bot.drop(s, false);
                }
            }
        }
        if (msg.has("charge")) {
            // [x,y,z,r]: fill the energy of the vehicles there (SuperbWarfare / Ash Vehicle ones run on FE)
            JsonArray a = msg.getAsJsonArray("charge");
            double rad = a.get(3).getAsDouble();
            AABB box = new AABB(a.get(0).getAsDouble() - rad, a.get(1).getAsDouble() - rad, a.get(2).getAsDouble() - rad,
                    a.get(0).getAsDouble() + rad, a.get(1).getAsDouble() + rad, a.get(2).getAsDouble() + rad);
            for (Entity en : level.getEntitiesOfClass(Entity.class, box, en -> !(en instanceof ServerPlayer))) {
                en.getCapability(net.minecraftforge.common.capabilities.ForgeCapabilities.ENERGY)
                        .ifPresent(es -> es.receiveEnergy(Integer.MAX_VALUE, false));
            }
        }
        if (msg.has("give_tacz_tables") && bot != null) {
            // every TaCZ workbench as a player gets it: the item carries the table's type
            for (Object[] t : TaczCatalog.tableStacks()) {
                ItemStack s = (ItemStack) t[1];
                if (!bot.getInventory().add(s.copy())) bot.drop(s.copy(), false);
            }
        }
        if (msg.has("entities")) {
            // [x,y,z,r]: the creatures (not players) there: type, where, health
            JsonArray a = msg.getAsJsonArray("entities");
            double rad = a.get(3).getAsDouble();
            AABB box = new AABB(a.get(0).getAsDouble() - rad, a.get(1).getAsDouble() - rad, a.get(2).getAsDouble() - rad,
                    a.get(0).getAsDouble() + rad, a.get(1).getAsDouble() + rad, a.get(2).getAsDouble() + rad);
            JsonArray list = new JsonArray();
            for (Entity en : level.getEntitiesOfClass(Entity.class, box, en -> !(en instanceof ServerPlayer) && !(en instanceof ItemEntity))) {
                JsonObject o = new JsonObject();
                o.addProperty("type", String.valueOf(BuiltInRegistries.ENTITY_TYPE.getKey(en.getType())));
                o.add("pos", J.arr(en.getX(), en.getY(), en.getZ()));
                o.addProperty("alive", en.isAlive());
                if (en instanceof net.minecraft.world.entity.LivingEntity le) o.addProperty("hp", le.getHealth());
                o.addProperty("rider", !en.getPassengers().isEmpty());
                en.getCapability(net.minecraftforge.common.capabilities.ForgeCapabilities.ENERGY).ifPresent(es -> {
                    o.addProperty("energy", es.getEnergyStored());
                    o.addProperty("max_energy", es.getMaxEnergyStored());
                });
                list.add(o);
            }
            r.add("entities", list);
        }
        if (msg.has("catalog")) r.add("catalog", Catalog.build(level));
        if (msg.has("scan")) {
            // test diagnostics only (never given to Altron): which blocks with these words stand around a point
            JsonObject s = msg.getAsJsonObject("scan");
            JsonArray c = s.getAsJsonArray("center");
            int rad = J.num(s, "r", 64);
            List<String> words = new ArrayList<>();
            s.getAsJsonArray("words").forEach(w -> words.add(w.getAsString()));
            BlockPos ctr = new BlockPos(c.get(0).getAsInt(), c.get(1).getAsInt(), c.get(2).getAsInt());
            JsonObject hits = new JsonObject();
            for (BlockPos bp : BlockPos.betweenClosed(ctr.offset(-rad, -24, -rad), ctr.offset(rad, 24, rad))) {
                if (!level.isLoaded(bp)) continue;
                String id = BuiltInRegistries.BLOCK.getKey(level.getBlockState(bp).getBlock()).toString();
                for (String w : words) {
                    if (id.contains(w) && !hits.has(id)) hits.addProperty(id, bp.getX() + " " + bp.getY() + " " + bp.getZ());
                }
            }
            r.add("scan", hits);
        }
        // the inventory was changed from the server side (maybe while a chest was still open on the client):
        // send it whole again, or the client keeps stale items and "gives" what it no longer has
        for (ServerPlayer p : new ServerPlayer[]{bot, host}) {
            if (p == null) continue;
            p.inventoryMenu.sendAllDataToRemote();
            if (p.containerMenu != p.inventoryMenu) p.containerMenu.sendAllDataToRemote();
        }
        if (bot != null) r.add("bot", player(bot));
        if (host != null) r.add("host", player(host));
        synchronized (HostServerEvents.BOT_BROKE) {
            JsonArray broke = new JsonArray();
            int from = Math.max(0, HostServerEvents.BOT_BROKE.size() - 40);
            for (String b : HostServerEvents.BOT_BROKE.subList(from, HostServerEvents.BOT_BROKE.size())) broke.add(b);
            r.addProperty("bot_broke_count", HostServerEvents.BOT_BROKE.size());
            r.add("bot_broke", broke);
        }
    }

    private static void tp(ServerPlayer p, ServerLevel level, JsonArray a) {
        p.stopRiding();   // out of the last scenario's vehicle first
        p.teleportTo(level, a.get(0).getAsDouble(), a.get(1).getAsDouble(), a.get(2).getAsDouble(), p.getYRot(), p.getXRot());
        p.fallDistance = 0;
    }

    private static JsonObject player(ServerPlayer p) {
        JsonObject o = new JsonObject();
        o.add("pos", J.arr(p.getX(), p.getY(), p.getZ()));
        JsonObject items = new JsonObject();
        int gunAmmo = 0;
        for (int i = 0; i < p.getInventory().getContainerSize(); i++) {
            ItemStack s = p.getInventory().getItem(i);
            if (s.isEmpty()) continue;
            String id = BuiltInRegistries.ITEM.getKey(s.getItem()).toString();
            items.addProperty(id, (items.has(id) ? items.get(id).getAsInt() : 0) + s.getCount());
            // rounds loaded in TaCZ guns: did he really shoot?
            if (s.hasTag() && s.getTag().contains("GunCurrentAmmoCount")) gunAmmo += s.getTag().getInt("GunCurrentAmmoCount");
        }
        o.add("items", items);
        o.addProperty("gun_ammo", gunAmmo);
        o.addProperty("hp", p.getHealth());
        o.addProperty("food", p.getFoodData().getFoodLevel());
        JsonArray effects = new JsonArray();
        p.getActiveEffects().forEach(e -> effects.add(String.valueOf(BuiltInRegistries.MOB_EFFECT.getKey(e.getEffect()))));
        o.add("effects", effects);
        o.addProperty("cooldowns", p.getCooldowns().getCooldownPercent(p.getMainHandItem().getItem(), 0) > 0);
        o.addProperty("riding", p.getVehicle() == null ? "" : String.valueOf(BuiltInRegistries.ENTITY_TYPE.getKey(p.getVehicle().getType())));
        o.addProperty("screen_open", p.containerMenu != p.inventoryMenu);
        return o;
    }

    private static BlockPos pos(JsonElement e) {
        JsonArray a = e.getAsJsonArray();
        return new BlockPos(a.get(0).getAsInt(), a.get(1).getAsInt(), a.get(2).getAsInt());
    }

    private static void run(MinecraftServer server, JsonObject msg) {
        ServerLevel level = server.overworld();
        ServerPlayer bot = server.getPlayerList().getPlayerByName(Config.BOT_NAME);
        JsonObject report = new JsonObject();
        report.addProperty("type", "setup_done");
        // Anchor: coordinates in the message are relative to the bot (or world spawn) at ground level
        BlockPos origin = bot != null ? bot.blockPosition() : level.getSharedSpawnPos();
        if (msg.has("relative") && !msg.get("relative").getAsBoolean()) origin = BlockPos.ZERO;

        if (msg.has("time")) level.setDayTime(J.num(msg, "time", 1000));
        if (msg.has("clear_weather")) level.setWeatherParameters(20 * 60 * 60, 0, false, false);
        if (msg.has("daylight_cycle")) {
            server.getGameRules().getRule(GameRules.RULE_DAYLIGHT).set(msg.get("daylight_cycle").getAsBoolean(), server);
        }
        if (msg.has("mob_spawning")) {
            server.getGameRules().getRule(GameRules.RULE_DOMOBSPAWNING).set(msg.get("mob_spawning").getAsBoolean(), server);
        }

        if (msg.has("flatten")) origin = flatten(level, origin, J.num(msg, "flatten", 12), server);
        report.add("origin", J.arr(origin.getX(), origin.getY(), origin.getZ()));

        // dy is relative to the ground at (dx,dz) when "surface" is true. The ground of every column is
        // measured once, BEFORE anything is placed: otherwise each block would land on top of the previous one
        boolean surface = msg.has("surface") && msg.get("surface").getAsBoolean();
        Map<Long, Integer> ground = new HashMap<>();
        if (surface) {
            for (String key : new String[]{"blocks", "chests"}) {
                if (!msg.has(key)) continue;
                for (JsonElement e : msg.getAsJsonArray(key)) {
                    JsonArray a = e.isJsonArray() ? e.getAsJsonArray() : e.getAsJsonObject().getAsJsonArray("pos");
                    ground(level, ground, origin.getX() + a.get(0).getAsInt(), origin.getZ() + a.get(2).getAsInt());
                }
            }
        }
        int placed = 0;
        if (msg.has("blocks")) {
            // [[dx,dy,dz,"id"], ...]
            for (JsonElement e : msg.getAsJsonArray("blocks")) {
                JsonArray a = e.getAsJsonArray();
                int x = origin.getX() + a.get(0).getAsInt(), z = origin.getZ() + a.get(2).getAsInt();
                int y = (surface ? ground(level, ground, x, z) : origin.getY()) + a.get(1).getAsInt();
                String id = a.get(3).getAsString();
                BlockState st = state(id);
                if (st == null || (st.isAir() && !id.contains("air"))) continue;
                // a block with its state given ("minecraft:ladder[facing=west]", door halves) is set as it is:
                // no shape updates, so a ladder does not fall off before its wall is there
                level.setBlock(new BlockPos(x, y, z), st, id.contains("[") ? 18 : 3);
                placed++;
            }
        }
        if (msg.has("chests")) {
            for (JsonElement e : msg.getAsJsonArray("chests")) {
                JsonObject c = e.getAsJsonObject();
                BlockPos p = origin.offset(pos(c.get("pos")));
                if (surface) p = new BlockPos(p.getX(), ground(level, ground, p.getX(), p.getZ()) + pos(c.get("pos")).getY(), p.getZ());
                level.setBlock(p, Blocks.CHEST.defaultBlockState(), 3);
                if (level.getBlockEntity(p) instanceof Container box) {
                    int slot = 0;
                    for (JsonElement it : c.getAsJsonArray("items")) {
                        JsonArray ia = it.getAsJsonArray();
                        Item item = BuiltInRegistries.ITEM.get(new ResourceLocation(ia.get(0).getAsString()));
                        int n = ia.get(1).getAsInt();
                        while (n > 0 && slot < box.getContainerSize()) {
                            int take = Math.min(n, item.getMaxStackSize());
                            box.setItem(slot++, new ItemStack(item, take));
                            n -= take;
                        }
                    }
                }
                report.add("chest", J.arr(p.getX(), p.getY(), p.getZ()));
            }
        }
        if (msg.has("mobs")) {
            final BlockPos base = origin;
            for (JsonElement e : msg.getAsJsonArray("mobs")) {
                JsonArray a = e.getAsJsonArray();
                EntityType.byString(a.get(3).getAsString()).ifPresent(type -> {
                    BlockPos p = base.offset(a.get(0).getAsInt(), 0, a.get(2).getAsInt());
                    p = new BlockPos(p.getX(), level.getHeight(Heightmap.Types.WORLD_SURFACE, p.getX(), p.getZ()) + a.get(1).getAsInt(), p.getZ());
                    type.spawn(level, p, MobSpawnType.COMMAND);
                });
            }
        }
        if (msg.has("host_spectator")) {
            for (ServerPlayer p : server.getPlayerList().getPlayers()) {
                if (p != bot) {
                    p.setGameMode(GameType.SPECTATOR);
                    if (bot != null) p.teleportTo(level, bot.getX() + 3, bot.getY() + 3, bot.getZ() + 3, p.getYRot(), p.getXRot());
                }
            }
        }
        if (msg.has("give_bot") && bot != null) {
            for (JsonElement it : msg.getAsJsonArray("give_bot")) {
                JsonArray ia = it.getAsJsonArray();
                Item item = BuiltInRegistries.ITEM.get(new ResourceLocation(ia.get(0).getAsString()));
                bot.getInventory().add(new ItemStack(item, ia.get(1).getAsInt()));
            }
        }
        report.addProperty("placed", placed);
        HostBridge.LINK.send(report);
        AltronMod.LOG.info("[Altron] demo setup: {} blocks placed", placed);
    }
}
