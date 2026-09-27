package com.altron.bot;

import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.tags.TagKey;
import net.minecraft.world.item.BlockItem;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * Resolves what the player said ("алмазы", "diamond", "tacz:ammo", "#minecraft:logs")
 * to registry entries, using ids and the in-game (Russian) display names.
 */
public final class Names {
    private static Map<Item, String> itemNames;
    private static Map<Block, String> blockNames;

    private static final Map<String, String> ALIASES = Map.ofEntries(
            Map.entry("дерево", "#minecraft:logs"), Map.entry("деревья", "#minecraft:logs"),
            Map.entry("древесина", "#minecraft:logs"), Map.entry("бревна", "#minecraft:logs"),
            Map.entry("бревно", "#minecraft:logs"), Map.entry("wood", "#minecraft:logs"),
            Map.entry("log", "#minecraft:logs"), Map.entry("logs", "#minecraft:logs"),
            // cobblestone is what stone drops: mining "cobblestone" means mining stone
            Map.entry("камень", "minecraft:stone,minecraft:deepslate"),
            Map.entry("булыжник", "minecraft:stone,minecraft:cobblestone"),
            Map.entry("cobblestone", "minecraft:stone,minecraft:cobblestone"),
            Map.entry("minecraft:cobblestone", "minecraft:stone,minecraft:cobblestone"),
            Map.entry("cobbled_deepslate", "minecraft:deepslate,minecraft:cobbled_deepslate"),
            Map.entry("земля", "minecraft:dirt"), Map.entry("песок", "minecraft:sand"),
            Map.entry("доски", "#minecraft:planks"), Map.entry("planks", "#minecraft:planks"));

    private Names() {
    }

    static String norm(String s) {
        return s.toLowerCase(Locale.ROOT).replace('ё', 'е').trim();
    }

    /** Crude Russian stemmer: drop common endings so "алмазы" matches "Алмаз". */
    static String stem(String w) {
        String[] ends = {"ами", "ями", "ого", "его", "ому", "ему", "ыми", "ими", "ов", "ев", "ей", "ам", "ям", "ах", "ях",
                "ой", "ый", "ий", "ая", "яя", "ое", "ее", "ые", "ие", "ую", "юю", "ы", "и", "а", "я", "у", "ю", "е", "о", "s"};
        for (String e : ends) {
            if (w.length() - e.length() >= 3 && w.endsWith(e)) return w.substring(0, w.length() - e.length());
        }
        return w;
    }

    private static Map<Item, String> itemNames() {
        if (itemNames == null) {
            itemNames = new HashMap<>();
            for (Item item : BuiltInRegistries.ITEM) {
                try {
                    itemNames.put(item, norm(new ItemStack(item).getHoverName().getString()));
                } catch (Exception ignored) {
                }
            }
        }
        return itemNames;
    }

    private static Map<Block, String> blockNames() {
        if (blockNames == null) {
            blockNames = new HashMap<>();
            for (Block b : BuiltInRegistries.BLOCK) {
                try {
                    blockNames.put(b, norm(b.getName().getString()));
                } catch (Exception ignored) {
                }
            }
        }
        return blockNames;
    }

    private static int score(String query, ResourceLocation id, String display) {
        String q = norm(query);
        String qid = q.replace(' ', '_');
        String path = id.getPath();
        int bonus = id.getNamespace().equals("minecraft") ? 3 : 0;
        if (path.equals(qid)) return 100 + bonus;
        if (display.equals(q)) return 95 + bonus;
        if (display.startsWith(q)) return 75 + bonus;
        if (path.startsWith(qid)) return 65 + bonus;
        if (path.contains(qid)) return 55 + bonus;
        if (display.contains(q)) return 50 + bonus;
        String[] words = q.split("[\\s_]+");
        int hits = 0;
        for (String w : words) {
            if (w.length() < 2) continue;
            String st = stem(w);
            if (display.contains(st) || path.contains(st)) hits++;
        }
        if (hits > 0 && hits == words.length) return 40 + hits + bonus;
        return 0;
    }

