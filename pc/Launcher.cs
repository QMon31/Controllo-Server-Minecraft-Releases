using System;
using System.IO;
using System.Drawing;
using System.Net;
using System.Net.Security;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Diagnostics;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using System.Runtime.InteropServices;
using System.Collections.Generic;
using System.Text.RegularExpressions;
using Microsoft.Win32;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using System.Reflection;

// Nome mostrato da Windows (Proprietà > Dettagli, Gestione attività); versione e produttore: build_info.cs, generato da build_deploy.py
[assembly: AssemblyTitle("Voxarium")]
[assembly: AssemblyDescription("Pannello di controllo per il server Minecraft")]

namespace MinecraftControlCenter
{
    public class MainWindow : Form
    {
        private WebView2 webView;
        private string targetUrl;
        private bool allowRealClose = false;
        private bool firstCloseTipShown = false;
        private NotifyIcon trayRef;
        private Label lblLoading;
        private Button btnRetry;
        private string pendingError = null;
        private int hostNavRetries = 0;

        public MainWindow(string url, Icon appIcon, NotifyIcon tray)
        {
            this.targetUrl = url;
            this.trayRef = tray;
            this.Text = "Voxarium";
            this.Size = new Size(1300, 850);
            this.MinimumSize = new Size(950, 620);
            this.StartPosition = FormStartPosition.CenterScreen;
            this.BackColor = Color.FromArgb(15, 23, 42); // #0f172a slate
            if (appIcon != null) this.Icon = appIcon;

            lblLoading = new Label();
            lblLoading.Text = "Caricamento interfaccia di controllo in corso...";
            lblLoading.ForeColor = Color.FromArgb(148, 163, 184);
            lblLoading.Font = new Font("Segoe UI", 11.5F, FontStyle.Regular);
            lblLoading.Dock = DockStyle.Fill;
            lblLoading.TextAlign = ContentAlignment.MiddleCenter;
            this.Controls.Add(lblLoading);

            btnRetry = new Button();
            btnRetry.Text = "Riprova ad avviare il pannello";
            btnRetry.Font = new Font("Segoe UI", 10.5F, FontStyle.Bold);
            btnRetry.Height = 46;
            btnRetry.Dock = DockStyle.Bottom;
            btnRetry.Visible = false;
            btnRetry.Click += (s, e) => RetryBackend();
            this.Controls.Add(btnRetry);

            webView = new WebView2();
            webView.Dock = DockStyle.Fill;
            webView.Visible = false;
            this.Controls.Add(webView);

            this.Shown += async (s, e) => {
                await InitWebView();
            };

            this.FormClosing += (s, e) => {
                if (!allowRealClose && e.CloseReason == CloseReason.UserClosing)
                {
                    e.Cancel = true;
                    this.Hide();
                    if (!firstCloseTipShown && trayRef != null)
                    {
                        firstCloseTipShown = true;
                        trayRef.ShowBalloonTip(3500, "Voxarium",
                            "L'applicazione continua a funzionare in background.\nFai doppio clic sull'icona accanto all'orologio per riaprirla.", ToolTipIcon.Info);
                    }
                }
            };
        }

        public void UpdateLoadingText(string text)
        {
            if (lblLoading != null)
            {
                if (lblLoading.InvokeRequired)
                {
                    try { lblLoading.Invoke(new Action(() => { lblLoading.Text = text; lblLoading.Visible = true; })); } catch { }
                }
                else
                {
                    lblLoading.Text = text;
                    lblLoading.Visible = true;
                }
            }
        }

        public void ShowBackendError(string message)
        {
            pendingError = string.IsNullOrEmpty(message) ? "Errore sconosciuto." : message;
            if (this.IsHandleCreated) ApplyBackendError();
        }

        private void ApplyBackendError()
        {
            if (this.InvokeRequired)
            {
                try { this.Invoke(new Action(ApplyBackendError)); } catch { }
                return;
            }
            if (webView != null) webView.Visible = false;
            lblLoading.Text = "Impossibile avviare il pannello del server.\r\n\r\n" + pendingError +
                              "\r\n\r\nRegistro completo: %LOCALAPPDATA%\\Controllo Server Minecraft\\backend.log";
            lblLoading.Visible = true;
            btnRetry.Visible = true;
            lblLoading.BringToFront(); // l'etichetta (Dock Fill) occupa lo spazio sopra il pulsante
        }

        private void RetryBackend()
        {
            btnRetry.Enabled = false;
            lblLoading.Text = "Nuovo tentativo di avvio del pannello in corso...";
            Task.Factory.StartNew(() => Program.StartPythonBackend()).ContinueWith(t => {
                btnRetry.Enabled = true;
                if (t.Result)
                {
                    pendingError = null;
                    btnRetry.Visible = false;
                    hostNavRetries = 0;
                    NavigateTo(targetUrl);
                }
                else
                {
                    pendingError = Program.BackendError;
                    ApplyBackendError();
                }
            }, TaskScheduler.FromCurrentSynchronizationContext());
        }

        private bool IsAutoUrl(string url)
        {
            return string.IsNullOrEmpty(url) || 
                   url.Equals("auto", StringComparison.OrdinalIgnoreCase) || 
                   url.Equals("http://auto", StringComparison.OrdinalIgnoreCase) || 
                   url.Equals("https://auto", StringComparison.OrdinalIgnoreCase);
        }

        // True se l'indirizzo è quello del pannello aperto (es. https://IP pubblico:8443): resta nella finestra
        private bool IsPanelAddress(Uri u)
        {
            try
            {
                if (IsAutoUrl(targetUrl)) return false;
                Uri panel = new Uri(targetUrl);
                return u.Scheme == panel.Scheme && u.Port == panel.Port &&
                       u.Host.Equals(panel.Host, StringComparison.OrdinalIgnoreCase);
            }
            catch { return false; }
        }

