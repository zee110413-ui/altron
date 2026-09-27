package com.altron.bot;

import com.altron.AltronMod;
import com.altron.Config;
import com.altron.J;
import com.altron.bot.tasks.AttackTask;
import com.altron.bot.tasks.EatTask;
import com.altron.bot.tasks.FollowTask;
import com.altron.net.BrainLink;
import com.google.gson.JsonObject;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.ConnectScreen;
import net.minecraft.client.gui.screens.DeathScreen;
import net.minecraft.client.gui.screens.DisconnectedScreen;
import net.minecraft.client.gui.screens.PauseScreen;
import net.minecraft.client.gui.screens.TitleScreen;
import net.minecraft.client.gui.screens.inventory.AbstractContainerScreen;
import net.minecraft.client.gui.screens.multiplayer.JoinMultiplayerScreen;
import net.minecraft.client.multiplayer.ServerData;
import net.minecraft.client.multiplayer.resolver.ServerAddress;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.player.Player;
import net.minecraftforge.client.event.ClientChatReceivedEvent;
import net.minecraftforge.event.TickEvent;
import net.minecraftforge.eventbus.api.SubscribeEvent;
import org.lwjgl.glfw.GLFW;

import java.util.List;
import java.util.concurrent.ConcurrentLinkedQueue;

/** The bot's body: connects to the host, runs tasks, reports to the brain. */
public class BotClient {
    public static final BrainLink LINK = new BrainLink("bot", Config.BRAIN_PORT, BotClient::onMessage);
    private static final ConcurrentLinkedQueue<JsonObject> INBOX = new ConcurrentLinkedQueue<>();

    public static volatile String owner = "";
    /** Save folder name of the host's world (from the brain): memories are kept per world. */
    public static volatile String worldName = "";
    private static Task task;
    private static int taskId;
    private static Task interrupt;

    private int ticks;
    private long nextConnect;
    private String lastRefusal = "";
    private int deadTicks;
    private boolean joined;
    private float lastHp = -1;
    private long lastLowHpEvent;
    private int idleTicks;

    private static final java.util.Map<String, Long> LAST_NEED = new java.util.HashMap<>();
    private static final boolean THIRD_PERSON = Boolean.getBoolean("altron.thirdPerson");

    // The bot plays through code and does not need a picture: the world is drawn only when someone needs it
    // (a recorded demo, the commander looking at the window, a screenshot for the eyes, a fight, driving).
    // The game keeps ticking at full speed either way; without drawing it hardly uses the GPU and much less CPU.
    private static final boolean RENDER_ALWAYS = Boolean.getBoolean("altron.render");
    private static final int FPS_HIDDEN = 10;
    private static final int FPS_SHOWN = 30;
    /** Frames need a moment after drawing resumes: chunk meshes are rebuilt, then the picture is complete. */
    private static final int SHOT_WARMUP = 40;
    private static int renderHold;
    private static int renderedTicks;
    /** Drawing switched on by a command (the commander wants to watch Altron's window, measurements). */
    public static volatile boolean renderForced;
    private static int fps = -1;
    private static final java.util.List<int[]> SHOTS = new java.util.ArrayList<>();   // {command id, tick to take it}

    private static void onMessage(JsonObject o) {
        // on someone else's server Altron speaks through his own voice chat client, right away (not on the game tick)
        try {
            switch (J.str(o, "type", "")) {
                case "speak" -> com.altron.host.HostMic.onBrain(o);
                case "speak_stop" -> com.altron.host.HostMic.clear();
                default -> INBOX.add(o);
            }
        } catch (Throwable t) {   // no Simple Voice Chat in this client
            LINK.send(J.obj("type", "mic_failed", "msg", t.toString()));
        }
    }

    /** Tell the brain the bot needs something from the player (rate-limited per kind). */
    public static void need(String kind, String msg) {
        long now = System.currentTimeMillis();
        if (now - LAST_NEED.getOrDefault(kind, 0L) < 180_000) return;
        LAST_NEED.put(kind, now);
        Bot.send(J.obj("type", "event", "event", "need", "kind", kind, "msg", msg));
    }

    public static Task currentTask() {
        return interrupt != null ? interrupt : task;
    }

    public static Task mainTask() {
        return task;
    }

    /** Replace the main task. Returns the new task id. */
    public static int setTask(Task t) {
        if (interrupt != null) {
            interrupt.stop();
            interrupt = null;
        }
        if (task != null) {
            task.stop();
            Bot.send(J.obj("type", "event", "event", "task_cancelled", "task", task.name(), "task_id", taskId));
        }
        task = t;
        return ++taskId;
    }

