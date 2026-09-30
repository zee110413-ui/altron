package com.altron.bot;

import net.minecraft.client.player.LocalPlayer;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.monster.Creeper;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.ItemStack;

import java.util.List;
import java.util.Locale;
import java.util.function.Predicate;

/**
 * Fighting logic shared by the attack and guard tasks:
 * pick a target, choose a weapon, aim, shoot (TaCZ/SuperbWarfare/bow) or melee.
 */
public class Combat {
    private final Predicate<Entity> filter;
    private final double radius;
    private Entity target;
    private int aimTicks;
    private int pathCooldown;
    private int reloadWait;
    private int emptyShots;
    private boolean gunsDry;
    private boolean weaponChosen;
    private int bowCharge;
    public int kills;

    public Combat(Predicate<Entity> filter, double radius) {
        this.filter = filter;
        this.radius = radius;
    }

    /** Build a target filter from what the player said: "hostile", a mob id/name, or "player:Name". */
    public static Predicate<Entity> filterFor(String what, String owner) {
        String w = what == null ? "hostile" : what.toLowerCase(Locale.ROOT).trim();
        Predicate<Entity> notFriend = e -> !(e instanceof Player pl
                && (pl.getGameProfile().getName().equalsIgnoreCase(owner) || pl == Bot.player()));
        if (w.isEmpty() || w.equals("hostile") || w.equals("monsters") || w.equals("враги") || w.equals("мобы")) {
            return e -> Info.isHostile(e) && notFriend.test(e);
        }
        if (w.startsWith("player:")) {
            String name = w.substring(7);
            return e -> e instanceof Player pl && pl.getGameProfile().getName().equalsIgnoreCase(name) && e.isAlive();
        }
        return e -> e instanceof LivingEntity && e.isAlive() && notFriend.test(e)
                && (Bot.id(e).contains(w) || e.getName().getString().toLowerCase(Locale.ROOT).contains(w)
                || Names.stem(w).length() >= 3 && e.getName().getString().toLowerCase(Locale.ROOT).contains(Names.stem(w)));
    }

    public Entity target() {
        return target;
    }

    private boolean valid(Entity e) {
        LocalPlayer p = Bot.player();
        return e != null && e.isAlive() && !e.isRemoved() && e.distanceTo(p) <= radius + 8
                && !(e instanceof LivingEntity le && le.isDeadOrDying());
    }

    private Entity pick() {
        LocalPlayer p = Bot.player();
        // Only targets the bot can see (or hear right next to it): no wallhack
        List<Entity> list = Info.entities(radius, e -> filter.test(e) && Info.perceives(e));
        Entity best = null;
        double bestScore = Double.MAX_VALUE;
        for (Entity e : list) {
            double score = e.distanceTo(p) + (p.hasLineOfSight(e) ? 0 : 20);
            if (score < bestScore) {
                bestScore = score;
                best = e;
            }
        }
        return best;
    }