        private async Task InitWebView()
        {
            try
            {
                string localApp = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                string profileDir = Path.Combine(localApp, @"Controllo Server Minecraft\WebView2Profile");
                try
                {
                    string cachePath = Path.Combine(profileDir, @"EBWebView\Default\Cache");
                    if (Directory.Exists(cachePath)) Directory.Delete(cachePath, true);
                    string codeCache = Path.Combine(profileDir, @"EBWebView\Default\Code Cache");
                    if (Directory.Exists(codeCache)) Directory.Delete(codeCache, true);
                }
                catch { }
                if (!Directory.Exists(profileDir)) Directory.CreateDirectory(profileDir);

                CoreWebView2EnvironmentOptions envOptions = new CoreWebView2EnvironmentOptions();
                if (!string.IsNullOrEmpty(Program.AppLanguage)) envOptions.Language = Program.AppLanguage;
                CoreWebView2Environment env = await CoreWebView2Environment.CreateAsync(null, profileDir, envOptions);
                await webView.EnsureCoreWebView2Async(env);

                webView.CoreWebView2.Settings.IsStatusBarEnabled = false;
                webView.CoreWebView2.Settings.AreDefaultContextMenusEnabled = true;
                webView.CoreWebView2.Settings.IsZoomControlEnabled = true;
                webView.CoreWebView2.Settings.AreDevToolsEnabled = true;

                this.KeyPreview = true;
                this.KeyDown += (s, e) => {
                    if (e.KeyCode == Keys.F5)
                    {
                        if (webView != null && webView.CoreWebView2 != null)
                        {
                            webView.CoreWebView2.Reload();
                            e.Handled = true;
                        }
                    }
                };

                // Apri tutti i link esterni con target="_blank" o window.open nel browser predefinito di Windows
                webView.CoreWebView2.NewWindowRequested += (s, e) => {
                    e.Handled = true;
                    try
                    {
                        if (!string.IsNullOrEmpty(e.Uri))
                        {
                            ProcessStartInfo psi = new ProcessStartInfo(e.Uri) { UseShellExecute = true };
                            Process.Start(psi);
                        }
                    }
                    catch { }
                };

                // Intercetta anche navigazioni dirette verso siti esterni (es. clic su link non-localhost)
                webView.CoreWebView2.NavigationStarting += (s, e) => {
                    try
                    {
                        if (!string.IsNullOrEmpty(e.Uri))
                        {
                            Uri u = new Uri(e.Uri);
                            if ((u.Scheme == Uri.UriSchemeHttp || u.Scheme == Uri.UriSchemeHttps) &&
                                !u.Host.Equals("localhost", StringComparison.OrdinalIgnoreCase) &&
                                !u.Host.Equals("127.0.0.1", StringComparison.OrdinalIgnoreCase) &&
                                !e.Uri.Contains(":8080") &&
                                !IsPanelAddress(u))
                            {
                                e.Cancel = true;
                                ProcessStartInfo psi = new ProcessStartInfo(e.Uri) { UseShellExecute = true };
                                Process.Start(psi);
                            }
                        }
                    }
                    catch { }
                };

                // Pannello remoto in HTTPS con certificato autofirmato: accettato solo se l'impronta è quella dell'Host
                webView.CoreWebView2.ServerCertificateErrorDetected += (s, e) => {
                    bool pinned = false;
                    try
                    {
                        string pem = e.ServerCertificate.ToPemEncoding();
                        string b64 = Regex.Replace(pem, "-----[^-]+-----|\\s", "");
                        pinned = Program.IsPinnedHostCertificate(new X509Certificate(Convert.FromBase64String(b64)));
                    }
                    catch { }
                    e.Action = pinned ? CoreWebView2ServerCertificateErrorAction.AlwaysAllow
                                      : CoreWebView2ServerCertificateErrorAction.Cancel;
                };

                webView.CoreWebView2.WebMessageReceived += OnWebMessageReceived;

                webView.NavigationCompleted += (s, e) => {
                    if (e.IsSuccess)
                    {
                        hostNavRetries = 0;
                        if (lblLoading != null) lblLoading.Visible = false;
                        btnRetry.Visible = false;
                        webView.Visible = true;
                    }
                    else if (Program.IsRemote())
                    {
                        UpdateLoadingText("Connessione non riuscita.\r\nRicerca automatica server host in corso (LAN & Cloud)...");
                        if (webView != null) webView.Visible = false;
                        Program.TriggerAutoReconnectAsync(false);
                    }
                    else
                    {
                        // Host locale: il pannello può essere ancora in avvio, riprova ogni 2 secondi per circa 60 secondi
                        webView.Visible = false;
                        if (pendingError == null && hostNavRetries < 30)
                        {
                            hostNavRetries++;
                            UpdateLoadingText("Avvio del pannello in corso... (tentativo " + hostNavRetries + "/30)");
                            System.Windows.Forms.Timer rt = new System.Windows.Forms.Timer();
                            rt.Interval = 2000;
                            rt.Tick += (s2, e2) => {
                                rt.Stop();
                                rt.Dispose();
                                try { webView.CoreWebView2.Navigate(targetUrl); } catch { }
                            };
                            rt.Start();
                        }
                        else
                        {
                            if (pendingError == null)
                                pendingError = "Il pannello non risponde su " + targetUrl + ".\r\n\r\n" + Program.GetBackendLogTail(12);
                            ApplyBackendError();
                        }
                    }
                };

                if (IsAutoUrl(targetUrl))
                {
                    UpdateLoadingText("Ricerca automatica del server Minecraft in corso...\r\nVerifica rete locale LAN e sincronizzazione Cloud.");
                    Program.TriggerAutoReconnectAsync(false);
                }
                else
                {
                    if (pendingError != null) ApplyBackendError();
                    else webView.Source = new Uri(targetUrl);
                }

                // Timer di sicurezza: se l'evento NavigationCompleted impiega più tempo del previsto e targetUrl è valido, rivela la webview
                System.Windows.Forms.Timer t = new System.Windows.Forms.Timer();
                t.Interval = 3500;
                t.Tick += (s, e) => {
                    t.Stop();
                    t.Dispose();
                    if (!IsAutoUrl(targetUrl) && pendingError == null && hostNavRetries == 0)
                    {
                        if (lblLoading != null) lblLoading.Visible = false;
                        webView.Visible = true;
                    }
                };
                t.Start();
            }
            catch (Exception ex)
            {
                if (lblLoading != null)
                {
                    lblLoading.Text = "Inizializzazione WebView2 non riuscita:\n" + ex.Message + "\n\nApertura nel browser di sistema in corso...";
                }
                try { if (!IsAutoUrl(targetUrl)) Process.Start(targetUrl); } catch { }
            }
        }

        public void NavigateTo(string newUrl)
        {
            this.targetUrl = newUrl;
            if (this.InvokeRequired)
            {
                try { this.Invoke(new Action(() => NavigateTo(newUrl))); } catch { }
                return;
            }
            if (webView != null && webView.CoreWebView2 != null && !IsAutoUrl(newUrl))
            {
                try
                {
                    webView.Source = new Uri(newUrl);
                }
                catch { }
            }
        }

