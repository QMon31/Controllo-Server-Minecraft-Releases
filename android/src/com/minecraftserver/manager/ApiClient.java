package com.minecraftserver.manager;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import android.os.Handler;
import android.os.Looper;

public class ApiClient {
    private static final ExecutorService executor = Executors.newFixedThreadPool(4);
    private static final Handler mainHandler = new Handler(Looper.getMainLooper());

    public interface ApiCallback {
        void onSuccess(String response);
        void onError(String error);
    }

    public static void call(String urlStr, String method, String jsonBody, String token, ApiCallback callback) {
        if ("POST".equalsIgnoreCase(method)) {
            post(urlStr, jsonBody, token, 4000, callback);
        } else {
            get(urlStr, token, 4000, callback);
        }
    }

    public static void get(final String urlStr, final String token, final int timeoutMs, final ApiCallback callback) {
        executor.execute(new Runnable() {
            @Override
            public void run() {
                HttpURLConnection conn = null;
                try {
                    URL url = new URL(urlStr);
                    conn = (HttpURLConnection) url.openConnection();
                    conn.setRequestMethod("GET");
                    conn.setConnectTimeout(timeoutMs > 0 ? timeoutMs : 3000);
                    conn.setReadTimeout(5000);
                    if (token != null && !token.isEmpty()) {
                        conn.setRequestProperty("X-Admin-Token", token);
                    }
                    int code = conn.getResponseCode();
                    InputStream is = (code >= 200 && code < 400) ? conn.getInputStream() : conn.getErrorStream();
                    String response = readStream(is);
                    boolean isHtml = response != null && (response.trim().startsWith("<!DOCTYPE") || response.trim().startsWith("<html") || response.trim().startsWith("<!doctype"));
                    if (code >= 200 && code < 400) {
                        if (isHtml) {
                            postError(callback, I18n.t("net.server_si_sta_ricollegando_riprova_tra"));
                        } else {
                            postSuccess(callback, response);
                        }
                    } else {
                        if (isHtml) {
                            postError(callback, I18n.t("net.server_non_momento_raggiungibile_http", code));
                        } else {
                            postError(callback, "HTTP " + code + ": " + errorDetail(response));
                        }
                    }
                } catch (final Exception e) {
                    postError(callback, e.getMessage() != null ? e.getMessage() : I18n.t("net.errore_connessione"));
                } finally {
                    if (conn != null) conn.disconnect();
                }
            }
        });
    }

    public static void post(final String urlStr, final String jsonBody, final String token, final int timeoutMs, final ApiCallback callback) {
        executor.execute(new Runnable() {
            @Override
            public void run() {
                HttpURLConnection conn = null;
                try {
                    URL url = new URL(urlStr);
                    conn = (HttpURLConnection) url.openConnection();
                    conn.setRequestMethod("POST");
                    conn.setConnectTimeout(timeoutMs > 0 ? timeoutMs : 3500);
                    conn.setReadTimeout(5000);
                    conn.setRequestProperty("Content-Type", "application/json; charset=UTF-8");
                    if (token != null && !token.isEmpty()) {
                        conn.setRequestProperty("X-Admin-Token", token);
                    }
                    conn.setDoOutput(true);
                    if (jsonBody != null) {
                        byte[] outputInBytes = jsonBody.getBytes(StandardCharsets.UTF_8);
                        OutputStream os = conn.getOutputStream();
                        os.write(outputInBytes);
                        os.close();
                    }
                    int code = conn.getResponseCode();
                    InputStream is = (code >= 200 && code < 400) ? conn.getInputStream() : conn.getErrorStream();
                    String response = readStream(is);
                    boolean isHtml = response != null && (response.trim().startsWith("<!DOCTYPE") || response.trim().startsWith("<html") || response.trim().startsWith("<!doctype"));
                    if (code >= 200 && code < 400) {
                        if (isHtml) {
                            postError(callback, I18n.t("net.server_si_sta_ricollegando_riprova_tra"));
                        } else {
                            postSuccess(callback, response);
                        }
                    } else {
                        if (isHtml) {
                            postError(callback, I18n.t("net.server_non_momento_raggiungibile_http", code));
                        } else {
                            postError(callback, "HTTP " + code + ": " + errorDetail(response));
                        }
                    }
                } catch (final Exception e) {
                    postError(callback, e.getMessage() != null ? e.getMessage() : I18n.t("net.errore_connessione"));
                } finally {
                    if (conn != null) conn.disconnect();
                }
            }
        });
    }

    public static final int HOST_SAME = 0, HOST_OTHER = 1, HOST_UNREACHABLE = 2;

    public interface HostCallback {
        void onResult(int result);
    }

    /**
     * Chi risponde a baseUrl è il PC con questo identificativo pubblico (host_id di /api/status)? Senza token: su un
     * Wi-Fi che non è quello di casa all'indirizzo 192.168.x.y può esserci un altro dispositivo. HOST_UNREACHABLE solo
     * se non risponde nessuno; qualunque risposta diversa (pagina web, errore, identificativo diverso) è HOST_OTHER.
     */
    public static void checkHost(final android.content.Context ctx, final String baseUrl, final int timeoutMs, final HostCallback cb) {
        final android.content.Context app = ctx.getApplicationContext();
        executor.execute(new Runnable() {
            @Override
            public void run() {
                final int result = checkHostBlocking(app, baseUrl, timeoutMs);
                mainHandler.post(new Runnable() {
                    @Override
                    public void run() {
                        cb.onResult(result);
                    }
                });
            }
        });
    }

