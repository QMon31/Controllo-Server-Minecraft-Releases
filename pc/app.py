"""
FastAPI Server & WebSocket Hub for Minecraft Server Controller
Serves REST API, real-time WebSocket feed, static frontend,
network QR codes for mobile/another PC, in-game chat, and error diagnostics.
"""

import os
import sys

# Avviato come "python app.py" questo modulo si chiama __main__: lo si registra anche come "app",
# così "from app import ..." nei file di routes/ usa questo stesso modulo invece di eseguirlo una seconda volta
sys.modules.setdefault("app", sys.modules[__name__])

# Windows headless / pythonw / service safety
if sys.stdout is None:
    try:
        sys.stdout = open(os.devnull, 'w')
    except Exception:
        pass
if sys.stderr is None:
    try:
        sys.stderr = open(os.devnull, 'w')
    except Exception:
        pass

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import io
import json
import uuid
import base64
import hashlib
import asyncio
import socket
import time
import subprocess
import platform
import psutil
from contextlib import asynccontextmanager
from typing import List, Dict, Any, Optional

import re
import shutil
import urllib.request
import threading
import qrcode
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, PlainTextResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from urllib.parse import urlsplit
from pydantic import BaseModel

from server_manager import ServerManager, is_legacy_topic
from backup_manager import BackupManager
from upnp_manager import UPnPManager
from server_installer import ForgeServerInstaller
import instance_manager
import modrinth_store
import pc_updater
from pin_rules import is_default_pin, validate_new_pin
from remote_access import RemoteAccess, DEFAULT_REMOTE_PORT
from pc_version import PC_BUILD, PC_VERSION

APP_START_TIME = time.time()

# Initialize Server Manager & Directories
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(STATIC_DIR, exist_ok=True)

manager = ServerManager()
backup_mgr = BackupManager(server_dir=manager.config.get("server_dir", ""))
# Niente avvio del server mentre un backup o un ripristino lavora sui suoi file
manager.start_blocker = backup_mgr.busy_message
if manager.config.get("cloud_backup_dir"):
    backup_mgr.set_cloud_sync_dir(manager.config.get("cloud_backup_dir"))

def _pc_update_loop():
    time.sleep(60)  # lascia completare l'avvio
    notified_build = None
    while True:
        try:
            # force: senza, la risposta restava in memoria 6 ore e una nuova versione si vedeva con ore di ritardo
            st = pc_updater.check_for_update(force=True)
            if st.get("available") and st.get("latest_build") != notified_build:
                notified_build = st.get("latest_build")
                manager.add_log(f"[Aggiornamenti] Disponibile la versione v{st['latest_version']} "
                                f"(Build {st['latest_build']}) del programma PC.")
        except Exception as e:
            print(f"[Aggiornamenti] Controllo non riuscito: {e}")
        time.sleep(3600)

