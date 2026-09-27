package com.altron.host;

import com.altron.AltronMod;
import com.altron.Config;
import com.altron.J;
import com.altron.net.BrainLink;
import com.google.gson.JsonObject;
import de.maxhenkel.voicechat.api.VoicechatApi;
import de.maxhenkel.voicechat.api.opus.OpusEncoder;

import java.lang.reflect.Constructor;
import java.lang.reflect.Method;
import java.util.Base64;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/**
 * This game's "microphone" in Simple Voice Chat: prepared speech is sent exactly like the mod's own microphone
 * thread does (20 ms Opus frames over the voice connection), so everyone on the server hears it like a player's voice.
 * Used by the demo host (the commander's recorded orders) and by Altron's own client on someone else's server
 * (there our server plugin is not running, so Altron speaks as a player).
 */
public final class HostMic {
    private static final int FRAME = 960; // 20 ms at 48 kHz
    private static final ConcurrentLinkedQueue<short[]> FRAMES = new ConcurrentLinkedQueue<>();
    private static VoicechatApi api;
    private static BrainLink statusLink;
    private static OpusEncoder encoder;
    private static ScheduledExecutorService timer;
    private static long sequence;
    private static boolean talking;
    private static Method getClient, getConnection, isInitialized, sendToServer;
    private static Constructor<?> micPacket, networkMessage;

    private HostMic() {
    }

    static void init(VoicechatApi voicechatApi) {
        api = voicechatApi;
        statusLink = Config.BOT ? com.altron.bot.BotClient.LINK : HostBridge.LINK;
        HostBridge.micHandler = HostMic::onBrain;
    }

    /** Stop talking at once (the brain was told "stop"). */
    public static void clear() {
        FRAMES.clear();
    }

    /** {"type":"mic"|"speak","pcm":base64 s16le mono 48 kHz}: queue it for sending. */
    public static synchronized void onBrain(JsonObject o) {
        if (api == null) {
            if (statusLink != null) statusLink.send(J.obj("type", "mic_failed", "msg", "нет Simple Voice Chat"));
            return;
        }
        byte[] data = Base64.getDecoder().decode(J.str(o, "pcm", ""));
        int samples = data.length / 2;
        for (int off = 0; off < samples; off += FRAME) {
            short[] frame = new short[FRAME];
            for (int i = 0; i < FRAME && off + i < samples; i++) {
                int b = (off + i) * 2;
                frame[i] = (short) ((data[b] & 0xFF) | (data[b + 1] << 8));
            }
            FRAMES.add(frame);
        }
        if (timer == null) {
            timer = Executors.newSingleThreadScheduledExecutor(r -> {
                Thread t = new Thread(r, "altron-mic");
                t.setDaemon(true);
                return t;
            });
            timer.scheduleAtFixedRate(HostMic::tick, 0, 20, TimeUnit.MILLISECONDS);
        }
    }

    private static void tick() {
        try {
            short[] frame = FRAMES.poll();
            if (frame == null) {
                if (talking) {
                    talking = false;
                    send(new byte[0]);   // end of speech, like releasing push-to-talk
                    statusLink.send(J.obj("type", "mic_done"));
                }
                return;
            }
            if (!talking) {
                talking = true;
                if (encoder == null) encoder = api.createEncoder();
                else encoder.resetState();
            }
            if (!send(encoder.encode(frame))) {
                FRAMES.clear();
                talking = false;
                statusLink.send(J.obj("type", "mic_failed", "msg", "голосовой чат не подключён"));
            }
        } catch (Throwable t) {
            FRAMES.clear();
            talking = false;
            AltronMod.LOG.warn("[Altron] mic failed: {}", t.toString());
            statusLink.send(J.obj("type", "mic_failed", "msg", t.toString()));
        }
    }

    /** Voice chat internals (no public API for sending microphone audio), looked up once. */
    private static boolean send(byte[] opus) throws Exception {
        if (getClient == null) {
            getClient = Class.forName("de.maxhenkel.voicechat.voice.client.ClientManager").getMethod("getClient");
            Class<?> client = Class.forName("de.maxhenkel.voicechat.voice.client.ClientVoicechat");
            getConnection = client.getMethod("getConnection");
            Class<?> conn = Class.forName("de.maxhenkel.voicechat.voice.client.ClientVoicechatConnection");
            isInitialized = conn.getMethod("isInitialized");
            Class<?> message = Class.forName("de.maxhenkel.voicechat.voice.common.NetworkMessage");
            sendToServer = conn.getMethod("sendToServer", message);
            micPacket = Class.forName("de.maxhenkel.voicechat.voice.common.MicPacket").getConstructor(byte[].class, boolean.class, long.class);
            networkMessage = message.getConstructor(Class.forName("de.maxhenkel.voicechat.voice.common.Packet"));
        }
        Object client = getClient.invoke(null);
        Object conn = client == null ? null : getConnection.invoke(client);
        if (conn == null || !(Boolean) isInitialized.invoke(conn)) return false;
        sendToServer.invoke(conn, networkMessage.newInstance(micPacket.newInstance(opus, false, sequence++)));
        return true;
    }
}
