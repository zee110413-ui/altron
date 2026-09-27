package com.altron.host;

import com.altron.Config;
import com.altron.J;
import com.altron.net.BrainLink;
import com.google.gson.JsonObject;

import java.util.function.Consumer;

/** Link between the host player's game and the brain. */
public final class HostBridge {
    public static final BrainLink LINK = new BrainLink("host", Config.BRAIN_PORT, HostBridge::onMessage);
    /** Set by the voice chat plugin when Simple Voice Chat is present. */
    public static volatile Consumer<JsonObject> voiceHandler;
    /** Demo microphone (speaks prepared audio as the host player), also set by the voice chat plugin. */
    public static volatile Consumer<JsonObject> micHandler;

    private HostBridge() {
    }

    private static void onMessage(JsonObject o) {
        String type = J.str(o, "type", "");
        switch (type) {
            case "speak", "speak_stop" -> {
                Consumer<JsonObject> h = voiceHandler;
                if (h != null) h.accept(o);
            }
            case "mic" -> {
                Consumer<JsonObject> h = micHandler;
                if (h != null) h.accept(o);
                else LINK.send(J.obj("type", "mic_failed", "msg", "нет Simple Voice Chat"));
            }
            case "notify" -> HostClientEvents.notifyPlayer(J.str(o, "text", ""));
            case "setup" -> HostSetup.apply(o);
            case "probe" -> HostSetup.probe(o);
            case "host_chat" -> HostClientEvents.sayInChat(J.str(o, "text", ""));
            case "bot_pickup" -> HostServerEvents.BOT_PICKUP = o.has("on") && o.get("on").getAsBoolean();
            default -> {
            }
        }
    }
}