threading.Thread(target=_pc_update_loop, daemon=True).start()
upnp_mgr = UPnPManager()
forge_installer = ForgeServerInstaller(config_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"))

# Active WebSocket connections
active_websockets: List[WebSocket] = []
loop = None
cached_public_ip: Optional[str] = None

def get_public_ip(force: bool = False) -> str:
    global cached_public_ip
    if cached_public_ip and not force:
        return cached_public_ip
    endpoints = [
        "https://api.ipify.org",
        "https://ifconfig.me/ip",
        "https://icanhazip.com"
    ]
    for ep in endpoints:
        try:
            req = urllib.request.Request(ep, headers={'User-Agent': 'curl/7.68.0'})
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                ip = resp.read().decode('utf-8').strip()
                if ip and re.match(r'^\d+\.\d+\.\d+\.\d+$', ip):
                    cached_public_ip = ip
                    return ip
        except Exception:
            continue
    # Nessun IP di riserva: un indirizzo fisso sarebbe quello di un altro PC
    return cached_public_ip or ""

def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def generate_qr_data_uri(text: str) -> str:
    try:
        qr = qrcode.QRCode(box_size=5, border=2)
        qr.add_data(text)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"
    except Exception as e:
        print(f"Errore generazione QR code: {e}")
        return ""

# Forward server logs to WebSockets asynchronously
def broadcast_log_to_ws(line: str):
    if loop and active_websockets:
        # seq: chiamato da add_log con il lock preso, quindi è il numero proprio di questa riga
        asyncio.run_coroutine_threadsafe(_async_broadcast({"type": "log", "line": line, "seq": manager.log_seq}), loop)

def broadcast_chat_to_ws(chat_item: dict):
    if loop and active_websockets:
        if chat_item.get("type") == "clear_chat":
            asyncio.run_coroutine_threadsafe(_async_broadcast({"type": "clear_chat"}), loop)
        else:
            asyncio.run_coroutine_threadsafe(_async_broadcast({"type": "chat", "data": chat_item}), loop)

def broadcast_error_to_ws(error_item: dict):
    if loop and active_websockets:
        if error_item.get("type") == "clear":
            asyncio.run_coroutine_threadsafe(_async_broadcast({"type": "clear_errors"}), loop)
        else:
            asyncio.run_coroutine_threadsafe(_async_broadcast({"type": "error", "data": error_item}), loop)

WS_SEND_TIMEOUT = 5.0
# Un invio alla volta per ogni WebSocket: all'avvio di Minecraft migliaia di righe partivano tutte insieme verso lo
# stesso pannello, si accodavano e superavano il tempo limite
_ws_send_locks: Dict[int, asyncio.Lock] = {}


async def _send_ws(ws: WebSocket, message: dict):
    lock = _ws_send_locks.setdefault(id(ws), asyncio.Lock())
    async with lock:
        await asyncio.wait_for(ws.send_json(message), timeout=WS_SEND_TIMEOUT)


async def _async_broadcast(message: dict):
    disconnected = []
    for ws in list(active_websockets):
        try:
            await _send_ws(ws, message)
        except Exception:
            disconnected.append(ws)
    for ws in disconnected:
        if ws in active_websockets:
            active_websockets.remove(ws)
        # Prima il WebSocket lento (PC sotto sforzo con Minecraft acceso) veniva solo tolto dalla lista ma restava
        # aperto: il pannello non riceveva più console né stato finché non si riavviava il programma. Chiudendolo,
        # il pannello si ricollega da solo.
        try:
            await asyncio.wait_for(ws.close(code=1011), timeout=2.0)
        except Exception:
            pass

manager.subscribe_logs(broadcast_log_to_ws)
manager.subscribe_chat(broadcast_chat_to_ws)
manager.subscribe_errors(broadcast_error_to_ws)

# Accesso da internet: HTTPS sull'IP pubblico (porta remote_port aperta sul router), al posto del tunnel
REMOTE_PORT = int(manager.config.get("remote_port") or DEFAULT_REMOTE_PORT)

def _save_remote_external_port(port: int):
    # Porta concessa dal router (può essere diversa da REMOTE_PORT): ricordata perché l'indirizzo non cambi
    manager.config["remote_external_port"] = port
    manager.save_config()

remote_access = RemoteAccess(os.path.dirname(os.path.abspath(__file__)), REMOTE_PORT, upnp_mgr, get_public_ip,
                             external_port=manager.config.get("remote_external_port"),
                             on_external_port=_save_remote_external_port)

def remote_status_payload() -> Dict[str, Any]:
    st = remote_access.get_status()
    st["qr_code"] = generate_qr_data_uri(st["url"]) if st.get("url") else None
    return st

def on_remote_url_changed(url: str):
    global _cached_network_payload
    local_ip = get_local_ip()
    local_url = f"http://{local_ip}:{manager.config.get('web_port', 8080)}"
    manager.current_local_url = local_url
    _cached_network_payload = None
    if loop and active_websockets:
        asyncio.run_coroutine_threadsafe(_async_broadcast({"type": "remote", "data": remote_status_payload()}), loop)
    # Nuovo indirizzo e impronta del certificato a telefono e PC Remoti via ntfy (messaggio firmato)
    try:
        manager.broadcast_network_sync(url, local_url, remote_access.fingerprint)
    except Exception as e:
        print(f"[Accesso remoto] Errore invio indirizzo: {e}")

remote_access.url_subscribers.append(on_remote_url_changed)

_last_backup_notified_timestamp = None

def broadcast_backup_to_ws(backup_item: dict):
    global _last_backup_notified_timestamp
    if loop and active_websockets:
        asyncio.run_coroutine_threadsafe(_async_broadcast({"type": "backup", "data": backup_item}), loop)

    # Invia notifica push istantanea allo smartphone quando il backup è terminato (SOLO UNA VOLTA per backup)
    try:
        is_active = backup_item.get("active", False)
        pct = backup_item.get("percent", 0)
        completed_at = backup_item.get("completed_at")
        if not is_active and pct == 100 and completed_at and completed_at != _last_backup_notified_timestamp:
            _last_backup_notified_timestamp = completed_at
            size_mb = backup_item.get("processed_mb", backup_item.get("existing_backup_size_mb", 0.0))
            elapsed = backup_item.get("elapsed_seconds", 0)
            cloud_str = " (Sincronizzato su Cloud)" if backup_item.get("cloud_synced") else ""
            elapsed_fmt = f" in {elapsed}s" if elapsed > 0 else ""
            msg = f"Il backup del server Minecraft da {size_mb} MB è terminato con successo{elapsed_fmt}!{cloud_str}"
            if hasattr(manager, 'add_log'):
                manager.add_log(f"[Backup] {msg}")
            if hasattr(manager, 'send_push_notification'):
                manager.send_push_notification(
                    title="Backup Server Completato!",
                    message=msg,
                    priority="high",
                    tags="white_check_mark,package,backup"
                )
    except Exception:
        pass

backup_mgr.subscribe_progress(broadcast_backup_to_ws)

# Models
class CommandRequest(BaseModel):
    command: str

class TalkRequest(BaseModel):
    message: str
    sender: Optional[str] = "Host"

class StopRequest(BaseModel):
    force: bool = False
    reason: Optional[str] = "Chiusura manuale da pannello"

class RestartRequest(BaseModel):
    countdown: int = 0
    reason: Optional[str] = "Riavvio manuale da pannello"

class PostponeRequest(BaseModel):
    minutes: int = 30

class ToggleRequest(BaseModel):
    enabled: Optional[bool] = None

class ConfigUpdateRequest(BaseModel):
    server_dir: Optional[str] = None
    server_run_bat: Optional[str] = None
    server_command: Optional[str] = None
    server_port: Optional[int] = None
    web_port: Optional[int] = None
    scheduled_restart_enabled: Optional[bool] = None
    scheduled_restart_interval_minutes: Optional[int] = None
    auto_restart_crash: Optional[bool] = None
    crash_retry_delay_seconds: Optional[int] = None
    chat_warnings_enabled: Optional[bool] = None
    admin_pin: Optional[str] = None
    require_pin_for_remote: Optional[bool] = None
    push_topic: Optional[str] = None

class VerifyPinRequest(BaseModel):
    pin: str

class AutoStartRequest(BaseModel):
    enabled: bool

class PlayerActionRequest(BaseModel):
    player_name: Optional[str] = None
    player: Optional[str] = None
    reason: Optional[str] = "Azione amministrativa remota"

    @property
    def target_player(self) -> str:
        return (self.player_name or self.player or "").strip()

class PropertiesUpdateRequest(BaseModel):
    properties: Optional[Dict[str, Any]] = None
    raw: Optional[str] = None

class CloudSyncRequest(BaseModel):
    cloud_sync_dir: Optional[str] = None

class RestoreRequest(BaseModel):
    source_path: Optional[str] = None

class DeleteBackupRequest(BaseModel):
    id: Optional[str] = None
    path: Optional[str] = None

class QuickActionRequest(BaseModel):
    player_name: Optional[str] = None
    player: Optional[str] = None
    action: str
    value: Optional[str] = None

    @property
    def target_player(self) -> str:
        return (self.player_name or self.player or "").strip()

class JvmTuningRequest(BaseModel):
    ram_gb: int = 16
    use_aikar_flags: bool = True

class ForgeInstallRequest(BaseModel):
    target_dir: str
    server_type: str = "forge"  # "forge", "neoforge", "fabric", "paper", "vanilla"
    mc_version: str = "1.20.1"
    ram_gb: int = 8
    port: int = 32769

class SwitchInstanceRequest(BaseModel):
    instance_id: str

class AddInstanceRequest(BaseModel):
    name: Optional[str] = None
    server_dir: str
    server_run_bat: Optional[str] = ""
    server_command: Optional[str] = ""
    server_port: Optional[int] = None
    server_type: Optional[str] = None
    mc_version: Optional[str] = None
    set_active: Optional[bool] = False
    run_bat: Optional[str] = None
    launch_cmd: Optional[str] = None
    port: Optional[int] = None
    type: Optional[str] = None
    version: Optional[str] = None

class DetectInstanceRequest(BaseModel):
    folder_path: str

class UpdateInstanceRequest(BaseModel):
    instance_id: str
    name: Optional[str] = None
    server_dir: Optional[str] = None
    server_run_bat: Optional[str] = None
    server_command: Optional[str] = None
    server_port: Optional[int] = None
    server_type: Optional[str] = None
    mc_version: Optional[str] = None
    run_bat: Optional[str] = None
    launch_cmd: Optional[str] = None
    port: Optional[int] = None
    type: Optional[str] = None
    version: Optional[str] = None

class DeleteInstanceRequest(BaseModel):
    instance_id: str

class SaveFileRequest(BaseModel):
    path: Optional[str] = None
    subpath: Optional[str] = None
    content: str = ""

class CreateItemRequest(BaseModel):
    path: Optional[str] = None
    subpath: Optional[str] = None
    name: Optional[str] = None
    is_dir: bool = False

class DeleteItemRequest(BaseModel):
    path: Optional[str] = None
    subpath: Optional[str] = None

class RenameItemRequest(BaseModel):
    path: Optional[str] = None
    subpath: Optional[str] = None
    new_name: str

class UnzipItemRequest(BaseModel):
    path: Optional[str] = None
    subpath: Optional[str] = None

class InstallStoreRequest(BaseModel):
    project_id: str
    loader: Optional[str] = "forge"
    version: Optional[str] = "1.20.1"
    game_version: Optional[str] = None
    target_subdir: Optional[str] = ""
    project_type: Optional[str] = "mod"

class UninstallStoreRequest(BaseModel):
    project_id: str
    filename: Optional[str] = None

class UpdateStoreRequest(BaseModel):
    current_filename: Optional[str] = None
    project_id: Optional[str] = None
    folder: Optional[str] = "mods"
    new_filename: Optional[str] = None
    download_url: Optional[str] = None
    version_number: Optional[str] = None

valid_auth_tokens: set = set()

FORBIDDEN_ROOTS = [
    os.path.normpath(os.environ.get("SystemRoot", r"C:\Windows")).lower(),
    os.path.normpath(os.environ.get("ProgramFiles", r"C:\Program Files")).lower(),
    os.path.normpath(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")).lower(),
    os.path.normpath(os.environ.get("SystemDrive", "C:") + "\\").lower(),
]

# Su Linux: cartelle di sistema in cui non va mai messo un server
LINUX_SYSTEM_DIRS = ("/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/proc", "/root", "/run", "/sbin", "/sys",
                     "/usr", "/var/lib", "/var/log")

def is_safe_server_path(p: Optional[str]) -> bool:
    if not p:
        return True
    if ".." in p:
        return False
    norm = os.path.normpath(os.path.abspath(p)).lower()
    for fb in FORBIDDEN_ROOTS:
        if norm == fb or norm == fb + "\\":
            return False
    win_dir = os.environ.get("SystemRoot", r"C:\Windows").lower()
    if norm.startswith(win_dir):
        return False
    if sys.platform != "win32":
        real = os.path.normpath(os.path.abspath(p))
        if real == "/" or any(real == d or real.startswith(d + "/") for d in LINUX_SYSTEM_DIRS):
            return False
    return True

DEFAULT_PIN_INTERNET_MSG = ("PIN predefinito (1234): l'accesso da internet è bloccato. "
                          "Cambia il PIN nelle Impostazioni del pannello sul PC Host (in casa funziona comunque).")

def pin_is_default() -> bool:
    return is_default_pin(manager.config.get("admin_pin", "1234"))

def is_internet_request(conn) -> bool:
    """Richiesta arrivata da internet: è entrata dalla porta HTTPS aperta sul router (remote_port).
    La porta del pannello (web_port) non è mai aperta sul router, quindi lì arriva solo la rete di casa."""
    server = conn.scope.get("server") or (None, None)
    return server[1] == REMOTE_PORT

CROSS_SITE_MSG = "Richiesta rifiutata: proviene da un altro sito web."

def is_cross_site_request(headers, is_api: bool = True) -> bool:
    """Richiesta partita da una pagina di un altro sito (es. un sito malevolo aperto nel browser del PC Host).
    Pannello web e finestra del programma sono sullo stesso indirizzo del backend; l'APK e i programmi non
    inviano Origin. Senza questo controllo qualunque sito potrebbe comandare il pannello da localhost senza PIN."""
    origin = (headers.get("origin") or "").strip().lower()
    if origin:
        host = (headers.get("host") or "").strip().lower()
        return origin == "null" or not host or urlsplit(origin).netloc != host
    # Browser senza Origin (es. GET): vale l'intestazione Sec-Fetch-Site. Aprire un link al pannello resta permesso.
    if (headers.get("sec-fetch-site") or "").lower() == "cross-site":
        return is_api and (headers.get("sec-fetch-mode") or "").lower() != "navigate"
    return False

def verify_admin_access(request: Request):
    # Con il PIN predefinito nessun accesso da internet, neanche con un token ottenuto prima in casa
    if is_internet_request(request):
        # Da internet il PIN serve sempre, anche se "Richiedi PIN" è disattivato (vale solo per la rete di casa)
        if pin_is_default():
            raise HTTPException(status_code=403, detail=DEFAULT_PIN_INTERNET_MSG)
    elif is_local_request(request) or not manager.config.get("require_pin_for_remote", True):
        return True
    token = request.headers.get("X-Admin-Token")
    if token and token in valid_auth_tokens:
        return True
    raise HTTPException(status_code=401, detail="Accesso non autorizzato: inserisci il PIN.")

def is_local_request(request: Request) -> bool:
    """Returns True ONLY if request is originating directly from localhost on the host PC itself."""
    if not request:
        return False
    if is_internet_request(request):
        return False
    # Check headers added by reverse proxies
    x_forwarded_for = request.headers.get("x-forwarded-for")
    x_forwarded_host = (request.headers.get("x-forwarded-host") or "").split(":")[0].lower()
    host_hdr = (request.headers.get("host") or "").split(":")[0].lower()

    if x_forwarded_host and x_forwarded_host not in ("localhost", "127.0.0.1", "::1"):
        return False
    if host_hdr and host_hdr not in ("localhost", "127.0.0.1", "::1"):
        return False
    if x_forwarded_for:
        first_ip = x_forwarded_for.split(",")[0].strip()
        if first_ip not in ("127.0.0.1", "::1", "localhost", "testclient"):
            return False

    client_ip = request.client.host if request.client else ""
    return client_ip in ("127.0.0.1", "::1", "localhost", "testclient")

def verify_host_only_access(request: Request):
    """Enforces that an action is strictly performed by a local client on the host PC."""
    if not is_local_request(request):
        raise HTTPException(
            status_code=403,
            detail="Questa operazione è disponibile esclusivamente sul computer host."
        )

# Porta di Minecraft sul router: prima si apriva una sola volta all'avvio del programma, quindi dopo un cambio di porta,
# un server collegato dopo l'avvio o un riavvio del router restava chiusa (e l'IP mostrato era quello vecchio)
MC_PORT_REOPEN_SECONDS = 600     # riapertura periodica: i router perdono le mappature quando si riavviano
MC_PORT_RETRY_SECONDS = 120      # dopo un errore si riprova più tardi, non a ogni giro
_mc_port_state: Dict[str, Any] = {"port": None, "last_open": 0.0, "retry_at": 0.0, "active": None}
_mc_port_lock = threading.Lock()
_mc_port_wakeup = threading.Event()


def broadcast_network_update():
    """Ricalcola indirizzi e stato UPnP e li manda subito a tutti i pannelli aperti."""
    global _cached_network_payload
    _cached_network_payload = None
    if loop and active_websockets:
        asyncio.run_coroutine_threadsafe(_async_broadcast({"type": "network", "data": build_network_payload()}), loop)


def sync_minecraft_port(force: bool = False) -> Optional[Dict[str, Any]]:
    """Apre sul router la porta del server scelto quando serve e avvisa i pannelli se porta o stato cambiano."""
    with _mc_port_lock:
        if not manager.config.get("server_dir"):
            return None
        port = manager.get_server_port()
        now = time.time()
        st = _mc_port_state
        changed = port != st["port"]
        if changed:
            manager.remember_server_port(port)   # es. porta cambiata nell'editor di server.properties
        res = None
        due = now - st["last_open"] > MC_PORT_REOPEN_SECONDS and now >= st["retry_at"]
        if changed or force or due:
            res = upnp_mgr.open_minecraft_port(port, avoid={remote_access.external_port, remote_access.port})
            st["last_open"] = now
            st["retry_at"] = 0.0 if res.get("success") else now + MC_PORT_RETRY_SECONDS
            if not res.get("success"):
                st["last_open"] = 0.0
                print(f"[UPnP] Porta Minecraft {port} non aperta: {res.get('message')}")
        st["port"] = port
        active = upnp_mgr.get_status(port).get("active")
        notify = changed or force or active != st["active"]
        st["active"] = active
    if notify:
        broadcast_network_update()
    return res


def request_minecraft_port_sync():
    """Chiede al controllo della porta di girare subito (dopo un cambio di porta o di server)."""
    _mc_port_state["retry_at"] = 0.0
    _mc_port_wakeup.set()


def _minecraft_port_watcher():
    while True:
        try:
            forced = _mc_port_wakeup.is_set()
            _mc_port_wakeup.clear()
            sync_minecraft_port(force=forced)
        except Exception as e:
            print(f"[UPnP] Errore controllo porta Minecraft: {e}")
        _mc_port_wakeup.wait(5.0)


# Background task to stream status to all connected WebSockets every second
async def status_broadcast_loop():
    """Broadcasts status to WebSocket clients. Full status every 2s instead of 1s."""
    while True:
        try:
            await asyncio.sleep(2.0)
            if active_websockets:
                status = manager.get_full_status()
                await _async_broadcast({"type": "status", "data": status})
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(2.0)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global loop
    loop = asyncio.get_running_loop()

    # Silenzia le disconnessioni forzate del client su Windows (WinError 10054 ConnectionResetError)
    def _silence_proactor_reset(lp, context):
        exc = context.get("exception")
        if isinstance(exc, ConnectionResetError) or (isinstance(exc, OSError) and getattr(exc, "winerror", None) == 10054):
            return
        lp.default_exception_handler(context)

    loop.set_exception_handler(_silence_proactor_reset)
    threading.Thread(target=get_public_ip, daemon=True).start()
    remote_access.start_watcher()
    try:
        _sync_config_with_active_instance()
    except Exception as e:
        print(f"[Istanze] Riallineamento della configurazione non riuscito: {e}")
    threading.Thread(target=_minecraft_port_watcher, daemon=True, name="McPortWatcher").start()
    broadcast_task = asyncio.create_task(status_broadcast_loop())

    # Start automated version watcher across all 5 loaders (Forge, NeoForge, Fabric, Paper, Vanilla)
    def on_new_versions_detected(info: dict):
        if loop and active_websockets:
            asyncio.run_coroutine_threadsafe(
                _async_broadcast({
                    "type": "versions_updated",
                    "data": info
                }),
                loop
            )
            new_list = info.get("new_versions", [])
            if new_list:
                summary_names = ", ".join([f"{nv['loader'].capitalize()} {nv['version']}" for nv in new_list[:3]])
                asyncio.run_coroutine_threadsafe(
                    _async_broadcast({
                        "type": "toast_info",
                        "message": f"Nuove versioni rilevate e aggiunte all'elenco: {summary_names}!"
                    }),
                    loop
                )

    forge_installer.start_version_watcher(interval_seconds=1200, callback=on_new_versions_detected)

    # Start automated continuous watcher for installed mod updates
    def on_mod_updates_detected(info: dict):
        if loop and active_websockets:
            asyncio.run_coroutine_threadsafe(
                _async_broadcast({
                    "type": "mod_updates_detected",
                    "data": info
                }),
                loop
            )
            cnt = info.get("updates_count", 0)
            if cnt > 0:
                asyncio.run_coroutine_threadsafe(
                    _async_broadcast({
                        "type": "toast_info",
                        "message": f"Rilevati {cnt} aggiornamenti disponibili per le mod installate!"
                    }),
                    loop
                )

    modrinth_store.start_mod_updates_watcher(
        get_server_dir_cb=get_server_dir_path,
        get_server_info_cb=instance_manager.get_active_instance,
        interval_seconds=900,
        callback=on_mod_updates_detected
    )

    yield
    broadcast_task.cancel()
    forge_installer.stop_version_watcher()
    modrinth_store.stop_mod_updates_watcher()
    remote_access.stop_watcher()
    try:
        upnp_mgr.close_port()
        upnp_mgr.close_port(remote_access.external_port)
    except Exception:
        pass

app = FastAPI(title="Voxarium", version=f"{PC_VERSION} (Build {PC_BUILD})", lifespan=lifespan)

@app.middleware("http")
async def block_cross_site_middleware(request: Request, call_next):
    # Nessun CORS: solo pagine servite da questo backend (stesso indirizzo) possono chiamare le API
    if is_cross_site_request(request.headers, request.scope.get("path", "").startswith("/api/")):
        return JSONResponse({"detail": CROSS_SITE_MSG}, status_code=403)
    return await call_next(request)

@app.middleware("http")
async def normalize_slashes_middleware(request: Request, call_next):
    raw_path = request.scope.get("path", "")
    if "//" in raw_path:
        request.scope["path"] = re.sub(r"/+", "/", raw_path)
    return await call_next(request)

# Routes
def _host_id() -> str:
    """Identificativo pubblico e stabile di questa installazione (non rivela il segreto da cui è derivato)."""
    import hashlib
    return hashlib.sha256(("host-id|" + str(manager.config.get("push_secret", ""))).encode("utf-8")).hexdigest()[:16]

# Percorsi del PC (con il nome utente di Windows) che gli indirizzi pubblici mostrano solo a chi ha accesso
PRIVATE_PATH_KEYS = ("server_dir", "target_path", "cloud_sync_dir")


def has_admin_access(request: Request) -> bool:
    try:
        return verify_admin_access(request) is not False
    except HTTPException:
        return False


def hide_private_paths(data: Dict[str, Any], request: Request) -> Dict[str, Any]:
    """Toglie i percorsi del PC dalle risposte leggibili senza PIN (anche da internet sulla porta HTTPS)."""
    if isinstance(data, dict) and not has_admin_access(request):
        data = {k: v for k, v in data.items() if k not in PRIVATE_PATH_KEYS}
    return data


@app.get("/api/status")
def get_status(request: Request):
    st = manager.get_full_status()
    st["is_local_client"] = is_local_request(request)
    st["host_id"] = _host_id()
    return hide_private_paths(st, request)

@app.get("/api/discovery")
def get_discovery(request: Request):
    """Per i PC in modalità Remoto: topic ntfy di questo Host, dato solo a chi è nella stessa rete locale
    (mai da internet), così da fuori casa possono ritrovare il nuovo indirizzo e riconoscere il certificato HTTPS."""
    import ipaddress
    client_ip = request.client.host if request.client else ""
    try:
        is_private = ipaddress.ip_address(client_ip).is_private
    except ValueError:
        is_private = False
    if is_internet_request(request) or request.headers.get("x-forwarded-for") or not is_private:
        raise HTTPException(status_code=403, detail="Disponibile solo dalla rete locale dell'Host.")
    return {"push_topic": manager.config.get("push_topic", ""), "host_id": _host_id(),
            "cert_fingerprint": remote_access.fingerprint, "remote_url": remote_access.url}

_cached_network_payload = None
_cached_network_payload_time = 0

def build_network_payload() -> Dict[str, Any]:
    global _cached_network_payload, _cached_network_payload_time
    import time as _time
    now = _time.time()
    local_ip = get_local_ip()
    pub_ip = get_public_ip()
    web_port = manager.config.get("web_port", 8080)
    mc_port = manager.get_server_port()
    lan_url = f"http://{local_ip}:{web_port}"

    r_status = remote_status_payload()

    upnp_status = upnp_mgr.get_status(mc_port)
    # Da fuori si entra dalla porta aperta sul router, che può essere diversa da quella del server
    public_mc_port = upnp_status.get("external_port") or mc_port
    direct_mc_addr = f"{pub_ip}:{public_mc_port}" if pub_ip else "--"
    active_mc_addr = direct_mc_addr

    apk_lan_url = f"{lan_url}/api/app/download"
    apk_remote_url = f"{r_status['url']}/api/app/download" if r_status.get("url") else None
    preferred_apk_url = apk_lan_url   # da internet il certificato è autofirmato: per installare l'APK è meglio la rete di casa

    if _cached_network_payload is not None and (now - _cached_network_payload_time) < 15:
        cached = dict(_cached_network_payload)
        cached["remote"] = r_status
        cached["upnp"] = upnp_status
        if not cached.get("qr_code"):
            cached["qr_code"] = generate_qr_data_uri(lan_url)
        cached["lan_qr"] = cached["qr_code"]
        cached["lan_url"] = lan_url
        cached["minecraft"] = {
            "port": mc_port,
            "public_port": public_mc_port,
            "connection_mode": "upnp",
            "direct_address": direct_mc_addr,
            "public_address": active_mc_addr,
            "lan_address": f"{local_ip}:{mc_port}",
            "local_address": f"localhost:{mc_port}",
            "is_static": True,
            "upnp": upnp_status
        }
        cached["apk"] = {
            "lan_url": apk_lan_url,
            "lan_qr": generate_qr_data_uri(apk_lan_url),
            "remote_url": apk_remote_url,
            "remote_qr": generate_qr_data_uri(apk_remote_url) if apk_remote_url else None,
            "preferred_url": preferred_apk_url,
            "preferred_qr": generate_qr_data_uri(preferred_apk_url)
        }
        return cached
    
    lan_qr = generate_qr_data_uri(lan_url)
    apk_lan_qr = generate_qr_data_uri(apk_lan_url)
    apk_remote_qr = generate_qr_data_uri(apk_remote_url) if apk_remote_url else None
    preferred_apk_qr = generate_qr_data_uri(preferred_apk_url)

    payload = {
        "hostname": socket.gethostname(),
        "local_ip": local_ip,
        "public_ip": pub_ip,
        "web_port": web_port,
        "local_url": f"http://localhost:{web_port}",
        "lan_url": lan_url,
        "qr_code": lan_qr,
        "lan_qr": lan_qr,
        "apk": {
            "lan_url": apk_lan_url,
            "lan_qr": apk_lan_qr,
            "remote_url": apk_remote_url,
            "remote_qr": apk_remote_qr,
            "preferred_url": preferred_apk_url,
            "preferred_qr": preferred_apk_qr
        },
        "minecraft": {
            "port": mc_port,
            "public_port": public_mc_port,
            "connection_mode": "upnp",
            "direct_address": direct_mc_addr,
            "public_address": active_mc_addr,
            "lan_address": f"{local_ip}:{mc_port}",
            "local_address": f"localhost:{mc_port}",
            "is_static": True,
            "upnp": upnp_status
        },
        "upnp": upnp_status,
        "remote": r_status
    }
    _cached_network_payload = payload
    _cached_network_payload_time = now
    return payload


@app.get("/api/network")
def get_network_info():
    return build_network_payload()

@app.get("/api/remote/status")
def get_remote_status():
    return remote_status_payload()

def _remote_changed_response() -> Dict[str, Any]:
    global _cached_network_payload
    _cached_network_payload = None
    st = remote_status_payload()
    if loop and active_websockets:
        asyncio.run_coroutine_threadsafe(_async_broadcast({"type": "remote", "data": st}), loop)
    return st

@app.post("/api/remote/start")
def start_remote(request: Request):
    """Apre (o riapre) la porta HTTPS sul router e rilegge l'IP pubblico."""
    verify_admin_access(request)
    remote_access.refresh(reopen=True)
    return _remote_changed_response()

@app.post("/api/remote/stop")
def stop_remote(request: Request):
    """Chiude la porta HTTPS sul router: niente accesso da internet finché non si riapre."""
    verify_admin_access(request)
    remote_access.close()
    return _remote_changed_response()

class OpenUrlRequest(BaseModel):
    url: str

@app.post("/api/open-browser")
def api_open_browser(req: OpenUrlRequest, request: Request):
    verify_admin_access(request)
    url = req.url.strip()
    if url.startswith("http://") or url.startswith("https://"):
        try:
            import webbrowser
            webbrowser.open(url)
            return {"status": "success", "url": url}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    return {"status": "error", "message": "Indirizzo non valido"}

# UPnP Automatic Router Port Forwarding Routes
@app.get("/api/upnp/status")
def get_upnp_status():
    mc_port = manager.get_server_port()
    return upnp_mgr.get_status(mc_port)

@app.post("/api/upnp/refresh")
def refresh_upnp_port(request: Request):
    verify_admin_access(request)
    if not manager.config.get("server_dir"):
        return {"success": False, "message": "Nessun server collegato: non c'è una porta da aprire."}
    # Stessa apertura del controllo automatico (con le porte di riserva se il router rifiuta quella del server)
    res = sync_minecraft_port(force=True) or {"success": False, "message": "Porta non aperta"}
    return res

# Auth Routes
_auth_failed_attempts: Dict[str, List[tuple]] = {}   # IP -> [(ora, impronta del PIN sbagliato)]
AUTH_LOCK_SECONDS = 600
AUTH_MAX_WRONG_PINS = 5
_auth_lock = threading.Lock()

@app.post("/api/auth/verify")
def verify_pin(req: VerifyPinRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"   # senza tunnel è l'IP reale del dispositivo
    now = time.time()
    with _auth_lock:
        # Tentativi sbagliati degli ultimi 10 minuti: (ora, impronta del PIN). Contano solo i PIN DIVERSI: l'app che
        # ritenta da sola il vecchio PIN salvato non deve far bloccare il telefono (non è chi prova a indovinare)
        attempts = [a for a in _auth_failed_attempts.get(client_ip, []) if now - a[0] < AUTH_LOCK_SECONDS]
        _auth_failed_attempts[client_ip] = attempts
        if len({a[1] for a in attempts}) >= AUTH_MAX_WRONG_PINS:
            retry_after = int(AUTH_LOCK_SECONDS - (now - min(a[0] for a in attempts))) + 1
            mins, secs = divmod(retry_after, 60)
            when = (f"{mins} minut{'o' if mins == 1 else 'i'}" + (f" e {secs} secondi" if secs else "")) if mins else f"{secs} secondi"
            # retry_after: secondi che mancano, per l'app e il pannello (prima diceva sempre "10 minuti")
            return JSONResponse(status_code=429, headers={"Retry-After": str(retry_after)}, content={
                "detail": f"Troppi PIN errati: accesso bloccato. Riprova tra {when}.",
                "retry_after": retry_after,
            })
    if is_internet_request(request) and pin_is_default():
        raise HTTPException(status_code=403, detail=DEFAULT_PIN_INTERNET_MSG)

    expected = str(manager.config.get("admin_pin", "1234")).strip()
    if req.pin and req.pin.strip() == expected:
        with _auth_lock:
            _auth_failed_attempts.pop(client_ip, None)
        token = str(uuid.uuid4())
        valid_auth_tokens.add(token)
        return {
            "status": "success",
            "token": token,
            "push_secret": manager.config.get("push_secret"),
            "push_topic": manager.config.get("push_topic"),
            "cert_fingerprint": remote_access.fingerprint,
            "remote_url": remote_access.url,
            "message": "Accesso autorizzato."
        }

    with _auth_lock:
        pin_mark = hashlib.sha256(str(req.pin or "").strip().encode("utf-8")).hexdigest()[:16]
        _auth_failed_attempts.setdefault(client_ip, []).append((now, pin_mark))
    raise HTTPException(status_code=403, detail="PIN di sicurezza non corretto.")

@app.post("/api/server/start")
def start_server(request: Request):
    verify_admin_access(request)
    res = manager.start_server()
    if res.get("status") == "error":
        # Errore vero (409): l'app del telefono altrimenti mostrava "Comando di avvio accettato"
        raise HTTPException(status_code=409, detail=res.get("message"))
    return res

@app.post("/api/server/stop")
def stop_server(req: StopRequest, request: Request):
    verify_admin_access(request)
    return manager.stop_server(force=req.force, reason=req.reason)

@app.post("/api/server/kill")
def kill_server(request: Request):
    verify_admin_access(request)
    return manager.stop_server(force=True, reason="Arresto forzato (kill) immediato da pannello")

@app.post("/api/server/restart")
def restart_server(req: RestartRequest, request: Request):
    verify_admin_access(request)
    blocked = backup_mgr.busy_message()
    if blocked:
        raise HTTPException(status_code=409, detail=blocked)
    return manager.restart_server(reason=req.reason, warning_countdown=req.countdown)

@app.post("/api/server/command")
def execute_command(req: CommandRequest, request: Request):
    verify_admin_access(request)
    if not req.command:
        raise HTTPException(status_code=400, detail="Comando vuoto")
    return manager.send_command(req.command)

# Chat & Talk Routes ("PARLARCI DENTRO")
@app.get("/api/chat")
def get_chat(request: Request, format: Optional[str] = None):
    verify_admin_access(request)
    with manager.lock:
        msgs = list(manager.chat_messages)
        if format in ("list", "raw"):
            return msgs
        return {"messages": msgs, "chat": msgs, "count": len(msgs)}

@app.post("/api/chat/talk")
def talk_to_server(req: TalkRequest, request: Request):
    verify_admin_access(request)
    return manager.talk_to_server(req.message, req.sender or "Host")

@app.post("/api/chat/clear")
def clear_chat_history(request: Request):
    verify_admin_access(request)
    return manager.clear_chat()

# Error & Diagnostics Routes ("MOSTRA GLI ERRORI")
@app.get("/api/errors")
def get_errors(request: Request):
    verify_admin_access(request)
    with manager.lock:
        return {
            "errors": list(manager.errors_list),
            "crash_reports": manager.scan_crash_reports()
        }

@app.post("/api/errors/clear")
def clear_errors(request: Request):
    verify_admin_access(request)
    manager.clear_errors()
    return {"status": "success", "message": "Errori ripuliti."}

@app.get("/api/crash-report/{filename}")
def get_crash_report(filename: str, request: Request):
    verify_admin_access(request)
    server_dir = manager.config.get("server_dir", "")
    safe_name = os.path.basename(filename)
    if not safe_name.endswith(".txt"):
        raise HTTPException(status_code=400, detail="Formato file non supportato")
    file_path = os.path.join(server_dir, "crash-reports", safe_name)
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read(1000000) # up to 1 MB
        return PlainTextResponse(content)
    raise HTTPException(status_code=404, detail="File non trovato")

@app.delete("/api/crash-report/{filename}")
def delete_crash_report(filename: str, request: Request):
    verify_admin_access(request)
    server_dir = manager.config.get("server_dir", "")
    safe_name = os.path.basename(filename)
    if not safe_name.endswith(".txt"):
        raise HTTPException(status_code=400, detail="Nome file non valido")
    file_path = os.path.join(server_dir, "crash-reports", safe_name)
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
            return {"status": "success", "message": f"Crash report {safe_name} eliminato."}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Errore durante l'eliminazione: {e}")
    raise HTTPException(status_code=404, detail="File non trovato")

@app.post("/api/crash-reports/open_folder")
def api_open_crash_folder(request: Request):
    verify_admin_access(request)
    verify_host_only_access(request)
    s_dir = manager.config.get("server_dir", "")
    crash_dir = os.path.join(s_dir, "crash-reports")
    os.makedirs(crash_dir, exist_ok=True)
    try:
        open_directory_in_explorer(crash_dir)
        return {"status": "success", "folder": crash_dir}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Impossibile aprire la cartella: {e}")

@app.delete("/api/crash-reports/clear-all")
def api_clear_all_crash_reports(request: Request):
    verify_admin_access(request)
    s_dir = manager.config.get("server_dir", "")
    crash_dir = os.path.join(s_dir, "crash-reports")
    if not os.path.exists(crash_dir):
        return {"status": "success", "deleted_count": 0}
    count = 0
    for f in os.listdir(crash_dir):
        if f.endswith(".txt"):
            try:
                os.remove(os.path.join(crash_dir, f))
                count += 1
            except Exception:
                pass
    return {"status": "success", "deleted_count": count}

def _broadcast_status_now():
    if loop and active_websockets:
        st = manager.get_full_status()
        asyncio.run_coroutine_threadsafe(_async_broadcast({"type": "status", "data": st}), loop)

# 4-Hour Scheduled Timer Routes
@app.post("/api/timer/postpone")
def postpone_timer(req: PostponeRequest, request: Request):
    verify_admin_access(request)
    res = manager.postpone_scheduled_restart(minutes=req.minutes)
    _broadcast_status_now()
    return res

@app.post("/api/timer/reset")
def reset_timer(request: Request):
    verify_admin_access(request)
    res = manager.reset_scheduled_timer()
    _broadcast_status_now()
    return res

@app.post("/api/timer/toggle")
def toggle_timer(req: ToggleRequest, request: Request):
    verify_admin_access(request)
    res = manager.toggle_scheduled_restart(enabled=req.enabled)
    _broadcast_status_now()
    return res

# Crash Protection Routes
@app.post("/api/crash/toggle")
def toggle_crash(req: ToggleRequest, request: Request):
    verify_admin_access(request)
    res = manager.toggle_auto_restart_crash(enabled=req.enabled)
    _broadcast_status_now()
    return res

@app.post("/api/crash/reset-lock")
def reset_crash_lock(request: Request):
    verify_admin_access(request)
    res = manager.reset_crash_paused()
    _broadcast_status_now()
    return res

# Logs Routes
@app.get("/api/logs")
def get_logs(request: Request, tail: int = 300, format: Optional[str] = None, since: Optional[int] = None):
    verify_admin_access(request)
    if since is not None:
        # Pannello senza WebSocket: solo le righe nuove (prima la console restava ferma fino al riavvio del programma)
        return manager.logs_since(since, tail)
    with manager.lock:
        lines = list(manager.log_buffer)
        if tail > 0 and len(lines) > tail:
            lines = lines[-tail:]
        if format in ("list", "raw"):
            return lines
        return {"lines": lines, "logs": lines, "count": len(lines)}

@app.post("/api/logs/clear")
def clear_logs(request: Request):
    verify_admin_access(request)
    with manager.lock:
        manager.log_buffer.clear()
        manager.add_log("[Pannello] Log della console svuotati dall'utente.")
    return {"status": "success", "message": "Console ripulita."}

# PC Program Updates
@app.get("/api/pc-update/status")
def api_pc_update_status():
    return pc_updater.status()

@app.post("/api/pc-update/check")
def api_pc_update_check(request: Request):
    verify_admin_access(request)
    return pc_updater.check_for_update(force=True)

@app.post("/api/pc-update/install")
def api_pc_update_install(request: Request):
    verify_admin_access(request)
    verify_host_only_access(request)
    if manager.state != "STOPPED":
        raise HTTPException(status_code=409, detail="Spegni il server Minecraft prima di aggiornare il programma.")
    if backup_mgr.is_running:
        raise HTTPException(status_code=409, detail="Attendi la fine del backup o del ripristino in corso.")
    res = pc_updater.download_and_run_installer()
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message"))
    return res

# App OTA Updates
@app.get("/api/app/version")
def get_app_version():
    v_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "version.json")
    data = None
    if os.path.exists(v_file):
        try:
            with open(v_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
    if not data:
        # Niente versione inventata: l'app mostra "impossibile verificare gli aggiornamenti" invece di un annuncio sbagliato
        raise HTTPException(status_code=404, detail="version.json dell'app non trovato")
    return data

@app.get("/api/app/download")
@app.get("/download/apk")
def download_app_apk():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(base_dir, "static", "Controllo_Server_Minecraft.apk"),
        os.path.join(base_dir, "android_project", "Controllo_Server_Minecraft.apk"),
        os.path.join(base_dir, "android_project", "app", "build", "outputs", "apk", "release", "app-release.apk"),
    ]
    apk_path = None
    for cand in candidates:
        if os.path.exists(cand):
            apk_path = cand
            break

    if not apk_path:
        raise HTTPException(status_code=404, detail="File APK non ancora generato sul PC")
    return FileResponse(
        apk_path,
        media_type="application/vnd.android.package-archive",
        filename="Controllo_Server_Minecraft.apk"
    )


SECRET_CONFIG_KEYS = ("admin_pin", "push_secret", "install_secret")

# Config Routes
@app.get("/api/config")
def get_config(request: Request):
    # Percorsi e topic ntfy non vanno mostrati a chi non ha fatto l'accesso
    verify_admin_access(request)
    cfg = {k: v for k, v in manager.config.items() if k not in SECRET_CONFIG_KEYS}
    cfg["admin_pin_is_default"] = pin_is_default()
    cfg["admin_pin_set"] = bool(manager.config.get("admin_pin"))
    # Porta del server scelto (server.properties); senza server nessuna porta predefinita da mostrare
    server_dir = manager.config.get("server_dir", "")
    cfg["server_port"] = manager.get_server_port() if server_dir and os.path.isdir(server_dir) else None
    return cfg

@app.post("/api/config")
def update_config(req: ConfigUpdateRequest, request: Request):
    verify_admin_access(request)
    with manager.lock:
        if req.server_dir is not None:
            if not is_safe_server_path(req.server_dir):
                raise HTTPException(status_code=400, detail="Percorso cartella server non consentito per la sicurezza del sistema.")
            backup_mgr.server_dir = os.path.abspath(req.server_dir)
        if req.server_run_bat is not None:
            if not is_safe_server_path(req.server_run_bat):
                raise HTTPException(status_code=400, detail="Percorso file di avvio non consentito per la sicurezza del sistema.")
        current = manager.config
        update_data = req.dict(exclude_none=True)
        # Se admin_pin è vuoto o composto solo da spazi, mantieni il PIN attuale invece di sovrascriverlo
        if "admin_pin" in update_data and not str(update_data["admin_pin"]).strip():
            update_data.pop("admin_pin", None)
        if "admin_pin" in update_data:
            pin_error = validate_new_pin(str(update_data["admin_pin"]))
            if pin_error:
                raise HTTPException(status_code=400, detail=pin_error)
            update_data["admin_pin"] = str(update_data["admin_pin"]).strip()
        # Topic ntfy: solo valori validi e mai il vecchio topic condiviso tra tutte le installazioni
        topic = str(update_data.get("push_topic", "")).strip()
        if "push_topic" in update_data and (not re.match(r"^[A-Za-z0-9_-]{8,64}$", topic) or is_legacy_topic(topic)):
            update_data.pop("push_topic", None)
        # La porta si scrive in server.properties (è quella che usa Minecraft): prima cambiava solo config.json
        new_port = update_data.pop("server_port", None)
        if new_port is not None and not (1024 <= int(new_port) <= 65535):
            raise HTTPException(status_code=400, detail="Porta non valida: scegli un numero tra 1024 e 65535.")
        old_dir = current.get("server_dir", "")
        for k, v in update_data.items():
            current[k] = v
        manager.save_config(current)
        port_res = None
        if new_port is not None and current.get("server_dir") and int(new_port) != manager.get_server_port():
            port_res = manager.set_server_port(int(new_port))
            if port_res.get("status") == "error":
                raise HTTPException(status_code=400, detail=port_res.get("message"))
        if req.scheduled_restart_interval_minutes is not None:
            manager.set_scheduled_interval(req.scheduled_restart_interval_minutes)
        if req.scheduled_restart_enabled is not None:
            manager.scheduled_enabled = req.scheduled_restart_enabled
        if req.auto_restart_crash is not None:
            manager.auto_restart_on_crash = req.auto_restart_crash
    if port_res or current.get("server_dir", "") != old_dir:
        request_minecraft_port_sync()   # router e indirizzo da copiare aggiornati subito, senza riavviare il programma
    safe_cfg = {k: v for k, v in manager.config.items() if k not in SECRET_CONFIG_KEYS}
    safe_cfg["admin_pin_is_default"] = pin_is_default()
    safe_cfg["admin_pin_set"] = bool(manager.config.get("admin_pin"))
    safe_cfg["server_port"] = manager.get_server_port() if manager.config.get("server_dir") else None
    return {"status": "success", "config": safe_cfg,
            "port_restart_needed": bool(port_res and port_res.get("restart_needed"))}

# Windows Auto-Start Management
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_RUN_NAME = "ControlloServerMinecraft"
# Collegamenti di avvio creati dalle versioni precedenti (prima e dopo il nome Voxarium)
STARTUP_SHORTCUT_NAMES = ("Controllo Server Minecraft.lnk", "Controllo Server Minecraft (Remoto).lnk",
                          "Voxarium.lnk", "Voxarium (Remoto).lnk")

LINUX_SERVICE = "voxarium.service"

def _linux_systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args, LINUX_SERVICE], capture_output=True, text=True,
                          timeout=15, creationflags=0)

