"""
Pubblica l'installer nel repository GitHub PUBBLICO delle release.
Il codice sorgente resta nel repository privato: qui vengono caricati solo l'installer e pc_version.json.
Token: variabile d'ambiente GITHUB_TOKEN oppure file %USERPROFILE%\\.controllo_mc_github_token (mai nel progetto).
"""

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

from pc_version import LEGACY_UPDATE_REPO, PC_BUILD, PC_VERSION, UPDATE_REPO

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETUP_PATH = os.path.join(BASE_DIR, "Setup_Voxarium.exe")
SETUP_NAME = "Setup_Voxarium.exe"
TOKEN_FILE = os.path.join(os.path.expanduser("~"), ".controllo_mc_github_token")
API = "https://api.github.com"


GH_EXE = r"C:\Program Files\GitHub CLI\gh.exe"


def _token() -> str:
    # 1. variabile d'ambiente, 2. login di GitHub CLI (gh auth login, già fatto su questo PC), 3. file locale
    tok = os.environ.get("GITHUB_TOKEN", "").strip()
    if not tok:
        try:
            import subprocess
            gh = GH_EXE if os.path.isfile(GH_EXE) else "gh"
            tok = subprocess.run([gh, "auth", "token"], capture_output=True, text=True, timeout=15).stdout.strip()
        except Exception:
            tok = ""
    if not tok and os.path.isfile(TOKEN_FILE):
        with open(TOKEN_FILE, encoding="utf-8") as f:
            tok = f.read().strip()
    if not tok:
        sys.exit(f"ERRORE: nessun accesso a GitHub (eseguire 'gh auth login', o impostare GITHUB_TOKEN / {TOKEN_FILE})")
    return tok


def _request(method: str, url: str, token: str, data: bytes = None, content_type: str = "application/json"):
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ControlloServerMinecraft-Publisher",
        "Content-Type": content_type,
    })
    with urllib.request.urlopen(req, timeout=900) as resp:
        body = resp.read()
        return json.loads(body) if body else {}


ABOUT_PATH = os.path.join(BASE_DIR, "release_about.md")
# Frase interna dei messaggi di commit: non serve a chi legge la pagina pubblica della release
INTERNAL_NOTE = "Modifica autorizzata dall'utente."


def release_body(notes: str) -> str:
    """Testo della pagina pubblica della release: novità della versione e, sotto, cosa fa il programma."""
    body = "## What's new\n\n" + (notes.replace(INTERNAL_NOTE, "").strip() or f"Voxarium v{PC_VERSION}")
    if os.path.isfile(ABOUT_PATH):
        with open(ABOUT_PATH, encoding="utf-8") as f:
            body += "\n\n---\n\n" + f.read().strip()
    return body


def main(notes: str):
    notes = notes.replace(INTERNAL_NOTE, "").strip()
    token = _token()
    with open(SETUP_PATH, "rb") as f:
        setup_bytes = f.read()
    tag = f"v{PC_VERSION}-b{PC_BUILD}"
    manifest = {
        "version": PC_VERSION,
        "build": PC_BUILD,
        # Nome vecchio del repository: i PC con versioni precedenti alla 4.17.2 accettano solo quello (GitHub reindirizza)
        "setup_url": f"https://github.com/{LEGACY_UPDATE_REPO}/releases/download/{tag}/{SETUP_NAME}",
        "sha256": hashlib.sha256(setup_bytes).hexdigest(),
        "size": len(setup_bytes),
        "notes": notes,
    }
    try:
        release = _request("POST", f"{API}/repos/{UPDATE_REPO}/releases", token, json.dumps({
            "tag_name": tag,
            "name": f"Voxarium v{PC_VERSION} (Build {PC_BUILD})",
            "body": release_body(notes),
            "draft": False,
            "prerelease": False,
        }).encode("utf-8"))
        upload_base = release["upload_url"].split("{")[0]
        _request("POST", f"{upload_base}?name={SETUP_NAME}", token, setup_bytes, "application/octet-stream")
        _request("POST", f"{upload_base}?name=pc_version.json", token,
                 json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    except urllib.error.HTTPError as e:
        sys.exit(f"ERRORE GitHub {e.code}: {e.read().decode('utf-8', 'replace')}"
                 + ("\n(Se il tag esiste già: aumentare PC_BUILD in pc_version.py)" if e.code == 422 else ""))
    print(f"Pubblicata {tag}: {release['html_url']}")


if __name__ == "__main__":
    main(" ".join(sys.argv[1:]).strip() or f"Aggiornamento v{PC_VERSION}")