    /** Il PC ha già risposto almeno una volta con la prova segreta: da allora una risposta senza prova non vale. */
    static final String PREF_HOST_PROOF_SEEN = "pc_host_proof_seen";

    /**
     * Come checkHost, sul thread chiamante (per il widget, che lavora già in un thread suo). Oltre all'identificativo
     * (che si legge senza PIN, anche da internet) il PC deve rispondere a una domanda casuale con la prova calcolata dal
     * segreto ntfy: un impostore su un Wi-Fi esterno non la conosce, e il PC la dà solo a chi è nella sua rete locale.
     */
    public static int checkHostBlocking(android.content.Context ctx, String baseUrl, int timeoutMs) {
        android.content.SharedPreferences prefs = ctx.getSharedPreferences("MinecraftControllerPrefs", android.content.Context.MODE_PRIVATE);
        String expectedHostId = prefs.getString(MainActivity.PREF_PC_HOST_ID, "");
        String secret = prefs.getString("push_secret", "");
        String nonce = "";
        if (!secret.isEmpty()) {
            byte[] rnd = new byte[16];
            new java.security.SecureRandom().nextBytes(rnd);
            StringBuilder sb = new StringBuilder();
            for (byte b : rnd) sb.append(String.format("%02x", b));
            nonce = sb.toString();
        }
        HttpURLConnection conn = null;
        int code;
        String response;
        try {
            conn = (HttpURLConnection) new URL(baseUrl + "/api/status" + (nonce.isEmpty() ? "" : "?nonce=" + nonce)).openConnection();
            conn.setRequestMethod("GET");
            conn.setInstanceFollowRedirects(false);
            conn.setConnectTimeout(timeoutMs);
            conn.setReadTimeout(timeoutMs + 2000);
            code = conn.getResponseCode();
            response = readStream(code >= 200 && code < 400 ? conn.getInputStream() : conn.getErrorStream());
        } catch (Exception e) {
            return HOST_UNREACHABLE;
        } finally {
            if (conn != null) conn.disconnect();
        }
        if (expectedHostId == null || expectedHostId.isEmpty()) return HOST_SAME;   // PC mai riconosciuto: come prima
        String seen = "";
        String proof = "";
        try {
            if (code == 200) {
                org.json.JSONObject obj = new org.json.JSONObject(response);
                seen = obj.optString("host_id", "");
                proof = obj.optString("host_proof", "");
            }
        } catch (Exception ignored) {
        }
        if (!expectedHostId.equals(seen)) return HOST_OTHER;
        if (nonce.isEmpty()) return HOST_SAME;                         // segreto non ancora ricevuto (app vecchia)
        if (proof.isEmpty()) return prefs.getBoolean(PREF_HOST_PROOF_SEEN, false) ? HOST_OTHER : HOST_SAME;   // PC non aggiornato
        if (!proofMatches(secret, nonce, proof)) return HOST_OTHER;
        if (!prefs.getBoolean(PREF_HOST_PROOF_SEEN, false)) prefs.edit().putBoolean(PREF_HOST_PROOF_SEEN, true).apply();
        return HOST_SAME;
    }

    /** Prova del PC: HMAC-SHA256(chiave, "host-proof|" + nonce), chiave = HMAC-SHA256(segreto ntfy, "voxarium-host-proof-key"). */
    static boolean proofMatches(String secret, String nonce, String proof) {
        try {
            javax.crypto.Mac mac = javax.crypto.Mac.getInstance("HmacSHA256");
            mac.init(new javax.crypto.spec.SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            byte[] key = mac.doFinal("voxarium-host-proof-key".getBytes(StandardCharsets.UTF_8));
            mac.init(new javax.crypto.spec.SecretKeySpec(key, "HmacSHA256"));
            byte[] expected = mac.doFinal(("host-proof|" + nonce).getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder();
            for (byte b : expected) sb.append(String.format("%02x", b));
            return java.security.MessageDigest.isEqual(sb.toString().getBytes(StandardCharsets.UTF_8),
                proof.toLowerCase(java.util.Locale.ROOT).getBytes(StandardCharsets.UTF_8));
        } catch (Exception e) {
            return false;
        }
    }

    /** Risposta d'errore del PC ({"detail": "..."}): solo il messaggio invece di tutto il JSON. */
    private static String errorDetail(String response) {
        try {
            Object d = new org.json.JSONObject(response).opt("detail");
            if (d instanceof String) return (String) d;
        } catch (Exception ignored) {
        }
        return response;
    }

    private static String readStream(InputStream is) {
        if (is == null) return "";
        try {
            BufferedReader reader = new BufferedReader(new InputStreamReader(is, StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder();
            String line;
            while ((line = reader.readLine()) != null) {
                sb.append(line).append("\n");
            }
            reader.close();
            return sb.toString().trim();
        } catch (Exception e) {
            return "";
        }
    }

    private static void postSuccess(final ApiCallback callback, final String response) {
        if (callback != null) {
            mainHandler.post(new Runnable() {
                @Override
                public void run() {
                    callback.onSuccess(response);
                }
            });
        }
    }

    private static void postError(final ApiCallback callback, final String error) {
        if (callback != null) {
            mainHandler.post(new Runnable() {
                @Override
                public void run() {
                    callback.onError(error);
                }
            });
        }
    }
}
