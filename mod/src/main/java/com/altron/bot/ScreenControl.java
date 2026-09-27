package com.altron.bot;

import com.mojang.blaze3d.platform.NativeImage;
import com.mojang.blaze3d.platform.Window;
import net.minecraft.client.Minecraft;
import net.minecraft.client.Screenshot;
import net.minecraft.client.gui.components.AbstractWidget;
import net.minecraft.client.gui.components.EditBox;
import net.minecraft.client.gui.components.events.GuiEventListener;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.gui.screens.inventory.AbstractContainerScreen;
import net.minecraft.world.inventory.Slot;
import org.lwjgl.glfw.GLFW;

import java.util.Base64;
import java.util.List;
import java.util.Locale;

/**
 * Generic control of ANY open screen, like a player with a mouse and keyboard:
 * read widgets and slots, click at a point, click a button, type text, press keys, take a screenshot.
 * Coordinates are pixels of the screenshot (the game window framebuffer).
 */
public final class ScreenControl {
    private ScreenControl() {
    }

    private static double scaleX() {
        Window w = Minecraft.getInstance().getWindow();
        return (double) w.getWidth() / w.getGuiScaledWidth();
    }

    private static double scaleY() {
        Window w = Minecraft.getInstance().getWindow();
        return (double) w.getHeight() / w.getGuiScaledHeight();
    }

    public static String info() {
        Minecraft mc = Minecraft.getInstance();
        Screen s = mc.screen;
        Window w = mc.getWindow();
        if (s == null) return "Окно не открыто (вижу мир). Размер экрана " + w.getWidth() + "x" + w.getHeight() + ".";
        StringBuilder sb = new StringBuilder();
        sb.append("Окно: \"").append(s.getTitle().getString()).append("\" (").append(s.getClass().getSimpleName())
                .append("), экран ").append(w.getWidth()).append("x").append(w.getHeight()).append(" px\n");
        List<? extends GuiEventListener> children = s.children();
        for (int i = 0; i < children.size(); i++) {
            if (!(children.get(i) instanceof AbstractWidget aw) || !aw.visible) continue;
            String kind = aw instanceof EditBox ? "поле ввода" : "кнопка";
            String text = aw instanceof EditBox eb ? eb.getValue() : aw.getMessage().getString();
            sb.append("- widget ").append(i).append(": ").append(kind).append(" \"").append(text.isBlank() ? "(значок без текста)" : text)
                    .append("\" центр ")
                    .append((int) ((aw.getX() + aw.getWidth() / 2.0) * scaleX())).append(",")
                    .append((int) ((aw.getY() + aw.getHeight() / 2.0) * scaleY()))
                    .append(aw.active ? "" : " (неактивна)");
            String tip = tooltip(aw);
            if (!tip.isEmpty()) sb.append(" — подсказка: ").append(tip);   // what an icon button does, as the mouse-over shows
            sb.append('\n');
        }
        if (s instanceof AbstractContainerScreen<?> cs) {
            var p = Bot.player();
            int shown = 0;
            for (Slot slot : cs.getMenu().slots) {
                if (slot.getItem().isEmpty() && Inv.isPlayerSlot(p, slot)) continue;
                if (shown++ > 60) break;
                sb.append("- слот ").append(slot.index).append(Inv.isPlayerSlot(p, slot) ? " (мой)" : "").append(": ")
                        .append(slot.getItem().isEmpty() ? "пусто" : Bot.describe(slot.getItem())).append(" центр ")
                        .append((int) ((cs.getGuiLeft() + slot.x + 8) * scaleX())).append(",")
                        .append((int) ((cs.getGuiTop() + slot.y + 8) * scaleY())).append('\n');
            }
        }
        return sb.toString().trim();
    }

