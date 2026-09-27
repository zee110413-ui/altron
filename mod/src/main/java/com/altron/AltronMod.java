package com.altron;

import com.mojang.logging.LogUtils;
import net.minecraftforge.api.distmarker.Dist;
import net.minecraftforge.common.MinecraftForge;
import net.minecraftforge.fml.common.Mod;
import net.minecraftforge.fml.loading.FMLEnvironment;
import org.slf4j.Logger;

@Mod(AltronMod.MOD_ID)
public class AltronMod {
    public static final String MOD_ID = "altron";
    public static final Logger LOG = LogUtils.getLogger();

    public AltronMod() {
        // Runs on the integrated server of the host (unlocks recipes for the bot)
        MinecraftForge.EVENT_BUS.register(new com.altron.host.HostServerEvents());
        if (FMLEnvironment.dist == Dist.CLIENT) {
            com.altron.client.AltronClient.init();
        }
    }
}
