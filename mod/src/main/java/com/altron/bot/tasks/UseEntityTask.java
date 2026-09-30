package com.altron.bot.tasks;

import com.altron.bot.Nav;
import com.altron.bot.Bot;
import com.altron.bot.Info;
import com.altron.bot.Input;
import com.altron.bot.Task;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.ExperienceOrb;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.item.ItemEntity;
import net.minecraft.world.entity.projectile.Projectile;
import net.minecraft.world.phys.EntityHitResult;

import java.util.List;
import java.util.Locale;
import java.util.regex.Pattern;

/**
 * Right-click an entity: mount a vehicle, trade, feed an animal...
 * With {@code crouch} the bot crouches next to it (reviving a downed player in Incapacitated).
 */
public class UseEntityTask extends Task {
    /** "Get in the tank / car / helicopter": the commander means the vehicle next to us, whatever its exact model. */
    private static final Pattern VEHICLE_WORDS = Pattern.compile("tank|танк|vehicle|машин|car|truck|грузов|jeep|джип|humvee|"
            + "heli|вертол|plane|самол|jet|boat|лодк|катер|bmp|бмп|btr|бтр|apc|техник|superbwarfare|vvp|ashvehicle", Pattern.CASE_INSENSITIVE);

    private final String target;
    private final int holdTicks;
    private final boolean crouch;
    private Entity entity;
    private String note = "";
    private int walk;
    private int held;
    private int aimWait;

    /** Held in the hand for the click (a SecurityCraft remote for a sentry, a lead, shears...), or null. */
    private net.minecraft.world.item.Item item;
    private boolean sneak;

    public UseEntityTask(String target, int holdTicks, boolean crouch) {
        super(crouch ? "revive" : "use_entity");
        this.target = target;
        this.holdTicks = Math.max(1, holdTicks);
        this.crouch = crouch;
    }

    public UseEntityTask withItem(net.minecraft.world.item.Item item, boolean sneak) {
        this.item = item;
        this.sneak = sneak;
        return this;
    }

    /** Where it was last known to be (a downed player behind a wall): walk there first if it is not in sight. */
    private net.minecraft.core.BlockPos near;
    private int nearWalk;

    public UseEntityTask near(net.minecraft.core.BlockPos at) {
        this.near = at;
        return this;
    }

    /** Boats, minecarts, modded tanks, cars, helicopters: big non-living entities. */
    public static boolean isVehicle(Entity e) {
        return !(e instanceof LivingEntity || e instanceof ItemEntity || e instanceof Projectile || e instanceof ExperienceOrb)
                && e.getBbWidth() >= 0.9f && e.getBbHeight() >= 0.5f;
    }

    private boolean matches(Entity e) {
        String t = target.toLowerCase(Locale.ROOT).trim();
        String id = Bot.id(e);
        String name = e.getName().getString().toLowerCase(Locale.ROOT);
        if (id.contains(t) || name.equalsIgnoreCase(t) || name.contains(t)) return true;
        // "superbwarfare:prism_tank" vs "prism tank", "t90" vs "vvp:t90_m": compare the words of the id
        String path = t.contains(":") ? t.substring(t.indexOf(':') + 1) : t;
        String idPath = id.substring(id.indexOf(':') + 1);
        return path.replaceAll("[^a-z0-9а-я]", "").length() >= 3
                && idPath.replaceAll("[^a-z0-9]", "").contains(path.replaceAll("[^a-z0-9а-я]", ""));
    }

    /** What the bot can see around it, for a helpful answer when the target is not there. */
    private static String around() {
        List<Entity> list = Info.entities(24, e -> e.isAlive() && Info.perceives(e) && !(e instanceof ItemEntity)
                && !(e instanceof Projectile) && !(e instanceof ExperienceOrb));
        if (list.isEmpty()) return "рядом никого и никакой техники не вижу";
        StringBuilder sb = new StringBuilder("рядом вижу: ");
        int n = 0;
        for (Entity e : list) {
            if (n++ >= 8) break;
            sb.append(n > 1 ? "; " : "").append(e.getName().getString()).append(" (").append(Bot.id(e)).append(", ")
                    .append(Math.round(e.distanceTo(Bot.player()))).append(" бл.)");
        }
        return sb.toString();
    }

