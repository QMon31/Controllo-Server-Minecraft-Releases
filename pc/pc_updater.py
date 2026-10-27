"""
Controllo e installazione degli aggiornamenti del programma PC dal repository pubblico delle release.
Il manifest pc_version.json della release più recente indica versione, build, URL e SHA-256 dell'installer.
Su Linux le release sono separate da quelle Windows: tag "linux-…", mai segnate come "latest", con il proprio
linux_version.json (stesse chiavi) e il pacchetto .tar.gz installato con install.sh --update.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
from typing import Any, Dict

from pc_version import LEGACY_UPDATE_REPO, PC_BUILD, PC_VERSION, UPDATE_REPO

MANIFEST_URL = f"https://github.com/{UPDATE_REPO}/releases/latest/download/pc_version.json"
# Installer accettati: dal repository con il nome nuovo o con quello vecchio (reindirizzato da GitHub)
SETUP_URL_PREFIXES = tuple(f"https://github.com/{r}/releases/download/" for r in (UPDATE_REPO, LEGACY_UPDATE_REPO))
CHECK_INTERVAL_SECONDS = 6 * 3600
USER_AGENT = f"ControlloServerMinecraft/{PC_VERSION}"
DETACHED_PROCESS = 0x00000008
IS_LINUX = sys.platform.startswith("linux")
LINUX_TAG_PREFIX = "linux-"
LINUX_MANIFEST_NAME = "linux_version.json"
# Le release Linux non sono "latest" (quella resta l'installer Windows): si cercano tra le ultime con l'API pubblica
RELEASES_API_URL = f"https://api.github.com/repos/{UPDATE_REPO}/releases?per_page=30"

_lock = threading.Lock()
_state: Dict[str, Any] = {
    "checked_at": 0.0,
    "available": False,
    "latest": None,
    "error": None,
    "downloading": False,
    "progress": 0,
}


def _http_get(url: str, timeout: int = 15):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(req, timeout=timeout)


def status() -> Dict[str, Any]:
    with _lock:
        latest = _state["latest"] or {}
        return {
            "current_version": PC_VERSION,
            "current_build": PC_BUILD,
            "available": _state["available"],
            "latest_version": latest.get("version"),
            "latest_build": latest.get("build"),
            "notes": latest.get("notes", ""),
            "error": _state["error"],
            "downloading": _state["downloading"],
            "progress": _state["progress"],
        }


def linux_manifest_url(releases) -> str:
    """Indirizzo del linux_version.json della release Linux più recente (l'API le elenca dalla più nuova); "" se nessuna."""
    for rel in releases or []:
        if rel.get("draft") or not str(rel.get("tag_name", "")).startswith(LINUX_TAG_PREFIX):
            continue
        for asset in rel.get("assets") or []:
            if asset.get("name") == LINUX_MANIFEST_NAME:
                return str(asset.get("browser_download_url", ""))
    return ""


def _fetch_manifest() -> Dict[str, Any]:
    if not IS_LINUX:
        with _http_get(MANIFEST_URL) as resp:
            return json.loads(resp.read().decode("utf-8"))
    with _http_get(RELEASES_API_URL) as resp:
        url = linux_manifest_url(json.loads(resp.read().decode("utf-8")))
    if not url:
        return {}  # nessuna release Linux pubblicata: nessun aggiornamento
    if not url.startswith(SETUP_URL_PREFIXES):
        raise ValueError("indirizzo del manifest Linux non valido")
    with _http_get(url) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_for_update(force: bool = False) -> Dict[str, Any]:
    with _lock:
        if not force and time.time() - _state["checked_at"] < CHECK_INTERVAL_SECONDS:
            return status()
    try:
        manifest = _fetch_manifest()
        latest_build = int(manifest.get("build", 0))
        if latest_build and not str(manifest.get("setup_url", "")).startswith(SETUP_URL_PREFIXES):
            raise ValueError("indirizzo dell'installer non valido nel manifest")
        with _lock:
            _state.update(checked_at=time.time(), latest=manifest, available=latest_build > PC_BUILD, error=None)
    except Exception as e:
        with _lock:
            _state.update(checked_at=time.time(), error=str(e))
    return status()


def download_and_run_installer() -> Dict[str, Any]:
    with _lock:
        if not _state["available"] or not _state["latest"]:
            return {"status": "error", "message": "Nessun aggiornamento disponibile."}
        if _state["downloading"]:
            return {"status": "error", "message": "Download dell'aggiornamento già in corso."}
        _state.update(downloading=True, progress=0, error=None)
        manifest = dict(_state["latest"])
    threading.Thread(target=_download_worker, args=(manifest,), daemon=True).start()
    return {"status": "success", "message": "Download dell'aggiornamento avviato."}


def _download_worker(manifest: Dict[str, Any]):
    ext = ".tar.gz" if IS_LINUX else ".exe"
    dest = os.path.join(tempfile.gettempdir(), f"Setup_Voxarium_b{int(manifest.get('build', 0))}{ext}")
    try:
        sha = hashlib.sha256()
        done = 0
        with _http_get(manifest["setup_url"], timeout=60) as resp, open(dest, "wb") as out:
            total = int(resp.headers.get("Content-Length") or manifest.get("size") or 0)
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                sha.update(chunk)
                done += len(chunk)
                if total:
                    with _lock:
                        _state["progress"] = min(99, int(done * 100 / total))
        if sha.hexdigest().lower() != str(manifest.get("sha256", "")).lower():
            os.remove(dest)
            raise ValueError("verifica SHA-256 dell'installer non riuscita")
        with _lock:
            _state["progress"] = 100
        if IS_LINUX:
            _run_linux_update(dest)
        else:
            # L'installer chiude da solo pannello, backend ed eventuale vecchio tunnel (versioni precedenti alla 4.10) prima di sostituire i file (Installer.cs, avvio)
            subprocess.Popen([dest, "--update"], close_fds=True, creationflags=DETACHED_PROCESS)
    except Exception as e:
        with _lock:
            _state["error"] = f"Aggiornamento non riuscito: {e}"
    finally:
        with _lock:
            _state["downloading"] = False


def _run_linux_update(archive: str):
    """Estrae il pacchetto e avvia install.sh --update fuori dal servizio: lo script riavvia voxarium.service, e
    systemd chiuderebbe con il servizio anche i processi avviati da lui (systemd-run li mette in un'unità a parte)."""
    dest_dir = tempfile.mkdtemp(prefix="voxarium_update_")
    with tarfile.open(archive) as tf:
        members = tf.getmembers()
        for m in members:
            if not (m.isfile() or m.isdir()) or m.name.startswith("/") or ".." in m.name.split("/"):
                raise ValueError(f"file non valido nel pacchetto: {m.name}")
        tf.extractall(dest_dir, members=members)
    script = os.path.join(dest_dir, "voxarium", "install.sh")
    if not os.path.isfile(script):
        raise ValueError("install.sh mancante nel pacchetto")
    cmd = ["sh", script, "--update"]
    if shutil.which("systemd-run"):
        cmd = ["systemd-run", "--user", "--collect", "--unit", "voxarium-update"] + cmd
    subprocess.Popen(cmd, close_fds=True, start_new_session=True, creationflags=0,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
