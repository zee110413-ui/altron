package com.altron;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

/** Small JSON helpers. */
public final class J {
    private J() {
    }

    public static String str(JsonObject o, String key, String def) {
        JsonElement e = o.get(key);
        if (e == null || e.isJsonNull()) return def;
        if (e.isJsonArray()) {
            StringBuilder sb = new StringBuilder();
            for (JsonElement x : e.getAsJsonArray()) {
                if (sb.length() > 0) sb.append(',');
                sb.append(x.getAsString());
            }
            return sb.toString();
        }
        return e.getAsString();
    }

    public static int num(JsonObject o, String key, int def) {
        JsonElement e = o.get(key);
        if (e == null || e.isJsonNull()) return def;
        try {
            return (int) Math.round(e.getAsDouble());
        } catch (Exception ex) {
            return def;
        }
    }

    public static double dbl(JsonObject o, String key, double def) {
        JsonElement e = o.get(key);
        if (e == null || e.isJsonNull()) return def;
        try {
            return e.getAsDouble();
        } catch (Exception ex) {
            return def;
        }
    }

    public static boolean has(JsonObject o, String key) {
        JsonElement e = o.get(key);
        return e != null && !e.isJsonNull();
    }

    /** Build an object from key/value pairs. */
    public static JsonObject obj(Object... kv) {
        JsonObject o = new JsonObject();
        for (int i = 0; i + 1 < kv.length; i += 2) {
            String k = String.valueOf(kv[i]);
            Object v = kv[i + 1];
            if (v == null) continue;
            if (v instanceof Number n) o.addProperty(k, n);
            else if (v instanceof Boolean b) o.addProperty(k, b);
            else if (v instanceof JsonElement je) o.add(k, je);
            else o.addProperty(k, String.valueOf(v));
        }
        return o;
    }

    public static JsonArray arr(double... v) {
        JsonArray a = new JsonArray();
        for (double d : v) a.add(Math.round(d * 10) / 10.0);
        return a;
    }
}
