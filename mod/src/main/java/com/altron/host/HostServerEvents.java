package com.altron.host;

import com.altron.AltronMod;
import com.altron.Config;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraftforge.common.MinecraftForge;
import net.minecraftforge.event.OnDatapackSyncEvent;
import net.minecraftforge.event.TickEvent;
import net.minecraftforge.event.entity.player.PlayerContainerEvent;
import net.minecraftforge.event.entity.player.PlayerEvent;
import net.minecraftforge.eventbus.api.SubscribeEvent;
import net.minecraftforge.server.ServerLifecycleHooks;

import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

/** Server-side helpers (runs inside the host's integrated server). */
public class HostServerEvents {
    /** Players who joined over the network: their mods' data is sent again in a moment. */
    private final Map<UUID, Integer> resync = new HashMap<>();

    @SubscribeEvent
    public void onLogin(PlayerEvent.PlayerLoggedInEvent event) {
        if (!(event.getEntity() instanceof ServerPlayer sp)) return;
        MinecraftServer server = sp.server;
        // Someone joining the opened world over the network (Altron, a friend) gets SuperbWarfare's gun and vehicle
        // data at the very moment of login, before his client listens on that mod's channel ("Unknown custom packet
        // identifier: superbwarfare:superbwarfare"): without it SuperbWarfare guns never fire and its vehicles do not
        // work for him. The host himself plays inside the server and always has it.
        if (!server.isSingleplayerOwner(sp.getGameProfile())) resync.put(sp.getUUID(), 60);
        if (!sp.getGameProfile().getName().equalsIgnoreCase(Config.BOT_NAME)) return;
        // The recipe book only auto-fills known recipes; unlock all of them for the bot
        int n = sp.awardRecipes(server.getRecipeManager().getRecipes());
        AltronMod.LOG.info("[Altron] bot joined, unlocked {} recipes", n);
    }

    // ------------------------------------------------------------------ learning from the commander's hands
    /** The block a player last right-clicked: the window he opens next belongs to it. */
    private final Map<UUID, net.minecraft.core.BlockPos> lastClicked = new HashMap<>();
    private record Watch(net.minecraft.core.BlockPos pos, String block, String name, Map<String, Integer> inv) {
    }
    private final Map<UUID, Watch> watching = new HashMap<>();

    private static boolean isBot(ServerPlayer sp) {
        return sp.getGameProfile().getName().equalsIgnoreCase(Config.BOT_NAME);
    }

    public static Map<String, Integer> snapshot(ServerPlayer sp) {
        Map<String, Integer> m = new HashMap<>();
        var inv = sp.getInventory();
        for (int i = 0; i < inv.getContainerSize(); i++) {
            var s = inv.getItem(i);
            if (!s.isEmpty()) m.merge(net.minecraft.core.registries.BuiltInRegistries.ITEM.getKey(s.getItem()).toString(), s.getCount(), Integer::sum);
        }
        return m;
    }

    public void clicked(ServerPlayer sp, net.minecraft.core.BlockPos pos) {
        lastClicked.put(sp.getUUID(), pos.immutable());
    }

    @SubscribeEvent
    public void onRightClickBlock(net.minecraftforge.event.entity.player.PlayerInteractEvent.RightClickBlock event) {
        if (event.getEntity() instanceof ServerPlayer sp && !isBot(sp)) clicked(sp, event.getPos());
    }

    @SubscribeEvent
    public void onContainerOpen(PlayerContainerEvent.Open event) {
        if (!(event.getEntity() instanceof ServerPlayer sp) || isBot(sp)) return;
        var pos = lastClicked.get(sp.getUUID());
        if (pos == null || pos.distSqr(sp.blockPosition()) > 100) return;
        var block = sp.level().getBlockState(pos).getBlock();
        watching.put(sp.getUUID(), new Watch(pos, net.minecraft.core.registries.BuiltInRegistries.BLOCK.getKey(block).toString(),
                block.getName().getString(), snapshot(sp)));
    }

