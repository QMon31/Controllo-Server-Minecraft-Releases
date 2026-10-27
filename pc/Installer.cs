using System;
using System.IO;
using System.Drawing;
using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using System.Threading;
using System.Threading.Tasks;
using System.ComponentModel;
using System.Windows.Forms;
using System.Reflection;
using System.IO.Compression;
using System.Collections.Generic;
using System.Text.RegularExpressions;
using Microsoft.Win32;
using System.Management;
using System.Runtime.InteropServices;

// Nome mostrato da Windows (Proprietà > Dettagli, richiesta UAC); versione e produttore: build_info.cs, generato da build_deploy.py
[assembly: AssemblyTitle("Installazione Voxarium")]
[assembly: AssemblyDescription("Installa o aggiorna il pannello di controllo per il server Minecraft")]

namespace MinecraftControlCenterInstaller
{
    static class Program
    {
        public static bool IsUpdateMode = false;
        // --silent: installazione senza domande (winget, script). Con Voxarium già installato vale come aggiornamento
        // (scelte conservate); su un PC nuovo usa le opzioni predefinite, non chiede l'amministratore e non apre il programma.
        public static bool IsSilentMode = false;

        [STAThread]
        static int Main(string[] args)
        {
            if (args != null)
            {
                for (int i = 0; i < args.Length; i++)
                {
                    if (string.Equals(args[i], "--update", StringComparison.OrdinalIgnoreCase))
                    {
                        IsUpdateMode = true;
                    }
                    if (string.Equals(args[i], "--silent", StringComparison.OrdinalIgnoreCase) ||
                        string.Equals(args[i], "/S", StringComparison.OrdinalIgnoreCase))
                    {
                        IsSilentMode = true;
                    }
                    if (string.Equals(args[i], FirewallRules.Argument, StringComparison.OrdinalIgnoreCase))
                    {
                        // Seconda istanza avviata con i permessi di amministratore solo per le regole del firewall
                        return FirewallRules.Apply() ? 0 : 1;
                    }
                }
            }

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            // Chiude finestra e backend di questo programma (solo i suoi processi) per evitare file bloccati
            InstallerForm.AutoCloseRunningApp();

            Application.Run(new InstallerForm());
            return 0;
        }
    }

    /// <summary>
    /// Regole del firewall per l'accesso dal telefono, create con l'interfaccia COM del Firewall di Windows
    /// (HNetCfg.FwPolicy2) dall'installer stesso avviato come amministratore: niente cmd.exe/netsh nascosti.
    /// </summary>
    static class FirewallRules
    {
        public const string Argument = "--firewall-rules";
        const int ProtocolTcp = 6, DirectionIn = 1, ActionAllow = 1;
        const int ProfilePrivate = 2, ProfileAll = 0x7FFFFFFF;

        public static bool Apply()
        {
            try
            {
                dynamic policy = Activator.CreateInstance(Type.GetTypeFromProgID("HNetCfg.FwPolicy2"));
                // 8080: pannello in HTTP, solo reti private. 8443: HTTPS da internet (porta aperta con UPnP), ogni profilo:
                // Windows classifica spesso come "Pubblica" anche la rete di casa
                AddRule(policy, "Controllo Server Minecraft (8080)", "8080", ProfilePrivate,
                        "Pannello Controllo Server Minecraft dalla rete di casa (HTTP)");
                AddRule(policy, "Controllo Server Minecraft (8443)", "8443", ProfileAll,
                        "Pannello Controllo Server Minecraft da internet (HTTPS con certificato del PC)");
                return true;
            }
            catch
            {
                return false;
            }
        }

        /// <summary>True se le due regole ci sono già (anche disattivate dall'utente): la lettura non richiede l'amministratore.</summary>
        public static bool AlreadyPresent()
        {
            try
            {
                dynamic policy = Activator.CreateInstance(Type.GetTypeFromProgID("HNetCfg.FwPolicy2"));
                foreach (string name in new string[] { "Controllo Server Minecraft (8080)", "Controllo Server Minecraft (8443)" })
                {
                    try { policy.Rules.Item(name); } catch { return false; }
                }
                return true;
            }
            catch
            {
                return false;
            }
        }

        static void AddRule(dynamic policy, string name, string port, int profiles, string description)
        {
            for (int i = 0; i < 5; i++)   // toglie eventuali copie della regola lasciate da installazioni precedenti
            {
                try { policy.Rules.Item(name); } catch { break; }
                policy.Rules.Remove(name);
            }
            dynamic rule = Activator.CreateInstance(Type.GetTypeFromProgID("HNetCfg.FWRule"));
            rule.Name = name;
            rule.Description = description;
            rule.Protocol = ProtocolTcp;
            rule.LocalPorts = port;
            rule.Direction = DirectionIn;
            rule.Action = ActionAllow;
            rule.Profiles = profiles;
            rule.Enabled = true;
            policy.Rules.Add(rule);
        }

        /// <summary>Riavvia questo installer come amministratore (una richiesta UAC) per creare le regole.</summary>
        public static void RequestElevated()
        {
            try
            {
                ProcessStartInfo psi = new ProcessStartInfo(Application.ExecutablePath, Argument);
                psi.UseShellExecute = true;   // necessario per "runas"
                psi.Verb = "runas";
                using (Process p = Process.Start(psi))
                {
                    if (p != null) p.WaitForExit(30000);
                }
            }
            catch
            {
                // UAC rifiutato: Windows chiederà il permesso del firewall al primo avvio del pannello
            }
        }
    }

    public class InstallerForm : Form
    {
        private Panel headerPanel;
        private PictureBox picIcon;
        private Label lblHeaderTitle;
        private Label lblHeaderSub;

        private Panel footerPanel;
        private Button btnBack;
        private Button btnNext;
        private Button btnCancel;

        private Panel contentPanel;

        // Step 0: Welcome & Security Diagnostics
        private Panel pnlStep0;
        private Label lblWelcomeDesc;
        private Label lblPyStatus;
        private Label lblReqStatus;
        private Label lblSecurityStatus;

        // Step 1: Mode Selection (Interactive Cards)
        private Panel pnlStep1;
        private Label lblModeTitle;
        private Panel cardHost;
        private RadioButton rbModeHost;
        private Label lblCardHostTitle;
        private Label lblCardHostBadge;
        private Label lblCardHostDesc;

        private Panel cardRemote;
        private RadioButton rbModeRemote;
        private Label lblCardRemoteTitle;
        private Label lblCardRemoteBadge;
        private Label lblCardRemoteDesc;

        private Panel pnlModeDetails;
        private Label lblHostSecurityInfo;

        private Panel pnlRemoteConfig;
        private Label lblRemoteUrlTitle;
        private TextBox txtRemoteUrl;
        private Button btnAutoDetect;
        private Button btnTestRemote;
        private Label lblTestResult;

        // Step 2: Directory Selection
        private Panel pnlStep2;
        private Label lblDestDesc;
        private TextBox txtDestPath;
        private Button btnBrowse;
        private Label lblSpaceInfo;

        // Step 3: Options & Security
        private Panel pnlStep3;
        private Label lblOptDesc;
        private CheckBox chkDesktop;
        private CheckBox chkStartMenu;
        private CheckBox chkAutoStart;
        private CheckBox chkRegisterUninstall;

        // Step 4: Progress
        private Panel pnlStep4;
        private ComboBox cmbLanguage;
        private Label lblProgressTitle;
        private ProgressBar prgBar;
        private Label lblProgressDetail;

        // Step 5: Finished
        private Panel pnlStep5;
        private Label lblFinishedTitle;
        private Label lblFinishedDesc;
        private Label lblFinishedRole;
        private CheckBox chkLaunchNow;

        private int currentStep = 0;
        private string defaultHostDir;
        private string defaultRemoteDir;

        public InstallerForm()
        {
            this.Text = "Installazione - Voxarium (Desktop Edition)";
            this.ClientSize = new Size(620, 520);
            this.FormBorderStyle = FormBorderStyle.FixedDialog;
            this.MaximizeBox = false;
            this.StartPosition = FormStartPosition.CenterScreen;
            this.BackColor = Color.FromArgb(248, 250, 252);
            this.Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point);

            try
            {
                this.Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
            }
            catch { }

            string localApp = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            defaultHostDir = Path.Combine(localApp, @"Programs\Controllo Server Minecraft");
            defaultRemoteDir = Path.Combine(localApp, @"Programs\Controllo Server Minecraft Client");

            I18n.Lang = I18n.Detect(Path.Combine(defaultHostDir, "launcher_config.json"),
                                    Path.Combine(defaultRemoteDir, "launcher_config.json"));
            InitComponents();
            I18n.Attach(this);

            // Reinstallazione o aggiornamento di un PC Remoto: si riparte dall'indirizzo dell'Host già salvato, non da "auto"
            try
            {
                string cfgRemote = Path.Combine(defaultRemoteDir, "launcher_config.json");
                string savedUrl = File.Exists(cfgRemote) ? ReadConfigString(File.ReadAllText(cfgRemote), "remote_url") : "";
                if (!string.IsNullOrEmpty(savedUrl)) txtRemoteUrl.Text = savedUrl;
            }
            catch { }

            if (Program.IsSilentMode)
            {
                if (File.Exists(Path.Combine(defaultHostDir, "launcher_config.json")) ||
                    File.Exists(Path.Combine(defaultRemoteDir, "launcher_config.json")))
                {
                    Program.IsUpdateMode = true;   // già installato: come un aggiornamento automatico
                }
                else
                {
                    // PC nuovo: PC Host nella cartella predefinita, opzioni predefinite
                    this.Shown += new EventHandler(delegate(object s, EventArgs e) {
                        rbModeHost.Checked = true;
                        txtDestPath.Text = defaultHostDir;
                        ShowStep(4);
                    });
                }
            }

            if (Program.IsUpdateMode)
            {
                this.Shown += new EventHandler(delegate(object s, EventArgs e) {
                    string cfgHost = Path.Combine(defaultHostDir, "launcher_config.json");
                    string cfgRemote = Path.Combine(defaultRemoteDir, "launcher_config.json");
                    if (File.Exists(cfgHost))
                    {
                        rbModeHost.Checked = true;
                        txtDestPath.Text = defaultHostDir;
                    }
                    else if (File.Exists(cfgRemote))
                    {
                        rbModeRemote.Checked = true;
                        txtDestPath.Text = defaultRemoteDir;
                    }
                    ShowStep(4);
                });
            }

