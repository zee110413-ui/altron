package com.altron;

/** Settings passed via -D system properties (the bot launcher sets them). */
public final class Config {
    /** true only in the bot's own Minecraft client */
    public static final boolean BOT = Boolean.getBoolean("altron.bot");
    public static final int BRAIN_PORT = Integer.getInteger("altron.brainPort", 47800);
    public static final String BOT_NAME = System.getProperty("altron.name", "altron");
    /**
     * address the bot client connects to. 127.0.0.1, not "localhost": with VPN adapters (Radmin) Java may pick
     * the IPv6 ::1 for localhost, and Windows refuses that connection ("Permission denied: getsockopt")
     */
    public static final String SERVER = System.getProperty("altron.server", "127.0.0.1:25566");
    /** port the host opens its world on */
    public static final int LAN_PORT = Integer.getInteger("altron.lanPort", 25566);
    /** a brain running on another PC connects to the commander's game on this port (with the key from config/altron-key.txt) */
    public static final int REMOTE_BRAIN_PORT = Integer.getInteger("altron.remoteBrainPort", 47810);
    /** the bot hears and speaks through its own voice chat client (someone else's server: our server plugin is not there) */
    public static final boolean BOT_VOICE = Boolean.getBoolean("altron.voice");

    private Config() {
    }
}
