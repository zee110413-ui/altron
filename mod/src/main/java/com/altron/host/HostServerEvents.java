package com.altron.host;

import com.altron.AltronMod;
import com.altron.Config;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraftforge.common.MinecraftForge;
import net.minecraftforge.event.OnDatapackSyncEvent;
import net.minecraftforge.event.TickEvent;
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
