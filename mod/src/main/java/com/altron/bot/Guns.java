package com.altron.bot;

import com.altron.AltronMod;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.BowItem;
import net.minecraft.world.item.CrossbowItem;
import net.minecraft.world.item.ItemStack;

import java.lang.reflect.Method;

/**
 * Firearms support. TaCZ and SuperbWarfare expose public entry points that the mods'
 * own input handlers call; using them works even when the bot window has no focus.
 */
public final class Guns {
    private static boolean taczTried, sbwTried;
    private static Class<?> taczGunIface;
    private static Method taczFrom, taczShoot, taczReload, taczBolt, taczAim, taczIsAim, taczGetIGun, taczAmmo, taczHasInvAmmo;
    private static Class<?> sbwGunClass;
    private static Method sbwFirePress, sbwFireRelease;
    private static boolean sbwFiring;
    private static Method sbwData, sbwEnough, sbwBackup, sbwReloading, taczGunId, taczIndex;
    private static java.lang.reflect.Field sbwAmmoField;
    private static Object sbwChannel, sbwReloadMsg;
    private static int sbwDebug;

    private Guns() {
    }

    private static void initTacz() {
        if (taczTried) return;
        taczTried = true;
        try {
            Class<?> op = Class.forName("com.tacz.guns.api.client.gameplay.IClientPlayerGunOperator");
            taczFrom = op.getMethod("fromLocalPlayer", LocalPlayer.class);
            taczShoot = op.getMethod("shoot");
            taczReload = op.getMethod("reload");
            taczBolt = op.getMethod("bolt");
            taczAim = op.getMethod("aim", boolean.class);
            taczIsAim = op.getMethod("isAim");
            taczGunIface = Class.forName("com.tacz.guns.api.item.IGun");
            taczGetIGun = taczGunIface.getMethod("getIGunOrNull", ItemStack.class);
            taczAmmo = taczGunIface.getMethod("getCurrentAmmoCount", ItemStack.class);
            taczHasInvAmmo = taczGunIface.getMethod("hasInventoryAmmo", net.minecraft.world.entity.LivingEntity.class, ItemStack.class, boolean.class);
            taczGunId = taczGunIface.getMethod("getGunId", ItemStack.class);
            taczIndex = Class.forName("com.tacz.guns.api.TimelessAPI").getMethod("getCommonGunIndex", net.minecraft.resources.ResourceLocation.class);
            AltronMod.LOG.info("[Altron] TaCZ guns supported");
        } catch (Throwable t) {
            taczGunIface = null;
        }
    }

    private static void initSbw() {
        if (sbwTried) return;
        sbwTried = true;
        try {
            sbwGunClass = Class.forName("com.atsuishio.superbwarfare.item.gun.GunItem");
            Class<?> ch = Class.forName("com.atsuishio.superbwarfare.client.ClickHandler");
            sbwFirePress = ch.getMethod("handleWeaponFirePress", Player.class, ItemStack.class);
            sbwFireRelease = ch.getMethod("handleWeaponFireRelease");
            // the magazine and reloading: a new SuperbWarfare gun comes empty, a player presses R
            Class<?> gd = Class.forName("com.atsuishio.superbwarfare.data.gun.GunData");
            sbwData = gd.getMethod("from", ItemStack.class);
            sbwEnough = gd.getMethod("hasEnoughAmmoToShoot", net.minecraft.world.entity.Entity.class);
            sbwBackup = gd.getMethod("hasBackupAmmo", net.minecraft.world.entity.Entity.class);
            sbwReloading = gd.getMethod("reloading");
            sbwAmmoField = gd.getField("ammo");
            sbwChannel = Class.forName("com.atsuishio.superbwarfare.network.NetworkRegistry").getField("PACKET_HANDLER").get(null);
            sbwReloadMsg = Class.forName("com.atsuishio.superbwarfare.network.message.send.ReloadMessage").getField("INSTANCE").get(null);
            AltronMod.LOG.info("[Altron] SuperbWarfare guns supported");
        } catch (Throwable t) {
            sbwGunClass = null;
        }
    }

    public static boolean isTacz(ItemStack s) {
        initTacz();
        return taczGunIface != null && !s.isEmpty() && taczGunIface.isInstance(s.getItem());
    }

    public static boolean isSbw(ItemStack s) {
        initSbw();
        return sbwGunClass != null && !s.isEmpty() && sbwGunClass.isInstance(s.getItem());
    }

    public static boolean isBow(ItemStack s) {
        return s.getItem() instanceof BowItem || s.getItem() instanceof CrossbowItem;
    }

    public static boolean isGun(ItemStack s) {
        return isTacz(s) || isSbw(s);
    }

    /**
     * A rocket or grenade launcher: its shot explodes where it hits, so never at a target right next to us
     * (he blew himself up with an M320 at two blocks).
     */
    public static boolean explosive(ItemStack s) {
        if (isTacz(s)) {
            try {
                Object gun = taczGetIGun.invoke(null, s);
                Object id = taczGunId.invoke(gun, s);
                var index = (java.util.Optional<?>) taczIndex.invoke(null, id);
                return index.isPresent() && "rpg".equals(index.get().getClass().getMethod("getType").invoke(index.get()));
            } catch (Throwable t) {
                return false;
            }
        }
        String id = Bot.id(s.getItem());
        return isSbw(s) && id.matches(".*(rpg|javelin|m79|launcher|mortar|rocket|grenade|stinger|igla|smaw|m72).*");
    }

