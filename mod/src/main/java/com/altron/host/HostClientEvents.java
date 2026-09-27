package com.altron.host;

import com.altron.Config;
import com.altron.J;
import com.mojang.brigadier.arguments.StringArgumentType;
import net.minecraft.client.Minecraft;
import net.minecraft.client.server.IntegratedServer;
import net.minecraft.commands.Commands;
import net.minecraft.network.chat.Component;
import net.minecraft.util.HttpUtil;
import net.minecraft.world.level.GameType;
import net.minecraft.world.level.storage.LevelResource;
import net.minecraftforge.client.event.RegisterClientCommandsEvent;
import net.minecraftforge.eventbus.api.SubscribeEvent;

/** The /altron command in the host player's game. */
public class HostClientEvents {
    @SubscribeEvent
    public void onRegisterClientCommands(RegisterClientCommandsEvent event) {
        event.getDispatcher().register(Commands.literal("altron")
                .executes(ctx -> start())
                .then(Commands.literal("lan").executes(ctx -> openLan()))
                .then(Commands.literal("stop").executes(ctx -> {
                    HostBridge.LINK.send(J.obj("type", "bot_stop"));
                    notifyPlayer("Отключаю Альтрона.");
                    return 1;
                }))
                .then(Commands.literal("say").then(Commands.argument("text", StringArgumentType.greedyString())
                        .executes(ctx -> {
                            Minecraft mc = Minecraft.getInstance();
                            String who = mc.player != null ? mc.player.getGameProfile().getName() : "host";
                            HostBridge.LINK.send(J.obj("type", "text", "from", who,
                                    "text", StringArgumentType.getString(ctx, "text")));
                            return 1;
                        }))));
    }

    /** Used by the unattended demo host (HostAuto). */
    public static void startForBot() {
        start();
    }

    /** Open this world to the network without licence checks (Altron has an offline account). Returns the port or -1. */
    private static int publish(Minecraft mc, IntegratedServer srv) {
        srv.setUsesAuthentication(false);
        if (srv.isPublished()) return srv.getPort();
        GameType mode = mc.gameMode.getPlayerMode();
        int port = Config.LAN_PORT;
        boolean ok = srv.publishServer(mode, false, port);
        if (!ok) {
            port = HttpUtil.getAvailablePort();
            ok = srv.publishServer(mode, false, port);
        }
        return ok ? port : -1;
    }

    /**
     * /altron lan — for a friend who hosts the world over Radmin VPN: opens it for Altron without a brain on this PC.
     * The owner of Altron types the friend's Radmin address and this port into his brain.
     */
    private static int openLan() {
        Minecraft mc = Minecraft.getInstance();
        IntegratedServer srv = mc.getSingleplayerServer();
        if (srv == null || mc.player == null || mc.gameMode == null) {
            notifyPlayer("Эту команду вводит хозяин мира в своём одиночном мире.");
            return 0;
        }
        int port = publish(mc, srv);
        if (port < 0) {
            notifyPlayer("§cНе получилось открыть мир по сети.");
            return 0;
        }
        notifyPlayer("Мир открыт по сети без проверки лицензий, порт §e" + port + "§r. Скажи владельцу Альтрона свой адрес из "
                + "Radmin VPN и этот порт, например §e26.x.x.x:" + port + "§r — он введёт его при запуске Альтрона.");
        return 1;
    }

    private static int start() {
        Minecraft mc = Minecraft.getInstance();
        IntegratedServer srv = mc.getSingleplayerServer();
        if (srv == null || mc.player == null || mc.gameMode == null) {
            notifyPlayer("Эту команду нужно вводить в своём одиночном мире: я открою его для Альтрона по сети. "
                    + "На чужом сервере Альтрона подключают из окна мозга: адрес Radmin VPN.");
            return 0;
        }
        if (!HostBridge.LINK.isConnected()) {
            notifyPlayer("§cМозг Альтрона не запущен. Запусти start_altron.bat и повтори /altron.");
            return 0;
        }
        // The bot uses an offline account, so the local server must not check Mojang sessions
        int port = publish(mc, srv);
        if (port < 0) {
            notifyPlayer("§cНе получилось открыть мир по сети.");
            return 0;
        }
        // the save folder names the world: Altron keeps separate memories for every world
        String world = srv.getWorldPath(LevelResource.ROOT).toAbsolutePath().normalize().getFileName().toString();
        HostBridge.LINK.send(J.obj("type", "host_ready", "port", port,
                "owner", mc.player.getGameProfile().getName(), "world", world));
        notifyPlayer("Мир открыт для Альтрона (порт " + port + "). Альтрон подключается, это займёт 1-2 минуты...");
        return 1;
    }

    /**
     * The brain was (re)started while this world is already open for Altron: call him back in by itself,
     * so a restart of the brain does not need /altron again.
     */
    public static void onBrainConnected() {
        Minecraft mc = Minecraft.getInstance();
        mc.execute(() -> {
            IntegratedServer srv = mc.getSingleplayerServer();
            if (srv == null || !srv.isPublished() || mc.player == null) return;
            String world = srv.getWorldPath(LevelResource.ROOT).toAbsolutePath().normalize().getFileName().toString();
            HostBridge.LINK.send(J.obj("type", "host_ready", "port", srv.getPort(),
                    "owner", mc.player.getGameProfile().getName(), "world", world));
        });
    }

    /** The host player says something in chat (the demo director speaks through it). */
    public static void sayInChat(String text) {
        Minecraft mc = Minecraft.getInstance();
        mc.execute(() -> {
            if (mc.player != null && !text.isBlank()) mc.player.connection.sendChat(text.length() > 250 ? text.substring(0, 250) : text);
        });
    }

    public static void notifyPlayer(String text) {
        Minecraft mc = Minecraft.getInstance();
        mc.execute(() -> {
            if (mc.player != null) {
                mc.player.displayClientMessage(Component.literal("§b[Альтрон]§r " + text), false);
            }
        });
    }
}