    public static List<Item> items(String query, int limit) {
        String q = norm(query);
        List<Item> out = new ArrayList<>();
        if (q.isEmpty()) return out;
        ResourceLocation rl = ResourceLocation.tryParse(q);
        if (q.contains(":") && rl != null && BuiltInRegistries.ITEM.containsKey(rl)) {
            out.add(BuiltInRegistries.ITEM.get(rl));
            return out;
        }
        List<Map.Entry<Item, Integer>> scored = new ArrayList<>();
        for (Map.Entry<Item, String> e : itemNames().entrySet()) {
            if (e.getKey() == Items.AIR) continue;
            int s = score(q, BuiltInRegistries.ITEM.getKey(e.getKey()), e.getValue());
            if (s > 0) scored.add(Map.entry(e.getKey(), s));
        }
        scored.sort(Comparator.<Map.Entry<Item, Integer>>comparingInt(Map.Entry::getValue).reversed()
                .thenComparingInt(e -> BuiltInRegistries.ITEM.getKey(e.getKey()).getPath().length()));
        for (int i = 0; i < scored.size() && out.size() < limit; i++) out.add(scored.get(i).getKey());
        return out;
    }

    public static Item item(String query) {
        List<Item> l = items(query, 1);
        return l.isEmpty() ? null : l.get(0);
    }

    /** Blocks for a query; material names ("алмазы", "iron") map to their ores. */
    public static List<Block> blocks(String query) {
        String q = norm(query);
        Set<Block> out = new LinkedHashSet<>();
        if (q.isEmpty()) return new ArrayList<>();
        q = ALIASES.getOrDefault(q, q);
        if (q.contains(",")) {
            for (String part : q.split(",")) out.addAll(blocks(part));
            return new ArrayList<>(out);
        }
        if (q.startsWith("#")) {
            ResourceLocation tag = ResourceLocation.tryParse(q.substring(1));
            if (tag != null) {
                TagKey<Block> key = TagKey.create(Registries.BLOCK, tag);
                BuiltInRegistries.BLOCK.getTagOrEmpty(key).forEach(h -> out.add(h.value()));
            }
            return new ArrayList<>(out);
        }
        ResourceLocation rl = ResourceLocation.tryParse(q);
        if (q.contains(":") && rl != null && BuiltInRegistries.BLOCK.containsKey(rl)) {
            out.add(BuiltInRegistries.BLOCK.get(rl));
            return new ArrayList<>(out);
        }
        // Material -> ores: "diamond"/"алмазы" -> diamond_ore, deepslate_diamond_ore
        Item it = item(q);
        if (it != null && !(it instanceof BlockItem)) {
            String mat = BuiltInRegistries.ITEM.getKey(it).getPath().replace("raw_", "").replace("_ingot", "").replace("_gem", "");
            for (Block b : BuiltInRegistries.BLOCK) {
                String p = BuiltInRegistries.BLOCK.getKey(b).getPath();
                if (p.contains(mat) && p.contains("ore") && !p.contains("raw")) out.add(b);
            }
            if (!out.isEmpty()) return new ArrayList<>(out);
        }
        List<Map.Entry<Block, Integer>> scored = new ArrayList<>();
        for (Map.Entry<Block, String> e : blockNames().entrySet()) {
            if (e.getKey() == Blocks.AIR) continue;
            int s = score(q, BuiltInRegistries.BLOCK.getKey(e.getKey()), e.getValue());
            if (s > 0) scored.add(Map.entry(e.getKey(), s));
        }
        scored.sort(Comparator.<Map.Entry<Block, Integer>>comparingInt(Map.Entry::getValue).reversed()
                .thenComparingInt(e -> BuiltInRegistries.BLOCK.getKey(e.getKey()).getPath().length()));
        if (!scored.isEmpty()) {
            int top = scored.get(0).getValue();
            for (Map.Entry<Block, Integer> e : scored) {
                if (e.getValue() < top - 5 || out.size() >= 6) break;
                out.add(e.getKey());
            }
        }
        // "железо" matches iron doors, bars and ore alike: when ores are among the hits, mean the ores
        List<Block> ores = new ArrayList<>();
        for (Block b : out) if (BuiltInRegistries.BLOCK.getKey(b).getPath().contains("ore")) ores.add(b);
        if (!ores.isEmpty() && ores.size() < out.size()) {
            out.clear();
            out.addAll(ores);
        }
        // Deepslate variants of ores
        for (Block b : new ArrayList<>(out)) {
            ResourceLocation id = BuiltInRegistries.BLOCK.getKey(b);
            if (!id.getPath().contains("ore") || id.getPath().startsWith("deepslate_")) continue;
            ResourceLocation deep = new ResourceLocation(id.getNamespace(), "deepslate_" + id.getPath());
            if (BuiltInRegistries.BLOCK.containsKey(deep)) out.add(BuiltInRegistries.BLOCK.get(deep));
        }
        return new ArrayList<>(out);
    }

    public static List<Block> blocks(List<String> queries) {
        Set<Block> all = new LinkedHashSet<>();
        for (String q : queries) all.addAll(blocks(q));
        return new ArrayList<>(all);
    }
}
