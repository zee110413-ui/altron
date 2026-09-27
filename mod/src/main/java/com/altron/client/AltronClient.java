package com.altron.client;

import com.altron.AltronMod;
import com.altron.Config;
import net.minecraftforge.common.MinecraftForge;

/** Client-side entry point: either the bot's body or the host (player) side. */
public final class AltronClient {
    private AltronClient() {
    }

    public static void init() {
        if (Config.BOT) {
            AltronMod.LOG.info("[Altron] starting in BOT mode as '{}', server {}", Config.BOT_NAME, Config.SERVER);
            MinecraftForge.EVENT_BUS.register(new com.altron.bot.BotClient());
            com.altron.bot.BotClient.LINK.start();
        } else {
            MinecraftForge.EVENT_BUS.register(new com.altron.host.HostClientEvents());
            if (com.altron.host.HostAuto.enabled()) {
                AltronMod.LOG.info("[Altron] unattended demo host, world '{}'", com.altron.host.HostAuto.WORLD);
                MinecraftForge.EVENT_BUS.register(new com.altron.host.HostAuto());
            }
            com.altron.host.HostBridge.LINK.setOnConnect(com.altron.host.HostClientEvents::onBrainConnected);
            com.altron.host.HostBridge.LINK.start();
            // Altron's AI may run on a second PC: it connects in with the key (none in demos: no key file there)
            try {
                java.nio.file.Path keyFile = net.minecraftforge.fml.loading.FMLPaths.CONFIGDIR.get().resolve("altron-key.txt");
                if (java.nio.file.Files.exists(keyFile)) {
                    String key = java.nio.file.Files.readString(keyFile).trim();
                    com.altron.host.HostBridge.LINK.listen(Config.REMOTE_BRAIN_PORT, key);
                }
            } catch (Exception e) {
                AltronMod.LOG.warn("[Altron] no remote brain: {}", e.toString());
            }
        }
    }
}
