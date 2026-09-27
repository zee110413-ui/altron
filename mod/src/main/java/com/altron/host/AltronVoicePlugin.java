package com.altron.host;

import com.altron.AltronMod;
import com.altron.Config;
import com.altron.J;
import com.google.gson.JsonObject;
import de.maxhenkel.voicechat.api.ForgeVoicechatPlugin;
import de.maxhenkel.voicechat.api.Group;
import de.maxhenkel.voicechat.api.VoicechatApi;
import de.maxhenkel.voicechat.api.VoicechatConnection;
import de.maxhenkel.voicechat.api.VoicechatPlugin;
import de.maxhenkel.voicechat.api.VoicechatServerApi;
import de.maxhenkel.voicechat.api.audiochannel.AudioChannel;
import de.maxhenkel.voicechat.api.audiochannel.AudioPlayer;
import de.maxhenkel.voicechat.api.audiochannel.EntityAudioChannel;
import de.maxhenkel.voicechat.api.audiochannel.LocationalAudioChannel;
import de.maxhenkel.voicechat.api.audiochannel.StaticAudioChannel;
import de.maxhenkel.voicechat.api.events.PlayerConnectedEvent;
import de.maxhenkel.voicechat.api.events.ClientReceiveSoundEvent;
import de.maxhenkel.voicechat.api.events.EventRegistration;
import de.maxhenkel.voicechat.api.events.MicrophonePacketEvent;
import de.maxhenkel.voicechat.api.events.VoicechatServerStartedEvent;
import de.maxhenkel.voicechat.api.events.VoicechatServerStoppedEvent;
import de.maxhenkel.voicechat.api.opus.OpusDecoder;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraftforge.server.ServerLifecycleHooks;

import java.util.Base64;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentLinkedQueue;

/**
 * Simple Voice Chat plugin, active on the host's integrated server.
 * Forwards players' microphone audio to the brain and plays the bot's speech from its head.
 */
@ForgeVoicechatPlugin
public class AltronVoicePlugin implements VoicechatPlugin {
    private static final int FRAME = 960; // 20 ms at 48 kHz
    private static final float SPEAK_DISTANCE = 48f;

    private VoicechatApi api;
    private volatile VoicechatServerApi server;
    private final Map<UUID, OpusDecoder> decoders = new ConcurrentHashMap<>();
    private final ConcurrentLinkedQueue<short[]> queue = new ConcurrentLinkedQueue<>();
    private final Object lock = new Object();
    private AudioPlayer player;
    private int idleFrames;
    /** The commander and Altron are in one voice chat group from the start: they hear each other at any distance. */
    private volatile Group group;
    private final Map<UUID, ConcurrentLinkedQueue<short[]>> toMember = new ConcurrentHashMap<>();
    private final Map<UUID, AudioPlayer> memberPlayers = new ConcurrentHashMap<>();

    @Override
    public String getPluginId() {
        return AltronMod.MOD_ID;
    }

    @Override
    public void initialize(VoicechatApi api) {
        this.api = api;
        HostBridge.voiceHandler = this::onBrain;
        HostMic.init(api);
        AltronMod.LOG.info("[Altron] voice chat plugin loaded");
    }

    @Override
    public void registerEvents(EventRegistration registration) {
        registration.registerEvent(VoicechatServerStartedEvent.class, e -> server = e.getVoicechat());
        registration.registerEvent(VoicechatServerStoppedEvent.class, e -> {
            server = null;
            queue.clear();
            decoders.values().forEach(OpusDecoder::close);
            decoders.clear();
        });
        registration.registerEvent(MicrophonePacketEvent.class, this::onMicrophone);
        registration.registerEvent(PlayerConnectedEvent.class, this::onConnected);
        if (Config.BOT && Config.BOT_VOICE) {
            // someone else's server: Altron hears players near him through his own voice chat client
            registration.registerEvent(ClientReceiveSoundEvent.EntitySound.class, AltronVoicePlugin::onHeard);
        }
    }

