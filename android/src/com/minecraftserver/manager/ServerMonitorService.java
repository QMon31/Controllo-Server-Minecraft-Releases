package com.minecraftserver.manager;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.ConnectivityManager;
import android.net.Network;
import android.graphics.Color;
import android.os.Build;
import android.os.IBinder;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.os.VibratorManager;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class ServerMonitorService extends Service {

    public static final String ACTION_START = "com.minecraftserver.manager.START_MONITOR";
    public static final String ACTION_STOP = "com.minecraftserver.manager.STOP_MONITOR";
    public static final String ACTION_RESTART = "com.minecraftserver.manager.RESTART_MONITOR";
    public static final String PREF_PUSH_TOPIC = "push_topic";

    /**
     * Topic ntfy ricevuto dal PC al login. Vuoto finché il PC non ne ha inviato uno: in quel caso l'app non ascolta
     * nessun topic (un topic fisso nel codice farebbe ricevere a ogni installazione le notifiche di un altro PC).
     */
    private static long sNextNtfyPollAt = 0;

    /**
     * Al massimo una lettura "poll" di ntfy.sh al minuto per tutta l'app: prima ogni chiamata fallita al PC ne avviava
     * una, e decine di richieste al minuto dallo stesso IP fanno bloccare l'IP da ntfy.sh (anche per gli altri dispositivi di casa).
     */
    public static synchronized boolean tryAcquireNtfyPoll() {
        long now = System.currentTimeMillis();
        if (now < sNextNtfyPollAt) return false;
        sNextNtfyPollAt = now + 60000;
        return true;
    }

    public static String getPushTopic(Context ctx) {
        String t = ctx.getSharedPreferences("MinecraftControllerPrefs", Context.MODE_PRIVATE).getString(PREF_PUSH_TOPIC, "");
        return (t != null && t.matches("[A-Za-z0-9_-]{1,64}")) ? t : "";
    }

    private static final String CHANNEL_SERVICE = "mc_monitor_service";
    private static final String CHANNEL_CRASH = "mc_crash_alerts";
    static final String CHANNEL_UPDATES = "mc_update_alerts";
    private static final String CHANNEL_BACKUP = "mc_backup_alerts";
    private static final String CHANNEL_ALERTS = "mc_general_alerts";

    private static final int NOTIF_SERVICE_ID = 2001;
    private static final int NOTIF_CRASH_ID = 1001;
    private static final int NOTIF_UPDATE_ID = 1002;
    private static final int NOTIF_BACKUP_ID = 1003;
    private static final int NOTIF_GENERAL_BASE_ID = 3000;

    private Thread mSseThread;
    private Thread mUpdateThread;
    private volatile boolean mIsRunning = false;
    private volatile int mGeneration = 0;
    private volatile HttpURLConnection mStreamConn;
    // true mentre il thread aspetta prima di riprovare: solo allora il ritorno della rete lo sveglia
    private volatile boolean mWaitingToReconnect = false;
    private ConnectivityManager.NetworkCallback mNetCallback;

    @Override
    public void onCreate() {
        super.onCreate();
        I18n.ensure(this);
        PcTls.install(this);
        createChannels();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_RESTART.equals(intent.getAction())) {
            stopMonitoring(); // il topic è cambiato: chiude l'ascolto attuale e lo riapre qui sotto sul nuovo topic
        }
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            stopMonitoring();
            stopSelf();
            return START_NOT_STICKY;
        }

        startForegroundServiceNotification();
        startPushListener();
        return START_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onDestroy() {
        stopMonitoring();
        super.onDestroy();
    }

    @Override
    public void onTaskRemoved(Intent rootIntent) {
        // Alcuni produttori chiudono il processo quando l'app viene tolta dalle recenti: riprogramma l'avvio del servizio
        try {
            Intent restart = new Intent(getApplicationContext(), ServerMonitorService.class);
            restart.setAction(ACTION_START);
            PendingIntent pi = PendingIntent.getForegroundService(
                    getApplicationContext(), 77, restart,
                    PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
            android.app.AlarmManager am = (android.app.AlarmManager) getSystemService(Context.ALARM_SERVICE);
            if (am != null) {
                am.set(android.app.AlarmManager.ELAPSED_REALTIME_WAKEUP,
                        android.os.SystemClock.elapsedRealtime() + 2000, pi);
            }
        } catch (Exception ignored) {}
        super.onTaskRemoved(rootIntent);
    }

    private void createChannels() {
        ensureChannels(this);
    }

    /** Canali di notifica dell'app: unica definizione, usata dal servizio e dalla schermata principale. */
    static void ensureChannels(Context ctx) {
        I18n.ensure(ctx);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationManager nm = (NotificationManager) ctx.getSystemService(Context.NOTIFICATION_SERVICE);
            if (nm == null) return;
            // Canali delle versioni precedenti: uno mai usato, l'altro doppione di CHANNEL_UPDATES
            nm.deleteNotificationChannel("mc_critical_channel");
            nm.deleteNotificationChannel("mc_updates_channel");

            NotificationChannel chanService = new NotificationChannel(
                    CHANNEL_SERVICE,
                    I18n.t("notif.servizio_monitor_minecraft"),
                    NotificationManager.IMPORTANCE_MIN
            );
            chanService.setDescription(I18n.t("notif.canale_silente_background_monitoraggio"));
            chanService.setShowBadge(false);
            nm.createNotificationChannel(chanService);

            NotificationChannel chanCrash = new NotificationChannel(
                    CHANNEL_CRASH,
                    I18n.t("notif.crash_allarmi_critici"),
                    NotificationManager.IMPORTANCE_HIGH
            );
            chanCrash.setDescription(I18n.t("notif.notifiche_prioritarie_crash_o_arresti"));
            chanCrash.enableVibration(true);
            chanCrash.enableLights(true);
            chanCrash.setLightColor(Color.RED);
            nm.createNotificationChannel(chanCrash);

            NotificationChannel chanUpdate = new NotificationChannel(
                    CHANNEL_UPDATES,
                    I18n.t("notif.nuovi_aggiornamenti_server"),
                    NotificationManager.IMPORTANCE_DEFAULT
            );
            chanUpdate.setDescription(I18n.t("notif.avvisi_nuove_versioni_o_release_dell"));
            nm.createNotificationChannel(chanUpdate);

            NotificationChannel chanBackup = new NotificationChannel(
                    CHANNEL_BACKUP,
                    I18n.t("notif.backup_manutenzione"),
                    NotificationManager.IMPORTANCE_DEFAULT
            );
            chanBackup.setDescription(I18n.t("notif.notifiche_backup_completati_o"));
            nm.createNotificationChannel(chanBackup);

            NotificationChannel chanAlerts = new NotificationChannel(
                    CHANNEL_ALERTS,
                    I18n.t("notif.avvisi_generali_connessione"),
                    NotificationManager.IMPORTANCE_DEFAULT
            );
            chanAlerts.setDescription(I18n.t("notif.notifiche_generali_dal_server"));
            nm.createNotificationChannel(chanAlerts);
        }
    }

    private void startForegroundServiceNotification() {
        Intent notificationIntent = new Intent(this, MainActivity.class);
        PendingIntent pendingIntent = PendingIntent.getActivity(
                this,
                0,
                notificationIntent,
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT
        );

        Notification.Builder builder;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            builder = new Notification.Builder(this, CHANNEL_SERVICE);
        } else {
            builder = new Notification.Builder(this);
        }

        builder.setContentTitle("Voxarium")
                .setContentText(I18n.t("notif.monitoraggio_attivo_ricezione"))
                .setSmallIcon(R.drawable.ic_stat_voxarium)
                .setContentIntent(pendingIntent)
                .setOngoing(true);

        if (Build.VERSION.SDK_INT >= 34) {
            try {
                startForeground(NOTIF_SERVICE_ID, builder.build(), android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
            } catch (Throwable t) {
                startForeground(NOTIF_SERVICE_ID, builder.build());
            }
        } else {
            startForeground(NOTIF_SERVICE_ID, builder.build());
        }
    }

    private void startPushListener() {
        if (mIsRunning) return;
        mIsRunning = true;
        final int myGen = ++mGeneration;

        startPeriodicUpdateChecker();
        initialSyncFromNtfy();

        mSseThread = new Thread(new Runnable() {
            @Override
            public void run() {
                SharedPreferences prefs = getSharedPreferences("MinecraftControllerPrefs", MODE_PRIVATE);
                String lastId = prefs.getString("ntfy_last_msg_id", "");
                // Attesa crescente tra le riconnessioni (5 s → 5 min): riconnessioni troppo frequenti fanno bloccare l'IP da ntfy.sh
                long backoffMs = 5000;
                while (mIsRunning && myGen == mGeneration) {
                    long connectedAt = 0;
                    HttpURLConnection conn = null;
                    String topic = getPushTopic(ServerMonitorService.this);
                    if (topic.isEmpty()) {
                        // Nessun login ancora fatto sul PC: niente topic da ascoltare (al login arriva ACTION_RESTART)
                        try { Thread.sleep(15000); } catch (InterruptedException ignored) {}
                        continue;
                    }
                    try {
                        String since = lastId.isEmpty() ? "10m" : lastId;
                        URL url = new URL("https://ntfy.sh/" + topic + "/json?since=" + since);
                        conn = (HttpURLConnection) url.openConnection();
                        mStreamConn = conn;
                        conn.setRequestMethod("GET");
                        conn.setConnectTimeout(15000);
                        conn.setReadTimeout(120000); // 2 minuti di timeout per rilevare disconnessioni di rete
                        conn.setRequestProperty("Accept", "application/x-ndjson");

                        InputStream is = conn.getInputStream();
                        connectedAt = System.currentTimeMillis();
                        BufferedReader reader = new BufferedReader(new InputStreamReader(is, StandardCharsets.UTF_8));
                        String line;

                        while (mIsRunning && myGen == mGeneration && (line = reader.readLine()) != null) {
                            line = line.trim();
                            if (line.isEmpty()) continue;
                            try {
                                JSONObject msgObj = new JSONObject(line);
                                String event = msgObj.optString("event", "");
                                if ("message".equals(event)) {
                                    String msgId = msgObj.optString("id", "");
                                    if (!msgId.isEmpty()) {
                                        if (msgId.equals(lastId)) continue;
                                        lastId = msgId;
                                        prefs.edit().putString("ntfy_last_msg_id", msgId).apply();
                                    }

                                    // Extract and update connection addresses automatically
                                    extractAndUpdateServerUrls(msgObj);

                                    String title = msgObj.optString("title", I18n.t("notif.avviso_server_minecraft"));
                                    String message = msgObj.optString("message", "");

                                    boolean isNetworkSync = false;
                                    if (msgObj.has("tags")) {
                                        Object tObj = msgObj.opt("tags");
                                        if (tObj instanceof JSONArray) {
                                            JSONArray tArr = (JSONArray) tObj;
                                            for (int k = 0; k < tArr.length(); k++) {
                                                String item = tArr.optString(k, "");
                                                if ("network_sync".equalsIgnoreCase(item) || "silent".equalsIgnoreCase(item)) {
                                                    isNetworkSync = true;
                                                    break;
                                                }
                                            }
                                        } else if (tObj instanceof String) {
                                            String sTags = (String) tObj;
                                            if (sTags.contains("network_sync") || sTags.contains("silent")) {
                                                isNetworkSync = true;
                                            }
                                        }
                                    }

                                    String lowerTitle = title.toLowerCase();
                                    String lowerMsg = message.toLowerCase();
                                    if (lowerTitle.contains("sincronizzato") || lowerTitle.contains("indirizzo") || lowerTitle.contains("network_sync") ||
                                        lowerMsg.contains("nuovo indirizzo") || (lowerMsg.contains("sig=") && lowerMsg.contains("lan:"))) {
                                        isNetworkSync = true;
                                    }

                                    if (isNetworkSync) {
                                        // Sincronizzazione 100% invisibile e silenziosa in background: nessuna notifica a schermo
                                    } else if (!isAlertAuthentic(msgObj)) {
                                        // Avviso senza firma valida del proprio PC (o più vecchio di 24 ore): scartato
                                    } else if (lowerTitle.contains("backup") || lowerMsg.contains("backup")) {
                                        triggerBackupNotification(title, message);
                                    } else if (lowerTitle.contains("warning") || lowerTitle.contains("crash") || lowerTitle.contains("errore")) {
                                        triggerCrashNotification(title, message);
                                    } else if (lowerTitle.contains("aggiornamento") || lowerTitle.contains("update") || lowerTitle.contains("nuova versione")) {
                                        triggerUpdateNotification(title, message);
                                    } else {
                                        // Tutti gli altri messaggi (avvio/arresto server, giocatori, notifica di prova...) non vanno persi
                                        triggerGeneralAlertNotification(title, message);
                                    }
                                }
                            } catch (Exception ignored) {}
                        }
                    } catch (Exception ignored) {
                    } finally {
                        if (conn != null) conn.disconnect();
                    }
                    if (!(mIsRunning && myGen == mGeneration)) break;
                    // Connessione rimasta aperta a lungo = rete sana: si riparte dall'attesa minima
                    if (connectedAt > 0 && System.currentTimeMillis() - connectedAt > 60000) backoffMs = 5000;
                    mWaitingToReconnect = true;
                    try {
                        Thread.sleep(backoffMs);
                        backoffMs = Math.min(backoffMs * 2, 300000);
                    } catch (InterruptedException e) {
                        backoffMs = 5000; // rete tornata: si riprova subito, ripartendo dall'attesa minima
                    } finally {
                        mWaitingToReconnect = false;
                    }
                }
            }
        });
        mSseThread.start();
        registerNetworkCallback();
    }

    /**
     * Quando torna una rete (fine di un periodo senza segnale, Wi-Fi ritrovato) l'ascolto riparte subito invece di
     * aspettare la fine dell'attesa crescente, che dopo un'assenza lunga arriva a 5 minuti. Mentre la connessione è
     * attiva non succede nulla, quindi le richieste a ntfy.sh non aumentano.
     */
    private void registerNetworkCallback() {
        if (mNetCallback != null || Build.VERSION.SDK_INT < Build.VERSION_CODES.N) return;
        try {
            ConnectivityManager cm = (ConnectivityManager) getSystemService(Context.CONNECTIVITY_SERVICE);
            if (cm == null) return;
            mNetCallback = new ConnectivityManager.NetworkCallback() {
                @Override
                public void onAvailable(Network network) {
                    Thread t = mSseThread;
                    if (t != null && mWaitingToReconnect) t.interrupt();
                }
            };
            cm.registerDefaultNetworkCallback(mNetCallback);
        } catch (Exception e) {
            mNetCallback = null;
        }
    }

    private void unregisterNetworkCallback() {
        if (mNetCallback == null) return;
        try {
            ConnectivityManager cm = (ConnectivityManager) getSystemService(Context.CONNECTIVITY_SERVICE);
            if (cm != null) cm.unregisterNetworkCallback(mNetCallback);
        } catch (Exception ignored) {}
        mNetCallback = null;
    }

    private void initialSyncFromNtfy() {
        new Thread(new Runnable() {
            @Override
            public void run() {
                HttpURLConnection conn = null;
                String topic = getPushTopic(ServerMonitorService.this);
                if (topic.isEmpty() || !tryAcquireNtfyPoll()) return;
                try {
                    URL url = new URL("https://ntfy.sh/" + topic + "/json?poll=1&since=24h");
                    conn = (HttpURLConnection) url.openConnection();
                    conn.setRequestMethod("GET");
                    conn.setConnectTimeout(6000);
                    conn.setReadTimeout(6000);
                    if (conn.getResponseCode() == 200) {
                        BufferedReader reader = new BufferedReader(new InputStreamReader(conn.getInputStream(), StandardCharsets.UTF_8));
                        String line;
                        while ((line = reader.readLine()) != null) {
                            line = line.trim();
                            if (line.isEmpty()) continue;
                            try {
                                JSONObject msgObj = new JSONObject(line);
                                if ("message".equals(msgObj.optString("event", ""))) {
                                    extractAndUpdateServerUrls(msgObj);
                                }
                            } catch (Exception ignored) {}
                        }
                        reader.close();
                    }
                } catch (Exception ignored) {
                } finally {
                    if (conn != null) conn.disconnect();
                }
            }
        }).start();
    }

    private void extractAndUpdateServerUrls(JSONObject msgObj) {
        if (msgObj == null) return;
        String directLocal = "";
        String directRemote = "";
        String sig = "";

        String message = msgObj.optString("message", "");
        String title = msgObj.optString("title", "");
        String click = msgObj.optString("click", "");
        StringBuilder sb = new StringBuilder();
        if (!click.isEmpty()) sb.append(" ").append(click);
        if (!message.isEmpty()) sb.append(" ").append(message);
        if (!title.isEmpty()) sb.append(" ").append(title);
        JSONArray actions = msgObj.optJSONArray("actions");
        if (actions != null) {
            for (int i = 0; i < actions.length(); i++) {
                JSONObject act = actions.optJSONObject(i);
                if (act != null && act.has("url")) sb.append(" ").append(act.optString("url", ""));
            }
        }
        String allText = sb.toString();

        Matcher mRem = PcTls.REMOTE_URL_PATTERN.matcher(allText);
        if (mRem.find()) directRemote = mRem.group().replaceAll("/+$", "");
        Matcher mLoc = Pattern.compile("http://(?:192\\.168|10\\.|172\\.(?:1[6-9]|2[0-9]|3[01]))\\.[0-9.]+:[0-9]+").matcher(allText);
        if (mLoc.find()) directLocal = mLoc.group().replaceAll("/+$", "");
        Matcher mSig = Pattern.compile("sig=([0-9a-fA-F]{64})").matcher(allText);
        if (mSig.find()) sig = mSig.group(1);
        String fp = "";
        Matcher mFp = Pattern.compile("fp=([0-9a-fA-F]{0,64})").matcher(allText);
        if (mFp.find()) fp = mFp.group(1);

        SharedPreferences prefs = getSharedPreferences("MinecraftControllerPrefs", MODE_PRIVATE);
        String secret = prefs.getString("push_secret", "");

        // Senza il segreto ricevuto dal PC (login non ancora fatto) gli indirizzi si aggiornano solo via LAN (/api/network).
        // Messaggi senza firma, con firma errata o più vecchi dell'ultimo accettato: scartati
        if (!acceptAddressMessage(this, secret, directRemote, directLocal, fp, sig, allText)) {
            return;
        }
        PcTls.saveFingerprint(this, fp); // impronta accettata solo con firma valida

        String foundRemote = null;
        if (!directRemote.isEmpty() && PcTls.REMOTE_URL_PATTERN.matcher(directRemote).matches()) {
            foundRemote = directRemote.replaceAll("/+$", "");
        }

        String foundLocal = null;
        if (!directLocal.isEmpty()) {
            foundLocal = directLocal.replaceAll("/+$", "");
        }

        boolean changed = false;
        Intent broadcastIntent = new Intent("com.minecraftserver.manager.URL_UPDATED");

        if (foundRemote != null && !foundRemote.isEmpty()) {
            String currentRemote = prefs.getString("remote_url", "");
            if (!foundRemote.equalsIgnoreCase(currentRemote)) {
                prefs.edit().putString("remote_url", foundRemote).apply();
                broadcastIntent.putExtra("remote_url", foundRemote);
                changed = true;
            }
        }

        if (foundLocal != null && !foundLocal.isEmpty()) {
            String currentLocal = prefs.getString("local_url", "");
            if (!foundLocal.equalsIgnoreCase(currentLocal)) {
                prefs.edit().putString("local_url", foundLocal).apply();
                broadcastIntent.putExtra("local_url", foundLocal);
                changed = true;
            }
        }

        if (changed) {
            broadcastIntent.setPackage(getPackageName());
            sendBroadcast(broadcastIntent);
        }
    }

    private void stopMonitoring() {
        mIsRunning = false;
        mGeneration++;
        unregisterNetworkCallback();
        final HttpURLConnection c = mStreamConn;
        mStreamConn = null;
        if (c != null) {
            // disconnect() sblocca readLine(); fatto su un thread separato per non fare rete sul thread principale
            new Thread(new Runnable() {
                @Override
                public void run() {
                    try { c.disconnect(); } catch (Exception ignored) {}
                }
            }).start();
        }
        if (mSseThread != null) {
            mSseThread.interrupt();
            mSseThread = null;
        }
        if (mUpdateThread != null) {
            mUpdateThread.interrupt();
            mUpdateThread = null;
        }
    }

    /**
     * Gli avvisi del PC portano i tag "ts-<secondi>" e "sig-<HMAC-SHA256(push_secret, titolo\nmessaggio\nts)>":
     * chi conosce solo il nome del topic non può mandare avvisi falsi, e uno vecchio non può essere rimandato.
     */
    private boolean isAlertAuthentic(JSONObject msgObj) {
        String secret = getSharedPreferences("MinecraftControllerPrefs", MODE_PRIVATE).getString("push_secret", "");
        JSONArray tags = msgObj.optJSONArray("tags");
        if (secret.isEmpty() || tags == null) return false;
        String ts = "", sig = "";
        for (int i = 0; i < tags.length(); i++) {
            String t = tags.optString(i, "");
            if (t.startsWith("ts-")) ts = t.substring(3);
            else if (t.startsWith("sig-")) sig = t.substring(4).toLowerCase(java.util.Locale.ROOT);
        }
        if (!ts.matches("\\d{1,12}") || !sig.matches("[0-9a-f]{64}")) return false;
        if (Math.abs(System.currentTimeMillis() / 1000L - Long.parseLong(ts)) > 24L * 3600L) return false;
        String expected = hmacSha256Hex(secret, msgObj.optString("title", "") + "\n" + msgObj.optString("message", "") + "\n" + ts);
        return java.security.MessageDigest.isEqual(sig.getBytes(StandardCharsets.UTF_8), expected.getBytes(StandardCharsets.UTF_8));
    }

    /** Ora (secondi, orologio del PC) dell'ultimo messaggio con gli indirizzi accettato. */
    static final String PREF_URL_MSG_TS = "ntfy_url_msg_ts";

    /**
     * Messaggio del PC con indirizzi e impronta del certificato: firmato con push_secret e non più vecchio dell'ultimo
     * accettato. Dalla 4.21.1 il PC aggiunge "ts=<secondi> | sig2=HMAC(url|lan|fp|ts)": chi conosce il nome del topic
     * non può rimandare un messaggio vecchio (con un IP pubblico che intanto può essere passato a qualcun altro).
     * Il formato senza ora (sig) vale solo finché dal PC non è arrivato un messaggio col nuovo formato.
     */
    static synchronized boolean acceptAddressMessage(Context ctx, String secret, String remote, String local, String fp,
                                                     String sig, String allText) {
        if (secret == null || secret.isEmpty() || sig == null || sig.isEmpty()) return false;
        String signed = remote + "|" + local + "|" + fp;
        SharedPreferences prefs = ctx.getSharedPreferences("MinecraftControllerPrefs", MODE_PRIVATE);
        long lastTs = prefs.getLong(PREF_URL_MSG_TS, 0L);
        Matcher mTs = Pattern.compile("(?:^|\\s)ts=(\\d{1,12})\\b").matcher(allText);
        Matcher mSig2 = Pattern.compile("sig2=([0-9a-fA-F]{64})").matcher(allText);
        if (mTs.find() && mSig2.find()) {
            long ts = Long.parseLong(mTs.group(1));
            String expected = hmacSha256Hex(secret, signed + "|" + ts);
            String got = mSig2.group(1).toLowerCase(java.util.Locale.ROOT);
            if (!java.security.MessageDigest.isEqual(got.getBytes(StandardCharsets.UTF_8), expected.getBytes(StandardCharsets.UTF_8))) return false;
            if (ts < lastTs || ts > System.currentTimeMillis() / 1000L + 24L * 3600L) return false;
            if (ts > lastTs) prefs.edit().putLong(PREF_URL_MSG_TS, ts).apply();
            return true;
        }
        if (lastTs > 0) return false;   // il PC manda già l'ora: un messaggio senza è vecchio o rimandato
        String expected = hmacSha256Hex(secret, signed);
        return java.security.MessageDigest.isEqual(sig.toLowerCase(java.util.Locale.ROOT).getBytes(StandardCharsets.UTF_8),
                expected.getBytes(StandardCharsets.UTF_8));
    }

    private static String hmacSha256Hex(String key, String data) {
        try {
            javax.crypto.Mac mac = javax.crypto.Mac.getInstance("HmacSHA256");
            mac.init(new javax.crypto.spec.SecretKeySpec(key.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            byte[] h = mac.doFinal(data.getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder();
            for (byte b : h) sb.append(String.format("%02x", b));
            return sb.toString();
        } catch (Exception e) {
            return "";
        }
    }

    private void triggerCrashNotification(String title, String message) {
        try {
            Intent notificationIntent = new Intent(this, MainActivity.class);
            notificationIntent.putExtra("navigate_to", "console");
            PendingIntent pendingIntent = PendingIntent.getActivity(
                    this,
                    1,
                    notificationIntent,
                    PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT
            );

            Notification.Builder builder;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                builder = new Notification.Builder(this, CHANNEL_CRASH);
            } else {
                builder = new Notification.Builder(this);
            }

            builder.setContentTitle(I18n.fromPc(title))
                    .setContentText(I18n.fromPc(message))
                    .setStyle(new Notification.BigTextStyle().bigText(I18n.fromPc(message)))
                    .setSmallIcon(R.drawable.ic_stat_voxarium)
                    .setContentIntent(pendingIntent)
                    .setAutoCancel(true)
                    .setPriority(Notification.PRIORITY_MAX)
                    .setCategory(Notification.CATEGORY_ERROR);

            NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
            if (nm != null) {
                nm.notify(NOTIF_CRASH_ID, builder.build());
            }
            vibrateAlert();
        } catch (Exception ignored) {}
    }

    private void triggerUpdateNotification(String title, String message) {
        if (!Flavor.SELF_UPDATE) return;
        try {
            Intent notificationIntent = new Intent(this, MainActivity.class);
            notificationIntent.putExtra("navigate_to", "settings");
            PendingIntent pendingIntent = PendingIntent.getActivity(
                    this,
                    2,
                    notificationIntent,
                    PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT
            );

            Notification.Builder builder;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                builder = new Notification.Builder(this, CHANNEL_UPDATES);
            } else {
                builder = new Notification.Builder(this);
            }

            builder.setContentTitle(I18n.fromPc(title))
                    .setContentText(I18n.fromPc(message))
                    .setStyle(new Notification.BigTextStyle().bigText(I18n.fromPc(message)))
                    .setSmallIcon(R.drawable.ic_stat_voxarium)
                    .setContentIntent(pendingIntent)
                    .setAutoCancel(true);

            NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
            if (nm != null) {
                nm.notify(NOTIF_UPDATE_ID, builder.build());
            }
            vibrateAlertShort();
        } catch (Exception ignored) {}
    }

    private void triggerBackupNotification(String title, String message) {
        try {
            Intent notificationIntent = new Intent(this, MainActivity.class);
            notificationIntent.putExtra("navigate_to", "dashboard");
            PendingIntent pendingIntent = PendingIntent.getActivity(
                    this,
                    3,
                    notificationIntent,
                    PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT
            );

            Notification.Builder builder;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                builder = new Notification.Builder(this, CHANNEL_BACKUP);
            } else {
                builder = new Notification.Builder(this);
            }

            builder.setContentTitle(I18n.fromPc(title))
                    .setContentText(I18n.fromPc(message))
                    .setStyle(new Notification.BigTextStyle().bigText(I18n.fromPc(message)))
                    .setSmallIcon(R.drawable.ic_stat_voxarium)
                    .setContentIntent(pendingIntent)
                    .setAutoCancel(true);

            NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
            if (nm != null) {
                nm.notify(NOTIF_BACKUP_ID, builder.build());
            }
            vibrateAlertShort();
        } catch (Exception ignored) {}
    }

    private void triggerGeneralAlertNotification(String title, String message) {
        try {
            Intent notificationIntent = new Intent(this, MainActivity.class);
            PendingIntent pendingIntent = PendingIntent.getActivity(
                    this,
                    4,
                    notificationIntent,
                    PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT
            );

            Notification.Builder builder;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                builder = new Notification.Builder(this, CHANNEL_ALERTS);
            } else {
                builder = new Notification.Builder(this);
            }

            builder.setContentTitle(I18n.fromPc(title))
                    .setContentText(I18n.fromPc(message))
                    .setStyle(new Notification.BigTextStyle().bigText(I18n.fromPc(message)))
                    .setSmallIcon(R.drawable.ic_stat_voxarium)
                    .setContentIntent(pendingIntent)
                    .setAutoCancel(true);

            NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
            if (nm != null) {
                int id = NOTIF_GENERAL_BASE_ID + (int) (System.currentTimeMillis() % 1000);
                nm.notify(id, builder.build());
            }
        } catch (Exception ignored) {}
    }

    private void vibrateAlertShort() {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                VibratorManager vm = (VibratorManager) getSystemService(Context.VIBRATOR_MANAGER_SERVICE);
                if (vm != null) vm.getDefaultVibrator().vibrate(VibrationEffect.createOneShot(120, VibrationEffect.DEFAULT_AMPLITUDE));
            } else {
                Vibrator v = (Vibrator) getSystemService(Context.VIBRATOR_SERVICE);
                if (v != null) v.vibrate(120);
            }
        } catch (Exception ignored) {}
    }

    private void vibrateAlert() {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                VibratorManager vm = (VibratorManager) getSystemService(Context.VIBRATOR_MANAGER_SERVICE);
                if (vm != null) vm.getDefaultVibrator().vibrate(VibrationEffect.createWaveform(new long[]{0, 200, 100, 300}, -1));
            } else {
                Vibrator v = (Vibrator) getSystemService(Context.VIBRATOR_SERVICE);
                if (v != null) v.vibrate(new long[]{0, 200, 100, 300}, -1);
            }
        } catch (Exception ignored) {}
    }

    private void startPeriodicUpdateChecker() {
        if (!Flavor.SELF_UPDATE) return;
        if (mUpdateThread != null && mUpdateThread.isAlive()) return;
        mUpdateThread = new Thread(new Runnable() {
            @Override
            public void run() {
                while (mIsRunning) {
                    try {
                        Thread.sleep(600000); // Controlla aggiornamenti ogni 10 minuti
                    } catch (InterruptedException e) {
                        break;
                    }
                    if (!mIsRunning) break;
                    checkUpdateViaHttp();
                }
            }
        });
        mUpdateThread.start();
    }

    private void checkUpdateViaHttp() {
        try {
            SharedPreferences prefs = getSharedPreferences("MinecraftControllerPrefs", MODE_PRIVATE);
            String localUrl = prefs.getString("local_url", "").replaceAll("/+$", "");
            String remoteUrl = prefs.getString("remote_url", "").replaceAll("/+$", "");
            int currentCode = prefs.getInt("app_version_code", 81);
            int lastNotified = prefs.getInt("last_notified_update_code", 0);

            String[] candidates = new String[]{localUrl, remoteUrl};
            for (String base : candidates) {
                if (base.isEmpty()) continue;
                HttpURLConnection conn = null;
                try {
                    URL url = new URL(base + "/static/version.json");
                    conn = (HttpURLConnection) url.openConnection();
                    conn.setRequestMethod("GET");
                    conn.setConnectTimeout(4000);
                    conn.setReadTimeout(4000);
                    if (conn.getResponseCode() == 200) {
                        BufferedReader r = new BufferedReader(new InputStreamReader(conn.getInputStream(), StandardCharsets.UTF_8));
                        StringBuilder sb = new StringBuilder();
                        String l;
                        while ((l = r.readLine()) != null) sb.append(l);
                        r.close();
                        JSONObject obj = new JSONObject(sb.toString());
                        int serverCode = obj.optInt("version_code", 0);
                        String vName = obj.optString("version_name", "nuova");
                        String notes = obj.optString("release_notes", I18n.t("app.miglioramenti_generali"));
                        if (serverCode > currentCode && serverCode != lastNotified) {
                            prefs.edit().putInt("last_notified_update_code", serverCode).apply();
                            String vClean = (vName != null && vName.startsWith("v")) ? vName : ("v" + vName);
                            triggerUpdateNotification(I18n.t("app.aggiornamento_disponibile", vClean), notes);
                        }
                        conn.disconnect();
                        break;
                    }
                } catch (Exception ignored) {
                } finally {
                    if (conn != null) conn.disconnect();
                }
            }
        } catch (Exception ignored) {}
    }
}