def get_windows_autostart() -> bool:
    if sys.platform != "win32":
        try:
            return _linux_systemctl("is-enabled").stdout.strip() == "enabled"
        except Exception:
            return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, APP_RUN_NAME)
            return bool(val)
    except Exception:
        return False

def set_windows_autostart(enable: bool) -> bool:
    if sys.platform != "win32":
        # Il servizio c'è solo se il programma è stato installato con install.sh
        try:
            return _linux_systemctl("enable" if enable else "disable").returncode == 0
        except Exception as e:
            print(f"Avvio automatico (systemd): {e}")
            return False
    try:
        import winreg
        app_dir = os.path.dirname(os.path.abspath(__file__))
        exe_path = os.path.join(app_dir, "Controllo Server Minecraft.exe")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                cmd = f'"{exe_path}"' if os.path.exists(exe_path) else f'"{os.path.join(app_dir, "Avvia_Pannello_Minecraft.bat")}"'
                winreg.SetValueEx(key, APP_RUN_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, APP_RUN_NAME)
                except Exception:
                    pass
        # L'avvio automatico usa solo la voce del registro: i collegamenti nella cartella Esecuzione automatica
        # (vecchio nome e "Voxarium") si tolgono sempre, altrimenti con l'avvio spento il programma partiva lo stesso
        try:
            appdata = os.environ.get("APPDATA", "")
            if appdata:
                startup_dir = os.path.join(appdata, r"Microsoft\Windows\Start Menu\Programs\Startup")
                for name in STARTUP_SHORTCUT_NAMES:
                    startup_lnk = os.path.join(startup_dir, name)
                    if os.path.exists(startup_lnk):
                        os.remove(startup_lnk)
        except Exception as e:
            print(f"Avvio automatico: collegamento in Esecuzione automatica non rimosso: {e}")
        return True
    except Exception as e:
        print(f"Error setting autostart: {e}")
        return False