    /** A player's voice reached Altron's client (already decoded, 20 ms of 48 kHz mono). */
    private static void onHeard(ClientReceiveSoundEvent.EntitySound event) {
        var mc = net.minecraft.client.Minecraft.getInstance();
        short[] pcm = event.getRawAudio();
        if (mc.level == null || mc.player == null || pcm == null || pcm.length == 0) return;
        if (event.getId().equals(mc.player.getUUID())) return;
        net.minecraft.world.entity.player.Player speaker;
        try {
            speaker = mc.level.getPlayerByUUID(event.getId());   // called on the voice thread: the list may change meanwhile
        } catch (RuntimeException e) {
            speaker = null;
        }
        String name = speaker != null ? speaker.getGameProfile().getName() : event.getId().toString();
        double dist = speaker != null ? speaker.distanceTo(mc.player) : -1;
        byte[] bytes = new byte[pcm.length * 2];
        for (int i = 0; i < pcm.length; i++) {
            bytes[i * 2] = (byte) pcm[i];
            bytes[i * 2 + 1] = (byte) (pcm[i] >> 8);
        }
        com.altron.bot.BotClient.LINK.send(J.obj("type", "voice", "uuid", event.getId().toString(), "name", name,
                "dist", Math.round(dist * 10) / 10.0, "pcm", Base64.getEncoder().encodeToString(bytes)));
    }

    private Group group(VoicechatServerApi s) {
        if (group == null) {
            group = s.groupBuilder().setName("Альтрон").setType(Group.Type.OPEN).setPersistent(true).build();
        }
        return group;
    }

    /** A player's voice chat connected (the commander, a friend, Altron): into the group «Альтрон». */
    private void onConnected(PlayerConnectedEvent event) {
        VoicechatServerApi s = server;
        VoicechatConnection c = event.getConnection();
        if (s == null || c == null || c.isInGroup()) return;
        try {
            c.setGroup(group(s));
        } catch (RuntimeException e) {
            AltronMod.LOG.warn("[Altron] could not put a player into the voice group: {}", e.toString());
        }
    }

    /** Altron's speech straight to everyone in his group (no fading with distance); false if nobody is in it. */
    private boolean toGroup(java.util.List<short[]> frames) {
        VoicechatServerApi s = server;
        MinecraftServer mcs = ServerLifecycleHooks.getCurrentServer();
        if (s == null || mcs == null) return false;
        boolean any = false;
        for (ServerPlayer p : mcs.getPlayerList().getPlayers()) {
            if (p.getGameProfile().getName().equalsIgnoreCase(Config.BOT_NAME)) continue;
            VoicechatConnection c = s.getConnectionOf(p.getUUID());
            if (c == null || !c.isInGroup()) continue;
            any = true;
            ConcurrentLinkedQueue<short[]> q = toMember.computeIfAbsent(p.getUUID(), u -> new ConcurrentLinkedQueue<>());
            q.addAll(frames);
            AudioPlayer ap = memberPlayers.get(p.getUUID());
            if (ap != null && ap.isPlaying()) continue;
            StaticAudioChannel ch = s.createStaticAudioChannel(UUID.randomUUID(), s.fromServerLevel(p.level()), c);
            if (ch == null) continue;
            int[] idle = {0};
            ap = s.createAudioPlayer(ch, api.createEncoder(), () -> {
                short[] f = q.poll();
                if (f != null) {
                    idle[0] = 0;
                    return f;
                }
                return ++idle[0] < 25 ? new short[FRAME] : null;   // a short pause does not cut the next sentence
            });
            memberPlayers.put(p.getUUID(), ap);
            ap.startPlaying();
        }
        return any;
    }

