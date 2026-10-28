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
    public static void checkHost(final String baseUrl, final String expectedHostId, final int timeoutMs, final HostCallback cb) {
        executor.execute(new Runnable() {
            @Override
            public void run() {
                final int result = checkHostBlocking(baseUrl, expectedHostId, timeoutMs);
                mainHandler.post(new Runnable() {
                    @Override
                    public void run() {
                        cb.onResult(result);
                    }
                });
            }
        });
    }

    /** Come checkHost, sul thread chiamante (per il widget, che lavora già in un thread suo). */
    public static int checkHostBlocking(String baseUrl, String expectedHostId, int timeoutMs) {
        HttpURLConnection conn = null;
        int code;
        String response;
        try {
            conn = (HttpURLConnection) new URL(baseUrl + "/api/status").openConnection();
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
        try {
            if (code == 200) seen = new org.json.JSONObject(response).optString("host_id", "");
        } catch (Exception ignored) {
        }
        return expectedHostId.equals(seen) ? HOST_SAME : HOST_OTHER;
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