    /** Closed the window: what went from his inventory into that block and what came out of it — to the brain. */
    @SubscribeEvent
    public void onContainerClose(PlayerContainerEvent.Close event) {
        if (!(event.getEntity() instanceof ServerPlayer sp)) return;
        Watch w = watching.remove(sp.getUUID());
        if (w == null) return;
        Map<String, Integer> now = snapshot(sp);
        com.google.gson.JsonArray put = new com.google.gson.JsonArray(), took = new com.google.gson.JsonArray();
        java.util.Set<String> ids = new java.util.HashSet<>(w.inv().keySet());
        ids.addAll(now.keySet());
        for (String id : ids) {
            int d = now.getOrDefault(id, 0) - w.inv().getOrDefault(id, 0);
            if (d == 0) continue;
            var item = net.minecraft.core.registries.BuiltInRegistries.ITEM.get(new net.minecraft.resources.ResourceLocation(id));
            com.google.gson.JsonArray e = new com.google.gson.JsonArray();
            e.add(id);
            e.add(Math.abs(d));
            e.add(new net.minecraft.world.item.ItemStack(item).getHoverName().getString());
            (d < 0 ? put : took).add(e);
        }
        if (put.isEmpty() && took.isEmpty()) return;
        HostBridge.LINK.send(com.altron.J.obj("type", "watch", "who", sp.getGameProfile().getName(),
                "pos", com.altron.J.arr(w.pos().getX(), w.pos().getY(), w.pos().getZ()), "block", w.block(), "name", w.name(),
                "put", put, "took", took));
    }

    /**
     * Altron picks up things lying about only while he is gathering them on purpose (mining, collecting, breaking) —
     * the brain turns this on for those jobs. Otherwise he would sweep the items off the commander's conveyor belts
     * just by walking past. What a player throws to him he always takes.
     */
    public static volatile boolean BOT_PICKUP = false;

    @SubscribeEvent
    public void onPickup(net.minecraftforge.event.entity.player.EntityItemPickupEvent event) {
        if (!(event.getEntity() instanceof ServerPlayer sp) || !isBot(sp) || BOT_PICKUP) return;
        var item = event.getItem();
        var thrower = item.getOwner();   // a player who dropped or threw it
        if (thrower instanceof net.minecraft.world.entity.player.Player && thrower != sp) return;
        event.setCanceled(true);
    }

    public static HostServerEvents INSTANCE;

    public HostServerEvents() {
        INSTANCE = this;
    }

    /** Blocks the bot broke in the world ("x y z id"), for the test course and missions: a guest must not wreck a base. */
    public static final java.util.List<String> BOT_BROKE = java.util.Collections.synchronizedList(new java.util.ArrayList<>());

    @SubscribeEvent
    public void onBreak(net.minecraftforge.event.level.BlockEvent.BreakEvent event) {
        if (event.getPlayer() == null || !event.getPlayer().getGameProfile().getName().equalsIgnoreCase(Config.BOT_NAME)) return;
        var pos = event.getPos();
        BOT_BROKE.add(pos.getX() + " " + pos.getY() + " " + pos.getZ() + " "
                + net.minecraft.core.registries.BuiltInRegistries.BLOCK.getKey(event.getState().getBlock()));
    }

    @SubscribeEvent
    public void onTick(TickEvent.ServerTickEvent event) {
        if (event.phase != TickEvent.Phase.END || resync.isEmpty()) return;
        MinecraftServer server = ServerLifecycleHooks.getCurrentServer();
        if (server == null) return;
        var it = resync.entrySet().iterator();
        while (it.hasNext()) {
            var e = it.next();
            int left = e.getValue() - 1;
            if (left > 0) {
                e.setValue(left);
                continue;
            }
            it.remove();
            ServerPlayer p = server.getPlayerList().getPlayer(e.getKey());
            if (p == null) continue;
            // the same event /reload fires: every mod sends its data (guns, vehicles, recipes...) to this player again
            MinecraftForge.EVENT_BUS.post(new OnDatapackSyncEvent(server.getPlayerList(), p));
            AltronMod.LOG.info("[Altron] mod data sent again to {}", p.getGameProfile().getName());
        }
    }
}