    private void onMicrophone(MicrophonePacketEvent event) {
        if (!HostBridge.LINK.isConnected()) return;
        VoicechatConnection conn = event.getSenderConnection();
        if (conn == null) return;
        Object obj = conn.getPlayer().getPlayer();
        if (!(obj instanceof ServerPlayer sender)) return;
        String name = sender.getGameProfile().getName();
        if (name.equalsIgnoreCase(Config.BOT_NAME)) return;

        byte[] opus = event.getPacket().getOpusEncodedData();
        if (opus == null || opus.length == 0) return;
        OpusDecoder decoder = decoders.computeIfAbsent(sender.getUUID(), u -> api.createDecoder());
        short[] pcm = decoder.decode(opus);
        if (pcm == null || pcm.length == 0) return;

        double dist = -1;
        ServerPlayer bot = sender.server.getPlayerList().getPlayerByName(Config.BOT_NAME);
        if (bot != null && bot.level() == sender.level()) dist = bot.distanceTo(sender);

        byte[] bytes = new byte[pcm.length * 2];
        for (int i = 0; i < pcm.length; i++) {
            bytes[i * 2] = (byte) pcm[i];
            bytes[i * 2 + 1] = (byte) (pcm[i] >> 8);
        }
        HostBridge.LINK.send(J.obj("type", "voice", "uuid", sender.getUUID().toString(), "name", name,
                "dist", Math.round(dist * 10) / 10.0, "pcm", Base64.getEncoder().encodeToString(bytes)));
    }

    /** Messages from the brain: {"type":"speak","pcm":base64 s16le mono 48k} or speak_stop. */
    private void onBrain(JsonObject o) {
        if ("speak_stop".equals(J.str(o, "type", ""))) {
            queue.clear();
            toMember.values().forEach(ConcurrentLinkedQueue::clear);
            return;
        }
        byte[] data = Base64.getDecoder().decode(J.str(o, "pcm", ""));
        int samples = data.length / 2;
        java.util.List<short[]> frames = new java.util.ArrayList<>();
        for (int off = 0; off < samples; off += FRAME) {
            short[] frame = new short[FRAME];
            for (int i = 0; i < FRAME && off + i < samples; i++) {
                int b = (off + i) * 2;
                frame[i] = (short) ((data[b] & 0xFF) | (data[b + 1] << 8));
            }
            frames.add(frame);
        }
        if (toGroup(frames)) return;   // his group hears him wherever they are
        queue.addAll(frames);
        ensurePlaying();
    }

    private void ensurePlaying() {
        VoicechatServerApi s = server;
        if (s == null) return;
        synchronized (lock) {
            if (player != null && player.isPlaying()) return;
            MinecraftServer mcs = ServerLifecycleHooks.getCurrentServer();
            if (mcs == null) return;
            AudioChannel channel = null;
            ServerPlayer bot = mcs.getPlayerList().getPlayerByName(Config.BOT_NAME);
            if (bot != null) {
                EntityAudioChannel ec = s.createEntityAudioChannel(UUID.randomUUID(), s.fromEntity(bot));
                if (ec != null) {
                    ec.setDistance(SPEAK_DISTANCE);
                    channel = ec;
                }
            } else if (!mcs.getPlayerList().getPlayers().isEmpty()) {
                // Bot not in the world yet: speak next to the first player
                ServerPlayer p = mcs.getPlayerList().getPlayers().get(0);
                LocationalAudioChannel lc = s.createLocationalAudioChannel(UUID.randomUUID(),
                        s.fromServerLevel(p.level()), s.createPosition(p.getX(), p.getY() + 1, p.getZ()));
                if (lc != null) {
                    lc.setDistance(SPEAK_DISTANCE);
                    channel = lc;
                }
            }
            if (channel == null) {
                queue.clear();
                return;
            }
            idleFrames = 0;
            player = s.createAudioPlayer(channel, api.createEncoder(), this::nextFrame);
            player.startPlaying();
        }
    }

    private short[] nextFrame() {
        short[] f = queue.poll();
        if (f != null) {
            idleFrames = 0;
            return f;
        }
        // Keep the player alive briefly so back-to-back sentences do not get cut
        if (++idleFrames < 25) return new short[FRAME];
        return null;
    }
}