    /** The text a player sees when hovering the mouse over a widget ("" if none). */
    private static String tooltip(AbstractWidget aw) {
        try {
            var tip = aw.getTooltip();
            if (tip == null) return "";
            StringBuilder sb = new StringBuilder();
            for (var line : tip.toCharSequence(Minecraft.getInstance())) {
                if (sb.length() > 0) sb.append(' ');
                line.accept((index, style, codePoint) -> {
                    sb.appendCodePoint(codePoint);
                    return true;
                });
            }
            String s = sb.toString().replaceAll("\\s+", " ").trim();
            return s.length() > 160 ? s.substring(0, 160) + "…" : s;
        } catch (Exception e) {
            return "";
        }
    }

    /** Click at screenshot pixel coordinates. */
    public static String click(double px, double py, int button) {
        Screen s = Minecraft.getInstance().screen;
        if (s == null) return "окно не открыто";
        double gx = px / scaleX(), gy = py / scaleY();
        s.mouseMoved(gx, gy);
        s.mouseClicked(gx, gy, button);
        s.mouseReleased(gx, gy, button);
        return "кликнул " + (button == 1 ? "ПКМ" : "ЛКМ") + " в " + (int) px + "," + (int) py;
    }

    public static String clickWidget(int index) {
        Screen s = Minecraft.getInstance().screen;
        if (s == null) return "окно не открыто";
        List<? extends GuiEventListener> children = s.children();
        if (index < 0 || index >= children.size() || !(children.get(index) instanceof AbstractWidget aw)) {
            return "нет widget " + index;
        }
        double gx = aw.getX() + aw.getWidth() / 2.0, gy = aw.getY() + aw.getHeight() / 2.0;
        s.mouseClicked(gx, gy, 0);
        s.mouseReleased(gx, gy, 0);
        return "нажал \"" + aw.getMessage().getString() + "\"";
    }

    public static String type(String text) {
        Screen s = Minecraft.getInstance().screen;
        if (s == null) return "окно не открыто";
        // focus the first text field if nothing is focused
        if (!(s.getFocused() instanceof EditBox)) {
            for (GuiEventListener l : s.children()) {
                if (l instanceof EditBox eb && eb.visible) {
                    s.setFocused(eb);
                    break;
                }
            }
        }
        for (char c : text.toCharArray()) s.charTyped(c, 0);
        return "ввёл: " + text;
    }

    public static String key(String name) {
        Screen s = Minecraft.getInstance().screen;
        int code = switch (name.toLowerCase(Locale.ROOT)) {
            case "enter", "return" -> GLFW.GLFW_KEY_ENTER;
            case "escape", "esc" -> GLFW.GLFW_KEY_ESCAPE;
            case "backspace" -> GLFW.GLFW_KEY_BACKSPACE;
            case "tab" -> GLFW.GLFW_KEY_TAB;
            case "up" -> GLFW.GLFW_KEY_UP;
            case "down" -> GLFW.GLFW_KEY_DOWN;
            case "left" -> GLFW.GLFW_KEY_LEFT;
            case "right" -> GLFW.GLFW_KEY_RIGHT;
            case "space" -> GLFW.GLFW_KEY_SPACE;
            case "delete" -> GLFW.GLFW_KEY_DELETE;
            default -> name.length() == 1 ? GLFW.GLFW_KEY_A + (Character.toUpperCase(name.charAt(0)) - 'A') : -1;
        };
        if (code < 0) return "не знаю клавишу " + name;
        if (s == null) return "окно не открыто (для игровых клавиш используй press_key)";
        int scan = GLFW.glfwGetKeyScancode(code);
        s.keyPressed(code, scan, 0);
        s.keyReleased(code, scan, 0);
        return "нажал " + name;
    }

    /** PNG of the current frame (what the bot's player sees, including open windows), base64. */
    public static String screenshotBase64() throws java.io.IOException {
        try (NativeImage img = Screenshot.takeScreenshot(Minecraft.getInstance().getMainRenderTarget())) {
            return Base64.getEncoder().encodeToString(img.asByteArray());
        }
    }
}