        public void ShowAndActivate()
        {
            if (this.WindowState == FormWindowState.Minimized)
            {
                this.WindowState = FormWindowState.Normal;
            }
            this.Show();
            this.Activate();
            this.BringToFront();
        }

        public void AllowExitAndClose()
        {
            allowRealClose = true;
            this.Close();
        }

        private void OnWebMessageReceived(object sender, CoreWebView2WebMessageReceivedEventArgs e)
        {
            try
            {
                string raw = null;
                try { raw = e.TryGetWebMessageAsString(); } catch { }
                if (string.IsNullOrEmpty(raw))
                {
                    try { raw = e.WebMessageAsJson; } catch { }
                }
                if (string.IsNullOrEmpty(raw)) return;

                if (raw.Contains("\"action\":\"native_browse\"") || raw.StartsWith("native_browse"))
                {
                    string target = "server_dir";
                    Match mTarget = Regex.Match(raw, "\"target\"\\s*:\\s*\"([^\"]+)\"");
                    if (mTarget.Success) target = mTarget.Groups[1].Value;

                    string initialPath = "";
                    Match mPath = Regex.Match(raw, "\"initialPath\"\\s*:\\s*\"([^\"]+)\"");
                    if (mPath.Success)
                    {
                        initialPath = Regex.Unescape(mPath.Groups[1].Value);
                    }

                    HandleNativeBrowseRequest(target, initialPath);
                }
                else if (raw.Contains("\"action\":\"open_folder\""))
                {
                    string fType = "mods";
                    Match mType = Regex.Match(raw, "\"folder_type\"\\s*:\\s*\"([^\"]+)\"");
                    if (mType.Success) fType = mType.Groups[1].Value;

                    string explicitPath = "";
                    Match mExplicit = Regex.Match(raw, "\"path\"\\s*:\\s*\"([^\"]+)\"");
                    if (mExplicit.Success) explicitPath = Regex.Unescape(mExplicit.Groups[1].Value);

                    HandleOpenFolderRequest(fType, explicitPath);
                }
            }
            catch { }
        }

        private void HandleOpenFolderRequest(string folderType, string explicitPath)
        {
            try
            {
                string targetDir = explicitPath;
                if (string.IsNullOrEmpty(targetDir) || !Directory.Exists(targetDir))
                {
                    string sDir = "";
                    try
                    {
                        string cfgFile = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "config.json");
                        if (!File.Exists(cfgFile))
                        {
                            string localApp = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                            cfgFile = Path.Combine(localApp, @"Programs\Controllo Server Minecraft\config.json");
                        }
                        if (File.Exists(cfgFile))
                        {
                            string text = File.ReadAllText(cfgFile);
                            Match m = Regex.Match(text, "\"server_dir\"\\s*:\\s*\"([^\"]+)\"");
                            if (m.Success) sDir = Regex.Unescape(m.Groups[1].Value);
                        }
                    }
                    catch { }

                    if (string.IsNullOrEmpty(sDir) || !Directory.Exists(sDir))
                    {
                        sDir = Environment.GetFolderPath(Environment.SpecialFolder.Desktop);
                    }

                    if (folderType == "mods")
                    {
                        targetDir = Path.Combine(sDir, "mods");
                    }
                    else
                    {
                        targetDir = sDir;
                    }
                }

                if (!Directory.Exists(targetDir))
                {
                    Directory.CreateDirectory(targetDir);
                }

                ProcessStartInfo psi = new ProcessStartInfo("explorer.exe", "\"" + targetDir + "\"") { UseShellExecute = true };
                Process.Start(psi);
            }
            catch { }
        }

