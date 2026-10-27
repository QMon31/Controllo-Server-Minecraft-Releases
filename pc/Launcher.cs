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
            lblLoading.Text = LauncherText.T("win.loading");
            lblLoading.ForeColor = Color.FromArgb(148, 163, 184);
            lblLoading.Font = new Font("Segoe UI", 11.5F, FontStyle.Regular);
            lblLoading.Dock = DockStyle.Fill;
            lblLoading.TextAlign = ContentAlignment.MiddleCenter;
            this.Controls.Add(lblLoading);

            btnRetry = new Button();
            btnRetry.Text = LauncherText.T("win.retry_button");
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
                        trayRef.ShowBalloonTip(3500, "Voxarium", LauncherText.T("win.background_tip"), ToolTipIcon.Info);
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
            pendingError = string.IsNullOrEmpty(message) ? LauncherText.T("err.unknown") : message;
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
            lblLoading.Text = LauncherText.T("err.cannot_start") + "\r\n\r\n" + pendingError +
                              "\r\n\r\n" + LauncherText.T("err.full_log", "%LOCALAPPDATA%\\Controllo Server Minecraft\\backend.log");
            lblLoading.Visible = true;
            btnRetry.Visible = true;
            lblLoading.BringToFront(); // l'etichetta (Dock Fill) occupa lo spazio sopra il pulsante
        }

        private void RetryBackend()
        {
            btnRetry.Enabled = false;
            lblLoading.Text = LauncherText.T("win.retrying");
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
                        UpdateLoadingText(LauncherText.T("win.remote_failed"));
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
                            UpdateLoadingText(LauncherText.T("win.host_try", hostNavRetries));
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
                                pendingError = LauncherText.T("err.not_responding", targetUrl) + "\r\n\r\n" + Program.GetBackendLogTail(12);
                            ApplyBackendError();
                        }
                    }
                };

                if (IsAutoUrl(targetUrl))
                {
                    UpdateLoadingText(LauncherText.T("disc.searching"));
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
                        ofd.Title = LauncherText.T("pick.run_bat_title");
                        ofd.Filter = LauncherText.T("pick.start_files") + " (*.bat;*.jar)|*.bat;*.jar|" + LauncherText.T("pick.batch_files") + " (*.bat)|*.bat|" + LauncherText.T("pick.jar_files") + " (*.jar)|*.jar|" + LauncherText.T("pick.all_files") + " (*.*)|*.*";
                    }
                    else
                    {
                        ofd.Title = LauncherText.T("pick.folder_title");
                        ofd.Filter = LauncherText.T("pick.server_files") + " (*.bat;*.jar;server.properties;*.*)|*.bat;*.jar;server.properties;*.*|" + LauncherText.T("pick.all_files") + " (*.*)|*.*";
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

    /// <summary>
    /// Aggiornamento automatico dei PC Remoti: qui non c'è il backend Python (che sul PC Host controlla gli aggiornamenti),
    /// quindi è il Launcher a leggere pc_version.json della release più recente. Se la Build è più nuova chiede all'utente,
    /// scarica l'installer (solo dagli indirizzi delle release, SHA-256 verificato) e lo avvia con --update: l'installer
    /// chiude questo programma, sostituisce i file conservando l'Host salvato e riapre il programma.
    /// </summary>
    static class RemoteUpdater
    {
        // Stessi indirizzi di pc_updater.py (UPDATE_REPO e LEGACY_UPDATE_REPO in pc_version.py)
        const string ManifestUrl = "https://github.com/QMon31/Voxarium/releases/latest/download/pc_version.json";
        static readonly string[] SetupUrlPrefixes = {
            "https://github.com/QMon31/Voxarium/releases/download/",
            "https://github.com/QMon31/Controllo-Server-Minecraft-Releases/releases/download/"
        };
        const int FirstCheckDelayMs = 45 * 1000;
        const int CheckIntervalMs = 6 * 3600 * 1000;

        static NotifyIcon tray;
        static int busy = 0;            // 1 durante un controllo o un download
        static int declinedBuild = 0;   // Build rifiutata dall'utente: non la si richiede fino al prossimo avvio

        public class Manifest
        {
            public string Version = "";
            public int Build = 0;
            public string SetupUrl = "";
            public string Sha256 = "";
        }

        /// <summary>Build di questo eseguibile (ultima cifra della versione scritta da build_deploy.py); 0 se compilato senza.</summary>
        public static int CurrentBuild()
        {
            try { return Math.Max(0, Assembly.GetExecutingAssembly().GetName().Version.Revision); }
            catch { return 0; }
        }

        public static string CurrentVersion()
        {
            try
            {
                Version v = Assembly.GetExecutingAssembly().GetName().Version;
                return v.Major + "." + v.Minor + "." + v.Build;
            }
            catch { return "?"; }
        }

        /// <summary>Legge il manifest; null se manca qualcosa o se l'installer non viene dalle release del programma.</summary>
        public static Manifest ParseManifest(string json)
        {
            if (string.IsNullOrEmpty(json)) return null;
            Match b = Regex.Match(json, "\"build\"\\s*:\\s*(\\d{1,6})");
            Manifest m = new Manifest();
            m.Version = ReadString(json, "version");
            m.SetupUrl = ReadString(json, "setup_url");
            m.Sha256 = ReadString(json, "sha256").ToLowerInvariant();
            m.Build = b.Success ? int.Parse(b.Groups[1].Value) : 0;
            if (m.Build <= 0 || !Regex.IsMatch(m.Sha256, "^[0-9a-f]{64}$") || !IsAllowedSetupUrl(m.SetupUrl)) return null;
            if (!Regex.IsMatch(m.Version, "^[0-9A-Za-z.+-]{1,32}$")) m.Version = "?";
            return m;
        }

        public static bool IsAllowedSetupUrl(string url)
        {
            if (string.IsNullOrEmpty(url) || url.Contains("..") || !url.EndsWith(".exe", StringComparison.OrdinalIgnoreCase)) return false;
            foreach (string p in SetupUrlPrefixes)
            {
                if (url.StartsWith(p, StringComparison.Ordinal)) return true;
            }
            return false;
        }

        static string ReadString(string json, string key)
        {
            Match m = Regex.Match(json, "\"" + Regex.Escape(key) + "\"\\s*:\\s*\"([^\"]*)\"");
            return m.Success ? m.Groups[1].Value.Trim() : "";
        }

        public static void Start(NotifyIcon trayIcon)
        {
            tray = trayIcon;
            if (CurrentBuild() == 0) return;  // compilazione di prova senza versione: niente aggiornamenti
            Thread t = new Thread(() => {
                Thread.Sleep(FirstCheckDelayMs);  // lascia aprire il pannello
                while (true)
                {
                    CheckNow(false);
                    Thread.Sleep(CheckIntervalMs);
                }
            });
            t.IsBackground = true;
            t.SetApartmentState(ApartmentState.STA);
            t.Start();
        }

        /// <summary>Voce "Controlla aggiornamenti" del menu: controllo subito, con l'esito anche se non c'è niente di nuovo.</summary>
        public static void CheckFromMenu()
        {
            Thread t = new Thread(() => CheckNow(true));
            t.IsBackground = true;
            t.SetApartmentState(ApartmentState.STA);
            t.Start();
        }

        static void CheckNow(bool manual)
        {
            if (Interlocked.CompareExchange(ref busy, 1, 0) != 0) return;
            try
            {
                Manifest m;
                try
                {
                    m = ParseManifest(Download(ManifestUrl));
                    if (m == null) throw new InvalidDataException("pc_version.json");
                }
                catch (Exception ex)
                {
                    if (manual) Balloon(LauncherText.T("upd.check_failed", ex.Message), ToolTipIcon.Warning);
                    return;
                }
                int current = CurrentBuild();
                if (m.Build <= current)
                {
                    if (manual) Balloon(LauncherText.T("upd.up_to_date", CurrentVersion(), current), ToolTipIcon.Info);
                    return;
                }
                if (!manual && m.Build == declinedBuild) return;

                DialogResult answer;
                using (Form owner = new Form())
                {
                    owner.TopMost = true;
                    owner.ShowInTaskbar = false;
                    answer = MessageBox.Show(owner,
                        LauncherText.T("upd.ask", m.Version, m.Build, CurrentVersion(), current),
                        LauncherText.T("upd.title"), MessageBoxButtons.YesNo, MessageBoxIcon.Information);
                }
                if (answer != DialogResult.Yes)
                {
                    declinedBuild = m.Build;
                    return;
                }
                Balloon(LauncherText.T("upd.downloading"), ToolTipIcon.Info);
                try
                {
                    string setup = DownloadInstaller(m);
                    Process.Start(new ProcessStartInfo(setup, "--update") { UseShellExecute = true });
                }
                catch (Exception ex)
                {
                    Balloon(LauncherText.T("upd.failed", ex.Message), ToolTipIcon.Error);
                }
            }
            finally
            {
                Interlocked.Exchange(ref busy, 0);
            }
        }

        static HttpWebRequest NewRequest(string url, int timeoutMs)
        {
            HttpWebRequest req = (HttpWebRequest)WebRequest.Create(url);
            req.UserAgent = "ControlloServerMinecraft/" + CurrentVersion();
            req.Timeout = timeoutMs;
            req.ReadWriteTimeout = timeoutMs;
            req.AllowAutoRedirect = true;  // GitHub reindirizza i file delle release ai suoi server dei download
            return req;
        }

        static string Download(string url)
        {
            using (HttpWebResponse resp = (HttpWebResponse)NewRequest(url, 15000).GetResponse())
            using (StreamReader reader = new StreamReader(resp.GetResponseStream()))
            {
                return reader.ReadToEnd();
            }
        }

        /// <summary>Scarica l'installer nella cartella temporanea e ne verifica lo SHA-256; restituisce il percorso.</summary>
        static string DownloadInstaller(Manifest m)
        {
            string dest = Path.Combine(Path.GetTempPath(), "Setup_Voxarium_b" + m.Build + ".exe");
            string part = dest + ".part";
            using (SHA256 sha = SHA256.Create())
            {
                using (HttpWebResponse resp = (HttpWebResponse)NewRequest(m.SetupUrl, 60000).GetResponse())
                using (Stream input = resp.GetResponseStream())
                using (FileStream output = new FileStream(part, FileMode.Create, FileAccess.Write))
                {
                    byte[] buffer = new byte[1024 * 1024];
                    int read;
                    while ((read = input.Read(buffer, 0, buffer.Length)) > 0)
                    {
                        output.Write(buffer, 0, read);
                        sha.TransformBlock(buffer, 0, read, null, 0);
                    }
                    sha.TransformFinalBlock(buffer, 0, 0);
                }
                string hex = BitConverter.ToString(sha.Hash).Replace("-", "").ToLowerInvariant();
                if (hex != m.Sha256)
                {
                    try { File.Delete(part); } catch { }
                    throw new InvalidDataException("SHA-256");
                }
            }
            if (File.Exists(dest)) File.Delete(dest);
            File.Move(part, dest);
            return dest;
        }

        static void Balloon(string text, ToolTipIcon icon)
        {
            try { if (tray != null) tray.ShowBalloonTip(5000, "Voxarium", text, icon); } catch { }
        }
    }

    /// <summary>Testi del Launcher per l'aggiornamento dei PC Remoti, nella lingua scelta durante l'installazione.</summary>
    static class LauncherText
    {
        static readonly Dictionary<string, Dictionary<string, string>> Texts = new Dictionary<string, Dictionary<string, string>> {
            { "it", new Dictionary<string, string> {
                { "upd.title", "Aggiornamento disponibile" },
                { "upd.ask", "È disponibile Voxarium v{0} (Build {1}); questo PC ha la v{2} (Build {3}).\n\nAggiornarlo adesso? Il programma si chiude durante l'aggiornamento e si riapre da solo; l'indirizzo del PC Host resta quello salvato." },
                { "upd.downloading", "Download dell'aggiornamento in corso..." },
                { "upd.failed", "Aggiornamento non riuscito: {0}" },
                { "upd.check_failed", "Controllo aggiornamenti non riuscito: {0}" },
                { "upd.up_to_date", "Il programma è aggiornato (v{0}, Build {1})." },
                { "win.loading", "Caricamento interfaccia di controllo in corso..." },
                { "win.retry_button", "Riprova ad avviare il pannello" },
                { "win.background_tip", "L'applicazione continua a funzionare in background.\nFai doppio clic sull'icona accanto all'orologio per riaprirla." },
                { "win.retrying", "Nuovo tentativo di avvio del pannello in corso..." },
                { "win.remote_failed", "Connessione non riuscita.\r\nRicerca automatica del PC Host in corso (rete di casa e internet)..." },
                { "win.host_try", "Avvio del pannello in corso... (tentativo {0}/30)" },
                { "win.host_starting", "Avvio del pannello del server in corso..." },
                { "err.unknown", "Errore sconosciuto." },
                { "err.cannot_start", "Impossibile avviare il pannello del server." },
                { "err.full_log", "Registro completo: {0}" },
                { "err.not_responding", "Il pannello non risponde su {0}." },
                { "err.save_config", "Impossibile salvare la configurazione del launcher:\n{0}" },
                { "err.title", "Errore" },
                { "err.no_app", "File app.pyc non trovato in:\r\n{0}" },
                { "err.no_runtime", "Runtime Python non trovato.\r\nReinstalla il programma: la cartella 'python' dell'installazione è mancante." },
                { "err.python_start", "Errore nell'avvio di Python:\r\n{0}" },
                { "err.closed", "Il pannello si è chiuso all'avvio (codice {0})." },
                { "err.timeout", "Il pannello non ha risposto sulla porta {0} entro 45 secondi." },
                { "pick.run_bat_title", "Seleziona il file di avvio del server (run.bat)" },
                { "pick.folder_title", "Seleziona la cartella del server Minecraft (entra nella cartella e scegli run.bat o un file qualsiasi)" },
                { "pick.start_files", "File di avvio" },
                { "pick.batch_files", "File batch" },
                { "pick.jar_files", "File jar" },
                { "pick.server_files", "File del server" },
                { "pick.all_files", "Tutti i file" },
                { "tray.client_autostarted", "Client avviato automaticamente con Windows." },
                { "tray.host_autostarted", "Pannello e server avviati automaticamente con Windows." },
                { "tray.remote_tooltip", "Voxarium (Remoto)" },
                { "tray.host_tooltip", "Voxarium (PC Host)" },
                { "tray.open_remote", "Apri il pannello" },
                { "tray.open_main", "Apri la finestra principale" },
                { "tray.reconnect", "Cerca il PC Host e ricollegati" },
                { "tray.change_address", "Cambia indirizzo del PC Host..." },
                { "prompt.address", "Indirizzo del PC Host (es. http://192.168.1.50:8080), oppure 'auto' per cercarlo da solo:" },
                { "prompt.address_title", "Indirizzo del PC Host" },
                { "prompt.save", "Salva" },
                { "prompt.cancel", "Annulla" },
                { "tray.address_saved_title", "Indirizzo aggiornato" },
                { "tray.address_saved", "Nuovo indirizzo salvato: {0}" },
                { "tray.autostart", "Avvia automaticamente con Windows" },
                { "tray.autostart_title", "Avvio automatico" },
                { "tray.autostart_on", "L'applicazione si avvierà all'accensione del PC." },
                { "tray.autostart_off", "Avvio automatico con Windows disattivato." },
                { "tray.exit", "Esci" },
                { "tray.exit_all", "Esci (chiude pannello e server)" },
                { "disc.channels", "Controllo dei canali di notifica e dell'indirizzo da internet..." },
                { "disc.test_lan", "Prova della rete di casa ({0})..." },
                { "disc.scan_wifi", "Ricerca veloce nella rete Wi-Fi di casa (porta 8080)..." },
                { "disc.test_remote", "Prova dell'indirizzo da internet ({0})..." },
                { "disc.search_title", "Ricerca del PC Host" },
                { "disc.search_balloon", "Ricerca veloce in rete e su internet in corso..." },
                { "disc.searching", "Ricerca automatica del PC Host in corso...\r\nRete di casa e indirizzo da internet." },
                { "disc.connected_title", "Collegato" },
                { "disc.connected", "Collegato al PC Host:\n{0}" },
                { "disc.not_found_window", "PC Host non trovato.\r\nControlla che sia acceso, oppure scegli 'Cambia indirizzo del PC Host' dal menu dell'icona accanto all'orologio." },
                { "disc.not_found_title", "PC Host non trovato" },
                { "disc.not_found", "Nessun PC Host trovato in rete o su internet.\nControlla che sia acceso e che il programma sia avviato." },
                { "upd.menu", "Controlla aggiornamenti" } } },
            { "en", new Dictionary<string, string> {
                { "upd.title", "Update available" },
                { "upd.ask", "Voxarium v{0} (Build {1}) is available; this PC has v{2} (Build {3}).\n\nUpdate it now? The program closes during the update and reopens by itself; the Host PC address stays the saved one." },
                { "upd.downloading", "Downloading the update..." },
                { "upd.failed", "Update failed: {0}" },
                { "upd.check_failed", "Update check failed: {0}" },
                { "upd.up_to_date", "The program is up to date (v{0}, Build {1})." },
                { "win.loading", "Loading the control panel..." },
                { "win.retry_button", "Try starting the panel again" },
                { "win.background_tip", "The application keeps running in the background.\nDouble-click the icon next to the clock to open it again." },
                { "win.retrying", "Trying to start the panel again..." },
                { "win.remote_failed", "Connection failed.\r\nSearching for the Host PC (home network and internet)..." },
                { "win.host_try", "Starting the panel... (attempt {0}/30)" },
                { "win.host_starting", "Starting the server panel..." },
                { "err.unknown", "Unknown error." },
                { "err.cannot_start", "The server panel could not be started." },
                { "err.full_log", "Full log: {0}" },
                { "err.not_responding", "The panel is not responding at {0}." },
                { "err.save_config", "Could not save the launcher settings:\n{0}" },
                { "err.title", "Error" },
                { "err.no_app", "File app.pyc not found in:\r\n{0}" },
                { "err.no_runtime", "Python runtime not found.\r\nReinstall the program: the 'python' folder of the installation is missing." },
                { "err.python_start", "Error starting Python:\r\n{0}" },
                { "err.closed", "The panel closed while starting (code {0})." },
                { "err.timeout", "The panel did not respond on port {0} within 45 seconds." },
                { "pick.run_bat_title", "Select the server start file (run.bat)" },
                { "pick.folder_title", "Select the Minecraft server folder (open the folder and pick run.bat or any file)" },
                { "pick.start_files", "Start files" },
                { "pick.batch_files", "Batch files" },
                { "pick.jar_files", "Jar files" },
                { "pick.server_files", "Server files" },
                { "pick.all_files", "All files" },
                { "tray.client_autostarted", "Client started automatically with Windows." },
                { "tray.host_autostarted", "Panel and server started automatically with Windows." },
                { "tray.remote_tooltip", "Voxarium (Remote)" },
                { "tray.host_tooltip", "Voxarium (Host PC)" },
                { "tray.open_remote", "Open the panel" },
                { "tray.open_main", "Open the main window" },
                { "tray.reconnect", "Find the Host PC and reconnect" },
                { "tray.change_address", "Change Host PC address..." },
                { "prompt.address", "Host PC address (e.g. http://192.168.1.50:8080), or 'auto' to find it automatically:" },
                { "prompt.address_title", "Host PC address" },
                { "prompt.save", "Save" },
                { "prompt.cancel", "Cancel" },
                { "tray.address_saved_title", "Address updated" },
                { "tray.address_saved", "New address saved: {0}" },
                { "tray.autostart", "Start automatically with Windows" },
                { "tray.autostart_title", "Automatic startup" },
                { "tray.autostart_on", "The application will start when the PC is turned on." },
                { "tray.autostart_off", "Automatic startup with Windows turned off." },
                { "tray.exit", "Exit" },
                { "tray.exit_all", "Exit (closes panel and server)" },
                { "disc.channels", "Checking notification channels and internet address..." },
                { "disc.test_lan", "Testing the home network ({0})..." },
                { "disc.scan_wifi", "Quick search on the home Wi-Fi network (port 8080)..." },
                { "disc.test_remote", "Testing the internet address ({0})..." },
                { "disc.search_title", "Searching for the Host PC" },
                { "disc.search_balloon", "Quick search on the network and internet..." },
                { "disc.searching", "Searching for the Host PC...\r\nHome network and internet address." },
                { "disc.connected_title", "Connected" },
                { "disc.connected", "Connected to the Host PC:\n{0}" },
                { "disc.not_found_window", "Host PC not found.\r\nCheck that it is turned on, or choose 'Change Host PC address' from the menu of the icon next to the clock." },
                { "disc.not_found_title", "Host PC not found" },
                { "disc.not_found", "No Host PC found on the network or the internet.\nCheck that it is on and the program is running." },
                { "upd.menu", "Check for updates" } } },
            { "es", new Dictionary<string, string> {
                { "upd.title", "Actualización disponible" },
                { "upd.ask", "Está disponible Voxarium v{0} (Build {1}); este PC tiene la v{2} (Build {3}).\n\n¿Actualizarlo ahora? El programa se cierra durante la actualización y se vuelve a abrir solo; la dirección del PC Host sigue siendo la guardada." },
                { "upd.downloading", "Descargando la actualización..." },
                { "upd.failed", "La actualización no se pudo completar: {0}" },
                { "upd.check_failed", "No se pudo buscar actualizaciones: {0}" },
                { "upd.up_to_date", "El programa está actualizado (v{0}, Build {1})." },
                { "win.loading", "Cargando el panel de control..." },
                { "win.retry_button", "Reintentar iniciar el panel" },
                { "win.background_tip", "La aplicación sigue funcionando en segundo plano.\nHaz doble clic en el icono junto al reloj para volver a abrirla." },
                { "win.retrying", "Reintentando iniciar el panel..." },
                { "win.remote_failed", "No se pudo conectar.\r\nBuscando el PC Host (red de casa e internet)..." },
                { "win.host_try", "Iniciando el panel... (intento {0}/30)" },
                { "win.host_starting", "Iniciando el panel del servidor..." },
                { "err.unknown", "Error desconocido." },
                { "err.cannot_start", "No se pudo iniciar el panel del servidor." },
                { "err.full_log", "Registro completo: {0}" },
                { "err.not_responding", "El panel no responde en {0}." },
                { "err.save_config", "No se pudo guardar la configuración del launcher:\n{0}" },
                { "err.title", "Error" },
                { "err.no_app", "No se encontró el archivo app.pyc en:\r\n{0}" },
                { "err.no_runtime", "No se encontró el entorno de Python.\r\nReinstala el programa: falta la carpeta 'python' de la instalación." },
                { "err.python_start", "Error al iniciar Python:\r\n{0}" },
                { "err.closed", "El panel se cerró al iniciarse (código {0})." },
                { "err.timeout", "El panel no respondió en el puerto {0} en 45 segundos." },
                { "pick.run_bat_title", "Selecciona el archivo de inicio del servidor (run.bat)" },
                { "pick.folder_title", "Selecciona la carpeta del servidor de Minecraft (entra en la carpeta y elige run.bat o cualquier archivo)" },
                { "pick.start_files", "Archivos de inicio" },
                { "pick.batch_files", "Archivos batch" },
                { "pick.jar_files", "Archivos jar" },
                { "pick.server_files", "Archivos del servidor" },
                { "pick.all_files", "Todos los archivos" },
                { "tray.client_autostarted", "Cliente iniciado automáticamente con Windows." },
                { "tray.host_autostarted", "Panel y servidor iniciados automáticamente con Windows." },
                { "tray.remote_tooltip", "Voxarium (Remoto)" },
                { "tray.host_tooltip", "Voxarium (PC Host)" },
                { "tray.open_remote", "Abrir el panel" },
                { "tray.open_main", "Abrir la ventana principal" },
                { "tray.reconnect", "Buscar el PC Host y reconectar" },
                { "tray.change_address", "Cambiar dirección del PC Host..." },
                { "prompt.address", "Dirección del PC Host (p. ej. http://192.168.1.50:8080), o 'auto' para buscarlo automáticamente:" },
                { "prompt.address_title", "Dirección del PC Host" },
                { "prompt.save", "Guardar" },
                { "prompt.cancel", "Cancelar" },
                { "tray.address_saved_title", "Dirección actualizada" },
                { "tray.address_saved", "Nueva dirección guardada: {0}" },
                { "tray.autostart", "Iniciar automáticamente con Windows" },
                { "tray.autostart_title", "Inicio automático" },
                { "tray.autostart_on", "La aplicación se iniciará al encender el PC." },
                { "tray.autostart_off", "Inicio automático con Windows desactivado." },
                { "tray.exit", "Salir" },
                { "tray.exit_all", "Salir (cierra panel y servidor)" },
                { "disc.channels", "Comprobando los canales de notificación y la dirección de internet..." },
                { "disc.test_lan", "Probando la red de casa ({0})..." },
                { "disc.scan_wifi", "Búsqueda rápida en la red Wi-Fi de casa (puerto 8080)..." },
                { "disc.test_remote", "Probando la dirección de internet ({0})..." },
                { "disc.search_title", "Buscando el PC Host" },
                { "disc.search_balloon", "Búsqueda rápida en la red e internet..." },
                { "disc.searching", "Buscando el PC Host...\r\nRed de casa y dirección de internet." },
                { "disc.connected_title", "Conectado" },
                { "disc.connected", "Conectado al PC Host:\n{0}" },
                { "disc.not_found_window", "No se encontró el PC Host.\r\nComprueba que esté encendido o elige 'Cambiar dirección del PC Host' en el menú del icono junto al reloj." },
                { "disc.not_found_title", "PC Host no encontrado" },
                { "disc.not_found", "No se encontró ningún PC Host en la red ni en internet.\nComprueba que esté encendido y que el programa esté abierto." },
                { "upd.menu", "Buscar actualizaciones" } } },
            { "fr", new Dictionary<string, string> {
                { "upd.title", "Mise à jour disponible" },
                { "upd.ask", "Voxarium v{0} (Build {1}) est disponible ; ce PC a la v{2} (Build {3}).\n\nLe mettre à jour maintenant ? Le programme se ferme pendant la mise à jour et se rouvre tout seul ; l'adresse du PC Hôte reste celle enregistrée." },
                { "upd.downloading", "Téléchargement de la mise à jour..." },
                { "upd.failed", "Échec de la mise à jour : {0}" },
                { "upd.check_failed", "Échec de la recherche de mises à jour : {0}" },
                { "upd.up_to_date", "Le programme est à jour (v{0}, Build {1})." },
                { "win.loading", "Chargement du panneau de contrôle..." },
                { "win.retry_button", "Réessayer de démarrer le panneau" },
                { "win.background_tip", "L'application continue de fonctionner en arrière-plan.\nDouble-cliquez sur l'icône près de l'horloge pour la rouvrir." },
                { "win.retrying", "Nouvelle tentative de démarrage du panneau..." },
                { "win.remote_failed", "Échec de la connexion.\r\nRecherche du PC Hôte (réseau domestique et internet)..." },
                { "win.host_try", "Démarrage du panneau... (tentative {0}/30)" },
                { "win.host_starting", "Démarrage du panneau du serveur..." },
                { "err.unknown", "Erreur inconnue." },
                { "err.cannot_start", "Impossible de démarrer le panneau du serveur." },
                { "err.full_log", "Journal complet : {0}" },
                { "err.not_responding", "Le panneau ne répond pas sur {0}." },
                { "err.save_config", "Impossible d'enregistrer la configuration du lanceur :\n{0}" },
                { "err.title", "Erreur" },
                { "err.no_app", "Fichier app.pyc introuvable dans :\r\n{0}" },
                { "err.no_runtime", "Environnement Python introuvable.\r\nRéinstallez le programme : le dossier 'python' de l'installation est manquant." },
                { "err.python_start", "Erreur au démarrage de Python :\r\n{0}" },
                { "err.closed", "Le panneau s'est fermé au démarrage (code {0})." },
                { "err.timeout", "Le panneau n'a pas répondu sur le port {0} en 45 secondes." },
                { "pick.run_bat_title", "Sélectionnez le fichier de démarrage du serveur (run.bat)" },
                { "pick.folder_title", "Sélectionnez le dossier du serveur Minecraft (ouvrez-le et choisissez run.bat ou n'importe quel fichier)" },
                { "pick.start_files", "Fichiers de démarrage" },
                { "pick.batch_files", "Fichiers batch" },
                { "pick.jar_files", "Fichiers jar" },
                { "pick.server_files", "Fichiers du serveur" },
                { "pick.all_files", "Tous les fichiers" },
                { "tray.client_autostarted", "Client démarré automatiquement avec Windows." },
                { "tray.host_autostarted", "Panneau et serveur démarrés automatiquement avec Windows." },
                { "tray.remote_tooltip", "Voxarium (Distant)" },
                { "tray.host_tooltip", "Voxarium (PC Hôte)" },
                { "tray.open_remote", "Ouvrir le panneau" },
                { "tray.open_main", "Ouvrir la fenêtre principale" },
                { "tray.reconnect", "Rechercher le PC Hôte et se reconnecter" },
                { "tray.change_address", "Modifier l'adresse du PC Hôte..." },
                { "prompt.address", "Adresse du PC Hôte (ex. http://192.168.1.50:8080), ou 'auto' pour le rechercher automatiquement :" },
                { "prompt.address_title", "Adresse du PC Hôte" },
                { "prompt.save", "Enregistrer" },
                { "prompt.cancel", "Annuler" },
                { "tray.address_saved_title", "Adresse mise à jour" },
                { "tray.address_saved", "Nouvelle adresse enregistrée : {0}" },
                { "tray.autostart", "Démarrer automatiquement avec Windows" },
                { "tray.autostart_title", "Démarrage automatique" },
                { "tray.autostart_on", "L'application démarrera à l'allumage du PC." },
                { "tray.autostart_off", "Démarrage automatique avec Windows désactivé." },
                { "tray.exit", "Quitter" },
                { "tray.exit_all", "Quitter (ferme le panneau et le serveur)" },
                { "disc.channels", "Vérification des canaux de notification et de l'adresse internet..." },
                { "disc.test_lan", "Test du réseau domestique ({0})..." },
                { "disc.scan_wifi", "Recherche rapide sur le réseau Wi-Fi domestique (port 8080)..." },
                { "disc.test_remote", "Test de l'adresse internet ({0})..." },
                { "disc.search_title", "Recherche du PC Hôte" },
                { "disc.search_balloon", "Recherche rapide sur le réseau et internet..." },
                { "disc.searching", "Recherche du PC Hôte...\r\nRéseau domestique et adresse internet." },
                { "disc.connected_title", "Connecté" },
                { "disc.connected", "Connecté au PC Hôte :\n{0}" },
                { "disc.not_found_window", "PC Hôte introuvable.\r\nVérifiez qu'il est allumé ou choisissez « Modifier l'adresse du PC Hôte » dans le menu de l'icône près de l'horloge." },
                { "disc.not_found_title", "PC Hôte introuvable" },
                { "disc.not_found", "Aucun PC Hôte trouvé sur le réseau ou sur internet.\nVérifiez qu'il est allumé et que le programme est lancé." },
                { "upd.menu", "Rechercher des mises à jour" } } },
            { "de", new Dictionary<string, string> {
                { "upd.title", "Update verfügbar" },
                { "upd.ask", "Voxarium v{0} (Build {1}) ist verfügbar; dieser PC hat v{2} (Build {3}).\n\nJetzt aktualisieren? Das Programm schließt sich während des Updates und öffnet sich danach von selbst; die Adresse des Host-PCs bleibt die gespeicherte." },
                { "upd.downloading", "Update wird heruntergeladen..." },
                { "upd.failed", "Update fehlgeschlagen: {0}" },
                { "upd.check_failed", "Suche nach Updates fehlgeschlagen: {0}" },
                { "upd.up_to_date", "Das Programm ist aktuell (v{0}, Build {1})." },
                { "win.loading", "Steuerungsoberfläche wird geladen..." },
                { "win.retry_button", "Panel erneut starten" },
                { "win.background_tip", "Die Anwendung läuft im Hintergrund weiter.\nDoppelklicke auf das Symbol neben der Uhr, um sie wieder zu öffnen." },
                { "win.retrying", "Neuer Versuch, das Panel zu starten..." },
                { "win.remote_failed", "Verbindung fehlgeschlagen.\r\nHost-PC wird gesucht (Heimnetz und Internet)..." },
                { "win.host_try", "Panel wird gestartet... (Versuch {0}/30)" },
                { "win.host_starting", "Server-Panel wird gestartet..." },
                { "err.unknown", "Unbekannter Fehler." },
                { "err.cannot_start", "Das Server-Panel konnte nicht gestartet werden." },
                { "err.full_log", "Vollständiges Protokoll: {0}" },
                { "err.not_responding", "Das Panel antwortet nicht unter {0}." },
                { "err.save_config", "Die Launcher-Einstellungen konnten nicht gespeichert werden:\n{0}" },
                { "err.title", "Fehler" },
                { "err.no_app", "Datei app.pyc nicht gefunden in:\r\n{0}" },
                { "err.no_runtime", "Python-Laufzeit nicht gefunden.\r\nInstalliere das Programm neu: der Ordner 'python' der Installation fehlt." },
                { "err.python_start", "Fehler beim Starten von Python:\r\n{0}" },
                { "err.closed", "Das Panel wurde beim Start beendet (Code {0})." },
                { "err.timeout", "Das Panel hat auf Port {0} nicht innerhalb von 45 Sekunden geantwortet." },
                { "pick.run_bat_title", "Startdatei des Servers auswählen (run.bat)" },
                { "pick.folder_title", "Ordner des Minecraft-Servers auswählen (Ordner öffnen und run.bat oder eine beliebige Datei wählen)" },
                { "pick.start_files", "Startdateien" },
                { "pick.batch_files", "Batch-Dateien" },
                { "pick.jar_files", "Jar-Dateien" },
                { "pick.server_files", "Serverdateien" },
                { "pick.all_files", "Alle Dateien" },
                { "tray.client_autostarted", "Client automatisch mit Windows gestartet." },
                { "tray.host_autostarted", "Panel und Server automatisch mit Windows gestartet." },
                { "tray.remote_tooltip", "Voxarium (Remote)" },
                { "tray.host_tooltip", "Voxarium (Host-PC)" },
                { "tray.open_remote", "Panel öffnen" },
                { "tray.open_main", "Hauptfenster öffnen" },
                { "tray.reconnect", "Host-PC suchen und neu verbinden" },
                { "tray.change_address", "Adresse des Host-PCs ändern..." },
                { "prompt.address", "Adresse des Host-PCs (z. B. http://192.168.1.50:8080) oder 'auto', um ihn automatisch zu suchen:" },
                { "prompt.address_title", "Adresse des Host-PCs" },
                { "prompt.save", "Speichern" },
                { "prompt.cancel", "Abbrechen" },
                { "tray.address_saved_title", "Adresse aktualisiert" },
                { "tray.address_saved", "Neue Adresse gespeichert: {0}" },
                { "tray.autostart", "Automatisch mit Windows starten" },
                { "tray.autostart_title", "Autostart" },
                { "tray.autostart_on", "Die Anwendung startet beim Einschalten des PCs." },
                { "tray.autostart_off", "Autostart mit Windows ausgeschaltet." },
                { "tray.exit", "Beenden" },
                { "tray.exit_all", "Beenden (schließt Panel und Server)" },
                { "disc.channels", "Benachrichtigungskanäle und Internetadresse werden geprüft..." },
                { "disc.test_lan", "Heimnetz wird getestet ({0})..." },
                { "disc.scan_wifi", "Schnellsuche im Heim-WLAN (Port 8080)..." },
                { "disc.test_remote", "Internetadresse wird getestet ({0})..." },
                { "disc.search_title", "Host-PC wird gesucht" },
                { "disc.search_balloon", "Schnellsuche im Netzwerk und Internet..." },
                { "disc.searching", "Host-PC wird gesucht...\r\nHeimnetz und Internetadresse." },
                { "disc.connected_title", "Verbunden" },
                { "disc.connected", "Mit dem Host-PC verbunden:\n{0}" },
                { "disc.not_found_window", "Host-PC nicht gefunden.\r\nPrüfe, ob er eingeschaltet ist, oder wähle „Adresse des Host-PCs ändern“ im Menü des Symbols neben der Uhr." },
                { "disc.not_found_title", "Host-PC nicht gefunden" },
                { "disc.not_found", "Kein Host-PC im Netzwerk oder im Internet gefunden.\nPrüfe, ob er eingeschaltet ist und das Programm läuft." },
                { "upd.menu", "Nach Updates suchen" } } },
            { "pt", new Dictionary<string, string> {
                { "upd.title", "Atualização disponível" },
                { "upd.ask", "O Voxarium v{0} (Build {1}) está disponível; este PC tem a v{2} (Build {3}).\n\nAtualizar agora? O programa fecha durante a atualização e reabre sozinho; o endereço do PC Host continua o que foi salvo." },
                { "upd.downloading", "Baixando a atualização..." },
                { "upd.failed", "Falha na atualização: {0}" },
                { "upd.check_failed", "Falha ao procurar atualizações: {0}" },
                { "upd.up_to_date", "O programa está atualizado (v{0}, Build {1})." },
                { "win.loading", "Carregando o painel de controle..." },
                { "win.retry_button", "Tentar iniciar o painel novamente" },
                { "win.background_tip", "O aplicativo continua funcionando em segundo plano.\nClique duas vezes no ícone ao lado do relógio para reabri-lo." },
                { "win.retrying", "Tentando iniciar o painel novamente..." },
                { "win.remote_failed", "Falha na conexão.\r\nProcurando o PC Host (rede de casa e internet)..." },
                { "win.host_try", "Iniciando o painel... (tentativa {0}/30)" },
                { "win.host_starting", "Iniciando o painel do servidor..." },
                { "err.unknown", "Erro desconhecido." },
                { "err.cannot_start", "Não foi possível iniciar o painel do servidor." },
                { "err.full_log", "Registro completo: {0}" },
                { "err.not_responding", "O painel não responde em {0}." },
                { "err.save_config", "Não foi possível salvar a configuração do launcher:\n{0}" },
                { "err.title", "Erro" },
                { "err.no_app", "Arquivo app.pyc não encontrado em:\r\n{0}" },
                { "err.no_runtime", "Ambiente Python não encontrado.\r\nReinstale o programa: a pasta 'python' da instalação está faltando." },
                { "err.python_start", "Erro ao iniciar o Python:\r\n{0}" },
                { "err.closed", "O painel fechou ao iniciar (código {0})." },
                { "err.timeout", "O painel não respondeu na porta {0} em 45 segundos." },
                { "pick.run_bat_title", "Selecione o arquivo de início do servidor (run.bat)" },
                { "pick.folder_title", "Selecione a pasta do servidor Minecraft (abra a pasta e escolha run.bat ou qualquer arquivo)" },
                { "pick.start_files", "Arquivos de início" },
                { "pick.batch_files", "Arquivos batch" },
                { "pick.jar_files", "Arquivos jar" },
                { "pick.server_files", "Arquivos do servidor" },
                { "pick.all_files", "Todos os arquivos" },
                { "tray.client_autostarted", "Cliente iniciado automaticamente com o Windows." },
                { "tray.host_autostarted", "Painel e servidor iniciados automaticamente com o Windows." },
                { "tray.remote_tooltip", "Voxarium (Remoto)" },
                { "tray.host_tooltip", "Voxarium (PC Host)" },
                { "tray.open_remote", "Abrir o painel" },
                { "tray.open_main", "Abrir a janela principal" },
                { "tray.reconnect", "Procurar o PC Host e reconectar" },
                { "tray.change_address", "Alterar endereço do PC Host..." },
                { "prompt.address", "Endereço do PC Host (ex.: http://192.168.1.50:8080), ou 'auto' para procurá-lo automaticamente:" },
                { "prompt.address_title", "Endereço do PC Host" },
                { "prompt.save", "Salvar" },
                { "prompt.cancel", "Cancelar" },
                { "tray.address_saved_title", "Endereço atualizado" },
                { "tray.address_saved", "Novo endereço salvo: {0}" },
                { "tray.autostart", "Iniciar automaticamente com o Windows" },
                { "tray.autostart_title", "Inicialização automática" },
                { "tray.autostart_on", "O aplicativo será iniciado ao ligar o PC." },
                { "tray.autostart_off", "Inicialização automática com o Windows desativada." },
                { "tray.exit", "Sair" },
                { "tray.exit_all", "Sair (fecha painel e servidor)" },
                { "disc.channels", "Verificando os canais de notificação e o endereço da internet..." },
                { "disc.test_lan", "Testando a rede de casa ({0})..." },
                { "disc.scan_wifi", "Busca rápida na rede Wi-Fi de casa (porta 8080)..." },
                { "disc.test_remote", "Testando o endereço da internet ({0})..." },
                { "disc.search_title", "Procurando o PC Host" },
                { "disc.search_balloon", "Busca rápida na rede e na internet..." },
                { "disc.searching", "Procurando o PC Host...\r\nRede de casa e endereço da internet." },
                { "disc.connected_title", "Conectado" },
                { "disc.connected", "Conectado ao PC Host:\n{0}" },
                { "disc.not_found_window", "PC Host não encontrado.\r\nVerifique se está ligado ou escolha 'Alterar endereço do PC Host' no menu do ícone ao lado do relógio." },
                { "disc.not_found_title", "PC Host não encontrado" },
                { "disc.not_found", "Nenhum PC Host encontrado na rede ou na internet.\nVerifique se está ligado e se o programa está aberto." },
                { "upd.menu", "Procurar atualizações" } } }
        };

        public static IEnumerable<string> Languages { get { return Texts.Keys; } }

        public static IEnumerable<string> Keys(string lang) { return Texts[lang].Keys; }

        public static string T(string key, params object[] args)
        {
            Dictionary<string, string> d;
            string s;
            // Installazioni precedenti alla scelta della lingua: quella di Windows, se è una delle sei
            string lang = string.IsNullOrEmpty(Program.AppLanguage)
                ? System.Globalization.CultureInfo.CurrentUICulture.TwoLetterISOLanguageName : Program.AppLanguage;
            if (!Texts.TryGetValue(lang, out d) || !d.TryGetValue(key, out s))
                s = Texts["it"].TryGetValue(key, out s) ? s : key;
            return args.Length > 0 ? string.Format(s, args) : s;
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
                    RemoteUpdater.Start(trayIcon);

                    if (isAutostart)
                    {
                        if (trayIcon != null)
                            trayIcon.ShowBalloonTip(3500, "Voxarium", LauncherText.T("tray.client_autostarted"), ToolTipIcon.Info);
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
                    mainWindow.UpdateLoadingText(LauncherText.T("win.host_starting"));

                    if (isAutostart)
                    {
                        if (trayIcon != null)
                            trayIcon.ShowBalloonTip(3500, "Voxarium", LauncherText.T("tray.host_autostarted"), ToolTipIcon.Info);
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
                MessageBox.Show(LauncherText.T("err.save_config", ex.Message), LauncherText.T("err.title"), MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
        }

        static void SetupRemoteTray()
        {
            trayIcon = new NotifyIcon();
            trayIcon.Text = LauncherText.T("tray.remote_tooltip");
            trayIcon.Icon = appIcon != null ? appIcon : SystemIcons.Application;
            trayIcon.Visible = true;

            ContextMenu menu = new ContextMenu();
            menu.MenuItems.Add(LauncherText.T("tray.open_remote"), (s, e) => {
                if (mainWindow != null) mainWindow.ShowAndActivate();
            });
            menu.MenuItems.Add(LauncherText.T("tray.reconnect"), (s, e) => {
                TriggerAutoReconnectAsync(true);
            });
            menu.MenuItems.Add(LauncherText.T("tray.change_address"), (s, e) => {
                string newUrl = PromptInput(LauncherText.T("prompt.address"), LauncherText.T("prompt.address_title"), remoteUrl);
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
                        trayIcon.ShowBalloonTip(3000, LauncherText.T("tray.address_saved_title"), LauncherText.T("tray.address_saved", remoteUrl), ToolTipIcon.Info);
                        if (mainWindow != null) mainWindow.NavigateTo(remoteUrl);
                    }
                }
            });
            menu.MenuItems.Add("-");
            MenuItem itemAutoRemote = new MenuItem(LauncherText.T("tray.autostart"));
            itemAutoRemote.Checked = IsAutoStartEnabled();
            itemAutoRemote.Click += (s, e) => {
                bool newState = !itemAutoRemote.Checked;
                SetAutoStart(newState);
                itemAutoRemote.Checked = IsAutoStartEnabled();
                if (itemAutoRemote.Checked)
                    trayIcon.ShowBalloonTip(3000, LauncherText.T("tray.autostart_title"), LauncherText.T("tray.autostart_on"), ToolTipIcon.Info);
                else
                    trayIcon.ShowBalloonTip(3000, LauncherText.T("tray.autostart_title"), LauncherText.T("tray.autostart_off"), ToolTipIcon.Info);
            };
            menu.MenuItems.Add(itemAutoRemote);
            menu.MenuItems.Add(LauncherText.T("upd.menu"), (s, e) => RemoteUpdater.CheckFromMenu());
            menu.MenuItems.Add("-");
            menu.MenuItems.Add(LauncherText.T("tray.exit"), (s, e) => {
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
            trayIcon.Text = LauncherText.T("tray.host_tooltip");
            trayIcon.Icon = appIcon != null ? appIcon : SystemIcons.Application;
            trayIcon.Visible = true;

            ContextMenu menu = new ContextMenu();
            menu.MenuItems.Add(LauncherText.T("tray.open_main"), (s, e) => {
                if (mainWindow != null) mainWindow.ShowAndActivate();
            });
            menu.MenuItems.Add("-");
            MenuItem itemAutoLocal = new MenuItem(LauncherText.T("tray.autostart"));
            itemAutoLocal.Checked = IsAutoStartEnabled();
            itemAutoLocal.Click += (s, e) => {
                bool newState = !itemAutoLocal.Checked;
                SetAutoStart(newState);
                itemAutoLocal.Checked = IsAutoStartEnabled();
                if (itemAutoLocal.Checked)
                    trayIcon.ShowBalloonTip(3000, LauncherText.T("tray.autostart_title"), LauncherText.T("tray.autostart_on"), ToolTipIcon.Info);
                else
                    trayIcon.ShowBalloonTip(3000, LauncherText.T("tray.autostart_title"), LauncherText.T("tray.autostart_off"), ToolTipIcon.Info);
            };
            menu.MenuItems.Add(itemAutoLocal);
            menu.MenuItems.Add("-");
            menu.MenuItems.Add(LauncherText.T("tray.exit_all"), (s, e) => {
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
                BackendError = LauncherText.T("err.no_app", appDir);
                return false;
            }

            string pyExe = FindPythonExe();
            if (pyExe == null)
            {
                BackendError = LauncherText.T("err.no_runtime");
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
                BackendError = LauncherText.T("err.python_start", ex.Message);
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
                    BackendError = LauncherText.T("err.closed", pythonProcess.ExitCode) + "\r\n\r\n" + GetBackendLogTail(12);
                    return false;
                }
                Thread.Sleep(200);
            }
            BackendError = LauncherText.T("err.timeout", webPort) + "\r\n\r\n" + GetBackendLogTail(12);
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

                buttonOk.Text = LauncherText.T("prompt.save");
                buttonCancel.Text = LauncherText.T("prompt.cancel");
                buttonOk.DialogResult = DialogResult.OK;
                buttonCancel.DialogResult = DialogResult.Cancel;

                label.SetBounds(16, 14, 380, 44);
                textBox.SetBounds(16, 62, 380, 23);
                buttonOk.SetBounds(216, 98, 85, 28);
                buttonCancel.SetBounds(311, 98, 85, 28);

                form.ClientSize = new Size(415, 140);
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
            if (statusUpdate != null) statusUpdate(LauncherText.T("disc.channels"));

            // 1. Query ntfy for candidates: solo sul topic del proprio Host (imparato in rete locale), mai su un topic fisso
            List<string> ntfyCandidates = string.IsNullOrEmpty(hostPushTopic) ? new List<string>() : QueryNtfyCandidates(hostPushTopic);

            // First, test LAN candidates from ntfy (super fast if at home)
            foreach (string cand in ntfyCandidates)
            {
                if (cand.StartsWith("http://", StringComparison.OrdinalIgnoreCase))
                {
                    if (statusUpdate != null) statusUpdate(LauncherText.T("disc.test_lan", cand));
                    if (VerifyKnownHost(cand)) { LearnHostIdentity(cand); return cand; }
                }
            }

            // 2. Fast local subnet scan (in case host changed local IP)
            if (statusUpdate != null) statusUpdate(LauncherText.T("disc.scan_wifi"));
            string lanFound = ScanLocalSubnetForServer(8080);
            if (!string.IsNullOrEmpty(lanFound)) { LearnHostIdentity(lanFound); return lanFound; }

            // 3. Test remote candidates from ntfy (for outside connection, HTTPS con impronta del certificato)
            foreach (string cand in ntfyCandidates)
            {
                if (cand.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
                {
                    if (statusUpdate != null) statusUpdate(LauncherText.T("disc.test_remote", cand));
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
                        trayIcon.ShowBalloonTip(2000, LauncherText.T("disc.search_title"), LauncherText.T("disc.search_balloon"), ToolTipIcon.Info);
                    }

                    if (mainWindow != null)
                    {
                        mainWindow.UpdateLoadingText(LauncherText.T("disc.searching"));
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
                            trayIcon.ShowBalloonTip(3500, LauncherText.T("disc.connected_title"), LauncherText.T("disc.connected", found), ToolTipIcon.Info);
                        }
                    }
                    else
                    {
                        if (mainWindow != null)
                        {
                            mainWindow.UpdateLoadingText(LauncherText.T("disc.not_found_window"));
                        }
                        if (userInitiated && trayIcon != null)
                        {
                            trayIcon.ShowBalloonTip(3500, LauncherText.T("disc.not_found_title"), LauncherText.T("disc.not_found"), ToolTipIcon.Warning);
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