    /** One tick of fighting. Returns false when there is nothing to fight. */
    public boolean tick() {
        LocalPlayer p = Bot.player();
        if (target != null && !valid(target)) {
            if (target instanceof LivingEntity le && (le.isDeadOrDying() || le.getHealth() <= 0)) kills++;
            target = null;
        }
        if (target == null) {
            target = pick();
            aimTicks = 0;
            weaponChosen = false;
            if (target == null) {
                Guns.ceaseFire();
                return false;
            }
        }
        boolean chose = !weaponChosen;
        if (!weaponChosen) {
            chooseWeapon(p);
            weaponChosen = true;
        }
        ItemStack held = p.getMainHandItem();
        double d = p.distanceTo(target);
        boolean los = p.hasLineOfSight(target);
        boolean ranged = !gunsDry && (Guns.isGun(held) || Guns.isBow(held));
        if (chose) {
            StringBuilder inv = new StringBuilder();
            for (int i = 0; i < 41; i++) {
                ItemStack s = p.getInventory().getItem(i);
                if (!s.isEmpty()) inv.append(i).append('=').append(Bot.id(s.getItem())).append(Guns.isGun(s) ? "(gun)" : "").append(' ');
            }
            com.altron.AltronMod.LOG.info("[Altron] combat: target {} at {}, weapon {} (TaCZ {}, SBW {}), ranged {}, dry {}, selected {}, inventory: {}",
                    Bot.id(target), Math.round(d), Bot.id(held.getItem()), Guns.isTacz(held), Guns.isSbw(held), ranged, gunsDry,
                    p.getInventory().selected, inv);
        }
        // Never melee a creeper
        if (!ranged && target instanceof Creeper && d < 5) {
            approach(target, 8);
            return true;
        }
        if (ranged) {
            // gun mods animate and time their shots while the game draws: draw during a firefight
            BotClient.keepRendering(60);
            if (!los || d > 60) {
                Guns.ceaseFire();
                approach(target, 6);
                return true;
            }
            if (Guns.explosive(held) && d < 7) {
                // a rocket at two blocks kills the shooter too: step back first
                Guns.ceaseFire();
                if (--pathCooldown <= 0) {
                    pathCooldown = 15;
                    var away = p.position().subtract(target.position()).normalize().scale(9).add(p.position());
                    Nav.gotoNear(net.minecraft.core.BlockPos.containing(away), 1);
                }
                Bot.lookAt(target);
                return true;
            }
            if (Nav.busy()) Nav.cancel();
            Bot.lookAt(target);
            // like a player: turn, settle the sight on the target, then pull the trigger (tighter aim far away);
            // a target that keeps dodging gets shot at anyway after a while
            if (!Bot.aimed(target, d > 20 ? 2.5f : 5f) && ++aimTicks < 15) return true;
            aimTicks = 0;
            if (Guns.isBow(held)) {
                bowTick(p);
                return true;
            }
            if (reloadWait > 0) {
                reloadWait--;
                return true;
            }
            Guns.aim(p, d > 12);
            String r = Guns.fire(p, held);
            if (r.equals("NO_AMMO")) {
                reloadWait = 30;
                if (!Guns.hasSpareAmmo(p, held) && ++emptyShots > 2) {
                    gunsDry = true;
                    weaponChosen = false;
                    BotClient.need("ammo", "Кончились патроны для " + held.getHoverName().getString() + ", перехожу на ближний бой.");
                }
            } else if (r.equals("SUCCESS") || r.equals("OK")) {
                emptyShots = 0;
            }
        } else {
            Guns.ceaseFire();
            if (d > 3.2) {
                approach(target, 1);
                Bot.lookAt(target);
                return true;
            }
            if (Nav.busy()) Nav.cancel();
            Bot.lookAt(target);
            if (p.getAttackStrengthScale(0.5f) >= 0.95f && (Bot.aimed(target, 25) || d < 1.2)) {
                Bot.mc().gameMode.attack(p, target);
                p.swing(InteractionHand.MAIN_HAND);
            }
        }
        return true;
    }

    private void bowTick(LocalPlayer p) {
        var use = Bot.mc().options.keyUse.getKey();
        if (bowCharge == 0) Input.press(use);
        if (++bowCharge >= 22) {
            Input.release(use);
            bowCharge = 0;
        }
    }

    private void approach(Entity e, int range) {
        if (--pathCooldown > 0) return;
        pathCooldown = 20;
        Nav.gotoNear(e.blockPosition(), range);
    }

    /** Gun with ammo > bow with arrows > best melee weapon. */
    private void chooseWeapon(LocalPlayer p) {
        if (!gunsDry) {
            if (Inv.holdMatching(p, s -> Guns.isTacz(s) && (Guns.loadedAmmo(s) > 0 || Guns.hasSpareAmmo(p, s)))) return;
            if (Inv.holdMatching(p, Guns::isSbw)) return;
        }
        int best = -1;
        double bestDmg = Inv.attackDamage(p.getMainHandItem());
        if (Guns.isGun(p.getMainHandItem())) bestDmg = -1;
        for (int i = 0; i < 36; i++) {
            ItemStack s = p.getInventory().getItem(i);
            if (s.isEmpty() || Guns.isGun(s)) continue;
            double dmg = Inv.attackDamage(s);
            if (dmg > bestDmg) {
                bestDmg = dmg;
                best = i;
            }
        }
        if (best >= 0) Inv.hold(p, best);
    }

    public void stop() {
        Guns.ceaseFire();
        LocalPlayer p = Bot.player();
        if (p != null) Guns.aim(p, false);
        if (bowCharge > 0) {
            Input.release(Bot.mc().options.keyUse.getKey());
            bowCharge = 0;
        }
    }
}