    @Override
    protected Status run() {
        var p = p();
        if (entity == null) {
            List<Entity> list = Info.entities(32, e -> e.isAlive() && Info.perceives(e) && e != p && matches(e));
            if (list.isEmpty() && !crouch && VEHICLE_WORDS.matcher(target).find()) {
                // the exact model is not here, but a vehicle is: that is the one the commander means
                list = Info.entities(10, e -> e.isAlive() && Info.perceives(e) && isVehicle(e));
                if (!list.isEmpty()) note = " (" + target + " не вижу, взял ближайшую технику)";
            }
            if (list.isEmpty() && near != null && p.blockPosition().distSqr(near) > 4) {
                if (nearWalk == 0 || (nearWalk % 40 == 0 && !Nav.busy())) Nav.gotoNear(near, 1);
                if (++nearWalk > 20 * 90) return fail("не смог дойти до " + target + " (" + Bot.pos(near) + ")");
                return Status.RUNNING;
            }
            if (list.isEmpty()) return fail("не вижу рядом: " + target + "; " + around());
            if (nearWalk > 0) Nav.cancel();
            entity = list.get(0);
        }
        if (!entity.isAlive()) return fail(target + " пропал");
        if (item == null && !crouch && held > 0 && p.getVehicle() != null
                && (p.getVehicle() == entity || p.getVehicle().getRootVehicle() == entity.getRootVehicle())) {
            // sitting in it: a helicopter's seat is far from its middle, "too far, walk closer" is wrong now
            String empty = com.altron.bot.VehicleEnergy.problem(entity);
            return done("сел в " + entity.getName().getString() + " (" + Bot.id(entity) + ")" + note
                    + com.altron.bot.VehicleEnergy.describe(entity) + (empty.isEmpty() ? "" : ". ВНИМАНИЕ: " + empty));
        }
        // reach like a player's: from the eyes to the nearest point of its body (a tank's middle is far from its side)
        double reach = Math.sqrt(entity.getBoundingBox().distanceToSqr(p.getEyePosition()));
        if (reach > 2.8 && p.getVehicle() == null) {
            if (walk == 0 || walk % 30 == 0) Nav.gotoNear(entity.blockPosition(), 1);
            if (++walk > 20 * 90) return fail("не смог подойти к " + target);
            return Status.RUNNING;
        }
        if (walk > 0) {
            Nav.cancel();
            walk = 0;
        }
        Bot.lookAt(entity.getBoundingBox().getCenter());
        if (held == 0 && !Bot.aimed(entity.getBoundingBox().getCenter(), 20) && ++aimWait < 12) return Status.RUNNING;
        if (held == 0) {
            Bot.closeContainer();
            if (item != null && !com.altron.bot.Inv.holdMatching(p, s -> s.is(item))) return fail("нет в инвентаре: " + Bot.id(item));
            if (crouch || sneak) Bot.mc().options.keyShift.setDown(true);
            var hit = new EntityHitResult(entity, entity.getBoundingBox().getCenter());
            var r = Bot.mc().gameMode.interactAt(p, entity, hit, InteractionHand.MAIN_HAND);
            if (!r.consumesAction()) Bot.mc().gameMode.interact(p, entity, InteractionHand.MAIN_HAND);
            Input.press(Bot.mc().options.keyUse.getKey());
        }
        if (++held == holdTicks) {
            Input.release(Bot.mc().options.keyUse.getKey());
            if (crouch || sneak) Bot.mc().options.keyShift.setDown(false);
        }
        if (held < holdTicks) return Status.RUNNING;
        String what = entity.getName().getString() + " (" + Bot.id(entity) + ")";
        if (item != null) return done("применил " + Bot.id(item) + " к " + what);
        if (!crouch && isVehicle(entity)) {
            // many vehicles seat the player on a separate seat entity: any vehicle under us counts
            if (p.getVehicle() != null) return done("сел в " + what + note);
            if (held < holdTicks + 20) return Status.RUNNING;   // the server may take a moment
            return fail("не получилось сесть в " + what + " — может, там нет места или нужен ключ/особая клавиша");
        }
        return done((crouch ? "поднимал " : "использовал ") + what + note);
    }

    @Override
    public void stop() {
        Input.release(Bot.mc().options.keyUse.getKey());
        if (crouch || sneak) Bot.mc().options.keyShift.setDown(false);
        if (walk > 0) Nav.cancel();
    }
}
