"""
Master Build, Sync, Package, and Service Restart Script for Controllo Server Minecraft (PC Program)
"""

import os
import sys
import shutil
import subprocess
import tempfile
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_APP_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local"),
                             "Programs", "Controllo Server Minecraft")
DESKTOP_DIR = os.path.join(os.path.expanduser("~"), "Desktop")
# Copie in più del progetto su questo PC (es. la cartella dell'app Android, Google Drive), una per riga.
# File locale escluso da git: i percorsi personali non stanno nel codice.
LOCAL_EXTRA_DIRS = os.path.join(BASE_DIR, "local_extra_dirs.txt")

CSC_EXE = r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
GIT_EXE = r"C:\Program Files\Git\cmd\git.exe"
# File che non devono MAI finire su GitHub, anche se qualcuno toglie le righe dal .gitignore
FORBIDDEN_IN_GIT = {"config.json", "instances.json", "launcher_config.json", ".controllo_mc_github_token"}
FORBIDDEN_EXT_IN_GIT = (".exe", ".zip", ".apk", ".keystore", ".jks", ".log")


def _git(*args):
    git = GIT_EXE if os.path.isfile(GIT_EXE) else "git"
    return subprocess.run([git, "-C", BASE_DIR, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")


def git_commit_and_push(message: str, tag: str = None) -> bool:
    """Salva il codice nel repository privato (commit + push). Restituisce False in caso di errore."""
    if _git("rev-parse", "--is-inside-work-tree").returncode != 0:
        print("Notice: la cartella non è un repository git: caricamento su GitHub saltato.")
        return True
    _git("add", "-A")
    staged = [p for p in _git("diff", "--cached", "--name-only").stdout.splitlines() if p.strip()]
    bad = [p for p in staged
           if os.path.basename(p).lower() in FORBIDDEN_IN_GIT or p.lower().endswith(FORBIDDEN_EXT_IN_GIT)]
    if bad:
        _git("reset", "-q")
        print("ERRORE: questi file non devono essere caricati su GitHub (controllare .gitignore):\n  " + "\n  ".join(bad))
        return False
    if not staged:
        print("Nessuna modifica al codice da caricare su GitHub.")
    else:
        res = _git("commit", "-m", message)
        if res.returncode != 0:
            print(f"Error git commit:\n{res.stdout}\n{res.stderr}")
            return False
        print(f"Commit creato: {message}")
    if tag:
        _git("tag", "-f", tag)
    res = _git("push", "--follow-tags")
    if res.returncode != 0:
        print(f"Error git push:\n{res.stderr}")
        return False
    if tag:
        _git("push", "-f", "origin", tag)
    print("Codice caricato su GitHub (QMon31/Voxarium-PC).")
    return True

FILES_TO_SYNC = [
    "app.py",
    "server_installer.py",
    "instance_manager.py",
    "file_manager.py",
    "modrinth_store.py",
    "mod_info.py",
    "PROJECT_CONTEXT.md",
    "backup_manager.py",
    "upnp_manager.py",
    "server_manager.py",
    "platform_utils.py",
    "remote_access.py",
    "mc_ping.py",
    "Launcher.cs",
    "Installer.cs",
    "InstallerI18n.cs",
    "create_package.py",
    "requirements.txt",
    "build_runtime.py",
    "pc_version.py",
    "pc_updater.py",
    "pin_rules.py",
    "publish_release.py",
    "publish_source.py",
    "build_deploy.py",
    "BUGFIX_PER_CHAT_PC.md",
    *[os.path.join("routes", f) for f in sorted(os.listdir(os.path.join(BASE_DIR, "routes"))) if f.endswith(".py")],
    os.path.join("static", "index.html"),
    os.path.join("static", "app.js"),
    os.path.join("static", "i18n.js"),
    *[os.path.join("static", f"i18n_{lang}.js") for lang in ("it", "en", "es", "fr", "de", "pt")],
    os.path.join("static", "style.css"),
    os.path.join("static", "theme.css"),
    # Pannello aggiunto alla schermata Home del telefono (iPhone e Android)
    os.path.join("static", "manifest.json"),
    os.path.join("static", "apple-touch-icon.png"),
    os.path.join("static", "icon-512.png"),
    os.path.join("static", "icon-maskable-512.png"),
    "minecraft.ico",
    "minecraft_block.png",
    "favicon.ico",
    "Microsoft.Web.WebView2.Core.dll",
    "Microsoft.Web.WebView2.WinForms.dll",
    "WebView2Loader.dll",
]

# File che servono solo a sviluppare: vanno nelle copie del progetto, non nella cartella installata del programma
DEV_ONLY_FILES = {"PROJECT_CONTEXT.md", "Launcher.cs", "Installer.cs", "InstallerI18n.cs", "create_package.py", "requirements.txt",
                  "build_runtime.py", "publish_release.py", "publish_source.py", "build_deploy.py", "BUGFIX_PER_CHAT_PC.md"}

# config.json e instances.json (PIN, push_secret, elenco server) non sono in FILES_TO_SYNC: l'unica copia valida è
# quella del programma installato, modificata dal pannello. Copiarli da qui sovrascriveva le modifiche fatte dal pannello
# (es. un PIN cambiato) e li mandava anche su Google Drive.

TARGET_DIRS = [LOCAL_APP_DIR]
if os.path.isfile(LOCAL_EXTRA_DIRS):
    with open(LOCAL_EXTRA_DIRS, encoding="utf-8") as _f:
        TARGET_DIRS += [d.strip() for d in _f if d.strip() and not d.startswith("#") and os.path.isdir(d.strip())]

print("=== STEP T: Automatic tests (tests/) ===")
# Prima di fermare o toccare qualunque cosa: se un test fallisce non si compila e non si pubblica niente
_tests = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", os.path.join(BASE_DIR, "tests")],
                        cwd=BASE_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace")
print((_tests.stderr or _tests.stdout).strip().splitlines()[-1] if (_tests.stderr or _tests.stdout).strip() else "")
if _tests.returncode != 0:
    print(_tests.stderr)
    print("ERRORE: test automatici falliti: compilazione e pubblicazione annullate.")
    sys.exit(1)

print("=== STEP 0: Terminating Running App and Backend to Unlock Files ===")
try:
    wmi_kill = 'Get-CimInstance Win32_Process | Where-Object { $_.Name -eq "Controllo Server Minecraft.exe" -or ($_.CommandLine -like "*app.py*") } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }'
    subprocess.run(["powershell", "-Command", wmi_kill], capture_output=True)
    time.sleep(1.5)
except Exception as e:
    print(f"Notice: {e}")

print("\n=== STEP 1: Syncing Core Source Files to All Repositories ===")
for target in TARGET_DIRS:
    for rel_path in FILES_TO_SYNC:
        if target == LOCAL_APP_DIR and rel_path in DEV_ONLY_FILES:
            continue
        src = os.path.join(BASE_DIR, rel_path)
        dst = os.path.join(target, rel_path)
        if os.path.exists(src):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            try:
                shutil.copy2(src, dst)
            except Exception as e:
                print(f"Notice copying {rel_path} -> {target}: {e}")
print("Files synced successfully.")

def write_build_info() -> str:
    """Versione e produttore negli eseguibili (Proprietà > Dettagli): un programma senza questi dati, oltre che
    senza firma, viene trattato dagli antivirus come anonimo. La versione viene da pc_version.py."""
    from pc_version import PC_BUILD, PC_VERSION
    numeric = ".".join((PC_VERSION.split("-")[0].split(".") + ["0", "0", "0"])[:3] + [str(PC_BUILD)])
    path = os.path.join(tempfile.gettempdir(), "csm_build_info.cs")
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            "using System.Reflection;\n"
            '[assembly: AssemblyCompany("QMon31")]\n'
            '[assembly: AssemblyProduct("Voxarium")]\n'
            f'[assembly: AssemblyCopyright("© {time.strftime("%Y")} QMon31")]\n'
            f'[assembly: AssemblyVersion("{numeric}")]\n'
            f'[assembly: AssemblyFileVersion("{numeric}")]\n'
            f'[assembly: AssemblyInformationalVersion("{PC_VERSION} (Build {PC_BUILD})")]\n'
        )
    return path


BUILD_INFO_CS = write_build_info()

print("\n=== STEP 2: Compiling Controllo Server Minecraft.exe (Launcher) ===")
os.chdir(BASE_DIR)
cmd_launcher = [
    CSC_EXE,
    "/target:winexe",
    f"/win32icon:{os.path.join(BASE_DIR, 'minecraft.ico')}",
    f"/out:{os.path.join(BASE_DIR, 'Controllo Server Minecraft.exe')}",
    "/reference:System.Windows.Forms.dll,System.dll,System.Drawing.dll,Microsoft.Web.WebView2.Core.dll,Microsoft.Web.WebView2.WinForms.dll",
    os.path.join(BASE_DIR, "Launcher.cs"),
    BUILD_INFO_CS,
]
res_launcher = subprocess.run(cmd_launcher, capture_output=True, text=True)
if res_launcher.returncode != 0:
    print(f"Error compiling Launcher.cs:\n{res_launcher.stderr}\n{res_launcher.stdout}")
    sys.exit(1)
print(f"Controllo Server Minecraft.exe compiled successfully ({os.path.getsize('Controllo Server Minecraft.exe')} bytes).")

print("\n=== STEP 2b: Preparing bundled Python runtime ===")
if not os.path.isfile(os.path.join(BASE_DIR, "runtime", "python", "python.exe")):
    res_rt = subprocess.run([sys.executable, "build_runtime.py"], capture_output=True, text=True)
    print(res_rt.stdout)
    if res_rt.returncode != 0:
        print(f"Error building runtime: {res_rt.stderr}")
        sys.exit(1)

print("\n=== STEP 3: Building package.zip ===")
res_pkg = subprocess.run([sys.executable, "create_package.py"], capture_output=True, text=True)
print(res_pkg.stdout)
if res_pkg.returncode != 0:
    print(f"Error building package.zip: {res_pkg.stderr}")
    sys.exit(1)

print("\n=== STEP 4: Compiling Setup_Voxarium.exe (Installer) ===")
cmd_installer = [
    CSC_EXE,
    "/target:winexe",
    f"/win32icon:{os.path.join(BASE_DIR, 'minecraft.ico')}",
    f"/resource:{os.path.join(BASE_DIR, 'package.zip')}",
    f"/out:{os.path.join(BASE_DIR, 'Setup_Voxarium.exe')}",
    "/r:System.Windows.Forms.dll,System.Drawing.dll,System.dll,System.IO.Compression.dll,System.IO.Compression.FileSystem.dll,Microsoft.CSharp.dll,System.Management.dll",
    os.path.join(BASE_DIR, "Installer.cs"),
    os.path.join(BASE_DIR, "InstallerI18n.cs"),
    BUILD_INFO_CS,
]
res_installer = subprocess.run(cmd_installer, capture_output=True, text=True)
if res_installer.returncode != 0:
    print(f"Error compiling Installer.cs:\n{res_installer.stderr}\n{res_installer.stdout}")
    sys.exit(1)
print(f"Setup_Voxarium.exe compiled successfully ({os.path.getsize('Setup_Voxarium.exe')} bytes).")

print("\n=== STEP 4a: Saving source code to GitHub (private repository) ===")
if "--no-git" not in sys.argv:
    from pc_version import PC_BUILD, PC_VERSION
    if "--message" in sys.argv and sys.argv.index("--message") + 1 < len(sys.argv):
        commit_msg = sys.argv[sys.argv.index("--message") + 1]
    elif "--publish" in sys.argv and sys.argv.index("--publish") + 1 < len(sys.argv):
        commit_msg = f"Release v{PC_VERSION} (Build {PC_BUILD}): " + sys.argv[sys.argv.index("--publish") + 1]
    else:
        commit_msg = f"Build v{PC_VERSION} (Build {PC_BUILD}) del {time.strftime('%d/%m/%Y %H:%M')}"
    release_tag = f"v{PC_VERSION}-b{PC_BUILD}" if "--publish" in sys.argv else None
    if not git_commit_and_push(commit_msg, release_tag):
        print("Compilazione completata ma il codice NON è stato caricato su GitHub: correggere e rieseguire.")
        sys.exit(1)

if "--publish" in sys.argv:
    idx = sys.argv.index("--publish")
    notes = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else ""
    print("\n=== STEP 4b: Publishing release to the public GitHub releases repository ===")
    res_pub = subprocess.run([sys.executable, "publish_release.py", notes], capture_output=True, text=True)
    print(res_pub.stdout)
    if res_pub.returncode != 0:
        print(f"Error publishing release: {res_pub.stderr}")
        sys.exit(1)

print("\n=== STEP 5: Distributing Binaries & Installer to Desktop & All Locations ===")
binaries = [
    "Controllo Server Minecraft.exe",
    "Setup_Voxarium.exe",
    "package.zip"
]
# Copy setup to Desktop
shutil.copy2(os.path.join(BASE_DIR, "Setup_Voxarium.exe"), os.path.join(DESKTOP_DIR, "Setup_Voxarium.exe"))
print("Copied Setup_Voxarium.exe to Desktop.")
# Il setup si chiamava "Setup_Controllo_Server_Minecraft.exe": toglie la vecchia copia dal Desktop
old_setup = os.path.join(DESKTOP_DIR, "Setup_Controllo_Server_Minecraft.exe")
if os.path.isfile(old_setup):
    os.remove(old_setup)

for target in TARGET_DIRS:
    for b in binaries:
        s = os.path.join(BASE_DIR, b)
        d = os.path.join(target, b)
        if os.path.exists(s):
            shutil.copy2(s, d)
            print(f"Synced {b} -> {target}")

print("\n=== STEP 6: Gracefully Restarting Backend Service (port 8080) ===")
# Find running app.py process and kill it
try:
    wmi_cmd = 'Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*Controllo Server Minecraft*app.py*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }'
    subprocess.run(["powershell", "-Command", wmi_cmd], capture_output=True)
    time.sleep(1.5)
except Exception as e:
    print(f"Notice on stopping old backend: {e}")

# Si riavvia il backend del programma installato, come fa Voxarium all'accensione: una sola configurazione e un solo
# certificato (prima partiva una seconda copia dalla cartella del progetto, con certificato diverso).
# Se la cartella installata manca (PC di sviluppo senza installazione) si usa quella del progetto.
installed = os.path.isfile(os.path.join(LOCAL_APP_DIR, "app.py"))
app_target_dir = LOCAL_APP_DIR if installed else BASE_DIR
bundled_py = os.path.join(LOCAL_APP_DIR, "python", "python.exe")
py_exe = bundled_py if installed and os.path.isfile(bundled_py) else sys.executable
app_py_file = os.path.join(app_target_dir, "app.py")
print(f"Avvio del backend da: {app_target_dir}")
# Stesso registro del launcher, così gli avvisi (print) restano leggibili
log_dir = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Controllo Server Minecraft")
os.makedirs(log_dir, exist_ok=True)
backend_log = open(os.path.join(log_dir, "backend.log"), "a", encoding="utf-8")
DETACHED = 0x00000008
NEW_GROUP = 0x00000200
NO_WINDOW = 0x08000000
subprocess.Popen(
    [py_exe, "-u", app_py_file],
    cwd=app_target_dir,
    stdin=subprocess.DEVNULL,
    stdout=backend_log,
    stderr=subprocess.STDOUT,
    creationflags=DETACHED | NEW_GROUP | NO_WINDOW,
    close_fds=True
)

# Verify port 8080 becomes available
time.sleep(3)
import urllib.request
success = False
for attempt in range(10):
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/api/status", timeout=2) as resp:
            if resp.status == 200:
                success = True
                print("Backend service is LIVE and responding on port 8080!")
                break
    except Exception:
        time.sleep(1)

if not success:
    print("Warning: Backend did not respond on port 8080 yet, but process was launched.")

print("\n=== BUILD & DEPLOYMENT COMPLETE! ===")