            ShowStep(0);
        }

        private void InitComponents()
        {
            int clientW = this.ClientSize.Width;
            int clientH = this.ClientSize.Height;

            // =========================================================
            // 1. HEADER PANEL (Fixed top area, 75px height)
            // =========================================================
            headerPanel = new Panel();
            headerPanel.Location = new Point(0, 0);
            headerPanel.Size = new Size(clientW, 75);
            headerPanel.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
            headerPanel.BackColor = Color.FromArgb(15, 23, 42); // Deep Slate Navy
            headerPanel.Paint += (s, e) => {
                using (Pen p = new Pen(Color.FromArgb(16, 185, 129), 2)) // Emerald Accent
                {
                    e.Graphics.DrawLine(p, 0, headerPanel.Height - 1, headerPanel.Width, headerPanel.Height - 1);
                }
            };

            picIcon = new PictureBox();
            picIcon.Size = new Size(52, 52);
            picIcon.Location = new Point(clientW - 68, 11);
            picIcon.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            picIcon.SizeMode = PictureBoxSizeMode.Zoom;
            picIcon.BackColor = Color.Transparent;
            picIcon.Image = LoadMinecraftIcon();
            headerPanel.Controls.Add(picIcon);

            lblHeaderTitle = new Label();
            lblHeaderTitle.Text = "Installazione di Voxarium";
            lblHeaderTitle.Font = new Font("Segoe UI", 12F, FontStyle.Bold);
            lblHeaderTitle.ForeColor = Color.White;
            lblHeaderTitle.Location = new Point(20, 14);
            lblHeaderTitle.AutoSize = true;
            headerPanel.Controls.Add(lblHeaderTitle);

            lblHeaderSub = new Label();
            lblHeaderSub.Text = "Procedura guidata • Protezione Host & Rete • Scelta Ruolo";
            lblHeaderSub.Font = new Font("Segoe UI", 8.5F, FontStyle.Regular);
            lblHeaderSub.ForeColor = Color.FromArgb(148, 163, 184);
            lblHeaderSub.Location = new Point(22, 42);
            lblHeaderSub.AutoSize = true;
            lblHeaderSub.MaximumSize = new Size(clientW - 230, 0);
            headerPanel.Controls.Add(lblHeaderSub);

            // Lingua dell'installazione (e del programma installato, salvata in launcher_config.json)
            Label lblLanguage = new Label();
            lblLanguage.Text = "Lingua";
            lblLanguage.Font = new Font("Segoe UI", 8F, FontStyle.Regular);
            lblLanguage.ForeColor = Color.FromArgb(148, 163, 184);
            lblLanguage.Location = new Point(clientW - 196, 12);
            lblLanguage.AutoSize = true;
            lblLanguage.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            headerPanel.Controls.Add(lblLanguage);

            cmbLanguage = new ComboBox();
            cmbLanguage.DropDownStyle = ComboBoxStyle.DropDownList;
            cmbLanguage.Items.AddRange(I18n.Names);
            cmbLanguage.SelectedIndex = Math.Max(0, I18n.IndexOf(I18n.Lang));
            cmbLanguage.Size = new Size(118, 23);
            cmbLanguage.Location = new Point(clientW - 196, 31);
            cmbLanguage.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            cmbLanguage.SelectedIndexChanged += (s, e) => { I18n.SetLanguage(I18n.Codes[cmbLanguage.SelectedIndex]); };
            headerPanel.Controls.Add(cmbLanguage);

            this.Controls.Add(headerPanel);

            // =========================================================
            // 2. FOOTER PANEL (Fixed bottom area, 55px height)
            // =========================================================
            footerPanel = new Panel();
            footerPanel.Location = new Point(0, clientH - 55);
            footerPanel.Size = new Size(clientW, 55);
            footerPanel.Anchor = AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right;
            footerPanel.BackColor = Color.FromArgb(241, 245, 249);
            footerPanel.Paint += (s, e) => {
                using (Pen p = new Pen(Color.FromArgb(226, 232, 240), 1))
                {
                    e.Graphics.DrawLine(p, 0, 0, footerPanel.Width, 0);
                }
            };

            btnCancel = new Button();
            btnCancel.Text = "Annulla";
            btnCancel.Size = new Size(86, 30);
            btnCancel.Location = new Point(516, 12);
            btnCancel.Click += (s, e) => { this.Close(); };
            footerPanel.Controls.Add(btnCancel);

            btnNext = new Button();
            btnNext.Text = "Avanti >";
            btnNext.Size = new Size(88, 30);
            btnNext.Location = new Point(420, 12);
            btnNext.BackColor = Color.FromArgb(16, 185, 129);
            btnNext.ForeColor = Color.White;
            btnNext.FlatStyle = FlatStyle.Flat;
            btnNext.FlatAppearance.BorderSize = 0;
            btnNext.Font = new Font("Segoe UI", 9F, FontStyle.Bold);
            btnNext.Click += (s, e) => { OnNextClicked(); };
            footerPanel.Controls.Add(btnNext);

            btnBack = new Button();
            btnBack.Text = "< Indietro";
            btnBack.Size = new Size(86, 30);
            btnBack.Location = new Point(326, 12);
            btnBack.Click += (s, e) => { OnBackClicked(); };
            footerPanel.Controls.Add(btnBack);

            this.Controls.Add(footerPanel);

            // =========================================================
            // 3. CONTENT PANEL (Explicit layout: Y=75 to Y=clientH-55)
            // =========================================================
            contentPanel = new Panel();
            contentPanel.Location = new Point(0, 75);
            contentPanel.Size = new Size(clientW, clientH - 75 - 55);
            contentPanel.Anchor = AnchorStyles.Top | AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right;
            contentPanel.Padding = new Padding(24, 12, 24, 12);
            this.Controls.Add(contentPanel);

            // Build all steps inside contentPanel
            BuildStep0();
            BuildStep1();
            BuildStep2();
            BuildStep3();
            BuildStep4();
            BuildStep5();
        }

        private Image LoadMinecraftIcon()
        {
            try
            {
                Assembly asm = Assembly.GetExecutingAssembly();
                using (Stream s = asm.GetManifestResourceStream("minecraft_block.png"))
                {
                    if (s != null)
                    {
                        using (MemoryStream ms = new MemoryStream())
                        {
                            s.CopyTo(ms);
                            ms.Position = 0;
                            using (Image img = Image.FromStream(ms))
                            {
                                return new Bitmap(img);
                            }
                        }
                    }
                }
                if (File.Exists("minecraft_block.png"))
                {
                    using (Image img = Image.FromFile("minecraft_block.png"))
                    {
                        return new Bitmap(img);
                    }
                }
                using (Stream s = asm.GetManifestResourceStream("package.zip"))
                {
                    if (s != null)
                    {
                        using (ZipArchive za = new ZipArchive(s, ZipArchiveMode.Read))
                        {
                            ZipArchiveEntry zePng = za.GetEntry("minecraft_block.png");
                            if (zePng != null)
                            {
                                using (Stream es = zePng.Open())
                                using (MemoryStream ms = new MemoryStream())
                                {
                                    es.CopyTo(ms);
                                    ms.Position = 0;
                                    using (Image img = Image.FromStream(ms))
                                    {
                                        return new Bitmap(img);
                                    }
                                }
                            }
                            ZipArchiveEntry ze = za.GetEntry("minecraft.ico");
                            if (ze != null)
                            {
                                using (Stream es = ze.Open())
                                using (MemoryStream ms = new MemoryStream())
                                {
                                    es.CopyTo(ms);
                                    ms.Position = 0;
                                    using (Icon ico = new Icon(ms, 128, 128))
                                    {
                                        return ico.ToBitmap();
                                    }
                                }
                            }
                        }
                    }
                }
                if (File.Exists("minecraft.ico"))
                {
                    using (Icon ico = new Icon("minecraft.ico", 128, 128))
                    {
                        return ico.ToBitmap();
                    }
                }
            }
            catch { }
            try
            {
                if (this.Icon != null) return this.Icon.ToBitmap();
            }
            catch { }
            return SystemIcons.Application.ToBitmap();
        }

        // STEP 0: Welcome & Security Diagnostics
        private void BuildStep0()
        {
            pnlStep0 = new Panel();
            pnlStep0.Dock = DockStyle.Fill;

            lblWelcomeDesc = new Label();
            lblWelcomeDesc.Text = "Benvenuto nella procedura guidata di Voxarium.\r\n\r\n" +
                                 "Questa applicazione ti consente di avviare, monitorare e gestire il tuo server sia in locale su questo PC, " +
                                 "sia in remoto da un altro computer o smartphone con protezioni di rete attive.";
            lblWelcomeDesc.Location = new Point(12, 10);
            lblWelcomeDesc.Size = new Size(540, 75);
            pnlStep0.Controls.Add(lblWelcomeDesc);

            GroupBox gbReq = new GroupBox();
            gbReq.Text = "Diagnostica di Sistema e Sicurezza";
            gbReq.Location = new Point(12, 95);
            gbReq.Size = new Size(540, 185);

            lblSecurityStatus = new Label();
            lblSecurityStatus.Text = "[✓] Protezione Host e Rete: Sanitizzazione comandi, protezione directory di Windows e blocco accessi esterni attivi.";
            lblSecurityStatus.ForeColor = Color.FromArgb(22, 101, 52);
            lblSecurityStatus.Location = new Point(16, 26);
            gbReq.Controls.Add(lblSecurityStatus);

            lblPyStatus = new Label();
            gbReq.Controls.Add(lblPyStatus);

            lblReqStatus = new Label();
            gbReq.Controls.Add(lblReqStatus);

            // Ogni riga alta quanto il suo testo (che cambia con la lingua e con l'esito dei controlli), la successiva subito sotto
            Label[] reqLines = { lblSecurityStatus, lblPyStatus, lblReqStatus };
            foreach (Label l in reqLines)
            {
                l.Left = 16;
                l.AutoSize = true;
                l.MaximumSize = new Size(505, 0);
                l.SizeChanged += delegate(object s, EventArgs e) {
                    for (int i = 1; i < reqLines.Length; i++) reqLines[i].Top = reqLines[i - 1].Bottom + 10;
                };
            }

            pnlStep0.Controls.Add(gbReq);

            contentPanel.Controls.Add(pnlStep0);
        }