@app.get("/api/autostart")
def api_get_autostart():
    return {"autostart": get_windows_autostart()}

@app.post("/api/autostart")
def api_set_autostart(req: AutoStartRequest, request: Request):
    verify_admin_access(request)
    success = set_windows_autostart(req.enabled)
    return {"status": "success" if success else "error", "autostart": get_windows_autostart()}

# Backup Routes
@app.get("/api/backup/status")
def get_backup_status(request: Request):
    return hide_private_paths(backup_mgr.get_status(), request)

@app.get("/api/backup/estimate")
def api_backup_estimate(request: Request):
    """Quanto peserà il backup (tutta la cartella del server) e se ci sta sul Desktop e su Google Drive."""
    verify_admin_access(request)
    res = backup_mgr.estimate_backup()
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message"))
    return res

@app.post("/api/backup/start")
async def start_backup(request: Request):
    verify_admin_access(request)
    is_stopped = (manager.state in ("STOPPED", "OFFLINE"))
    copy_cloud = False
    try:
        body = await request.json()
        if isinstance(body, dict) and "copy_to_cloud" in body:
            copy_cloud = bool(body["copy_to_cloud"])
    except Exception:
        pass
    # Sempre tutta la cartella del server (scelta dell'utente del 26/10/2026): exclude_lod_cache delle versioni
    # precedenti del pannello e dell'app non viene più considerato
    res = backup_mgr.start_backup(is_server_stopped=is_stopped, exclude_lod_cache=False, copy_to_cloud=copy_cloud)
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message"))
    return res

