package com.altron.bot.tasks;

import com.altron.AltronMod;
import com.altron.bot.Bot;
import com.altron.bot.BotClient;
import com.altron.bot.Info;
import com.altron.bot.Task;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;

import java.lang.reflect.Constructor;
import java.lang.reflect.Method;
import java.util.Comparator;
import java.util.List;

/**
 * Altron mans the gun of a SuperbWarfare vehicle he sits in (the commander drives, or Altron drives and the
 * commander shoots, whatever they agreed): takes a seat that has a weapon, turns to the nearest hostile creature he
 * can see and fires at the weapon's own rate, like a player holding the trigger. Goes on until told to stop.
 * The weapon follows the player's view, as for a person in that seat; firing is SuperbWarfare's own message.
 */
public class VehicleGunTask extends Task {
    private static boolean tried;
    private static Class<?> vehicleClass;
    private static Method seatIndex, hasWeapon, canShoot, rpm;
    private static Object channel;
    private static Constructor<?> fireMessage, seatMessage;

    private final double radius;
    /** What to shoot at, chosen by the AI: "hostile", a creature type ("zombie"), or "player:Name". */
    private final String aim;
    private int cooldown;
    private int shots;
    private int seatTries;
    private int retarget;
    private Entity target;

    public VehicleGunTask(double radius, String aim) {
        super("vehicle_gunner");
        this.radius = Math.max(8, Math.min(96, radius));
        this.aim = aim == null || aim.isBlank() ? "hostile" : aim.toLowerCase(java.util.Locale.ROOT).trim();
    }

    private boolean wanted(Entity e) {
        if (!e.isAlive() || e == p() || !Info.perceives(e)) return false;
        if (aim.equals("hostile")) return Info.isHostile(e);
        if (aim.startsWith("player:")) {
            return e instanceof net.minecraft.world.entity.player.Player pl
                    && pl.getGameProfile().getName().equalsIgnoreCase(aim.substring(7));
        }
        return e instanceof LivingEntity && (Bot.id(e).contains(aim) || e.getName().getString().toLowerCase(java.util.Locale.ROOT).contains(aim));
    }

    private static boolean ready() {
        if (!tried) {
            tried = true;
            try {
                vehicleClass = Class.forName("com.atsuishio.superbwarfare.entity.vehicle.base.VehicleEntity");
                seatIndex = vehicleClass.getMethod("getSeatIndex", Entity.class);
                hasWeapon = vehicleClass.getMethod("hasWeapon", int.class);
                canShoot = vehicleClass.getMethod("canShoot", LivingEntity.class);
                rpm = vehicleClass.getMethod("vehicleWeaponRpm", LivingEntity.class);
                channel = Class.forName("com.atsuishio.superbwarfare.network.NetworkRegistry").getField("PACKET_HANDLER").get(null);
                fireMessage = Class.forName("com.atsuishio.superbwarfare.network.message.send.VehicleFireMessage")
                        .getConstructor(java.util.UUID.class, org.joml.Vector3f.class);
                seatMessage = Class.forName("com.atsuishio.superbwarfare.network.message.send.ChangeVehicleSeatMessage")
                        .getConstructor(int.class);
            } catch (Throwable t) {
                AltronMod.LOG.info("[Altron] no SuperbWarfare vehicle weapons: {}", t.toString());
                vehicleClass = null;
            }
        }
        return vehicleClass != null;
    }

    private static void send(Object message) {
        ((net.minecraftforge.network.simple.SimpleChannel) channel).sendToServer(message);
    }

    /** The SuperbWarfare vehicle he sits in (his seat may be a part of it). */
    private static Entity vehicle() {
        for (Entity v = p().getVehicle(); v != null; v = v.getVehicle()) {
            if (vehicleClass.isInstance(v)) return v;
        }
        return null;
    }

    @Override
    protected Status run() {
        if (!ready()) return fail("в этой сборке нет техники SuperbWarfare с оружием");
        var p = p();
        Entity v = vehicle();
        if (v == null) return fail("я не сижу в боевой технике — сначала сядь в неё (use_entity)");
        BotClient.keepRendering(40);   // turrets turn after the view while the game draws
        try {
            int seat = (int) seatIndex.invoke(v, p);
            if (!(boolean) hasWeapon.invoke(v, seat)) {
                // a seat without a gun (a passenger bench): move to one that has a weapon, as a player would with 1..9
                if (seatTries >= 8) return fail("на моём месте нет оружия, а места с оружием заняты");
                if (age % 10 == 1) {
                    int want = -1;
                    for (int i = seatTries; i < 8 && want < 0; i++) {
                        if ((boolean) hasWeapon.invoke(v, i) && i != seat) want = i;
                    }
                    seatTries = want < 0 ? 8 : want + 1;
                    if (want >= 0) send(seatMessage.newInstance(want));
                }
                return Status.RUNNING;
            }
            if (target == null || !target.isAlive() || --retarget <= 0) {
                retarget = 10;
                List<Entity> foes = Info.entities(radius, this::wanted);
                foes.sort(Comparator.comparingDouble(e -> e.distanceTo(p)));
                target = foes.isEmpty() ? null : foes.get(0);
            }
            if (target == null) return Status.RUNNING;   // on watch: nobody to shoot yet
            Bot.lookAt(target.getBoundingBox().getCenter());
            if (--cooldown <= 0 && Bot.aimed(target.getBoundingBox().getCenter(), 6) && (boolean) canShoot.invoke(v, p)) {
                send(fireMessage.newInstance(null, null));
                shots++;
                int perMinute = Math.max(1, (int) rpm.invoke(v, p));
                cooldown = Math.max(1, 1200 / perMinute);
            }
        } catch (Throwable t) {
            AltronMod.LOG.warn("[Altron] vehicle gun failed", t);
            return fail("не получилось стрелять из этой техники: " + t);
        }
        return Status.RUNNING;
    }

    @Override
    public String progress() {
        return "стрелок: выстрелов " + shots + (target != null && target.isAlive() ? ", цель " + target.getName().getString() : ", жду врагов");
    }
}