        // STEP 1: Interactive Mode Selection Cards
        private void BuildStep1()
        {
            pnlStep1 = new Panel();
            pnlStep1.Dock = DockStyle.Fill;

            lblModeTitle = new Label();
            lblModeTitle.Text = "Seleziona dove si trova il tuo server Minecraft:";
            lblModeTitle.Font = new Font("Segoe UI", 10F, FontStyle.Bold);
            lblModeTitle.Location = new Point(12, 8);
            lblModeTitle.AutoSize = true;
            pnlStep1.Controls.Add(lblModeTitle);

            // ==========================================
            // CARD 1: SERVER HOST LOCALE
            // ==========================================
            cardHost = new Panel();
            cardHost.Location = new Point(12, 36);
            cardHost.Size = new Size(540, 88);
            cardHost.BackColor = Color.FromArgb(240, 253, 244); // Light emerald
            cardHost.BorderStyle = BorderStyle.FixedSingle;
            cardHost.Cursor = Cursors.Hand;

            rbModeHost = new RadioButton();
            rbModeHost.Text = "";
            rbModeHost.Location = new Point(12, 14);
            rbModeHost.Size = new Size(20, 20);
            rbModeHost.Checked = true;
            cardHost.Controls.Add(rbModeHost);

            lblCardHostTitle = new Label();
            lblCardHostTitle.Text = "Server Host Locale";
            lblCardHostTitle.Font = new Font("Segoe UI", 10.5F, FontStyle.Bold);
            lblCardHostTitle.ForeColor = Color.FromArgb(6, 78, 59);
            lblCardHostTitle.Location = new Point(36, 10);
            lblCardHostTitle.AutoSize = true;
            lblCardHostTitle.Cursor = Cursors.Hand;
            cardHost.Controls.Add(lblCardHostTitle);

            lblCardHostBadge = new Label();
            lblCardHostBadge.Text = "[ QUESTO COMPUTER ]";
            lblCardHostBadge.Font = new Font("Segoe UI", 8F, FontStyle.Bold);
            lblCardHostBadge.ForeColor = Color.FromArgb(16, 185, 129);
            lblCardHostBadge.Location = new Point(190, 13);
            lblCardHostBadge.AutoSize = true;
            lblCardHostBadge.Cursor = Cursors.Hand;
            cardHost.Controls.Add(lblCardHostBadge);
            lblCardHostTitle.SizeChanged += delegate(object s, EventArgs e) { lblCardHostBadge.Left = lblCardHostTitle.Right + 10; };
            lblCardHostBadge.Left = lblCardHostTitle.Right + 10;

            lblCardHostDesc = new Label();
            lblCardHostDesc.Text = "I file del server Minecraft e Java sono su questo computer.\r\n" +
                                   "Installa: gestione completa, rilevamento crash, monitoraggio RAM/CPU e backup.";
            lblCardHostDesc.ForeColor = Color.FromArgb(51, 65, 85);
            lblCardHostDesc.Location = new Point(36, 36);
            lblCardHostDesc.Size = new Size(490, 44);
            lblCardHostDesc.Cursor = Cursors.Hand;
            cardHost.Controls.Add(lblCardHostDesc);

            // Attach click handler to all elements of Card 1
            EventHandler selectHost = (s, e) => { SelectMode(true); };
            cardHost.Click += selectHost;
            rbModeHost.Click += selectHost;
            lblCardHostTitle.Click += selectHost;
            lblCardHostBadge.Click += selectHost;
            lblCardHostDesc.Click += selectHost;

            pnlStep1.Controls.Add(cardHost);

            // ==========================================
            // CARD 2: CONTROLLO REMOTO (CLIENT)
            // ==========================================
            cardRemote = new Panel();
            cardRemote.Location = new Point(12, 132);
            cardRemote.Size = new Size(540, 88);
            cardRemote.BackColor = Color.FromArgb(248, 250, 252); // Muted slate
            cardRemote.BorderStyle = BorderStyle.FixedSingle;
            cardRemote.Cursor = Cursors.Hand;

            rbModeRemote = new RadioButton();
            rbModeRemote.Text = "";
            rbModeRemote.Location = new Point(12, 14);
            rbModeRemote.Size = new Size(20, 20);
            rbModeRemote.Checked = false;
            cardRemote.Controls.Add(rbModeRemote);

            lblCardRemoteTitle = new Label();
            lblCardRemoteTitle.Text = "Controllo Remoto (PC Secondario)";
            lblCardRemoteTitle.Font = new Font("Segoe UI", 10.5F, FontStyle.Bold);
            lblCardRemoteTitle.ForeColor = Color.FromArgb(15, 23, 42);
            lblCardRemoteTitle.Location = new Point(36, 10);
            lblCardRemoteTitle.AutoSize = true;
            lblCardRemoteTitle.Cursor = Cursors.Hand;
            cardRemote.Controls.Add(lblCardRemoteTitle);

            lblCardRemoteBadge = new Label();
            lblCardRemoteBadge.Text = "[ SERVER SU UN ALTRO PC ]";
            lblCardRemoteBadge.Font = new Font("Segoe UI", 8F, FontStyle.Bold);
            lblCardRemoteBadge.ForeColor = Color.FromArgb(59, 130, 246);
            lblCardRemoteBadge.Location = new Point(280, 13);
            lblCardRemoteBadge.AutoSize = true;
            lblCardRemoteBadge.Cursor = Cursors.Hand;
            cardRemote.Controls.Add(lblCardRemoteBadge);
            lblCardRemoteTitle.SizeChanged += delegate(object s, EventArgs e) { lblCardRemoteBadge.Left = lblCardRemoteTitle.Right + 10; };
            lblCardRemoteBadge.Left = lblCardRemoteTitle.Right + 10;

            lblCardRemoteDesc = new Label();
            lblCardRemoteDesc.Text = "Il server Minecraft gira su un ALTRO computer (PC secondario o mini-PC).\r\n" +
                                     "Installa il client leggero sul PC principale: Java non usa RAM o CPU su questo PC.";
            lblCardRemoteDesc.ForeColor = Color.FromArgb(71, 85, 105);
            lblCardRemoteDesc.Location = new Point(36, 36);
            lblCardRemoteDesc.Size = new Size(490, 44);
            lblCardRemoteDesc.Cursor = Cursors.Hand;
            cardRemote.Controls.Add(lblCardRemoteDesc);

            // Attach click handler to all elements of Card 2
            EventHandler selectRemote = (s, e) => { SelectMode(false); };
            cardRemote.Click += selectRemote;
            rbModeRemote.Click += selectRemote;
            lblCardRemoteTitle.Click += selectRemote;
            lblCardRemoteBadge.Click += selectRemote;
            lblCardRemoteDesc.Click += selectRemote;

            pnlStep1.Controls.Add(cardRemote);

            // ==========================================
            // DETAILS CONTAINER (SUBPANEL BELOW CARDS)
            // ==========================================
            pnlModeDetails = new Panel();
            pnlModeDetails.Location = new Point(12, 228);
            pnlModeDetails.Size = new Size(540, 86);
            pnlModeDetails.BackColor = Color.FromArgb(241, 245, 249);
            pnlModeDetails.BorderStyle = BorderStyle.FixedSingle;

            // Host Security Info Box
            lblHostSecurityInfo = new Label();
            lblHostSecurityInfo.Text = "[✓] Protezione Host Attiva: Questo computer ospiterà il server in modo isolato.\r\n" +
                                       "• Riconoscimento automatico della cartella del server e porta Minecraft.\r\n" +
                                       "• Comandi e percorsi file protetti e sanitizzati contro injection e traversal.\r\n" +
                                       "• Monitoraggio automatico dello stato del server e gestione crash integrata.";
            lblHostSecurityInfo.ForeColor = Color.FromArgb(30, 41, 59);
            lblHostSecurityInfo.Location = new Point(10, 8);
            lblHostSecurityInfo.Size = new Size(518, 68);
            pnlModeDetails.Controls.Add(lblHostSecurityInfo);

            // Remote Configuration Box
            pnlRemoteConfig = new Panel();
            pnlRemoteConfig.Dock = DockStyle.Fill;
            pnlRemoteConfig.Visible = false;

            lblRemoteUrlTitle = new Label();
            lblRemoteUrlTitle.Text = "Indirizzo Server Minecraft Host (Automatico o IP Manuale):";
            lblRemoteUrlTitle.Font = new Font("Segoe UI", 8.5F, FontStyle.Bold);
            lblRemoteUrlTitle.Location = new Point(10, 6);
            lblRemoteUrlTitle.AutoSize = true;
            pnlRemoteConfig.Controls.Add(lblRemoteUrlTitle);

            txtRemoteUrl = new TextBox();
            txtRemoteUrl.Text = "auto";
            txtRemoteUrl.Location = new Point(12, 26);
            txtRemoteUrl.Size = new Size(250, 23);
            pnlRemoteConfig.Controls.Add(txtRemoteUrl);

            btnAutoDetect = new Button();
            btnAutoDetect.Text = "Rileva Subito";
            btnAutoDetect.Location = new Point(268, 24);
            btnAutoDetect.Size = new Size(110, 26);
            btnAutoDetect.BackColor = Color.FromArgb(16, 185, 129);
            btnAutoDetect.ForeColor = Color.White;
            btnAutoDetect.FlatStyle = FlatStyle.Flat;
            btnAutoDetect.FlatAppearance.BorderSize = 0;
            btnAutoDetect.Font = new Font("Segoe UI", 8.5F, FontStyle.Bold);
            btnAutoDetect.Click += (s, e) => { StartInstallerAutoDiscovery(); };
            pnlRemoteConfig.Controls.Add(btnAutoDetect);

            btnTestRemote = new Button();
            btnTestRemote.Text = "Test";
            btnTestRemote.Location = new Point(384, 24);
            btnTestRemote.Size = new Size(70, 26);
            btnTestRemote.Click += (s, e) => { TestRemoteConnection(txtRemoteUrl.Text); };
            pnlRemoteConfig.Controls.Add(btnTestRemote);

            lblTestResult = new Label();
            lblTestResult.Text = "Lascia 'auto' per connetterti automaticamente all'avvio (scansione LAN & Cloud).";
            lblTestResult.ForeColor = Color.FromArgb(100, 116, 139);
            lblTestResult.Location = new Point(12, 54);
            lblTestResult.Size = new Size(510, 25);
            pnlRemoteConfig.Controls.Add(lblTestResult);

            pnlModeDetails.Controls.Add(pnlRemoteConfig);
            pnlStep1.Controls.Add(pnlModeDetails);

            contentPanel.Controls.Add(pnlStep1);
        }

