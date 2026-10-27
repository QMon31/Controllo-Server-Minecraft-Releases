"""
Pubblica il codice sorgente di Voxarium (programma PC + app Android) nel repository pubblico QMon31/Voxarium,
con licenza GPL-3.0.

Uso:
    python publish_source.py                      prepara e mostra cosa verrebbe pubblicato (non pubblica nulla)
    python publish_source.py --push "messaggio"   prepara, controlla e pubblica (commit + push)

Cosa fa:
- prende solo i file già salvati con commit (HEAD) dei due repository privati, mai file locali o non salvati;
- lascia fuori le note interne (EXCLUDED) e le librerie di Microsoft WebView2 (si scaricano da NuGet, vedi BUILDING.md);
- li copia in pc/ e android/ di una copia locale del repository pubblico (README.md, LICENSE e gli altri file in
  cima restano com'erano, li gestisce chi pubblica);
- si ferma se trova un dato personale (personal_strings.txt, locale) o qualcosa che somiglia a una chiave o password.

La cartella dell'app Android si cerca in local_extra_dirs.txt (locale) come <cartella>/android_project.
"""
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_REPO_URL = "https://github.com/QMon31/Voxarium.git"
LOCAL_APPDATA = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
PUBLIC_CHECKOUT = os.path.join(LOCAL_APPDATA, "Controllo Server Minecraft", "public_source")
GIT = r"C:\Program Files\Git\cmd\git.exe" if os.path.isfile(r"C:\Program Files\Git\cmd\git.exe") else "git"

# Note interne delle chat di sviluppo (IP, percorsi, decisioni): restano solo nel repository privato
EXCLUDED = {
    "AGENTS.md", "BUGFIX_PER_CHAT_PC.md", "BUGFIX_PER_CHAT_APK.md", "PROJECT_CONTEXT.md",
    # Librerie di Microsoft (licenza propria, non GPL): si scaricano dal pacchetto NuGet Microsoft.Web.WebView2
    "Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll", "WebView2Loader.dll",
}
# Somiglia a una chiave o a una password scritta nel codice
SECRET_PATTERNS = [
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"(store|key)_password\s*=\s*[^{\s]",
    r"ghp_[A-Za-z0-9]{20,}", r"github_pat_[A-Za-z0-9_]{20,}",
    r"--ks-pass\s+pass:[^{]",
]


def git(repo, *args, binary=False):
    res = subprocess.run([GIT, "-C", repo, *args], capture_output=True)
    if res.returncode != 0:
        sys.exit(f"ERRORE git {' '.join(args)} in {repo}:\n{res.stderr.decode('utf-8', 'replace')}")
    return res.stdout if binary else res.stdout.decode("utf-8", "replace")


def find_android_repo():
    try:
        with open(os.path.join(BASE_DIR, "local_extra_dirs.txt"), encoding="utf-8") as f:
            dirs = [d.strip() for d in f if d.strip() and not d.startswith("#")]
    except OSError:
        dirs = []
    for d in dirs:
        candidate = os.path.join(d, "android_project")
        if os.path.isdir(os.path.join(candidate, ".git")):
            return candidate
    sys.exit("ERRORE: cartella android_project non trovata (aggiungi la cartella che la contiene a local_extra_dirs.txt).")


def export(repo, dest, excluded=()):
    """Copia in dest i file di HEAD del repository (non quelli della cartella di lavoro). Ritorna i percorsi copiati."""
    if git(repo, "status", "--porcelain").strip():
        print(f"Attenzione: in {os.path.basename(repo)} ci sono modifiche non salvate, che NON vengono pubblicate.")
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    copied = []
    with tarfile.open(fileobj=io.BytesIO(git(repo, "archive", "--format=tar", "HEAD", binary=True))) as tar:
        for m in tar.getmembers():
            if not m.isfile() or m.name in excluded:
                continue
            target = os.path.join(dest, *m.name.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with tar.extractfile(m) as src, open(target, "wb") as out:
                out.write(src.read())
            copied.append(m.name)
    return copied


def check(root):
    """Elenco dei problemi: dati personali o segreti nei file da pubblicare."""
    try:
        with open(os.path.join(BASE_DIR, "personal_strings.txt"), encoding="utf-8") as f:
            personal = [s.strip() for s in f if s.strip() and not s.startswith("#")]
    except OSError:
        sys.exit("ERRORE: manca personal_strings.txt (dati personali da cercare prima di pubblicare).")
    problems = []
    for folder, _, files in os.walk(root):
        if ".git" in folder.split(os.sep):
            continue
        for name in files:
            path = os.path.join(folder, name)
            with open(path, "rb") as f:
                data = f.read()
            text = data.decode("utf-8", "ignore")
            rel = os.path.relpath(path, root)
            for s in personal:
                if s.lower() in text.lower():
                    problems.append(f"{rel}: dato personale '{s}'")
            for p in SECRET_PATTERNS:
                if re.search(p, text):
                    problems.append(f"{rel}: possibile segreto ({p})")
    return problems


def main():
    push = "--push" in sys.argv
    message = sys.argv[sys.argv.index("--push") + 1] if push and sys.argv.index("--push") + 1 < len(sys.argv) else ""
    if push and not message:
        sys.exit('Uso: python publish_source.py --push "messaggio del commit"')

    if os.path.isdir(os.path.join(PUBLIC_CHECKOUT, ".git")):
        git(PUBLIC_CHECKOUT, "pull", "-q", "--ff-only")
    else:
        os.makedirs(os.path.dirname(PUBLIC_CHECKOUT), exist_ok=True)
        res = subprocess.run([GIT, "clone", "-q", PUBLIC_REPO_URL, PUBLIC_CHECKOUT], capture_output=True, text=True)
        if res.returncode != 0:
            sys.exit(f"ERRORE clone: {res.stderr}")

    android_repo = find_android_repo()
    pc_files = export(BASE_DIR, os.path.join(PUBLIC_CHECKOUT, "pc"), EXCLUDED)
    app_files = export(android_repo, os.path.join(PUBLIC_CHECKOUT, "android"), EXCLUDED)
    print(f"Programma PC: {len(pc_files)} file in pc/  |  App Android: {len(app_files)} file in android/")

    problems = check(PUBLIC_CHECKOUT)
    if problems:
        print("\nPUBBLICAZIONE BLOCCATA, da correggere nel repository privato:")
        print("\n".join("  " + p for p in problems))
        sys.exit(1)
    print("Controllo dati personali e segreti: nessun problema.")

    git(PUBLIC_CHECKOUT, "add", "-A")
    status = git(PUBLIC_CHECKOUT, "status", "--short")
    if not status.strip():
        print("Nessuna differenza rispetto al codice già pubblicato.")
        return
    print("\nModifiche rispetto al repository pubblico:\n" + status)
    if not push:
        print(f"Anteprima pronta in {PUBLIC_CHECKOUT} (nulla è stato pubblicato). Per pubblicare: --push \"messaggio\"")
        return
    git(PUBLIC_CHECKOUT, "commit", "-q", "-m", message)
    git(PUBLIC_CHECKOUT, "push", "-q")
    print("Codice sorgente pubblicato su " + PUBLIC_REPO_URL.removesuffix(".git"))


if __name__ == "__main__":
    main()
