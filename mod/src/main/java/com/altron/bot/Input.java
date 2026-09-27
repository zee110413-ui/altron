package com.altron.bot;

import com.mojang.blaze3d.platform.InputConstants;
import net.minecraft.client.KeyMapping;
import net.minecraft.client.Minecraft;
import net.minecraftforge.client.event.InputEvent;
import net.minecraftforge.common.MinecraftForge;
import org.lwjgl.glfw.GLFW;

import java.util.HashMap;
import java.util.Iterator;
import java.util.Locale;
import java.util.Map;

/**
 * Emulates physical key/mouse presses the same way Minecraft's input handlers do,
 * so vanilla and modded key bindings react as if a human pressed them.
 */
public final class Input {
    private static final Map<InputConstants.Key, Integer> HELD = new HashMap<>();

    private Input() {
    }

    public static void press(InputConstants.Key key) {
        set(key, true);
    }

    public static void release(InputConstants.Key key) {
        set(key, false);
        HELD.remove(key);
    }

    /** Press now, release automatically after {@code ticks}. */
    public static void hold(InputConstants.Key key, int ticks) {
        set(key, true);
        HELD.put(key, Math.max(1, ticks));
    }

    public static void tick() {
        Iterator<Map.Entry<InputConstants.Key, Integer>> it = HELD.entrySet().iterator();
        while (it.hasNext()) {
            Map.Entry<InputConstants.Key, Integer> e = it.next();
            int left = e.getValue() - 1;
            if (left <= 0) {
                set(e.getKey(), false);
                it.remove();
            } else {
                e.setValue(left);
            }
        }
    }

    public static void releaseAll() {
        for (InputConstants.Key k : HELD.keySet()) set(k, false);
        HELD.clear();
        KeyMapping.releaseAll();
    }

    private static void set(InputConstants.Key key, boolean down) {
        Minecraft mc = Minecraft.getInstance();
        int action = down ? GLFW.GLFW_PRESS : GLFW.GLFW_RELEASE;
        if (key.getType() == InputConstants.Type.MOUSE) {
            int button = key.getValue();
            if (MinecraftForge.EVENT_BUS.post(new InputEvent.MouseButton.Pre(button, action, 0))) return;
            if (mc.screen == null) {
                KeyMapping.set(key, down);
                if (down) KeyMapping.click(key);
            }
            MinecraftForge.EVENT_BUS.post(new InputEvent.MouseButton.Post(button, action, 0));
        } else {
            if (mc.screen == null) {
                KeyMapping.set(key, down);
                if (down) KeyMapping.click(key);
            }
            int scan = key.getType() == InputConstants.Type.KEYSYM ? GLFW.glfwGetKeyScancode(key.getValue()) : key.getValue();
            MinecraftForge.EVENT_BUS.post(new InputEvent.Key(key.getValue(), scan, action, 0));
        }
    }

    /** Find a key binding by its id ("key.jump") or a friendly alias ("jump", "reload"). */
    public static KeyMapping find(String name) {
        Minecraft mc = Minecraft.getInstance();
        String n = name.toLowerCase(Locale.ROOT).trim();
        String alias = switch (n) {
            case "jump", "прыжок" -> "key.jump";
            case "sneak", "shift", "присесть" -> "key.sneak";
            case "sprint", "бег" -> "key.sprint";
            case "attack", "left_click", "лкм" -> "key.attack";
            case "use", "right_click", "пкм" -> "key.use";
            case "drop", "выбросить" -> "key.drop";
            case "swap", "swap_offhand" -> "key.swapOffhand";
            case "forward", "вперед" -> "key.forward";
            case "back", "назад" -> "key.back";
            case "left", "влево" -> "key.left";
            case "right", "вправо" -> "key.right";
            default -> n;
        };
        for (KeyMapping km : mc.options.keyMappings) {
            if (km.getName().equalsIgnoreCase(alias)) return km;
        }
        // Loose match on the binding id, e.g. "reload" -> "key.tacz.reload.desc"
        for (KeyMapping km : mc.options.keyMappings) {
            if (km.getName().toLowerCase(Locale.ROOT).contains(n)) return km;
        }
        return null;
    }
}
