package com.minecraftserver.manager;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.drawable.ColorDrawable;
import android.hardware.biometrics.BiometricPrompt;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.NetworkRequest;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.CancellationSignal;
import android.os.Handler;
import android.os.Looper;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.view.KeyEvent;
import android.view.LayoutInflater;
import android.view.inputmethod.EditorInfo;
import android.view.View;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.RelativeLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.File;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class MainActivity extends Activity implements View.OnClickListener {

    public static final int CURRENT_VERSION_CODE = 110;
    public static final String CURRENT_VERSION_NAME = "4.13.4";

    private int dpToPx(int dp) {
        return Math.round(dp * getResources().getDisplayMetrics().density);
    }

    private static final String PREFS_NAME = "MinecraftControllerPrefs";
    private static final String PREF_LOCAL_URL = "local_url";
    private static final String PREF_REMOTE_URL = "remote_url";
    private static final String PREF_CONN_MODE = "conn_mode";
    private static final String PREF_ADMIN_PIN = "admin_pin";
    // PIN salvato rifiutato dal PC (cambiato sul PC): non si ritenta da soli, né qui né nel widget, altrimenti
    // dopo 5 tentativi il PC blocca il telefono per 10 minuti e anche il PIN giusto risulta rifiutato
    static final String PREF_ADMIN_PIN_REJECTED = "admin_pin_rejected";
    private static final long AUTH_LOCK_MS = 10 * 60 * 1000L;
    private static final String PREF_AUTH_TOKEN = "auth_token";
    private static final String PREF_BIOMETRIC = "biometric_enabled";
    private static final String PREF_FIRST_RUN = "first_run_done";

    // Nessun indirizzo predefinito: ogni installazione si collega solo al PC inserito dal proprio utente
    private static final String DEFAULT_LOCAL_URL = "";
    private static final String DEFAULT_REMOTE_URL = "";

    private static final String CHANNEL_UPDATES = ServerMonitorService.CHANNEL_UPDATES;

    // Header Widgets
    private Button btnToggleDrawer, btnTopHome, btnTopRefresh;
    private TextView txtHeaderTitle, txtHeaderSubtitle, pillServerStatus, pillRouteStatus;

    // Navigation Drawer
    private View drawerScrim;
    private LinearLayout drawerPanel;
    private Button btnDrawerClose;
    private Button drawerItemDashboard, drawerItemPlayers, drawerItemGameRules, drawerItemProperties;
    private Button drawerItemConsole, drawerItemAutoRestart, drawerItemErrors, drawerItemChat, drawerItemSettings;
    private TextView txtDrawerVersion;

    // 9 Content Views
    private View viewDashboard, viewPlayers, viewGameRules, viewProperties;
    private View viewConsole, viewAutoRestart, viewErrors, viewChat, viewSettings;

    // Dashboard Widgets
    private TextView pillServerStatus2, txtMainStatus, txtServerAddress, txtUptime, pillTps;
    private TextView txtRamValue, txtCpuValue, txtPlayersOverview, txtPlayerList;
    private ProgressBar progBarRam, progBarCpu;
    private Button btnActionStart, btnActionRestart, btnActionStop, btnQuickGoPlayers;
    private Button chipTimeDay, chipTimeNight, chipWeatherClear, chipWeatherRain;

    // Players & Whitelist Widgets
    private TextView txtPlayersTitleCount, txtPlayersEmpty, txtWhitelistCount;
    private LinearLayout layoutPlayersList, layoutWhitelistList;
    private Button btnWhitelistToggle, btnWhitelistAdd;
    private EditText edtWhitelistInput;

    // GameRules Widgets
    private Button btnRuleKeepInventory, btnRuleMobGriefing, btnRulePvP;
    private Button btnRuleDaylight, btnRuleFireTick, btnRuleWeatherCycle, btnRuleShowDeath;

    // Server Properties Widgets
    private Button chipDiffPeaceful, chipDiffEasy, chipDiffNormal, chipDiffHard;
    private Button chipView8, chipView10, chipView12, chipView14;
    private EditText edtPropertiesMotd, edtPropertiesMaxPlayers;
    private Button btnSaveProperties, btnRestorePropertiesBak;
    private String mCurrentDifficulty = "normal";
    private int mCurrentViewDistance = 8;

    // Console Widgets
    private TextView txtConsoleLogs;
    private ScrollView scrollConsole;
    private EditText edtCommand;
    private Button btnSendCommand, btnClearConsole;
    private Button chipList, chipSaveAll, chipTps, chipHelp, chipStop;
    private EditText edtSearchLog;
    private Button btnLogFilterAll, btnLogFilterError, btnLogFilterChat;
    private final List<String> mRawLogsList = new ArrayList<>();
    private String mCurrentLogFilter = "ALL"; // ALL, ERROR, CHAT
    private String mLogSearchQuery = "";

    // Auto-Restart Widgets
    private TextView txtRestartCountdown;
    private ProgressBar progBarRestart;
    private Button btnPostponeRestart, btnResetRestartTimer, btnRestartServerNow;

    // Errors Widgets
    private TextView txtErrorHealthBadge, txtErrorStats, txtErrorsLogList;
    private Button btnClearErrors, btnViewLatestCrash;
    private String mLatestCrashReportContent = "";
    private String mLatestCrashReportFilename = "";

    // Chat Widgets
    private TextView txtChatMessages;
    private ScrollView scrollChat;
    private EditText edtChatText;
    private Button btnSendChat, btnClearChat;

    // Settings & Backup Widgets
    private EditText edtLocalUrl, edtRemoteUrl, edtAdminPin;
    private Button btnTestLocal, btnTestRemote, btnModeAuto, btnModeLocalOnly, btnModeRemoteOnly, btnSaveConnection, btnSyncNtfyConnection, btnTestPushNotification;
    private BroadcastReceiver mUrlUpdateReceiver;
    private ConnectivityManager.NetworkCallback mNetworkCallback;
    private TextView txtBackupStatusBadge, txtBackupInfo, txtAppVersionStatus;
    private ProgressBar progBarBackup;
    private Button btnTriggerBackup, btnRestoreBackup, btnCheckUpdates;
    private Button btnCleanLogs;
    private TextView txtStorageBreakdown;
    private TextView txtJvmRamBadge;
    private Button btnJvmRamPick, btnSaveJvmTuning;
    private CheckBox chkJvmAikar;
    private int mSelectedJvmRam = 0;   // 0 = RAM non impostata nel server (Java usa un quarto di quella del PC)
    private int mHostRamGb = 16;       // RAM del PC host, letta da /api/server/jvm-tuning
    private CheckBox chkBiometric;

    // Eco Mode Widgets
    private TextView txtEcoModeStatusBadge;
    private CheckBox chkEcoMode;
    private Button chipEco30, chipEco45, chipEco60, btnSaveEcoMode;
    private int mSelectedEcoMinutes = 45;

    // Anti-Lag Watchdog Widgets
    private TextView txtAntiLagStatusBadge;
    private CheckBox chkAntiLagEnabled, chkAntiLagAutoClean;
    private Button btnSaveAntiLag;

    // Remote Config Editor Widgets
    private Button btnOpenConfigEditor;

    // Changelog & Info Widgets
    private Button btnTopInfo;

    // Auth & Wizard Overlays
    private RelativeLayout authLayout, wizardLayout;
    private Button btnAuthenticate, wizardBtnSaveAndConnect;
    private EditText wizardEdtLocalUrl, wizardEdtRemoteUrl, wizardEdtPin;

    // App State
    private String mLocalUrl = DEFAULT_LOCAL_URL;
    private String mRemoteUrl = DEFAULT_REMOTE_URL;
    private int mConnMode = 0; // 0=Auto, 1=Local, 2=Remote
    private String mAdminPin = "";
    private long mAuthLockedUntil = 0;   // il PC ha bloccato i tentativi (troppi PIN errati): niente login fino ad allora
    // Un solo accesso con il PIN alla volta: all'avvio più richieste rifiutate insieme facevano partire più tentativi
    // con lo stesso PIN vecchio e il PC bloccava il telefono prima ancora che si scrivesse il PIN nuovo
    private boolean mAuthInFlight = false;
    private final java.util.List<Runnable> mAuthWaiters = new java.util.ArrayList<>();
    /** true dopo il primo login riuscito in questa apertura dell'app (topic e segreto ntfy riletti dal PC). */
    private boolean mPushConfigSynced = false;
    private long mLastPushSyncAttempt = 0;
    private long mDefaultPinBlockedUntil = 0;
    private String mAuthToken = "";
    private boolean mIsUsingRemote = false;
    private boolean mIsAuthenticated = false;
    private boolean mIsAuthenticating = false;
    private boolean mIsRuleKeepInventory = true;
    private boolean mIsRuleMobGriefing = true;
    private boolean mIsRulePvP = true;
    private boolean mIsRuleDaylight = true;
    private boolean mIsRuleFireTick = true;
    private boolean mIsRuleWeatherCycle = true;
    private boolean mIsRuleShowDeath = true;
    private boolean mIsWhitelistEnabled = false;

    private boolean mWasRunningBackup = false;
    // "Spegni server e fai backup": il backup si apre appena il PC risponde che il server è spento
    private boolean mBackupAfterStop = false;
    private boolean mWasRunningRestore = false;
    private static final String PREF_PUSH_SECRET = "push_secret";
    private String mLastNotifiedBackupDate = "";
    private String mServerState = "OFFLINE";
    // Server (istanze) configurati sul PC: con più di uno la Dashboard mostra quale è attivo e permette di cambiarlo
    private JSONArray mInstances = new JSONArray();
    private String mActiveInstanceName = "";
    private String mActiveInstanceDetails = "";  // tipo e versione del server attivo (es. "Fabric 1.21.4")
    private long mLastInstancesFetch = 0;
    private int mInstancesFailures = 0;  // letture dei server non riuscite di fila
    private Button btnServerSwitch;
    private TextView txtDrawerServerName;

    private int mCurrentSectionIndex = 0;
    private final Handler mPollHandler = new Handler(Looper.getMainLooper());
    private CancellationSignal mCancellationSignal;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        // Lingua dell'app: i testi del layout prendono le loro chiavi (android:tag), quelli arrivati dal PC si traducono
        I18n.ensure(this);
        I18n.attach(getWindow().getDecorView());

        PcTls.install(this);
        if (savedInstanceState == null) deleteOldUpdateApks();
        initNotificationChannels();
        checkNotificationPermission();
        loadPreferences();
        bindViews();
        setupListeners();
        setupUrlUpdateReceiver();
        initNetworkRouteMonitor();
        startBackgroundMonitoringService();
        requestIgnoreBatteryOptimizations();

        selectSection(0);

        SharedPreferences prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        boolean firstRunDone = prefs.getBoolean(PREF_FIRST_RUN, false);
        boolean bioEnabled = prefs.getBoolean(PREF_BIOMETRIC, true);

        if (!firstRunDone) {
            wizardLayout.setVisibility(View.VISIBLE);
        } else if (bioEnabled) {
            authLayout.setVisibility(View.VISIBLE);
            mIsAuthenticated = false;
            requestBiometricOrCredentialAuth();
        } else {
            mIsAuthenticated = true;
            authLayout.setVisibility(View.GONE);
            ensureAuthToken(null);
            startPolling();
            checkForUpdates(true);
            handleIncomingIntent(getIntent());
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleIncomingIntent(intent);
    }

    private void handleIncomingIntent(Intent intent) {
        if (intent == null) return;
        int targetSection = intent.getIntExtra("target_section", -1);
        if (targetSection >= 0) {
            selectSection(targetSection);
        } else {
            int targetTab = intent.getIntExtra("target_tab", -1);
            if (targetTab == 2) {
                selectSection(6); // Errors
            } else if (targetTab == 4) {
                selectSection(8); // Settings
            }
        }
        if (intent.getBooleanExtra("check_update", false)) {
            checkForUpdates(false);
        }
    }

    private void restartMonitorService() {
        try {
            Intent i = new Intent(this, ServerMonitorService.class);
            i.setAction(ServerMonitorService.ACTION_RESTART);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                startForegroundService(i);
            } else {
                startService(i);
            }
        } catch (Exception ignored) {}
    }

    private void startBackgroundMonitoringService() {
        try {
            Intent serviceIntent = new Intent(this, ServerMonitorService.class);
            serviceIntent.setAction(ServerMonitorService.ACTION_START);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                startForegroundService(serviceIntent);
            } else {
                startService(serviceIntent);
            }
        } catch (Exception ignored) {}
    }

    private void initNotificationChannels() {
        ServerMonitorService.ensureChannels(this);
    }

    private void loadPreferences() {
        SharedPreferences prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        mLocalUrl = prefs.getString(PREF_LOCAL_URL, DEFAULT_LOCAL_URL);
        mRemoteUrl = prefs.getString(PREF_REMOTE_URL, DEFAULT_REMOTE_URL);
        if (mRemoteUrl != null) {
            Matcher m = PcTls.REMOTE_URL_PATTERN.matcher(mRemoteUrl);
            if (m.find()) mRemoteUrl = m.group();
        }
        while (mLocalUrl != null && mLocalUrl.endsWith("/")) mLocalUrl = mLocalUrl.substring(0, mLocalUrl.length() - 1);
        while (mRemoteUrl != null && mRemoteUrl.endsWith("/")) mRemoteUrl = mRemoteUrl.substring(0, mRemoteUrl.length() - 1);
        mConnMode = prefs.getInt(PREF_CONN_MODE, 0);
        mAdminPin = prefs.getString(PREF_ADMIN_PIN, "");
        mAuthToken = prefs.getString(PREF_AUTH_TOKEN, "");
        mIsUsingRemote = prefs.getBoolean("is_using_remote", false);
    }

    private void savePreferences() {
        SharedPreferences prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        prefs.edit()
             .putString(PREF_LOCAL_URL, mLocalUrl)
             .putString(PREF_REMOTE_URL, mRemoteUrl)
             .putInt(PREF_CONN_MODE, mConnMode)
             .putString(PREF_ADMIN_PIN, mAdminPin)
             .putBoolean(PREF_BIOMETRIC, chkBiometric != null && chkBiometric.isChecked())
             .putBoolean("is_using_remote", mIsUsingRemote)
             .apply();
    }

    private void bindViews() {
        // Header
        btnToggleDrawer = findViewById(R.id.btnToggleDrawer);
        btnTopHome = findViewById(R.id.btnTopHome);
        btnTopRefresh = findViewById(R.id.btnTopRefresh);
        txtHeaderTitle = findViewById(R.id.txtHeaderTitle);
        txtHeaderSubtitle = findViewById(R.id.txtHeaderSubtitle);
        pillServerStatus = findViewById(R.id.pillServerStatus);
        pillRouteStatus = findViewById(R.id.pillRouteStatus);

        // Drawer
        drawerScrim = findViewById(R.id.drawerScrim);
        drawerPanel = findViewById(R.id.drawerPanel);
        btnDrawerClose = findViewById(R.id.btnDrawerClose);
        drawerItemDashboard = findViewById(R.id.drawerItemDashboard);
        drawerItemPlayers = findViewById(R.id.drawerItemPlayers);
        drawerItemGameRules = findViewById(R.id.drawerItemGameRules);
        drawerItemProperties = findViewById(R.id.drawerItemProperties);
        drawerItemConsole = findViewById(R.id.drawerItemConsole);
        drawerItemAutoRestart = findViewById(R.id.drawerItemAutoRestart);
        drawerItemErrors = findViewById(R.id.drawerItemErrors);
        drawerItemChat = findViewById(R.id.drawerItemChat);
        drawerItemSettings = findViewById(R.id.drawerItemSettings);
        txtDrawerVersion = findViewById(R.id.txtDrawerVersion);

        // 9 Views
        viewDashboard = findViewById(R.id.viewDashboard);
        viewPlayers = findViewById(R.id.viewPlayers);
        viewGameRules = findViewById(R.id.viewGameRules);
        viewProperties = findViewById(R.id.viewProperties);
        viewConsole = findViewById(R.id.viewConsole);
        viewAutoRestart = findViewById(R.id.viewAutoRestart);
        viewErrors = findViewById(R.id.viewErrors);
        viewChat = findViewById(R.id.viewChat);
        viewSettings = findViewById(R.id.viewSettings);

        // Dashboard
        pillServerStatus2 = findViewById(R.id.pillServerStatus2);
        txtMainStatus = findViewById(R.id.txtMainStatus);
        txtServerAddress = findViewById(R.id.txtServerAddress);
        txtUptime = findViewById(R.id.txtUptime);
        pillTps = findViewById(R.id.pillTps);
        txtRamValue = findViewById(R.id.txtRamValue);
        txtCpuValue = findViewById(R.id.txtCpuValue);
        progBarRam = findViewById(R.id.progBarRam);
        progBarCpu = findViewById(R.id.progBarCpu);
        btnActionStart = findViewById(R.id.btnActionStart);
        btnServerSwitch = findViewById(R.id.btnServerSwitch);
        txtDrawerServerName = findViewById(R.id.txtDrawerServerName);
        txtDrawerServerName.setText(I18n.t("ui.server_principale"));
        btnActionRestart = findViewById(R.id.btnActionRestart);
        btnActionStop = findViewById(R.id.btnActionStop);
        btnQuickGoPlayers = findViewById(R.id.btnQuickGoPlayers);
        txtPlayersOverview = findViewById(R.id.txtPlayersOverview);
        txtPlayerList = findViewById(R.id.txtPlayerList);
        chipTimeDay = findViewById(R.id.chipTimeDay);
        chipTimeNight = findViewById(R.id.chipTimeNight);
        chipWeatherClear = findViewById(R.id.chipWeatherClear);
        chipWeatherRain = findViewById(R.id.chipWeatherRain);

        // Players & Whitelist
        txtPlayersTitleCount = findViewById(R.id.txtPlayersTitleCount);
        txtPlayersEmpty = findViewById(R.id.txtPlayersEmpty);
        layoutPlayersList = findViewById(R.id.layoutPlayersList);
        btnWhitelistToggle = findViewById(R.id.btnWhitelistToggle);
        edtWhitelistInput = findViewById(R.id.edtWhitelistInput);
        btnWhitelistAdd = findViewById(R.id.btnWhitelistAdd);
        txtWhitelistCount = findViewById(R.id.txtWhitelistCount);
        layoutWhitelistList = findViewById(R.id.layoutWhitelistList);

        // GameRules
        btnRuleKeepInventory = findViewById(R.id.btnRuleKeepInventory);
        btnRuleMobGriefing = findViewById(R.id.btnRuleMobGriefing);
        btnRulePvP = findViewById(R.id.btnRulePvP);
        btnRuleDaylight = findViewById(R.id.btnRuleDaylight);
        btnRuleFireTick = findViewById(R.id.btnRuleFireTick);
        btnRuleWeatherCycle = findViewById(R.id.btnRuleWeatherCycle);
        btnRuleShowDeath = findViewById(R.id.btnRuleShowDeath);
        loadGameRulesPrefs();
        updateGameRuleButtonsUI();

        // Server Properties
        chipDiffPeaceful = findViewById(R.id.chipDiffPeaceful);
        chipDiffEasy = findViewById(R.id.chipDiffEasy);
        chipDiffNormal = findViewById(R.id.chipDiffNormal);
        chipDiffHard = findViewById(R.id.chipDiffHard);
        chipView8 = findViewById(R.id.chipView8);
        chipView10 = findViewById(R.id.chipView10);
        chipView12 = findViewById(R.id.chipView12);
        chipView14 = findViewById(R.id.chipView14);
        edtPropertiesMotd = findViewById(R.id.edtPropertiesMotd);
        edtPropertiesMaxPlayers = findViewById(R.id.edtPropertiesMaxPlayers);
        btnSaveProperties = findViewById(R.id.btnSaveProperties);
        btnRestorePropertiesBak = findViewById(R.id.btnRestorePropertiesBak);

        // Console
        scrollConsole = findViewById(R.id.scrollConsole);
        txtConsoleLogs = findViewById(R.id.txtConsoleLogs);
        I18n.setLineMode(txtConsoleLogs, I18n.LINES_CONSOLE);
        edtCommand = findViewById(R.id.edtCommand);
        btnSendCommand = findViewById(R.id.btnSendCommand);
        btnClearConsole = findViewById(R.id.btnClearConsole);
        chipList = findViewById(R.id.chipList);
        chipSaveAll = findViewById(R.id.chipSaveAll);
        chipTps = findViewById(R.id.chipTps);
        chipHelp = findViewById(R.id.chipHelp);
        chipStop = findViewById(R.id.chipStop);
        edtSearchLog = findViewById(R.id.edtSearchLog);
        btnLogFilterAll = findViewById(R.id.btnLogFilterAll);
        btnLogFilterError = findViewById(R.id.btnLogFilterError);
        btnLogFilterChat = findViewById(R.id.btnLogFilterChat);

        // Auto-Restart
        txtRestartCountdown = findViewById(R.id.txtRestartCountdown);
        progBarRestart = findViewById(R.id.progBarRestart);
        btnPostponeRestart = findViewById(R.id.btnPostponeRestart);
        btnResetRestartTimer = findViewById(R.id.btnResetRestartTimer);
        btnRestartServerNow = findViewById(R.id.btnRestartServerNow);

        // Errors
        txtErrorHealthBadge = findViewById(R.id.txtErrorHealthBadge);
        txtErrorStats = findViewById(R.id.txtErrorStats);
        txtErrorsLogList = findViewById(R.id.txtErrorsLogList);
        I18n.setLineMode(txtErrorsLogList, I18n.LINES_CONSOLE);
        btnClearErrors = findViewById(R.id.btnClearErrors);
        btnViewLatestCrash = findViewById(R.id.btnViewLatestCrash);

        // Chat
        txtChatMessages = findViewById(R.id.txtChatMessages);
        I18n.setLineMode(txtChatMessages, I18n.LINES_CHAT);
        scrollChat = findViewById(R.id.scrollChat);
        edtChatText = findViewById(R.id.edtChatText);
        btnSendChat = findViewById(R.id.btnSendChat);
        btnClearChat = findViewById(R.id.btnClearChat);

        // Settings & Backup
        edtLocalUrl = findViewById(R.id.edtLocalUrl);
        edtRemoteUrl = findViewById(R.id.edtRemoteUrl);
        edtAdminPin = findViewById(R.id.edtAdminPin);
        btnTestLocal = findViewById(R.id.btnTestLocal);
        btnTestRemote = findViewById(R.id.btnTestRemote);
        btnModeAuto = findViewById(R.id.btnModeAuto);
        btnModeLocalOnly = findViewById(R.id.btnModeLocalOnly);
        btnModeRemoteOnly = findViewById(R.id.btnModeRemoteOnly);
        btnSaveConnection = findViewById(R.id.btnSaveConnection);
        btnSyncNtfyConnection = findViewById(R.id.btnSyncNtfyConnection);
        btnTestPushNotification = findViewById(R.id.btnTestPushNotification);
        txtBackupStatusBadge = findViewById(R.id.txtBackupStatusBadge);
        txtBackupInfo = findViewById(R.id.txtBackupInfo);
        progBarBackup = findViewById(R.id.progBarBackup);
        btnTriggerBackup = findViewById(R.id.btnTriggerBackup);
        btnRestoreBackup = findViewById(R.id.btnRestoreBackup);
        btnCleanLogs = findViewById(R.id.btnCleanLogs);
        txtStorageBreakdown = findViewById(R.id.txtStorageBreakdown);
        txtJvmRamBadge = findViewById(R.id.txtJvmRamBadge);
        btnJvmRamPick = findViewById(R.id.btnJvmRamPick);
        chkJvmAikar = findViewById(R.id.chkJvmAikar);
        btnSaveJvmTuning = findViewById(R.id.btnSaveJvmTuning);
        chkBiometric = findViewById(R.id.chkBiometric);
        txtAppVersionStatus = findViewById(R.id.txtAppVersionStatus);
        btnCheckUpdates = findViewById(R.id.btnCheckUpdates);
        // Variante F-Droid: gli aggiornamenti arrivano da F-Droid, l'app non scarica APK da sola
        if (!Flavor.SELF_UPDATE) {
            if (btnCheckUpdates != null) btnCheckUpdates.setVisibility(View.GONE);
            TextView securityTitle = findViewById(R.id.txtSecurityTitle);
            securityTitle.setTag("t:ui.sicurezza");
            securityTitle.setText(I18n.t("ui.sicurezza"));
        }

        // Eco Mode
        txtEcoModeStatusBadge = findViewById(R.id.txtEcoModeStatusBadge);
        chkEcoMode = findViewById(R.id.chkEcoMode);
        chipEco30 = findViewById(R.id.chipEco30);
        chipEco45 = findViewById(R.id.chipEco45);
        chipEco60 = findViewById(R.id.chipEco60);
        btnSaveEcoMode = findViewById(R.id.btnSaveEcoMode);

        // Anti-Lag Watchdog
        txtAntiLagStatusBadge = findViewById(R.id.txtAntiLagStatusBadge);
        chkAntiLagEnabled = findViewById(R.id.chkAntiLagEnabled);
        chkAntiLagAutoClean = findViewById(R.id.chkAntiLagAutoClean);
        btnSaveAntiLag = findViewById(R.id.btnSaveAntiLag);

        // Remote Config Editor
        btnOpenConfigEditor = findViewById(R.id.btnOpenConfigEditor);

        // Changelog & Informazioni
        btnTopInfo = findViewById(R.id.btnTopInfo);

        // Overlays
        authLayout = findViewById(R.id.authLayout);
        btnAuthenticate = findViewById(R.id.btnAuthenticate);
        wizardLayout = findViewById(R.id.wizardLayout);
        wizardBtnSaveAndConnect = findViewById(R.id.wizardBtnSaveAndConnect);
        wizardEdtLocalUrl = findViewById(R.id.wizardEdtLocalUrl);
        wizardEdtRemoteUrl = findViewById(R.id.wizardEdtRemoteUrl);
        wizardEdtPin = findViewById(R.id.wizardEdtPin);

        // Set inputs from preferences
        edtLocalUrl.setText(mLocalUrl);
        edtRemoteUrl.setText(mRemoteUrl);
        if (edtAdminPin != null) edtAdminPin.setText(mAdminPin);
        wizardEdtLocalUrl.setText(mLocalUrl);
        wizardEdtRemoteUrl.setText(mRemoteUrl);
        wizardEdtPin.setText(mAdminPin);
        chkBiometric.setChecked(getSharedPreferences(PREFS_NAME, MODE_PRIVATE).getBoolean(PREF_BIOMETRIC, true));
        txtAppVersionStatus.setText(I18n.t("app.versione_installata_v_build", CURRENT_VERSION_NAME, CURRENT_VERSION_CODE));
        if (txtDrawerVersion != null) {
            txtDrawerVersion.setText("v" + CURRENT_VERSION_NAME + " (Build " + CURRENT_VERSION_CODE + ")");
        }

        updateRoutePillUI();
    }

    private void setupListeners() {
        btnToggleDrawer.setOnClickListener(this);
        btnTopHome.setOnClickListener(this);
        btnDrawerClose.setOnClickListener(this);
        drawerScrim.setOnClickListener(this);
        btnTopRefresh.setOnClickListener(this);

        drawerItemDashboard.setOnClickListener(this);
        drawerItemPlayers.setOnClickListener(this);
        drawerItemGameRules.setOnClickListener(this);
        drawerItemProperties.setOnClickListener(this);
        drawerItemConsole.setOnClickListener(this);
        drawerItemAutoRestart.setOnClickListener(this);
        drawerItemErrors.setOnClickListener(this);
        drawerItemChat.setOnClickListener(this);
        drawerItemSettings.setOnClickListener(this);

        btnActionStart.setOnClickListener(this);
        btnServerSwitch.setOnClickListener(this);
        btnActionRestart.setOnClickListener(this);
        btnActionStop.setOnClickListener(this);
        btnQuickGoPlayers.setOnClickListener(this);

        chipTimeDay.setOnClickListener(this);
        chipTimeNight.setOnClickListener(this);
        chipWeatherClear.setOnClickListener(this);
        chipWeatherRain.setOnClickListener(this);

        btnWhitelistToggle.setOnClickListener(this);
        btnWhitelistAdd.setOnClickListener(this);

        btnRuleKeepInventory.setOnClickListener(this);
        btnRuleMobGriefing.setOnClickListener(this);
        btnRulePvP.setOnClickListener(this);
        btnRuleDaylight.setOnClickListener(this);
        btnRuleFireTick.setOnClickListener(this);
        btnRuleWeatherCycle.setOnClickListener(this);
        btnRuleShowDeath.setOnClickListener(this);

        chipDiffPeaceful.setOnClickListener(this);
        chipDiffEasy.setOnClickListener(this);
        chipDiffNormal.setOnClickListener(this);
        chipDiffHard.setOnClickListener(this);
        chipView8.setOnClickListener(this);
        chipView10.setOnClickListener(this);
        chipView12.setOnClickListener(this);
        chipView14.setOnClickListener(this);
        btnSaveProperties.setOnClickListener(this);
        if (btnRestorePropertiesBak != null) {
            btnRestorePropertiesBak.setOnClickListener(this);
        }

        btnSendCommand.setOnClickListener(this);
        btnClearConsole.setOnClickListener(this);
        chipList.setOnClickListener(this);
        chipSaveAll.setOnClickListener(this);
        chipTps.setOnClickListener(this);
        chipHelp.setOnClickListener(this);
        chipStop.setOnClickListener(this);

        btnPostponeRestart.setOnClickListener(this);
        btnResetRestartTimer.setOnClickListener(this);
        btnRestartServerNow.setOnClickListener(this);

        btnClearErrors.setOnClickListener(this);
        btnViewLatestCrash.setOnClickListener(this);

        btnSendChat.setOnClickListener(this);
        if (btnClearChat != null) {
            btnClearChat.setOnClickListener(this);
        }
        edtChatText.setOnEditorActionListener(new TextView.OnEditorActionListener() {
            @Override
            public boolean onEditorAction(TextView v, int actionId, KeyEvent event) {
                if (actionId == EditorInfo.IME_ACTION_SEND ||
                    (event != null && event.getKeyCode() == KeyEvent.KEYCODE_ENTER && event.getAction() == KeyEvent.ACTION_DOWN)) {
                    submitChat();
                    return true;
                }
                return false;
            }
        });

        btnTestLocal.setOnClickListener(this);
        btnTestRemote.setOnClickListener(this);
        btnModeAuto.setOnClickListener(this);
        btnModeLocalOnly.setOnClickListener(this);
        btnModeRemoteOnly.setOnClickListener(this);
        btnSaveConnection.setOnClickListener(this);
        if (btnSyncNtfyConnection != null) {
            btnSyncNtfyConnection.setOnClickListener(this);
        }
        if (btnTestPushNotification != null) {
            btnTestPushNotification.setOnClickListener(this);
        }
        btnTriggerBackup.setOnClickListener(this);
        btnRestoreBackup.setOnClickListener(this);
        btnLogFilterAll.setOnClickListener(this);
        btnLogFilterError.setOnClickListener(this);
        btnLogFilterChat.setOnClickListener(this);
        edtCommand.setOnEditorActionListener(new TextView.OnEditorActionListener() {
            @Override
            public boolean onEditorAction(TextView v, int actionId, KeyEvent event) {
                if (actionId == EditorInfo.IME_ACTION_SEND ||
                    (event != null && event.getKeyCode() == KeyEvent.KEYCODE_ENTER && event.getAction() == KeyEvent.ACTION_DOWN)) {
                    submitCommand(edtCommand.getText().toString());
                    return true;
                }
                return false;
            }
        });

        edtSearchLog.addTextChangedListener(new android.text.TextWatcher() {
            @Override public void beforeTextChanged(CharSequence s, int start, int count, int after) {}
            @Override public void onTextChanged(CharSequence s, int start, int before, int count) {
                mLogSearchQuery = s.toString();
                renderFilteredLogs();
            }
            @Override public void afterTextChanged(android.text.Editable s) {}
        });

        btnCleanLogs.setOnClickListener(this);
        btnJvmRamPick.setOnClickListener(this);
        btnSaveJvmTuning.setOnClickListener(this);
        if (btnCheckUpdates != null) btnCheckUpdates.setOnClickListener(this);
        Button btnAppLanguage = findViewById(R.id.btnAppLanguage);
        if (btnAppLanguage != null) {
            btnAppLanguage.setText(I18n.getLanguage().toUpperCase(java.util.Locale.ROOT));
            btnAppLanguage.setOnClickListener(this);
        }
        if (btnTopInfo != null) btnTopInfo.setOnClickListener(this);

        if (chipEco30 != null) chipEco30.setOnClickListener(this);
        if (chipEco45 != null) chipEco45.setOnClickListener(this);
        if (chipEco60 != null) chipEco60.setOnClickListener(this);
        if (btnSaveEcoMode != null) btnSaveEcoMode.setOnClickListener(this);

        if (btnSaveAntiLag != null) btnSaveAntiLag.setOnClickListener(this);
        if (btnOpenConfigEditor != null) btnOpenConfigEditor.setOnClickListener(this);

        btnAuthenticate.setOnClickListener(this);
        wizardBtnSaveAndConnect.setOnClickListener(this);
    }

    // ================= NAVIGATION DRAWER LOGIC =================

    private void openDrawer() {
        vibrate(15);
        drawerScrim.setVisibility(View.VISIBLE);
        drawerPanel.setVisibility(View.VISIBLE);
    }

    private void closeDrawer() {
        drawerScrim.setVisibility(View.GONE);
        drawerPanel.setVisibility(View.GONE);
    }

    private void selectSection(int index) {
        mCurrentSectionIndex = index;
        closeDrawer();

        viewDashboard.setVisibility(index == 0 ? View.VISIBLE : View.GONE);
        viewPlayers.setVisibility(index == 1 ? View.VISIBLE : View.GONE);
        viewGameRules.setVisibility(index == 2 ? View.VISIBLE : View.GONE);
        viewProperties.setVisibility(index == 3 ? View.VISIBLE : View.GONE);
        viewConsole.setVisibility(index == 4 ? View.VISIBLE : View.GONE);
        viewAutoRestart.setVisibility(index == 5 ? View.VISIBLE : View.GONE);
        viewErrors.setVisibility(index == 6 ? View.VISIBLE : View.GONE);
        viewChat.setVisibility(index == 7 ? View.VISIBLE : View.GONE);
        viewSettings.setVisibility(index == 8 ? View.VISIBLE : View.GONE);

        // Voce attiva del menu: fondo rialzato con linea verde, testo chiaro; le altre righe piatte
        Button[] drawerItems = {drawerItemDashboard, drawerItemPlayers, drawerItemGameRules, drawerItemProperties,
                drawerItemConsole, drawerItemAutoRestart, drawerItemErrors, drawerItemChat, drawerItemSettings};
        for (int i = 0; i < drawerItems.length; i++) {
            boolean active = (i == index);
            drawerItems[i].setBackgroundResource(active ? R.drawable.drawer_item_selected : R.drawable.drawer_item);
            drawerItems[i].setTextColor(Color.parseColor(active ? "#f4f4f5" : "#c4c4c8"));
        }

        switch (index) {
            case 0:
                txtHeaderTitle.setText(I18n.t("ui.stato_server"));
                txtHeaderSubtitle.setText(mActiveInstanceDetails.isEmpty() ? "Voxarium" : mActiveInstanceDetails);
                fetchStatus();
                break;
            case 1:
                txtHeaderTitle.setText(I18n.t("ui.giocatori_whitelist"));
                txtHeaderSubtitle.setText(I18n.t("app.chi_online_chi_puo_entrare"));
                fetchPlayersAndWhitelist();
                break;
            case 2:
                txtHeaderTitle.setText(I18n.t("ui.regole_gioco"));
                txtHeaderSubtitle.setText(I18n.t("app.gamerule_mondo"));
                fetchGameRulesFromServer();
                break;
            case 3:
                txtHeaderTitle.setText(I18n.t("ui.impostazioni_server"));
                txtHeaderSubtitle.setText("server.properties");
                fetchProperties();
                break;
            case 4:
                txtHeaderTitle.setText(I18n.t("ui.console"));
                txtHeaderSubtitle.setText(I18n.t("app.log_comandi"));
                fetchLogs();
                break;
            case 5:
                txtHeaderTitle.setText(I18n.t("ui.riavvio_programmato"));
                txtHeaderSubtitle.setText(I18n.t("app.ogni_4_ore"));
                fetchStatus();
                break;
            case 6:
                txtHeaderTitle.setText(I18n.t("ui.errori"));
                txtHeaderSubtitle.setText(I18n.t("app.crash_report_anomalie"));
                fetchErrors();
                break;
            case 7:
                txtHeaderTitle.setText("Chat");
                txtHeaderSubtitle.setText(I18n.t("app.messaggi_giocatori"));
                fetchChat();
                break;
            case 8:
                txtHeaderTitle.setText(I18n.t("ui.backup_connessione"));
                syncUrlsFromNtfy(false, null);
                txtHeaderSubtitle.setText(I18n.t("app.copie_mondo_indirizzi"));
                fetchTunnelStatus();
                fetchBackupStatus();
                fetchStorageBreakdown();
                fetchJvmTuning();
                fetchEcoModeAndAntiLag();
                break;
        }
    }

    @Override
    public void onClick(View v) {
        int id = v.getId();

        if (id == R.id.wizardBtnSaveAndConnect) {
            saveWizardSettingsAndConnect();
            return;
        }

        if (id == R.id.btnAuthenticate) {
            requestBiometricOrCredentialAuth();
            return;
        }

        if (!mIsAuthenticated) {
            I18n.makeText(this, I18n.t("app.sblocca_prima_applicazione"), Toast.LENGTH_SHORT).show();
            return;
        }

        // Drawer buttons
        if (id == R.id.btnToggleDrawer) {
            openDrawer();
        } else if (id == R.id.btnDrawerClose || id == R.id.drawerScrim) {
            closeDrawer();
        } else if (id == R.id.drawerItemDashboard) {
            selectSection(0);
        } else if (id == R.id.drawerItemPlayers) {
            selectSection(1);
        } else if (id == R.id.drawerItemGameRules) {
            selectSection(2);
        } else if (id == R.id.drawerItemProperties) {
            selectSection(3);
        } else if (id == R.id.drawerItemConsole) {
            selectSection(4);
        } else if (id == R.id.drawerItemAutoRestart) {
            selectSection(5);
        } else if (id == R.id.drawerItemErrors) {
            selectSection(6);
        } else if (id == R.id.drawerItemChat) {
            selectSection(7);
        } else if (id == R.id.drawerItemSettings) {
            selectSection(8);
        } else if (id == R.id.btnQuickGoPlayers) {
            selectSection(1);
        } else if (id == R.id.btnTopHome) {
            vibrate(20);
            selectSection(0);
        } else if (id == R.id.btnTopRefresh) {
            vibrate(20);
            mLastInstancesFetch = 0;
            selectSection(mCurrentSectionIndex);
            fetchStatus();
            syncUrlsFromNtfy(false, null);
        }

        // Dashboard Actions
        else if (id == R.id.btnServerSwitch) {
            vibrate(20);
            showServerPicker();
        } else if (id == R.id.btnActionStart) {
            vibrate(40);
            actionStartServer();
        } else if (id == R.id.btnActionRestart) {
            vibrate(40);
            confirmRestartServer();
        } else if (id == R.id.btnActionStop) {
            vibrate(40);
            confirmStopServer();
        } else if (id == R.id.chipTimeDay || id == R.id.chipTimeNight || id == R.id.chipWeatherClear || id == R.id.chipWeatherRain) {
            if (!"ONLINE".equalsIgnoreCase(mServerState)) {
                I18n.makeText(this, I18n.t("app.server_deve_essere_online_comandi"), Toast.LENGTH_SHORT).show();
            } else if (id == R.id.chipTimeDay) {
                submitCommand("time set day");
            } else if (id == R.id.chipTimeNight) {
                submitCommand("time set night");
            } else if (id == R.id.chipWeatherClear) {
                submitCommand("weather clear");
            } else if (id == R.id.chipWeatherRain) {
                submitCommand("weather rain");
            }
        }

        // Whitelist Actions
        else if (id == R.id.btnWhitelistToggle) {
            executeApiCall("/api/whitelist/toggle", "POST", "{}", new ApiClient.ApiCallback() {
                @Override
                public void onSuccess(String response) {
                    try {
                        JSONObject obj = new JSONObject(response);
                        mIsWhitelistEnabled = obj.optBoolean("enabled", !mIsWhitelistEnabled);
                    } catch (Exception e) {
                        mIsWhitelistEnabled = !mIsWhitelistEnabled;
                    }
                    updateWhitelistToggleUI();
                    I18n.makeText(MainActivity.this, I18n.t("app.whitelist", (mIsWhitelistEnabled ? I18n.t("app.attiva") : I18n.t("ui.disattiva"))), Toast.LENGTH_SHORT).show();
                    fetchPlayersAndWhitelist();
                }
                @Override
                public void onError(String error) {
                    mIsWhitelistEnabled = !mIsWhitelistEnabled;
                    submitCommand("whitelist " + (mIsWhitelistEnabled ? "on" : "off"));
                    updateWhitelistToggleUI();
                    I18n.makeText(MainActivity.this, I18n.t("app.whitelist_impostata", (mIsWhitelistEnabled ? "ON" : "OFF")), Toast.LENGTH_SHORT).show();
                }
            });
        } else if (id == R.id.btnWhitelistAdd) {
            String pName = edtWhitelistInput.getText().toString().trim();
            if (!pName.isEmpty()) {
                addPlayerToWhitelist(pName);
                edtWhitelistInput.setText("");
            }
        }

        // GameRules Actions
        else if (id == R.id.btnRuleKeepInventory || id == R.id.btnRuleMobGriefing || id == R.id.btnRulePvP
                || id == R.id.btnRuleDaylight || id == R.id.btnRuleFireTick || id == R.id.btnRuleWeatherCycle
                || id == R.id.btnRuleShowDeath) {

            // PvP è in server.properties: si cambia anche a server spento
            if (id != R.id.btnRulePvP && !"ONLINE".equalsIgnoreCase(mServerState)) {
                vibrate(30);
                I18n.makeText(this, I18n.t("app.server_deve_essere_online_modificare"), Toast.LENGTH_SHORT).show();
                return;
            }

            vibrate(25);
            if (id == R.id.btnRuleKeepInventory) {
                toggleGameRule("keepInventory", !mIsRuleKeepInventory, btnRuleKeepInventory);
            } else if (id == R.id.btnRuleMobGriefing) {
                toggleGameRule("mobGriefing", !mIsRuleMobGriefing, btnRuleMobGriefing);
            } else if (id == R.id.btnRulePvP) {
                toggleGameRule("pvp", !mIsRulePvP, btnRulePvP);
            } else if (id == R.id.btnRuleDaylight) {
                toggleGameRule("doDaylightCycle", !mIsRuleDaylight, btnRuleDaylight);
            } else if (id == R.id.btnRuleFireTick) {
                toggleGameRule("doFireTick", !mIsRuleFireTick, btnRuleFireTick);
            } else if (id == R.id.btnRuleWeatherCycle) {
                toggleGameRule("doWeatherCycle", !mIsRuleWeatherCycle, btnRuleWeatherCycle);
            } else if (id == R.id.btnRuleShowDeath) {
                toggleGameRule("showDeathMessages", !mIsRuleShowDeath, btnRuleShowDeath);
            }
        }

        // Properties Actions
        else if (id == R.id.chipDiffPeaceful) {
            setDifficultyUI("peaceful");
        } else if (id == R.id.chipDiffEasy) {
            setDifficultyUI("easy");
        } else if (id == R.id.chipDiffNormal) {
            setDifficultyUI("normal");
        } else if (id == R.id.chipDiffHard) {
            setDifficultyUI("hard");
        } else if (id == R.id.chipView8) {
            setViewDistanceUI(8);
        } else if (id == R.id.chipView10) {
            setViewDistanceUI(10);
        } else if (id == R.id.chipView12) {
            setViewDistanceUI(12);
        } else if (id == R.id.chipView14) {
            setViewDistanceUI(14);
        } else if (id == R.id.btnSaveProperties) {
            saveProperties();
        } else if (id == R.id.btnRestorePropertiesBak) {
            confirmRestorePropertiesBak();
        }

        // Console Actions
        else if (id == R.id.btnSendCommand) {
            submitCommand(edtCommand.getText().toString());
        } else if (id == R.id.btnClearConsole) {
            actionClearLogs();
        } else if (id == R.id.btnLogFilterAll) {
            setLogFilter("ALL");
        } else if (id == R.id.btnLogFilterError) {
            setLogFilter("ERROR");
        } else if (id == R.id.btnLogFilterChat) {
            setLogFilter("CHAT");
        } else if (id == R.id.chipList) {
            submitCommand("list");
        } else if (id == R.id.chipSaveAll) {
            submitCommand("save-all");
        } else if (id == R.id.chipTps) {
            submitCommand("forge tps");
        } else if (id == R.id.chipHelp) {
            submitCommand("help");
        } else if (id == R.id.chipStop) {
            confirmStopServer();
        }

        // Auto-Restart Actions
        else if (id == R.id.btnPostponeRestart) {
            postponeRestartTimer();
        } else if (id == R.id.btnResetRestartTimer) {
            resetRestartTimer();
        } else if (id == R.id.btnRestartServerNow) {
            confirmRestartServer();
        }

        // Errors Actions
        else if (id == R.id.btnClearErrors) {
            actionClearErrors();
        } else if (id == R.id.btnViewLatestCrash) {
            viewLatestCrashReport();
        }

        // Chat Actions
        else if (id == R.id.btnSendChat) {
            submitChat();
        } else if (id == R.id.btnClearChat) {
            actionClearChat();
        }

        // Settings Actions
        else if (id == R.id.btnTestLocal) {
            testUrl(edtLocalUrl.getText().toString().trim(), I18n.t("app.wi_fi_locale"), false);
        } else if (id == R.id.btnTestRemote) {
            testUrl(edtRemoteUrl.getText().toString().trim(), I18n.t("ui.internet"), true);
        } else if (id == R.id.btnModeAuto) {
            vibrate(20);
            mConnMode = 0;
            savePreferences();
            updateRoutePillUI();
            I18n.makeText(this, I18n.t("app.modalita_automatica_smart"), Toast.LENGTH_SHORT).show();
            fetchStatus();
        } else if (id == R.id.btnModeLocalOnly) {
            vibrate(20);
            mConnMode = 1;
            savePreferences();
            updateRoutePillUI();
            I18n.makeText(this, I18n.t("app.modalita_solo_wi_fi_locale"), Toast.LENGTH_SHORT).show();
            fetchStatus();
        } else if (id == R.id.btnModeRemoteOnly) {
            vibrate(20);
            mConnMode = 2;
            savePreferences();
            updateRoutePillUI();
            I18n.makeText(this, I18n.t("app.modalita_solo_internet"), Toast.LENGTH_SHORT).show();
            fetchStatus();
        } else if (id == R.id.btnSaveConnection) {
            actionSaveSettings();
        } else if (id == R.id.btnTestPushNotification) {
            testPushNotification();
        } else if (id == R.id.btnSyncNtfyConnection) {
            syncUrlsFromNtfy(true, null);
        } else if (id == R.id.btnTriggerBackup) {
            startBackupAction();
        } else if (id == R.id.btnRestoreBackup) {
            startRestoreAction();
        } else if (id == R.id.btnCleanLogs) {
            actionCleanOldLogs();
        } else if (id == R.id.btnJvmRamPick) {
            showJvmRamPicker();
        } else if (id == R.id.btnSaveJvmTuning) {
            actionSaveJvmTuning();
        } else if (id == R.id.chipEco30) {
            setEcoMinutesUI(30);
        } else if (id == R.id.chipEco45) {
            setEcoMinutesUI(45);
        } else if (id == R.id.chipEco60) {
            setEcoMinutesUI(60);
        } else if (id == R.id.btnSaveEcoMode) {
            actionSaveEcoMode();
        } else if (id == R.id.btnSaveAntiLag) {
            actionSaveAntiLag();
        } else if (id == R.id.btnOpenConfigEditor) {
            openRemoteConfigDialog();
        } else if (id == R.id.btnAppLanguage) {
            vibrate(25);
            showLanguagePicker();
        } else if (id == R.id.btnCheckUpdates) {
            vibrate(25);
            checkForUpdates(false);
        } else if (id == R.id.btnTopInfo) {
            vibrate(25);
            showInfoVersionsPopup();
        }
    }

    private void vibrate(long ms) {
        try {
            Vibrator v = (Vibrator) getSystemService(Context.VIBRATOR_SERVICE);
            if (v != null) {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    v.vibrate(VibrationEffect.createOneShot(ms, VibrationEffect.DEFAULT_AMPLITUDE));
                } else {
                    v.vibrate(ms);
                }
            }
        } catch (Exception ignored) {}
    }

    // ================= SMART DUAL-CONNECTION ROUTING =================

    private String getEffectiveBaseUrl() {
        if (mConnMode == 1) return mLocalUrl;
        if (mConnMode == 2) return mRemoteUrl;
        return mIsUsingRemote ? mRemoteUrl : mLocalUrl;
    }

    /** Identificativo pubblico del PC (host_id) ricevuto al login: all'indirizzo di casa deve rispondere lui. */
    static final String PREF_PC_HOST_ID = "pc_host_id";
    // Indirizzo di casa già riconosciuto come il nostro PC su questa rete (si azzera quando cambia la rete)
    private volatile String mLocalVerifiedUrl = "";
    private Network mLastNetwork = null;

    /**
     * Prima di mandare token o PIN in chiaro all'indirizzo di casa (http://192.168...) si controlla che risponda il
     * nostro PC: su un Wi-Fi esterno (bar, amici) allo stesso indirizzo può esserci un altro dispositivo.
     * Se risponde un altro: in automatico si passa all'indirizzo da internet, in "Solo Wi-Fi" si rinuncia (refuse).
     * Se il controllo non riceve risposta vale lo stesso (unreachable in "Solo Wi-Fi"): prima la richiesta partiva
     * comunque, e chi su un Wi-Fi esterno faceva fallire apposta il controllo riceveva poi token o PIN.
     */
    private void withVerifiedBase(final Runnable go, final Runnable refuse) {
        withVerifiedBase(go, refuse, refuse);
    }

    private void withVerifiedBase(final Runnable go, final Runnable refuse, final Runnable unreachable) {
        final String base = getEffectiveBaseUrl();
        if (base == null || base.isEmpty() || !base.equals(mLocalUrl) || base.startsWith("https://")
                || base.equals(mLocalVerifiedUrl)) {
            go.run();
            return;
        }
        ApiClient.checkHost(this, base, 1500, new ApiClient.HostCallback() {
            @Override
            public void onResult(int result) {
                if (result == ApiClient.HOST_SAME) {
                    mLocalVerifiedUrl = base;
                    go.run();
                } else if (mConnMode == 0 && mRemoteUrl != null && !mRemoteUrl.isEmpty()) {
                    mIsUsingRemote = true;
                    savePreferences();
                    updateRoutePillUI();
                    go.run();
                } else if (result == ApiClient.HOST_UNREACHABLE) {
                    unreachable.run();
                } else {
                    refuse.run();
                }
            }
        });
    }

    private void updateRoutePillUI() {
        if (pillRouteStatus != null) {
            if (mConnMode == 1) {
                pillRouteStatus.setText(I18n.t("app.solo_wi_fi"));
                pillRouteStatus.setBackgroundResource(R.drawable.pill_route_local);
                pillRouteStatus.setTextColor(Color.parseColor("#9ccc65"));
            } else if (mConnMode == 2) {
                pillRouteStatus.setText(I18n.t("app.solo_internet"));
                pillRouteStatus.setBackgroundResource(R.drawable.pill_route_remote);
                pillRouteStatus.setTextColor(Color.parseColor("#c4c4c8"));
            } else {
                if (mIsUsingRemote) {
                    pillRouteStatus.setText(I18n.t("app.internet_auto"));
                    pillRouteStatus.setBackgroundResource(R.drawable.pill_route_remote);
                    pillRouteStatus.setTextColor(Color.parseColor("#c4c4c8"));
                } else {
                    pillRouteStatus.setText(I18n.t("app.wi_fi_auto"));
                    pillRouteStatus.setBackgroundResource(R.drawable.pill_route_local);
                    pillRouteStatus.setTextColor(Color.parseColor("#9ccc65"));
                }
            }
        }

        if (btnModeAuto != null && btnModeLocalOnly != null && btnModeRemoteOnly != null) {
            int inactiveColor = Color.parseColor("#9a9ca2");
            if (mConnMode == 1) {
                btnModeAuto.setText("Auto");
                btnModeAuto.setBackgroundResource(R.drawable.btn_slate);
                btnModeAuto.setTextColor(inactiveColor);
                btnModeLocalOnly.setText("✓ Wi-Fi");
                btnModeLocalOnly.setBackgroundResource(R.drawable.btn_blue);
                btnModeLocalOnly.setTextColor(Color.WHITE);
                btnModeRemoteOnly.setText(I18n.t("ui.internet"));
                btnModeRemoteOnly.setBackgroundResource(R.drawable.btn_slate);
                btnModeRemoteOnly.setTextColor(inactiveColor);
            } else if (mConnMode == 2) {
                btnModeAuto.setText("Auto");
                btnModeAuto.setBackgroundResource(R.drawable.btn_slate);
                btnModeAuto.setTextColor(inactiveColor);
                btnModeLocalOnly.setText("Wi-Fi");
                btnModeLocalOnly.setBackgroundResource(R.drawable.btn_slate);
                btnModeLocalOnly.setTextColor(inactiveColor);
                btnModeRemoteOnly.setText("✓ Internet");
                btnModeRemoteOnly.setBackgroundResource(R.drawable.btn_purple);
                btnModeRemoteOnly.setTextColor(Color.WHITE);
            } else {
                btnModeAuto.setText("✓ Auto");
                btnModeAuto.setBackgroundResource(R.drawable.btn_blue);
                btnModeAuto.setTextColor(Color.WHITE);
                btnModeLocalOnly.setText("Wi-Fi");
                btnModeLocalOnly.setBackgroundResource(R.drawable.btn_slate);
                btnModeLocalOnly.setTextColor(inactiveColor);
                btnModeRemoteOnly.setText(I18n.t("ui.internet"));
                btnModeRemoteOnly.setBackgroundResource(R.drawable.btn_slate);
                btnModeRemoteOnly.setTextColor(inactiveColor);
            }
        }
    }

    private void ensureAuthToken(final Runnable onReady) {
        ensureAuthToken(onReady, false);
    }

    /**
     * Impronta del certificato, identificativo, segreto e topic ntfy ricevuti dal PC: si sostituiscono solo se la
     * risposta arriva dall'indirizzo HTTPS (certificato riconosciuto) o se il PC non è ancora stato riconosciuto (primo
     * accesso). In chiaro sull'indirizzo di casa si aggiunge solo ciò che manca: l'identificativo del PC si legge senza
     * PIN, e prima un dispositivo su un Wi-Fi esterno poteva sostituirli con i suoi e ricevere il PIN anche da fuori.
     */
    private boolean mayReplaceTrustData(String base) {
        return (base != null && base.startsWith("https://"))
            || getSharedPreferences(PREFS_NAME, MODE_PRIVATE).getString(PREF_PC_HOST_ID, "").isEmpty();
    }

    private void ensureAuthToken(final Runnable onReady, final boolean hostChecked) {
        // Con un token valido si rifà comunque il login una volta per apertura, per rileggere topic e segreto ntfy dal PC
        if (mAuthToken != null && !mAuthToken.isEmpty() && mPushConfigSynced) {
            if (onReady != null) onReady.run();
            return;
        }
        String pin = mAdminPin != null ? mAdminPin : "";
        String base = getEffectiveBaseUrl();
        // Il PC ha rifiutato da poco l'accesso via internet per PIN predefinito: niente nuovi tentativi per 1 minuto
        boolean blockedRecently = System.currentTimeMillis() < mDefaultPinBlockedUntil
            || System.currentTimeMillis() < mAuthLockedUntil;
        final SharedPreferences authPrefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        boolean pinRejected = !pin.isEmpty() && pin.equals(authPrefs.getString(PREF_ADMIN_PIN_REJECTED, ""));
        if (pin.isEmpty() || base == null || base.isEmpty() || blockedRecently || pinRejected) {
            if (onReady != null) onReady.run();
            return;
        }
        if (mAuthInFlight) {   // accesso già in corso: si aspetta il suo esito
            if (onReady != null) mAuthWaiters.add(onReady);
            return;
        }
        if (!hostChecked) {
            // Il PIN passa in chiaro sull'indirizzo di casa: prima si controlla che lì risponda il nostro PC
            Runnable retry = new Runnable() {
                @Override
                public void run() {
                    ensureAuthToken(onReady, true);
                }
            };
            withVerifiedBase(retry, new Runnable() {
                @Override
                public void run() {
                    I18n.makeText(MainActivity.this, I18n.t("app.pc_casa_non_riconosciuto"), Toast.LENGTH_LONG).show();
                    if (onReady != null) onReady.run();
                }
            }, new Runnable() {
                @Override
                public void run() {
                    if (onReady != null) onReady.run();   // PC spento o fuori rete: nessun PIN inviato
                }
            });
            return;
        }
        mAuthInFlight = true;
        final String triedPin = pin;
        String body = "{\"pin\":\"" + pin.replace("\\", "\\\\").replace("\"", "\\\"") + "\"}";
        final String targetUrl = base + "/api/auth/verify";
        final boolean mayReplace = mayReplaceTrustData(base);
        ApiClient.post(targetUrl, body, null, 4000, new ApiClient.ApiCallback() {
            @Override
            public void onSuccess(String response) {
                try {
                    JSONObject obj = new JSONObject(response);
                    mAuthToken = obj.optString("token", "");
                    if (!mAuthToken.isEmpty()) mPushConfigSynced = true;
                    String pushSecret = obj.optString("push_secret", "");
                    String pushTopic = obj.optString("push_topic", "");
                    SharedPreferences prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
                    // Impronta del certificato HTTPS del PC: serve per collegarsi da fuori casa a https://IP:porta
                    if (mayReplace) PcTls.saveFingerprint(MainActivity.this, obj.optString("cert_fingerprint", ""));
                    else PcTls.saveFingerprintIfMissing(MainActivity.this, obj.optString("cert_fingerprint", ""));
                    String verifyRemote = obj.optString("remote_url", "").replaceAll("/+$", "");
                    // L'indirizzo da internet si aggiorna sempre (nuovo IP pubblico): senza l'impronta salvata lì non parte niente
                    if (PcTls.REMOTE_URL_PATTERN.matcher(verifyRemote).matches() && !verifyRemote.equalsIgnoreCase(mRemoteUrl)) {
                        mRemoteUrl = verifyRemote;
                        if (edtRemoteUrl != null) edtRemoteUrl.setText(mRemoteUrl);
                        savePreferences();
                    }
                    SharedPreferences.Editor ed = prefs.edit();
                    if (!mAuthToken.isEmpty()) ed.putString(PREF_AUTH_TOKEN, mAuthToken);
                    if (!pushSecret.isEmpty() && (mayReplace || prefs.getString(PREF_PUSH_SECRET, "").isEmpty()))
                        ed.putString(PREF_PUSH_SECRET, pushSecret);
                    // Il PC che ha accettato il PIN: da ora all'indirizzo di casa deve rispondere lui
                    String hostId = obj.optString("host_id", "");
                    if (!mAuthToken.isEmpty() && hostId.matches("[0-9a-f]{16}") && mayReplace) ed.putString(PREF_PC_HOST_ID, hostId);
                    boolean topicChanged = false;
                    if (pushTopic.matches("[A-Za-z0-9_-]{1,64}")
                            && (mayReplace || prefs.getString(ServerMonitorService.PREF_PUSH_TOPIC, "").isEmpty())
                            && !pushTopic.equals(prefs.getString(ServerMonitorService.PREF_PUSH_TOPIC, ""))) {
                        ed.putString(ServerMonitorService.PREF_PUSH_TOPIC, pushTopic);
                        ed.remove("ntfy_last_msg_id"); // l'ultimo ID appartiene al topic precedente
                        topicChanged = true;
                    }
                    ed.apply();
                    if (topicChanged) restartMonitorService();
                } catch (Exception ignored) {}
                finishAuth(onReady);
            }

            @Override
            public void onError(String error) {
                if (error != null && error.contains("PIN predefinito")) {
                    mDefaultPinBlockedUntil = System.currentTimeMillis() + 60000;
                    I18n.makeText(MainActivity.this, I18n.t("app.accesso_internet_bloccato_sul_pc_host"), Toast.LENGTH_LONG).show();
                } else if (error != null && error.contains("HTTP 429")) {
                    I18n.makeText(MainActivity.this, authLockMessage(error), Toast.LENGTH_LONG).show();
                } else if (error != null && (error.contains("401") || error.contains("403"))) {
                    // PIN cambiato sul PC: lo si ricorda come rifiutato, così non si ritenta da soli
                    authPrefs.edit().putString(PREF_ADMIN_PIN_REJECTED, triedPin).apply();
                    I18n.makeText(MainActivity.this, I18n.t("app.pin_salvato_nell_app_non_piu_quello_pc"), Toast.LENGTH_LONG).show();
                }
                finishAuth(onReady);
            }
        });
    }

    private void finishAuth(Runnable onReady) {
        mAuthInFlight = false;
        java.util.List<Runnable> waiting = new java.util.ArrayList<>(mAuthWaiters);
        mAuthWaiters.clear();
        if (onReady != null) onReady.run();
        for (Runnable r : waiting) r.run();
    }

    // Blocco del PC per troppi PIN errati: il PC dice quanto manca ("riprova tra 7 minuti e 12 secondi").
    // Si ricorda la scadenza e si restituisce il messaggio da mostrare.
    private String authLockMessage(String error) {
        long seconds = AUTH_LOCK_MS / 1000;
        java.util.regex.Matcher m = java.util.regex.Pattern.compile("\\\"retry_after\\\"\\s*:\\s*(\\d+)").matcher(error);
        if (m.find()) {
            try { seconds = Long.parseLong(m.group(1)); } catch (NumberFormatException ignored) {}
        }
        mAuthLockedUntil = System.currentTimeMillis() + seconds * 1000L;
        return authLockRemainingText();
    }

    private String authLockRemainingText() {
        long left = Math.max(1, (mAuthLockedUntil - System.currentTimeMillis() + 999) / 1000);
        long min = left / 60, sec = left % 60;
        String secs = I18n.t("app.durata_secondi", sec);
        String mins = I18n.t(min == 1 ? "app.durata_minuto" : "app.durata_minuti", min);
        String when = min > 0 ? (sec > 0 ? I18n.t("app.durata_minuti_e_secondi", mins, secs) : mins) : secs;
        return I18n.t("app.pin_bloccato_riprova_tra", when);
    }

    private void executeApiCall(final String endpoint, final String method, final String jsonBody, final ApiClient.ApiCallback callback) {
        executeApiCallInternal(endpoint, method, jsonBody, false, uiSafe(callback));
    }

    /** Risposta del PC arrivata quando la schermata è già chiusa (app chiusa, tema chiaro/scuro cambiato durante una
     *  richiesta): si ignora. Prima aprire una finestra in quel momento faceva chiudere l'app (BadTokenException). */
    private ApiClient.ApiCallback uiSafe(final ApiClient.ApiCallback callback) {
        return new ApiClient.ApiCallback() {
            @Override
            public void onSuccess(String response) {
                if (isFinishing() || isDestroyed()) return;
                try {
                    callback.onSuccess(response);
                } catch (android.view.WindowManager.BadTokenException e) {
                    android.util.Log.w("MainActivity", "Finestra non mostrata: schermata chiusa", e);
                }
            }

            @Override
            public void onError(String error) {
                if (isFinishing() || isDestroyed()) return;
                try {
                    callback.onError(error);
                } catch (android.view.WindowManager.BadTokenException e) {
                    android.util.Log.w("MainActivity", "Finestra non mostrata: schermata chiusa", e);
                }
            }
        };
    }

    private void executeApiCallInternal(final String endpoint, final String method, final String jsonBody, final boolean isRetry, final ApiClient.ApiCallback callback) {
        withVerifiedBase(new Runnable() {
            @Override
            public void run() {
                executeApiCallChecked(endpoint, method, jsonBody, isRetry, callback);
            }
        }, new Runnable() {
            @Override
            public void run() {
                callback.onError(I18n.t("app.pc_casa_non_riconosciuto"));
            }
        }, new Runnable() {
            @Override
            public void run() {
                // Il PC può aver cambiato indirizzo in casa: si rilegge l'indirizzo firmato da ntfy (senza mandare
                // token né PIN) e, se è cambiato, si riprova una volta. Prima ci pensava la richiesta fallita
                final String oldLocal = mLocalUrl;
                if (isRetry) {
                    callback.onError(I18n.t("app.pc_casa_non_risponde"));
                    return;
                }
                syncFromNtfyGateway(false, new Runnable() {
                    @Override
                    public void run() {
                        if (mLocalUrl != null && !mLocalUrl.equals(oldLocal)) {
                            executeApiCallInternal(endpoint, method, jsonBody, true, callback);
                        } else {
                            callback.onError(I18n.t("app.pc_casa_non_risponde"));
                        }
                    }
                });
            }
        });
    }

    private void executeApiCallChecked(final String endpoint, final String method, final String jsonBody, final boolean isRetry, final ApiClient.ApiCallback callback) {
        final String base = getEffectiveBaseUrl();
        ApiClient.call(base + endpoint, method, jsonBody, mAuthToken, new ApiClient.ApiCallback() {
            @Override
            public void onSuccess(String response) {
                callback.onSuccess(response);
            }

            @Override
            public void onError(final String error) {
                if (error != null && error.matches("(?s)HTTP 4\\d\\d:.*") && !error.startsWith("HTTP 401") && !error.startsWith("HTTP 403")) {
                    // Il PC ha risposto con un errore (es. "spegni prima il server"): è raggiungibile, quindi niente
                    // passaggio all'altro indirizzo; prima si cambiava indirizzo a ogni errore e si ripeteva la richiesta
                    callback.onError(error);
                    return;
                }
                if (!isRetry && error != null && (error.contains("401") || error.contains("403")) && !"/api/auth/verify".equals(endpoint)) {
                    // Token non valido o scaduto: azzera, autentica con PIN e riprova la richiesta
                    mAuthToken = "";
                    getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit().remove(PREF_AUTH_TOKEN).apply();
                    ensureAuthToken(new Runnable() {
                        @Override
                        public void run() {
                            executeApiCallInternal(endpoint, method, jsonBody, true, callback);
                        }
                    });
                    return;
                }

                if (mConnMode == 0 && !mIsUsingRemote && !mRemoteUrl.isEmpty()) {
                    // Failover da LAN all'indirizzo da internet (HTTPS)
                    mIsUsingRemote = true;
                    savePreferences();
                    updateRoutePillUI();
                    ApiClient.call(mRemoteUrl + endpoint, method, jsonBody, mAuthToken, new ApiClient.ApiCallback() {
                        @Override
                        public void onSuccess(String resp) {
                            callback.onSuccess(resp);
                        }

                        @Override
                        public void onError(String errRemote) {
                            if (!isRetry && errRemote != null && (errRemote.contains("401") || errRemote.contains("403")) && !"/api/auth/verify".equals(endpoint)) {
                                mAuthToken = "";
                                getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit().remove(PREF_AUTH_TOKEN).apply();
                                ensureAuthToken(new Runnable() {
                                    @Override
                                    public void run() {
                                        executeApiCallInternal(endpoint, method, jsonBody, true, callback);
                                    }
                                });
                                return;
                            }
                            // Anche l'endpoint remoto memorizzato ha fallito: sincronizza subito da ntfy
                            syncUrlsFromNtfy(false, new Runnable() {
                                @Override
                                public void run() {
                                    if (mRemoteUrl != null && !mRemoteUrl.isEmpty()) {
                                        ApiClient.call(mRemoteUrl + endpoint, method, jsonBody, mAuthToken, callback);
                                    } else {
                                        callback.onError(errRemote);
                                    }
                                }
                            });
                        }
                    });
                } else if (mConnMode == 0 && mIsUsingRemote && !mLocalUrl.isEmpty() && mLocalUrl.equals(mLocalVerifiedUrl)) {
                    // Remote fallito: prova fallback su LAN locale (solo se lì risponde il nostro PC: il token passa in chiaro;
                    // altrimenti ci pensa il controllo della rete, testLocalReachabilityQuick, a riconoscerlo e tornarci)
                    ApiClient.call(mLocalUrl + endpoint, method, jsonBody, mAuthToken, new ApiClient.ApiCallback() {
                        @Override
                        public void onSuccess(String resp) {
                            mIsUsingRemote = false;
                            savePreferences();
                            updateRoutePillUI();
                            callback.onSuccess(resp);
                        }

                        @Override
                        public void onError(String err2) {
                            if (!isRetry && err2 != null && (err2.contains("401") || err2.contains("403")) && !"/api/auth/verify".equals(endpoint)) {
                                mAuthToken = "";
                                getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit().remove(PREF_AUTH_TOKEN).apply();
                                ensureAuthToken(new Runnable() {
                                    @Override
                                    public void run() {
                                        executeApiCallInternal(endpoint, method, jsonBody, true, callback);
                                    }
                                });
                                return;
                            }
                            // Entrambi gli indirizzi non hanno risposto: sincronizza da ntfy per verificare se il tunnel è cambiato
                            syncUrlsFromNtfy(false, new Runnable() {
                                @Override
                                public void run() {
                                    if (mRemoteUrl != null && !mRemoteUrl.isEmpty() && !mRemoteUrl.equalsIgnoreCase(base)) {
                                        ApiClient.call(mRemoteUrl + endpoint, method, jsonBody, mAuthToken, callback);
                                    } else {
                                        callback.onError(error);
                                    }
                                }
                            });
                        }
                    });
                } else {
                    // Modalità forzata o nessun endpoint alternativo: prova comunque un recupero da ntfy
                    syncUrlsFromNtfy(false, new Runnable() {
                        @Override
                        public void run() {
                            String freshBase = getEffectiveBaseUrl();
                            if (freshBase != null && !freshBase.equalsIgnoreCase(base)) {
                                ApiClient.call(freshBase + endpoint, method, jsonBody, mAuthToken, callback);
                            } else {
                                callback.onError(error);
                            }
                        }
                    });
                }
            }
        });
    }

    private void setupUrlUpdateReceiver() {
        mUrlUpdateReceiver = new BroadcastReceiver() {
            @Override
            public void onReceive(Context context, Intent intent) {
                if ("com.minecraftserver.manager.URL_UPDATED".equals(intent.getAction())) {
                    // Gli indirizzi si rileggono da quelli salvati dal servizio (dopo il controllo della firma), mai dal
                    // messaggio: fino ad Android 12 questo avviso lo può mandare qualunque app del telefono
                    SharedPreferences saved = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
                    boolean updated = false;
                    String newRemote = saved.getString("remote_url", "");
                    if (newRemote != null && !newRemote.trim().isEmpty() && !newRemote.equalsIgnoreCase(mRemoteUrl)
                            && PcTls.REMOTE_URL_PATTERN.matcher(newRemote.trim()).matches()) {
                        mRemoteUrl = newRemote.trim();
                        if (edtRemoteUrl != null) edtRemoteUrl.setText(mRemoteUrl);
                        updated = true;
                    }
                    {
                        String newLocal = saved.getString("local_url", "");
                        if (newLocal != null && !newLocal.trim().isEmpty() && !newLocal.equalsIgnoreCase(mLocalUrl)) {
                            mLocalUrl = newLocal.trim();
                            if (edtLocalUrl != null) edtLocalUrl.setText(mLocalUrl);
                            updated = true;
                        }
                    }
                    if (updated) {
                        savePreferences();
                        updateRoutePillUI();
                        fetchStatus();
                    }
                }
            }
        };
        IntentFilter filter = new IntentFilter("com.minecraftserver.manager.URL_UPDATED");
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(mUrlUpdateReceiver, filter, Context.RECEIVER_NOT_EXPORTED);
        } else {
            registerReceiver(mUrlUpdateReceiver, filter);
        }
    }

    private void initNetworkRouteMonitor() {
        try {
            ConnectivityManager cm = (ConnectivityManager) getSystemService(Context.CONNECTIVITY_SERVICE);
            if (cm == null) return;
            NetworkRequest request = new NetworkRequest.Builder()
                .addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
                .build();
            mNetworkCallback = new ConnectivityManager.NetworkCallback() {
                @Override
                public void onCapabilitiesChanged(Network network, NetworkCapabilities caps) {
                    // Arriva spesso anche senza cambiare rete (es. velocità del Wi-Fi): il PC di casa va riconosciuto
                    // di nuovo solo su una rete diversa
                    if (!network.equals(mLastNetwork)) {
                        mLastNetwork = network;
                        mLocalVerifiedUrl = "";
                    }
                    evaluateNetworkRoute(caps);
                }
                @Override
                public void onLost(Network network) {
                    mLastNetwork = null;
                    mLocalVerifiedUrl = "";
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            testNetworkFailoverAuto();
                        }
                    });
                }
            };
            cm.registerNetworkCallback(request, mNetworkCallback);

            Network activeNet = cm.getActiveNetwork();
            if (activeNet != null) {
                evaluateNetworkRoute(cm.getNetworkCapabilities(activeNet));
            }
        } catch (Exception ignored) {}
    }

    private void evaluateNetworkRoute(NetworkCapabilities caps) {
        if (mConnMode != 0) return; // Modalità manuale rispettata

        final boolean hasWifi = caps != null && caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI);
        final boolean hasCellular = caps != null && caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR);

        if (hasCellular && !hasWifi) {
            // Su rete dati mobile 4G/5G: la LAN privata non è raggiungibile
            runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    if (!mIsUsingRemote) {
                        mIsUsingRemote = true;
                        savePreferences();
                        updateRoutePillUI();
                        fetchStatus();
                    }
                }
            });
        } else if (hasWifi) {
            // Su rete Wi-Fi: verifica veloce della LAN domestica
            testLocalReachabilityQuick();
        }
    }

    private void testLocalReachabilityQuick() {
        if (mConnMode != 0 || mLocalUrl == null || mLocalUrl.isEmpty()) return;
        final String local = mLocalUrl;
        // Senza token: su un Wi-Fi esterno allo stesso indirizzo può rispondere un altro dispositivo
        ApiClient.checkHost(this, local, 1500, new ApiClient.HostCallback() {
            @Override
            public void onResult(int result) {
                boolean home = result == ApiClient.HOST_SAME;
                if (home) mLocalVerifiedUrl = local;
                else if (local.equals(mLocalVerifiedUrl)) mLocalVerifiedUrl = "";
                // Wi-Fi esterno (non domestico) o altro dispositivo all'indirizzo di casa: usa l'indirizzo da internet
                if (home == mIsUsingRemote) {
                    mIsUsingRemote = !home;
                    savePreferences();
                    updateRoutePillUI();
                    fetchStatus();
                }
            }
        });
    }

    private long mLastHomeRouteCheck = 0;

    /**
     * Passata a internet perché una richiesta all'indirizzo di casa non ha risposto (es. PC occupato un momento), l'app
     * ci restava finché il telefono non cambiava rete. Sul Wi-Fi ogni 30 s si riprova l'indirizzo di casa.
     */
    private void recheckHomeRouteIfNeeded() {
        if (mConnMode != 0 || !mIsUsingRemote) return;
        long now = System.currentTimeMillis();
        if (now - mLastHomeRouteCheck < 30000L) return;
        mLastHomeRouteCheck = now;
        try {
            ConnectivityManager cm = (ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
            Network net = cm != null ? cm.getActiveNetwork() : null;
            NetworkCapabilities caps = net != null ? cm.getNetworkCapabilities(net) : null;
            if (caps != null && caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) testLocalReachabilityQuick();
        } catch (Exception ignored) {}
    }

    private void testNetworkFailoverAuto() {
        if (mConnMode != 0) return;
        mIsUsingRemote = !mIsUsingRemote;
        savePreferences();
        updateRoutePillUI();
        fetchStatus();
    }

    private void syncUrlsFromNtfy(final boolean showToast, final Runnable onDone) {
        if (showToast) {
            I18n.makeText(this, I18n.t("app.sincronizzazione_indirizzi_corso"), Toast.LENGTH_SHORT).show();
        }

        // 1. Tenta la sincronizzazione diretta con il server attivo tramite /api/network
        // (chiamata diretta, non executeApiCall: il suo errore richiamerebbe questa funzione all'infinito)
        // Token solo se l'indirizzo è HTTPS o è stato riconosciuto come il nostro PC (dalla rete di casa non serve)
        final String syncBase = getEffectiveBaseUrl();
        boolean unverifiedLocal = syncBase != null && syncBase.equals(mLocalUrl) && !syncBase.startsWith("https://")
            && !syncBase.equals(mLocalVerifiedUrl);
        // Indirizzi e impronta del certificato si accettano solo dal PC riconosciuto: in HTTPS (impronta già verificata)
        // o all'indirizzo di casa con lo stesso identificativo del login. Prima li poteva cambiare chiunque rispondesse
        // in chiaro all'indirizzo di casa (es. su un Wi-Fi esterno), e l'app si sarebbe poi fidata del suo certificato
        final boolean trustedSource = syncBase != null && (syncBase.startsWith("https://")
            || (syncBase.equals(mLocalVerifiedUrl)
                && !getSharedPreferences(PREFS_NAME, MODE_PRIVATE).getString(PREF_PC_HOST_ID, "").isEmpty()));
        ApiClient.call(syncBase + "/api/network", "GET", null, unverifiedLocal ? "" : mAuthToken, new ApiClient.ApiCallback() {
            @Override
            public void onSuccess(final String netResp) {
                try {
                    if (!trustedSource) throw new IllegalStateException("PC non riconosciuto");   // si passa a ntfy
                    JSONObject netObj = new JSONObject(netResp);
                    String lanUrl = netObj.optString("lan_url", "");
                    if (lanUrl.isEmpty()) {
                        String localIp = netObj.optString("local_ip", "");
                        int port = netObj.optInt("web_port", 8080);
                        if (!localIp.isEmpty()) lanUrl = "http://" + localIp + ":" + port;
                    }
                    JSONObject remObj = netObj.optJSONObject("remote");
                    String tunUrl = (remObj != null) ? remObj.optString("url", "") : "";
                    // Impronta del certificato: sostituita solo in HTTPS o al primo accesso, in chiaro solo se manca
                    if (remObj != null) {
                        if (mayReplaceTrustData(syncBase)) PcTls.saveFingerprint(MainActivity.this, remObj.optString("fingerprint", ""));
                        else PcTls.saveFingerprintIfMissing(MainActivity.this, remObj.optString("fingerprint", ""));
                    }

                    final String finalLan = lanUrl.replaceAll("/+$", "");
                    final String finalTun = tunUrl.replaceAll("/+$", "");

                    boolean changed = false;
                    if (!finalLan.isEmpty() && !finalLan.equalsIgnoreCase(mLocalUrl)) {
                        mLocalUrl = finalLan;
                        if (edtLocalUrl != null) edtLocalUrl.setText(mLocalUrl);
                        changed = true;
                    }
                    if (!finalTun.isEmpty() && PcTls.REMOTE_URL_PATTERN.matcher(finalTun).matches() && !finalTun.equalsIgnoreCase(mRemoteUrl)) {
                        mRemoteUrl = finalTun;
                        if (edtRemoteUrl != null) edtRemoteUrl.setText(mRemoteUrl);
                        changed = true;
                    }

                    if (changed) {
                        savePreferences();
                        updateRoutePillUI();
                        runOnUiThread(new Runnable() {
                            @Override
                            public void run() {
                                if (showToast) {
                                    I18n.makeText(MainActivity.this, I18n.t("app.indirizzo_lan_sincronizzato_dal_pc", mLocalUrl), Toast.LENGTH_LONG).show();
                                }
                                if (onDone != null) onDone.run();
                            }
                        });
                        return; // Sincronizzazione diretta dal server completata con successo!
                    }
                } catch (Exception ignored) {}

                // Se non ci sono stati aggiornamenti diretti dal server, interroga il gateway ntfy
                syncFromNtfyGateway(showToast, onDone);
            }

            @Override
            public void onError(String error) {
                // Fallback su gateway ntfy se il server non risponde
                syncFromNtfyGateway(showToast, onDone);
            }
        });
    }

    private void syncFromNtfyGateway(final boolean showToast, final Runnable onDone) {
        new Thread(new Runnable() {
            @Override
            public void run() {
                HttpURLConnection conn = null;
                String discoveredRemote = null;
                String discoveredLocal = null;
                try {
                    String topic = ServerMonitorService.getPushTopic(MainActivity.this);
                    if (topic.isEmpty()) throw new java.io.IOException("Nessun topic ntfy: login sul PC non ancora fatto");
                    if (!ServerMonitorService.tryAcquireNtfyPoll()) throw new java.io.IOException("Lettura ntfy già fatta nell'ultimo minuto");
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
                                if (!"message".equals(msgObj.optString("event", ""))) continue;

                                String directLocal = "";
                                String directRemote = "";
                                String sig = "";
                                String fp = "";

                                String message = msgObj.optString("message", "");
                                String title = msgObj.optString("title", "");
                                String click = msgObj.optString("click", "");
                                StringBuilder sb = new StringBuilder();
                                if (!click.isEmpty()) sb.append(" ").append(click);
                                if (!message.isEmpty()) sb.append(" ").append(message);
                                if (!title.isEmpty()) sb.append(" ").append(title);
                                JSONArray actions = msgObj.optJSONArray("actions");
                                if (actions != null) {
                                    for (int k = 0; k < actions.length(); k++) {
                                        JSONObject act = actions.optJSONObject(k);
                                        if (act != null && act.has("url")) sb.append(" ").append(act.optString("url", ""));
                                    }
                                }
                                String combined = sb.toString();

                                Matcher mRem = PcTls.REMOTE_URL_PATTERN.matcher(combined);
                                if (mRem.find()) directRemote = mRem.group().replaceAll("/+$", "");
                                Matcher mLoc = Pattern.compile("http://(?:192\\.168|10\\.|172\\.(?:1[6-9]|2[0-9]|3[01]))\\.[0-9.]+:[0-9]+").matcher(combined);
                                if (mLoc.find()) directLocal = mLoc.group().replaceAll("/+$", "");
                                Matcher mSig = Pattern.compile("sig=([0-9a-fA-F]{64})").matcher(combined);
                                if (mSig.find()) sig = mSig.group(1);
                                Matcher mFp = Pattern.compile("fp=([0-9a-fA-F]{0,64})").matcher(combined);
                                if (mFp.find()) fp = mFp.group(1);

                                String secret = getSharedPreferences(PREFS_NAME, MODE_PRIVATE).getString(PREF_PUSH_SECRET, "");
                                // Nessun segreto, messaggio non firmato, firma errata o più vecchio dell'ultimo accettato: scartato
                                if (!ServerMonitorService.acceptAddressMessage(MainActivity.this, secret, directRemote,
                                        directLocal, fp, sig, combined)) {
                                    continue;
                                }
                                if (!directLocal.isEmpty()) {
                                    discoveredLocal = directLocal.replaceAll("/+$", "");
                                }
                                PcTls.saveFingerprint(MainActivity.this, fp); // solo dopo la verifica della firma
                                if (!directRemote.isEmpty() && PcTls.REMOTE_URL_PATTERN.matcher(directRemote).matches()) {
                                    discoveredRemote = directRemote.replaceAll("/+$", "");
                                }
                            } catch (Exception ignored) {}
                        }
                        reader.close();
                    }
                } catch (Exception ignored) {
                } finally {
                    if (conn != null) conn.disconnect();
                }

                final boolean didUpdateRemote = (discoveredRemote != null && !discoveredRemote.isEmpty() && !discoveredRemote.equalsIgnoreCase(mRemoteUrl));
                final boolean didUpdateLocal = (discoveredLocal != null && !discoveredLocal.isEmpty() && !discoveredLocal.equalsIgnoreCase(mLocalUrl));
                final String finalRemote = discoveredRemote;
                final String finalLocal = discoveredLocal;

                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        boolean changed = false;
                        if (didUpdateRemote) {
                            mRemoteUrl = finalRemote;
                            if (edtRemoteUrl != null) edtRemoteUrl.setText(mRemoteUrl);
                            changed = true;
                        }
                        if (didUpdateLocal) {
                            mLocalUrl = finalLocal;
                            if (edtLocalUrl != null) edtLocalUrl.setText(mLocalUrl);
                            changed = true;
                        }
                        if (changed) {
                            savePreferences();
                            updateRoutePillUI();
                            if (showToast) {
                                I18n.makeText(MainActivity.this, I18n.t("app.indirizzo_aggiornato_ntfy_lan_remoto", mLocalUrl, mRemoteUrl), Toast.LENGTH_LONG).show();
                            }
                            fetchStatus();
                        } else if (showToast) {
                            I18n.makeText(MainActivity.this, I18n.t("app.indirizzi_gia_allineati_lan", mLocalUrl), Toast.LENGTH_SHORT).show();
                        }
                        if (onDone != null) {
                            onDone.run();
                        }
                    }
                });
            }
        }).start();
    }

    // ================= STATUS & DASHBOARD POLLING =================

    private void fetchStatus() {
        executeApiCall("/api/status", "GET", null, new ApiClient.ApiCallback() {
            @Override
            public void onSuccess(String response) {
                // Il PC risponde ma il login di questa apertura non è ancora riuscito (es. app aperta fuori casa): riprova, max 1 volta al minuto
                long now = System.currentTimeMillis();
                if (!mPushConfigSynced && now - mLastPushSyncAttempt > 60000) {
                    mLastPushSyncAttempt = now;
                    ensureAuthToken(null);
                }
                fetchInstancesIfDue();
                try {
                    JSONObject obj = new JSONObject(response);
                    String state = obj.optString("state", "OFFLINE");
                    mServerState = state;
                    if (mBackupAfterStop && ("STOPPED".equalsIgnoreCase(state) || "OFFLINE".equalsIgnoreCase(state))) {
                        mBackupAfterStop = false;
                        startBackupAction();
                    } else if ("CRASHED".equalsIgnoreCase(state) || "ONLINE".equalsIgnoreCase(state)) {
                        mBackupAfterStop = false;
                    }

                    if ("ONLINE".equalsIgnoreCase(state)) {
                        pillServerStatus.setText("Online");
                        pillServerStatus.setBackgroundResource(R.drawable.pill_online);
                        pillServerStatus.setTextColor(Color.parseColor("#a5d36f"));

                        pillServerStatus2.setText("Online");
                        pillServerStatus2.setBackgroundResource(R.drawable.pill_online);
                        pillServerStatus2.setTextColor(Color.parseColor("#a5d36f"));

                        txtMainStatus.setText(I18n.t("app.server_attivo"));
                        txtMainStatus.setTextColor(Color.parseColor("#a5d36f"));

                        btnActionStart.setEnabled(false);
                        btnActionStart.setAlpha(0.4f);
                        btnActionStop.setEnabled(true);
                        btnActionStop.setAlpha(1.0f);
                        btnActionRestart.setEnabled(true);
                        btnActionRestart.setAlpha(1.0f);
                    } else if ("STARTING".equalsIgnoreCase(state)) {
                        pillServerStatus.setText(I18n.t("app.avvio"));
                        pillServerStatus.setBackgroundResource(R.drawable.pill_pending);
                        pillServerStatus.setTextColor(Color.parseColor("#e3b35a"));

                        pillServerStatus2.setText(I18n.t("app.avvio"));
                        pillServerStatus2.setBackgroundResource(R.drawable.pill_pending);
                        pillServerStatus2.setTextColor(Color.parseColor("#e3b35a"));

                        txtMainStatus.setText(I18n.t("app.avvio_server_corso"));
                        txtMainStatus.setTextColor(Color.parseColor("#e3b35a"));

                        btnActionStart.setEnabled(false);
                        btnActionStart.setAlpha(0.4f);
                        btnActionStop.setEnabled(true);
                        btnActionStop.setAlpha(1.0f);
                        btnActionRestart.setEnabled(false);
                        btnActionRestart.setAlpha(0.4f);
                    } else if ("STOPPING".equalsIgnoreCase(state) || "RESTARTING".equalsIgnoreCase(state)) {
                        String label = "STOPPING".equalsIgnoreCase(state) ? I18n.t("app.arresto") : I18n.t("app.riavvio");
                        String desc = "STOPPING".equalsIgnoreCase(state) ? I18n.t("app.arresto_server_corso") : I18n.t("app.riavvio_server_corso");
                        pillServerStatus.setText(label);
                        pillServerStatus.setBackgroundResource(R.drawable.pill_pending);
                        pillServerStatus.setTextColor(Color.parseColor("#e3b35a"));

                        pillServerStatus2.setText(label);
                        pillServerStatus2.setBackgroundResource(R.drawable.pill_pending);
                        pillServerStatus2.setTextColor(Color.parseColor("#e3b35a"));

                        txtMainStatus.setText(desc);
                        txtMainStatus.setTextColor(Color.parseColor("#e3b35a"));

                        btnActionStart.setEnabled(false);
                        btnActionStart.setAlpha(0.4f);
                        btnActionStop.setEnabled(false);
                        btnActionStop.setAlpha(0.4f);
                        btnActionRestart.setEnabled(false);
                        btnActionRestart.setAlpha(0.4f);
                    } else if ("CRASHED".equalsIgnoreCase(state)) {
                        pillServerStatus.setText("Crash");
                        pillServerStatus.setBackgroundResource(R.drawable.pill_offline);
                        pillServerStatus.setTextColor(Color.parseColor("#e5566f"));

                        pillServerStatus2.setText("Crash");
                        pillServerStatus2.setBackgroundResource(R.drawable.pill_offline);
                        pillServerStatus2.setTextColor(Color.parseColor("#e5566f"));

                        txtMainStatus.setText(I18n.t("app.server_si_chiuso_errore"));
                        txtMainStatus.setTextColor(Color.parseColor("#e5566f"));

                        btnActionStart.setEnabled(true);
                        btnActionStart.setAlpha(1.0f);
                        btnActionStop.setEnabled(false);
                        btnActionStop.setAlpha(0.4f);
                        btnActionRestart.setEnabled(true);
                        btnActionRestart.setAlpha(1.0f);
                    } else {
                        pillServerStatus.setText(I18n.t("ui.offline"));
                        pillServerStatus.setBackgroundResource(R.drawable.pill_offline);
                        pillServerStatus.setTextColor(Color.parseColor("#f0788c"));

                        pillServerStatus2.setText(I18n.t("ui.offline"));
                        pillServerStatus2.setBackgroundResource(R.drawable.pill_offline);
                        pillServerStatus2.setTextColor(Color.parseColor("#f0788c"));

                        txtMainStatus.setText(I18n.t("ui.server_spento"));
                        txtMainStatus.setTextColor(Color.parseColor("#f0788c"));

                        btnActionStart.setEnabled(true);
                        btnActionStart.setAlpha(1.0f);
                        btnActionStop.setEnabled(false);
                        btnActionStop.setAlpha(0.4f);
                        btnActionRestart.setEnabled(false);
                        btnActionRestart.setAlpha(0.4f);
                    }

                    String uptime = obj.optString("uptime_formatted", obj.optString("uptime", "--"));
                    txtUptime.setText("Uptime: " + uptime);

                    JSONObject metrics = obj.optJSONObject("metrics");
                    if (metrics != null) {
                        double mcRamGb = metrics.optDouble("mc_ram_gb", 0.0);
                        double mcRamMax = metrics.optDouble("mc_ram_max_gb", 16.0);
                        int ramPct = (mcRamMax > 0 && mcRamGb > 0) ? (int) Math.min(100, Math.round((mcRamGb / mcRamMax) * 100)) : 0;
                        txtRamValue.setText(String.format(java.util.Locale.US, "%.1f / %.1f GB (%d%%)", mcRamGb, mcRamMax, ramPct));
                        progBarRam.setProgress(ramPct);

                        double mcCpu = metrics.optDouble("mc_cpu", 0.0);
                        double hostCpu = metrics.optDouble("host_cpu", 0.0);
                        int cpuPct = (int) Math.round(mcCpu > 0 ? mcCpu : hostCpu);
                        txtCpuValue.setText(cpuPct + " %");
                        progBarCpu.setProgress(Math.min(100, Math.max(0, cpuPct)));

                        int port = obj.optInt("server_port", obj.optInt("port", 32769));
                        JSONObject slpObj = obj.optJSONObject("slp");
                        double ping = metrics.optDouble("ping_ms", slpObj != null ? slpObj.optDouble("latency_ms", 0) : 0);
                        boolean pingValid = ping > 0 && "ONLINE".equalsIgnoreCase(obj.optString("state", ""));
                        txtServerAddress.setText(pingValid
                                ? String.format(java.util.Locale.US, "Porta MC: %d | Ping: %.1f ms", port, ping)
                                : String.format(java.util.Locale.US, "Porta MC: %d | Ping: --", port));
                        if (slpObj != null) {
                            String runningVer = slpObj.optString("version", "");
                            if (!runningVer.isEmpty() && mCurrentSectionIndex == 0 && txtHeaderSubtitle != null) {
                                txtHeaderSubtitle.setText(runningVer);
                            }
                        }
                    }

                    // Scheduled restart timer parsing
                    JSONObject restartObj = obj.optJSONObject("scheduled_restart");
                    if (restartObj != null) {
                        String formatted = restartObj.optString("remaining_formatted", "");
                        if (formatted.isEmpty()) {
                            formatted = restartObj.optString("formatted", "04h 00m 00s");
                        }
                        double progressPct = restartObj.optDouble("progress_percent", -1.0);
                        if (progressPct < 0) {
                            progressPct = restartObj.optDouble("progress_pct", 0.0);
                        }

                        if (!"ONLINE".equalsIgnoreCase(mServerState)) {
                            txtRestartCountdown.setText(I18n.t("app.pausa_server_spento"));
                            txtRestartCountdown.setTextColor(Color.parseColor("#9a9ca2"));
                            progBarRestart.setProgress(0);
                        } else {
                            txtRestartCountdown.setText(formatted);
                            txtRestartCountdown.setTextColor(Color.parseColor("#9ccc65"));
                            progBarRestart.setProgress((int) Math.round(progressPct));
                        }
                    }

                    // Players count
                    JSONObject pObj = obj.optJSONObject("players");
                    int pOnline = pObj != null ? pObj.optInt("count", 0) : obj.optInt("players_online", obj.optInt("players_count", 0));
                    int pMax = pObj != null ? pObj.optInt("max", 20) : obj.optInt("players_max", 20);
                    txtPlayersOverview.setText(I18n.t("app.giocatori_connessi", pOnline, pMax));
                    txtPlayersTitleCount.setText(I18n.t("app.giocatori_online", pOnline, pMax));

                    JSONArray playersArr = new JSONArray();
                    JSONArray rawList = pObj != null ? pObj.optJSONArray("list") : obj.optJSONArray("players");
                    if (rawList != null) {
                        for (int pi = 0; pi < rawList.length(); pi++) {
                            Object it = rawList.opt(pi);
                            playersArr.put(it instanceof JSONObject ? ((JSONObject) it).optString("name", "") : String.valueOf(it));
                        }
                    }
                    if (txtPlayerList != null) {
                        if (playersArr.length() > 0) {
                            StringBuilder sbPlayers = new StringBuilder("Online: ");
                            for (int pi = 0; pi < playersArr.length(); pi++) {
                                if (pi > 0) sbPlayers.append(", ");
                                sbPlayers.append(playersArr.optString(pi));
                            }
                            txtPlayerList.setText(sbPlayers.toString());
                            txtPlayerList.setTextColor(Color.parseColor("#9ccc65"));
                        } else {
                            txtPlayerList.setText(I18n.t("ui.nessun_giocatore_connesso_momento"));
                            txtPlayerList.setTextColor(Color.parseColor("#9a9ca2"));
                        }
                    }

                    double tps = obj.optDouble("tps", 20.0);
                    if (pillTps != null) {
                        pillTps.setText(String.format(java.util.Locale.US, "%.1f TPS", tps));
                        // Pillola e testo dello stesso tono; a server spento resta neutra (0 TPS non è un allarme)
                        if (!"ONLINE".equalsIgnoreCase(mServerState)) {
                            pillTps.setBackgroundResource(R.drawable.pill_route_local);
                            pillTps.setTextColor(Color.parseColor("#c4c4c8"));
                        } else if (tps >= 18.0) {
                            pillTps.setBackgroundResource(R.drawable.pill_online);
                            pillTps.setTextColor(Color.parseColor("#a5d36f"));
                        } else if (tps >= 14.0) {
                            pillTps.setBackgroundResource(R.drawable.pill_pending);
                            pillTps.setTextColor(Color.parseColor("#e3b35a"));
                        } else {
                            pillTps.setBackgroundResource(R.drawable.pill_offline);
                            pillTps.setTextColor(Color.parseColor("#f0788c"));
                        }
                    }

                    JSONObject ecoObj = obj.optJSONObject("eco_mode");
                    if (ecoObj != null) {
                        boolean ecoOn = ecoObj.optBoolean("enabled", false);
                        int ecoMins = ecoObj.optInt("minutes", 45);
                        if (chkEcoMode != null) chkEcoMode.setChecked(ecoOn);
                        setEcoMinutesUI(ecoMins);
                        if (txtEcoModeStatusBadge != null) {
                            txtEcoModeStatusBadge.setText(ecoOn ? I18n.t("app.attiva_m", ecoMins) : I18n.t("ui.disattiva"));
                            txtEcoModeStatusBadge.setTextColor(Color.parseColor(ecoOn ? "#a5d36f" : "#9a9ca2"));
                        }
                    }

                    JSONObject lagObj = obj.optJSONObject("anti_lag");
                    if (lagObj != null) {
                        boolean lagOn = lagObj.optBoolean("enabled", true);
                        boolean autoClean = lagObj.optBoolean("auto_clean", true);
                        if (chkAntiLagEnabled != null) chkAntiLagEnabled.setChecked(lagOn);
                        if (chkAntiLagAutoClean != null) chkAntiLagAutoClean.setChecked(autoClean);
                        if (txtAntiLagStatusBadge != null) {
                            txtAntiLagStatusBadge.setText(lagOn ? I18n.t("ui.attivo") : I18n.t("app.disattivo"));
                            txtAntiLagStatusBadge.setTextColor(Color.parseColor(lagOn ? "#a5d36f" : "#9a9ca2"));
                        }
                    }

                } catch (Exception ignored) {}
            }

            @Override
            public void onError(String error) {
                pillServerStatus.setText(I18n.t("app.errore"));
                pillServerStatus.setBackgroundResource(R.drawable.pill_offline);
                pillServerStatus.setTextColor(Color.parseColor("#f0788c"));

                pillServerStatus2.setText(I18n.t("app.errore"));
                pillServerStatus2.setBackgroundResource(R.drawable.pill_offline);
                pillServerStatus2.setTextColor(Color.parseColor("#f0788c"));

                txtMainStatus.setText(I18n.t("app.connessione_non_riuscita"));
                txtMainStatus.setTextColor(Color.parseColor("#f0788c"));

                btnActionStart.setEnabled(false);
                btnActionStart.setAlpha(0.4f);
                btnActionStop.setEnabled(false);
                btnActionStop.setAlpha(0.4f);
                btnActionRestart.setEnabled(false);
                btnActionRestart.setAlpha(0.4f);
            }
        });
    }

    // ================= PLAYERS & WHITELIST =================

    private void updateWhitelistToggleUI() {
        if (btnWhitelistToggle != null) {
            btnWhitelistToggle.setText(mIsWhitelistEnabled ? I18n.t("app.stato_on") : I18n.t("app.stato_off"));
            btnWhitelistToggle.setTextColor(mIsWhitelistEnabled ? Color.parseColor("#a5d36f") : Color.parseColor("#9a9ca2"));
            btnWhitelistToggle.setBackgroundResource(mIsWhitelistEnabled ? R.drawable.btn_green : R.drawable.btn_slate);
        }
    }

    private void fetchPlayersAndWhitelist() {
        executeApiCall("/api/status", "GET", null, new ApiClient.ApiCallback() {
            @Override
            public void onSuccess(String response) {
                try {
                    JSONObject obj = new JSONObject(response);
                    JSONObject pObj = obj.optJSONObject("players");
                    JSONArray players = new JSONArray();
                    JSONArray rawList = pObj != null ? pObj.optJSONArray("list") : obj.optJSONArray("players");
                    if (rawList != null) {
                        for (int i = 0; i < rawList.length(); i++) {
                            Object it = rawList.opt(i);
                            players.put(it instanceof JSONObject ? ((JSONObject) it).optString("name", "") : String.valueOf(it));
                        }
                    }
                    layoutPlayersList.removeAllViews();

                    if (players != null && players.length() > 0) {
                        txtPlayersEmpty.setVisibility(View.GONE);
                        for (int i = 0; i < players.length(); i++) {
                            final String pName = players.getString(i);
                            LinearLayout row = new LinearLayout(MainActivity.this);
                            row.setOrientation(LinearLayout.HORIZONTAL);
                            row.setPadding(12, 12, 12, 12);
                            row.setBackgroundResource(R.drawable.chip_player_bg);
                            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
                            lp.setMargins(0, 0, 0, 8);
                            row.setLayoutParams(lp);

                            // Avatar skin giocatore Minecraft reale caricato asincronamente
                            final ImageView imgAvatar = new ImageView(MainActivity.this);
                            LinearLayout.LayoutParams imgLp = new LinearLayout.LayoutParams(dpToPx(30), dpToPx(30));
                            imgLp.setMargins(0, 0, dpToPx(10), 0);
                            imgAvatar.setLayoutParams(imgLp);
                            imgAvatar.setImageResource(R.drawable.ic_launcher);
                            row.addView(imgAvatar);

                            final String targetPlayerName = pName;
                            new Thread(new Runnable() {
                                @Override
                                public void run() {
                                    try {
                                        URL u = new URL("https://mc-heads.net/avatar/" + targetPlayerName + "/64");
                                        HttpURLConnection c = (HttpURLConnection) u.openConnection();
                                        c.setConnectTimeout(2500);
                                        c.setReadTimeout(2500);
                                        c.setInstanceFollowRedirects(true);
                                        if (c.getResponseCode() == 200) {
                                            java.io.InputStream is = c.getInputStream();
                                            final android.graphics.Bitmap bmp = android.graphics.BitmapFactory.decodeStream(is);
                                            is.close();
                                            if (bmp != null) {
                                                runOnUiThread(new Runnable() {
                                                    @Override
                                                    public void run() {
                                                        imgAvatar.setImageBitmap(bmp);
                                                    }
                                                });
                                            }
                                        }
                                        c.disconnect();
                                    } catch (Exception ignored) {}
                                }
                            }).start();

                            TextView tv = new TextView(MainActivity.this);
                            tv.setText(pName);
                            tv.setTextColor(Color.parseColor("#f4f4f5"));
                            tv.setTextSize(14);
                            tv.setTypeface(null, android.graphics.Typeface.BOLD);
                            LinearLayout.LayoutParams tvLp = new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1.0f);
                            tv.setLayoutParams(tvLp);
                            row.addView(tv);

                            Button btnAction = new Button(MainActivity.this);
                            btnAction.setText((I18n.t("app.azioni") + " "));
                            btnAction.setTextSize(10);
                            btnAction.setTextColor(Color.parseColor("#9ccc65"));
                            btnAction.setBackgroundResource(R.drawable.btn_slate);
                            btnAction.setOnClickListener(new View.OnClickListener() {
                                @Override
                                public void onClick(View v) {
                                    showPlayerActionDialog(pName);
                                }
                            });
                            row.addView(btnAction);

                            layoutPlayersList.addView(row);
                        }
                    } else {
                        txtPlayersEmpty.setVisibility(View.VISIBLE);
                    }
                } catch (Exception ignored) {}
            }
            @Override public void onError(String error) {}
        });

        // Whitelist list
        executeApiCall("/api/whitelist", "GET", null, new ApiClient.ApiCallback() {
            @Override
            public void onSuccess(String response) {
                try {
                    JSONObject obj = new JSONObject(response);
                    if (obj.has("enabled")) {
                        mIsWhitelistEnabled = obj.optBoolean("enabled", mIsWhitelistEnabled);
                        updateWhitelistToggleUI();
                    }
                    JSONArray wl = obj.optJSONArray("whitelist");
                    layoutWhitelistList.removeAllViews();

                    int count = (wl != null) ? wl.length() : 0;
                    txtWhitelistCount.setText(I18n.t("app.giocatori_abilitati_nella_whitelist", count));

                    if (wl != null && wl.length() > 0) {
                        for (int i = 0; i < wl.length(); i++) {
                            JSONObject item = wl.optJSONObject(i);
                            final String name = (item != null) ? item.optString("name", "") : wl.getString(i);
                            if (name.isEmpty()) continue;

                            LinearLayout row = new LinearLayout(MainActivity.this);
                            row.setOrientation(LinearLayout.HORIZONTAL);
                            row.setPadding(8, 8, 8, 8);
                            row.setBackgroundResource(R.drawable.chip_bg);
                            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
                            lp.setMargins(0, 0, 0, 6);
                            row.setLayoutParams(lp);

                            TextView tv = new TextView(MainActivity.this);
                            tv.setText("" + name);
                            tv.setTextColor(Color.parseColor("#c4c4c8"));
                            tv.setTextSize(12);
                            LinearLayout.LayoutParams tvLp = new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1.0f);
                            tv.setLayoutParams(tvLp);
                            row.addView(tv);

                            Button btnDel = new Button(MainActivity.this);
                            btnDel.setText(I18n.t("app.rimuovi"));
                            btnDel.setTextSize(9);
                            btnDel.setTextColor(Color.parseColor("#f0788c"));
                            btnDel.setBackgroundResource(R.drawable.btn_slate);
                            btnDel.setOnClickListener(new View.OnClickListener() {
                                @Override
                                public void onClick(View v) {
                                    removePlayerFromWhitelist(name);
                                }
                            });
                            row.addView(btnDel);

                            layoutWhitelistList.addView(row);
                        }
                    } else {
                        TextView emptyWl = new TextView(MainActivity.this);
                        emptyWl.setText(I18n.t("app.nessun_giocatore_registrato_whitelist"));
                        emptyWl.setTextColor(Color.parseColor("#8a8c92"));
                        emptyWl.setTextSize(12);
                        emptyWl.setPadding(4, 10, 4, 10);
                        layoutWhitelistList.addView(emptyWl);
                    }
                } catch (Exception ignored) {}
            }
            @Override public void onError(String error) {}
        });
    }

    private void showPlayerActionDialog(final String playerName) {
        String[] options = {
            I18n.t("app.promuovi_op_admin"),
            I18n.t("app.rimuovi_op"),
            I18n.t("app.cura_sazia_heal"),
            I18n.t("app.modalita_sopravvivenza_survival"),
            I18n.t("app.modalita_creativa_creative"),
            I18n.t("app.modalita_spettatore_spectator"),
            I18n.t("app.teletrasporto_allo_spawn"),
            I18n.t("app.messaggio_privato"),
            I18n.t("app.svuota_inventario"),
            I18n.t("app.espelli_kick"),
            I18n.t("app.banna_giocatore")
        };
        new I18n.Builder(this)
            .setTitle(I18n.t("app.gestione", playerName))
            .setItems(options, new DialogInterface.OnClickListener() {
                @Override
                public void onClick(DialogInterface dialog, int which) {
                    if (which == 0) {
                        submitCommand("op " + playerName);
                        I18n.makeText(MainActivity.this, I18n.t("app.promosso_op", playerName), Toast.LENGTH_SHORT).show();
                    } else if (which == 1) {
                        submitCommand("deop " + playerName);
                        I18n.makeText(MainActivity.this, I18n.t("app.permessi_op_rimossi", playerName), Toast.LENGTH_SHORT).show();
                    } else if (which == 2) {
                        submitCommand("effect give " + playerName + " minecraft:instant_health 1 255");
                        submitCommand("effect give " + playerName + " minecraft:saturation 1 255");
                        I18n.makeText(MainActivity.this, I18n.t("app.salute_fame_ripristinate", playerName), Toast.LENGTH_SHORT).show();
                    } else if (which == 3) {
                        submitCommand("gamemode survival " + playerName);
                        I18n.makeText(MainActivity.this, I18n.t("app.impostato_sopravvivenza", playerName), Toast.LENGTH_SHORT).show();
                    } else if (which == 4) {
                        submitCommand("gamemode creative " + playerName);
                        I18n.makeText(MainActivity.this, I18n.t("app.impostato_creativa", playerName), Toast.LENGTH_SHORT).show();
                    } else if (which == 5) {
                        submitCommand("gamemode spectator " + playerName);
                        I18n.makeText(MainActivity.this, I18n.t("app.impostato_spettatore", playerName), Toast.LENGTH_SHORT).show();
                    } else if (which == 6) {
                        teleportPlayerToSpawn(playerName);
                    } else if (which == 7) {
                        showPrivateMessageDialog(playerName);
                    } else if (which == 8) {
                        confirmClearPlayerInventory(playerName);
                    } else if (which == 9) {
                        confirmKickPlayer(playerName);
                    } else if (which == 10) {
                        confirmBanPlayer(playerName);
                    }
                }
            })
            .setNegativeButton(I18n.t("app.annulla"), null)
            .show();
    }

    /** Spawn del mondo letto dal PC (level.dat). Prima: "spawnpoint" spostava lo spawn personale del giocatore dove si
     *  trovava e poi lo mandava a 0 100 0, che di solito non è lo spawn (a mezz'aria o dentro la roccia). */
    private void teleportPlayerToSpawn(final String playerName) {
        try {
            JSONObject body = new JSONObject();
            body.put("player_name", playerName);
            body.put("action", "spawn");
            executeApiCall("/api/player/quick-action", "POST", body.toString(), new ApiClient.ApiCallback() {
                @Override
                public void onSuccess(String response) {
                    I18n.makeText(MainActivity.this, I18n.t("app.teletrasportato_allo_spawn", playerName), Toast.LENGTH_SHORT).show();
                }

                @Override
                public void onError(String error) {
                    I18n.makeText(MainActivity.this, error, Toast.LENGTH_LONG).show();
                }
            });
        } catch (Exception e) {
            I18n.makeText(this, String.valueOf(e.getMessage()), Toast.LENGTH_SHORT).show();
        }
    }

    private void showPrivateMessageDialog(final String playerName) {
        final EditText input = new EditText(this);
        input.setHint(I18n.t("app.scrivi_messaggio_privato"));
        new I18n.Builder(this)
            .setTitle(I18n.t("app.messaggio", playerName))
            .setView(withDialogMargins(input))
            .setPositiveButton(I18n.t("ui.invia"), new DialogInterface.OnClickListener() {
                @Override
                public void onClick(DialogInterface dialog, int which) {
                    String msg = input.getText().toString().trim();
                    if (!msg.isEmpty()) {
                        submitCommand("msg " + playerName + " " + msg);
                        I18n.makeText(MainActivity.this, I18n.t("app.messaggio_privato_inviato"), Toast.LENGTH_SHORT).show();
                    }
                }
            })
            .setNegativeButton(I18n.t("app.annulla"), null)
            .show();
    }

    private void confirmClearPlayerInventory(final String playerName) {
        new I18n.Builder(this)
            .setTitle(I18n.t("app.svuota_inventario"))
            .setMessage(I18n.t("app.vuoi_davvero_rimuovere_tutti_oggetti", playerName))
            .setPositiveButton(I18n.t("app.si_svuota"), new DialogInterface.OnClickListener() {
                @Override
                public void onClick(DialogInterface dialog, int which) {
                    submitCommand("clear " + playerName);
                    I18n.makeText(MainActivity.this, I18n.t("app.inventario_svuotato", playerName), Toast.LENGTH_SHORT).show();
                }
            })
            .setNegativeButton(I18n.t("app.annulla"), null)
            .show();
    }

    private void confirmKickPlayer(final String playerName) {
        new I18n.Builder(this)
            .setTitle(I18n.t("app.espelli_giocatore"))
            .setMessage(I18n.t("app.vuoi_davvero_espellere_dal_server", playerName))
            .setPositiveButton(I18n.t("app.espelli"), new DialogInterface.OnClickListener() {
                @Override
                public void onClick(DialogInterface dialog, int which) {
                    submitCommand("kick " + playerName + " Espulso dall'amministratore");
                    I18n.makeText(MainActivity.this, I18n.t("app.espulso_dal_server", playerName), Toast.LENGTH_SHORT).show();
                    fetchPlayersAndWhitelist();
                }
            })
            .setNegativeButton(I18n.t("app.annulla"), null)
            .show();
    }

    private void confirmBanPlayer(final String playerName) {
        new I18n.Builder(this)
            .setTitle(I18n.t("app.banna_giocatore"))
            .setMessage(I18n.t("app.vuoi_davvero_bandire_permanentemente", playerName))
            .setPositiveButton(I18n.t("app.banna"), new DialogInterface.OnClickListener() {
                @Override
                public void onClick(DialogInterface dialog, int which) {
                    submitCommand("ban " + playerName + " Bandito dall'amministratore");
                    I18n.makeText(MainActivity.this, I18n.t("app.bannato_dal_server", playerName), Toast.LENGTH_SHORT).show();
                    fetchPlayersAndWhitelist();
                }
            })
            .setNegativeButton(I18n.t("app.annulla"), null)
            .show();
    }

    private void addPlayerToWhitelist(String playerName) {
        try {
            android.view.inputmethod.InputMethodManager imm = (android.view.inputmethod.InputMethodManager) getSystemService(Context.INPUT_METHOD_SERVICE);
            if (imm != null && edtWhitelistInput != null) {
                imm.hideSoftInputFromWindow(edtWhitelistInput.getWindowToken(), 0);
            }
            JSONObject body = new JSONObject();
            body.put("player_name", playerName);
            executeApiCall("/api/whitelist/add", "POST", body.toString(), new ApiClient.ApiCallback() {
                @Override
                public void onSuccess(String response) {
                    I18n.makeText(MainActivity.this, I18n.t("app.giocatore_aggiunto_whitelist"), Toast.LENGTH_SHORT).show();
                    fetchPlayersAndWhitelist();
                }
                @Override public void onError(String error) {
                    // Prima l'errore del PC (nome inesistente, Mojang non raggiungibile) restava nascosto e si mandava
                    // lo stesso comando in console
                    I18n.makeText(MainActivity.this, error, Toast.LENGTH_LONG).show();
                    fetchPlayersAndWhitelist();
                }
            });
        } catch (Exception ignored) {}
    }

    private void removePlayerFromWhitelist(String playerName) {
        try {
            JSONObject body = new JSONObject();
            body.put("player_name", playerName);
            executeApiCall("/api/whitelist/remove", "POST", body.toString(), new ApiClient.ApiCallback() {
                @Override
                public void onSuccess(String response) {
                    I18n.makeText(MainActivity.this, I18n.t("app.giocatore_rimosso_dalla_whitelist"), Toast.LENGTH_SHORT).show();
                    fetchPlayersAndWhitelist();
                }
                @Override public void onError(String error) {
                    submitCommand("whitelist remove " + playerName);
                    fetchPlayersAndWhitelist();
                }
            });
        } catch (Exception ignored) {}
    }

    // ================= PROPERTIES EDITOR =================

    private void fetchGameRulesFromServer() {
        fetchPvpFromProperties();
        if (!"ONLINE".equalsIgnoreCase(mServerState)) {
            updateGameRuleButtonsUI();
            return;
        }
        // Valori veri letti dal PC (che usa i nomi nuovi delle regole da Minecraft 1.21.11). Prima "gamerule X" via
        // /api/server/command rispondeva solo "Comando inviato" e l'app mostrava i valori salvati sul telefono.
        executeApiCall("/api/server/gamerules", "GET", null, new ApiClient.ApiCallback() {
            @Override
            public void onSuccess(String response) {
                try {
                    JSONObject rules = new JSONObject(response).optJSONObject("rules");
                    if (rules == null) return;
                    java.util.Iterator<String> it = rules.keys();
                    while (it.hasNext()) {
                        String rule = it.next();
                        Object v = rules.opt(rule);
                        if (v instanceof Boolean) {
                            setRuleVariable(rule, (Boolean) v);
                            saveGameRulePref(rule, (Boolean) v);
                        }
                    }
                    runOnUiThread(new Runnable() {
                        @Override public void run() { updateGameRuleButtonsUI(); }
                    });
                } catch (Exception ignored) {}
            }
            @Override public void onError(String error) {}
        });
    }

    /** PvP da server.properties (vale anche a server spento). */
    private void fetchPvpFromProperties() {
        executeApiCall("/api/server/properties", "GET", null, new ApiClient.ApiCallback() {
            @Override
            public void onSuccess(String response) {
                try {
                    JSONObject props = new JSONObject(response).optJSONObject("properties");
                    if (props == null || !props.has("pvp")) return;
                    final boolean val = !"false".equalsIgnoreCase(props.optString("pvp", "true").trim());
                    setRuleVariable("pvp", val);
                    saveGameRulePref("pvp", val);
                    runOnUiThread(new Runnable() {
                        @Override public void run() { updateGameRuleButtonsUI(); }
                    });
                } catch (Exception ignored) {}
            }
            @Override public void onError(String error) {}
        });
    }

    /** PvP si cambia in server.properties (il comando "gamerule pvp" non esiste) e vale dal prossimo avvio del server. */
    private void togglePvpProperty(final boolean targetValue, final Button btn) {
        btn.setEnabled(false);
        String body;
        try {
            JSONObject p = new JSONObject();
            p.put("pvp", String.valueOf(targetValue));
            body = new JSONObject().put("properties", p).toString();
        } catch (Exception e) {
            btn.setEnabled(true);
            return;
        }
        executeApiCall("/api/server/properties", "POST", body, new ApiClient.ApiCallback() {
            @Override
            public void onSuccess(String response) {
                boolean needsRestart = false;
                try {
                    needsRestart = new JSONObject(response).optBoolean("needs_restart", false);
                } catch (Exception ignored) {}
                final boolean restartRequired = needsRestart;
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        btn.setEnabled(true);
                        setRuleVariable("pvp", targetValue);
                        saveGameRulePref("pvp", targetValue);
                        updateGameRuleButtonsUI();
                        if (restartRequired) {
                            new I18n.Builder(MainActivity.this)
                                .setTitle(I18n.t("app.proprieta_salvate"))
                                .setMessage(I18n.t("app.modifiche_server_properties_sono_state"))
                                .setPositiveButton(I18n.t("app.riavvia_ora"), new DialogInterface.OnClickListener() {
                                    @Override
                                    public void onClick(DialogInterface dialog, int which) {
                                        confirmRestartServer();
                                    }
                                })
                                .setNegativeButton(I18n.t("app.riavvia_piu_tardi"), null)
                                .show();
                        } else {
                            I18n.makeText(MainActivity.this, I18n.t("app.proprieta_salvate_successo"), Toast.LENGTH_SHORT).show();
                        }
                    }
                });
            }
            @Override
            public void onError(final String error) {
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        btn.setEnabled(true);
                        I18n.makeText(MainActivity.this, I18n.t("app.errore_modifica", "pvp", error), Toast.LENGTH_LONG).show();
                    }
                });
            }
        });
    }

    private void toggleGameRule(final String ruleName, final boolean targetValue, final Button btn) {
        if ("pvp".equals(ruleName)) {
            togglePvpProperty(targetValue, btn);
            return;
        }
        if (!"ONLINE".equalsIgnoreCase(mServerState)) {
            vibrate(30);
            I18n.makeText(this, I18n.t("app.server_deve_essere_online_modificare_2"), Toast.LENGTH_SHORT).show();
            return;
        }
        btn.setEnabled(false);
        String body = "{\"command\":\"gamerule " + ruleName + " " + targetValue + "\"}";
        executeApiCall("/api/server/command", "POST", body, new ApiClient.ApiCallback() {
            @Override
            public void onSuccess(String response) {
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        btn.setEnabled(true);
                        setRuleVariable(ruleName, targetValue);
                        saveGameRulePref(ruleName, targetValue);
                        updateGameRuleButtonsUI();
                        I18n.makeText(MainActivity.this, ruleName + ": " + (targetValue ? I18n.t("app.attivato") : I18n.t("app.disattivato")), Toast.LENGTH_SHORT).show();
                    }
                });
            }
            @Override
            public void onError(final String error) {
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        btn.setEnabled(true);
                        I18n.makeText(MainActivity.this, I18n.t("app.errore_modifica", ruleName, error), Toast.LENGTH_LONG).show();
                    }
                });
            }
        });
    }

    private void setRuleVariable(String rule, boolean val) {
        if ("keepInventory".equals(rule)) mIsRuleKeepInventory = val;
        else if ("mobGriefing".equals(rule)) mIsRuleMobGriefing = val;
        else if ("pvp".equals(rule)) mIsRulePvP = val;
        else if ("doDaylightCycle".equals(rule)) mIsRuleDaylight = val;
        else if ("doFireTick".equals(rule)) mIsRuleFireTick = val;
        else if ("doWeatherCycle".equals(rule)) mIsRuleWeatherCycle = val;
        else if ("showDeathMessages".equals(rule)) mIsRuleShowDeath = val;
    }

    private void loadGameRulesPrefs() {
        SharedPreferences prefs = getSharedPreferences("server_gamerules", MODE_PRIVATE);
        mIsRuleKeepInventory = prefs.getBoolean("keepInventory", false);
        mIsRuleMobGriefing = prefs.getBoolean("mobGriefing", true);
        mIsRulePvP = prefs.getBoolean("pvp", true);
        mIsRuleDaylight = prefs.getBoolean("doDaylightCycle", true);
        mIsRuleFireTick = prefs.getBoolean("doFireTick", true);
        mIsRuleWeatherCycle = prefs.getBoolean("doWeatherCycle", true);
        mIsRuleShowDeath = prefs.getBoolean("showDeathMessages", true);
    }

    private void updateGameRuleButtonsUI() {
        if (btnRuleKeepInventory != null) {
            btnRuleKeepInventory.setText(I18n.t("app.mantieni_inventario_keepinventory", (mIsRuleKeepInventory ? I18n.t("app.attivato") : I18n.t("app.disattivato"))));
            applyToggleStyle(btnRuleKeepInventory, mIsRuleKeepInventory);
        }
        if (btnRuleMobGriefing != null) {
            btnRuleMobGriefing.setText(I18n.t("app.danni_creeper_mob_mobgriefing", (mIsRuleMobGriefing ? I18n.t("app.attivi") : I18n.t("app.bloccati"))));
            applyToggleStyle(btnRuleMobGriefing, !mIsRuleMobGriefing);
        }
        if (btnRulePvP != null) {
            btnRulePvP.setText(I18n.t("app.combattimento_pvp", (mIsRulePvP ? I18n.t("app.abilitato") : I18n.t("app.disabilitato"))));
            applyToggleStyle(btnRulePvP, mIsRulePvP);
        }
        if (btnRuleDaylight != null) {
            btnRuleDaylight.setText(I18n.t("app.ciclo_giorno_notte_dodaylightcycle", (mIsRuleDaylight ? I18n.t("ui.attivo") : I18n.t("app.fermo"))));
            applyToggleStyle(btnRuleDaylight, mIsRuleDaylight);
        }
        if (btnRuleFireTick != null) {
            btnRuleFireTick.setText(I18n.t("app.propagazione_fuoco_dofiretick", (mIsRuleFireTick ? I18n.t("app.attiva") : I18n.t("app.bloccata"))));
            applyToggleStyle(btnRuleFireTick, !mIsRuleFireTick);
        }
        if (btnRuleWeatherCycle != null) {
            btnRuleWeatherCycle.setText(I18n.t("app.ciclo_meteo_dinamico_doweathercycle", (mIsRuleWeatherCycle ? I18n.t("ui.attivo") : I18n.t("app.fisso"))));
            applyToggleStyle(btnRuleWeatherCycle, mIsRuleWeatherCycle);
        }
        if (btnRuleShowDeath != null) {
            btnRuleShowDeath.setText(I18n.t("app.mostra_morti_chat_showdeathmessages", (mIsRuleShowDeath ? I18n.t("ui.attivo") : I18n.t("app.disattivato"))));
            applyToggleStyle(btnRuleShowDeath, mIsRuleShowDeath);
        }
    }

    private void applyToggleStyle(Button btn, boolean isPositiveState) {
        if (btn == null) return;
        if (isPositiveState) {
            btn.setBackgroundResource(R.drawable.btn_blue);
            btn.setTextColor(Color.WHITE);
        } else {
            btn.setBackgroundResource(R.drawable.btn_slate);
            btn.setTextColor(Color.parseColor("#c4c4c8"));
        }
    }

    private void saveGameRulePref(String key, boolean value) {
        getSharedPreferences("server_gamerules", MODE_PRIVATE).edit().putBoolean(key, value).apply();
    }

    private void fetchProperties() {
        executeApiCall("/api/server/properties", "GET", null, new ApiClient.ApiCallback() {
            @Override
            public void onSuccess(String response) {
                try {
                    JSONObject obj = new JSONObject(response);
                    final JSONObject props = obj.optJSONObject("properties");
                    final boolean backupExists = obj.optBoolean("backup_exists", false);
                    if (props != null) {
                        final String diff = props.optString("difficulty", "normal");
                        final int viewDist = props.optInt("view-distance", 8);
                        final String motd = props.optString("motd", "Server Minecraft Moddato Forge");
                        final String maxPlayers = props.optString("max-players", "20");

                        runOnUiThread(new Runnable() {
                            @Override
                            public void run() {
                                setDifficultyUI(diff);
                                setViewDistanceUI(viewDist);
                                if (edtPropertiesMotd != null) edtPropertiesMotd.setText(motd);
                                if (edtPropertiesMaxPlayers != null) edtPropertiesMaxPlayers.setText(maxPlayers);
                                if (btnRestorePropertiesBak != null) {
                                    btnRestorePropertiesBak.setVisibility(backupExists ? View.VISIBLE : View.GONE);
                                }
                            }
                        });
                    }
                } catch (Exception ignored) {}
            }
            @Override public void onError(String error) {}
        });
    }

    private void setDifficultyUI(String diff) {
        mCurrentDifficulty = diff;
        applyChipStyle(chipDiffPeaceful, "peaceful".equalsIgnoreCase(diff));
        applyChipStyle(chipDiffEasy, "easy".equalsIgnoreCase(diff));
        applyChipStyle(chipDiffNormal, "normal".equalsIgnoreCase(diff));
        applyChipStyle(chipDiffHard, "hard".equalsIgnoreCase(diff));
    }

    private void setViewDistanceUI(int dist) {
        mCurrentViewDistance = dist;
        applyChipStyle(chipView8, dist == 8);
        applyChipStyle(chipView10, dist == 10);
        applyChipStyle(chipView12, dist == 12);
        applyChipStyle(chipView14, dist == 14);
    }

    private void applyChipStyle(Button btn, boolean isSelected) {
        if (btn == null) return;
        if (isSelected) {
            btn.setBackgroundResource(R.drawable.btn_blue);
            btn.setTextColor(Color.WHITE);
            btn.setTypeface(null, android.graphics.Typeface.BOLD);
        } else {
            btn.setBackgroundResource(R.drawable.chip_bg);
            btn.setTextColor(Color.parseColor("#9a9ca2"));
            btn.setTypeface(null, android.graphics.Typeface.NORMAL);
        }
    }

    private void saveProperties() {
        try {
            String motd = (edtPropertiesMotd != null) ? edtPropertiesMotd.getText().toString().trim() : I18n.t("app.server_minecraft");
            if (motd.isEmpty()) motd = I18n.t("app.server_minecraft");

            String maxPlayersStr = (edtPropertiesMaxPlayers != null) ? edtPropertiesMaxPlayers.getText().toString().trim() : "20";
            int maxPlayers = 20;
            try {
                maxPlayers = Integer.parseInt(maxPlayersStr);
                if (maxPlayers <= 0) maxPlayers = 20;
            } catch (Exception e) {
                maxPlayers = 20;
            }

            JSONObject body = new JSONObject();
            JSONObject p = new JSONObject();
            p.put("difficulty", mCurrentDifficulty);
            p.put("view-distance", String.valueOf(mCurrentViewDistance));
            p.put("motd", motd);
            p.put("max-players", String.valueOf(maxPlayers));
            body.put("properties", p);

            executeApiCall("/api/server/properties", "POST", body.toString(), new ApiClient.ApiCallback() {
                @Override
                public void onSuccess(String response) {
                    vibrate(40);
                    boolean needsRestart = false;
                    try {
                        JSONObject res = new JSONObject(response);
                        needsRestart = res.optBoolean("needs_restart", false);
                    } catch (Exception ignored) {}

                    final boolean restartRequired = needsRestart;
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            if (restartRequired) {
                                new I18n.Builder(MainActivity.this)
                                    .setTitle(I18n.t("app.proprieta_salvate"))
                                    .setMessage(I18n.t("app.modifiche_server_properties_sono_state"))
                                    .setPositiveButton(I18n.t("app.riavvia_ora"), new DialogInterface.OnClickListener() {
                                        @Override
                                        public void onClick(DialogInterface dialog, int which) {
                                            confirmRestartServer();
                                        }
                                    })
                                    .setNegativeButton(I18n.t("app.riavvia_piu_tardi"), null)
                                    .show();
                            } else {
                                I18n.makeText(MainActivity.this, I18n.t("app.proprieta_salvate_successo"), Toast.LENGTH_SHORT).show();
                            }
                            fetchProperties();
                        }
                    });
                }
                @Override
                public void onError(final String error) {
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            I18n.makeText(MainActivity.this, I18n.t("app.errore_salvataggio", error), Toast.LENGTH_LONG).show();
                        }
                    });
                }
            });
        } catch (Exception ignored) {}
    }

    private void confirmRestorePropertiesBak() {
        vibrate(20);
        new I18n.Builder(this)
            .setTitle(I18n.t("app.ripristino_server_properties"))
            .setMessage(I18n.t("app.vuoi_ripristinare_file_server"))
            .setPositiveButton(I18n.t("app.ripristina"), new DialogInterface.OnClickListener() {
                @Override
                public void onClick(DialogInterface dialog, int which) {
                    executeApiCall("/api/server/properties/restore-backup", "POST", "{}", new ApiClient.ApiCallback() {
                        @Override
                        public void onSuccess(final String response) {
                            runOnUiThread(new Runnable() {
                                @Override
                                public void run() {
                                    I18n.makeText(MainActivity.this, I18n.t("app.backup_server_properties_ripristinato"), Toast.LENGTH_SHORT).show();
                                    fetchProperties();
                                }
                            });
                        }
                        @Override
                        public void onError(final String error) {
                            runOnUiThread(new Runnable() {
                                @Override
                                public void run() {
                                    I18n.makeText(MainActivity.this, I18n.t("app.errore_ripristino", error), Toast.LENGTH_LONG).show();
                                }
                            });
                        }
                    });
                }
            })
            .setNegativeButton(I18n.t("app.annulla"), null)
            .show();
    }

    // ================= AUTO-RESTART TIMER =================

    private void postponeRestartTimer() {
        if (!"ONLINE".equalsIgnoreCase(mServerState)) {
            vibrate(30);
            I18n.makeText(this, I18n.t("app.server_deve_essere_online_gestire"), Toast.LENGTH_SHORT).show();
            return;
        }
        try {
            JSONObject body = new JSONObject();
            body.put("minutes", 60);
            executeApiCall("/api/timer/postpone", "POST", body.toString(), new ApiClient.ApiCallback() {
                @Override
                public void onSuccess(String response) {
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            vibrate(30);
                            I18n.makeText(MainActivity.this, I18n.t("app.riavvio_posticipato_1_ora_60_min"), Toast.LENGTH_SHORT).show();
                            fetchStatus();
                        }
                    });
                }
                @Override
                public void onError(final String error) {
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            I18n.makeText(MainActivity.this, I18n.t("app.errore_posticipo_timer", error), Toast.LENGTH_SHORT).show();
                        }
                    });
                }
            });
        } catch (Exception ignored) {}
    }

    private void resetRestartTimer() {
        if (!"ONLINE".equalsIgnoreCase(mServerState)) {
            vibrate(30);
            I18n.makeText(this, I18n.t("app.server_deve_essere_online_gestire"), Toast.LENGTH_SHORT).show();
            return;
        }
        executeApiCall("/api/timer/reset", "POST", "{}", new ApiClient.ApiCallback() {
            @Override
            public void onSuccess(String response) {
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        vibrate(30);
                        I18n.makeText(MainActivity.this, I18n.t("app.timer_reimpostato_4_ore"), Toast.LENGTH_SHORT).show();
                        fetchStatus();
                    }
                });
            }
            @Override
            public void onError(final String error) {
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        I18n.makeText(MainActivity.this, I18n.t("app.errore_reset_timer", error), Toast.LENGTH_SHORT).show();
                    }
                });
            }
        });
    }

    // ================= CONSOLE & COMMANDS =================

    private void submitCommand(String cmd) {
        if (cmd == null || cmd.trim().isEmpty()) return;
        final String commandStr = cmd.trim();

        if (!"ONLINE".equalsIgnoreCase(mServerState)) {
            vibrate(30);
            I18n.makeText(this, I18n.t("app.server_deve_essere_online_inviare"), Toast.LENGTH_SHORT).show();
            return;
        }

        edtCommand.setText("");
        vibrate(25);

        String currentLogs = txtConsoleLogs.getText().toString();
        if (currentLogs.isEmpty() || currentLogs.contains(I18n.t("ui.inizializzazione_terminale"))) {
            txtConsoleLogs.setText("> " + commandStr);
        } else {
            txtConsoleLogs.append("\n> " + commandStr);
        }
        scrollConsole.post(new Runnable() {
            @Override
            public void run() { scrollConsole.fullScroll(View.FOCUS_DOWN); }
        });

        try {
            JSONObject body = new JSONObject();
            body.put("command", commandStr);
            executeApiCall("/api/server/command", "POST", body.toString(), new ApiClient.ApiCallback() {
                @Override
                public void onSuccess(String response) {
                    mPollHandler.postDelayed(new Runnable() {
                        @Override
                        public void run() { fetchLogs(); }
                    }, 400);
                }
                @Override
                public void onError(final String error) {
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            txtConsoleLogs.append(I18n.t("app.errore_esecuzione", error));
                        }
                    });
                }
            });
        } catch (Exception ignored) {}
    }

    private void fetchLogs() {
        executeApiCall("/api/logs", "GET", null, new ApiClient.ApiCallback() {
            @Override
            public void onSuccess(String response) {
                try {
                    if (response == null || response.trim().isEmpty()) return;
                    String trimmed = response.trim();
                    JSONArray arr = null;
                    if (trimmed.startsWith("[")) {
                        arr = new JSONArray(trimmed);
                    } else if (trimmed.startsWith("{")) {
                        JSONObject obj = new JSONObject(trimmed);
                        if (obj.has("lines")) {
                            arr = obj.optJSONArray("lines");
                        } else if (obj.has("logs")) {
                            arr = obj.optJSONArray("logs");
                        }
                    }
                    if (arr != null) {
                        mRawLogsList.clear();
                        int startIdx = Math.max(0, arr.length() - 350);
                        for (int i = startIdx; i < arr.length(); i++) {
                            mRawLogsList.add(arr.getString(i));
                        }
                        runOnUiThread(new Runnable() {
                            @Override
                            public void run() {
                                renderFilteredLogs(false);
                            }
                        });
                    }
                } catch (Exception ignored) {}
            }
            @Override public void onError(String error) {}
        });
    }

    private void setLogFilter(String filter) {
        mCurrentLogFilter = filter;
        btnLogFilterAll.setBackgroundResource("ALL".equals(filter) ? R.drawable.btn_blue : R.drawable.btn_slate);
        btnLogFilterAll.setTextColor("ALL".equals(filter) ? Color.WHITE : Color.parseColor("#c4c4c8"));

        btnLogFilterError.setBackgroundResource("ERROR".equals(filter) ? R.drawable.btn_blue : R.drawable.btn_slate);
        btnLogFilterError.setTextColor("ERROR".equals(filter) ? Color.WHITE : Color.parseColor("#c4c4c8"));

        btnLogFilterChat.setBackgroundResource("CHAT".equals(filter) ? R.drawable.btn_blue : R.drawable.btn_slate);
        btnLogFilterChat.setTextColor("CHAT".equals(filter) ? Color.WHITE : Color.parseColor("#c4c4c8"));

        renderFilteredLogs(true);
    }

    private void renderFilteredLogs() {
        renderFilteredLogs(true);
    }

    private void renderFilteredLogs(final boolean forceScrollDown) {
        if (txtConsoleLogs == null || scrollConsole == null) return;
        StringBuilder sb = new StringBuilder();
        String queryLower = (mLogSearchQuery != null) ? mLogSearchQuery.trim().toLowerCase() : "";

        for (String line : mRawLogsList) {
            if ("ERROR".equals(mCurrentLogFilter)) {
                if (!line.contains("/ERROR]") && !line.contains("/FATAL]") && !line.toLowerCase().contains("exception") && !line.toLowerCase().contains("error")) {
                    continue;
                }
            } else if ("CHAT".equals(mCurrentLogFilter)) {
                if (!line.contains("<") || !line.contains(">") || line.contains("[Server thread/WARN]")) {
                    continue;
                }
            }

            if (!queryLower.isEmpty()) {
                if (!line.toLowerCase().contains(queryLower)) {
                    continue;
                }
            }

            sb.append(line).append("\n");
        }

        final String filtered = sb.toString().trim();
        txtConsoleLogs.setText(filtered.isEmpty() ? (mRawLogsList.isEmpty() ? I18n.t("app.nessun_log_recente") : I18n.t("app.nessun_risultato_filtri_selezionati")) : filtered);
        scrollConsole.post(new Runnable() {
            @Override
            public void run() {
                if (forceScrollDown) {
                    scrollConsole.fullScroll(View.FOCUS_DOWN);
                } else {
                    View child = scrollConsole.getChildAt(0);
                    if (child != null) {
                        int diff = (child.getBottom() - (scrollConsole.getHeight() + scrollConsole.getScrollY()));
                        if (diff <= 120) {
                            scrollConsole.fullScroll(View.FOCUS_DOWN);
                        }
                    }
                }
            }
        });
    }

    private void actionClearLogs() {
        vibrate(20);
        new I18n.Builder(this)
            .setTitle(I18n.t("app.svuota_console"))
            .setMessage(I18n.t("app.vuoi_davvero_cancellare_cronologia_log"))
            .setPositiveButton(I18n.t("ui.svuota"), new DialogInterface.OnClickListener() {
                @Override
                public void onClick(DialogInterface dialog, int which) {
                    executeApiCall("/api/logs/clear", "POST", "{}", new ApiClient.ApiCallback() {
                        @Override
                        public void onSuccess(String response) {
                            mRawLogsList.clear();
                            runOnUiThread(new Runnable() {
                                @Override
                                public void run() {
                                    txtConsoleLogs.setText(I18n.t("app.console_svuotata_dall_amministratore"));
                                    I18n.makeText(MainActivity.this, I18n.t("app.console_svuotata"), Toast.LENGTH_SHORT).show();
                                }
                            });
                        }
                        @Override public void onError(String error) {
                            runOnUiThread(new Runnable() {
                                @Override
                                public void run() {
                                    txtConsoleLogs.setText("");
                                }
                            });
                        }
                    });
                }
            })
            .setNegativeButton(I18n.t("app.annulla"), null)
            .show();
    }

    // ================= ERRORS & DIAGNOSTICS =================

    private void fetchErrors() {
        executeApiCall("/api/errors", "GET", null, new ApiClient.ApiCallback() {
            @Override
            public void onSuccess(String response) {
                try {
                    JSONObject obj = new JSONObject(response);
                    final JSONArray errors = obj.optJSONArray("errors");
                    final JSONArray crashReports = obj.optJSONArray("crash_reports");

                    final int errCount = (errors != null) ? errors.length() : 0;
                    final int crashCount = (crashReports != null) ? crashReports.length() : 0;

                    final StringBuilder sb = new StringBuilder();

                    if (crashReports != null && crashReports.length() > 0) {
                        sb.append(I18n.t("app.crash_report_recenti")).append(crashReports.length()).append("):\n");
                        for (int i = 0; i < crashReports.length(); i++) {
                            JSONObject cr = crashReports.getJSONObject(i);
                            sb.append("• ").append(cr.optString("filename", "crash.txt"))
                              .append(" (").append(cr.optString("size_formatted", "")).append(")\n")
                              .append("").append(cr.optString("time", "")).append("\n")
                              .append("").append(cr.optString("description", "")).append("\n\n");
                        }
                    }

                    if (errors != null && errors.length() > 0) {
                        sb.append(I18n.t("app.errori_esecuzione")).append(errors.length()).append("):\n");
                        for (int i = 0; i < errors.length(); i++) {
                            JSONObject err = errors.getJSONObject(i);
                            // Il PC manda il testo in "message" ("text" c'è solo in alcuni avvisi di crash)
                            sb.append("• [").append(err.optString("time", "")).append("] ")
                              .append(err.optString("message", err.optString("text", ""))).append("\n\n");
                        }
                    }

                    if (crashReports != null && crashReports.length() > 0) {
                        JSONObject latest = crashReports.getJSONObject(0);
                        mLatestCrashReportFilename = latest.optString("filename", "");
                        String content = latest.optString("content", "");
                        if (content.isEmpty()) {
                            content = latest.optString("error_summary", "");
                        }
                        if (content.isEmpty()) {
                            content = latest.optString("description", I18n.t("app.dettaglio_crash_non_presente_nel_log"));
                        }
                        String time = latest.optString("time", "");
                        mLatestCrashReportContent = I18n.t("app.file_data_ora", mLatestCrashReportFilename, time, content);
                    } else {
                        mLatestCrashReportFilename = "";
                        mLatestCrashReportContent = "";
                    }

                    final String fullErrorsText = sb.toString().trim();

                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            if (txtErrorStats != null) {
                                txtErrorStats.setText(I18n.t("app.errori_registrati_crash_reports", errCount, crashCount));
                            }
                            if (txtErrorHealthBadge != null) {
                                if (errCount > 0 || crashCount > 0) {
                                    txtErrorHealthBadge.setText(I18n.t("app.anomalie_rilevate", (errCount + crashCount)));
                                    txtErrorHealthBadge.setTextColor(Color.parseColor("#f0788c"));
                                } else {
                                    txtErrorHealthBadge.setText(I18n.t("ui.nessun_errore_critico"));
                                    txtErrorHealthBadge.setTextColor(Color.parseColor("#a5d36f"));
                                }
                            }
                            if (txtErrorsLogList != null) {
                                txtErrorsLogList.setText(fullErrorsText.isEmpty() ? I18n.t("app.nessun_errore_registrato_nella") : fullErrorsText);
                            }
                            if (btnViewLatestCrash != null) {
                                if (!mLatestCrashReportFilename.isEmpty()) {
                                    btnViewLatestCrash.setVisibility(View.VISIBLE);
                                    btnViewLatestCrash.setText(I18n.t("app.ultimo_crash", mLatestCrashReportFilename));
                                } else {
                                    btnViewLatestCrash.setVisibility(View.GONE);
                                }
                            }
                        }
                    });

                } catch (Exception ignored) {}
            }
            @Override public void onError(String error) {}
        });
    }

    private void actionClearErrors() {
        vibrate(20);
        new I18n.Builder(this)
            .setTitle(I18n.t("app.svuota_registro_errori"))
            .setMessage(I18n.t("app.vuoi_davvero_cancellare_lista_degli"))
            .setPositiveButton(I18n.t("ui.svuota"), new DialogInterface.OnClickListener() {
                @Override
                public void onClick(DialogInterface dialog, int which) {
                    executeApiCall("/api/errors/clear", "POST", "{}", new ApiClient.ApiCallback() {
                        @Override
                        public void onSuccess(String response) {
                            runOnUiThread(new Runnable() {
                                @Override
                                public void run() {
                                    if (txtErrorHealthBadge != null) {
                                        txtErrorHealthBadge.setText(I18n.t("ui.nessun_errore_critico"));
                                        txtErrorHealthBadge.setTextColor(Color.parseColor("#a5d36f"));
                                    }
                                    if (txtErrorStats != null) {
                                        txtErrorStats.setText(I18n.t("ui.0_errori_registrati_0_crash_reports"));
                                    }
                                    if (txtErrorsLogList != null) {
                                        txtErrorsLogList.setText(I18n.t("app.nessun_errore_registrato_nella"));
                                    }
                                    if (btnViewLatestCrash != null) {
                                        btnViewLatestCrash.setVisibility(View.GONE);
                                    }
                                    mLatestCrashReportFilename = "";
                                    mLatestCrashReportContent = "";
                                    I18n.makeText(MainActivity.this, I18n.t("app.log_errori_svuotato"), Toast.LENGTH_SHORT).show();
                                }
                            });
                        }
                        @Override public void onError(final String error) {
                            runOnUiThread(new Runnable() {
                                @Override
                                public void run() {
                                    I18n.makeText(MainActivity.this, I18n.t("app.errore_svuotamento", error), Toast.LENGTH_SHORT).show();
                                }
                            });
                        }
                    });
                }
            })
            .setNegativeButton(I18n.t("app.annulla"), null)
            .show();
    }

    private void viewLatestCrashReport() {
        if (mLatestCrashReportFilename == null || mLatestCrashReportFilename.trim().isEmpty()) {
            new I18n.Builder(this)
                .setTitle(I18n.t("app.crash_report_recente"))
                .setMessage(mLatestCrashReportContent.isEmpty() ? I18n.t("app.nessun_dettaglio_disponibile") : mLatestCrashReportContent)
                .setPositiveButton(I18n.t("app.chiudi"), null)
                .show();
            return;
        }

        vibrate(20);
        I18n.makeText(this, I18n.t("app.caricamento_crash_report_completo"), Toast.LENGTH_SHORT).show();
        executeApiCall("/api/crash-report/" + mLatestCrashReportFilename, "GET", null, new ApiClient.ApiCallback() {
            @Override
            public void onSuccess(final String fullText) {
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        showCrashReportDialog(mLatestCrashReportFilename, fullText);
                    }
                });
            }
            @Override
            public void onError(String error) {
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        showCrashReportDialog(mLatestCrashReportFilename, mLatestCrashReportContent);
                    }
                });
            }
        });
    }

    private void showCrashReportDialog(String filename, String content) {
        ScrollView scroll = new ScrollView(this);
        scroll.setPadding(24, 16, 24, 16);
        TextView tv = new TextView(this);
        tv.setText(content);
        tv.setTextSize(10f);
        tv.setTextColor(Color.parseColor("#f4f4f5"));
        tv.setTypeface(android.graphics.Typeface.MONOSPACE);
        tv.setTextIsSelectable(true);
        scroll.addView(tv);

        new I18n.Builder(this)
            .setTitle("" + filename)
            .setView(scroll)
            .setPositiveButton(I18n.t("app.chiudi"), null)
            .show();
    }

    // ================= CHAT IN-GAME =================

    private void fetchChat() {
        fetchChat(false);
    }

    private void fetchChat(final boolean forceScrollDown) {
        executeApiCall("/api/chat", "GET", null, new ApiClient.ApiCallback() {
            @Override
            public void onSuccess(String response) {
                try {
                    if (response == null || response.trim().isEmpty()) return;
                    String trimmed = response.trim();
                    JSONArray arr = null;
                    if (trimmed.startsWith("[")) {
                        arr = new JSONArray(trimmed);
                    } else if (trimmed.startsWith("{")) {
                        JSONObject obj = new JSONObject(trimmed);
                        if (obj.has("messages")) {
                            arr = obj.optJSONArray("messages");
                        } else if (obj.has("chat")) {
                            arr = obj.optJSONArray("chat");
                        }
                    }
                    if (arr != null) {
                        StringBuilder sb = new StringBuilder();
                        int startIdx = Math.max(0, arr.length() - 200); // circular buffer: max 200 messages
                        for (int i = startIdx; i < arr.length(); i++) {
                            JSONObject msg = arr.getJSONObject(i);
                            String time = msg.optString("time", "");
                            String sender = msg.optString("sender", "Server");
                            String text = msg.optString("text", "");
                            String reply = msg.optString("reply", "");

                            if (!time.isEmpty()) {
                                sb.append("[").append(time).append("] ");
                            }
                            sb.append(sender).append(": ").append(text).append("\n");
                            if (reply != null && !reply.trim().isEmpty() && !"null".equalsIgnoreCase(reply.trim())) {
                                sb.append((I18n.t("app.sistema") + " ")).append(reply.trim()).append("\n");
                            }
                        }
                        final String chatContent = sb.toString().trim();
                        runOnUiThread(new Runnable() {
                            @Override
                            public void run() {
                                if (txtChatMessages != null) {
                                    txtChatMessages.setText(chatContent.isEmpty() ? I18n.t("app.nessun_messaggio_recente_chat") : chatContent);
                                }
                                if (scrollChat != null) {
                                    scrollChat.post(new Runnable() {
                                        @Override
                                        public void run() {
                                            if (forceScrollDown) {
                                                scrollChat.fullScroll(View.FOCUS_DOWN);
                                            } else {
                                                View child = scrollChat.getChildAt(0);
                                                if (child != null) {
                                                    int diff = child.getBottom() - (scrollChat.getHeight() + scrollChat.getScrollY());
                                                    if (diff <= 120) {
                                                        scrollChat.fullScroll(View.FOCUS_DOWN);
                                                    }
                                                }
                                            }
                                        }
                                    });
                                }
                            }
                        });
                    }
                } catch (Exception ignored) {}
            }
            @Override public void onError(String error) {}
        });
    }

    private void submitChat() {
        final String text = edtChatText.getText().toString().trim();
        if (text.isEmpty()) return;
        edtChatText.setText("");
        vibrate(20);

        try {
            JSONObject body = new JSONObject();
            body.put("message", text);
            body.put("sender", "Admin");
            executeApiCall("/api/chat/talk", "POST", body.toString(), new ApiClient.ApiCallback() {
                @Override
                public void onSuccess(final String response) {
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            fetchChat(true);
                        }
                    });
                }
                @Override
                public void onError(final String error) {
                    // Fallback a comando console standard
                    submitCommand("say " + text);
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            I18n.makeText(MainActivity.this, I18n.t("app.messaggio_inviato_via_console_fallback"), Toast.LENGTH_SHORT).show();
                            fetchChat(true);
                        }
                    });
                }
            });
        } catch (Exception e) {
            submitCommand("say " + text);
        }
    }

    private void actionClearChat() {
        vibrate(20);
        new I18n.Builder(this)
            .setTitle(I18n.t("app.svuota_chat"))
            .setMessage(I18n.t("app.vuoi_davvero_cancellare_cronologia"))
            .setPositiveButton(I18n.t("ui.svuota"), new DialogInterface.OnClickListener() {
                @Override
                public void onClick(DialogInterface dialog, int which) {
                    executeApiCall("/api/chat/clear", "POST", "{}", new ApiClient.ApiCallback() {
                        @Override
                        public void onSuccess(String response) {
                            runOnUiThread(new Runnable() {
                                @Override
                                public void run() {
                                    if (txtChatMessages != null) {
                                        txtChatMessages.setText(I18n.t("ui.nessun_messaggio_chat"));
                                    }
                                    I18n.makeText(MainActivity.this, I18n.t("app.chat_svuotata"), Toast.LENGTH_SHORT).show();
                                }
                            });
                        }
                        @Override
                        public void onError(final String error) {
                            runOnUiThread(new Runnable() {
                                @Override
                                public void run() {
                                    I18n.makeText(MainActivity.this, I18n.t("app.errore_svuotamento_chat", error), Toast.LENGTH_SHORT).show();
                                }
                            });
                        }
                    });
                }
            })
            .setNegativeButton(I18n.t("app.annulla"), null)
            .show();
    }

    // ================= BACKUP & REAL-TIME PROGRESS BAR =================

    private void startBackupAction() {
        if (!"OFFLINE".equalsIgnoreCase(mServerState) && !"STOPPED".equalsIgnoreCase(mServerState)) {
            new I18n.Builder(this)
                .setTitle(I18n.t("app.server_attivo_2"))
                .setMessage(I18n.t("app.effettuare_backup_senza_corrompere"))
                .setPositiveButton(I18n.t("app.salva_mondo_game_save_all"), new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        submitCommand("save-all");
                        I18n.makeText(MainActivity.this, I18n.t("app.comando_save_all_inviato_mondo_stato"), Toast.LENGTH_LONG).show();
                    }
                })
                .setNeutralButton(I18n.t("app.spegni_server_fai_backup"), new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        // Prima il server si spegneva ma il backup non partiva mai
                        confirmStopServer(true);
                    }
                })
                .setNegativeButton(I18n.t("app.annulla"), null)
                .show();
            return;
        }

        // Se un'operazione è già in corso, il pulsante serve ad annullarla
        if (mWasRunningBackup || mWasRunningRestore) {
            new I18n.Builder(this)
                .setTitle(I18n.t("app.annulla_operazione"))
                .setMessage(I18n.t("app.sei_sicuro_voler_interrompere"))
                .setPositiveButton(I18n.t("app.si_interrompi"), new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        executeApiCall("/api/backup/cancel", "POST", "{}", new ApiClient.ApiCallback() {
                            @Override public void onSuccess(String resp) {
                                I18n.makeText(MainActivity.this, I18n.t("app.richiesta_annullamento_inviata"), Toast.LENGTH_SHORT).show();
                                fetchBackupStatus();
                            }
                            @Override public void onError(String err) {
                                I18n.makeText(MainActivity.this, I18n.t("app.errore_2", err), Toast.LENGTH_SHORT).show();
                            }
                        });
                    }
                })
                .setNegativeButton("No", null)
                .show();
            return;
        }

        final SharedPreferences prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        boolean lastCloudChoice = prefs.getBoolean("backup_copy_to_cloud", true);

        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setPadding(40, 20, 40, 10);

        TextView tvMsg = new TextView(this);
        tvMsg.setText(I18n.t("app.verra_creato_archivio_completo_tutta"));
        tvMsg.setTextColor(Color.parseColor("#c4c4c8"));
        tvMsg.setTextSize(13);
        layout.addView(tvMsg);

        // Peso previsto del backup e spazio libero su Desktop e Google Drive (come nel pannello del PC)
        final TextView tvSize = new TextView(this);
        tvSize.setText(I18n.t("app.peso_backup_calcolo_corso"));
        tvSize.setTextColor(Color.parseColor("#93c5fd"));
        tvSize.setTextSize(13);
        tvSize.setPadding(0, 16, 0, 0);
        layout.addView(tvSize);
        loadBackupEstimate(tvSize);

        final CheckBox chkCloud = new CheckBox(this);
        chkCloud.setText(I18n.t("app.copia_anche_google_drive"));
        chkCloud.setChecked(lastCloudChoice);
        chkCloud.setTextColor(Color.parseColor("#9ccc65"));
        chkCloud.setPadding(10, 20, 10, 10);
        layout.addView(chkCloud);

        TextView tvDriveNote = new TextView(this);
        tvDriveNote.setText(I18n.t("app.copia_drive_sul_pc_deve_essere"));
        tvDriveNote.setTextColor(Color.parseColor("#9a9ca2"));
        tvDriveNote.setTextSize(12);
        layout.addView(tvDriveNote);

        new I18n.Builder(this)
            .setTitle(I18n.t("app.avvia_nuovo_backup"))
            .setView(layout)
            .setPositiveButton(I18n.t("app.avvia_backup"), new DialogInterface.OnClickListener() {
                @Override
                public void onClick(DialogInterface dialog, int which) {
                    boolean copyCloud = chkCloud.isChecked();
                    prefs.edit().putBoolean("backup_copy_to_cloud", copyCloud).apply();

                    btnTriggerBackup.setEnabled(false);
                    progBarBackup.setVisibility(View.VISIBLE);
                    progBarBackup.setIndeterminate(true);
                    txtBackupStatusBadge.setText(I18n.t("app.avvio_corso"));
                    txtBackupStatusBadge.setTextColor(Color.parseColor("#e3b35a"));

                    String payload = "{\"copy_to_cloud\":" + copyCloud + "}";
                    executeApiCall("/api/backup/start", "POST", payload, new ApiClient.ApiCallback() {
                        @Override
                        public void onSuccess(final String response) {
                            runOnUiThread(new Runnable() {
                                @Override
                                public void run() {
                                    I18n.makeText(MainActivity.this, I18n.t("app.backup_avviato_sul_pc"), Toast.LENGTH_SHORT).show();
                                    fetchBackupStatus();
                                }
                            });
                        }

                        @Override
                        public void onError(final String error) {
                            runOnUiThread(new Runnable() {
                                @Override
                                public void run() {
                                    btnTriggerBackup.setEnabled(true);
                                    progBarBackup.setVisibility(View.GONE);
                                    txtBackupStatusBadge.setText(I18n.t("app.errore_avvio"));
                                    txtBackupStatusBadge.setTextColor(Color.parseColor("#e5566f"));
                                    I18n.makeText(MainActivity.this, I18n.t("app.impossibile_avviare_backup", error), Toast.LENGTH_LONG).show();
                                }
                            });
                        }
                    });
                }
            })
            .setNegativeButton(I18n.t("app.annulla"), null)
            .show();
    }

    // ================= PIÙ SERVER (ISTANZE) =================

    /** Elenco dei server del PC, al massimo ogni 20 secondi (lo chiama fetchStatus). */
    private void fetchInstancesIfDue() {
        long now = System.currentTimeMillis();
        if (now - mLastInstancesFetch < 20000) return;
        mLastInstancesFetch = now;
        fetchInstances(true);
    }

    private void fetchInstances() {
        fetchInstances(false);
    }

    /** background: lettura periodica, senza login con PIN né passaggio all'indirizzo da internet se non riesce
     *  (executeApiCall li farebbe a ogni errore, ogni 20 secondi). */
    private void fetchInstances(final boolean background) {
        ApiClient.ApiCallback cb = new ApiClient.ApiCallback() {
            @Override
            public void onSuccess(String response) {
                try {
                    mInstancesFailures = 0;
                    JSONObject obj = new JSONObject(response);
                    JSONArray list = obj.optJSONArray("instances");
                    mInstances = list != null ? list : new JSONArray();
                    JSONObject active = obj.optJSONObject("active_instance");
                    mActiveInstanceName = active != null ? active.optString("name", "") : "";
                    mActiveInstanceDetails = active != null
                        ? (active.optString("server_type", "") + " " + active.optString("mc_version", "")).trim() : "";
                    // Il sottotitolo della Dashboard dice quale server è; acceso, la versione la dà il server stesso
                    // (se non la dà, resta questa al posto di "Voxarium")
                    if (mCurrentSectionIndex == 0 && txtHeaderSubtitle != null && (!"ONLINE".equalsIgnoreCase(mServerState)
                            || "Voxarium".contentEquals(txtHeaderSubtitle.getText()))) {
                        txtHeaderSubtitle.setText(mActiveInstanceDetails.isEmpty() ? "Voxarium" : mActiveInstanceDetails);
                    }
                    updateServerSwitchUI();
                } catch (Exception ignored) {
                }
            }

            @Override
            public void onError(String error) {
                // PC non raggiungibile o login non ancora fatto (all'apertura dell'app): il selettore resta com'era.
                // Si riprova tra 5 secondi, poi una volta al minuto (es. PIN rifiutato: niente richieste continue)
                if (background) {
                    mInstancesFailures++;
                    mLastInstancesFetch = System.currentTimeMillis() + (mInstancesFailures == 1 ? -15000 : 40000);
                }
            }
        };
        String base = getEffectiveBaseUrl();
        if (background) {
            if (base != null && !base.isEmpty()) ApiClient.call(base + "/api/instances", "GET", null, mAuthToken, cb);
        } else {
            executeApiCall("/api/instances", "GET", null, cb);
        }
    }

    private void updateServerSwitchUI() {
        if (btnServerSwitch == null) return;
        if (txtDrawerServerName != null) {
            txtDrawerServerName.setText(mInstances.length() >= 2 && !mActiveInstanceName.isEmpty()
                ? mActiveInstanceName : I18n.t("ui.server_principale"));
        }
        if (mInstances.length() < 2) {
            btnServerSwitch.setVisibility(View.GONE);
            return;
        }
        String name = mActiveInstanceName.isEmpty() ? "?" : mActiveInstanceName;
        btnServerSwitch.setText(I18n.t("app.server_attivo_selettore", name, mInstances.length()));
        btnServerSwitch.setVisibility(View.VISIBLE);
    }

    private boolean isServerStoppedForSwitch() {
        return "OFFLINE".equalsIgnoreCase(mServerState) || "STOPPED".equalsIgnoreCase(mServerState)
            || "CRASHED".equalsIgnoreCase(mServerState);
    }

    private void showServerPicker() {
        final int n = mInstances.length();
        if (n == 0) {
            fetchInstances();
            return;
        }
        final String[] items = new String[n];
        final String[] ids = new String[n];
        final String[] names = new String[n];
        int activeIndex = -1;
        for (int i = 0; i < n; i++) {
            JSONObject inst = mInstances.optJSONObject(i);
            if (inst == null) inst = new JSONObject();
            ids[i] = inst.optString("id", "");
            names[i] = inst.optString("name", "Server");
            StringBuilder desc = new StringBuilder(names[i]);
            String details = (inst.optString("server_type", "") + " " + inst.optString("mc_version", "")).trim();
            int port = inst.optInt("server_port", 0);
            if (port > 0) {
                details = details.isEmpty() ? I18n.t("app.porta_n", port) : details + " • " + I18n.t("app.porta_n", port);
            }
            if (!details.isEmpty()) desc.append("\n").append(details);
            if (inst.optBoolean("is_active", false)) {
                activeIndex = i;
                desc.append("\n").append(I18n.t("app.server_in_uso"));
            }
            if (!inst.optBoolean("dir_exists", true)) desc.append("\n").append(I18n.t("app.server_cartella_mancante"));
            items[i] = desc.toString();
        }
        final int current = activeIndex;
        new I18n.Builder(this)
            .setTitle(I18n.t("app.scegli_server"))
            .setSingleChoiceItems(items, current, new DialogInterface.OnClickListener() {
                @Override
                public void onClick(DialogInterface dialog, int which) {
                    dialog.dismiss();
                    if (which == current) return;
                    switchToServer(ids[which], names[which]);
                }
            })
            .setNegativeButton(I18n.t("app.annulla"), null)
            .show();
    }

    private void switchToServer(final String instanceId, final String name) {
        if (!isServerStoppedForSwitch()) {
            // Sul PC gira un solo server alla volta: prima va spento quello attivo
            new I18n.Builder(this)
                .setTitle(I18n.t("app.server_attivo_2"))
                .setMessage(I18n.t("app.spegni_prima_di_cambiare_server", mActiveInstanceName, name))
                .setPositiveButton(I18n.t("app.spegni_server_ora"), new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        confirmStopServer();
                    }
                })
                .setNegativeButton(I18n.t("app.annulla"), null)
                .show();
            return;
        }
        try {
            JSONObject body = new JSONObject();
            body.put("instance_id", instanceId);
            executeApiCall("/api/instances/switch", "POST", body.toString(), new ApiClient.ApiCallback() {
                @Override
                public void onSuccess(String response) {
                    I18n.makeText(MainActivity.this, I18n.t("app.server_attivato", name), Toast.LENGTH_SHORT).show();
                    mActiveInstanceName = name;
                    mLastInstancesFetch = 0;
                    updateServerSwitchUI();
                    fetchStatus();
                    if (viewSettings.getVisibility() == View.VISIBLE) fetchBackupStatus();
                }

                @Override
                public void onError(String error) {
                    new I18n.Builder(MainActivity.this)
                        .setTitle(I18n.t("app.cambio_server_non_riuscito"))
                        .setMessage(error)
                        .setPositiveButton("OK", null)
                        .show();
                }
            });
        } catch (Exception e) {
            I18n.makeText(this, String.valueOf(e.getMessage()), Toast.LENGTH_SHORT).show();
        }
    }

    private void startRestoreAction() {
        if (!"OFFLINE".equalsIgnoreCase(mServerState) && !"STOPPED".equalsIgnoreCase(mServerState)) {
            new I18n.Builder(this)
                .setTitle(I18n.t("app.server_attivo_2"))
                .setMessage(I18n.t("app.ripristinare_mondo_modo_sicuro_senza"))
                .setPositiveButton(I18n.t("app.spegni_server_ora"), new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        confirmStopServer();
                    }
                })
                .setNegativeButton(I18n.t("app.annulla"), null)
                .show();
            return;
        }

        I18n.makeText(this, I18n.t("app.ricerca_backup_disponibili_corso"), Toast.LENGTH_SHORT).show();
        fetchInstances();  // nome del server attivo aggiornato per l'avviso sui backup di altri server
        executeApiCall("/api/backup/list", "GET", null, new ApiClient.ApiCallback() {
            @Override
            public void onSuccess(final String response) {
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        try {
                            JSONObject obj = new JSONObject(response);
                            JSONArray backups = obj.optJSONArray("backups");
                            if (backups == null || backups.length() == 0) {
                                new I18n.Builder(MainActivity.this)
                                    .setTitle(I18n.t("app.nessun_backup_trovato"))
                                    .setMessage(I18n.t("app.non_stato_trovato_alcun_archivio_zip"))
                                    .setPositiveButton("OK", null)
                                    .show();
                                return;
                            }

                            final String[] items = new String[backups.length()];
                            final String[] paths = new String[backups.length()];
                            final String[] labels = new String[backups.length()];
                            // Di quale server è ogni backup: 1 = server attivo, 0 = un altro, -1 = non si sa (backup vecchio)
                            final int[] sameServer = new int[backups.length()];
                            final String[] backupServers = new String[backups.length()];

                            for (int i = 0; i < backups.length(); i++) {
                                JSONObject b = backups.getJSONObject(i);
                                String loc = b.optString("location", "desktop");
                                String filename = b.optString("filename", "Backup_Server_Mc.zip");
                                double sizeMb = b.optDouble("size_mb", 0.0);
                                String mod = b.optString("modified", "");
                                String p = b.optString("path", "");

                                String locLabel = "cloud".equalsIgnoreCase(loc) ? I18n.t("app.cloud_google_drive") : I18n.t("app.desktop_pc_2");
                                String desc = locLabel + "\n" + filename + " (" + String.format(java.util.Locale.US, "%.1f MB", sizeMb) + ")\n" + mod;
                                backupServers[i] = b.isNull("server_name") ? "" : b.optString("server_name", "");
                                sameServer[i] = b.isNull("same_server") ? -1 : (b.optBoolean("same_server", false) ? 1 : 0);
                                if (!backupServers[i].isEmpty()) {
                                    desc += "\n" + I18n.t("app.backup_del_server", backupServers[i])
                                        + (sameServer[i] == 1 ? " (" + I18n.t("app.server_in_uso") + ")" : "");
                                } else if (mInstances.length() > 1) {
                                    desc += "\n" + I18n.t("app.backup_server_non_indicato");
                                }
                                items[i] = desc;
                                paths[i] = p;
                                labels[i] = locLabel + " (" + filename + ")";
                            }

                            new I18n.Builder(MainActivity.this)
                                .setTitle(I18n.t("app.seleziona_archivio_ripristinare"))
                                .setItems(items, new DialogInterface.OnClickListener() {
                                    @Override
                                    public void onClick(DialogInterface dialog, int which) {
                                        confirmExecuteRestore(paths[which], labels[which], sameServer[which], backupServers[which]);
                                    }
                                })
                                .setNegativeButton(I18n.t("app.annulla"), null)
                                .show();

                        } catch (Exception e) {
                            I18n.makeText(MainActivity.this, I18n.t("app.errore_lettura_lista_backup", e.getMessage()), Toast.LENGTH_LONG).show();
                        }
                    }
                });
            }

            @Override
            public void onError(final String error) {
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        I18n.makeText(MainActivity.this, I18n.t("app.impossibile_recuperare_lista_backup", error), Toast.LENGTH_LONG).show();
                    }
                });
            }
        });
    }

    private void confirmExecuteRestore(final String sourcePath, final String label, final int sameServer, final String backupServer) {
        // Il ripristino cancella i file che non sono nel backup: un backup di un altro server sostituirebbe
        // tutto il server attivo, quindi lo si dice chiaramente (il PC lo accetta solo con confirm_other_server)
        String current = mActiveInstanceName.isEmpty() ? "?" : mActiveInstanceName;
        String message;
        if (sameServer == 0) {
            String other = backupServer.isEmpty() ? "?" : backupServer;
            message = I18n.t("app.backup_altro_server_conferma", other, current, current, other);
        } else if (sameServer == -1 && mInstances.length() > 1) {
            message = I18n.t("app.backup_server_ignoto_conferma", current);
        } else {
            message = I18n.t("app.attenzione_ripristino_sovrascrivera", label);
        }
        // Conferma per il PC solo se l'utente ha letto l'avviso; altrimenti è il PC a fermare il ripristino (409) e
        // l'app ne mostra il messaggio (es. elenco dei server non ancora letto)
        final boolean confirmOther = sameServer == 0 || (sameServer == -1 && mInstances.length() > 1);
        new I18n.Builder(this)
            .setTitle(I18n.t("app.conferma_ripristino_backup"))
            .setMessage(message)
            .setIcon(android.R.drawable.ic_dialog_alert)
            .setPositiveButton(I18n.t("app.si_ripristina_mondo"), new DialogInterface.OnClickListener() {
                @Override
                public void onClick(DialogInterface dialog, int which) {
                    executeRestoreApi(sourcePath, confirmOther);
                }
            })
            .setNegativeButton(I18n.t("app.annulla"), null)
            .show();
    }

    private void executeRestoreApi(String sourcePath, boolean confirmOtherServer) {
        if (btnRestoreBackup != null) btnRestoreBackup.setEnabled(false);
        if (btnTriggerBackup != null) btnTriggerBackup.setEnabled(false);
        progBarBackup.setVisibility(View.VISIBLE);
        progBarBackup.setIndeterminate(true);
        txtBackupStatusBadge.setText(I18n.t("app.avvio_ripristino"));
        txtBackupStatusBadge.setTextColor(Color.parseColor("#e3b35a"));

        try {
            JSONObject body = new JSONObject();
            body.put("source_path", sourcePath);
            if (confirmOtherServer) body.put("confirm_other_server", true);

            executeApiCall("/api/backup/restore", "POST", body.toString(), new ApiClient.ApiCallback() {
                @Override
                public void onSuccess(String response) {
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            I18n.makeText(MainActivity.this, I18n.t("app.ripristino_mondo_avviato_successo"), Toast.LENGTH_SHORT).show();
                            fetchBackupStatus();
                        }
                    });
                }

                @Override
                public void onError(final String error) {
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            if (btnRestoreBackup != null) btnRestoreBackup.setEnabled(true);
                            if (btnTriggerBackup != null) btnTriggerBackup.setEnabled(true);
                            progBarBackup.setVisibility(View.GONE);
                            txtBackupStatusBadge.setText(I18n.t("app.errore_ripristino_2"));
                            txtBackupStatusBadge.setTextColor(Color.parseColor("#f0788c"));
                            new I18n.Builder(MainActivity.this)
                                .setTitle(I18n.t("app.errore_ripristino_3"))
                                .setMessage(I18n.t("app.impossibile_avviare_ripristino", error))
                                .setPositiveButton("OK", null)
                                .show();
                        }
                    });
                }
            });
        } catch (Exception e) {
            I18n.makeText(this, I18n.t("app.errore_preparazione_richiesta", e.getMessage()), Toast.LENGTH_SHORT).show();
        }
    }

    private void fetchBackupStatus() {
        executeApiCall("/api/backup/status", "GET", null, new ApiClient.ApiCallback() {
            @Override
            public void onSuccess(String response) {
                try {
                    JSONObject obj = new JSONObject(response);
                    boolean isRunning = obj.optBoolean("active", obj.optBoolean("running", obj.optBoolean("in_progress", false)));
                    int percent = obj.optInt("percent", 0);
                    double processedMb = obj.optDouble("processed_mb", 0.0);
                    double totalMb = obj.optDouble("total_mb", 0.0);
                    String statusMsg = obj.optString("status", I18n.t("app.elaborazione"));
                    String currentFile = obj.optString("current_file", "");
                    boolean exists = obj.optBoolean("existing_backup_exists", false);
                    double sizeMb = obj.optDouble("existing_backup_size_mb", 0.0);
                    String date = obj.optString("existing_backup_date", "");
                    String targetFilename = obj.optString("target_filename", "Backup_Server_Mc.zip");

                    String operation = obj.optString("operation", statusMsg.toLowerCase().contains("ripristin") ? "restore" : "backup");
                    boolean isRestore = "restore".equals(operation);

                    if (isRunning) {
                        mWasRunningBackup = !isRestore;
                        mWasRunningRestore = isRestore;
                        progBarBackup.setVisibility(View.VISIBLE);
                        progBarBackup.setIndeterminate(false);
                        progBarBackup.setMax(100);
                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
                            progBarBackup.setProgress(percent, true);
                        } else {
                            progBarBackup.setProgress(percent);
                        }
                        btnTriggerBackup.setEnabled(true);
                        btnTriggerBackup.setText(I18n.t("app.annulla_operazione"));
                        btnTriggerBackup.setBackgroundResource(R.drawable.btn_red);
                        if (btnRestoreBackup != null) btnRestoreBackup.setEnabled(false);

                        double speedMb = obj.optDouble("speed_mb_s", 0.0);
                        int elapsedSec = obj.optInt("elapsed_seconds", 0);
                        String etaFormatted = obj.optString("eta_formatted", "");
                        int etaSeconds = obj.optInt("eta_seconds", -1);

                        if (speedMb <= 0 && elapsedSec > 0 && processedMb > 0) {
                            speedMb = Math.round((processedMb / elapsedSec) * 10.0) / 10.0;
                        }

                        if (etaSeconds > 0) {
                            if (etaSeconds < 60) etaFormatted = I18n.t("app.s_rimanenti", etaSeconds);
                            else if (etaSeconds < 3600) etaFormatted = I18n.t("app.m_s_rimanenti", (etaSeconds / 60), (etaSeconds % 60));
                            else etaFormatted = I18n.t("app.h_m_rimanenti", (etaSeconds / 3600), ((etaSeconds % 3600) / 60));
                        } else if (speedMb > 0 && totalMb > processedMb) {
                            double remMb = totalMb - processedMb;
                            int sec = (int) Math.round(remMb / speedMb);
                            if (sec < 60) etaFormatted = I18n.t("app.s_rimanenti", sec);
                            else if (sec < 3600) etaFormatted = I18n.t("app.m_s_rimanenti", (sec / 60), (sec % 60));
                            else etaFormatted = I18n.t("app.h_m_rimanenti", (sec / 3600), ((sec % 3600) / 60));
                        } else if (percent >= 99) {
                            etaFormatted = I18n.t("app.finalizzazione");
                        } else {
                            etaFormatted = I18n.t("app.calcolo_corso");
                        }

                        if (isRestore) {
                            txtBackupStatusBadge.setText(I18n.t("app.ripristino_corso", percent));
                            txtBackupStatusBadge.setTextColor(Color.parseColor("#e3b35a"));
                        } else {
                            txtBackupStatusBadge.setText(I18n.t("app.mancano", etaFormatted, percent));
                            txtBackupStatusBadge.setTextColor(Color.parseColor("#9ccc65"));
                        }

                        StringBuilder sb = new StringBuilder();
                        if (!statusMsg.isEmpty()) {
                            sb.append("").append(statusMsg).append("\n");
                        }
                        if (!isRestore) {
                            sb.append((I18n.t("app.tempo_rimanente") + " ")).append(etaFormatted).append("\n");
                        }
                        if (speedMb > 0) {
                            sb.append((I18n.t("app.velocita") + " ")).append(String.format(java.util.Locale.US, "%.1f MB/s", speedMb));
                            if (elapsedSec > 0) {
                                int m = elapsedSec / 60;
                                int s = elapsedSec % 60;
                                sb.append((" " + I18n.t("app.trascorso") + " ")).append(m > 0 ? (m + "m " + s + "s") : (s + "s"));
                            }
                            sb.append("\n");
                        }
                        if (totalMb > 0 && !isRestore) {
                            if (totalMb >= 1024) {
                                sb.append(String.format(java.util.Locale.US, "Progresso: %.2f / %.2f GB (%d%%)\n", processedMb / 1024.0, totalMb / 1024.0, percent));
                            } else {
                                sb.append(String.format(java.util.Locale.US, "Progresso: %.1f / %.1f MB (%d%%)\n", processedMb, totalMb, percent));
                            }
                        }
                        int filesProcessed = obj.optInt("files_processed", 0);
                        int filesTotal = obj.optInt("files_total", 0);
                        if (filesTotal > 0) {
                            sb.append((I18n.t("app.file") + " ")).append(filesProcessed).append(" / ").append(filesTotal).append("\n");
                        }
                        if (!currentFile.isEmpty()) {
                            sb.append(isRestore ? (I18n.t("app.estrazione") + " ") : (I18n.t("app.scrittura") + " ")).append(currentFile);
                        }
                        txtBackupInfo.setText(sb.toString().trim());

                        mPollHandler.postDelayed(new Runnable() {
                            @Override
                            public void run() {
                                if (!isFinishing()) {
                                    fetchBackupStatus();
                                }
                            }
                        }, 1000);
                    } else {
                        String err = obj.optString("error", "");
                        boolean completed = !obj.isNull("completed_at") && obj.optInt("percent", 0) >= 100 && (err.isEmpty() || "null".equals(err));

                        if (mWasRunningBackup) {
                            mWasRunningBackup = false;
                            if (completed) {
                                if (!date.isEmpty() && !date.equals(mLastNotifiedBackupDate)) {
                                    mLastNotifiedBackupDate = date;
                                    showBackupCompleteDialog(sizeMb, date, targetFilename);
                                    showLocalBackupCompleteNotification(sizeMb, targetFilename);
                                }
                            } else {
                                I18n.makeText(MainActivity.this, I18n.t("app.backup_non_completato", (err.isEmpty() || "null".equals(err) ? I18n.t("app.annullato") : err)), Toast.LENGTH_LONG).show();
                            }
                        }

                        if (mWasRunningRestore) {
                            mWasRunningRestore = false;
                            if (err.isEmpty() || "null".equals(err)) {
                                new I18n.Builder(MainActivity.this)
                                    .setTitle(I18n.t("app.ripristino_completato"))
                                    .setMessage(I18n.t("app.mondo_dati_server_minecraft_sono_stati"))
                                    .setPositiveButton("OK", null)
                                    .show();
                            } else {
                                new I18n.Builder(MainActivity.this)
                                    .setTitle(I18n.t("app.ripristino_non_riuscito"))
                                    .setMessage(I18n.t("app.errore_durante_ripristino", err))
                                    .setPositiveButton(I18n.t("app.chiudi"), null)
                                    .show();
                            }
                        }

                        progBarBackup.setVisibility(View.GONE);
                        btnTriggerBackup.setEnabled(true);
                        btnTriggerBackup.setText(I18n.t("app.crea_nuovo_backup"));
                        btnTriggerBackup.setBackgroundResource(R.drawable.btn_blue);
                        if (btnRestoreBackup != null) btnRestoreBackup.setEnabled(true);
                        if (exists) {
                            txtBackupStatusBadge.setText(I18n.t("app.pronto_mb", sizeMb));
                            txtBackupStatusBadge.setTextColor(Color.parseColor("#a5d36f"));
                            txtBackupInfo.setText(I18n.t("app.ultimo_backup_creato_data_mb", targetFilename, date, sizeMb));
                        } else {
                            txtBackupStatusBadge.setText(I18n.t("ui.attesa"));
                            txtBackupStatusBadge.setTextColor(Color.parseColor("#9a9ca2"));
                            txtBackupInfo.setText(I18n.t("app.nessun_archivio_zip_recente_sul"));
                        }
                    }
                } catch (Exception ignored) {}
            }

            @Override
            public void onError(String error) {
                progBarBackup.setVisibility(View.GONE);
                btnTriggerBackup.setEnabled(true);
                if (btnRestoreBackup != null) btnRestoreBackup.setEnabled(true);
            }
        });
    }

    private void showBackupCompleteDialog(final double sizeMb, final String date, final String filename) {
        runOnUiThread(new Runnable() {
            @Override
            public void run() {
                try {
                    new I18n.Builder(MainActivity.this)
                        .setTitle(I18n.t("app.backup_completato"))
                        .setMessage(I18n.t("app.backup_server_minecraft_stato_creato", filename, sizeMb, date))
                        .setIcon(R.drawable.ic_launcher)
                        .setPositiveButton(I18n.t("app.fantastico"), null)
                        .show();
                } catch (Exception ignored) {}
            }
        });
    }

    private void showLocalBackupCompleteNotification(double sizeMb, String filename) {
        try {
            NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
            if (nm == null) return;

            Intent intent = new Intent(this, MainActivity.class);
            intent.addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
            intent.putExtra("target_section", 8);

            int flags = PendingIntent.FLAG_UPDATE_CURRENT;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                flags |= PendingIntent.FLAG_IMMUTABLE;
            }
            PendingIntent pi = PendingIntent.getActivity(this, 105, intent, flags);

            Notification.Builder builder;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                builder = new Notification.Builder(this, "mc_backup_alerts");
            } else {
                builder = new Notification.Builder(this);
                builder.setPriority(Notification.PRIORITY_HIGH);
            }

            builder.setSmallIcon(R.drawable.ic_stat_voxarium)
                   .setContentTitle(I18n.t("app.backup_server_completato"))
                   .setContentText(I18n.t("app.archivio_mb_pronto", filename, sizeMb))
                   .setStyle(new Notification.BigTextStyle().bigText(I18n.t("app.backup_mondo_minecraft_stato", sizeMb, filename)))
                   .setAutoCancel(true)
                   .setContentIntent(pi)
                   .setPriority(Notification.PRIORITY_HIGH);

            nm.notify(1003, builder.build());
        } catch (Exception ignored) {}
    }

    private void fetchTunnelStatus() {
        executeApiCall("/api/remote/status", "GET", null, new ApiClient.ApiCallback() {
            @Override
            public void onSuccess(final String response) {
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        try {
                            JSONObject obj = new JSONObject(response);
                            String url = obj.optString("url", "").replaceAll("/+$", "");
                            boolean mayReplace = mayReplaceTrustData(getEffectiveBaseUrl());
                            if (mayReplace) PcTls.saveFingerprint(MainActivity.this, obj.optString("fingerprint", ""));
                            else PcTls.saveFingerprintIfMissing(MainActivity.this, obj.optString("fingerprint", ""));
                            if (PcTls.REMOTE_URL_PATTERN.matcher(url).matches() && !url.equalsIgnoreCase(edtRemoteUrl.getText().toString())) {
                                edtRemoteUrl.setText(url);
                                mRemoteUrl = url;
                            }
                        } catch (Exception ignored) {}
                    }
                });
            }
            @Override public void onError(String error) {}
        });
    }

    // ================= STORAGE BREAKDOWN & CLEAN LOGS =================

    private void fetchStorageBreakdown() {
        executeApiCall("/api/storage/breakdown", "GET", null, new ApiClient.ApiCallback() {
            @Override
            public void onSuccess(final String response) {
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        try {
                            JSONObject obj = new JSONObject(response);
                            double totalGb = obj.optDouble("total_gb", 0.0);
                            double worldGb = obj.optDouble("world_gb", 0.0);
                            double modsMb = obj.optDouble("mods_mb", 0.0);
                            double logsCleanableMb = obj.optDouble("logs_cleanable_mb", 0.0);

                            if (txtStorageBreakdown != null) {
                                String info = String.format(java.util.Locale.US,
                                    "Spazio totale: %.2f GB\n• Mondo: %.2f GB | • Mod: %.1f MB\n• Log archiviati pulibili: %.1f MB",
                                    totalGb, worldGb, modsMb, logsCleanableMb);
                                txtStorageBreakdown.setText(info);
                            }
                        } catch (Exception ignored) {}
                    }
                });
            }
            @Override public void onError(String error) {}
        });
    }

    private void actionCleanOldLogs() {
        new I18n.Builder(this)
            .setTitle(I18n.t("app.pulizia_log_vecchi"))
            .setMessage(I18n.t("app.vuoi_eliminare_tutti_file_log"))
            .setPositiveButton(I18n.t("app.si_pulisci_ora"), new DialogInterface.OnClickListener() {
                @Override
                public void onClick(DialogInterface dialog, int which) {
                    executeApiCall("/api/storage/clean-logs", "POST", "{}", new ApiClient.ApiCallback() {
                        @Override
                        public void onSuccess(final String response) {
                            runOnUiThread(new Runnable() {
                                @Override
                                public void run() {
                                    try {
                                        JSONObject obj = new JSONObject(response);
                                        int count = obj.optInt("deleted_count", 0);
                                        double freedMb = obj.optDouble("freed_mb", 0.0);
                                        vibrate(30);
                                        new I18n.Builder(MainActivity.this)
                                            .setTitle(I18n.t("app.pulizia_completata_2"))
                                            .setMessage(I18n.t("app.rimossi_successo_file_log_vecchi", count, freedMb))
                                            .setPositiveButton(I18n.t("app.ottimo"), null)
                                            .show();
                                        fetchStorageBreakdown();
                                    } catch (Exception e) {
                                        I18n.makeText(MainActivity.this, I18n.t("app.pulizia_completata"), Toast.LENGTH_SHORT).show();
                                    }
                                }
                            });
                        }

                        @Override
                        public void onError(final String error) {
                            runOnUiThread(new Runnable() {
                                @Override
                                public void run() {
                                    I18n.makeText(MainActivity.this, I18n.t("app.errore_durante_pulizia", error), Toast.LENGTH_LONG).show();
                                }
                            });
                        }
                    });
                }
            })
            .setNegativeButton(I18n.t("app.annulla"), null)
            .show();
    }

    // ================= BACKUP: PESO PREVISTO E SPAZIO LIBERO =================

    private void loadBackupEstimate(final TextView target) {
        executeApiCall("/api/backup/estimate", "GET", null, new ApiClient.ApiCallback() {
            @Override
            public void onSuccess(final String response) {
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        try {
                            JSONObject e = new JSONObject(response);
                            StringBuilder sb = new StringBuilder();
                            sb.append((I18n.t("app.peso_backup_circa") + " ")).append(e.optDouble("total_gb", 0)).append(" GB (")
                              .append(e.optInt("files", 0)).append(" file)");
                            sb.append(("\n" + I18n.t("app.desktop_pc") + " "));
                            if (e.isNull("desktop_free_gb")) sb.append(I18n.t("app.spazio_non_leggibile"));
                            else sb.append(e.optDouble("desktop_free_gb", 0)).append((" " + I18n.t("app.gb_liberi")))
                                   .append(e.optBoolean("desktop_fits", true) ? I18n.t("app.ci_sta") : I18n.t("app.non_ci_sta"));
                            sb.append("\nGoogle Drive: ");
                            if (!e.optBoolean("cloud_configured", false)) sb.append(I18n.t("app.non_collegato"));
                            else if (!e.optBoolean("cloud_available", false)) sb.append(I18n.t("app.cartella_non_raggiungibile_drive"));
                            else if (e.isNull("cloud_free_gb")) sb.append(I18n.t("app.spazio_non_leggibile"));
                            else sb.append(e.optDouble("cloud_free_gb", 0)).append((" " + I18n.t("app.gb_liberi")))
                                   .append(e.optBoolean("cloud_fits", true) ? I18n.t("app.ci_sta") : I18n.t("app.non_ci_sta"));
                            target.setText(sb.toString());
                            boolean problem = !e.optBoolean("desktop_fits", true)
                                || (e.optBoolean("cloud_available", false) && !e.optBoolean("cloud_fits", true));
                            target.setTextColor(Color.parseColor(problem ? "#e5566f" : "#93c5fd"));
                        } catch (Exception ex) {
                            target.setText(I18n.t("app.peso_backup_non_calcolabile"));
                        }
                    }
                });
            }

            @Override
            public void onError(final String error) {
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        // PC con una versione del programma precedente alla 4.14.0: l'indirizzo non esiste ancora
                        target.setText(I18n.t("app.peso_backup_non_disponibile_aggiorna"));
                    }
                });
            }
        });
    }

    // ================= JVM TUNING & RAM ALLOCATION =================

    private void setJvmRamUI(int ramGb) {
        mSelectedJvmRam = ramGb;
        String label = ramGb > 0 ? ramGb + " GB" : I18n.t("app.non_impostata");
        if (txtJvmRamBadge != null) {
            txtJvmRamBadge.setText(label);
        }
        if (btnJvmRamPick != null) {
            btnJvmRamPick.setText(I18n.t("app.ram_server", label));
        }
    }

    // Stessa lista del pannello del PC: da 2 GB fino alla RAM del PC meno 2 GB per Windows (+1 GB fino a 12, poi +2)
    private void showJvmRamPicker() {
        final java.util.List<Integer> values = new java.util.ArrayList<>();
        int maxGb = Math.max(2, mHostRamGb - 2);
        for (int v = 2; v <= maxGb; v += (v < 12 ? 1 : 2)) values.add(v);
        if (mSelectedJvmRam > 0 && !values.contains(mSelectedJvmRam)) {
            values.add(mSelectedJvmRam);
            java.util.Collections.sort(values);
        }
        String[] labels = new String[values.size()];
        int checked = -1;
        for (int i = 0; i < values.size(); i++) {
            labels[i] = values.get(i) + " GB";
            if (values.get(i) == mSelectedJvmRam) checked = i;
        }
        new I18n.Builder(this)
            .setTitle(I18n.t("app.ram_server_pc_ha_gb", mHostRamGb))
            .setSingleChoiceItems(labels, checked, new DialogInterface.OnClickListener() {
                @Override
                public void onClick(DialogInterface dialog, int which) {
                    setJvmRamUI(values.get(which));
                    dialog.dismiss();
                }
            })
            .setNegativeButton(I18n.t("app.annulla"), null)
            .show();
    }

    private void fetchJvmTuning() {
        executeApiCall("/api/server/jvm-tuning", "GET", null, new ApiClient.ApiCallback() {
            @Override
            public void onSuccess(final String response) {
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        try {
                            JSONObject obj = new JSONObject(response);
                            // ram_gb null = RAM non impostata nel server (prima l'app mostrava 16 GB)
                            int ram = obj.isNull("ram_gb") ? 0 : obj.optInt("ram_gb", 0);
                            mHostRamGb = obj.optInt("host_ram_gb", mHostRamGb);
                            boolean aikar = obj.optBoolean("use_aikar_flags", true);
                            setJvmRamUI(ram);
                            if (chkJvmAikar != null) {
                                chkJvmAikar.setChecked(aikar);
                            }
                        } catch (Exception ignored) {}
                    }
                });
            }
            @Override public void onError(String error) {}
        });
    }

    private void actionSaveJvmTuning() {
        if (!"OFFLINE".equalsIgnoreCase(mServerState) && !"STOPPED".equalsIgnoreCase(mServerState)) {
            new I18n.Builder(this)
                .setTitle(I18n.t("app.modifica_parametri_java"))
                .setMessage(I18n.t("app.parametri_ram_flag_jvm_vengono"))
                .setPositiveButton(I18n.t("app.continua"), new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        executeSaveJvmTuning();
                    }
                })
                .setNegativeButton(I18n.t("app.annulla"), null)
                .show();
            return;
        }
        executeSaveJvmTuning();
    }

    private void executeSaveJvmTuning() {
        if (mSelectedJvmRam <= 0) {
            I18n.makeText(this, I18n.t("app.scegli_prima_quanta_ram_dare_server"), Toast.LENGTH_SHORT).show();
            return;
        }
        try {
            JSONObject body = new JSONObject();
            body.put("ram_gb", mSelectedJvmRam);
            body.put("use_aikar_flags", chkJvmAikar != null && chkJvmAikar.isChecked());

            executeApiCall("/api/server/jvm-tuning", "POST", body.toString(), new ApiClient.ApiCallback() {
                @Override
                public void onSuccess(String response) {
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            vibrate(30);
                            I18n.makeText(MainActivity.this, I18n.t("app.parametri_jvm_salvati_successo"), Toast.LENGTH_SHORT).show();
                            fetchJvmTuning();
                        }
                    });
                }

                @Override
                public void onError(final String error) {
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            I18n.makeText(MainActivity.this, I18n.t("app.errore_salvataggio_parametri_jvm", error), Toast.LENGTH_LONG).show();
                        }
                    });
                }
            });
        } catch (Exception e) {
            I18n.makeText(this, I18n.t("app.errore_preparazione_dati_jvm", e.getMessage()), Toast.LENGTH_SHORT).show();
        }
    }

    // ================= ECO MODE & ANTI-LAG WATCHDOG =================

    private void setEcoMinutesUI(int minutes) {
        mSelectedEcoMinutes = minutes;
        if (chipEco30 != null) {
            chipEco30.setBackgroundResource(minutes == 30 ? R.drawable.btn_blue : R.drawable.btn_slate);
            chipEco30.setTextColor(minutes == 30 ? Color.WHITE : Color.parseColor("#c4c4c8"));
        }
        if (chipEco45 != null) {
            chipEco45.setBackgroundResource(minutes == 45 ? R.drawable.btn_blue : R.drawable.btn_slate);
            chipEco45.setTextColor(minutes == 45 ? Color.WHITE : Color.parseColor("#c4c4c8"));
        }
        if (chipEco60 != null) {
            chipEco60.setBackgroundResource(minutes == 60 ? R.drawable.btn_blue : R.drawable.btn_slate);
            chipEco60.setTextColor(minutes == 60 ? Color.WHITE : Color.parseColor("#c4c4c8"));
        }
    }

    private void fetchEcoModeAndAntiLag() {
        executeApiCall("/api/status", "GET", null, new ApiClient.ApiCallback() {
            @Override
            public void onSuccess(final String response) {
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        try {
                            JSONObject obj = new JSONObject(response);
                            JSONObject ecoObj = obj.optJSONObject("eco_mode");
                            if (ecoObj != null) {
                                boolean ecoOn = ecoObj.optBoolean("enabled", false);
                                int ecoMins = ecoObj.optInt("minutes", 45);
                                if (chkEcoMode != null) chkEcoMode.setChecked(ecoOn);
                                setEcoMinutesUI(ecoMins);
                                if (txtEcoModeStatusBadge != null) {
                                    txtEcoModeStatusBadge.setText(ecoOn ? I18n.t("app.attiva_m", ecoMins) : I18n.t("ui.disattiva"));
                                    txtEcoModeStatusBadge.setTextColor(Color.parseColor(ecoOn ? "#a5d36f" : "#9a9ca2"));
                                }
                            }

                            JSONObject lagObj = obj.optJSONObject("anti_lag");
                            if (lagObj != null) {
                                boolean lagOn = lagObj.optBoolean("enabled", true);
                                boolean autoClean = lagObj.optBoolean("auto_clean", true);
                                if (chkAntiLagEnabled != null) chkAntiLagEnabled.setChecked(lagOn);
                                if (chkAntiLagAutoClean != null) chkAntiLagAutoClean.setChecked(autoClean);
                                if (txtAntiLagStatusBadge != null) {
                                    txtAntiLagStatusBadge.setText(lagOn ? I18n.t("ui.attivo") : I18n.t("app.disattivo"));
                                    txtAntiLagStatusBadge.setTextColor(Color.parseColor(lagOn ? "#a5d36f" : "#9a9ca2"));
                                }
                            }
                        } catch (Exception ignored) {}
                    }
                });
            }
            @Override public void onError(String error) {}
        });
    }

    private void actionSaveEcoMode() {
        try {
            final boolean enabled = chkEcoMode != null && chkEcoMode.isChecked();
            JSONObject body = new JSONObject();
            body.put("enabled", enabled);
            body.put("minutes", mSelectedEcoMinutes);

            executeApiCall("/api/eco-mode", "POST", body.toString(), new ApiClient.ApiCallback() {
                @Override
                public void onSuccess(String response) {
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            vibrate(25);
                            I18n.makeText(MainActivity.this, I18n.t("app.impostazioni_eco_mode_salvate"), Toast.LENGTH_SHORT).show();
                            if (txtEcoModeStatusBadge != null) {
                                txtEcoModeStatusBadge.setText(enabled ? I18n.t("app.attiva_m", mSelectedEcoMinutes) : I18n.t("ui.disattiva"));
                                txtEcoModeStatusBadge.setTextColor(Color.parseColor(enabled ? "#a5d36f" : "#9a9ca2"));
                            }
                        }
                    });
                }

                @Override
                public void onError(final String error) {
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            I18n.makeText(MainActivity.this, I18n.t("app.errore_salvataggio_eco_mode", error), Toast.LENGTH_LONG).show();
                        }
                    });
                }
            });
        } catch (Exception e) {
            I18n.makeText(this, I18n.t("app.errore_2", e.getMessage()), Toast.LENGTH_SHORT).show();
        }
    }

    private void actionSaveAntiLag() {
        try {
            final boolean enabled = chkAntiLagEnabled != null && chkAntiLagEnabled.isChecked();
            final boolean autoClean = chkAntiLagAutoClean != null && chkAntiLagAutoClean.isChecked();
            JSONObject body = new JSONObject();
            body.put("enabled", enabled);
            body.put("auto_clean", autoClean);

            executeApiCall("/api/anti-lag", "POST", body.toString(), new ApiClient.ApiCallback() {
                @Override
                public void onSuccess(String response) {
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            vibrate(25);
                            I18n.makeText(MainActivity.this, I18n.t("app.impostazioni_anti_lag_salvate"), Toast.LENGTH_SHORT).show();
                            if (txtAntiLagStatusBadge != null) {
                                txtAntiLagStatusBadge.setText(enabled ? I18n.t("ui.attivo") : I18n.t("app.disattivo"));
                                txtAntiLagStatusBadge.setTextColor(Color.parseColor(enabled ? "#a5d36f" : "#9a9ca2"));
                            }
                        }
                    });
                }

                @Override
                public void onError(final String error) {
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            I18n.makeText(MainActivity.this, I18n.t("app.errore_salvataggio_anti_lag", error), Toast.LENGTH_LONG).show();
                        }
                    });
                }
            });
        } catch (Exception e) {
            I18n.makeText(this, I18n.t("app.errore_2", e.getMessage()), Toast.LENGTH_SHORT).show();
        }
    }

    // ================= REMOTE CONFIG FILES EDITOR =================

    private void openRemoteConfigDialog() {
        vibrate(20);
        I18n.makeText(this, I18n.t("app.caricamento_elenco_file_config"), Toast.LENGTH_SHORT).show();
        executeApiCall("/api/config-files", "GET", null, new ApiClient.ApiCallback() {
            @Override
            public void onSuccess(final String response) {
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        try {
                            JSONObject obj = new JSONObject(response);
                            JSONArray filesArr = obj.optJSONArray("files");
                            if (filesArr == null || filesArr.length() == 0) {
                                I18n.makeText(MainActivity.this, I18n.t("app.nessun_file_configurazione_trovato"), Toast.LENGTH_SHORT).show();
                                return;
                            }
                            final String[] filePaths = new String[filesArr.length()];
                            final String[] fileLabels = new String[filesArr.length()];
                            for (int i = 0; i < filesArr.length(); i++) {
                                JSONObject item = filesArr.optJSONObject(i);
                                if (item != null) {
                                    filePaths[i] = item.optString("rel_path", "");
                                    String cat = item.optString("category", "");
                                    String size = item.optString("size_str", "");
                                    fileLabels[i] = "[" + cat + "] " + filePaths[i] + " (" + size + ")";
                                } else {
                                    filePaths[i] = filesArr.getString(i);
                                    fileLabels[i] = filePaths[i];
                                }
                            }

                            new I18n.Builder(MainActivity.this)
                                .setTitle(I18n.t("app.seleziona_file_modificare"))
                                .setItems(fileLabels, new DialogInterface.OnClickListener() {
                                    @Override
                                    public void onClick(DialogInterface dialog, int which) {
                                        final String selectedPath = filePaths[which];
                                        loadAndEditConfigFile(selectedPath);
                                    }
                                })
                                .setNegativeButton(I18n.t("app.chiudi"), null)
                                .show();
                        } catch (Exception e) {
                            I18n.makeText(MainActivity.this, I18n.t("app.errore_lettura_elenco", e.getMessage()), Toast.LENGTH_SHORT).show();
                        }
                    }
                });
            }

            @Override
            public void onError(final String error) {
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        I18n.makeText(MainActivity.this, I18n.t("app.errore_recupero_file_config", error), Toast.LENGTH_LONG).show();
                    }
                });
            }
        });
    }

    private void loadAndEditConfigFile(final String filePath) {
        vibrate(20);
        I18n.makeText(this, I18n.t("app.apertura", filePath), Toast.LENGTH_SHORT).show();
        try {
            String encodedPath = java.net.URLEncoder.encode(filePath, "UTF-8");
            executeApiCall("/api/config-file?path=" + encodedPath, "GET", null, new ApiClient.ApiCallback() {
                @Override
                public void onSuccess(final String response) {
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            try {
                                JSONObject obj = new JSONObject(response);
                                final String content = obj.optString("content", "");

                                final EditText editor = new EditText(MainActivity.this);
                                editor.setText(content);
                                editor.setTypeface(android.graphics.Typeface.MONOSPACE);
                                editor.setTextSize(12);
                                editor.setTextColor(Color.parseColor("#f4f4f5"));
                                editor.setBackgroundColor(Color.parseColor("#0f0f11"));
                                editor.setPadding(24, 24, 24, 24);
                                editor.setInputType(android.text.InputType.TYPE_CLASS_TEXT |
                                                    android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE |
                                                    android.text.InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
                                editor.setGravity(android.view.Gravity.TOP | android.view.Gravity.START);

                                ScrollView scroll = new ScrollView(MainActivity.this);
                                scroll.setLayoutParams(new LinearLayout.LayoutParams(
                                    LinearLayout.LayoutParams.MATCH_PARENT, 800));
                                scroll.addView(editor);

                                new I18n.Builder(MainActivity.this)
                                    .setTitle("" + filePath)
                                    .setView(scroll)
                                    .setPositiveButton(I18n.t("app.salva"), new DialogInterface.OnClickListener() {
                                        @Override
                                        public void onClick(DialogInterface dialog, int which) {
                                            final String newContent = editor.getText().toString();
                                            confirmSaveConfigFile(filePath, newContent);
                                        }
                                    })
                                    .setNegativeButton(I18n.t("app.annulla"), null)
                                    .show();
                            } catch (Exception e) {
                                I18n.makeText(MainActivity.this, I18n.t("app.errore_caricamento_file", e.getMessage()), Toast.LENGTH_SHORT).show();
                            }
                        }
                    });
                }

                @Override
                public void onError(final String error) {
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            I18n.makeText(MainActivity.this, I18n.t("app.errore_lettura", filePath, error), Toast.LENGTH_LONG).show();
                        }
                    });
                }
            });
        } catch (Exception e) {
            I18n.makeText(this, I18n.t("app.errore_codifica", e.getMessage()), Toast.LENGTH_SHORT).show();
        }
    }

    private void confirmSaveConfigFile(final String filePath, final String newContent) {
        new I18n.Builder(this)
            .setTitle(I18n.t("app.conferma_salvataggio"))
            .setMessage(I18n.t("app.vuoi_salvare_modifiche_alcune", filePath))
            .setPositiveButton(I18n.t("app.si_salva_ora"), new DialogInterface.OnClickListener() {
                @Override
                public void onClick(DialogInterface dialog, int which) {
                    try {
                        JSONObject body = new JSONObject();
                        body.put("path", filePath);
                        body.put("rel_path", filePath);
                        body.put("content", newContent);

                        executeApiCall("/api/config-file", "POST", body.toString(), new ApiClient.ApiCallback() {
                            @Override
                            public void onSuccess(String response) {
                                runOnUiThread(new Runnable() {
                                    @Override
                                    public void run() {
                                        vibrate(30);
                                        I18n.makeText(MainActivity.this, I18n.t("app.salvato_successo", filePath), Toast.LENGTH_SHORT).show();
                                    }
                                });
                            }

                            @Override
                            public void onError(final String error) {
                                runOnUiThread(new Runnable() {
                                    @Override
                                    public void run() {
                                        I18n.makeText(MainActivity.this, I18n.t("app.errore_salvataggio_2", filePath, error), Toast.LENGTH_LONG).show();
                                    }
                                });
                            }
                        });
                    } catch (Exception e) {
                        I18n.makeText(MainActivity.this, I18n.t("app.errore_preparazione_dati", e.getMessage()), Toast.LENGTH_SHORT).show();
                    }
                }
            })
            .setNegativeButton(I18n.t("app.annulla"), null)
            .show();
    }



    // ================= CHANGELOG & INFORMAZIONI =================


    /** Elenco delle lingue, ognuna scritta nella propria lingua; la schermata si traduce sul posto, senza sbloccare di nuovo. */
    private void showLanguagePicker() {
        final int current = Math.max(0, I18n.indexOf(I18n.getLanguage()));
        new AlertDialog.Builder(this)
            .setTitle(I18n.t("ui.lingua_dell_app_2"))
            .setSingleChoiceItems(I18n.NAMES, current, new DialogInterface.OnClickListener() {
                @Override
                public void onClick(DialogInterface dialog, int which) {
                    dialog.dismiss();
                    if (which == current) return;
                    I18n.setLanguage(MainActivity.this, I18n.CODES[which]);
                    Button btnAppLanguage = findViewById(R.id.btnAppLanguage);
                    if (btnAppLanguage != null) btnAppLanguage.setText(I18n.getLanguage().toUpperCase(java.util.Locale.ROOT));
                    // Nomi dei tipi di notifica (Impostazioni di Android) nella nuova lingua
                    ServerMonitorService.ensureChannels(MainActivity.this);
                    // Widget: si ridisegna subito con i pulsanti nella nuova lingua
                    android.appwidget.AppWidgetManager widgets = android.appwidget.AppWidgetManager.getInstance(MainActivity.this);
                    for (int id : widgets.getAppWidgetIds(new android.content.ComponentName(MainActivity.this, ServerWidgetProvider.class))) {
                        ServerWidgetProvider.updateAppWidget(MainActivity.this, widgets, id);
                    }
                }
            })
            .setNegativeButton(I18n.t("app.annulla"), null)
            .show();
    }

    private void showInfoVersionsPopup() {
        try {
            View dialogView = LayoutInflater.from(this).inflate(R.layout.dialog_info_versions, null);
            final AlertDialog dialog = new I18n.Builder(this)
                .setView(dialogView)
                .setCancelable(true)
                .create();

            if (dialog.getWindow() != null) {
                dialog.getWindow().setBackgroundDrawable(new ColorDrawable(Color.TRANSPARENT));
            }

            TextView txtVer = dialogView.findViewById(R.id.dialogTxtCurrentVersion);
            if (txtVer != null) {
                txtVer.setText(I18n.t("app.release_ufficiale_v_build", CURRENT_VERSION_NAME, CURRENT_VERSION_CODE));
            }

            Button btnCloseTop = dialogView.findViewById(R.id.dialogBtnCloseTop);
            if (btnCloseTop != null) {
                btnCloseTop.setOnClickListener(new View.OnClickListener() {
                    @Override
                    public void onClick(View v) {
                        dialog.dismiss();
                    }
                });
            }

            Button btnCloseBottom = dialogView.findViewById(R.id.dialogBtnCloseBottom);
            if (btnCloseBottom != null) {
                btnCloseBottom.setOnClickListener(new View.OnClickListener() {
                    @Override
                    public void onClick(View v) {
                        dialog.dismiss();
                    }
                });
            }

            dialog.show();
        } catch (Exception e) {
            new I18n.Builder(this)
                .setTitle(I18n.t("app.informazioni_versioni_telefono"))
                .setMessage(I18n.t("app.release_ufficiale_v_build", CURRENT_VERSION_NAME, CURRENT_VERSION_CODE))
                .setPositiveButton(I18n.t("app.chiudi"), null)
                .show();
        }
    }

    // ================= ACTIONS: START, STOP, RESTART =================

    private void actionStartServer() {
        btnActionStart.setEnabled(false);
        btnActionStart.setAlpha(0.4f);
        pillServerStatus.setText(I18n.t("app.avvio"));
        pillServerStatus.setBackgroundResource(R.drawable.pill_pending);
        pillServerStatus.setTextColor(Color.parseColor("#e3b35a"));
        pillServerStatus2.setText(I18n.t("app.avvio"));
        pillServerStatus2.setBackgroundResource(R.drawable.pill_pending);
        pillServerStatus2.setTextColor(Color.parseColor("#e3b35a"));
        txtMainStatus.setText(I18n.t("app.avvio_server_corso"));
        txtMainStatus.setTextColor(Color.parseColor("#e3b35a"));
        I18n.makeText(MainActivity.this, I18n.t("app.avvio_server_minecraft_corso"), Toast.LENGTH_SHORT).show();

        executeApiCall("/api/server/start", "POST", "{}", new ApiClient.ApiCallback() {
            @Override
            public void onSuccess(String response) {
                I18n.makeText(MainActivity.this, I18n.t("app.comando_avvio_accettato"), Toast.LENGTH_SHORT).show();
                fetchStatus();
            }
            @Override
            public void onError(String error) {
                btnActionStart.setEnabled(true);
                btnActionStart.setAlpha(1.0f);
                I18n.makeText(MainActivity.this, I18n.t("app.errore_avvio_2", error), Toast.LENGTH_LONG).show();
                fetchStatus();
            }
        });
    }

    private void confirmRestartServer() {
        new I18n.Builder(this)
            .setTitle(I18n.t("app.conferma_riavvio"))
            .setMessage(I18n.t("app.vuoi_riavviare_server_minecraft_adesso"))
            .setPositiveButton(I18n.t("ui.riavvia"), new DialogInterface.OnClickListener() {
                @Override
                public void onClick(DialogInterface dialog, int which) {
                    executeApiCall("/api/server/restart", "POST", "{\"countdown\":5,\"reason\":\"Riavvio richiesto da App Android\"}", new ApiClient.ApiCallback() {
                        @Override
                        public void onSuccess(String response) {
                            runOnUiThread(new Runnable() {
                                @Override
                                public void run() {
                                    I18n.makeText(MainActivity.this, I18n.t("app.riavvio_corso"), Toast.LENGTH_SHORT).show();
                                    fetchStatus();
                                }
                            });
                        }
                        @Override
                        public void onError(final String error) {
                            runOnUiThread(new Runnable() {
                                @Override
                                public void run() {
                                    I18n.makeText(MainActivity.this, I18n.t("app.errore_riavvio", error), Toast.LENGTH_LONG).show();
                                }
                            });
                        }
                    });
                }
            })
            .setNegativeButton(I18n.t("app.annulla"), null)
            .show();
    }

    private void confirmStopServer() {
        confirmStopServer(false);
    }

    private void confirmStopServer(final boolean backupAfter) {
        new I18n.Builder(this)
            .setTitle(I18n.t("app.conferma_arresto"))
            .setMessage(I18n.t("app.vuoi_arrestare_server_minecraft"))
            .setPositiveButton(I18n.t("ui.arresta"), new DialogInterface.OnClickListener() {
                @Override
                public void onClick(DialogInterface dialog, int which) {
                    executeApiCall("/api/server/stop", "POST", "{\"force\":false,\"reason\":\"Arresto da App Android\"}", new ApiClient.ApiCallback() {
                        @Override
                        public void onSuccess(String response) {
                            mBackupAfterStop = backupAfter;
                            I18n.makeText(MainActivity.this, I18n.t("app.arresto_server_inviato"), Toast.LENGTH_SHORT).show();
                            fetchStatus();
                        }
                        @Override
                        public void onError(String error) {
                            I18n.makeText(MainActivity.this, I18n.t("app.errore_arresto", error), Toast.LENGTH_LONG).show();
                        }
                    });
                }
            })
            .setNegativeButton(I18n.t("app.annulla"), null)
            .show();
    }

    // ================= POLLING LOOP =================

    private void startPolling() {
        mPollHandler.removeCallbacks(mPollRunnable);
        mPollHandler.post(mPollRunnable);
    }

    private int mPollTick = 0;

    private static boolean isTransitionState(String state) {
        return "STARTING".equalsIgnoreCase(state) || "STOPPING".equalsIgnoreCase(state) || "RESTARTING".equalsIgnoreCase(state);
    }

    private final Runnable mPollRunnable = new Runnable() {
        @Override
        public void run() {
            if (mIsAuthenticated) {
                if (viewDashboard.getVisibility() == View.VISIBLE || viewAutoRestart.getVisibility() == View.VISIBLE) {
                    fetchStatus();
                } else if (viewPlayers.getVisibility() == View.VISIBLE) {
                    fetchPlayersAndWhitelist();
                } else if (viewConsole.getVisibility() == View.VISIBLE) {
                    fetchLogs();
                } else if (viewErrors.getVisibility() == View.VISIBLE) {
                    fetchErrors();
                } else if (viewChat.getVisibility() == View.VISIBLE) {
                    fetchChat();
                } else if (viewSettings.getVisibility() == View.VISIBLE) {
                    fetchBackupStatus();
                }
                // Fuori dalla Home lo stato del server si rileggeva mai: l'intestazione restava su "Arresto..." e il
                // backup credeva il server ancora acceso. Ogni 3 giri (7,5 s), o a ogni giro dopo un comando.
                boolean otherView = viewDashboard.getVisibility() != View.VISIBLE && viewAutoRestart.getVisibility() != View.VISIBLE;
                if (otherView && (++mPollTick % 3 == 0 || mBackupAfterStop || isTransitionState(mServerState))) {
                    fetchStatus();
                }
                recheckHomeRouteIfNeeded();
            }
            mPollHandler.postDelayed(this, 2500);
        }
    };

    @Override
    protected void onPause() {
        super.onPause();
        mPollHandler.removeCallbacks(mPollRunnable);
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        mPollHandler.removeCallbacks(mPollRunnable);
        if (mUrlUpdateReceiver != null) {
            try { unregisterReceiver(mUrlUpdateReceiver); } catch (Exception ignored) {}
        }
        if (mNetworkCallback != null) {
            try {
                ConnectivityManager cm = (ConnectivityManager) getSystemService(Context.CONNECTIVITY_SERVICE);
                if (cm != null) cm.unregisterNetworkCallback(mNetworkCallback);
            } catch (Exception ignored) {}
        }
    }

    // ================= AUTHENTICATION & SECURITY =================

    private void requestBiometricOrCredentialAuth() {
        if (mIsAuthenticating) return;
        mIsAuthenticating = true;

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            BiometricPrompt.Builder builder = new BiometricPrompt.Builder(this)
                .setTitle("Voxarium")
                .setSubtitle(I18n.t("app.sblocca_accedere_server"));

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                builder.setAllowedAuthenticators(android.hardware.biometrics.BiometricManager.Authenticators.BIOMETRIC_STRONG
                    | android.hardware.biometrics.BiometricManager.Authenticators.DEVICE_CREDENTIAL);
            } else {
                builder.setNegativeButton(I18n.t("app.usa_pin_app"), getMainExecutor(), new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        mIsAuthenticating = false;
                        showPinFallbackDialog();
                    }
                });
            }

            mCancellationSignal = new CancellationSignal();
            builder.build().authenticate(mCancellationSignal, getMainExecutor(), new BiometricPrompt.AuthenticationCallback() {
                @Override
                public void onAuthenticationSucceeded(BiometricPrompt.AuthenticationResult result) {
                    super.onAuthenticationSucceeded(result);
                    mIsAuthenticating = false;
                    onUnlockSuccess();
                }

                @Override
                public void onAuthenticationError(int errorCode, CharSequence errString) {
                    super.onAuthenticationError(errorCode, errString);
                    mIsAuthenticating = false;
                    showPinFallbackDialog();
                }

                @Override
                public void onAuthenticationFailed() {
                    super.onAuthenticationFailed();
                }
            });
        } else {
            mIsAuthenticating = false;
            showPinFallbackDialog();
        }
    }

    /** Campo di una finestra con i margini del titolo (senza, il campo toccava i bordi della finestra). */
    private View withDialogMargins(View field) {
        android.widget.FrameLayout box = new android.widget.FrameLayout(this);
        int side = (int) (22 * getResources().getDisplayMetrics().density);
        box.setPadding(side, side / 3, side, 0);
        box.addView(field);
        return box;
    }

    private void showPinFallbackDialog() {
        final EditText pinInput = new EditText(this);
        pinInput.setInputType(android.text.InputType.TYPE_CLASS_NUMBER | android.text.InputType.TYPE_NUMBER_VARIATION_PASSWORD);
        pinInput.setHint(I18n.t("app.inserisci_pin_pc"));
        // Un campo password usa il carattere a larghezza fissa: quello normale dell'app, come nel resto delle finestre
        pinInput.setTypeface(android.graphics.Typeface.DEFAULT);

        new I18n.Builder(this)
            .setTitle(I18n.t("app.autenticazione_pin"))
            .setView(withDialogMargins(pinInput))
            .setCancelable(false)
            .setPositiveButton(I18n.t("ui.sblocca"), new DialogInterface.OnClickListener() {
                @Override
                public void onClick(DialogInterface dialog, int which) {
                    verifyPinWithPc(pinInput.getText().toString().trim());
                }
            })
            .show();
    }

    // Il PIN scritto nella finestra di sblocco si controlla con il PC: se è giusto diventa il PIN salvato nell'app.
    // Solo se il PC non risponde (spento, fuori rete) vale il PIN salvato, per aprire l'app anche senza PC.
    private void verifyPinWithPc(final String entered) {
        String base = getEffectiveBaseUrl();
        if (entered.isEmpty() || base == null || base.isEmpty()) {
            unlockWithSavedPin(entered);
            return;
        }
        if (System.currentTimeMillis() < mAuthLockedUntil) {
            I18n.makeText(this, authLockRemainingText(), Toast.LENGTH_LONG).show();
            showPinFallbackDialog();
            return;
        }
        // Il PIN passa in chiaro sull'indirizzo di casa: prima si controlla che lì risponda il nostro PC. Prima questa
        // finestra lo mandava a chiunque rispondesse a quell'indirizzo (es. su un Wi-Fi esterno in "Solo Wi-Fi")
        Runnable savedPinOnly = new Runnable() {
            @Override
            public void run() {
                unlockWithSavedPin(entered);
            }
        };
        withVerifiedBase(new Runnable() {
            @Override
            public void run() {
                postUnlockPin(entered);
            }
        }, savedPinOnly, savedPinOnly);
    }

    private void postUnlockPin(final String entered) {
        String base = getEffectiveBaseUrl();
        String body = "{\"pin\":\"" + entered.replace("\\", "\\\\").replace("\"", "\\\"") + "\"}";
        ApiClient.post(base + "/api/auth/verify", body, null, 4000, new ApiClient.ApiCallback() {
            @Override
            public void onSuccess(String response) {
                mAdminPin = entered;
                mAuthToken = "";
                mPushConfigSynced = false;
                getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit()
                    .remove(PREF_ADMIN_PIN_REJECTED).remove(PREF_AUTH_TOKEN).apply();
                if (edtAdminPin != null) edtAdminPin.setText(entered);
                savePreferences();
                onUnlockSuccess();
            }

            @Override
            public void onError(String error) {
                if (error != null && error.contains("HTTP 429")) {
                    vibrate(200);
                    I18n.makeText(MainActivity.this, authLockMessage(error), Toast.LENGTH_LONG).show();
                    showPinFallbackDialog();
                } else if (error != null && error.contains("HTTP 403") && !error.contains("PIN predefinito")) {
                    vibrate(200);
                    I18n.makeText(MainActivity.this, I18n.t("app.pin_errato"), Toast.LENGTH_SHORT).show();
                    showPinFallbackDialog();
                } else {
                    unlockWithSavedPin(entered);   // PC non raggiungibile
                }
            }
        });
    }

    private void unlockWithSavedPin(String entered) {
        if (entered.equals(mAdminPin)) {
            onUnlockSuccess();
        } else {
            vibrate(200);
            I18n.makeText(this, I18n.t("app.pin_errato_pc_non_raggiungibile_vale"), Toast.LENGTH_LONG).show();
            showPinFallbackDialog();
        }
    }

    private void onUnlockSuccess() {
        mIsAuthenticated = true;
        authLayout.setVisibility(View.GONE);
        ensureAuthToken(null);
        startPolling();
        checkForUpdates(true);
        handleIncomingIntent(getIntent());
    }

    /** label: nome già tradotto della connessione; remote: indirizzo da internet (HTTPS) invece della rete di casa. */
    private void testUrl(final String targetUrl, final String label, final boolean remote) {
        vibrate(20);
        String urlToTest = targetUrl != null ? targetUrl.trim() : "";
        while (urlToTest.endsWith("/")) urlToTest = urlToTest.substring(0, urlToTest.length() - 1);
        if (urlToTest.isEmpty()) {
            I18n.makeText(this, I18n.t("app.indirizzo_non_inserito", label), Toast.LENGTH_SHORT).show();
            return;
        }
        if (!urlToTest.startsWith("http://") && !urlToTest.startsWith("https://")) {
            urlToTest = remote ? ("https://" + urlToTest) : ("http://" + urlToTest);
        }
        final String finalTestUrl = urlToTest;
        I18n.makeText(this, I18n.t("app.test_corso", label), Toast.LENGTH_SHORT).show();
        // /api/status risponde anche senza token: in chiaro (http) non lo si manda, all'indirizzo provato può esserci chiunque
        ApiClient.call(finalTestUrl + "/api/status", "GET", null, finalTestUrl.startsWith("https://") ? mAuthToken : "", new ApiClient.ApiCallback() {
            @Override
            public void onSuccess(final String response) {
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        I18n.makeText(MainActivity.this, I18n.t("app.raggiungibile", label), Toast.LENGTH_SHORT).show();
                    }
                });
            }
            @Override
            public void onError(final String error) {
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        String hint = "";
                        if (!remote) {
                            hint = (" " + I18n.t("app.nota_wi_fi_aziendali_universitari"));
                        }
                        I18n.makeText(MainActivity.this, "" + label + ": " + error + hint, Toast.LENGTH_LONG).show();
                    }
                });
            }
        });
    }

    private void actionSaveSettings() {
        vibrate(30);
        String typedLocal = edtLocalUrl.getText().toString().trim();
        String typedRemote = edtRemoteUrl.getText().toString().trim();
        if (typedLocal.isEmpty() && typedRemote.isEmpty()) {
            I18n.makeText(this, I18n.t("app.inserisci_almeno_indirizzo_pc_casa_o"), Toast.LENGTH_LONG).show();
            return;
        }
        final String oldLocal = mLocalUrl != null ? mLocalUrl : "";
        mLocalUrl = typedLocal;
        mRemoteUrl = typedRemote;
        Matcher mIp = PcTls.REMOTE_URL_PATTERN.matcher(mRemoteUrl);
        if (mIp.find()) mRemoteUrl = mIp.group();
        while (mLocalUrl.endsWith("/")) mLocalUrl = mLocalUrl.substring(0, mLocalUrl.length() - 1);
        while (mRemoteUrl.endsWith("/")) mRemoteUrl = mRemoteUrl.substring(0, mRemoteUrl.length() - 1);
        if (!mLocalUrl.isEmpty() && !mLocalUrl.startsWith("http://") && !mLocalUrl.startsWith("https://")) {
            mLocalUrl = "http://" + mLocalUrl;
        }
        if (!mRemoteUrl.isEmpty() && !mRemoteUrl.startsWith("http://") && !mRemoteUrl.startsWith("https://")) {
            mRemoteUrl = "https://" + mRemoteUrl;
        }
        edtLocalUrl.setText(mLocalUrl);
        edtRemoteUrl.setText(mRemoteUrl);
        if (!mLocalUrl.equalsIgnoreCase(oldLocal)) {
            // Indirizzo di casa cambiato a mano (magari un altro PC): si riconosce di nuovo al prossimo accesso col PIN
            getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit().remove(PREF_PC_HOST_ID)
                .remove(ApiClient.PREF_HOST_PROOF_SEEN).apply();
            mLocalVerifiedUrl = "";
        }
        if (edtAdminPin != null) {
            String newPin = edtAdminPin.getText().toString().trim();
            if (!newPin.isEmpty() && !newPin.equals(mAdminPin)) {
                mAdminPin = newPin;
                mAuthToken = "";
                mAuthLockedUntil = 0;
                getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit()
                    .remove(PREF_AUTH_TOKEN).remove(PREF_ADMIN_PIN_REJECTED).apply();
                ensureAuthToken(null);
            }
        }
        savePreferences();
        updateRoutePillUI();
        I18n.makeText(this, I18n.t("app.impostazioni_salvate_successo"), Toast.LENGTH_SHORT).show();
        fetchStatus();
    }

    private void saveWizardSettingsAndConnect() {
        mLocalUrl = wizardEdtLocalUrl.getText().toString().trim();
        mRemoteUrl = wizardEdtRemoteUrl.getText().toString().trim();
        mAdminPin = wizardEdtPin.getText().toString().trim();
        while (mLocalUrl.endsWith("/")) mLocalUrl = mLocalUrl.substring(0, mLocalUrl.length() - 1);
        while (mRemoteUrl.endsWith("/")) mRemoteUrl = mRemoteUrl.substring(0, mRemoteUrl.length() - 1);
        if (!mLocalUrl.isEmpty() && !mLocalUrl.startsWith("http://") && !mLocalUrl.startsWith("https://")) {
            mLocalUrl = "http://" + mLocalUrl;
        }
        if (!mRemoteUrl.isEmpty() && !mRemoteUrl.startsWith("http://") && !mRemoteUrl.startsWith("https://")) {
            mRemoteUrl = "https://" + mRemoteUrl;
        }
        if (mLocalUrl.isEmpty() && mRemoteUrl.isEmpty()) {
            I18n.makeText(this, I18n.t("app.inserisci_indirizzo_tuo_pc_trovi_nel"), Toast.LENGTH_LONG).show();
            return;
        }
        if (mAdminPin.isEmpty()) {
            I18n.makeText(this, I18n.t("app.inserisci_pin_impostato_sul_pc"), Toast.LENGTH_LONG).show();
            return;
        }
        if (mLocalUrl.isEmpty()) mIsUsingRemote = true;

        SharedPreferences prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        prefs.edit()
             .putString(PREF_LOCAL_URL, mLocalUrl)
             .putString(PREF_REMOTE_URL, mRemoteUrl)
             .putString(PREF_ADMIN_PIN, mAdminPin)
             .putBoolean(PREF_FIRST_RUN, true)
             .putBoolean(PREF_BIOMETRIC, true)
             .remove(PREF_PC_HOST_ID)   // configurazione nuova: il PC si riconosce al primo accesso col PIN
             .remove(ApiClient.PREF_HOST_PROOF_SEEN)
             .apply();
        mLocalVerifiedUrl = "";

        // Anche la schermata Connessione deve mostrare i valori appena salvati: con i campi vuoti,
        // "Salva impostazioni connessione" cancellava gli indirizzi
        if (edtLocalUrl != null) edtLocalUrl.setText(mLocalUrl);
        if (edtRemoteUrl != null) edtRemoteUrl.setText(mRemoteUrl);
        if (edtAdminPin != null) edtAdminPin.setText(mAdminPin);

        wizardLayout.setVisibility(View.GONE);
        mIsAuthenticated = true;
        mAuthToken = "";
        prefs.edit().remove(PREF_AUTH_TOKEN).apply();
        ensureAuthToken(null);
        startPolling();
        checkForUpdates(false);
    }

    // ================= OTA AUTO-UPDATER =================

    private void checkForUpdates(final boolean silent) {
        if (!Flavor.SELF_UPDATE) return;
        if (!silent) {
            I18n.makeText(this, I18n.t("app.verifica_aggiornamenti_corso"), Toast.LENGTH_SHORT).show();
        }
        executeApiCall("/static/version.json", "GET", null, new ApiClient.ApiCallback() {
            @Override
            public void onSuccess(String response) {
                parseAndPromptUpdate(response, silent);
            }
            @Override
            public void onError(String error) {
                executeApiCall("/api/app/version", "GET", null, new ApiClient.ApiCallback() {
                    @Override
                    public void onSuccess(String response) {
                        parseAndPromptUpdate(response, silent);
                    }
                    @Override
                    public void onError(String err) {
                        if (!silent) {
                            I18n.makeText(MainActivity.this, I18n.t("app.impossibile_verificare_aggiornamenti", err), Toast.LENGTH_SHORT).show();
                        }
                    }
                });
            }
        });
    }

    private void parseAndPromptUpdate(String response, boolean silent) {
        try {
            JSONObject obj = new JSONObject(response);
            int serverCode = obj.optInt("version_code", 0);
            String versionName = obj.optString("version_name", "nuova");
            String notes = obj.optString("release_notes", I18n.t("app.miglioramenti_generali"));
            String downloadUrl = obj.optString("download_url", "/static/Controllo_Server_Minecraft.apk");

            if (serverCode > CURRENT_VERSION_CODE) {
                SharedPreferences prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
                int lastNotified = prefs.getInt("last_notified_update_code", -1);
                if (lastNotified != serverCode) {
                    prefs.edit().putInt("last_notified_update_code", serverCode).apply();
                    showUpdateAvailableNotification(versionName, notes);
                }
                showUpdateAvailableDialog(versionName, notes, downloadUrl);
            } else if (!silent) {
                I18n.makeText(MainActivity.this, I18n.t("app.app_gia_aggiornata_all_ultima_versione", CURRENT_VERSION_NAME), Toast.LENGTH_SHORT).show();
            }
        } catch (Exception ignored) {}
    }

    private void showUpdateAvailableNotification(String versionName, String releaseNotes) {
        try {
            NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
            if (nm == null) return;

            Intent intent = new Intent(this, MainActivity.class);
            intent.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
            intent.putExtra("check_update", true);

            int flags = PendingIntent.FLAG_UPDATE_CURRENT;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                flags |= PendingIntent.FLAG_IMMUTABLE;
            }
            PendingIntent pi = PendingIntent.getActivity(this, 1001, intent, flags);

            Notification.Builder builder;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                builder = new Notification.Builder(this, CHANNEL_UPDATES);
            } else {
                builder = new Notification.Builder(this);
            }

            final String vClean = (versionName != null && versionName.startsWith("v")) ? versionName : ("v" + versionName);
            builder.setSmallIcon(R.drawable.ic_stat_voxarium)
                   .setContentTitle(I18n.t("app.aggiornamento_disponibile", vClean))
                   .setContentText(I18n.t("app.nuova_versione_pronta_installazione"))
                   .setStyle(new Notification.BigTextStyle().bigText(I18n.t("app.disponibile_versione_compilata_sul_pc", vClean, releaseNotes)))
                   .setAutoCancel(true)
                   .setContentIntent(pi);

            nm.notify(1002, builder.build());
        } catch (Exception ignored) {}
    }

    private File mPendingInstallApk = null;

    @Override
    protected void onResume() {
        super.onResume();
        if (mIsAuthenticated) {
            startPolling();
        }
        if (mPendingInstallApk != null && mPendingInstallApk.exists()) {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O || getPackageManager().canRequestPackageInstalls()) {
                File toInstall = mPendingInstallApk;
                mPendingInstallApk = null;
                triggerApkInstallation(toInstall);
            }
        }
    }

    private void showUpdateAvailableDialog(final String versionName, final String notes, final String downloadEndpoint) {
        final String effectiveBase = getEffectiveBaseUrl();
        final String fullUrl = downloadEndpoint.startsWith("http") ? downloadEndpoint : (effectiveBase + downloadEndpoint);
        final String vClean = (versionName != null && versionName.startsWith("v")) ? versionName : ("v" + versionName);
        new I18n.Builder(this)
            .setTitle(I18n.t("app.aggiornamento_disponibile", vClean))
            .setMessage(I18n.t("app.disponibile_nuovo_aggiornamento", notes))
            .setPositiveButton(I18n.t("app.scarica_installa"), new DialogInterface.OnClickListener() {
                @Override
                public void onClick(DialogInterface dialog, int which) {
                    startOtaDownloadAndInstall(downloadEndpoint);
                }
            })
            .setNeutralButton(I18n.t("app.scarica_browser"), new DialogInterface.OnClickListener() {
                @Override
                public void onClick(DialogInterface dialog, int which) {
                    try {
                        Intent intent = new Intent(Intent.ACTION_VIEW, Uri.parse(fullUrl));
                        startActivity(intent);
                    } catch (Exception e) {
                        I18n.makeText(MainActivity.this, I18n.t("app.errore_apertura_browser", e.getMessage()), Toast.LENGTH_SHORT).show();
                    }
                }
            })
            .setNegativeButton(I18n.t("app.piu_tardi"), null)
            .show();
    }

    private void startOtaDownloadAndInstall(final String downloadEndpoint) {
        final AlertDialog progressDialog = new I18n.Builder(this)
            .setTitle(I18n.t("app.download_aggiornamento"))
            .setMessage(I18n.t("app.scaricamento_pacchetto_apk_corso_dal"))
            .setCancelable(false)
            .create();
        progressDialog.show();

        // Estrai percorso relativo pulito (es. /static/Controllo_Server_Minecraft.apk) per evitare conflitti con vecchi domini scaduti
        String detectedSubPath = "/static/Controllo_Server_Minecraft.apk";
        if (downloadEndpoint != null && !downloadEndpoint.trim().isEmpty()) {
            String ep = downloadEndpoint.trim();
            int idx = ep.indexOf("/static/");
            if (idx != -1) {
                detectedSubPath = ep.substring(idx);
            } else if (!ep.startsWith("http://") && !ep.startsWith("https://")) {
                detectedSubPath = ep.startsWith("/") ? ep : ("/" + ep);
            }
        }
        final String subPath = detectedSubPath;

        final String activeBase = getEffectiveBaseUrl();
        final String primaryUrl = activeBase + subPath;
        final String otherBase = activeBase.equalsIgnoreCase(mLocalUrl) ? mRemoteUrl : mLocalUrl;
        final String secondaryUrl = (otherBase != null && !otherBase.isEmpty()) ? (otherBase + subPath) : "";

        new Thread(new Runnable() {
            @Override
            public void run() {
                // Tentativo 1: Connessione attualmente attiva e funzionante
                File savedApk = tryDownloadApk(primaryUrl);

                // Tentativo 2: Connessione alternativa (Remote se in LAN, LAN se in Remote)
                if ((savedApk == null || !savedApk.exists() || savedApk.length() == 0) && !secondaryUrl.isEmpty() && !secondaryUrl.equalsIgnoreCase(primaryUrl)) {
                    savedApk = tryDownloadApk(secondaryUrl);
                }

                // Tentativo 3: Se downloadEndpoint era un URL assoluto valido e diverso
                if ((savedApk == null || !savedApk.exists() || savedApk.length() == 0) && downloadEndpoint != null && downloadEndpoint.startsWith("http")) {
                    if (!downloadEndpoint.equalsIgnoreCase(primaryUrl) && !downloadEndpoint.equalsIgnoreCase(secondaryUrl)) {
                        savedApk = tryDownloadApk(downloadEndpoint);
                    }
                }

                // Tentativo 4: Sincronizzazione automatica del nuovo indirizzo da internet da ntfy e download istantaneo
                if (savedApk == null || !savedApk.exists() || savedApk.length() == 0) {
                    syncUrlsFromNtfy(false, null);
                    try { Thread.sleep(1200); } catch (Exception ignored) {}
                    if (mRemoteUrl != null && !mRemoteUrl.isEmpty()) {
                        savedApk = tryDownloadApk(mRemoteUrl + subPath);
                    }
                }

                final File finalApk = savedApk;
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        progressDialog.dismiss();
                        if (finalApk != null && finalApk.exists() && finalApk.length() > 0) {
                            checkPermissionAndInstall(finalApk);
                        } else {
                            I18n.makeText(MainActivity.this, I18n.t("app.impossibile_scaricare_apk_sia_lan_che"), Toast.LENGTH_LONG).show();
                        }
                    }
                });
            }
        }).start();
    }

    private File tryDownloadApk(String urlString) {
        File cacheDir = getCacheDir();
        File apkFile = new File(cacheDir, "update.apk");
        File tempFile = new File(cacheDir, "update.apk.part");
        if (tempFile.exists()) tempFile.delete();

        java.net.HttpURLConnection conn = null;
        java.io.InputStream in = null;
        java.io.FileOutputStream out = null;

        try {
            java.net.URL url = new java.net.URL(urlString);
            conn = (java.net.HttpURLConnection) url.openConnection();
            conn.setRequestMethod("GET");
            conn.setConnectTimeout(10000);
            conn.setReadTimeout(30000);
            // Token solo in HTTPS (certificato del PC) o all'indirizzo di casa già riconosciuto: un download in chiaro
            // verso un indirizzo non controllato (es. su un Wi-Fi esterno) lo consegnava a chi rispondeva lì
            boolean trustedUrl = urlString.startsWith("https://")
                || (!mLocalVerifiedUrl.isEmpty() && urlString.startsWith(mLocalVerifiedUrl + "/"));
            if (!mAuthToken.isEmpty() && trustedUrl) {
                conn.setRequestProperty("X-Admin-Token", mAuthToken);
            }

            int responseCode = conn.getResponseCode();
            if (responseCode == 200) {
                long expectedLength = conn.getContentLengthLong();
                in = conn.getInputStream();
                out = new java.io.FileOutputStream(tempFile);
                byte[] buffer = new byte[8192];
                int read;
                long total = 0;
                while ((read = in.read(buffer)) != -1) {
                    out.write(buffer, 0, read);
                    total += read;
                }
                out.flush();
                out.close();
                out = null;
                in.close();
                in = null;

                if (expectedLength > 0 && total != expectedLength) {
                    if (tempFile.exists()) tempFile.delete();
                    return null;
                }
                if (total == 0) {
                    if (tempFile.exists()) tempFile.delete();
                    return null;
                }

                if (apkFile.exists()) apkFile.delete();
                if (!tempFile.renameTo(apkFile)) {
                    copyFileSimple(tempFile, apkFile);
                    tempFile.delete();
                }

                return apkFile;
            }
        } catch (Exception ignored) {
            if (tempFile != null && tempFile.exists()) tempFile.delete();
        } finally {
            try { if (out != null) out.close(); } catch (Exception ignored) {}
            try { if (in != null) in.close(); } catch (Exception ignored) {}
            if (conn != null) conn.disconnect();
        }
        return null;
    }

    /**
     * All'apertura dell'app l'aggiornamento scaricato non serve più (già installato o rifiutato): si cancella, con le
     * copie in cache/updates/ e nei file dell'app lasciate dalle versioni fino alla 4.12.4, che non venivano mai tolte.
     */
    private void deleteOldUpdateApks() {
        File[] old = {
            new File(getCacheDir(), "update.apk"),
            new File(getCacheDir(), "update.apk.part"),
            new File(new File(getCacheDir(), "updates"), "update.apk"),
            new File(getFilesDir(), "update.apk"),
            new File(new File(getFilesDir(), "updates"), "update.apk"),
        };
        for (File f : old) {
            try { if (f.exists()) f.delete(); } catch (Exception ignored) {}
        }
    }

    private void checkPermissionAndInstall(final File apkFile) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            if (!getPackageManager().canRequestPackageInstalls()) {
                mPendingInstallApk = apkFile;
                new I18n.Builder(this)
                    .setTitle(I18n.t("app.autorizzazione_installazione"))
                    .setMessage(I18n.t("app.pacchetto_aggiornamento_stato"))
                    .setPositiveButton(I18n.t("app.apri_impostazioni"), new DialogInterface.OnClickListener() {
                        @Override
                        public void onClick(DialogInterface dialog, int which) {
                            try {
                                Intent intent = new Intent(android.provider.Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES);
                                intent.setData(Uri.parse("package:" + getPackageName()));
                                startActivity(intent);
                            } catch (Exception e) {
                                startActivity(new Intent(android.provider.Settings.ACTION_SECURITY_SETTINGS));
                            }
                        }
                    })
                    .setNegativeButton(I18n.t("app.annulla"), null)
                    .show();
                return;
            }
        }
        triggerApkInstallation(apkFile);
    }

    private void copyFileSimple(File src, File dst) {
        try {
            if (dst.getParentFile() != null && !dst.getParentFile().exists()) {
                dst.getParentFile().mkdirs();
            }
            java.io.InputStream in = new java.io.FileInputStream(src);
            java.io.OutputStream out = new java.io.FileOutputStream(dst);
            byte[] buf = new byte[8192];
            int len;
            while ((len = in.read(buf)) > 0) {
                out.write(buf, 0, len);
            }
            in.close();
            out.close();
        } catch (Exception ignored) {}
    }

    private void triggerApkInstallation(File apkFile) {
        try {
            File rootCache = new File(getCacheDir(), "update.apk");
            if (!apkFile.getAbsolutePath().equals(rootCache.getAbsolutePath())) {
                copyFileSimple(apkFile, rootCache);
            }

            Uri rawUri = Uri.parse("content://com.minecraftserver.manager.updateprovider/update.apk");
            Intent intent = new Intent(Intent.ACTION_VIEW);
            intent.setDataAndType(rawUri, "application/vnd.android.package-archive");
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);

            java.util.List<android.content.pm.ResolveInfo> resolveInfoList = getPackageManager().queryIntentActivities(intent, android.content.pm.PackageManager.MATCH_DEFAULT_ONLY);
            if (resolveInfoList != null) {
                for (android.content.pm.ResolveInfo resolveInfo : resolveInfoList) {
                    if (resolveInfo.activityInfo != null && resolveInfo.activityInfo.packageName != null) {
                        grantUriPermission(resolveInfo.activityInfo.packageName, rawUri, Intent.FLAG_GRANT_READ_URI_PERMISSION);
                    }
                }
            }
            startActivity(intent);
        } catch (Exception e) {
            I18n.makeText(this, I18n.t("app.impossibile_aprire_installer_apk", e.getMessage()), Toast.LENGTH_LONG).show();
        }
    }

    private void requestIgnoreBatteryOptimizations() {
        try {
            android.os.PowerManager pm = (android.os.PowerManager) getSystemService(Context.POWER_SERVICE);
            if (pm != null && !pm.isIgnoringBatteryOptimizations(getPackageName())) {
                Intent i = new Intent(android.provider.Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS);
                i.setData(android.net.Uri.parse("package:" + getPackageName()));
                startActivity(i);
            }
        } catch (Exception ignored) {}
    }

    private void checkNotificationPermission() {
        if (Build.VERSION.SDK_INT >= 33) {
            if (checkSelfPermission("android.permission.POST_NOTIFICATIONS") != android.content.pm.PackageManager.PERMISSION_GRANTED) {
                requestPermissions(new String[]{"android.permission.POST_NOTIFICATIONS"}, 1001);
            }
        }
    }

    private void testPushNotification() {
        vibrate(30);
        checkNotificationPermission();
        I18n.makeText(this, I18n.t("app.invio_notifica_push_prova_corso"), Toast.LENGTH_SHORT).show();
        executeApiCall("/api/notifications/test", "POST", "{}", new ApiClient.ApiCallback() {
            @Override
            public void onSuccess(final String response) {
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        vibrate(40);
                        I18n.makeText(MainActivity.this, I18n.t("app.notifica_inviata_controlla_tendina"), Toast.LENGTH_LONG).show();
                    }
                });
            }
            @Override
            public void onError(final String error) {
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        I18n.makeText(MainActivity.this, I18n.t("app.errore_invio_notifica", error), Toast.LENGTH_LONG).show();
                    }
                });
            }
        });
    }

}