        private void SelectMode(bool isHost)
        {
            rbModeHost.Checked = isHost;
            rbModeRemote.Checked = !isHost;

            if (isHost)
            {
                // Highlight Host Card
                cardHost.BackColor = Color.FromArgb(240, 253, 244); // Light emerald
                lblCardHostTitle.ForeColor = Color.FromArgb(6, 78, 59);

                // Mute Remote Card
                cardRemote.BackColor = Color.FromArgb(248, 250, 252);
                lblCardRemoteTitle.ForeColor = Color.FromArgb(15, 23, 42);

                pnlRemoteConfig.Visible = false;
                lblHostSecurityInfo.Visible = true;

                if (txtDestPath != null) txtDestPath.Text = defaultHostDir;
                if (lblSpaceInfo != null) lblSpaceInfo.Text = "Spazio su disco richiesto: circa 65 MB (Host Server Completo)\nInclude tutti i componenti di gestione e sicurezza.";
            }
            else
            {
                // Highlight Remote Card
                cardRemote.BackColor = Color.FromArgb(239, 246, 255); // Light sapphire
                lblCardRemoteTitle.ForeColor = Color.FromArgb(30, 58, 138);

                // Mute Host Card
                cardHost.BackColor = Color.FromArgb(248, 250, 252);
                lblCardHostTitle.ForeColor = Color.FromArgb(15, 23, 42);

                lblHostSecurityInfo.Visible = false;
                pnlRemoteConfig.Visible = true;

                if (txtDestPath != null) txtDestPath.Text = defaultRemoteDir;
                if (lblSpaceInfo != null) lblSpaceInfo.Text = "Spazio su disco richiesto: circa 1 MB (Client Leggero)\nNessun consumo di CPU/RAM per Java su questo PC.";

                if (txtRemoteUrl != null && (txtRemoteUrl.Text == "auto" || string.IsNullOrEmpty(txtRemoteUrl.Text)))
                {
                    StartInstallerAutoDiscovery();
                }
            }
        }

        private void StartInstallerAutoDiscovery()
        {
            lblTestResult.Text = "Ricerca automatica del PC Host nella rete di casa in corso...";
            lblTestResult.ForeColor = Color.FromArgb(37, 99, 235);
            if (btnAutoDetect != null) btnAutoDetect.Enabled = false;

            BackgroundWorker bwDetect = new BackgroundWorker();
            bwDetect.DoWork += (s, e) => {
                string found = AutoDiscoverServerInInstaller();
                e.Result = found;
            };
            bwDetect.RunWorkerCompleted += (s, e) => {
                if (btnAutoDetect != null) btnAutoDetect.Enabled = true;
                string foundUrl = e.Result as string;
                if (!string.IsNullOrEmpty(foundUrl))
                {
                    txtRemoteUrl.Text = foundUrl;
                    lblTestResult.Text = "Server host rilevato con successo: " + foundUrl;
                    lblTestResult.ForeColor = Color.FromArgb(22, 101, 52);
                }
                else
                {
                    lblTestResult.Text = "Nessun server risponde attualmente. Lascia 'auto' per rilevamento automatico all'avvio.";
                    lblTestResult.ForeColor = Color.FromArgb(100, 116, 139);
                }
            };
            bwDetect.RunWorkerAsync();
        }

        private static string AutoDiscoverServerInInstaller()
        {
            // Solo la rete locale: un topic ntfy fisso porterebbe al PC Host di un'altra persona.
            // Fuori casa si inserisce l'indirizzo a mano; il programma poi impara il topic del proprio Host.
            // Fast LAN subnet scan
            try
            {
                string localIp = null;
                using (Socket socket = new Socket(AddressFamily.InterNetwork, SocketType.Dgram, 0))
                {
                    socket.Connect("8.8.8.8", 65530);
                    IPEndPoint endPoint = socket.LocalEndPoint as IPEndPoint;
                    if (endPoint != null) localIp = endPoint.Address.ToString();
                }

                if (!string.IsNullOrEmpty(localIp) && localIp.Contains("."))
                {
                    int lastDot = localIp.LastIndexOf('.');
                    string subnet = localIp.Substring(0, lastDot + 1);
                    string foundLan = null;
                    object lockObj = new object();

                    Parallel.For(1, 255, new ParallelOptions { MaxDegreeOfParallelism = 64 }, (i, state) =>
                    {
                        if (foundLan != null) { state.Stop(); return; }
                        string testIp = subnet + i;
                        try
                        {
                            using (TcpClient client = new TcpClient())
                            {
                                IAsyncResult ar = client.BeginConnect(testIp, 8080, null, null);
                                bool success = ar.AsyncWaitHandle.WaitOne(250);
                                if (success && client.Connected)
                                {
                                    client.EndConnect(ar);
                                    string candUrl = string.Format("http://{0}:8080", testIp);
                                    if (VerifyCandidateInInstaller(candUrl))
                                    {
                                        lock (lockObj)
                                        {
                                            foundLan = candUrl;
                                            state.Stop();
                                        }
                                    }
                                }
                            }
                        }
                        catch { }
                    });

                    if (!string.IsNullOrEmpty(foundLan)) return foundLan;
                }
            }
            catch { }

            return null;
        }

        private static bool VerifyCandidateInInstaller(string baseUrl)
        {
            if (string.IsNullOrEmpty(baseUrl)) return false;
            try
            {
                string target = baseUrl.TrimEnd('/') + "/api/status";
                HttpWebRequest req = (HttpWebRequest)WebRequest.Create(target);
                req.Timeout = 1200;
                req.Method = "GET";
                using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
                using (StreamReader reader = new StreamReader(resp.GetResponseStream()))
                {
                    string json = reader.ReadToEnd();
                    return json.Contains("\"state\"") || json.Contains("\"server_info\"") || json.Contains("\"status\"");
                }
            }
            catch { return false; }
        }