@app.post("/api/backup/cancel")
def cancel_backup(request: Request):
    verify_admin_access(request)
    return backup_mgr.cancel_backup(force=False)

@app.post("/api/backup/force-cancel")
def force_cancel_backup(request: Request):
    verify_admin_access(request)
    return backup_mgr.cancel_backup(force=True)

@app.get("/api/backup/download")
def download_backup_zip(request: Request):
    verify_admin_access(request)
    path = backup_mgr.target_path
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Nessun file di backup trovato sul Desktop. Avvia prima la creazione del backup.")
    filename = os.path.basename(path)
    return FileResponse(
        path,
        media_type="application/zip",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

@app.get("/api/backup/download-item")
def download_backup_item(request: Request, id: str = "desktop"):
    verify_admin_access(request)
    backups = backup_mgr.list_available_backups()
    for b in backups:
        if b.get("id") == id or b.get("filename") == id:
            p = b.get("path")
            if p and os.path.exists(p):
                return FileResponse(
                    p,
                    media_type="application/zip",
                    filename=os.path.basename(p),
                    headers={"Content-Disposition": f'attachment; filename="{os.path.basename(p)}"'}
                )
    if os.path.exists(backup_mgr.target_path):
        return FileResponse(
            backup_mgr.target_path,
            media_type="application/zip",
            filename=backup_mgr.target_filename,
            headers={"Content-Disposition": f'attachment; filename="{backup_mgr.target_filename}"'}
        )
    raise HTTPException(status_code=404, detail="File di backup non trovato.")

# =========================================================
# BACKUP RESTORE, STORAGE CLEANER & TUNING ROUTES
# =========================================================
@app.get("/api/backup/list")
def api_get_backup_list(request: Request):
    verify_admin_access(request)
    return {
        "status": "success",
        "backups": backup_mgr.list_available_backups(),
        "cloud_check": backup_mgr.cloud_backup_check(),
    }

@app.post("/api/backup/restore")
def api_backup_restore(req: RestoreRequest, request: Request):
    verify_admin_access(request)
    is_stopped = (manager.state == "STOPPED")
    res = backup_mgr.start_restore(is_server_stopped=is_stopped, source_path=req.source_path)
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message"))
    return res

@app.post("/api/backup/delete")
def api_delete_backup(req: DeleteBackupRequest, request: Request):
    verify_admin_access(request)
    target = req.path or req.id or ""
    res = backup_mgr.delete_backup(target)
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message"))
    return res

@app.get("/api/storage/breakdown")
def api_get_storage(request: Request):
    return hide_private_paths(manager.get_storage_breakdown(), request)

@app.post("/api/storage/clean-logs")
def api_clean_logs(request: Request):
    verify_admin_access(request)
    return manager.clean_old_logs()

@app.post("/api/player/quick-action")
def api_player_quick_action(req: QuickActionRequest, request: Request):
    verify_admin_access(request)
    target = req.target_player
    if not target:
        raise HTTPException(status_code=400, detail="Nome giocatore non specificato")
    res = manager.player_quick_action(target, req.action, req.value)
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message"))
    return res

@app.get("/api/server/jvm-tuning")
def api_get_jvm_tuning(request: Request):
    verify_admin_access(request)   # contiene la riga di avvio del server (percorsi del PC)
    return manager.get_jvm_tuning()

@app.post("/api/server/jvm-tuning")
def api_save_jvm_tuning(req: JvmTuningRequest, request: Request):
    verify_admin_access(request)
    res = manager.save_jvm_tuning(req.ram_gb, req.use_aikar_flags)
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message"))
    return res

@app.post("/api/mods/open_folder")
def api_open_mods_folder(request: Request):
    verify_admin_access(request)
    verify_host_only_access(request)
    mods_dir = get_mods_dir()
    os.makedirs(mods_dir, exist_ok=True)
    try:
        open_directory_in_explorer(mods_dir)
        return {"status": "success", "folder": mods_dir}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Impossibile aprire la cartella: {e}")

@app.post("/api/server/open_folder")
def api_open_server_folder(request: Request):
    verify_admin_access(request)
    verify_host_only_access(request)
    s_dir = manager.config.get("server_dir", "")
    os.makedirs(s_dir, exist_ok=True)
    try:
        open_directory_in_explorer(s_dir)
        return {"status": "success", "folder": s_dir}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Impossibile aprire la cartella: {e}")

@app.post("/api/backup/open_folder")
def api_open_backup_folder(request: Request):
    verify_admin_access(request)
    verify_host_only_access(request)
    b_dir = os.path.dirname(backup_mgr.target_path) if hasattr(backup_mgr, 'target_path') and backup_mgr.target_path else os.path.join(os.path.expanduser("~"), "Desktop")
    os.makedirs(b_dir, exist_ok=True)
    try:
        open_directory_in_explorer(b_dir)
        return {"status": "success", "folder": b_dir}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Impossibile aprire la cartella: {e}")

@app.post("/api/logs/open_folder")
def api_open_logs_folder(request: Request):
    verify_admin_access(request)
    verify_host_only_access(request)
    s_dir = manager.config.get("server_dir", "")
    logs_dir = os.path.join(s_dir, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    try:
        open_directory_in_explorer(logs_dir)
        return {"status": "success", "folder": logs_dir}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Impossibile aprire la cartella logs: {e}")

@app.post("/api/program/open_folder")
def api_open_program_folder(request: Request):
    verify_admin_access(request)
    verify_host_only_access(request)
    prog_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        open_directory_in_explorer(prog_dir)
        return {"status": "success", "folder": prog_dir}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Impossibile aprire la cartella del programma: {e}")

@app.get("/api/system/about")
def api_get_system_about(request: Request):
    verify_admin_access(request)
    # Java info
    java_ver = "Non rilevato"
    try:
        prereq = forge_installer.check_prerequisites()
        java_ver = prereq.get("java_version", "Non rilevato")
    except Exception:
        pass
        
    s_dir = manager.config.get("server_dir", "")
    prog_dir = os.path.dirname(os.path.abspath(__file__))
    mods_dir = os.path.join(s_dir, "mods")
    mods_count = len([f for f in os.listdir(mods_dir) if f.endswith(".jar")]) if os.path.isdir(mods_dir) else 0
    
    vm = psutil.virtual_memory()
    ram_total_gb = round(vm.total / (1024**3), 1)
    
    import shutil
    try:
        du = shutil.disk_usage(prog_dir)
        disk_free_gb = round(du.free / (1024**3), 1)
    except Exception:
        disk_free_gb = 0.0
        
    uptime_sec = int(time.time() - APP_START_TIME)
    uptime_str = f"{uptime_sec // 3600:02d}h {(uptime_sec % 3600) // 60:02d}m {uptime_sec % 60:02d}s"
    mc_port = manager.get_server_port()
    
    return {
        "status": "success",
        "app_name": "Voxarium",
        "version": PC_VERSION,
        "build": PC_BUILD,
        "release_channel": "Stabile (Ufficiale)",
        "release_date": "Ottobre 2026",
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "python_version": sys.version.split()[0],
        "java_version": java_ver,
        "cpu_count": os.cpu_count() or 4,
        "ram_total_gb": ram_total_gb,
        "disk_free_gb": disk_free_gb,
        "server_dir": s_dir,
        "program_dir": prog_dir,
        "server_port": mc_port,
        "web_port": manager.config.get("web_port", 8080),
        "mods_count": mods_count,
        "server_state": manager.state,
        "remote_active": bool(remote_access.get_status().get("active")),
        "remote_url": remote_access.url,
        "upnp_active": bool(upnp_mgr and upnp_mgr.is_mapped),
        "uptime_seconds": uptime_sec,
        "uptime_formatted": uptime_str
    }

@app.post("/api/notifications/test")
def api_test_push_notification(request: Request):
    verify_admin_access(request)
    try:
        if hasattr(manager, 'send_push_notification'):
            manager.send_push_notification(
                title="Notifica di Prova Minecraft",
                message="Test di comunicazione completato con successo dal pannello di controllo PC!",
                priority="default",
                tags="bell,white_check_mark"
            )
            return {"status": "success", "message": "Notifica push di prova inviata con successo!"}
        return {"status": "error", "message": "Funzione notifiche non disponibile"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# WebSocket for real-time dashboard
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # Stesse regole di verify_admin_access: locale, PIN disattivato, oppure token valido passato come ?token=
    client_host = websocket.client.host if websocket.client else ""
    hdrs = websocket.headers
    internet = is_internet_request(websocket)
    is_local = (
        not internet
        and client_host in ("127.0.0.1", "::1", "localhost")
        and (hdrs.get("host") or "").split(":")[0].lower() in ("localhost", "127.0.0.1", "::1", "")
    )
    token = websocket.query_params.get("token", "")
    # I WebSocket non sono protetti dal browser come le richieste normali: un altro sito potrebbe aprirne uno
    if is_cross_site_request(hdrs):
        await websocket.close(code=4403)
        return
    if internet and pin_is_default():
        await websocket.close(code=4403)
        return
    pin_not_needed = not internet and (is_local or not manager.config.get("require_pin_for_remote", True))
    if not (pin_not_needed or (token and token in valid_auth_tokens)):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    active_websockets.append(websocket)
    try:
        initial_status = manager.get_full_status()
        with manager.lock:
            recent_logs = list(manager.log_buffer)[-100:]
            log_seq = manager.log_seq
        recent_chat = list(manager.chat_messages)[-30:]
        recent_errors = list(manager.errors_list)[-30:]

        net_payload = build_network_payload()
        await _send_ws(websocket, {
            "type": "init",
            "data": initial_status,
            "logs": recent_logs,
            "log_seq": log_seq,
            "chat": recent_chat,
            "errors": recent_errors,
            "network": net_payload,
            "remote": net_payload.get("remote", {}),
            "backup": backup_mgr.get_status()
        })
        while True:
            data = await websocket.receive_json()
            action = data.get("action")
            if action == "command":
                cmd = data.get("command", "")
                if cmd:
                    # In un thread: RCON può attendere qualche secondo e bloccherebbe tutti i WebSocket
                    res = await asyncio.to_thread(manager.send_command, cmd)
                    if res.get("status") == "error":
                        # Prima l'errore si perdeva e nella console non compariva niente
                        await _send_ws(websocket, {"type": "toast_error", "message": res.get("message")})
            elif action == "talk":
                msg = data.get("message", "")
                sender = data.get("sender", "Host")
                if msg:
                    res = await asyncio.to_thread(manager.talk_to_server, msg, sender)
                    if res.get("status") == "error":
                        await _send_ws(websocket, {"type": "toast_error", "message": res.get("message")})
            elif action == "postpone":
                mins = data.get("minutes", 30)
                manager.postpone_scheduled_restart(mins)
            elif action == "backup_start":
                is_stopped = (manager.state == "STOPPED")
                res = backup_mgr.start_backup(is_server_stopped=is_stopped)
                if res.get("status") == "error":
                    await _send_ws(websocket, {"type": "toast_error", "message": res.get("message")})
            elif action == "backup_cancel":
                backup_mgr.cancel_backup()
            elif action == "ping":
                await _send_ws(websocket, {"type": "pong"})
    except WebSocketDisconnect:
        if websocket in active_websockets:
            active_websockets.remove(websocket)
    except Exception:
        if websocket in active_websockets:
            active_websockets.remove(websocket)
    finally:
        _ws_send_locks.pop(id(websocket), None)


NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0"
}

# Serve static frontend (both at root and /static)
@app.get("/style.css")
def serve_style_css():
    return FileResponse(os.path.join(STATIC_DIR, "style.css"), media_type="text/css", headers=NO_CACHE_HEADERS)

@app.get("/app.js")
def serve_app_js():
    return FileResponse(os.path.join(STATIC_DIR, "app.js"), media_type="application/javascript", headers=NO_CACHE_HEADERS)

# Dizionari delle lingue del pannello: i18n.js scarica solo quello scelto, senza cache come app.js
@app.get("/i18n_{lang}.js")
def serve_i18n_dict(lang: str):
    path = os.path.join(STATIC_DIR, f"i18n_{lang}.js")
    if not re.fullmatch(r"[a-z]{2}", lang) or not os.path.isfile(path):
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="application/javascript", headers=NO_CACHE_HEADERS)

@app.get("/favicon.ico")
def serve_favicon():
    fav = os.path.join(STATIC_DIR, "favicon.ico")
    if not os.path.exists(fav):
        fav = os.path.join(STATIC_DIR, "minecraft.ico")
    if os.path.exists(fav):
        return FileResponse(fav, media_type="image/x-icon")
    raise HTTPException(status_code=404)

@app.get("/minecraft_block.png")
def serve_minecraft_block_png():
    img = os.path.join(STATIC_DIR, "minecraft_block.png")
    if os.path.exists(img):
        return FileResponse(img, media_type="image/png")
    raise HTTPException(status_code=404)

class EcoModeRequest(BaseModel):
    enabled: bool
    minutes: int = 45

class AntiLagRequest(BaseModel):
    enabled: bool
    auto_clean: bool = True

class SaveConfigFileRequest(BaseModel):
    rel_path: Optional[str] = None
    path: Optional[str] = None
    content: str

class RestoreConfigFileRequest(BaseModel):
    rel_path: Optional[str] = None
    path: Optional[str] = None

@app.post("/api/eco-mode")
def api_set_eco_mode(req: EcoModeRequest, request: Request):
    verify_admin_access(request)
    return manager.set_eco_mode(req.enabled, req.minutes)

@app.post("/api/anti-lag")
def api_set_anti_lag(req: AntiLagRequest, request: Request):
    verify_admin_access(request)
    return manager.set_anti_lag(req.enabled, req.auto_clean)

@app.get("/api/config-files")
def api_list_config_files(request: Request):
    verify_admin_access(request)
    return manager.list_config_files()

@app.get("/api/config-file")
def api_read_config_file(path: str, request: Request):
    verify_admin_access(request)
    return manager.read_config_file(path)

@app.post("/api/config-file")
def api_save_config_file(req: SaveConfigFileRequest, request: Request):
    verify_admin_access(request)
    target_path = req.rel_path or req.path or ""
    return manager.save_config_file(target_path, req.content)

@app.post("/api/config-file/restore")
def api_restore_config_file(req: RestoreConfigFileRequest, request: Request):
    verify_admin_access(request)
    target_path = req.rel_path or req.path or ""
    return manager.restore_config_file(target_path)

# =========================================================
# ADVANCED DESKTOP FILESYSTEM EXPLORER ENDPOINTS
# =========================================================

@app.post("/api/filesystem/native_browse")
async def api_filesystem_native_browse(request: Request):
    verify_admin_access(request)
    verify_host_only_access(request)
    initial_path = ""
    target = "server_dir"
    try:
        body = await request.json()
        if isinstance(body, dict):
            initial_path = body.get("initial_path") or ""
            target = body.get("target") or "server_dir"
    except Exception:
        pass

    s_dir = manager.config.get("server_dir")
    curr_dir = initial_path if (initial_path and os.path.exists(initial_path)) else (s_dir if (s_dir and os.path.exists(s_dir)) else os.path.join(os.environ.get("USERPROFILE", "") or os.path.expanduser("~"), "Desktop"))
    if os.path.isfile(curr_dir):
        curr_dir = os.path.dirname(curr_dir)

    is_file_target = (target == "run_bat")
    if is_file_target:
        title = "Seleziona File di Avvio Server (run.bat)"
        filter_str = "File di avvio (*.bat;*.jar)|*.bat;*.jar|Tutti i file (*.*)|*.*"
    else:
        title = "Seleziona Server Minecraft (entra nella cartella e seleziona run.bat o qualsiasi file)"
        filter_str = "File del Server (*.bat;*.jar;server.properties;*.*)|*.bat;*.jar;server.properties;*.*|Tutti i file (*.*)|*.*"

    if sys.platform != "win32":
        return await asyncio.to_thread(_linux_browse, title, curr_dir, target)

    def _ps_quote(s: str) -> str:
        return (s or "").replace("'", "''")

    ps_script = f"""[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$topForm = New-Object System.Windows.Forms.Form
$topForm.TopMost = $true
$topForm.StartPosition = 'CenterScreen'
$topForm.Size = New-Object System.Drawing.Size(1,1)
$topForm.Opacity = 0
$topForm.Show()
$topForm.BringToFront()
$topForm.Activate()

$d = New-Object System.Windows.Forms.OpenFileDialog
$d.Title = '{_ps_quote(title)}'
$d.Filter = '{_ps_quote(filter_str)}'
$d.InitialDirectory = '{_ps_quote(curr_dir)}'
$d.CheckFileExists = $false
$d.RestoreDirectory = $true

if ($d.ShowDialog($topForm) -eq [System.Windows.Forms.DialogResult]::OK) {{
    Write-Output $d.FileName
}}

$topForm.Close()
$topForm.Dispose()
"""
    enc_cmd = base64.b64encode(ps_script.encode("utf-16le")).decode("ascii")
    try:
        res = await asyncio.to_thread(
            subprocess.run,
            ["powershell", "-Sta", "-NoProfile", "-EncodedCommand", enc_cmd],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120
        )
        chosen = res.stdout.strip()
        if chosen and os.path.exists(chosen):
            if os.path.isfile(chosen):
                return {
                    "success": True,
                    "target": target,
                    "selected_path": chosen,
                    "folder_path": os.path.dirname(chosen),
                    "file_path": chosen,
                    "is_file": True
                }
            else:
                return {
                    "success": True,
                    "target": target,
                    "selected_path": chosen,
                    "folder_path": chosen,
                    "file_path": None,
                    "is_file": False
                }
        elif chosen:
            parent = os.path.dirname(chosen)
            if os.path.exists(parent):
                return {
                    "success": True,
                    "target": target,
                    "selected_path": chosen,
                    "folder_path": parent,
                    "file_path": chosen,
                    "is_file": True
                }
        return {"success": False, "target": target, "cancelled": True}
    except Exception as e:
        return {"success": False, "target": target, "error": str(e)}



def _linux_browse(title: str, curr_dir: str, target: str) -> dict:
    """Finestra di scelta file su Linux (zenity o kdialog). Senza desktop: "unavailable", il percorso si scrive a mano."""
    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    start = (curr_dir.rstrip("/") + "/") if curr_dir else os.path.expanduser("~/")
    if has_display and shutil.which("zenity"):
        cmd = ["zenity", "--file-selection", f"--title={title}", f"--filename={start}"]
    elif has_display and shutil.which("kdialog"):
        cmd = ["kdialog", "--title", title, "--getopenfilename", start]
    else:
        return {"success": False, "target": target, "unavailable": True}
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
                             creationflags=0)
    except Exception as e:
        return {"success": False, "target": target, "error": str(e)}
    chosen = res.stdout.strip()
    if not chosen or not os.path.exists(chosen):
        return {"success": False, "target": target, "cancelled": True}
    is_file = os.path.isfile(chosen)
    return {"success": True, "target": target, "selected_path": chosen,
            "folder_path": os.path.dirname(chosen) if is_file else chosen,
            "file_path": chosen if is_file else None, "is_file": is_file}


