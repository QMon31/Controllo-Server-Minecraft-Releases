"""
Controllo e installazione degli aggiornamenti del programma PC dal repository pubblico delle release.
Il manifest pc_version.json della release più recente indica versione, build, URL e SHA-256 dell'installer.
"""

import hashlib
import json
import os
import subprocess
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


def check_for_update(force: bool = False) -> Dict[str, Any]:
    with _lock:
        if not force and time.time() - _state["checked_at"] < CHECK_INTERVAL_SECONDS:
            return status()
    try:
        with _http_get(MANIFEST_URL) as resp:
            manifest = json.loads(resp.read().decode("utf-8"))
        latest_build = int(manifest.get("build", 0))
        if not str(manifest.get("setup_url", "")).startswith(SETUP_URL_PREFIXES):
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
    dest = os.path.join(tempfile.gettempdir(), f"Setup_Voxarium_b{int(manifest.get('build', 0))}.exe")
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
        # L'installer chiude da solo pannello, backend ed eventuale vecchio tunnel (versioni precedenti alla 4.10) prima di sostituire i file (Installer.cs, avvio)
        subprocess.Popen([dest, "--update"], close_fds=True, creationflags=DETACHED_PROCESS)
    except Exception as e:
        with _lock:
            _state["error"] = f"Aggiornamento non riuscito: {e}"
    finally:
        with _lock:
            _state["downloading"] = False