    /** Keep drawing the world for a while (mods whose guns or vehicles work only while the game draws). */
    public static void keepRendering(int ticks) {
        renderHold = Math.max(renderHold, ticks);
    }

    /**
     * A screenshot needs a drawn picture: if drawing is off, turn it on and answer the command once the frame
     * is complete. Returns true if the answer will come later.
     */
    static boolean deferScreenshot(int id) {
        if (!Minecraft.getInstance().noRender && renderedTicks >= SHOT_WARMUP) return false;
        keepRendering(SHOT_WARMUP + 40);
        SHOTS.add(new int[]{id, SHOT_WARMUP});
        return true;
    }

    private void updateRendering(Minecraft mc) {
        long win = mc.getWindow().getWindow();
        boolean watched = GLFW.glfwGetWindowAttrib(win, GLFW.GLFW_FOCUSED) != 0;   // the commander clicked into it
        // loading screens and menus finish their work while being drawn: only the world itself is skipped
        boolean menu = mc.level == null || mc.getOverlay() != null
                || (mc.screen != null && !(mc.screen instanceof AbstractContainerScreen<?>));
        boolean shown = RENDER_ALWAYS || renderForced || watched;
        boolean render = shown || menu || renderHold > 0;
        if (renderHold > 0) renderHold--;
        mc.noRender = !render;
        renderedTicks = render ? renderedTicks + 1 : 0;
        int want = shown ? FPS_SHOWN : render ? 20 : FPS_HIDDEN;
        if (want != fps) {
            fps = want;
            mc.getWindow().setFramerateLimit(want);
        }
        for (java.util.Iterator<int[]> it = SHOTS.iterator(); it.hasNext(); ) {
            int[] s = it.next();
            if (--s[1] > 0 && renderedTicks < SHOT_WARMUP) continue;
            it.remove();
            JsonObject res = Actions.screenshot();
            res.addProperty("type", "result");
            res.addProperty("id", s[0]);
            Bot.send(res);
        }
    }

    /** Run a short task on top of the main one (eat, fight back), then resume it. */
    static void interrupt(Task t) {
        if (interrupt != null) return;
        if (task != null) task.pause();
        interrupt = t;
    }

    @SubscribeEvent
    public void onTick(TickEvent.ClientTickEvent event) {
        if (event.phase != TickEvent.Phase.END) return;
        Minecraft mc = Minecraft.getInstance();
        ticks++;
        updateRendering(mc);
        if (ticks % 40 == 0) {
            mc.getWindow().setTitle("ALTRON"); // a fixed title so the recorder can find this window
            // recorded demos show Altron himself on the surface; in tunnels a third-person camera
            // gets stuck in the walls, so there it looks through his eyes
            if (THIRD_PERSON && mc.player != null) {
                boolean openAir = mc.level.canSeeSky(mc.player.blockPosition().above());
                var want = openAir ? net.minecraft.client.CameraType.THIRD_PERSON_BACK : net.minecraft.client.CameraType.FIRST_PERSON;
                if (mc.options.getCameraType() != want) mc.options.setCameraType(want);
            }
        }
        if (mc.player == null || mc.level == null) {
            if (joined) Memory.save();
            joined = false;
            autoConnect(mc);
            JsonObject msg;
            while ((msg = INBOX.poll()) != null) {
                if ("config".equals(J.str(msg, "type", ""))) {
                    Actions.handle(msg);   // who the commander is, which world: needed before joining
                } else if ("cmd".equals(J.str(msg, "type", ""))) {
                    Bot.send(J.obj("type", "result", "id", J.num(msg, "id", 0), "ok", false,
                            "msg", "Альтрон ещё не зашёл в мир"));
                }
            }
            return;
        }
        LocalPlayer p = mc.player;
        // Behave as a focused window so mods that check focus still accept input
        mc.setWindowActive(true);
        if (!joined) {
            joined = true;
            Baritone.setup();
            Memory.onJoin();
            // the world key lets the brain keep its own memories (places, facts) per world too
            Bot.send(J.obj("type", "event", "event", "joined", "world", Memory.worldKey(),
                    "msg", "Альтрон в мире: " + Bot.pos(p.blockPosition()) + ". " + Memory.summary()));
        }
        Input.tick();
        Bot.tickLook();
        Memory.tick();
        // in a vehicle: mods like SuperbWarfare read the controls only for a window in front
        if (p.getVehicle() != null && mc.screen == null) Focus.pretend(true);
        else if (p.getVehicle() == null && !Guns.firing()) Focus.pretend(false);
        handleScreens(mc, p);

        JsonObject msg;
        int n = 0;
        while (n++ < 20 && (msg = INBOX.poll()) != null) {
            try {
                Actions.handle(msg);
            } catch (Exception e) {
                AltronMod.LOG.error("[Altron] command failed", e);
            }
        }

        tickTasks();
        autoBehaviour(p);
        idleLook(p);
        if (ticks % 20 == 0) Bot.send(Info.state());
    }