# Funzioni di servizio usate sia qui sia nei file di routes/
def get_server_dir_path() -> str:
    # "" senza server: os.path.abspath("") darebbe la cartella del programma (il file manager mostrava quella)
    s_dir = (manager.config.get("server_dir") or "").strip()
    return os.path.abspath(s_dir) if s_dir else ""


def _sync_config_with_active_instance():
    """config.json senza cartella ma con un server attivo in instances.json (versioni precedenti): li riallinea."""
    if manager.config.get("server_dir"):
        return
    active = instance_manager.get_active_instance()
    if active.get("id") and active.get("server_dir") and os.path.isdir(active["server_dir"]):
        instance_manager.switch_instance(active["id"], manager=manager)
        backup_mgr.server_dir = manager.config.get("server_dir", "")


def get_mods_dir() -> str:
    s_dir = manager.config.get("server_dir", "")
    return os.path.join(s_dir, "mods")


def open_directory_in_explorer(path: str):
    norm = os.path.normpath(path)
    os.makedirs(norm, exist_ok=True)
    if sys.platform == "win32":
        opened = False
        try:
            os.startfile(norm)
            opened = True
        except Exception:
            pass
        if not opened:
            try:
                subprocess.Popen(["explorer.exe", norm], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except Exception:
                pass
    else:
        try:
            subprocess.Popen(["xdg-open", norm])
        except Exception:
            pass


# Rotte divise per argomento in routes/ (importate qui, dopo che app.py ha definito tutto ciò che usano)
from routes import files, instances, mods, players, properties_metrics, server_creation, store  # noqa: E402
for _r in (files.router, instances.router, mods.router, players.router, properties_metrics.router, server_creation.router, store.router):
    app.include_router(_r)


@app.get("/", response_class=HTMLResponse)
def serve_index():
    index_file = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_file):
        with open(index_file, "r", encoding="utf-8") as f:
            html = f.read()
        return HTMLResponse(_with_asset_versions(html), headers=NO_CACHE_HEADERS)
    return HTMLResponse("<h1>Pannello Server Minecraft in caricamento...</h1>")