    private static Object sbwGun(ItemStack s) throws Exception {
        return sbwData.invoke(null, s);
    }

    /** Ammo loaded in a TaCZ or SuperbWarfare gun, or -1 if unknown. */
    public static int loadedAmmo(ItemStack s) {
        if (isSbw(s)) {
            try {
                Object v = sbwAmmoField.get(sbwGun(s));
                return (Integer) v.getClass().getMethod("get").invoke(v);
            } catch (Throwable t) {
                return -1;
            }
        }
        if (!isTacz(s)) return -1;
        try {
            Object gun = taczGetIGun.invoke(null, s);
            return gun == null ? -1 : (Integer) taczAmmo.invoke(gun, s);
        } catch (Throwable t) {
            return -1;
        }
    }

    /** True if the player carries ammo for this TaCZ gun (or it does not need any). */
    public static boolean hasSpareAmmo(LocalPlayer p, ItemStack s) {
        if (isSbw(s)) {
            try {
                return (Boolean) sbwBackup.invoke(sbwGun(s), p);
            } catch (Throwable t) {
                return true;
            }
        }
        if (!isTacz(s)) return true;
        try {
            Object gun = taczGetIGun.invoke(null, s);
            return gun == null || (Boolean) taczHasInvAmmo.invoke(gun, p, s, false);
        } catch (Throwable t) {
            return true;
        }
    }

    /** Pull the trigger once. Returns the TaCZ result name, "OK" or an error. */
    public static String fire(LocalPlayer p, ItemStack held) {
        if (isTacz(held)) {
            try {
                Object op = taczFrom.invoke(null, p);
                Object result = taczShoot.invoke(op);
                String r = String.valueOf(result);
                switch (r) {
                    case "NO_AMMO" -> taczReload.invoke(op);
                    case "NEED_BOLT" -> taczBolt.invoke(op);
                    case "IS_SPRINTING" -> p.setSprinting(false);
                    default -> {
                    }
                }
                return r;
            } catch (Throwable t) {
                return "ERROR " + t;
            }
        }
        if (isSbw(held)) {
            try {
                Object gun = sbwGun(held);
                if (++sbwDebug % 20 == 1) {
                    AltronMod.LOG.info("[Altron] SBW {}: ammo={} enough={} reloading={} backup={} windowActive={} grabbed={} screen={}",
                            Bot.id(held.getItem()), loadedAmmo(held), sbwEnough.invoke(gun, p), sbwReloading.invoke(gun),
                            sbwBackup.invoke(gun, p), Bot.mc().isWindowActive(), Bot.mc().mouseHandler.isMouseGrabbed(), Bot.mc().screen);
                }
                if (!(Boolean) sbwEnough.invoke(gun, p)) {
                    // empty magazine: reload like pressing R (the gun would just click otherwise)
                    ceaseFire();
                    if (!(Boolean) sbwReloading.invoke(gun)) sbwReload();
                    return "NO_AMMO";
                }
                if (sbwFiring) sbwFireRelease.invoke(null);
                Focus.pretend(true);   // its trigger works only for a window in front
                sbwFirePress.invoke(null, p, held);
                sbwFiring = true;
                return "OK";
            } catch (Throwable t) {
                AltronMod.LOG.warn("[Altron] SBW fire failed", t);
                return "ERROR " + t;
            }
        }
        return "NOT_GUN";
    }

    public static boolean firing() {
        return sbwFiring;
    }

    /** Release a held trigger (SuperbWarfare automatic weapons keep firing while held). */
    public static void ceaseFire() {
        if (sbwFiring && sbwFireRelease != null) {
            try {
                sbwFireRelease.invoke(null);
            } catch (Throwable ignored) {
            }
        }
        sbwFiring = false;
        Focus.pretend(false);
    }

    private static void sbwReload() {
        try {
            ((net.minecraftforge.network.simple.SimpleChannel) sbwChannel).sendToServer(sbwReloadMsg);
        } catch (Throwable t) {
            AltronMod.LOG.warn("[Altron] SuperbWarfare reload failed: {}", t.toString());
        }
    }

    public static void reload(LocalPlayer p) {
        if (isSbw(p.getMainHandItem())) {
            sbwReload();
            return;
        }
        if (!isTacz(p.getMainHandItem())) {
            var km = Input.find("reload");
            if (km != null) Input.hold(km.getKey(), 2);
            return;
        }
        try {
            taczReload.invoke(taczFrom.invoke(null, p));
        } catch (Throwable ignored) {
        }
    }

    public static void aim(LocalPlayer p, boolean aim) {
        if (!isTacz(p.getMainHandItem())) return;
        try {
            Object op = taczFrom.invoke(null, p);
            if ((Boolean) taczIsAim.invoke(op) != aim) taczAim.invoke(op, aim);
        } catch (Throwable ignored) {
        }
    }
}