    private void tickTasks() {
        if (interrupt != null) {
            Task.Status s = runSafe(interrupt);
            if (s != Task.Status.RUNNING) {
                interrupt.stop();
                interrupt = null;
                if (task != null) task.resume();
            }
            return;
        }
        if (task == null) return;
        Task t = task;
        Task.Status s = runSafe(t);
        if (s != Task.Status.RUNNING && task == t) {
            t.stop();
            task = null;
            String said = serverMessagesSince(t.startedMs);
            Bot.send(J.obj("type", "event", "event", s == Task.Status.DONE ? "task_done" : "task_failed",
                    "task", t.name(), "task_id", taskId, "msg", t.result() + (said.isEmpty() ? "" : " | сервер написал: " + said)));
        }
    }

    private static Task.Status runSafe(Task t) {
        try {
            return t.tick();
        } catch (Exception e) {
            AltronMod.LOG.error("[Altron] task {} crashed", t.name(), e);
            t.result = "ошибка: " + e;
            return Task.Status.FAILED;
        }
    }

    private void autoBehaviour(LocalPlayer p) {
        // Report getting hurt badly
        float hp = p.getHealth();
        long now = System.currentTimeMillis();
        // (not at the moment of death: "мало здоровья" right after "меня уничтожили" makes no sense)
        if (lastHp > 0 && hp > 0 && hp < 8 && hp < lastHp && now - lastLowHpEvent > 30000) {
            lastLowHpEvent = now;
            Bot.event("low_health", "У меня мало здоровья: " + Math.round(hp) + "/20");
        }
        lastHp = hp;
        if (interrupt != null || ticks % 10 != 0) return;
        if (p.containerMenu != p.inventoryMenu) return;
        boolean fighting = task instanceof AttackTask || (task instanceof FollowTask ft && ft.name().equals("guard"));
        // Fight back if a hostile mob is right next to us
        if (!fighting) {
            List<Entity> close = Info.entities(6, e -> Info.isHostile(e) && p.hasLineOfSight(e));
            if (!close.isEmpty()) {
                interrupt(new AttackTask("hostile", 10, 30));
                return;
            }
        }
        // Eat when hungry; ask the player for food if there is none
        int food = p.getFoodData().getFoodLevel();
        if (food <= 8 || (task == null && food <= 14)) {
            if (Inv.find(p, s -> s.getItem().getFoodProperties(s, p) != null) >= 0) interrupt(new EatTask());
            else if (food <= 8) need("food", "Я голоден (сытость " + food + "/20), а еды у меня нет.");
        }
    }

    // ---------------------------------------------------------------- idle life: where he looks when he has nothing to do
    private static String attentionTo = "";
    private static int attentionTicks;
    private int glanceTicks;
    private net.minecraft.world.phys.Vec3 glance;
    private net.minecraft.world.phys.Vec3 gazeOffset = net.minecraft.world.phys.Vec3.ZERO;

    /** Someone spoke to Altron: he turns to that player for a while (if he is not busy). */
    public static void attention(String player, int ticks) {
        attentionTo = player == null ? "" : player;
        attentionTicks = ticks;
    }

    /**
     * Standing idle like a person, not a statue: looks at the commander when he is near (the eyes wander a little),
     * now and then glances around (which also lets his memory take in the place).
     */
    private void idleLook(LocalPlayer p) {
        if (attentionTicks > 0) attentionTicks--;
        if (Bot.turnedOnRequest()) return;   // he was asked to look somewhere: keep looking there
        // following the commander and standing next to him: look at him, not wherever the last step pointed
        // (only right next to him, and only while Baritone is not leading anywhere: turning the head while it wants to
        // set off kept Altron standing still — "иду за тобой" and not a step)
        if (currentTask() instanceof FollowTask) {
            Player who = Bot.findPlayer(attentionTicks > 0 && !attentionTo.isBlank() ? attentionTo : owner);
            if (who != null && who.distanceTo(p) < 4.5 && !Baritone.pathing()
                    && p.getDeltaMovement().horizontalDistanceSqr() < 0.003 && p.hasLineOfSight(who)) {
                Bot.lookAndHold(who, 10);
            }
            return;
        }
        if (currentTask() != null || p.containerMenu != p.inventoryMenu || p.isPassenger() || Baritone.busy()) {
            glanceTicks = 0;
            return;
        }
        var r = p.getRandom();
        if (glanceTicks > 0) {
            glanceTicks--;
            Bot.lookAt(glance);
            return;
        }
        Player who = Bot.findPlayer(attentionTicks > 0 && !attentionTo.isBlank() ? attentionTo : owner);
        if (who != null && who.distanceTo(p) < 24 && p.hasLineOfSight(who)) {
            if (ticks % 50 == 0) gazeOffset = new net.minecraft.world.phys.Vec3(r.nextGaussian() * 0.12, r.nextGaussian() * 0.08, r.nextGaussian() * 0.12);
            if (attentionTicks == 0 && r.nextInt(500) == 0) {
                startGlance(p, r);
                return;
            }
            Bot.lookAt(who.getEyePosition().add(gazeOffset));
        } else if (r.nextInt(140) == 0) {
            startGlance(p, r);
        }
    }