_ASSET_REF_RE = re.compile(r'((?:href|src)=")(app\.js|i18n\.js|style\.css|theme\.css)(?:\?v=[^"]*)?"')

def _with_asset_versions(html: str) -> str:
    """Aggiunge ?v=<data di modifica> a CSS e JS: dopo un aggiornamento il browser (e l'app) non usa copie vecchie."""
    def _sub(m):
        try:
            ver = int(os.path.getmtime(os.path.join(STATIC_DIR, m.group(2))))
        except OSError:
            ver = PC_BUILD
        return f'{m.group(1)}{m.group(2)}?v={ver}"'
    return _ASSET_REF_RE.sub(_sub, html)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static_root")

async def _serve_all(port: int):
    import uvicorn
    # HTTP per il PC e la rete di casa (con i servizi in background); HTTPS per internet (stessa app, senza lifespan)
    http_srv = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info", access_log=False))
    https_srv = uvicorn.Server(uvicorn.Config(
        app, host="0.0.0.0", port=REMOTE_PORT, log_level="warning", access_log=False, lifespan="off",
        ssl_keyfile=remote_access.key_path, ssl_certfile=remote_access.cert_path))

    async def _https():
        try:
            await https_srv.serve()
        except (SystemExit, Exception) as e:   # porta occupata: il pannello in casa deve funzionare lo stesso
            print(f"[Accesso remoto] Porta HTTPS {REMOTE_PORT} non disponibile: {e}")

    https_task = asyncio.create_task(_https())
    try:
        await http_srv.serve()
    finally:
        https_srv.should_exit = True
        await https_task

if __name__ == "__main__":
    port = manager.config.get("web_port", 8080)
    print(f"Avvio Voxarium su porta {port} (HTTPS da internet su {REMOTE_PORT})...")
    asyncio.run(_serve_all(port))