        private void TestRemoteConnection(string url)
        {
            if (string.IsNullOrEmpty(url) || url.Trim().Equals("auto", StringComparison.OrdinalIgnoreCase))
            {
                StartInstallerAutoDiscovery();
                return;
            }

            lblTestResult.Text = "Verifica in corso...";
            lblTestResult.ForeColor = Color.FromArgb(71, 85, 105);
            btnTestRemote.Enabled = false;

            BackgroundWorker bwTest = new BackgroundWorker();
            bwTest.DoWork += (s, e) => {
                try
                {
                    string testUrl = url.Trim();
                    if (!testUrl.StartsWith("http://", StringComparison.OrdinalIgnoreCase) &&
                        !testUrl.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
                    {
                        testUrl = "http://" + testUrl;
                    }
                    if (!testUrl.EndsWith("/")) testUrl += "/";
                    testUrl += "api/status";

                    HttpWebRequest req = (HttpWebRequest)WebRequest.Create(testUrl);
                    req.Timeout = 2500;
                    req.Method = "GET";
                    using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
                    {
                        e.Result = (resp.StatusCode == HttpStatusCode.OK);
                    }
                }
                catch
                {
                    e.Result = false;
                }
            };
            bwTest.RunWorkerCompleted += (s, e) => {
                btnTestRemote.Enabled = true;
                bool ok = (e.Result is bool) && (bool)e.Result;
                if (ok)
                {
                    lblTestResult.Text = "[✓] Connessione riuscita! Il PC secondario risponde correttamente.";
                    lblTestResult.ForeColor = Color.FromArgb(22, 101, 52);
                }
                else
                {
                    lblTestResult.Text = "[!] Nessuna risposta all'indirizzo specificato. Verifica che il PC sia acceso.";
                    lblTestResult.ForeColor = Color.FromArgb(180, 83, 9);
                }
            };
            bwTest.RunWorkerAsync();
        }

        // STEP 2: Destination Directory
        private void BuildStep2()
        {
            pnlStep2 = new Panel();
            pnlStep2.Dock = DockStyle.Fill;

            lblDestDesc = new Label();
            lblDestDesc.Text = "Scegli la cartella di installazione sul computer.\r\n\r\nI file dell'applicazione verranno salvati nel percorso seguente:";
            lblDestDesc.Location = new Point(12, 12);
            lblDestDesc.Size = new Size(540, 48);
            pnlStep2.Controls.Add(lblDestDesc);

            Label lblPath = new Label();
            lblPath.Text = "Cartella di destinazione:";
            lblPath.Font = new Font("Segoe UI", 9F, FontStyle.Bold);
            lblPath.Location = new Point(12, 68);
            lblPath.AutoSize = true;
            pnlStep2.Controls.Add(lblPath);

            txtDestPath = new TextBox();
            txtDestPath.Text = defaultHostDir;
            txtDestPath.Location = new Point(12, 92);
            txtDestPath.Size = new Size(430, 23);
            pnlStep2.Controls.Add(txtDestPath);

            btnBrowse = new Button();
            btnBrowse.Text = "Sfoglia...";
            btnBrowse.Location = new Point(450, 90);
            btnBrowse.Size = new Size(88, 26);
            btnBrowse.Click += (s, e) => {
                using (FolderBrowserDialog fbd = new FolderBrowserDialog())
                {
                    fbd.Description = I18n.T("Seleziona la cartella di destinazione:");
                    fbd.SelectedPath = txtDestPath.Text;
                    if (fbd.ShowDialog() == DialogResult.OK)
                    {
                        txtDestPath.Text = fbd.SelectedPath;
                    }
                }
            };
            pnlStep2.Controls.Add(btnBrowse);

            lblSpaceInfo = new Label();
            lblSpaceInfo.Text = "Spazio su disco richiesto: circa 65 MB\nInclude tutti i componenti del server manager.";
            lblSpaceInfo.ForeColor = Color.FromArgb(100, 116, 139);
            lblSpaceInfo.Location = new Point(12, 132);
            lblSpaceInfo.Size = new Size(520, 45);
            pnlStep2.Controls.Add(lblSpaceInfo);

            contentPanel.Controls.Add(pnlStep2);
        }

        // STEP 3: Options & Security
        private void BuildStep3()
        {
            pnlStep3 = new Panel();
            pnlStep3.Dock = DockStyle.Fill;

            lblOptDesc = new Label();
            lblOptDesc.Text = "Seleziona le opzioni e le protezioni di sicurezza desiderate:";
            lblOptDesc.Font = new Font("Segoe UI", 9.5F, FontStyle.Bold);
            lblOptDesc.Location = new Point(12, 10);
            lblOptDesc.AutoSize = true;
            pnlStep3.Controls.Add(lblOptDesc);

            chkDesktop = new CheckBox();
            chkDesktop.Text = "Crea collegamento sul Desktop (con icona Minecraft 3D)";
            chkDesktop.Checked = true;
            chkDesktop.Location = new Point(16, 38);
            chkDesktop.Size = new Size(520, 24);
            pnlStep3.Controls.Add(chkDesktop);

            chkStartMenu = new CheckBox();
            chkStartMenu.Text = "Crea gruppo di avvio nel Menu Start di Windows";
            chkStartMenu.Checked = true;
            chkStartMenu.Location = new Point(16, 66);
            chkStartMenu.Size = new Size(520, 24);
            pnlStep3.Controls.Add(chkStartMenu);

            chkAutoStart = new CheckBox();
            chkAutoStart.Text = "Avvia automaticamente quando accendi il computer (Consigliato)";
            chkAutoStart.Checked = true;
            chkAutoStart.Location = new Point(16, 94);
            chkAutoStart.Size = new Size(520, 24);
            pnlStep3.Controls.Add(chkAutoStart);

            chkRegisterUninstall = new CheckBox();
            chkRegisterUninstall.Text = "Registra nell'elenco 'Installazione Applicazioni' di Windows (Disinstallatore pulito)";
            chkRegisterUninstall.Checked = true;
            chkRegisterUninstall.Location = new Point(16, 122);
            chkRegisterUninstall.Size = new Size(520, 24);
            pnlStep3.Controls.Add(chkRegisterUninstall);

            Label lblSecNote = new Label();
            lblSecNote.Text = "Nota: L'avvio automatico garantisce che il server Minecraft e il pannello siano subito attivi all'accensione del PC, pronti per giocare o per il controllo da cellulare anche se il monitor o la finestra non sono ancora stati aperti.";
            lblSecNote.ForeColor = Color.FromArgb(71, 85, 105);
            lblSecNote.Location = new Point(16, 152);
            lblSecNote.Size = new Size(520, 50);
            pnlStep3.Controls.Add(lblSecNote);

            contentPanel.Controls.Add(pnlStep3);
        }

        // STEP 4: Progress
        private void BuildStep4()
        {
            pnlStep4 = new Panel();
            pnlStep4.Dock = DockStyle.Fill;

            lblProgressTitle = new Label();
            lblProgressTitle.Text = "Installazione e configurazione in corso...";
            lblProgressTitle.Font = new Font("Segoe UI", 11F, FontStyle.Bold);
            lblProgressTitle.Location = new Point(12, 22);
            lblProgressTitle.AutoSize = true;
            pnlStep4.Controls.Add(lblProgressTitle);

            prgBar = new ProgressBar();
            prgBar.Location = new Point(12, 60);
            prgBar.Size = new Size(530, 26);
            pnlStep4.Controls.Add(prgBar);

            lblProgressDetail = new Label();
            lblProgressDetail.Text = "Inizializzazione file...";
            lblProgressDetail.ForeColor = Color.FromArgb(71, 85, 105);
            lblProgressDetail.Location = new Point(12, 98);
            lblProgressDetail.Size = new Size(530, 40);
            pnlStep4.Controls.Add(lblProgressDetail);

            contentPanel.Controls.Add(pnlStep4);
        }

        // STEP 5: Finished
        private void BuildStep5()
        {
            pnlStep5 = new Panel();
            pnlStep5.Dock = DockStyle.Fill;

            lblFinishedTitle = new Label();
            lblFinishedTitle.Text = "Installazione Completata con Successo!";
            lblFinishedTitle.Font = new Font("Segoe UI", 13F, FontStyle.Bold);
            lblFinishedTitle.ForeColor = Color.FromArgb(16, 185, 129); // Emerald
            lblFinishedTitle.Location = new Point(12, 16);
            lblFinishedTitle.AutoSize = true;
            pnlStep5.Controls.Add(lblFinishedTitle);

            lblFinishedDesc = new Label();
            lblFinishedDesc.Text = "Voxarium è stato installato e configurato con le protezioni di sicurezza attive.\r\n\r\n" +
                                  "Puoi avviare l'applicazione in qualsiasi momento dal collegamento sul Desktop o dal Menu Start.";
            lblFinishedDesc.Location = new Point(14, 52);
            lblFinishedDesc.Size = new Size(530, 55);
            pnlStep5.Controls.Add(lblFinishedDesc);

            lblFinishedRole = new Label();
            lblFinishedRole.Text = "Modalità attiva: Server Host Locale (Questo PC)";
            lblFinishedRole.Font = new Font("Segoe UI", 9.5F, FontStyle.Bold);
            lblFinishedRole.ForeColor = Color.FromArgb(30, 41, 59);
            lblFinishedRole.Location = new Point(14, 114);
            lblFinishedRole.Size = new Size(530, 25);
            pnlStep5.Controls.Add(lblFinishedRole);

            chkLaunchNow = new CheckBox();
            chkLaunchNow.Text = "Avvia Voxarium adesso";
            chkLaunchNow.Font = new Font("Segoe UI", 9.5F, FontStyle.Bold);
            chkLaunchNow.Checked = true;
            chkLaunchNow.Location = new Point(16, 150);
            chkLaunchNow.Size = new Size(400, 28);
            pnlStep5.Controls.Add(chkLaunchNow);

            contentPanel.Controls.Add(pnlStep5);
        }

        private void CheckPrerequisites()
        {
            bool javaOk = IsJavaAvailable();
            lblPyStatus.Text = "[✓] Interfaccia Desktop Nativa Windows integrata con Microsoft Edge WebView2.";
            lblPyStatus.ForeColor = Color.FromArgb(22, 101, 52);
            lblReqStatus.Text = "[✓] Runtime del pannello incluso nell'installazione (nessuna installazione di Python richiesta).\r\n" +
                (javaOk ? "[✓] Java rilevato: il server Minecraft potrà essere avviato in modalità Host."
                        : "[!] Java non rilevato: per la modalità Host installare Java 17 o 21 prima di avviare il server Minecraft.");
            lblReqStatus.ForeColor = javaOk ? Color.FromArgb(22, 101, 52) : Color.FromArgb(180, 83, 9);
        }

        private static bool IsJavaAvailable()
        {
            try
            {
                ProcessStartInfo psi = new ProcessStartInfo("java", "-version");
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.RedirectStandardError = true;
                psi.RedirectStandardOutput = true;
                using (Process p = Process.Start(psi))
                {
                    if (p == null) return false;
                    p.WaitForExit(5000);
                    return p.HasExited && p.ExitCode == 0;
                }
            }
            catch { return false; }
        }

        private void ShowStep(int step)
        {
            currentStep = step;
            pnlStep0.Visible = (step == 0);
            pnlStep1.Visible = (step == 1);
            pnlStep2.Visible = (step == 2);
            pnlStep3.Visible = (step == 3);
            pnlStep4.Visible = (step == 4);
            pnlStep5.Visible = (step == 5);

            btnBack.Enabled = (step > 0 && step < 4);
            btnCancel.Enabled = (step != 4);

            if (step == 0)
            {
                CheckPrerequisites();
                btnNext.Text = "Avanti >";
            }
            else if (step == 1)
            {
                SelectMode(rbModeHost.Checked);
                btnNext.Text = "Avanti >";
            }
            else if (step == 3)
            {
                btnNext.Text = "Installa";
            }
            else if (step == 4)
            {
                btnNext.Enabled = false;
                btnBack.Enabled = false;
                btnCancel.Enabled = false;
                StartInstallation();
            }
            else if (step == 5)
            {
                btnNext.Enabled = true;
                btnNext.Text = "Fine";
                btnBack.Visible = false;
                btnCancel.Visible = false;
                if (lblFinishedRole != null)
                {
                    lblFinishedRole.Text = rbModeHost.Checked
                        ? "Modalità attiva: Server Host Locale (Questo PC ospita il server)"
                        : string.Format("Modalità attiva: Controllo Remoto (Connesso a {0})", txtRemoteUrl.Text.Trim());
                }

                if (Program.IsUpdateMode || Program.IsSilentMode)
                {
                    // Senza domande su un PC nuovo il programma non si apre da solo; se era già installato (aggiornamento con
                    // winget) si riapre come prima, altrimenti su un PC Host il pannello resterebbe spento
                    if (Program.IsSilentMode && !Program.IsUpdateMode) chkLaunchNow.Checked = false;
                    System.Windows.Forms.Timer autoFinish = new System.Windows.Forms.Timer();
                    autoFinish.Interval = 800;
                    autoFinish.Tick += new EventHandler(delegate(object s, EventArgs e) {
                        autoFinish.Stop();
                        autoFinish.Dispose();
                        btnNext.PerformClick();
                    });
                    autoFinish.Start();
                }
            }
            else
            {
                btnNext.Text = "Avanti >";
            }
        }

        private void OnNextClicked()
        {
            if (currentStep == 0)
            {
                ShowStep(1);
            }
            else if (currentStep == 1)
            {
                if (rbModeRemote.Checked)
                {
                    string rUrl = txtRemoteUrl.Text.Trim();
                    if (string.IsNullOrEmpty(rUrl))
                    {
                        I18n.ShowMessage("Inserisci un indirizzo valido per il server remoto (es. http://192.168.1.50:8080).", "Attenzione", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                        return;
                    }
                }
                ShowStep(2);
            }
            else if (currentStep == 2)
            {
                string targetDir = txtDestPath.Text.Trim();
                if (string.IsNullOrEmpty(targetDir))
                {
                    I18n.ShowMessage("Specificare una cartella di destinazione valida.", "Attenzione", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    return;
                }
                ShowStep(3);
            }
            else if (currentStep == 3)
            {
                ShowStep(4);
            }
            else if (currentStep == 5)
            {
                if (chkLaunchNow.Checked)
                {
                    string targetExe = Path.Combine(txtDestPath.Text.Trim(), "Controllo Server Minecraft.exe");
                    if (File.Exists(targetExe))
                    {
                        ProcessStartInfo psi = new ProcessStartInfo(targetExe);
                        psi.WorkingDirectory = txtDestPath.Text.Trim();
                        Process.Start(psi);
                    }
                }
                this.Close();
            }
        }

        private void OnBackClicked()
        {
            if (currentStep > 0 && currentStep < 4)
            {
                ShowStep(currentStep - 1);
            }
        }

        /// <summary>Cartelle in cui il programma può essere installato (Host e Remoto).</summary>
        static string[] InstallDirs()
        {
            string programs = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs");
            return new string[] {
                Path.Combine(programs, "Controllo Server Minecraft") + "\\",
                Path.Combine(programs, "Controllo Server Minecraft Client") + "\\"
            };
        }

        static bool IsInside(string path, string[] dirs)
        {
            if (string.IsNullOrEmpty(path)) return false;
            foreach (string d in dirs)
            {
                if (path.StartsWith(d, StringComparison.OrdinalIgnoreCase)) return true;
            }
            return false;
        }

        /// <summary>
        /// Chiude SOLO i processi di questo programma prima di sostituire i file: la finestra del programma
        /// (per nome esatto) e i processi avviati dalla cartella di installazione (Python incluso, vecchio cloudflared),
        /// più un eventuale Python di sistema che esegue l'app.py installato. Nessun altro processo viene toccato.
        /// </summary>
        /// <summary>Valore di una chiave di launcher_config.json (stesso formato letto dal Launcher), "" se manca.</summary>
        private static string ReadConfigString(string json, string key)
        {
            Match m = Regex.Match(json ?? "", "\"" + Regex.Escape(key) + "\"\\s*:\\s*\"([^\"]*)\"");
            return m.Success ? m.Groups[1].Value.Trim() : "";
        }

        public static void AutoCloseRunningApp()
        {
            try
            {
                int currentPid = Process.GetCurrentProcess().Id;
                string[] dirs = InstallDirs();

                using (ManagementObjectSearcher searcher = new ManagementObjectSearcher(
                    "SELECT ProcessId, Name, ExecutablePath, CommandLine FROM Win32_Process"))
                {
                    foreach (ManagementObject obj in searcher.Get())
                    {
                        try
                        {
                            int pid = Convert.ToInt32(obj["ProcessId"]);
                            if (pid == currentPid) continue;

                            string name = obj["Name"] != null ? obj["Name"].ToString() : "";
                            string exe = obj["ExecutablePath"] != null ? obj["ExecutablePath"].ToString() : "";
                            string cmd = obj["CommandLine"] != null ? obj["CommandLine"].ToString() : "";

                            bool ourWindow = name.Equals("Controllo Server Minecraft.exe", StringComparison.OrdinalIgnoreCase) ||
                                             name.Equals("Controllo Server Minecraft Client.exe", StringComparison.OrdinalIgnoreCase);
                            bool fromInstallDir = IsInside(exe, dirs);
                            bool runsInstalledApp = false;
                            foreach (string d in dirs)
                            {
                                if (cmd.IndexOf(d + "app.py", StringComparison.OrdinalIgnoreCase) >= 0) runsInstalledApp = true;
                            }
                            if (!(ourWindow || fromInstallDir || runsInstalledApp)) continue;

                            Process target = Process.GetProcessById(pid);
                            if (!target.HasExited)
                            {
                                target.Kill();
                                target.WaitForExit(1500);
                            }
                        }
                        catch { }
                    }
                }

                // Pausa di rilascio handle file da parte di Windows
                Thread.Sleep(300);
            }
            catch { }
        }

        private void StartInstallation()
        {
            string destDir = txtDestPath.Text.Trim();
            bool isRemote = rbModeRemote.Checked;
            string remoteUrlVal = txtRemoteUrl.Text.Trim();

            // Assicura nuovamente che qualsiasi processo sia terminato prima della sovrascrittura file
            AutoCloseRunningApp();

            BackgroundWorker bw = new BackgroundWorker();
            bw.WorkerReportsProgress = true;

            bw.DoWork += (s, e) => {
                bw.ReportProgress(10, "Creazione cartella di destinazione...");
                if (!Directory.Exists(destDir))
                {
                    Directory.CreateDirectory(destDir);
                }

                Assembly asm = Assembly.GetExecutingAssembly();
                string resourceName = "package.zip";

                using (Stream stream = asm.GetManifestResourceStream(resourceName))
                {
                    if (stream != null)
                    {
                        using (ZipArchive archive = new ZipArchive(stream, ZipArchiveMode.Read))
                        {
                            int total = archive.Entries.Count;
                            int count = 0;

                            foreach (ZipArchiveEntry entry in archive.Entries)
                            {
                                if (string.IsNullOrEmpty(entry.Name) && (entry.FullName.EndsWith("/") || entry.FullName.EndsWith("\\")))
                                {
                                    continue;
                                }

                                // In Remote Mode, we need Controllo Server Minecraft.exe, WebView2 DLLs, icons and the license texts
                                if (isRemote)
                                {
                                    if (!entry.FullName.StartsWith("licenses/", StringComparison.OrdinalIgnoreCase) &&
                                        !entry.Name.Equals("Controllo Server Minecraft.exe", StringComparison.OrdinalIgnoreCase) &&
                                        !entry.Name.Equals("Microsoft.Web.WebView2.Core.dll", StringComparison.OrdinalIgnoreCase) &&
                                        !entry.Name.Equals("Microsoft.Web.WebView2.WinForms.dll", StringComparison.OrdinalIgnoreCase) &&
                                        !entry.Name.Equals("WebView2Loader.dll", StringComparison.OrdinalIgnoreCase) &&
                                        !entry.Name.Equals("minecraft.ico", StringComparison.OrdinalIgnoreCase) &&
                                        !entry.Name.Equals("voxarium.ico", StringComparison.OrdinalIgnoreCase) &&
                                        !entry.Name.Equals("minecraft_block.png", StringComparison.OrdinalIgnoreCase))
                                    {
                                        continue;
                                    }
                                }

                                string fullPath = Path.Combine(destDir, entry.FullName.Replace("/", "\\"));
                                string parent = Path.GetDirectoryName(fullPath);
                                if (!Directory.Exists(parent))
                                {
                                    Directory.CreateDirectory(parent);
                                }

                                if (entry.Name.Equals("config.json", StringComparison.OrdinalIgnoreCase) && File.Exists(fullPath))
                                {
                                    // Preserve existing server configuration
                                }
                                else
                                {
                                    try
                                    {
                                        if (File.Exists(fullPath))
                                        {
                                            try { File.SetAttributes(fullPath, FileAttributes.Normal); } catch { }
                                        }
                                        entry.ExtractToFile(fullPath, true);
                                    }
                                    catch (Exception)
                                    {
                                        // If file is locked because it is in use, rename old file and extract new one
                                        try
                                        {
                                            string oldTemp = fullPath + ".old_" + Environment.TickCount;
                                            if (File.Exists(oldTemp)) File.Delete(oldTemp);
                                            File.Move(fullPath, oldTemp);
                                            entry.ExtractToFile(fullPath, true);
                                        }
                                        catch { }
                                    }
                                }

                                count++;
                                int pct = 15 + (int)((float)count / Math.Max(1, total) * 55);
                                bw.ReportProgress(pct, string.Format("Estratto: {0}", entry.Name));
                            }
                        }
                    }
                }

                // Le versioni precedenti lasciavano file non più usati (vecchi tunnel, script, log)
                RemoveObsoleteFiles(destDir);

                // Write launcher_config.json
                bw.ReportProgress(75, "Configurazione parametri di avvio...");
                string launcherCfgPath = Path.Combine(destDir, "launcher_config.json");
                string modeStr = isRemote ? "remote" : "local";
                string finalRemoteUrl = remoteUrlVal;
                if (finalRemoteUrl.Equals("auto", StringComparison.OrdinalIgnoreCase))
                {
                    finalRemoteUrl = "auto";
                }
                else if (!finalRemoteUrl.StartsWith("http://", StringComparison.OrdinalIgnoreCase) &&
                    !finalRemoteUrl.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
                {
                    finalRemoteUrl = "http://" + finalRemoteUrl;
                }

                // PC Remoto: topic ntfy, identificativo e impronta del certificato dell'Host li impara il Launcher in rete
                // locale. Si conservano se l'Host è lo stesso di prima, altrimenti dopo ogni aggiornamento il PC fuori casa
                // non riusciva più a collegarsi finché non tornava nella rete di casa.
                string oldCfg = "";
                try { if (File.Exists(launcherCfgPath)) oldCfg = File.ReadAllText(launcherCfgPath); } catch { }
                bool sameHost = isRemote && ReadConfigString(oldCfg, "remote_url").Equals(finalRemoteUrl.Replace("\\", "/"), StringComparison.OrdinalIgnoreCase);
                string pushTopic = sameHost ? ReadConfigString(oldCfg, "push_topic") : "";
                string hostId = sameHost ? ReadConfigString(oldCfg, "host_id") : "";
                string certFp = sameHost ? ReadConfigString(oldCfg, "cert_fingerprint") : "";
                if (!Regex.IsMatch(pushTopic, "^[A-Za-z0-9_-]{1,64}$") || !Regex.IsMatch(hostId, "^[0-9a-f]{16}$")) { pushTopic = ""; hostId = ""; }
                if (!Regex.IsMatch(certFp, "^[0-9a-f]{64}$")) certFp = "";

                string cfgJson = string.Format("{{\r\n  \"mode\": \"{0}\",\r\n  \"remote_url\": \"{1}\",\r\n  \"push_topic\": \"{2}\",\r\n  \"host_id\": \"{3}\",\r\n  \"cert_fingerprint\": \"{4}\",\r\n  \"language\": \"{5}\"\r\n}}\r\n",
                    modeStr, finalRemoteUrl.Replace("\\", "/"), pushTopic, hostId, certFp, I18n.Lang);
                File.WriteAllText(launcherCfgPath, cfgJson);

                // Accesso dal telefono: regole firewall per le porte 8080 (rete di casa) e 8443 (da internet),
                // create dall'installer stesso come amministratore (FirewallRules). Solo modalità Host.
                // In aggiornamento, se le regole ci sono già, niente richiesta di amministratore e niente regole ricreate
                // (una regola spenta dall'utente resta spenta)
                // Senza domande (--silent) niente richiesta di amministratore: Windows chiederà il permesso del firewall
                // al primo avvio del pannello
                if (!isRemote && !Program.IsSilentMode && !(Program.IsUpdateMode && FirewallRules.AlreadyPresent()))
                {
                    bw.ReportProgress(80, "Regole firewall per l'accesso dal telefono (porte 8080 e 8443)...");
                    FirewallRules.RequestElevated();
                }

                // Create Shortcuts
                bw.ReportProgress(84, "Creazione collegamenti Desktop e Menu Start...");
                string mainExe = Path.Combine(destDir, "Controllo Server Minecraft.exe");
                // Icona dei collegamenti: voxarium.ico (un nome nuovo). Con lo stesso percorso di prima Windows continuava
                // a mostrare la vecchia icona che tiene in memoria; minecraft.ico resta per le versioni precedenti.
                string iconPath = Path.Combine(destDir, "voxarium.ico");
                if (!File.Exists(iconPath)) iconPath = Path.Combine(destDir, "minecraft.ico");
                string shortcutName = isRemote ? "Voxarium (Remoto).lnk" : "Voxarium.lnk";
                string shortcutDesc = isRemote ? "Controllo Remoto per Server Minecraft su PC Secondario" : "Pannello di Controllo per Server Minecraft";

                // Aggiornamento automatico: si rispettano le scelte fatte dopo l'installazione. Collegamenti rinnovati solo
                // dove ci sono ancora (uno cancellato dall'utente non torna), avvio automatico lasciato com'era.
                bool update = Program.IsUpdateMode;

                // 1. Desktop Shortcut across all desktop profiles
                if (chkDesktop.Checked)
                {
                    CreateDesktopShortcuts(mainExe, destDir, iconPath, shortcutName, shortcutDesc, update);
                }

                // 2. Start Menu Shortcut
                string programsMenu = Environment.GetFolderPath(Environment.SpecialFolder.Programs);
                bool hadStartMenu = Directory.Exists(Path.Combine(programsMenu, "Voxarium")) ||
                                    Directory.Exists(Path.Combine(programsMenu, LegacyName));
                if (chkStartMenu.Checked && (!update || hadStartMenu))
                {
                    string startMenu = Environment.GetFolderPath(Environment.SpecialFolder.Programs);
                    // Il programma si chiamava "Controllo Server Minecraft": toglie la vecchia cartella del Menu Start
                    try
                    {
                        string legacyFolder = Path.Combine(startMenu, LegacyName);
                        if (Directory.Exists(legacyFolder)) Directory.Delete(legacyFolder, true);
                    }
                    catch { }
                    string folder = Path.Combine(startMenu, "Voxarium");
                    if (!Directory.Exists(folder)) Directory.CreateDirectory(folder);
                    string lnkMenu = Path.Combine(folder, shortcutName);
                    CreateShortcut(lnkMenu, mainExe, destDir, iconPath, shortcutDesc);
                }

                // 3. Auto-start on Windows Boot
                if (update)
                {
                    // La voce del registro (scritta dall'installer, dal pannello o dal menu dell'icona, con o senza
                    // --autostart) resta com'è; un vecchio collegamento in Esecuzione automatica diventa la voce del registro
                    if (StartupShortcutExists() && !AutoStartRegistered())
                    {
                        try
                        {
                            using (RegistryKey rk = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run", true))
                            {
                                if (rk != null) rk.SetValue("ControlloServerMinecraft", string.Format("\"{0}\"", mainExe));
                            }
                        }
                        catch { }
                    }
                    RemoveStartupShortcuts();
                }
                else if (chkAutoStart != null && chkAutoStart.Checked)
                {
                    bw.ReportProgress(88, "Configurazione avvio automatico con Windows...");
                    try
                    {
                        using (RegistryKey rk = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run", true))
                        {
                            if (rk != null)
                            {
                                rk.SetValue("ControlloServerMinecraft", string.Format("\"{0}\"", mainExe));
                            }
                        }
                    }
                    catch { }

                    // Solo la voce del registro: un collegamento in Esecuzione automatica in più non serve
                    // e prima restava anche con l'avvio automatico spento dal pannello
                    RemoveStartupShortcuts();
                }
                else
                {
                    try
                    {
                        using (RegistryKey rk = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run", true))
                        {
                            if (rk != null) rk.DeleteValue("ControlloServerMinecraft", false);
                        }
                    }
                    catch { }
                    RemoveStartupShortcuts();
                }

                // 4. Uninstaller Script and Registry
                bw.ReportProgress(92, "Registrazione disinstallatore in Windows...");
                string uninstallBat = Path.Combine(destDir, "Disinstalla.bat");
                // La cartella si può eliminare per intero solo se è davvero quella del programma (mai una cartella generica)
                bool canDeleteFolder = Path.GetFileName(destDir.TrimEnd('\\')).StartsWith("Controllo Server Minecraft", StringComparison.OrdinalIgnoreCase);
                string cleanupPart = string.Format(
                    "echo.\r\n" +
                    "echo " + I18n.T("Vuoi eliminare anche le impostazioni (PIN, canale notifiche, elenco server)?") + "\r\n" +
                    "echo " + I18n.T("I mondi Minecraft e i backup NON vengono toccati.") + "\r\n" +
                    "choice /c " + I18n.T("SN") + " /m \"" + I18n.T("[S] elimina tutto   [N] conserva le impostazioni per una futura reinstallazione") + "\"\r\n" +
                    "if errorlevel 2 goto keep\r\n" +
                    (canDeleteFolder
                        // Un server Minecraft dentro la cartella del programma: si cancellano solo le impostazioni
                        ? "dir /s /b \"{0}\\server.properties\" >nul 2>&1 && goto onlycfg\r\n" +
                          "echo " + I18n.T("La cartella del programma verra' eliminata.") + "\r\n" +
                          "echo " + I18n.T("Premi un tasto per completare...") + "\r\n" +
                          "pause >nul\r\n" +
                          "cd /d \"%TEMP%\"\r\n" +
                          "start \"\" /min cmd /c \"timeout /t 2 /nobreak >nul & rmdir /s /q \"{0}\"\"\r\n" +
                          "exit\r\n"
                        : "") +
                    ":onlycfg\r\n" +
                    "del /f /q \"{0}\\config.json\" \"{0}\\instances.json\" \"{0}\\launcher_config.json\" 2>nul\r\n" +
                    "echo " + I18n.T("Impostazioni eliminate. Puoi eliminare manualmente la cartella:") + "\r\n" +
                    "echo \"{0}\"\r\n" +
                    "goto end\r\n" +
                    ":keep\r\n" +
                    "echo " + I18n.T("Impostazioni conservate in:") + "\r\n" +
                    "echo \"{0}\"\r\n" +
                    ":end\r\n" +
                    "echo.\r\n" +
                    "pause\r\n", destDir);
                string uninstContent = string.Format(
                    "@echo off\r\n" +
                    "chcp 65001 >nul\r\n" +
                    "echo ========================================================\r\n" +
                    "echo     " + I18n.T("DISINSTALLAZIONE VOXARIUM") + "\r\n" +
                    "echo ========================================================\r\n" +
                    "echo.\r\n" +
                    "echo " + I18n.T("Arresto processi in corso...") + "\r\n" +
                    "taskkill /f /im \"Controllo Server Minecraft.exe\" 2>nul\r\n" +
                    // Pannello (python) e tunnel (cloudflared) avviati da questa cartella
                    "powershell -NoProfile -Command \"Get-Process | Where-Object {{ $_.Path -like '{1}\\*' }} | Stop-Process -Force\" 2>nul\r\n" +
                    "echo " + I18n.T("Eliminazione avvio automatico con Windows...") + "\r\n" +
                    "reg delete \"HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run\" /v \"ControlloServerMinecraft\" /f 2>nul\r\n" +
                    "del /f /q \"%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\Controllo Server Minecraft*.lnk\" 2>nul\r\n" +
                    "del /f /q \"%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\Voxarium*.lnk\" 2>nul\r\n" +
                    "echo " + I18n.T("Eliminazione collegamenti...") + "\r\n" +
                    "del /f /q \"%USERPROFILE%\\Desktop\\Controllo Server Minecraft.lnk\" 2>nul\r\n" +
                    "del /f /q \"%USERPROFILE%\\Desktop\\Controllo Server Minecraft (Remoto).lnk\" 2>nul\r\n" +
                    "del /f /q \"%USERPROFILE%\\OneDrive\\Desktop\\Controllo Server Minecraft.lnk\" 2>nul\r\n" +
                    "del /f /q \"%USERPROFILE%\\OneDrive\\Desktop\\Controllo Server Minecraft (Remoto).lnk\" 2>nul\r\n" +
                    "rmdir /s /q \"%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Controllo Server Minecraft\" 2>nul\r\n" +
                    "del /f /q \"%USERPROFILE%\\Desktop\\Voxarium.lnk\" 2>nul\r\n" +
                    "del /f /q \"%USERPROFILE%\\Desktop\\Voxarium (Remoto).lnk\" 2>nul\r\n" +
                    "del /f /q \"%USERPROFILE%\\OneDrive\\Desktop\\Voxarium.lnk\" 2>nul\r\n" +
                    "del /f /q \"%USERPROFILE%\\OneDrive\\Desktop\\Voxarium (Remoto).lnk\" 2>nul\r\n" +
                    "rmdir /s /q \"%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Voxarium\" 2>nul\r\n" +
                    "reg delete \"HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\ControlloServerMinecraft\" /f 2>nul\r\n" +
                    "echo.\r\n" +
                    "echo " + I18n.T("I collegamenti, l'avvio automatico e la registrazione sono stati rimossi.") + "\r\n",
                    destDir, destDir.Replace("'", "''")) + cleanupPart;
                File.WriteAllText(uninstallBat, uninstContent);

                if (chkRegisterUninstall.Checked)
                {
                    RegisterUninstaller(destDir, uninstallBat, iconPath, isRemote);
                }

                RefreshShellIcons();
                bw.ReportProgress(100, "Completato!");
            };

            bw.ProgressChanged += (s, e) => {
                int pct = e.ProgressPercentage;
                if (pct < 0) pct = 0;
                if (pct > 100) pct = 100;
                prgBar.Value = pct;
                lblProgressDetail.Text = (string)e.UserState;
            };

            bw.RunWorkerCompleted += (s, e) => {
                if (e.Error != null)
                {
                    I18n.ShowMessage("Nota durante l'installazione: " + e.Error.Message, "Installazione", MessageBoxButtons.OK, MessageBoxIcon.Information);
                }
                ShowStep(5);
            };

            bw.RunWorkerAsync();
        }

        // Nome visibile fino alla v4.10.0: cartelle, .exe e chiavi di registro lo mantengono per l'aggiornamento automatico
        // File lasciati da versioni precedenti e non più usati: tunnel Cloudflare/ngrok/playit (tolti),
        // copie vecchie del pannello fuori da static\, script di sviluppo e log di vecchie prove.
        // Mai qui: config.json, instances.json, launcher_config.json, certs\, python\, static\.
        static readonly string[] ObsoleteFiles = {
            "tunnel_manager.py", "tunnel_provisioning.py", "ngrok_manager.py", "playit_manager.py", "playit.toml",
            "app.js", "index.html", "style.css", "app.manifest",
            "build_v480.py", "generate_final_assets.py", "generate_layout_v480.py", "generate_main_activity_v480.py",
            "build_apk.py", "make_icon.py", "read_lnk.py", "create_shortcut.py", "create_setup_shortcut.ps1",
            "CLAUDE.md", "README.md", "AGENTS.md", "PROJECT_CONTEXT.md", "BUGFIX_PER_CHAT_PC.md", ".gitignore",
            "Installer.cs", "Launcher.cs", "build_deploy.py", "build_runtime.py", "create_package.py",
            "publish_release.py", "requirements.txt",
            "app_stderr.log", "app_stdout.log", "backend_err.log", "backend_service.log", "backend_service_err.log",
            "out.log", "pyw_err.log", "pyw_out.log", "run_out.txt", "stderr.txt", "stdout.txt", "tunnel_debug.log"
        };

        static void RemoveObsoleteFiles(string destDir)
        {
            // Mai in una cartella di sviluppo (stessi nomi dei file di progetto)
            if (Directory.Exists(Path.Combine(destDir, ".git"))) return;
            foreach (string name in ObsoleteFiles)
            {
                try
                {
                    string f = Path.Combine(destDir, name);
                    if (File.Exists(f))
                    {
                        File.SetAttributes(f, FileAttributes.Normal);
                        File.Delete(f);
                    }
                }
                catch { }
            }
            // bin\ conteneva solo cloudflared.exe, ngrok.exe e playit.exe
            try
            {
                string bin = Path.Combine(destDir, "bin");
                if (Directory.Exists(bin)) Directory.Delete(bin, true);
            }
            catch { }
            // Il pannello è installato compilato (.pyc): via i .py delle versioni precedenti, che Python
            // userebbe al posto dei .pyc nuovi, e le loro copie compilate in __pycache__
            foreach (string dir in new string[] { destDir, Path.Combine(destDir, "routes") })
            {
                try
                {
                    if (!Directory.Exists(dir)) continue;
                    foreach (string pyc in Directory.GetFiles(dir, "*.pyc"))
                    {
                        string py = pyc.Substring(0, pyc.Length - 1);
                        try { if (File.Exists(py)) { File.SetAttributes(py, FileAttributes.Normal); File.Delete(py); } } catch { }
                    }
                    string cache = Path.Combine(dir, "__pycache__");
                    if (Directory.Exists(cache)) Directory.Delete(cache, true);
                }
                catch { }
            }
            // Copie ".old_<n>" create quando un file era bloccato durante un aggiornamento precedente
            try
            {
                foreach (string f in Directory.GetFiles(destDir, "*.old_*", SearchOption.AllDirectories))
                {
                    try { File.Delete(f); } catch { }
                }
            }
            catch { }
        }

        // Collegamenti di avvio delle versioni precedenti (prima e dopo il nome Voxarium): l'avvio automatico
        // usa solo la voce ControlloServerMinecraft nel registro
        static readonly string[] StartupShortcutNames = { "Controllo Server Minecraft.lnk", "Controllo Server Minecraft (Remoto).lnk",
                                                          "Voxarium.lnk", "Voxarium (Remoto).lnk" };

        static bool AutoStartRegistered()
        {
            try
            {
                using (RegistryKey rk = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run", false))
                {
                    return rk != null && rk.GetValue("ControlloServerMinecraft") != null;
                }
            }
            catch { return false; }
        }

        static bool StartupShortcutExists()
        {
            try
            {
                string startupFolder = Environment.GetFolderPath(Environment.SpecialFolder.Startup);
                if (string.IsNullOrEmpty(startupFolder)) return false;
                foreach (string name in StartupShortcutNames)
                {
                    if (File.Exists(Path.Combine(startupFolder, name))) return true;
                }
            }
            catch { }
            return false;
        }

        static void RemoveStartupShortcuts()
        {
            try
            {
                string startupFolder = Environment.GetFolderPath(Environment.SpecialFolder.Startup);
                if (string.IsNullOrEmpty(startupFolder) || !Directory.Exists(startupFolder)) return;
                foreach (string name in StartupShortcutNames)
                {
                    try
                    {
                        string f = Path.Combine(startupFolder, name);
                        if (File.Exists(f)) File.Delete(f);
                    }
                    catch { }
                }
            }
            catch { }
        }

        const string LegacyName = "Controllo Server Minecraft";

        /// <summary>onlyExisting (aggiornamento): rinnova il collegamento solo sui Desktop dove c'è già, col nome nuovo o vecchio.</summary>
        private void CreateDesktopShortcuts(string targetExe, string workingDir, string iconPath, string shortcutName, string description, bool onlyExisting)
        {
            HashSet<string> desktopDirs = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

            string d1 = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
            if (!string.IsNullOrEmpty(d1) && Directory.Exists(d1)) desktopDirs.Add(d1);

            string d2 = Environment.GetFolderPath(Environment.SpecialFolder.Desktop);
            if (!string.IsNullOrEmpty(d2) && Directory.Exists(d2)) desktopDirs.Add(d2);

            string userProfile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            if (!string.IsNullOrEmpty(userProfile))
            {
                string d3 = Path.Combine(userProfile, "Desktop");
                if (Directory.Exists(d3)) desktopDirs.Add(d3);

                string d4 = Path.Combine(userProfile, "OneDrive", "Desktop");
                if (Directory.Exists(d4)) desktopDirs.Add(d4);
            }

            string oneDrive = Environment.GetEnvironmentVariable("OneDrive");
            if (!string.IsNullOrEmpty(oneDrive))
            {
                string d5 = Path.Combine(oneDrive, "Desktop");
                if (Directory.Exists(d5)) desktopDirs.Add(d5);
            }

            foreach (string dir in desktopDirs)
            {
                try
                {
                    if (onlyExisting)
                    {
                        string legacyName = shortcutName.Replace("Voxarium", LegacyName);
                        if (!File.Exists(Path.Combine(dir, shortcutName)) && !File.Exists(Path.Combine(dir, legacyName))) continue;
                    }
                    // Collegamenti col vecchio nome del programma
                    foreach (string legacy in new string[] { LegacyName + ".lnk", LegacyName + " (Remoto).lnk" })
                    {
                        try { File.Delete(Path.Combine(dir, legacy)); } catch { }
                    }
                    string lnkDesktop = Path.Combine(dir, shortcutName);
                    if (File.Exists(lnkDesktop))
                    {
                        try { File.Delete(lnkDesktop); } catch { }
                    }
                    string urlFile = Path.ChangeExtension(lnkDesktop, ".url");
                    if (File.Exists(urlFile))
                    {
                        try { File.Delete(urlFile); } catch { }
                    }
                    CreateShortcut(lnkDesktop, targetExe, workingDir, iconPath, description);
                }
                catch { }
            }
        }

        [DllImport("shell32.dll")]
        static extern void SHChangeNotify(int eventId, uint flags, IntPtr item1, IntPtr item2);

        /// <summary>Chiede a Windows di ricaricare le icone (collegamenti con l'icona nuova subito visibili).</summary>
        static void RefreshShellIcons()
        {
            try { SHChangeNotify(0x08000000 /* SHCNE_ASSOCCHANGED */, 0 /* SHCNF_IDLIST */, IntPtr.Zero, IntPtr.Zero); } catch { }
        }

        private void CreateShortcut(string shortcutPath, string targetExe, string workingDir, string iconPath, string description)
        {
            try
            {
                Type shellType = Type.GetTypeFromProgID("WScript.Shell");
                if (shellType == null) return;
                dynamic shell = Activator.CreateInstance(shellType);
                dynamic shortcut = shell.CreateShortcut(shortcutPath);
                shortcut.TargetPath = targetExe;
                shortcut.WorkingDirectory = workingDir;
                shortcut.Description = description;
                if (File.Exists(iconPath))
                {
                    shortcut.IconLocation = iconPath + ",0";
                }
                shortcut.Save();
            }
            catch
            {
                try
                {
                    string urlContent = string.Format("[InternetShortcut]\r\nURL=file:///{0}\r\nIconFile={1}\r\nIconIndex=0\r\n",
                        targetExe.Replace("\\", "/"),
                        iconPath.Replace("\\", "/"));
                    File.WriteAllText(Path.ChangeExtension(shortcutPath, ".url"), urlContent);
                }
                catch { }
            }
        }

        private void RegisterUninstaller(string installDir, string uninstallBat, string iconPath, bool isRemote)
        {
            try
            {
                string keyPath = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\ControlloServerMinecraft";
                using (RegistryKey key = Registry.CurrentUser.CreateSubKey(keyPath))
                {
                    if (key != null)
                    {
                        string dispName = isRemote ? "Voxarium (Client Remoto)" : "Voxarium";
                        key.SetValue("DisplayName", dispName);
                        // Versione vera (winget la usa per capire cosa è installato e se c'è un aggiornamento): era fissa a "3.0.0"
                        Version v = Assembly.GetExecutingAssembly().GetName().Version;
                        key.SetValue("DisplayVersion", v.Major + "." + v.Minor + "." + v.Build);
                        key.SetValue("Publisher", "QMon31");
                        key.SetValue("UninstallString", string.Format("cmd.exe /c \"{0}\"", uninstallBat));
                        key.SetValue("InstallLocation", installDir);
                        if (File.Exists(iconPath))
                        {
                            key.SetValue("DisplayIcon", iconPath);
                        }
                        key.SetValue("NoModify", 1, RegistryValueKind.DWord);
                        key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
                    }
                }
            }
            catch { }
        }
    }
}