    private void startGlance(LocalPlayer p, net.minecraft.util.RandomSource r) {
        float yaw = p.getYRot() + (r.nextFloat() - 0.5f) * 150f;
        float pitch = Math.max(-30f, Math.min(30f, (r.nextFloat() - 0.5f) * 40f));
        glance = p.getEyePosition().add(net.minecraft.world.phys.Vec3.directionFromRotation(pitch, yaw).scale(8));
        glanceTicks = 25 + r.nextInt(40);
    }

    private void handleScreens(Minecraft mc, LocalPlayer p) {
        if (mc.screen instanceof DeathScreen) {
            if (++deadTicks == 1) Bot.event("death", "Альтрон погиб");
            if (deadTicks > 30) {
                p.respawn();
                mc.setScreen(null);
                deadTicks = 0;
                setTask(null);
            }
            return;
        }
        deadTicks = 0;
        if (mc.screen instanceof PauseScreen) mc.setScreen(null);
        // Close stray container screens when no task needs them
        if (mc.screen instanceof AbstractContainerScreen<?> && currentTask() == null) {
            if (++idleTicks > 20 * 60) {
                p.closeContainer();
                idleTicks = 0;
            }
        } else {
            idleTicks = 0;
        }
    }

    private void autoConnect(Minecraft mc) {
        String screenName = mc.screen == null ? "" : mc.screen.getClass().getSimpleName();
        boolean refused = mc.screen instanceof DisconnectedScreen || screenName.contains("Mismatch");
        if (refused) {
            // tell the brain why the server did not let us in (e.g. a mod the lite client left out)
            String why = screenName + ": " + mc.screen.getNarrationMessage().getString();
            if (!why.equals(lastRefusal)) {
                lastRefusal = why;
                Bot.send(J.obj("type", "event", "event", "connect_failed", "msg", why));
            }
        }
        if (!(mc.screen instanceof TitleScreen || refused || mc.screen instanceof JoinMultiplayerScreen)) return;
        long now = System.currentTimeMillis();
        if (now < nextConnect) return;
        nextConnect = now + 10000;
        AltronMod.LOG.info("[Altron] connecting to {}", Config.SERVER);
        ServerData data = new ServerData("Altron host", Config.SERVER, false);
        ConnectScreen.startConnecting(new TitleScreen(), mc, ServerAddress.parseString(Config.SERVER), data, false);
    }

    // What the server tells the player in chat ("this belongs to MJreggich", "you have no access"): the answer to
    // what he just tried, read like a player reads it
    private static final java.util.ArrayDeque<Object[]> SERVER_MESSAGES = new java.util.ArrayDeque<>();

    public static synchronized String serverMessagesSince(long since) {
        StringBuilder sb = new StringBuilder();
        for (Object[] m : SERVER_MESSAGES) {
            if ((Long) m[0] >= since && sb.indexOf((String) m[1]) < 0) sb.append(sb.length() > 0 ? " / " : "").append(m[1]);
        }
        return sb.length() > 300 ? sb.substring(0, 300) + "…" : sb.toString();
    }

    private static synchronized void rememberServerMessage(String text) {
        text = text.replaceAll("§.", "").trim();
        if (text.isEmpty() || text.startsWith("[Baritone]")) return;
        SERVER_MESSAGES.addLast(new Object[]{System.currentTimeMillis(), text});
        while (SERVER_MESSAGES.size() > 20) SERVER_MESSAGES.pollFirst();
    }

    @SubscribeEvent
    public void onChat(ClientChatReceivedEvent event) {
        if (event.isSystem()) {
            rememberServerMessage(event.getMessage().getString());
            return;
        }
        if (event.getBoundChatType() == null) return;
        String sender = event.getBoundChatType().name().getString();
        if (sender.equalsIgnoreCase(Config.BOT_NAME)) return;
        String text = event.getMessage().getString().replaceFirst("^<[^>]+>\\s*", "");
        Bot.send(J.obj("type", "chat", "from", sender, "text", text));
    }
}
