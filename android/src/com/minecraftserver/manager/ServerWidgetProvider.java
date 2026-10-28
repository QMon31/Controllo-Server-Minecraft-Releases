package com.minecraftserver.manager;

import android.app.PendingIntent;
import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.os.Build;
import android.widget.RemoteViews;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

public class ServerWidgetProvider extends AppWidgetProvider {

    public static final String ACTION_WIDGET_START = "com.minecraftserver.manager.WIDGET_START";
    public static final String ACTION_WIDGET_RESTART = "com.minecraftserver.manager.WIDGET_RESTART";
    public static final String ACTION_WIDGET_STOP = "com.minecraftserver.manager.WIDGET_STOP";
    public static final String ACTION_WIDGET_UPDATE = "com.minecraftserver.manager.WIDGET_UPDATE_STATE";

    private static final String PREFS_NAME = "MinecraftControllerPrefs";
    private static final String PREF_LOCAL_URL = "local_url";
    private static final String PREF_REMOTE_URL = "remote_url";
    private static final String PREF_CONN_MODE = "conn_mode";
    private static final String PREF_AUTH_TOKEN = "auth_token";
    private static final String DEFAULT_LOCAL_URL = "";

    @Override
    public void onUpdate(Context context, AppWidgetManager appWidgetManager, int[] appWidgetIds) {
        for (int widgetId : appWidgetIds) {
            updateAppWidget(context, appWidgetManager, widgetId);
        }
        fetchServerStatusAsync(context);
    }

    @Override
    public void onReceive(Context context, Intent intent) {
        PcTls.install(context);
        super.onReceive(context, intent);
        String action = intent.getAction();
        if (action == null) return;

        // Avvia / Riavvia / Ferma arrivano a WidgetActionReceiver (non esportato): questo ricevitore lo deve essere per
        // il sistema dei widget, quindi qui non si accetta nessun comando verso il server
        if (ACTION_WIDGET_UPDATE.equals(action)) {
            AppWidgetManager appWidgetManager = AppWidgetManager.getInstance(context);
            ComponentName thisWidget = new ComponentName(context, ServerWidgetProvider.class);
            int[] allWidgetIds = appWidgetManager.getAppWidgetIds(thisWidget);
            for (int widgetId : allWidgetIds) {
                updateAppWidget(context, appWidgetManager, widgetId);
            }
        }
    }

