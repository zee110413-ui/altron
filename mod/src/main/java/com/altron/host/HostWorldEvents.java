package com.altron.host;

import com.altron.Config;
import com.altron.J;
import net.minecraft.advancements.DisplayInfo;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.boss.enderdragon.EnderDragon;
import net.minecraft.world.entity.boss.wither.WitherBoss;
import net.minecraft.world.entity.monster.Creeper;
import net.minecraft.world.entity.monster.Enemy;
import net.minecraft.world.entity.monster.warden.Warden;
import net.minecraftforge.event.TickEvent;
import net.minecraftforge.event.entity.living.LivingDeathEvent;
import net.minecraftforge.event.entity.living.LivingHurtEvent;
import net.minecraftforge.event.entity.player.AdvancementEvent;
import net.minecraftforge.event.entity.player.PlayerEvent;
import net.minecraftforge.eventbus.api.SubscribeEvent;
import net.minecraftforge.server.ServerLifecycleHooks;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * What happens around the players in the host's world, for Altron to notice like a companion would: a player badly hurt
 * or killed, friends coming and going, advancements, a trip to another dimension, nightfall and storms, a creeper
 * hissing next to someone, a boss nearby, someone going hungry. Each kind is sent at most once in a while per player.
 */
public class HostWorldEvents {
    private final Map<String, Long> lastSent = new HashMap<>();
    private long lastDayTime = -1;
    private boolean wasThundering;

    private static boolean isBot(ServerPlayer sp) {
        return sp.getGameProfile().getName().equalsIgnoreCase(Config.BOT_NAME);
    }

    /** Send unless the same kind about the same player went out less than {@code gapMs} ago. */
    private void send(String kind, String who, long gapMs, Object... more) {
        sendAs(kind, kind, who, gapMs, more);
    }

    /** Each kind of danger keeps its own pause: a creeper's hiss must not silence the boss or the crowd warning. */
    private void danger(String what, String who, long gapMs, Object... more) {
        Object[] all = new Object[more.length + 2];
        all[0] = "what";
        all[1] = what;
        System.arraycopy(more, 0, all, 2, more.length);
        sendAs("danger", "danger:" + what, who, gapMs, all);
    }

    private void sendAs(String kind, String rateKey, String who, long gapMs, Object... more) {
        String key = rateKey + "|" + who;
        long now = System.currentTimeMillis();
        if (now - lastSent.getOrDefault(key, 0L) < gapMs) return;
        lastSent.put(key, now);
        Object[] base = {"type", "world_event", "kind", kind, "who", who};
        Object[] all = new Object[base.length + more.length];
        System.arraycopy(base, 0, all, 0, base.length);
        System.arraycopy(more, 0, all, base.length, more.length);
        HostBridge.LINK.send(J.obj(all));
    }

    private static String name(ServerPlayer sp) {
        return sp.getGameProfile().getName();
    }

    @SubscribeEvent
    public void onHurt(LivingHurtEvent event) {
        if (!(event.getEntity() instanceof ServerPlayer sp) || isBot(sp)) return;
        float left = sp.getHealth() - event.getAmount();
        if (left > 6 || left <= 0) return;   // three hearts or less, and still alive
        Entity by = event.getSource().getEntity();
        send("player_low_health", name(sp), 30_000, "hp", Math.max(0, Math.round(left)),
                "cause", by != null ? by.getName().getString() : event.getSource().getMsgId());
    }

    @SubscribeEvent
    public void onDeath(LivingDeathEvent event) {
        if (!(event.getEntity() instanceof ServerPlayer sp) || isBot(sp)) return;
        var p = sp.blockPosition();
        send("player_died", name(sp), 5_000, "text", event.getSource().getLocalizedDeathMessage(sp).getString(),
                "pos", J.arr(p.getX(), p.getY(), p.getZ()), "dim", sp.level().dimension().location().toString());
    }

    @SubscribeEvent
    public void onJoin(PlayerEvent.PlayerLoggedInEvent event) {
        if (event.getEntity() instanceof ServerPlayer sp && !isBot(sp)) send("player_joined", name(sp), 60_000);
    }

    @SubscribeEvent
    public void onLeave(PlayerEvent.PlayerLoggedOutEvent event) {
        if (event.getEntity() instanceof ServerPlayer sp && !isBot(sp)) send("player_left", name(sp), 60_000);
    }

    @SubscribeEvent
    public void onDimension(PlayerEvent.PlayerChangedDimensionEvent event) {
        if (event.getEntity() instanceof ServerPlayer sp && !isBot(sp)) {
            send("dimension", name(sp), 30_000, "to", event.getTo().location().toString());
        }
    }

    @SubscribeEvent
    public void onAdvancement(AdvancementEvent.AdvancementEarnEvent event) {
        if (!(event.getEntity() instanceof ServerPlayer sp) || isBot(sp)) return;
        DisplayInfo display = event.getAdvancement().getDisplay();
        if (display == null || !display.shouldAnnounceChat()) return;   // recipe unlocks and hidden steps are not news
        send("advancement", name(sp), 10_000, "title", display.getTitle().getString());
    }

    @SubscribeEvent
    public void onTick(TickEvent.ServerTickEvent event) {
        if (event.phase != TickEvent.Phase.END) return;
        MinecraftServer server = ServerLifecycleHooks.getCurrentServer();
        if (server == null || server.getTickCount() % 20 != 0) return;
        ServerLevel overworld = server.overworld();
        long day = overworld.getDayTime() % 24000;
        if (lastDayTime >= 0 && lastDayTime < 12500 && day >= 12500) send("night", "*", 60_000);
        lastDayTime = day;
        boolean thunder = overworld.isThundering();
        if (thunder && !wasThundering) send("storm", "*", 120_000);
        wasThundering = thunder;
        for (ServerPlayer sp : server.getPlayerList().getPlayers()) {
            if (isBot(sp) || sp.isSpectator() || sp.isCreative()) continue;
            danger(sp);
            int food = sp.getFoodData().getFoodLevel();
            if (food <= 6) send("player_hungry", name(sp), 300_000, "food", food);
        }
    }

    /** A creeper about to blow up next to him, a crowd of monsters, a boss: said at once, the AI would be too slow. */
    private void danger(ServerPlayer sp) {
        var box = sp.getBoundingBox().inflate(8);
        List<Creeper> creepers = sp.level().getEntitiesOfClass(Creeper.class, box.deflate(3),
                c -> c.isAlive() && (c.getSwellDir() > 0 || c.isIgnited()));
        if (!creepers.isEmpty()) {
            danger("creeper", name(sp), 8_000);
            return;
        }
        var bosses = sp.level().getEntitiesOfClass(Entity.class, sp.getBoundingBox().inflate(48),
                e -> e.isAlive() && (e instanceof WitherBoss || e instanceof EnderDragon || e instanceof Warden));
        // a boss stays around for minutes: it must not hide a crowd of monsters closing in meanwhile
        if (!bosses.isEmpty()) danger("boss", name(sp), 120_000, "name", bosses.get(0).getName().getString());
        int hostile = sp.level().getEntitiesOfClass(Entity.class, box, e -> e.isAlive() && e instanceof Enemy).size();
        if (hostile >= 4) danger("crowd", name(sp), 60_000, "count", hostile);
    }
}
