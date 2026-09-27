package com.altron.net;

import com.altron.AltronMod;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.function.Consumer;

/**
 * TCP link to the Python "brain" (127.0.0.1). One JSON object per line.
 * Reconnects forever in a daemon thread.
 */
public class BrainLink {
    private final String role;
    private final int port;
    private final Consumer<JsonObject> handler;
    private volatile BufferedWriter out;
    private volatile Runnable onConnect;
    private boolean started;

    public BrainLink(String role, int port, Consumer<JsonObject> handler) {
        this.role = role;
        this.port = port;
        this.handler = handler;
    }

    public void setOnConnect(Runnable r) {
        onConnect = r;
    }

    public synchronized void start() {
        if (started) return;
        started = true;
        Thread t = new Thread(this::loop, "Altron-Link-" + role);
        t.setDaemon(true);
        t.start();
    }

    private void loop() {
        boolean warned = false;
        while (true) {
            try (Socket s = new Socket()) {
                s.connect(new InetSocketAddress("127.0.0.1", port), 2000);
                s.setTcpNoDelay(true);
                BufferedReader in = new BufferedReader(new InputStreamReader(s.getInputStream(), StandardCharsets.UTF_8));
                out = new BufferedWriter(new OutputStreamWriter(s.getOutputStream(), StandardCharsets.UTF_8));
                JsonObject hello = new JsonObject();
                hello.addProperty("type", "hello");
                hello.addProperty("role", role);
                send(hello);
                AltronMod.LOG.info("[Altron] connected to brain as {}", role);
                warned = false;
                Runnable r = onConnect;
                if (r != null) r.run();
                String line;
                while ((line = in.readLine()) != null) {
                    if (line.isBlank()) continue;
                    try {
                        handler.accept(JsonParser.parseString(line).getAsJsonObject());
                    } catch (Exception e) {
                        AltronMod.LOG.warn("[Altron] bad message from brain: {}", e.toString());
                    }
                }
            } catch (IOException e) {
                if (!warned) {
                    AltronMod.LOG.info("[Altron] brain not reachable on port {} ({}), retrying", port, e.getMessage());
                    warned = true;
                }
            } finally {
                out = null;
            }
            try {
                Thread.sleep(3000);
            } catch (InterruptedException e) {
                return;
            }
        }
    }

    // ------------------------------------------------------------ a brain on another PC connects in
    private volatile BufferedWriter remoteOut;

    /**
     * Also accept a brain from another PC (Altron's AI running on a second computer): it connects to this game on
     * {@code port} and must first send {"type":"auth","key":...} with the key from the game's config/altron-key.txt.
     * From then on it is this link's brain, exactly like a local one.
     */
    public void listen(int port, String key) {
        if (key == null || key.isBlank()) return;
        Thread t = new Thread(() -> {
            try (java.net.ServerSocket server = new java.net.ServerSocket(port, 2, java.net.InetAddress.getByName("0.0.0.0"))) {
                AltronMod.LOG.info("[Altron] waiting for a brain from another PC on port {}", port);
                while (true) {
                    Socket s = server.accept();
                    try {
                        serveRemote(s, key);
                    } catch (Exception e) {
                        AltronMod.LOG.info("[Altron] remote brain disconnected: {}", e.toString());
                    } finally {
                        remoteOut = null;
                        try {
                            s.close();
                        } catch (IOException ignored) {
                        }
                    }
                }
            } catch (IOException e) {
                AltronMod.LOG.warn("[Altron] cannot accept a remote brain on port {}: {}", port, e.toString());
            }
        }, "Altron-RemoteBrain-" + role);
        t.setDaemon(true);
        t.start();
    }

    private void serveRemote(Socket s, String key) throws IOException {
        s.setTcpNoDelay(true);
        s.setSoTimeout(10000);   // the password must come at once
        BufferedReader in = new BufferedReader(new InputStreamReader(s.getInputStream(), StandardCharsets.UTF_8));
        String first = in.readLine();
        JsonObject auth = first == null ? null : JsonParser.parseString(first).getAsJsonObject();
        if (auth == null || !"auth".equals(auth.has("type") ? auth.get("type").getAsString() : "")
                || !key.equals(auth.has("key") ? auth.get("key").getAsString() : "")) {
            AltronMod.LOG.warn("[Altron] refused a brain from {}: wrong key", s.getRemoteSocketAddress());
            return;
        }
        s.setSoTimeout(0);
        remoteOut = new BufferedWriter(new OutputStreamWriter(s.getOutputStream(), StandardCharsets.UTF_8));
        JsonObject hello = new JsonObject();
        hello.addProperty("type", "hello");
        hello.addProperty("role", role);
        send(hello);
        AltronMod.LOG.info("[Altron] brain connected from another PC: {}", s.getRemoteSocketAddress());
        Runnable r = onConnect;
        if (r != null) r.run();
        String line;
        while ((line = in.readLine()) != null) {
            if (line.isBlank()) continue;
            try {
                handler.accept(JsonParser.parseString(line).getAsJsonObject());
            } catch (Exception e) {
                AltronMod.LOG.warn("[Altron] bad message from brain: {}", e.toString());
            }
        }
    }

    public boolean isConnected() {
        return out != null || remoteOut != null;
    }

    public boolean send(JsonObject o) {
        BufferedWriter w = remoteOut != null ? remoteOut : out;   // a brain on another PC takes precedence
        if (w == null) return false;
        String s = o.toString();
        synchronized (this) {
            try {
                w.write(s);
                w.write('\n');
                w.flush();
                return true;
            } catch (IOException e) {
                if (w == remoteOut) remoteOut = null;
                return false;
            }
        }
    }
}