        private void HandleNativeBrowseRequest(string target, string initialPath)
        {
            if (this.InvokeRequired)
            {
                try { this.Invoke(new Action(() => HandleNativeBrowseRequest(target, initialPath))); } catch { }
                return;
            }

            try
            {
                this.Activate();
                this.BringToFront();

                using (OpenFileDialog ofd = new OpenFileDialog())
                {
                    if (target == "run_bat")
                    {
                        ofd.Title = "Seleziona File di Avvio Server (run.bat)";
                        ofd.Filter = "File di avvio (*.bat;*.jar)|*.bat;*.jar|File Batch (*.bat)|*.bat|File Jar (*.jar)|*.jar|Tutti i file (*.*)|*.*";
                    }
                    else
                    {
                        ofd.Title = "Seleziona Cartella Server Minecraft (entra nella cartella e seleziona run.bat o qualsiasi file)";
                        ofd.Filter = "File del Server (*.bat;*.jar;server.properties;*.*)|*.bat;*.jar;server.properties;*.*|Tutti i file (*.*)|*.*";
                    }

                    ofd.CheckFileExists = false;
                    ofd.RestoreDirectory = true;

                    string startDir = initialPath;
                    if (!string.IsNullOrEmpty(startDir))
                    {
                        if (File.Exists(startDir)) startDir = Path.GetDirectoryName(startDir);
                    }
                    if (string.IsNullOrEmpty(startDir) || !Directory.Exists(startDir))
                    {
                        startDir = Environment.GetFolderPath(Environment.SpecialFolder.Desktop);
                    }
                    ofd.InitialDirectory = startDir;

                    // ShowDialog(this) guarantees it's an owned modal strictly in the FOREGROUND on top of MainWindow!
                    DialogResult res = ofd.ShowDialog(this);
                    if (res == DialogResult.OK && !string.IsNullOrEmpty(ofd.FileName))
                    {
                        string chosen = ofd.FileName;
                        bool isFile = File.Exists(chosen);
                        string folder = isFile ? Path.GetDirectoryName(chosen) : chosen;

                        string jsonEscChosen = chosen.Replace("\\", "\\\\").Replace("\"", "\\\"");
                        string jsonEscFolder = (folder != null ? folder : "").Replace("\\", "\\\\").Replace("\"", "\\\"");

                        string response = "{\"action\":\"native_browse_result\",\"target\":\"" + target + "\",\"success\":true,\"selected_path\":\"" + jsonEscChosen + "\",\"folder_path\":\"" + jsonEscFolder + "\",\"is_file\":" + (isFile ? "true" : "false") + "}";
                        
                        if (webView != null && webView.CoreWebView2 != null)
                        {
                            webView.CoreWebView2.PostWebMessageAsJson(response);
                        }
                    }
                    else
                    {
                        string responseCancel = "{\"action\":\"native_browse_result\",\"target\":\"" + target + "\",\"success\":false,\"cancelled\":true}";
                        if (webView != null && webView.CoreWebView2 != null)
                        {
                            webView.CoreWebView2.PostWebMessageAsJson(responseCancel);
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                string errEsc = ex.Message.Replace("\\", "\\\\").Replace("\"", "\\\"");
                string responseErr = "{\"action\":\"native_browse_result\",\"target\":\"" + target + "\",\"success\":false,\"error\":\"" + errEsc + "\"}";
                if (webView != null && webView.CoreWebView2 != null)
                {
                    webView.CoreWebView2.PostWebMessageAsJson(responseErr);
                }
            }
        }

        protected override void WndProc(ref Message m)
        {
            if (m.Msg == Program.WM_SHOW_APP)
            {
                ShowAndActivate();
                return;
            }
            base.WndProc(ref m);
        }
    }

    static class Program
    {
        public const string WM_SHOW_APP_NAME = "SHOW_MINECRAFT_CONTROL_CENTER_MAIN_WINDOW";
        public static readonly uint WM_SHOW_APP = RegisterWindowMessage(WM_SHOW_APP_NAME);

        [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Auto)]
        public static extern uint RegisterWindowMessage(string lpString);

        [DllImport("user32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool PostMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);

        public static readonly IntPtr HWND_BROADCAST = new IntPtr(0xffff);

        static NotifyIcon trayIcon;
        static Process pythonProcess;
        static string backendLogPath = "";
        static StreamWriter backendLog = null;
        static readonly object backendLogLock = new object();
        public static string BackendError = null;
        static string appDir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
        static int webPort = 8080;
        static string localUrl = "http://localhost:8080";
        static bool isRemoteMode = false;
        static string remoteUrl = "http://localhost:8080";
        static string configPath = "";
        // Lingua scelta durante l'installazione (it, en, es, fr, de, pt): il pannello parte in questa lingua
        // finché l'utente non ne sceglie un'altra dal pannello stesso
        public static string AppLanguage = "";
        // Topic ntfy e identificativo del PC Host, imparati dalla rete locale (/api/discovery): servono per
        // ritrovare il nuovo indirizzo remoto da fuori casa senza fidarsi di topic fissi o di altri PC.
        // hostCertFingerprint = SHA-256 del certificato HTTPS autofirmato dell'Host (esadecimale minuscolo):
        // da fuori casa si accetta solo il certificato con questa impronta.
        static string hostPushTopic = "";
        static string hostId = "";
        static string hostCertFingerprint = "";
        static MainWindow mainWindow = null;
        static Icon appIcon = null;

        const string RUN_KEY = @"Software\Microsoft\Windows\CurrentVersion\Run";
        const string APP_NAME = "ControlloServerMinecraft";

        public static bool IsAutoStartEnabled()
        {
            try
            {
                using (RegistryKey rk = Registry.CurrentUser.OpenSubKey(RUN_KEY, false))
                {
                    if (rk == null) return false;
                    return rk.GetValue(APP_NAME) != null;
                }
            }
            catch { return false; }
        }

        public static void SetAutoStart(bool enable)
        {
            try
            {
                using (RegistryKey rk = Registry.CurrentUser.OpenSubKey(RUN_KEY, true))
                {
                    if (rk == null) return;
                    if (enable)
                    {
                        string exePath = Application.ExecutablePath;
                        rk.SetValue(APP_NAME, string.Format("\"{0}\" --autostart", exePath));
                    }
                    else
                    {
                        rk.DeleteValue(APP_NAME, false);
                    }
                }
            }
            catch { }
        }

        [STAThread]
        static void Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            bool isAutostart = false;
            if (args != null)
            {
                foreach (string a in args)
                {
                    if (a.Equals("--autostart", StringComparison.OrdinalIgnoreCase) ||
                        a.Equals("/autostart", StringComparison.OrdinalIgnoreCase))
                    {
                        isAutostart = true;
                    }
                }
            }

            // Il pannello HTTPS del PC Host richiede TLS 1.2 (non attivo di default per questo eseguibile)
            try { ServicePointManager.SecurityProtocol |= SecurityProtocolType.Tls12; } catch { }

            configPath = Path.Combine(appDir, "launcher_config.json");
            LoadLauncherConfig();

            try
            {
                string iconPath = Path.Combine(appDir, "minecraft.ico");
                if (File.Exists(iconPath))
                {
                    appIcon = new Icon(iconPath);
                }
                else
                {
                    appIcon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
                }
            }
            catch { }

            string mutexName = isRemoteMode ? "Global\\MinecraftControlCenter_Remote" : "Global\\MinecraftControlCenter_SingleInstance";
            bool createdNew;
            using (Mutex mutex = new Mutex(true, mutexName, out createdNew))
            {
                if (!createdNew)
                {
                    // L'applicazione è già attiva in memoria: invia messaggio di attivazione alla finestra esistente
                    PostMessage(HWND_BROADCAST, WM_SHOW_APP, IntPtr.Zero, IntPtr.Zero);
                    return;
                }

                if (isRemoteMode)
                {
                    SetupRemoteTray();
                    mainWindow = new MainWindow(remoteUrl, appIcon, trayIcon);
                    string startUrl = remoteUrl;
                    Task.Factory.StartNew(() => LearnHostIdentity(startUrl));

                    if (isAutostart)
                    {
                        if (trayIcon != null)
                            trayIcon.ShowBalloonTip(3500, "Voxarium", "Client avviato automaticamente con Windows.", ToolTipIcon.Info);
                    }
                    else
                    {
                        mainWindow.ShowAndActivate();
                    }
                }
                else
                {
                    // Modalità Host Locale: crea subito la finestra visibile e avvia backend Python in background
                    SetupLocalTray();
                    mainWindow = new MainWindow(localUrl, appIcon, trayIcon);
                    mainWindow.UpdateLoadingText("Avvio del pannello del server in corso...");

                    if (isAutostart)
                    {
                        if (trayIcon != null)
                            trayIcon.ShowBalloonTip(3500, "Voxarium", "Pannello e server avviati automaticamente con Windows.", ToolTipIcon.Info);
                    }
                    else
                    {
                        mainWindow.ShowAndActivate();
                    }

                    Task.Factory.StartNew(() => StartPythonBackend()).ContinueWith(t => {
                        if (!t.Result)
                        {
                            mainWindow.ShowBackendError(BackendError);
                        }
                        else
                        {
                            mainWindow.NavigateTo(localUrl);
                        }
                    }, TaskScheduler.FromCurrentSynchronizationContext());
                }

                Application.ApplicationExit += (s, e) => {
                    if (trayIcon != null) trayIcon.Visible = false;
                };

                Application.Run();
            }
        }

        static void LoadLauncherConfig()
        {
            if (File.Exists(configPath))
            {
                try
                {
                    string txt = File.ReadAllText(configPath);
                    if (txt.IndexOf("\"mode\": \"remote\"", StringComparison.OrdinalIgnoreCase) >= 0 ||
                        txt.IndexOf("\"mode\":\"remote\"", StringComparison.OrdinalIgnoreCase) >= 0)
                    {
                        isRemoteMode = true;
                    }

                    string parsed = ReadJsonString(txt, "remote_url");
                    if (!string.IsNullOrEmpty(parsed)) remoteUrl = parsed;
                    hostPushTopic = ReadJsonString(txt, "push_topic");
                    hostId = ReadJsonString(txt, "host_id");
                    string lang = ReadJsonString(txt, "language");
                    if (Regex.IsMatch(lang, "^[a-z]{2}$")) AppLanguage = lang;
                    string fp = ReadJsonString(txt, "cert_fingerprint");
                    if (Regex.IsMatch(fp, "^[0-9a-f]{64}$")) hostCertFingerprint = fp;
                }
                catch { }
            }
        }

        static string ReadJsonString(string txt, string key)
        {
            Match m = Regex.Match(txt, "\"" + Regex.Escape(key) + "\"\\s*:\\s*\"([^\"]*)\"");
            return m.Success ? m.Groups[1].Value.Trim() : "";
        }

        /// <summary>True se il certificato è quello dell'Host: SHA-256 del DER uguale all'impronta imparata in rete locale.</summary>
        public static bool IsPinnedHostCertificate(X509Certificate cert)
        {
            string pinned = hostCertFingerprint;
            if (cert == null || string.IsNullOrEmpty(pinned)) return false;
            try
            {
                byte[] hash;
                using (SHA256 sha = SHA256.Create()) hash = sha.ComputeHash(cert.GetRawCertData());
                return BitConverter.ToString(hash).Replace("-", "").ToLowerInvariant() == pinned;
            }
            catch { return false; }
        }

        /// <summary>Richiesta verso il pannello dell'Host: un certificato non valido è accettato solo se è quello dell'Host.</summary>
        static HttpWebRequest CreateHostRequest(string url)
        {
            HttpWebRequest req = (HttpWebRequest)WebRequest.Create(url);
            req.ServerCertificateValidationCallback = (sender, cert, chain, errors) =>
                errors == SslPolicyErrors.None || IsPinnedHostCertificate(cert);
            return req;
        }

        /// <summary>Se l'Host è raggiungibile in rete locale, salva il suo topic ntfy, il suo identificativo
        /// e l'impronta del suo certificato HTTPS.</summary>
        static void LearnHostIdentity(string baseUrl)
        {
            if (string.IsNullOrEmpty(baseUrl) || !baseUrl.StartsWith("http://", StringComparison.OrdinalIgnoreCase)) return;
            try
            {
                HttpWebRequest req = (HttpWebRequest)WebRequest.Create(baseUrl.TrimEnd('/') + "/api/discovery");
                req.Timeout = 2500;
                using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
                using (StreamReader reader = new StreamReader(resp.GetResponseStream()))
                {
                    string json = reader.ReadToEnd();
                    string topic = ReadJsonString(json, "push_topic");
                    string id = ReadJsonString(json, "host_id");
                    string fp = ReadJsonString(json, "cert_fingerprint");
                    if (Regex.IsMatch(topic, "^[A-Za-z0-9_-]{1,64}$") && Regex.IsMatch(id, "^[0-9a-f]{16}$"))
                    {
                        hostPushTopic = topic;
                        hostId = id;
                        hostCertFingerprint = Regex.IsMatch(fp, "^[0-9a-f]{64}$") ? fp : "";
                        SaveLauncherConfig("remote", remoteUrl);
                    }
                }
            }
            catch { }
        }

        static void SaveLauncherConfig(string mode, string url)
        {
            try
            {
                string json = string.Format("{{\r\n  \"mode\": \"{0}\",\r\n  \"remote_url\": \"{1}\",\r\n  \"push_topic\": \"{2}\",\r\n  \"host_id\": \"{3}\",\r\n  \"cert_fingerprint\": \"{4}\",\r\n  \"language\": \"{5}\"\r\n}}\r\n",
                    mode, url.Replace("\\", "/"), hostPushTopic, hostId, hostCertFingerprint, AppLanguage);
                File.WriteAllText(configPath, json);
            }
            catch (Exception ex)
            {
                MessageBox.Show("Impossibile salvare la configurazione del launcher:\n" + ex.Message, "Errore", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
        }

        static void SetupRemoteTray()
        {
            trayIcon = new NotifyIcon();
            trayIcon.Text = "Voxarium (Remoto)";
            trayIcon.Icon = appIcon != null ? appIcon : SystemIcons.Application;
            trayIcon.Visible = true;

            ContextMenu menu = new ContextMenu();
            menu.MenuItems.Add("Apri Pannello Remoto", (s, e) => {
                if (mainWindow != null) mainWindow.ShowAndActivate();
            });
            menu.MenuItems.Add("Rileva e Riconnetti Subito", (s, e) => {
                TriggerAutoReconnectAsync(true);
            });
            menu.MenuItems.Add("Cambia Indirizzo Server Remoto...", (s, e) => {
                string newUrl = PromptInput("Inserisci l'indirizzo IP del PC secondario o 'auto' per rilevamento automatico:", "Indirizzo Server Remoto", remoteUrl);
                if (!string.IsNullOrEmpty(newUrl))
                {
                    remoteUrl = newUrl;
                    hostPushTopic = "";  // nuovo Host: topic, identificativo e impronta vanno reimparati
                    hostId = "";
                    hostCertFingerprint = "";
                    SaveLauncherConfig("remote", remoteUrl);
                    string learnUrl = remoteUrl;
                    Task.Factory.StartNew(() => LearnHostIdentity(learnUrl));
                    if (remoteUrl.Equals("auto", StringComparison.OrdinalIgnoreCase))
                    {
                        TriggerAutoReconnectAsync(true);
                    }
                    else
                    {
                        trayIcon.ShowBalloonTip(3000, "Indirizzo Aggiornato", "Nuovo indirizzo salvato: " + remoteUrl, ToolTipIcon.Info);
                        if (mainWindow != null) mainWindow.NavigateTo(remoteUrl);
                    }
                }
            });
            menu.MenuItems.Add("-");
            MenuItem itemAutoRemote = new MenuItem("Avvia automaticamente con Windows");
            itemAutoRemote.Checked = IsAutoStartEnabled();
            itemAutoRemote.Click += (s, e) => {
                bool newState = !itemAutoRemote.Checked;
                SetAutoStart(newState);
                itemAutoRemote.Checked = IsAutoStartEnabled();
                if (itemAutoRemote.Checked)
                    trayIcon.ShowBalloonTip(3000, "Avvio Automatico", "L'applicazione si avvierà all'accensione del PC.", ToolTipIcon.Info);
                else
                    trayIcon.ShowBalloonTip(3000, "Avvio Automatico", "Avvio automatico con Windows disattivato.", ToolTipIcon.Info);
            };
            menu.MenuItems.Add(itemAutoRemote);
            menu.MenuItems.Add("-");
            menu.MenuItems.Add("Esci", (s, e) => {
                trayIcon.Visible = false;
                if (mainWindow != null) mainWindow.AllowExitAndClose();
                Application.Exit();
            });

            trayIcon.ContextMenu = menu;
            trayIcon.DoubleClick += (s, e) => {
                if (mainWindow != null) mainWindow.ShowAndActivate();
            };
        }

        static void SetupLocalTray()
        {
            trayIcon = new NotifyIcon();
            trayIcon.Text = "Pannello Server Minecraft (Host Locale)";
            trayIcon.Icon = appIcon != null ? appIcon : SystemIcons.Application;
            trayIcon.Visible = true;

            ContextMenu menu = new ContextMenu();
            menu.MenuItems.Add("Apri Finestra Principale", (s, e) => {
                if (mainWindow != null) mainWindow.ShowAndActivate();
            });
            menu.MenuItems.Add("-");
            MenuItem itemAutoLocal = new MenuItem("Avvia automaticamente con Windows");
            itemAutoLocal.Checked = IsAutoStartEnabled();
            itemAutoLocal.Click += (s, e) => {
                bool newState = !itemAutoLocal.Checked;
                SetAutoStart(newState);
                itemAutoLocal.Checked = IsAutoStartEnabled();
                if (itemAutoLocal.Checked)
                    trayIcon.ShowBalloonTip(3000, "Avvio Automatico", "L'applicazione si avvierà all'accensione del PC.", ToolTipIcon.Info);
                else
                    trayIcon.ShowBalloonTip(3000, "Avvio Automatico", "Avvio automatico con Windows disattivato.", ToolTipIcon.Info);
            };
            menu.MenuItems.Add(itemAutoLocal);
            menu.MenuItems.Add("-");
            menu.MenuItems.Add("Esci da Tutto", (s, e) => {
                trayIcon.Visible = false;
                if (mainWindow != null) mainWindow.AllowExitAndClose();
                KillPythonBackend();
                Application.Exit();
            });

            trayIcon.ContextMenu = menu;
            trayIcon.DoubleClick += (s, e) => {
                if (mainWindow != null) mainWindow.ShowAndActivate();
            };
        }

        static string FindPythonExe()
        {
            // 1. Runtime incluso nell'installazione
            string bundled = Path.Combine(appDir, @"python\python.exe");
            if (File.Exists(bundled)) return bundled;
            // 2. Python di sviluppo di questo PC
            string dev = @"C:\Python314\python.exe";
            if (File.Exists(dev)) return dev;
            // 3. python.exe nel PATH, escluso l'alias del Microsoft Store (WindowsApps)
            string path = Environment.GetEnvironmentVariable("PATH") ?? "";
            foreach (string dir in path.Split(';'))
            {
                if (string.IsNullOrEmpty(dir)) continue;
                if (dir.IndexOf("WindowsApps", StringComparison.OrdinalIgnoreCase) >= 0) continue;
                try
                {
                    string candidate = Path.Combine(dir.Trim(), "python.exe");
                    if (File.Exists(candidate)) return candidate;
                }
                catch { }
            }
            return null;
        }

        static void WriteBackendLog(string line)
        {
            if (line == null) return;
            lock (backendLogLock)
            {
                try
                {
                    if (backendLog != null)
                    {
                        backendLog.WriteLine(line);
                        backendLog.Flush();
                    }
                }
                catch { }
            }
        }

        public static string GetBackendLogTail(int lines)
        {
            try
            {
                if (string.IsNullOrEmpty(backendLogPath) || !File.Exists(backendLogPath)) return "";
                string text;
                using (FileStream fs = new FileStream(backendLogPath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                using (StreamReader sr = new StreamReader(fs))
                {
                    text = sr.ReadToEnd();
                }
                string[] all = text.Split(new string[] { "\r\n", "\n" }, StringSplitOptions.None);
                int start = Math.Max(0, all.Length - lines);
                return string.Join("\r\n", all, start, all.Length - start).Trim();
            }
            catch { return ""; }
        }

        public static bool StartPythonBackend()
        {
            BackendError = null;
            if (IsPortOpen("127.0.0.1", webPort, 300))
            {
                return true; // Già attivo
            }

            // Installazione normale: solo app.pyc (compilato, senza sorgente); app.py c'è solo nelle cartelle di sviluppo
            string appPyPath = Path.Combine(appDir, "app.py");
            if (!File.Exists(appPyPath)) appPyPath = Path.Combine(appDir, "app.pyc");
            if (!File.Exists(appPyPath))
            {
                BackendError = "File app.pyc non trovato in:\r\n" + appDir;
                return false;
            }

            string pyExe = FindPythonExe();
            if (pyExe == null)
            {
                BackendError = "Runtime Python non trovato.\r\nReinstalla il programma: la cartella 'python' dell'installazione è mancante.";
                return false;
            }

            string logDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Controllo Server Minecraft");
            try { Directory.CreateDirectory(logDir); } catch { }
            backendLogPath = Path.Combine(logDir, "backend.log");
            lock (backendLogLock)
            {
                try
                {
                    if (backendLog != null) backendLog.Dispose();
                    backendLog = new StreamWriter(
                        new FileStream(backendLogPath, FileMode.Create, FileAccess.Write, FileShare.ReadWrite),
                        new System.Text.UTF8Encoding(false));
                }
                catch { backendLog = null; }
            }
            WriteBackendLog("[Launcher] " + DateTime.Now.ToString("dd/MM/yyyy HH:mm:ss") + " avvio: \"" + pyExe + "\" -u \"" + appPyPath + "\"");

            ProcessStartInfo psi = new ProcessStartInfo();
            psi.FileName = pyExe;
            psi.Arguments = "-u \"" + appPyPath + "\"";
            psi.WorkingDirectory = appDir;
            psi.CreateNoWindow = true;
            psi.UseShellExecute = false;
            psi.WindowStyle = ProcessWindowStyle.Hidden;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            psi.StandardOutputEncoding = System.Text.Encoding.UTF8;
            psi.StandardErrorEncoding = System.Text.Encoding.UTF8;
            psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            psi.EnvironmentVariables["PYTHONUTF8"] = "1";

            try
            {
                pythonProcess = Process.Start(psi);
                pythonProcess.OutputDataReceived += (s, e) => WriteBackendLog(e.Data);
                pythonProcess.ErrorDataReceived += (s, e) => WriteBackendLog(e.Data);
                pythonProcess.BeginOutputReadLine();
                pythonProcess.BeginErrorReadLine();
            }
            catch (Exception ex)
            {
                BackendError = "Errore nell'avvio di Python:\r\n" + ex.Message;
                return false;
            }

            // Attende fino a 45 secondi l'apertura della porta, fermandosi subito se il processo termina
            Stopwatch sw = Stopwatch.StartNew();
            while (sw.Elapsed.TotalSeconds < 45)
            {
                if (IsPortOpen("127.0.0.1", webPort, 200)) return true;
                if (pythonProcess.HasExited)
                {
                    Thread.Sleep(300); // lascia arrivare le ultime righe nel log
                    BackendError = "Il pannello si è chiuso all'avvio (codice " + pythonProcess.ExitCode + ").\r\n\r\n" + GetBackendLogTail(12);
                    return false;
                }
                Thread.Sleep(200);
            }
            BackendError = "Il pannello non ha risposto sulla porta " + webPort + " entro 45 secondi.\r\n\r\n" + GetBackendLogTail(12);
            return false;
        }

        static void KillPythonBackend()
        {
            if (isRemoteMode) return;
            try
            {
                if (pythonProcess != null && !pythonProcess.HasExited)
                {
                    pythonProcess.Kill();
                }
            }
            catch {}
        }

        static string PromptInput(string prompt, string title, string defaultValue)
        {
            using (Form form = new Form())
            {
                Label label = new Label();
                TextBox textBox = new TextBox();
                Button buttonOk = new Button();
                Button buttonCancel = new Button();

                form.Text = title;
                label.Text = prompt;
                textBox.Text = defaultValue;

                buttonOk.Text = "Salva";
                buttonCancel.Text = "Annulla";
                buttonOk.DialogResult = DialogResult.OK;
                buttonCancel.DialogResult = DialogResult.Cancel;

                label.SetBounds(16, 16, 380, 26);
                textBox.SetBounds(16, 46, 380, 23);
                buttonOk.SetBounds(236, 82, 75, 26);
                buttonCancel.SetBounds(321, 82, 75, 26);

                form.ClientSize = new Size(415, 122);
                form.FormBorderStyle = FormBorderStyle.FixedDialog;
                form.StartPosition = FormStartPosition.CenterScreen;
                form.MinimizeBox = false;
                form.MaximizeBox = false;
                form.AcceptButton = buttonOk;
                form.CancelButton = buttonCancel;

                form.Controls.AddRange(new Control[] { label, textBox, buttonOk, buttonCancel });
                return (form.ShowDialog() == DialogResult.OK) ? textBox.Text.Trim() : null;
            }
        }

        static bool IsPortOpen(string host, int port, int timeoutMs)
        {
            try
            {
                using (TcpClient client = new TcpClient())
                {
                    IAsyncResult result = client.BeginConnect(host, port, null, null);
                    bool success = result.AsyncWaitHandle.WaitOne(timeoutMs);
                    if (!success) return false;
                    client.EndConnect(result);
                    return true;
                }
            }
            catch
            {
                return false;
            }
        }

        public static bool IsRemote()
        {
            return isRemoteMode;
        }

        public static List<string> QueryNtfyCandidates(string topic)
        {
            List<string> candidates = new List<string>();
            try
            {
                string url = string.Format("https://ntfy.sh/{0}/json?poll=1&since=24h", topic);
                HttpWebRequest req = (HttpWebRequest)WebRequest.Create(url);
                req.Timeout = 4000;
                req.UserAgent = "MinecraftServerController/1.0";
                using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
                using (StreamReader reader = new StreamReader(resp.GetResponseStream()))
                {
                    string line;
                    while ((line = reader.ReadLine()) != null)
                    {
                        // Look for LAN URLs
                        Match mLocal = Regex.Match(line, @"(?:local_url""\s*:\s*""|lan=|[Rr]ete LAN:\s*)(http://[0-9\.]+:[0-9]+)");
                        if (mLocal.Success)
                        {
                            string cand = mLocal.Groups[1].Value.Trim();
                            if (!candidates.Contains(cand)) candidates.Insert(0, cand);
                        }

                        // Look for direct remote URLs (https://IP pubblico:porta). L'impronta "fp=" del messaggio
                        // non viene usata: il certificato è accettato solo con l'impronta imparata in rete locale.
                        Match mRemote = Regex.Match(line, @"(?:remote_url""\s*:\s*""|click""\s*:\s*""|[Ii]ndirizzo remoto:\s*)(https://(?:[0-9]{1,3}\.){3}[0-9]{1,3}:[0-9]{1,5})");
                        if (mRemote.Success)
                        {
                            string cand = mRemote.Groups[1].Value.Trim();
                            if (!candidates.Contains(cand)) candidates.Add(cand);
                        }
                    }
                }
            }
            catch { }
            return candidates;
        }

        public static bool VerifyServerCandidate(string baseUrl)
        {
            if (string.IsNullOrEmpty(baseUrl)) return false;
            try
            {
                string target = baseUrl.TrimEnd('/') + "/api/status";
                HttpWebRequest req = CreateHostRequest(target);
                req.Timeout = 1500;
                req.Method = "GET";
                using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
                using (StreamReader reader = new StreamReader(resp.GetResponseStream()))
                {
                    string json = reader.ReadToEnd();
                    if (json.Contains("\"state\"") || json.Contains("\"server_info\"") || json.Contains("\"status\""))
                    {
                        return true;
                    }
                }
            }
            catch { }
            return false;
        }

        public static string ScanLocalSubnetForServer(int port = 8080)
        {
            try
            {
                string localIp = null;
                using (Socket socket = new Socket(AddressFamily.InterNetwork, SocketType.Dgram, 0))
                {
                    socket.Connect("8.8.8.8", 65530);
                    IPEndPoint endPoint = socket.LocalEndPoint as IPEndPoint;
                    if (endPoint != null) localIp = endPoint.Address.ToString();
                }

                if (string.IsNullOrEmpty(localIp) || !localIp.Contains(".")) return null;
                int lastDot = localIp.LastIndexOf('.');
                string subnet = localIp.Substring(0, lastDot + 1);

                string foundUrl = null;
                object lockObj = new object();

                Parallel.For(1, 255, new ParallelOptions { MaxDegreeOfParallelism = 64 }, (i, state) =>
                {
                    if (foundUrl != null) { state.Stop(); return; }

                    string testIp = subnet + i;
                    try
                    {
                        using (TcpClient client = new TcpClient())
                        {
                            IAsyncResult ar = client.BeginConnect(testIp, port, null, null);
                            bool success = ar.AsyncWaitHandle.WaitOne(300);
                            if (success && client.Connected)
                            {
                                client.EndConnect(ar);
                                string candUrl = string.Format("http://{0}:{1}", testIp, port);
                                if (VerifyKnownHost(candUrl))
                                {
                                    lock (lockObj)
                                    {
                                        foundUrl = candUrl;
                                        state.Stop();
                                    }
                                }
                            }
                        }
                    }
                    catch { }
                });

                return foundUrl;
            }
            catch { return null; }
        }

        public static string AutoDiscoverServerUrl(Action<string> statusUpdate = null)
        {
            if (statusUpdate != null) statusUpdate("Verifica canali di sincronizzazione Cloud e notifiche push...");

            // 1. Query ntfy for candidates: solo sul topic del proprio Host (imparato in rete locale), mai su un topic fisso
            List<string> ntfyCandidates = string.IsNullOrEmpty(hostPushTopic) ? new List<string>() : QueryNtfyCandidates(hostPushTopic);

            // First, test LAN candidates from ntfy (super fast if at home)
            foreach (string cand in ntfyCandidates)
            {
                if (cand.StartsWith("http://", StringComparison.OrdinalIgnoreCase))
                {
                    if (statusUpdate != null) statusUpdate("Test connessione LAN (" + cand + ")...");
                    if (VerifyKnownHost(cand)) { LearnHostIdentity(cand); return cand; }
                }
            }

            // 2. Fast local subnet scan (in case host changed local IP)
            if (statusUpdate != null) statusUpdate("Scansione rapida della rete Wi-Fi locale (porta 8080)...");
            string lanFound = ScanLocalSubnetForServer(8080);
            if (!string.IsNullOrEmpty(lanFound)) { LearnHostIdentity(lanFound); return lanFound; }

            // 3. Test remote candidates from ntfy (for outside connection, HTTPS con impronta del certificato)
            foreach (string cand in ntfyCandidates)
            {
                if (cand.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
                {
                    if (statusUpdate != null) statusUpdate("Test indirizzo remoto (" + cand + ")...");
                    if (VerifyKnownHost(cand)) return cand;
                }
            }

            return null;
        }

        /// <summary>Come VerifyServerCandidate, ma se l'identificativo dell'Host è noto deve corrispondere.</summary>
        public static bool VerifyKnownHost(string baseUrl)
        {
            if (!VerifyServerCandidate(baseUrl)) return false;
            if (string.IsNullOrEmpty(hostId)) return true;
            try
            {
                HttpWebRequest req = CreateHostRequest(baseUrl.TrimEnd('/') + "/api/status");
                req.Timeout = 2500;
                using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
                using (StreamReader reader = new StreamReader(resp.GetResponseStream()))
                {
                    return ReadJsonString(reader.ReadToEnd(), "host_id") == hostId;
                }
            }
            catch { return false; }
        }

        private static bool isAutoReconnecting = false;
        private static object reconnectLock = new object();

        public static void TriggerAutoReconnectAsync(bool userInitiated = false)
        {
            lock (reconnectLock)
            {
                if (isAutoReconnecting) return;
                isAutoReconnecting = true;
            }

            Task.Factory.StartNew(() =>
            {
                try
                {
                    if (userInitiated && trayIcon != null)
                    {
                        trayIcon.ShowBalloonTip(2000, "Ricerca Server", "Scansione rapida LAN e Cloud in corso...", ToolTipIcon.Info);
                    }

                    if (mainWindow != null)
                    {
                        mainWindow.UpdateLoadingText("Ricerca automatica del server Minecraft in corso...\r\nScansione rete locale Wi-Fi e Cloud.");
                    }

                    string found = AutoDiscoverServerUrl((msg) =>
                    {
                        if (mainWindow != null) mainWindow.UpdateLoadingText(msg);
                    });

                    if (!string.IsNullOrEmpty(found))
                    {
                        remoteUrl = found;
                        SaveLauncherConfig("remote", remoteUrl);

                        if (mainWindow != null)
                        {
                            mainWindow.NavigateTo(found);
                        }

                        if (trayIcon != null)
                        {
                            trayIcon.ShowBalloonTip(3500, "Server Connesso", "Connesso automaticamente al server Minecraft su:\n" + found, ToolTipIcon.Info);
                        }
                    }
                    else
                    {
                        if (mainWindow != null)
                        {
                            mainWindow.UpdateLoadingText("Impossibile trovare il server Minecraft in rete.\r\nVerifica che il PC host sia acceso o seleziona 'Cambia Indirizzo' dal menu dell'icona accanto all'orologio.");
                        }
                        if (userInitiated && trayIcon != null)
                        {
                            trayIcon.ShowBalloonTip(3500, "Server Non Rilevato", "Nessun server trovato sulla rete locale o via Internet.\nVerifica che il server sia avviato.", ToolTipIcon.Warning);
                        }
                    }
                }
                finally
                {
                    lock (reconnectLock)
                    {
                        isAutoReconnecting = false;
                    }
                }
            });
        }
    }
}