    public static void updateAppWidget(Context context, AppWidgetManager appWidgetManager, int appWidgetId) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_server_control);
        // Il widget non passa dalle chiavi del layout (android:tag): i testi si scrivono qui
        I18n.ensure(context);
        views.setTextViewText(R.id.widget_title, I18n.t("app.server_minecraft"));
        views.setTextViewText(R.id.widget_players, I18n.t("widget.giocatori"));
        views.setTextViewText(R.id.widget_btn_start, I18n.t("ui.avvia"));
        views.setTextViewText(R.id.widget_btn_restart, I18n.t("ui.riavvia"));
        views.setTextViewText(R.id.widget_btn_stop, I18n.t("widget.ferma"));

        // Click on widget body opens MainActivity
        Intent openAppIntent = new Intent(context, MainActivity.class);
        openAppIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent piOpen = getPendingIntent(context, 100, openAppIntent);
        views.setOnClickPendingIntent(R.id.widget_title, piOpen);
        views.setOnClickPendingIntent(R.id.widget_icon, piOpen);

        // Start Button Intent
        Intent startIntent = new Intent(context, WidgetActionReceiver.class);
        startIntent.setAction(ACTION_WIDGET_START);
        views.setOnClickPendingIntent(R.id.widget_btn_start, getBroadcastIntent(context, 101, startIntent));

        // Restart Button Intent
        Intent restartIntent = new Intent(context, WidgetActionReceiver.class);
        restartIntent.setAction(ACTION_WIDGET_RESTART);
        views.setOnClickPendingIntent(R.id.widget_btn_restart, getBroadcastIntent(context, 102, restartIntent));

        // Stop Button Intent
        Intent stopIntent = new Intent(context, WidgetActionReceiver.class);
        stopIntent.setAction(ACTION_WIDGET_STOP);
        views.setOnClickPendingIntent(R.id.widget_btn_stop, getBroadcastIntent(context, 103, stopIntent));

        appWidgetManager.updateAppWidget(appWidgetId, views);
    }

    private static PendingIntent getPendingIntent(Context context, int requestCode, Intent intent) {
        int flags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            flags |= PendingIntent.FLAG_IMMUTABLE;
        }
        return PendingIntent.getActivity(context, requestCode, intent, flags);
    }

    private static PendingIntent getBroadcastIntent(Context context, int requestCode, Intent intent) {
        int flags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            flags |= PendingIntent.FLAG_IMMUTABLE;
        }
        return PendingIntent.getBroadcast(context, requestCode, intent, flags);
    }

    /**
     * Indirizzo da usare (sempre da un thread in background). Sull'indirizzo di casa (in chiaro) prima si controlla che
     * risponda il nostro PC: su un Wi-Fi esterno allo stesso indirizzo può esserci un altro dispositivo, che riceverebbe
     * token e PIN. In quel caso si passa all'indirizzo da internet, oppure ("Solo Wi-Fi") non si manda niente.
     */
    private static String getBaseUrl(Context context) {
        SharedPreferences prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
        String base = chooseBaseUrl(context, prefs);
        String local = prefs.getString(PREF_LOCAL_URL, DEFAULT_LOCAL_URL).replaceAll("/+$", "");
        if (base.isEmpty() || !base.equals(local) || base.startsWith("https://")) return base;
        String hostId = prefs.getString(MainActivity.PREF_PC_HOST_ID, "");
        if (ApiClient.checkHostBlocking(base, hostId, 1500) != ApiClient.HOST_OTHER) return base;
        String remote = prefs.getString(PREF_REMOTE_URL, "").replaceAll("/+$", "");
        return prefs.getInt(PREF_CONN_MODE, 0) != 1 && !remote.isEmpty() ? remote : "";
    }

    private static String chooseBaseUrl(Context context, SharedPreferences prefs) {
        int mode = prefs.getInt(PREF_CONN_MODE, 0);
        String local = prefs.getString(PREF_LOCAL_URL, DEFAULT_LOCAL_URL).replaceAll("/+$", "");
        String remote = prefs.getString(PREF_REMOTE_URL, "").replaceAll("/+$", "");
        if (mode == 2 && !remote.isEmpty()) return remote;
        if (mode == 1 || remote.isEmpty()) return local;

        boolean isUsingRemote = prefs.getBoolean("is_using_remote", false);
        try {
            android.net.ConnectivityManager cm = (android.net.ConnectivityManager) context.getSystemService(Context.CONNECTIVITY_SERVICE);
            if (cm != null) {
                android.net.Network net = cm.getActiveNetwork();
                if (net != null) {
                    android.net.NetworkCapabilities caps = cm.getNetworkCapabilities(net);
                    if (caps != null && caps.hasTransport(android.net.NetworkCapabilities.TRANSPORT_CELLULAR) && !caps.hasTransport(android.net.NetworkCapabilities.TRANSPORT_WIFI)) {
                        isUsingRemote = true;
                    }
                }
            }
        } catch (Exception ignored) {}
        return (isUsingRemote && !remote.isEmpty()) ? remote : local;
    }

    private static String getOrFetchAuthToken(Context context, String baseUrl) {
        SharedPreferences prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
        String token = prefs.getString(PREF_AUTH_TOKEN, "");
        if (!token.isEmpty()) return token;

        String pin = prefs.getString("admin_pin", "");
        if (pin.isEmpty() || baseUrl == null || baseUrl.isEmpty()) return "";
        // PIN già rifiutato dal PC (cambiato sul PC): ritentarlo farebbe bloccare il telefono per 10 minuti
        if (pin.equals(prefs.getString(MainActivity.PREF_ADMIN_PIN_REJECTED, ""))) return "";
        try {
            URL url = new URL(baseUrl + "/api/auth/verify");
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("POST");
            conn.setConnectTimeout(4000);
            conn.setReadTimeout(4000);
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setDoOutput(true);
            String body = "{\"pin\":\"" + pin.replace("\\", "\\\\").replace("\"", "\\\"") + "\"}";
            OutputStream os = conn.getOutputStream();
            os.write(body.getBytes(StandardCharsets.UTF_8));
            os.close();
            int code = conn.getResponseCode();
            if (code == 403) {
                // PIN sbagliato: lo si ricorda come rifiutato. Non con "PIN predefinito" (il PC rifiuta solo l'accesso
                // da internet con 1234): prima il widget lo segnava come sbagliato e non entrava più nemmeno in casa
                String detail = "";
                try {
                    InputStream es = conn.getErrorStream();
                    if (es != null) {
                        BufferedReader er = new BufferedReader(new InputStreamReader(es, StandardCharsets.UTF_8));
                        StringBuilder eb = new StringBuilder();
                        String l;
                        while ((l = er.readLine()) != null) eb.append(l);
                        er.close();
                        detail = eb.toString();
                    }
                } catch (Exception ignored) {}
                if (!detail.contains("PIN predefinito")) {
                    prefs.edit().putString(MainActivity.PREF_ADMIN_PIN_REJECTED, pin).apply();
                }
            }
            if (code == 200) {
                InputStream is = conn.getInputStream();
                BufferedReader reader = new BufferedReader(new InputStreamReader(is, StandardCharsets.UTF_8));
                StringBuilder sb = new StringBuilder();
                String line;
                while ((line = reader.readLine()) != null) sb.append(line);
                reader.close();
                JSONObject json = new JSONObject(sb.toString());
                token = json.optString("token", "");
                if (!token.isEmpty()) {
                    prefs.edit().putString(PREF_AUTH_TOKEN, token).apply();
                }
            }
            conn.disconnect();
        } catch (Exception ignored) {}
        return token;
    }

    private static void fetchServerStatusAsync(final Context context) {
        new Thread(new Runnable() {
            @Override
            public void run() {
                fetchServerStatus(context);
            }
        }).start();
    }

    /** Avviso dal PC (server acceso, spento, crash...): il widget rilegge subito lo stato, se è sulla schermata Home. */
    static void refreshStatusAsync(Context context) {
        int[] ids = AppWidgetManager.getInstance(context).getAppWidgetIds(new ComponentName(context, ServerWidgetProvider.class));
        if (ids != null && ids.length > 0) fetchServerStatusAsync(context);
    }

    /** Legge lo stato dal PC e aggiorna il widget (da un thread in background). Restituisce lo stato, o null se non risponde. */
    private static String fetchServerStatus(final Context context) {
        String state = null;
        try {
            String baseUrl = getBaseUrl(context);
            if (baseUrl.isEmpty()) return null;
            URL url = new URL(baseUrl + "/api/status");
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("GET");
            conn.setConnectTimeout(4000);
            conn.setReadTimeout(4000);

            String token = getOrFetchAuthToken(context, baseUrl);
            if (token != null && !token.isEmpty()) {
                conn.setRequestProperty("X-Admin-Token", token);
            }

            int respCode = conn.getResponseCode();
            if (respCode == 401 || respCode == 403) {
                // Invalida il token scaduto e riprova
                context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE).edit().remove(PREF_AUTH_TOKEN).apply();
            } else if (respCode == 200) {
                InputStream is = conn.getInputStream();
                BufferedReader reader = new BufferedReader(new InputStreamReader(is, StandardCharsets.UTF_8));
                StringBuilder sb = new StringBuilder();
                String line;
                while ((line = reader.readLine()) != null) sb.append(line);
                reader.close();

                JSONObject json = new JSONObject(sb.toString());
                state = json.optString("state", "OFFLINE");
                JSONObject playersObj = json.optJSONObject("players");
                int playersOnline = 0;
                int maxPlayers = 20;
                if (playersObj != null) {
                    playersOnline = playersObj.optInt("count", 0);
                    maxPlayers = playersObj.optInt("max", 20);
                } else {
                    playersOnline = json.optInt("players_online", 0);
                    maxPlayers = json.optInt("players_max", 20);
                }

                JSONObject metrics = json.optJSONObject("metrics");
                double ramGb = 0.0;
                if (metrics != null) {
                    ramGb = metrics.optDouble("mc_ram_gb", 0.0);
                }
                double tps = json.optDouble("tps", 20.0);

                updateWidgetUI(context, state, playersOnline, maxPlayers, ramGb, tps);
            }
            conn.disconnect();
        } catch (Exception ignored) {}
        return state;
    }

    private static boolean isTransitionState(String state) {
        return "STARTING".equalsIgnoreCase(state) || "STOPPING".equalsIgnoreCase(state) || "RESTARTING".equalsIgnoreCase(state);
    }

    static void sendServerActionAsync(final Context context, final String endpoint, final String method, final String jsonBody) {
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    String baseUrl = getBaseUrl(context);
                    if (baseUrl.isEmpty()) return;
                    String token = getOrFetchAuthToken(context, baseUrl);

                    URL url = new URL(baseUrl + endpoint);
                    HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                    conn.setRequestMethod(method);
                    conn.setConnectTimeout(5000);
                    conn.setReadTimeout(5000);
                    conn.setRequestProperty("Content-Type", "application/json");

                    if (token != null && !token.isEmpty()) {
                        conn.setRequestProperty("X-Admin-Token", token);
                    }

                    if (jsonBody != null && !jsonBody.isEmpty()) {
                        conn.setDoOutput(true);
                        OutputStream os = conn.getOutputStream();
                        os.write(jsonBody.getBytes(StandardCharsets.UTF_8));
                        os.close();
                    }

                    int respCode = conn.getResponseCode();
                    conn.disconnect();

                    if (respCode == 401 || respCode == 403) {
                        // Token non valido: rigenera e riprova una volta
                        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE).edit().remove(PREF_AUTH_TOKEN).apply();
                        String newToken = getOrFetchAuthToken(context, baseUrl);
                        HttpURLConnection retryConn = (HttpURLConnection) new URL(baseUrl + endpoint).openConnection();
                        retryConn.setRequestMethod(method);
                        retryConn.setConnectTimeout(5000);
                        retryConn.setReadTimeout(5000);
                        retryConn.setRequestProperty("Content-Type", "application/json");
                        if (newToken != null && !newToken.isEmpty()) {
                            retryConn.setRequestProperty("X-Admin-Token", newToken);
                        }
                        if (jsonBody != null && !jsonBody.isEmpty()) {
                            retryConn.setDoOutput(true);
                            OutputStream os2 = retryConn.getOutputStream();
                            os2.write(jsonBody.getBytes(StandardCharsets.UTF_8));
                            os2.close();
                        }
                        retryConn.getResponseCode();
                        retryConn.disconnect();
                    }

                    // Il server impiega da qualche secondo a qualche minuto ad accendersi o spegnersi: lo stato si rilegge
                    // ogni 3 s finché non è definitivo (per al massimo 3 minuti), così il widget non resta su STARTING/STOPPING.
                    // Nei primi 10 s uno stato definitivo può essere ancora quello di prima del comando: si continua.
                    long started = System.currentTimeMillis();
                    boolean seenTransition = false;
                    while (System.currentTimeMillis() - started < 180000L) {
                        Thread.sleep(3000);
                        String state = fetchServerStatus(context);
                        if (isTransitionState(state)) {
                            seenTransition = true;
                        } else if (state != null && (seenTransition || System.currentTimeMillis() - started > 10000L)) {
                            break;
                        }
                    }
                } catch (Exception ignored) {}
            }
        }).start();
    }

    private static void updateWidgetUI(Context context, String state, int playersOnline, int maxPlayers, double ramGb, double tps) {
        AppWidgetManager appWidgetManager = AppWidgetManager.getInstance(context);
        ComponentName thisWidget = new ComponentName(context, ServerWidgetProvider.class);
        int[] allWidgetIds = appWidgetManager.getAppWidgetIds(thisWidget);
        if (allWidgetIds == null || allWidgetIds.length == 0) return;

        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_server_control);
        // Il widget non passa dal controllo delle schermate: i pulsanti si traducono qui
        I18n.ensure(context);
        views.setTextViewText(R.id.widget_btn_start, I18n.t("ui.avvia"));
        views.setTextViewText(R.id.widget_btn_restart, I18n.t("ui.riavvia"));
        views.setTextViewText(R.id.widget_btn_stop, I18n.t("widget.ferma"));

        I18n.ensure(context);
        views.setTextViewText(R.id.widget_status_pill, I18n.fromPc(state));
        if ("ONLINE".equalsIgnoreCase(state)) {
            views.setTextColor(R.id.widget_status_pill, Color.parseColor("#a5d36f"));
        } else if ("CRASHED".equalsIgnoreCase(state)) {
            views.setTextColor(R.id.widget_status_pill, Color.parseColor("#f0788c"));
        } else if ("STARTING".equalsIgnoreCase(state) || "STOPPING".equalsIgnoreCase(state) || "RESTARTING".equalsIgnoreCase(state)) {
            views.setTextColor(R.id.widget_status_pill, Color.parseColor("#e3b35a"));
        } else {
            views.setTextColor(R.id.widget_status_pill, Color.parseColor("#9a9ca2"));
        }

        views.setTextViewText(R.id.widget_players, "" + playersOnline + "/" + maxPlayers + " (" + String.format("%.1f", tps) + " TPS)");
        views.setTextViewText(R.id.widget_ram, "" + String.format("%.1f", ramGb) + " GB");

        for (int widgetId : allWidgetIds) {
            appWidgetManager.partiallyUpdateAppWidget(widgetId, views);
        }
    }
}
