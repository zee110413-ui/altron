package com.altron.host;

import com.altron.AltronMod;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.TitleScreen;
import net.minecraft.world.Difficulty;
import net.minecraft.world.level.GameRules;
import net.minecraft.world.level.GameType;
import net.minecraft.world.level.LevelSettings;
import net.minecraft.world.level.WorldDataConfiguration;
import net.minecraft.world.level.levelgen.WorldOptions;
import net.minecraft.world.level.levelgen.presets.WorldPresets;
import net.minecraftforge.event.TickEvent;
import net.minecraftforge.eventbus.api.SubscribeEvent;

import java.nio.file.Files;
import java.nio.file.Path;

/**
 * Unattended host for recorded demos: -Daltron.autoWorld=Name loads (or creates) a normal survival world
 * and opens it for the bot as soon as the brain is connected.
 */
public class HostAuto {
    public static final String WORLD = System.getProperty("altron.autoWorld", "");
    private boolean worldRequested;
    private boolean started;
    private int ticks;

    public static boolean enabled() {
        return !WORLD.isBlank();
    }

    @SubscribeEvent
    public void onTick(TickEvent.ClientTickEvent event) {
        if (event.phase != TickEvent.Phase.END) return;
        Minecraft mc = Minecraft.getInstance();
        ticks++;
        if (!worldRequested && mc.screen instanceof TitleScreen && ticks > 40) {
            worldRequested = true;
            openWorld(mc);
            return;
        }
        if (!started && mc.player != null && mc.getSingleplayerServer() != null && HostBridge.LINK.isConnected() && ticks % 20 == 0) {
            started = true;
            HostClientEvents.startForBot();
        }
    }

    private static void openWorld(Minecraft mc) {
        Path dir = mc.gameDirectory.toPath().resolve("saves").resolve(WORLD);
        if (Files.isDirectory(dir)) {
            AltronMod.LOG.info("[Altron] loading demo world {}", WORLD);
            mc.createWorldOpenFlows().loadLevel(new TitleScreen(), WORLD);
            return;
        }
        AltronMod.LOG.info("[Altron] creating demo world {}", WORLD);
        GameRules rules = new GameRules();
        LevelSettings settings = new LevelSettings(WORLD, GameType.SURVIVAL, false, Difficulty.EASY, true, rules,
                WorldDataConfiguration.DEFAULT);
        long seed = Long.getLong("altron.seed", 20260923L);
        WorldOptions options = new WorldOptions(seed, true, false);
        mc.createWorldOpenFlows().createFreshLevel(WORLD, settings, options, WorldPresets::createNormalWorldDimensions);
    }
}
