"""
Minecraft Server Process & System Manager
Manages server lifecycle, crash detection with auto-restart,
scheduled 4-hour restarts with warnings, real-time resource tracking,
player presence, in-game chat, and server error diagnostics.
"""

import os
import time
import json
import re
import shlex
import socket
import threading
import subprocess
import uuid
import urllib.request
import urllib.parse
from datetime import datetime
from collections import deque
from typing import Dict, Any, List, Optional, Callable, Tuple
import shutil

import psutil
from mc_ping import query_server_status
from platform_utils import IS_WINDOWS, LAUNCH_SCRIPT, LAUNCH_SCRIPT_CANDIDATES, split_java_line, script_fallback_command

# Processi figli (java, jcmd, run.bat) senza finestra: il pannello gira senza console, quindi ogni processo
# avviato senza questo flag aprirebbe una propria finestra cmd
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

# Topic ntfy condiviso dalle vecchie versioni (incluso nei vecchi pacchetti, quindi uguale su più PC): non va mai
# usato. Si conserva solo l'impronta SHA-256, non il nome.
LEGACY_TOPIC_HASHES = {"21e962e1eeb195bd5908e3e9ae7246ccdd2ed837715e5752bf5e13d2cb441e6f"}


def is_legacy_topic(topic) -> bool:
    import hashlib
    return bool(topic) and hashlib.sha256(str(topic).encode("utf-8")).hexdigest() in LEGACY_TOPIC_HASHES

DEFAULT_CONFIG_PATH =os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def _read_nbt(f, tag_type):
    """Valore di un tag NBT (formato dei file di Minecraft) letto dal file già aperto."""
    import struct
    rd = lambda fmt: struct.unpack(">" + fmt, f.read(struct.calcsize(">" + fmt)))[0]
    if tag_type == 1: return rd("b")
    if tag_type == 2: return rd("h")
    if tag_type == 3: return rd("i")
    if tag_type == 4: return rd("q")
    if tag_type == 5: return rd("f")
    if tag_type == 6: return rd("d")
    if tag_type == 7: return f.read(rd("i"))
    if tag_type == 8: return f.read(rd("H")).decode("utf-8", "replace")
    if tag_type == 9:
        item_type, n = rd("b"), rd("i")
        return [_read_nbt(f, item_type) for _ in range(n)]
    if tag_type == 10:
        out = {}
        while True:
            t = rd("b")
            if t == 0:
                return out
            name = f.read(rd("H")).decode("utf-8", "replace")
            out[name] = _read_nbt(f, t)
    if tag_type == 11: return [rd("i") for _ in range(rd("i"))]
    if tag_type == 12: return [rd("q") for _ in range(rd("i"))]
    raise ValueError(f"tag NBT sconosciuto {tag_type}")


def read_world_spawn(server_dir: str) -> Optional[Tuple[int, int, int]]:
    """Punto di spawn del mondo principale (x, y, z) da level.dat; None se non si legge.
    Fino a Minecraft 1.21.4 sono SpawnX/Y/Z, dopo un gruppo "spawn" con "pos"."""
    import gzip
    try:
        level = "world"
        props = os.path.join(server_dir, "server.properties")
        if os.path.isfile(props):
            for line in open(props, encoding="utf-8", errors="ignore"):
                if line.startswith("level-name="):
                    level = line.split("=", 1)[1].strip() or "world"
        with gzip.open(os.path.join(server_dir, level, "level.dat"), "rb") as f:
            root_type = f.read(1)[0]
            f.read(int.from_bytes(f.read(2), "big"))
            data = _read_nbt(f, root_type).get("Data", {})
        if all(k in data for k in ("SpawnX", "SpawnY", "SpawnZ")):
            return int(data["SpawnX"]), int(data["SpawnY"]), int(data["SpawnZ"])
        pos = (data.get("spawn") or {}).get("pos")
        if isinstance(pos, list) and len(pos) == 3:
            return int(pos[0]), int(pos[1]), int(pos[2])
    except Exception:
        pass
    return None


class ServerManager:
    # Pre-compiled regex patterns for log parsing performance
    _RE_SERVER_READY = re.compile(r'Done \([0-9.]+s\)! For help, type "help"')
    _RE_SERVER_READY2 = re.compile(r'Time elapsed: [0-9.]+ ms')
    _RE_SERVER_READY3 = re.compile(r'Listening on \/0\.0\.0\.0:[0-9]+')
    _RE_PLAYER_JOIN = re.compile(r'\[Server thread/INFO\].*?: (\w+) joined the game')
    _RE_PLAYER_JOIN2 = re.compile(r'\[Server thread/INFO\].*?: (\w+)\[/[0-9.:]+\] logged in with entity id')
    _RE_PLAYER_LEAVE = re.compile(r'\[Server thread/INFO\].*?: (\w+) left the game')
    _RE_PLAYER_LEAVE2 = re.compile(r'\[Server thread/INFO\].*?: (\w+) lost connection')
    _RE_PLAYER_LEAVE3 = re.compile(r'Disconnecting (\w+)')
    _RE_CHAT_MSG = re.compile(r'\[Server thread/INFO\].*?: (?:\[Not Secure\] )?<(\w+)>\s+(.*)')
    # Livello scritto nell'intestazione della riga: "[14:15:16] [main/WARN] [mixin/]: ..." o "[...] [Server thread/ERROR]: ..."
    _RE_LOG_LEVEL = re.compile(r'^\[[^\]]*\] \[[^\]]*/(TRACE|DEBUG|INFO|WARN|ERROR|FATAL)\]')
    # Righe marcate ERROR/FATAL che Forge scrive sempre e che non indicano un problema
    _HARMLESS_ERRORS = (
        "Could not determine mod trust worthiness",
    )
    _RE_EXCEPTION = re.compile(r'(Exception in thread|java\.lang\.\w+Exception|java\.lang\.Error:)')
    _RE_XMX_G = re.compile(r'-Xmx(\d+)[Gg]')
    _RE_XMX_M = re.compile(r'-Xmx(\d+)[Mm]')

    def __init__(self, config_path: str = DEFAULT_CONFIG_PATH):
        self.config_path = config_path
        self.start_blocker: Optional[Callable[[], Optional[str]]] = None
        self.config = self.load_config()
        import secrets
        changed = False
        if is_legacy_topic(self.config.get("push_topic")):
            # Topic condiviso delle vecchie versioni (config.json incluso nei vecchi pacchetti): topic e segreto
            # potrebbero essere uguali su più PC, quindi se ne generano di nuovi propri di questa installazione
            self.config["push_topic"] = ""
            self.config["push_secret"] = ""
        if not self.config.get("push_secret"):
            self.config["push_secret"] = secrets.token_hex(32)
            changed = True
        if not self.config.get("push_topic"):
            # Topic ntfy diverso per ogni installazione (le installazioni esistenti mantengono il loro)
            self.config["push_topic"] = "mc_server_" + secrets.token_hex(8)
            changed = True
        from instance_manager import _is_stale_path
        if _is_stale_path(self.config.get("server_dir", "")):
            # Cartella server di un altro PC (config.json rimasto da una vecchia installazione): nessun server configurato
            self.config["server_dir"] = ""
            self.config["server_run_bat"] = ""
            changed = True
        if changed:
            self.save_config()
        
        # Process & state
        self.process: Optional[subprocess.Popen] = None
        self.psutil_proc: Optional[psutil.Process] = None
        self.state: str = "STOPPED" # STOPPED, STARTING, ONLINE, STOPPING, CRASHED, RESTARTING
        self.start_time: Optional[float] = None
        self.intentional_stop: bool = False
        self.scheduled_restart_in_progress: bool = False
        
        # Console output buffer (keep last 1500 lines)
        self.log_buffer: deque = deque(maxlen=1500)
        self.log_seq = 0   # numero dell'ultima riga della console: il pannello senza WebSocket chiede solo le nuove
        self.log_subscribers: List[Callable[[str], None]] = []
        
        # In-game Chat feed (keep last 300 messages)
        self.chat_messages: deque = deque(maxlen=300)
        self.chat_subscribers: List[Callable[[dict], None]] = []
        
        # Server Errors & Warnings buffer (keep last 200 items)
        self.errors_list: deque = deque(maxlen=200)
        self.error_subscribers: List[Callable[[dict], None]] = []
        
        # Player tracking
        self.players: Dict[str, Dict[str, Any]] = {} # name -> {joined_at, ip, etc.}
        self.server_info: Dict[str, Any] = {
            "online": False,
            "latency_ms": None,
            "players_online": 0,
            "players_max": 20,
            "motd": "",
            "version": ""
        }
        
        # Scheduled Restart Timer (Default 4 hours = 14400s)
        self.scheduled_interval_seconds = self.config.get("scheduled_restart_interval_minutes", 240) * 60
        self.scheduled_remaining_seconds = self.scheduled_interval_seconds
        self.scheduled_enabled = self.config.get("scheduled_restart_enabled", True)
        self.warning_sent_flags: set = set()
        
        # Crash tracking & protection
        self.crash_history: List[Dict[str, Any]] = []
        self.auto_restart_on_crash = self.config.get("auto_restart_crash", True)
        self.crash_retry_delay = self.config.get("crash_retry_delay_seconds", 8)
        self.crash_window_seconds = self.config.get("crash_window_seconds", 180)
        self.max_crashes_in_window = self.config.get("max_crashes_in_window", 3)
        self.auto_restart_paused = False
        self.crash_countdown = 0
        
        # Metrics history for live UI chart (last 60 seconds)
        self.metrics_history: deque = deque(maxlen=60)
        self.metrics_history_24h: deque = deque(maxlen=288) # 24 hours at 1 snapshot every 5 min
        self._last_24h_snapshot_time: float = 0.0
        
        # TPS Tracking (Ticks Per Second)
        self.current_tps: float = 20.0
        self.last_tps_drop_time: float = 0.0
        self._RE_CANT_KEEP_UP = re.compile(r"Can't keep up!.*?Running\s+(\d+)ms\s+or\s+(\d+)\s+ticks behind", re.IGNORECASE)
        
        # Mutex lock
        self.lock = threading.RLock()
        self._last_ram_alert_time = 0.0
        self._last_disk_alert_time = 0.0
        
        # Eco Mode (Auto-shutdown when empty for X minutes)
        self.eco_mode_enabled = self.config.get("eco_mode_enabled", False)
        self.eco_mode_minutes = self.config.get("eco_mode_minutes", 45)
        self.server_empty_since: Optional[float] = None

        # Anti-Lag Watchdog (Detect low TPS and auto clean ground items)
        self.anti_lag_enabled = self.config.get("anti_lag_enabled", True)
        self.anti_lag_auto_clean = self.config.get("anti_lag_auto_clean", True)
        self.low_tps_start_time: Optional[float] = None
        self._last_lag_alert_time: float = 0.0

        # Background worker threads
        self.running = True
        self.stdout_thread: Optional[threading.Thread] = None
        self.monitor_thread: Optional[threading.Thread] = None
        
        # Start background timer and resource monitor thread
        self.ticker_thread = threading.Thread(target=self._ticker_loop, daemon=True)
        self.ticker_thread.start()
        self._network_sync_pending = False
        threading.Thread(target=self._network_sync_retry_loop, daemon=True).start()
        
        # Scan initial crash reports
        self.scan_crash_reports()

        # Check if Minecraft server is already running in background and attach to it
        existing = self._find_running_minecraft_proc()
        if existing:
            self._attach_running_process(existing)
        
        self.add_log("[Pannello] Modulo di controllo server inizializzato con diagnostica errori e chat attiva.")

    def _find_running_minecraft_proc(self) -> Optional[psutil.Process]:
        # Senza server configurato non c'è niente di nostro: normpath("") è "." e "." compare in ogni "server.jar",
        # quindi prima si "adottava" qualunque server Minecraft acceso sul PC (e Arresta l'avrebbe spento)
        if not (self.config.get("server_dir") or "").strip():
            return None
        server_dir = os.path.normpath(self.config.get("server_dir", "")).lower()
        for p in psutil.process_iter(['pid', 'name', 'cmdline', 'cwd']):
            try:
                name = p.info['name'] or ''
                if 'java' in name.lower():
                    cmdline = " ".join(p.info['cmdline'] or []).lower()
                    cwd = os.path.normpath(p.info['cwd'] or '').lower()
                    # Escludi categoricamente i client di gioco Minecraft Forge / Vanilla / Fabric
                    if 'forgeclient' in cmdline or '--accesstoken' in cmdline or 'net.minecraft.client' in cmdline or 'minecraft.client' in cmdline:
                        continue
                    in_server_dir = (cwd == server_dir) or (server_dir in cmdline)
                    looks_like_server = ('nogui' in cmdline or 'forgeserver' in cmdline or 'win_args.txt' in cmdline
                                         or 'unix_args.txt' in cmdline or 'server.jar' in cmdline or 'user_jvm_args.txt' in cmdline)
                    if in_server_dir and looks_like_server:
                        return p
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return None

    _log_tail_active = False

    def _get_rcon_credentials(self):
        """Reads RCON port and password from server.properties."""
        server_dir = self.config.get("server_dir", "")
        props_path = os.path.join(server_dir, "server.properties")
        port = 25575
        password = ""
        if os.path.isfile(props_path):
            try:
                with open(props_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k = k.strip().lower()
                        v = v.strip()
                        if k == "rcon.port":
                            try: port = int(v)
                            except Exception: pass
                        elif k == "rcon.password":
                            password = v.replace("\\!", "!").replace("\\=", "=").replace("\\:", ":")
            except Exception:
                pass
        return port, password

    def _send_rcon_command(self, cmd: str) -> Optional[str]:
        """Executes a command via Minecraft RCON protocol (works attached or detached)."""
        port, password = self._get_rcon_credentials()
        if not password:
            return None
        import struct
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.5)
            s.connect(("127.0.0.1", port))

            # Auth packet (type=3)
            req_id = 99
            pwd_bytes = password.encode("utf-8")
            pkt = struct.pack("<iii", len(pwd_bytes) + 10, req_id, 3) + pwd_bytes + b"\x00\x00"
            s.sendall(pkt)

            resp = s.recv(4096)
            if len(resp) < 12:
                s.close()
                return None
            _, resp_id, _ = struct.unpack("<iii", resp[:12])
            if resp_id == -1:
                s.close()
                return None

            # Command packet (type=2)
            cmd_bytes = cmd.encode("utf-8")
            pkt = struct.pack("<iii", len(cmd_bytes) + 10, req_id + 1, 2) + cmd_bytes + b"\x00\x00"
            s.sendall(pkt)

            resp = s.recv(4096)
            out = ""
            if len(resp) >= 12:
                out = resp[12:-2].decode("utf-8", errors="replace")
            s.close()
            return out
        except Exception:
            if s:
                try: s.close()
                except Exception: pass
            return None

    def _start_log_tailing(self):
        """Tails server_dir/logs/latest.log to feed console/CLI when attached to a pre-existing server process."""
        if getattr(self, '_log_tail_active', False):
            return
        self._log_tail_active = True
        t = threading.Thread(target=self._tail_log_worker, daemon=True)
        t.start()

    def _tail_log_worker(self):
        server_dir = self.config.get("server_dir", "")
        log_file = os.path.join(server_dir, "logs", "latest.log")

        # 1. Pre-load recent log history into buffer if buffer has few items
        if os.path.isfile(log_file):
            try:
                with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                    tail_lines = lines[-120:] if len(lines) > 120 else lines
                    with self.lock:
                        if len(self.log_buffer) <= 2:
                            for l in tail_lines:
                                s = l.rstrip("\r\n")
                                if s:
                                    self.log_buffer.append(s)
                                    self.log_seq += 1
            except Exception:
                pass

        # 2. Tail latest.log in real time for new lines
        try:
            if not os.path.isfile(log_file):
                self._log_tail_active = False
                return

            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                f.seek(0, os.SEEK_END)
                while self.running and self._log_tail_active:
                    # If process was launched directly with stdout pipe by this app instance, let stdout_thread handle it
                    if self.process and self.process.poll() is None and self.process.stdout:
                        break

                    is_active = (self.psutil_proc and self.psutil_proc.is_running())
                    line = f.readline()
                    if line:
                        s = line.rstrip("\r\n")
                        if s:
                            self.add_log(s)
                            self._parse_log_line(s)
                    else:
                        if not is_active and self.state == "STOPPED":
                            break
                        time.sleep(0.3)
        except Exception:
            pass
        finally:
            self._log_tail_active = False

    def _monitor_attached_proc(self, proc: psutil.Process):
        """Monitors an attached existing Java process and handles exit/restart seamlessly."""
        try:
            while self.running:
                if not proc.is_running():
                    break
                time.sleep(1)
        except Exception:
            pass

        with self.lock:
            if self.psutil_proc and self.psutil_proc.pid == proc.pid:
                self.psutil_proc = None
                self.players.clear()
                self.server_info["online"] = False

                if self.scheduled_restart_in_progress:
                    self.state = "RESTARTING"
                    self.add_log("[Pannello] Server precedente arrestato. Avvio del server in corso tra 3 secondi...")
                    threading.Thread(target=self._delayed_restart_worker, args=(3,), daemon=True).start()
                    return

                if self.intentional_stop:
                    self.state = "STOPPED"
                    self.add_log("[Pannello] Server arrestato con successo.")
                    return

                # If terminated unexpectedly while attached
                self.state = "CRASHED"
                self.add_log("[Pannello/ALLARME] Il server Minecraft si è arrestato inaspettatamente!")

    def _attach_running_process(self, proc: psutil.Process):
        with self.lock:
            self.psutil_proc = proc
            self.state = "ONLINE"
            self.auto_restart_paused = False
            self.crash_countdown = 0
            try:
                self.start_time = proc.create_time()
            except Exception:
                if not self.start_time:
                    self.start_time = time.time()
            self.add_log(f"[Pannello] Server Minecraft Java già attivo rilevato e agganciato (PID {proc.pid}).")

        # Start live log tailing to immediately populate CLI and stream new events
        self._start_log_tailing()

        # Start monitor thread to track process exit and support seamless restart
        threading.Thread(target=self._monitor_attached_proc, args=(proc,), daemon=True).start()

    def load_config(self) -> Dict[str, Any]:
        default_cfg = {
            "server_dir": "",
            "server_run_bat": "",
            "cloud_backup_dir": "",
            "server_command": "",  # si usa run.bat/start.bat o server.jar del server scelto
            "server_port": 32769,
            "web_port": 8080,
            "auto_restart_crash": True,
            "crash_retry_delay_seconds": 8,
            "max_crashes_in_window": 3,
            "crash_window_seconds": 180,
            "scheduled_restart_enabled": True,
            "scheduled_restart_interval_minutes": 240,
            "chat_warnings_enabled": True,
            "admin_pin": "1234",
            "require_pin_for_remote": True,
            "mc_connection_mode": "upnp"
        }
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    default_cfg.update(loaded)
                    # Chiavi dei vecchi tunnel (Cloudflare con token, ngrok): non servono più e non vanno mostrate
                    for old_key in ("tunnel_token", "tunnel_domain", "ngrok_token"):
                        default_cfg.pop(old_key, None)
            except Exception as e:
                print(f"Errore nel caricamento del file di configurazione: {e}")
        else:
            self.save_config(default_cfg)
        return default_cfg

    def save_config(self, cfg: Optional[Dict[str, Any]] = None):
        if cfg:
            self.config = cfg
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Errore nel salvataggio della configurazione: {e}")

    def add_log(self, line: str):
        line = line.rstrip("\r\n")
        timestamp = datetime.now().strftime("[%H:%M:%S]")
        formatted_line = f"{timestamp} {line}" if not line.startswith("[") else line
        with self.lock:
            self.log_buffer.append(formatted_line)
            self.log_seq += 1
            for sub in list(self.log_subscribers):
                try:
                    sub(formatted_line)
                except Exception:
                    pass

    def logs_since(self, since: int, tail: int = 300) -> Dict[str, Any]:
        """Righe della console dopo la numero `since`. reset=True se il pannello deve ripartire da capo
        (console svuotata, programma riavviato o troppe righe perse)."""
        with self.lock:
            lines = list(self.log_buffer)
            seq = self.log_seq
        new_count = seq - since
        if since <= 0 or new_count < 0 or new_count > len(lines):
            return {"lines": lines[-tail:] if tail > 0 else lines, "seq": seq, "reset": True}
        return {"lines": lines[len(lines) - new_count:] if new_count else [], "seq": seq, "reset": False}

    def subscribe_logs(self, callback: Callable[[str], None]):
        with self.lock:
            if callback not in self.log_subscribers:
                self.log_subscribers.append(callback)

    def subscribe_chat(self, callback: Callable[[dict], None]):
        with self.lock:
            if callback not in self.chat_subscribers:
                self.chat_subscribers.append(callback)

    def subscribe_errors(self, callback: Callable[[dict], None]):
        with self.lock:
            if callback not in self.error_subscribers:
                self.error_subscribers.append(callback)

    def _broadcast_chat(self, msg: dict):
        for sub in list(self.chat_subscribers):
            try:
                sub(msg)
            except Exception:
                pass

    def _broadcast_error(self, error_item: dict):
        for sub in list(self.error_subscribers):
            try:
                sub(error_item)
            except Exception:
                pass

    def send_push_notification(self, title: str, message: str, priority: str = "urgent", tags: str = "warning", click_url: Optional[str] = None, local_url: Optional[str] = None):
        """Sends an instant push-to-wake notification to the user's phone via ntfy gateway (0% battery consumption)."""
        topic = self.config.get("push_topic", "")
        if not topic:
            return
        target_click = click_url or getattr(self, "current_remote_url", None)
        def _do_push():
            try:
                import json

                tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else ["warning"]
                # Firma dell'avviso: l'app mostra solo gli avvisi con firma valida e recente, così chi conosce il nome
                # del topic non può mandare al telefono avvisi falsi né rimandare quelli vecchi
                tag_list += self._alert_signature_tags(title, message)
                prio_map = {"min": 1, "low": 2, "default": 3, "high": 4, "urgent": 5}
                prio_val = prio_map.get(str(priority).lower(), 5)
                
                payload = {
                    "topic": topic,
                    "title": title,
                    "message": message,
                    "priority": prio_val,
                    "tags": tag_list
                }
                headers = {"Content-Type": "application/json; charset=utf-8"}
                if target_click:
                    payload["click"] = target_click
                    payload["actions"] = [
                        {
                            "action": "view",
                            "label": "⬇️ Scarica APK Aggiornato",
                            "url": f"{target_click}/static/Controllo_Server_Minecraft.apk"
                        },
                        {
                            "action": "view",
                            "label": "Apri Dashboard",
                            "url": target_click
                        }
                    ]
                
                req = urllib.request.Request(
                    "https://ntfy.sh",
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers=headers
                )
                with urllib.request.urlopen(req, timeout=6):
                    pass
                if "network_sync" in tag_list:
                    self._network_sync_pending = False
            except Exception as e:
                if "network_sync" in (tags or ""):
                    # ntfy.sh non raggiungibile: l'indirizzo attuale verrà reinviato da _network_sync_retry_loop
                    self._network_sync_pending = True
        threading.Thread(target=_do_push, daemon=True).start()

    def _alert_signature_tags(self, title: str, message: str, ts: Optional[int] = None) -> List[str]:
        """Tag "ts-<secondi>" e "sig-<HMAC-SHA256(push_secret, titolo\nmessaggio\nts)>" verificati dall'app Android."""
        import hmac
        import hashlib
        secret = str(self.config.get("push_secret") or "")
        if not secret:
            return []
        ts = int(time.time()) if ts is None else ts
        data = f"{title}\n{message}\n{ts}".encode("utf-8")
        return [f"ts-{ts}", "sig-" + hmac.new(secret.encode("utf-8"), data, hashlib.sha256).hexdigest()]

    def _network_sync_retry_loop(self):
        """Se l'invio dell'indirizzo è fallito (ntfy.sh irraggiungibile o IP bloccato), lo ritenta ogni 5 minuti:
        così telefono e PC Remoti ricevono il nuovo indirizzo remoto appena ntfy torna disponibile.
        Un solo tentativo ogni 5 minuti per non allungare un eventuale blocco dell'IP da parte di ntfy."""
        while True:
            time.sleep(300)
            if getattr(self, "_network_sync_pending", False) and getattr(self, "current_remote_url", None):
                self.broadcast_network_sync(self.current_remote_url, getattr(self, "current_local_url", "") or "",
                                            getattr(self, "current_cert_fingerprint", "") or "")

    def broadcast_network_sync(self, remote_url: str, local_url: str, fingerprint: str = ""):
        """Invia a telefono e PC Remoti l'indirizzo da internet, quello di casa e l'impronta del certificato HTTPS,
        firmati con push_secret (HMAC-SHA256 su "url|lan|fp"): chi legge il topic non può falsificarli."""
        self.current_remote_url = remote_url
        self.current_local_url = local_url
        self.current_cert_fingerprint = fingerprint
        import hmac
        import hashlib
        push_sec = self.config.get("push_secret") or str(self.config.get("admin_pin", "1234"))
        sig = hmac.new(push_sec.encode("utf-8"), f"{remote_url}|{local_url}|{fingerprint}".encode("utf-8"), hashlib.sha256).hexdigest()
        message = f"Nuovo indirizzo remoto: {remote_url} | Rete LAN: {local_url} | fp={fingerprint} | sig={sig}"
        self.send_push_notification(
            title="network_sync",
            message=message,
            priority="min",
            tags="network_sync,silent",
            click_url=remote_url,
            local_url=local_url
        )

    def get_current_tps(self) -> float:
        if self.state != "ONLINE":
            return 0.0
        now = time.time()
        # If no lag warning in the last 15s, smoothly restore towards 20.0
        if (now - self.last_tps_drop_time) > 15.0:
            if self.current_tps < 20.0:
                self.current_tps = min(20.0, round(self.current_tps + 0.5, 1))
        return round(self.current_tps, 1)

    def get_server_properties(self) -> Dict[str, Any]:
        server_dir = self.config.get("server_dir", "")
        props_path = os.path.join(server_dir, "server.properties")
        props = {}
        if os.path.isfile(props_path):
            try:
                with open(props_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        s = line.strip()
                        if s and not s.startswith("#") and "=" in s:
                            k, v = s.split("=", 1)
                            props[k.strip()] = v.strip()
            except Exception as e:
                self.add_log(f"[Pannello/ERRORE] Lettura server.properties: {e}")
        return props

    def save_server_properties(self, new_props: Dict[str, Any]) -> Dict[str, Any]:
        server_dir = self.config.get("server_dir", "")
        props_path = os.path.join(server_dir, "server.properties")
        if not os.path.isfile(props_path):
            return {"status": "error", "message": "File server.properties non trovato."}

        try:
            # Create safety backup server.properties.bak before modifying
            try:
                import shutil
                shutil.copy2(props_path, props_path + ".bak")
            except Exception as e:
                self.add_log(f"[Pannello/WARN] Copia di sicurezza server.properties.bak non creata: {e}")

            lines = []
            with open(props_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()

            existing_keys = set()
            new_lines = []
            for line in lines:
                s = line.strip()
                if s and not s.startswith("#") and "=" in s:
                    k = s.split("=", 1)[0]
                    k = k.strip()
                    existing_keys.add(k)
                    if k in new_props:
                        new_lines.append(f"{k}={new_props[k]}\n")
                    else:
                        new_lines.append(line)
                else:
                    new_lines.append(line)

            # Append any new properties that were not present in the file
            for k, v in new_props.items():
                if k not in existing_keys:
                    new_lines.append(f"{k}={v}\n")

            with open(props_path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)

            self.add_log("[Pannello] Impostazioni di server.properties salvate con successo (creato backup server.properties.bak).")
            return {
                "status": "success",
                "message": "Impostazioni salvate! Riavvia il server per renderle effettive.",
                "needs_restart": self.state == "ONLINE"
            }
        except Exception as e:
            return {"status": "error", "message": f"Errore nel salvataggio: {e}"}

    def get_server_port(self) -> int:
        server_dir = self.config.get("server_dir", "")
        props_path = os.path.join(server_dir, "server.properties") if server_dir else ""
        if os.path.isfile(props_path):
            try:
                with open(props_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("server-port="):
                            val = line.split("=", 1)[1].strip()
                            if val.isdigit():
                                return int(val)
            except Exception:
                pass
        return int(self.config.get("server_port", 25565))

    @staticmethod
    def write_properties(server_dir: str, values: Dict[str, str]) -> bool:
        """Scrive le chiavi indicate in server.properties lasciando invariato il resto. False senza cartella."""
        props_path = os.path.join(server_dir, "server.properties") if server_dir else ""
        if not props_path or not os.path.isdir(server_dir):
            return False
        lines: List[str] = []
        if os.path.isfile(props_path):
            with open(props_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.read().splitlines()
        pending = dict(values)
        for i, l in enumerate(lines):
            key = l.split("=", 1)[0].strip()
            if not l.lstrip().startswith("#") and "=" in l and key in pending:
                lines[i] = f"{key}={pending.pop(key)}"
        lines.extend(f"{k}={v}" for k, v in pending.items())
        with open(props_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return True

    @classmethod
    def write_port_to_properties(cls, server_dir: str, port: int) -> bool:
        """Scrive server-port in server.properties lasciando invariato il resto. False se il file non si può scrivere."""
        return cls.write_properties(server_dir, {"server-port": str(port)})

    def _read_properties(self) -> Dict[str, str]:
        props: Dict[str, str] = {}
        server_dir = self.config.get("server_dir", "")
        try:
            with open(os.path.join(server_dir, "server.properties"), "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    s = line.strip()
                    if s and not s.startswith("#") and "=" in s:
                        k, v = s.split("=", 1)
                        props[k.strip()] = v.strip()
        except Exception:
            pass
        return props

    def ensure_rcon_enabled(self) -> bool:
        """Attiva RCON in server.properties (password casuale se manca). Serve per mandare comandi quando il server
        non è stato avviato da questo pannello (run.bat, pannello riaperto a server acceso): sui server nuovi è spento
        e i comandi non arrivavano. True se il file è stato cambiato (vale dal prossimo avvio del server)."""
        server_dir = self.config.get("server_dir", "")
        if not server_dir or not os.path.isfile(os.path.join(server_dir, "server.properties")):
            return False   # il file lo crea Minecraft al primo avvio: lo si sistema al successivo
        props = self._read_properties()
        changes: Dict[str, str] = {}
        if props.get("enable-rcon", "").lower() != "true":
            changes["enable-rcon"] = "true"
        if not props.get("rcon.password"):
            import secrets
            changes["rcon.password"] = secrets.token_hex(16)
        if not props.get("rcon.port", "").isdigit():
            changes["rcon.port"] = "25575"
        if changes:
            try:
                self.write_properties(server_dir, changes)
            except Exception:
                return False
        return bool(changes)

    def set_server_port(self, port: int) -> Dict[str, Any]:
        """Cambia la porta del server scelto: server.properties (quella che usa Minecraft), config.json e istanza attiva."""
        server_dir = self.config.get("server_dir", "")
        if not server_dir or not os.path.isdir(server_dir):
            return {"status": "error", "message": "Nessun server collegato: la porta non si può cambiare."}
        try:
            self.write_port_to_properties(server_dir, port)
        except Exception as e:
            return {"status": "error", "message": f"server.properties non scritto: {e}"}
        self.remember_server_port(port)
        self.add_log(f"[Pannello] Porta del server impostata a {port} in server.properties.")
        return {"status": "success", "port": port, "restart_needed": self.state in ("ONLINE", "STARTING")}

    def remember_server_port(self, port: int):
        """Allinea config.json e l'istanza attiva alla porta di server.properties (anche se cambiata a mano)."""
        with self.lock:
            if self.config.get("server_port") != port:
                self.config["server_port"] = port
                self.save_config()
        try:
            import instance_manager
            instances = instance_manager.load_instances_data()
            changed = False
            for inst in instances:
                if inst.get("is_active") and inst.get("server_port") != port:
                    inst["server_port"] = port
                    changed = True
            if changed:
                instance_manager.save_instances_data(instances)
        except Exception:
            pass

    def get_configured_max_players(self) -> int:
        """max-players da server.properties (20 se manca, come Minecraft)."""
        server_dir = self.config.get("server_dir", "")
        props_path = os.path.join(server_dir, "server.properties") if server_dir else ""
        if os.path.isfile(props_path):
            try:
                with open(props_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("max-players="):
                            val = line.split("=", 1)[1].strip()
                            if val.isdigit():
                                return int(val)
            except Exception:
                pass
        return 20

    def _find_run_bat(self) -> str:
        """File di avvio del server (run.bat/start.bat) usato da get_launch_command; "" se non c'è."""
        server_dir = self.config.get("server_dir", "")
        if not server_dir:
            return ""
        run_bat = self.config.get("server_run_bat") or os.path.join(server_dir, LAUNCH_SCRIPT)
        if not os.path.isabs(run_bat):
            run_bat = os.path.join(server_dir, run_bat)
        for c_bat in (run_bat,) + tuple(os.path.join(server_dir, n) for n in LAUNCH_SCRIPT_CANDIDATES):
            if os.path.isfile(c_bat):
                return c_bat
        return ""

    def get_launch_command(self) -> List[str]:
        server_dir = self.config.get("server_dir", "")
        run_bat = self.config.get("server_run_bat", os.path.join(server_dir, LAUNCH_SCRIPT))
        cmd_str = self.config.get("server_command", "").strip()

        # 1. If run.bat or start.bat exists, parse java arguments from it
        c_bat = self._find_run_bat()
        if c_bat:
            try:
                with open(c_bat, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        s = line.strip()
                        if s.startswith("java "):
                            return split_java_line(s, server_dir)
            except Exception:
                pass
            return script_fallback_command(c_bat)

        # 2. Check if a server.jar exists (Vanilla, Fabric, Paper, Spigot)
        jar_path = os.path.join(server_dir, "server.jar")
        if os.path.isfile(jar_path):
            return ["java", "-Xmx4G", "-Xms2G", "-jar", "server.jar", "nogui"]

        # 3. Fallback to configured custom command
        if cmd_str:
            return shlex.split(cmd_str, posix=not IS_WINDOWS)

        return script_fallback_command(run_bat)

    def start_server(self) -> Dict[str, Any]:
        # Backup o ripristino in corso (impostato da app.py): il server non parte, nemmeno dai riavvii automatici
        blocked = self.start_blocker() if self.start_blocker else None
        if blocked:
            self.add_log(f"[Pannello] Avvio del server bloccato. {blocked}")
            return {"status": "error", "message": blocked}
        with self.lock:
            if self.process and self.process.poll() is None:
                return {"status": "error", "message": "Il server è già in esecuzione!"}

            # Check if an existing Java server process is already running
            existing = self._find_running_minecraft_proc()
            if existing:
                self._attach_running_process(existing)
                return {"status": "error", "message": f"Il server Minecraft è già attivo in background (PID {existing.pid})!"}

            # Check if the server port is already listening
            port = self.get_server_port()
            slp = query_server_status(host="127.0.0.1", port=port, timeout=0.5)
            if slp.get("online"):
                self.state = "ONLINE"
                return {"status": "error", "message": "La porta del server Minecraft è già aperta e il server risponde!"}

            server_dir = self.config.get("server_dir", "")
            run_bat = self.config.get("server_run_bat", os.path.join(server_dir, LAUNCH_SCRIPT))

            if not os.path.isdir(server_dir):
                return {"status": "error", "message": f"Cartella del server non trovata: {server_dir}"}

            self.intentional_stop = False
            self.scheduled_restart_in_progress = False
            self.state = "STARTING"
            self.start_time = time.time()
            self.players.clear()
            self.warning_sent_flags.clear()
            self.crash_countdown = 0
            self.clear_errors()

            # Reset timer to full interval
            self.scheduled_remaining_seconds = self.scheduled_interval_seconds

            if self.ensure_rcon_enabled():
                self.add_log("[Pannello] RCON attivato in server.properties: i comandi dal pannello funzionano anche se "
                             "il pannello viene riaperto a server acceso.")
            cmd_parts = self.get_launch_command()
            self.add_log(f"[Pannello] Avvio server Minecraft in corso in: {server_dir}")
            self.add_log(f"[Pannello] File batch: {run_bat}")
            self.add_log(f"[Pannello] Comando rilevato ed eseguito: {' '.join(cmd_parts)}")

            try:
                self.process = subprocess.Popen(
                    cmd_parts,
                    cwd=server_dir,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=NO_WINDOW  # la console del server è nel pannello, nessuna finestra cmd
                )
                try:
                    self.psutil_proc = psutil.Process(self.process.pid)
                except Exception:
                    self.psutil_proc = None

                self.stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
                self.stdout_thread.start()

                self.monitor_thread = threading.Thread(target=self._monitor_exit, daemon=True)
                self.monitor_thread.start()

                return {"status": "success", "message": "Server avviato con successo!"}
            except Exception as e:
                self.state = "STOPPED"
                self.add_log(f"[Pannello/ERRORE] Impossibile avviare il processo: {e}")
                return {"status": "error", "message": str(e)}

    def send_command(self, cmd: str) -> Dict[str, Any]:
        with self.lock:
            cmd_clean = re.sub(r'[\r\n\0]', '', cmd).strip()
            if not cmd_clean:
                return {"status": "error", "message": "Comando vuoto"}
            if len(cmd_clean) > 350:
                return {"status": "error", "message": "Comando rifiutato: lunghezza eccessiva (max 350 caratteri)."}
            # Nessun filtro su | & ; ` $: il comando va alla console di Minecraft (stdin) o a RCON, mai a una shell
            # di Windows. Il filtro scartava in silenzio i messaggi in chat con questi caratteri ("alle 21; ci siete?").
            # A capo e \0 sono già tolti sopra: un comando non può diventarne due.

            if cmd_clean.startswith("/"):
                cmd_clean = cmd_clean[1:]

            is_proc_alive = (self.process and self.process.poll() is None)
            is_psutil_alive = (self.psutil_proc and self.psutil_proc.is_running())

            if not is_proc_alive and not is_psutil_alive:
                existing = self._find_running_minecraft_proc()
                if existing:
                    self._attach_running_process(existing)
                    is_psutil_alive = True
                else:
                    self.add_log(f"> {cmd_clean}")
                    self.add_log("[Pannello/WARN] Impossibile inviare comando: il server Minecraft è offline o non avviato.")
                    return {"status": "error", "message": "Il server non è attivo!"}

            # Strategy 1: Stdin Pipe if this app instance launched the server process
            if is_proc_alive and self.process.stdin:
                try:
                    self.add_log(f"> {cmd_clean}")
                    self.process.stdin.write(cmd_clean + "\n")
                    self.process.stdin.flush()
                    return {"status": "success", "message": f"Comando inviato: {cmd_clean}"}
                except Exception as e:
                    self.add_log(f"[Pannello/WARN] Invio console stdin non riuscito: {e}. Tento via RCON...")

            # Strategy 2: Minecraft RCON Protocol (active even after app restarts)
            rcon_resp = self._send_rcon_command(cmd_clean)
            if rcon_resp is not None:
                self.add_log(f"> {cmd_clean}")
                resp_clean = rcon_resp.strip()
                if resp_clean:
                    self.add_log(f"[RCON] {resp_clean}")
                return {"status": "success", "message": resp_clean or f"Comando eseguito via RCON: {cmd_clean}"}

            # Server non avviato da questo pannello e RCON spento o non raggiungibile: lo si scrive nella console
            # (prima il comando spariva senza nessun segno)
            self.add_log(f"> {cmd_clean}")
            if self.ensure_rcon_enabled():
                msg = ("Comando non inviato: il server non è stato avviato da questo pannello e RCON era spento. "
                       "RCON è stato attivato in server.properties: riavvia il server dal pannello e i comandi funzioneranno.")
            else:
                msg = ("Comando non inviato: il server non è stato avviato da questo pannello e RCON non risponde. "
                       "Riavvia il server dal pannello.")
            self.add_log(f"[Pannello/WARN] {msg}")
            return {"status": "error", "message": msg}

    def stop_server(self, force: bool = False, reason: str = "Chiusura manuale da pannello") -> Dict[str, Any]:
        with self.lock:
            # Arresto chiesto durante un riavvio: il riavvio si annulla (prima il server si riaccendeva lo stesso)
            restart_was_pending = self.scheduled_restart_in_progress or self.state == "RESTARTING"
            self.scheduled_restart_in_progress = False
            is_proc_running = (self.process and self.process.poll() is None)
            is_psutil_running = (self.psutil_proc and self.psutil_proc.is_running())
            if not is_proc_running and not is_psutil_running:
                existing = self._find_running_minecraft_proc()
                if existing:
                    self._attach_running_process(existing)
                    is_psutil_running = True
                else:
                    self.state = "STOPPED"
                    if restart_was_pending:
                        self.intentional_stop = True
                        self.add_log("[Pannello] Riavvio annullato: il server resta spento.")
                        return {"status": "success", "message": "Riavvio annullato: il server resta spento."}
                    return {"status": "error", "message": "Il server è già spento."}

            self.intentional_stop = True

            if force:
                self.add_log(f"[Pannello] ARRESTO FORZATO richiesto ({reason}). Terminazione processi in corso...")
                try:
                    if self.psutil_proc and self.psutil_proc.is_running():
                        for child in self.psutil_proc.children(recursive=True):
                            try: child.kill()
                            except Exception: pass
                        self.psutil_proc.kill()
                    if self.process and self.process.poll() is None:
                        try: self.process.kill()
                        except Exception: pass
                    self.state = "STOPPED"
                    self.process = None
                    self.psutil_proc = None
                    self.add_log("[Pannello] Processo terminato forzatamente con successo.")
                    return {"status": "success", "message": "Server arrestato forzatamente!"}
                except Exception as e:
                    return {"status": "error", "message": f"Errore nell'arresto forzato: {e}"}

            self.state = "STOPPING"
            self.add_log(f"[Pannello] Arresto sicuro in corso ({reason})...")
            threading.Thread(target=self._graceful_stop_worker, args=(reason,), daemon=True).start()
            return {"status": "success", "message": "Procedura di arresto sicuro avviata."}

    def _graceful_stop_worker(self, reason: str):
        try:
            self.send_command(f"say §c[Server] ATTENZIONE: Il server si sta arrestando ({reason})")
            time.sleep(1)
            self.send_command("save-all")
            time.sleep(2)
            self.send_command("stop")

            # Wait up to 25 seconds for the process to exit cleanly
            for _ in range(25):
                is_alive = False
                if self.process and self.process.poll() is None:
                    is_alive = True
                if self.psutil_proc and self.psutil_proc.is_running():
                    is_alive = True
                if not is_alive:
                    break
                time.sleep(1)

            # If still alive after 25s, kill it
            still_alive = (self.process and self.process.poll() is None) or \
                          (self.psutil_proc and self.psutil_proc.is_running())
            if still_alive:
                self.add_log("[Pannello] Il server non ha risposto a 'stop' dopo 25s. Terminazione forzata...")
                if self.psutil_proc and self.psutil_proc.is_running():
                    for child in self.psutil_proc.children(recursive=True):
                        try: child.kill()
                        except Exception: pass
                    self.psutil_proc.kill()
                if self.process and self.process.poll() is None:
                    try: self.process.kill()
                    except Exception: pass

            with self.lock:
                # _monitor_exit può aver già impostato RESTARTING e avviato il riavvio: non sovrascriverlo
                restarting = self.state == "RESTARTING"
                if not restarting:
                    self.state = "STOPPED"
                self.process = None
                self.psutil_proc = None
                self.server_info["online"] = False
                self.players.clear()
                if not restarting:
                    self.add_log("[Pannello] Server arrestato con successo.")

                restart_pending = self.scheduled_restart_in_progress
                self.scheduled_restart_in_progress = False  # evita che _monitor_exit lanci un secondo riavvio
                if restart_pending and self.state != "RESTARTING":
                    self.state = "RESTARTING"
                    self.add_log("[Pannello] Server precedente terminato. Riavvio in corso tra 3 secondi...")
                    threading.Thread(target=self._delayed_restart_worker, args=(3,), daemon=True).start()
        except Exception as e:
            self.add_log(f"[Pannello/ERRORE] Errore durante l'arresto: {e}")

    def restart_server(self, reason: str = "Riavvio manuale", warning_countdown: int = 0) -> Dict[str, Any]:
        with self.lock:
            is_proc_alive = (self.process and self.process.poll() is None)
            is_psutil_alive = (self.psutil_proc and self.psutil_proc.is_running())

            if not is_proc_alive and not is_psutil_alive:
                existing = self._find_running_minecraft_proc()
                if existing:
                    self._attach_running_process(existing)
                    is_psutil_alive = True
                else:
                    return self.start_server()

            self.scheduled_restart_in_progress = True
            self.intentional_stop = True

            if warning_countdown > 0:
                threading.Thread(target=self._countdown_and_restart, args=(warning_countdown, reason), daemon=True).start()
                return {"status": "success", "message": f"Riavvio programmato tra {warning_countdown} secondi."}
            else:
                self.add_log(f"[Pannello] Riavvio immediato ({reason})...")
                threading.Thread(target=self._graceful_stop_worker, args=(reason,), daemon=True).start()
                return {"status": "success", "message": "Riavvio in corso..."}

    def _countdown_and_restart(self, seconds: int, reason: str):
        while seconds > 0:
            if seconds in (60, 30, 15, 10, 5, 4, 3, 2, 1):
                self.send_command(f"say §c[Server] Riavvio tra {seconds} secondi ({reason})!")
            time.sleep(1)
            seconds -= 1
        self._graceful_stop_worker(reason)

    def postpone_scheduled_restart(self, minutes: int = 30) -> Dict[str, Any]:
        with self.lock:
            self.scheduled_remaining_seconds += minutes * 60
            self.add_log(f"[Pannello] Riavvio programmato posticipato di {minutes} minuti. Tempo residuo: {self.format_seconds(self.scheduled_remaining_seconds)}")
            self.send_command(f"say §a[Server] Riavvio programmato posticipato di {minutes} minuti dall'amministratore.")
            return {
                "status": "success",
                "remaining_seconds": self.scheduled_remaining_seconds,
                "formatted": self.format_seconds(self.scheduled_remaining_seconds)
            }

    def reset_scheduled_timer(self) -> Dict[str, Any]:
        with self.lock:
            self.scheduled_remaining_seconds = self.scheduled_interval_seconds
            self.warning_sent_flags.clear()
            self.add_log(f"[Pannello] Timer riavvio reimpostato a {self.config.get('scheduled_restart_interval_minutes', 240)} minuti.")
            return {
                "status": "success",
                "remaining_seconds": self.scheduled_remaining_seconds,
                "formatted": self.format_seconds(self.scheduled_remaining_seconds)
            }

    def toggle_scheduled_restart(self, enabled: Optional[bool] = None) -> Dict[str, Any]:
        with self.lock:
            if enabled is None:
                self.scheduled_enabled = not self.scheduled_enabled
            else:
                self.scheduled_enabled = bool(enabled)
            self.config["scheduled_restart_enabled"] = self.scheduled_enabled
            self.save_config()
            status_text = "ATTIVATO" if self.scheduled_enabled else "DISATTIVATO"
            self.add_log(f"[Pannello] Riavvio automatico ogni 4 ore: {status_text}")
            return {"status": "success", "enabled": self.scheduled_enabled}

    def set_scheduled_interval(self, minutes: int) -> Dict[str, Any]:
        with self.lock:
            minutes = max(10, min(1440, minutes))
            self.config["scheduled_restart_interval_minutes"] = minutes
            self.scheduled_interval_seconds = minutes * 60
            self.scheduled_remaining_seconds = min(self.scheduled_remaining_seconds, self.scheduled_interval_seconds)
            self.save_config()
            self.add_log(f"[Pannello] Intervallo riavvio automatico aggiornato a {minutes} minuti.")
            return {"status": "success", "minutes": minutes}

    def toggle_auto_restart_crash(self, enabled: Optional[bool] = None) -> Dict[str, Any]:
        with self.lock:
            if enabled is None:
                self.auto_restart_on_crash = not self.auto_restart_on_crash
            else:
                self.auto_restart_on_crash = bool(enabled)
            self.config["auto_restart_crash"] = self.auto_restart_on_crash
            self.save_config()
            status_text = "ATTIVATO" if self.auto_restart_on_crash else "DISATTIVATO"
            self.add_log(f"[Pannello] Riavvio automatico su crash: {status_text}")
            return {"status": "success", "enabled": self.auto_restart_on_crash}

    def reset_crash_paused(self) -> Dict[str, Any]:
        with self.lock:
            self.auto_restart_paused = False
            self.crash_countdown = 0
            self.crash_history.clear()
            if self.state == "CRASHED":
                self.state = "STOPPED"
            self.add_log("[Pannello] Blocco riavvio crash resettato. Il riavvio automatico e lo stato sono stati ripristinati.")
            return {"status": "success", "message": "Protezione crash ripristinata e banner azzerato."}

    # =========================================================
    # IN-GAME CHAT & TALK METHODS ("PARLARCI DENTRO")
    # =========================================================
    def talk_to_server(self, message: str, sender: str = "Host") -> Dict[str, Any]:
        """
        Allows the user to speak directly into the Minecraft server!
        Also supports smart conversational commands (e.g. 'fai giorno', 'sole', 'salva').
        """
        text = message.strip()
        if not text:
            return {"status": "error", "message": "Messaggio vuoto"}

        now_str = datetime.now().strftime("%H:%M:%S")

        is_proc_alive = (self.process and self.process.poll() is None)
        is_psutil_alive = (self.psutil_proc and self.psutil_proc.is_running())
        is_online = is_proc_alive or is_psutil_alive

        # Check for smart commands first
        t_lower = text.lower()
        if t_lower in ("giorno", "fai giorno", "metti giorno", "/time set day"):
            self.send_command("time set day")
            reply = "Orario impostato su giorno." if is_online else "Server offline: comando non inviato."
        elif t_lower in ("notte", "fai notte", "metti notte", "/time set night"):
            self.send_command("time set night")
            reply = "Orario impostato su notte." if is_online else "Server offline: comando non inviato."
        elif t_lower in ("sole", "bel tempo", "metti sole", "metti sereno", "/weather clear"):
            self.send_command("weather clear")
            reply = "Meteo impostato su sereno." if is_online else "Server offline: comando non inviato."
        elif t_lower in ("pioggia", "metti pioggia", "/weather rain"):
            self.send_command("weather rain")
            reply = "Meteo impostato su pioggia." if is_online else "Server offline: comando non inviato."
        elif t_lower in ("salva", "salva tutto", "salvataggio", "/save-all"):
            self.send_command("save-all")
            reply = "Salvataggio forzato del mondo avviato." if is_online else "Server offline: nessun salvataggio necessario."
        elif t_lower.startswith("riavvia"):
            if is_online:
                self.restart_server(reason="Riavvio richiesto dalla chat", warning_countdown=10)
                reply = "Riavvio programmato tra 10 secondi."
            else:
                self.start_server()
                reply = "Avvio del server in corso..."
        elif text.startswith("/"):
            # Raw command
            self.send_command(text)
            reply = f"Comando '{text}' inviato al server." if is_online else "Server offline: comando non eseguito."
        else:
            # Plain chat message to the players in-game!
            if is_online:
                sent = self.send_command(f'say §a[{sender}] §f{text}')
                # Se il messaggio non parte lo si dice nella chat del pannello (prima sembrava inviato)
                reply = None if sent.get("status") != "error" else f"Messaggio non inviato: {sent.get('message')}"
            else:
                reply = "Server offline: messaggio visibile solo nel pannello (il server di gioco è spento)." 

        # Add message to internal chat history
        chat_item = {
            "id": str(uuid.uuid4())[:8],
            "time": now_str,
            "sender": sender,
            "text": text,
            "type": "host",
            "reply": reply
        }
        with self.lock:
            self.chat_messages.append(chat_item)
            self._broadcast_chat(chat_item)

        return {"status": "success", "item": chat_item}

    def clear_chat(self) -> Dict[str, Any]:
        """Clears the in-memory chat message buffer."""
        with self.lock:
            self.chat_messages.clear()
            self.add_log("[Pannello] Cronologia chat svuotata dall'amministratore.")
            self._broadcast_chat({"type": "clear_chat"})
        return {"status": "success", "message": "Chat ripulita."}

    # =========================================================
    # ERROR TRACKING & CRASH REPORT SCANNING
    # =========================================================
    def scan_crash_reports(self) -> List[Dict[str, Any]]:
        """Scans crash-reports/ folder in the server directory and parses details."""
        server_dir = self.config.get("server_dir", "")
        crash_dir = os.path.join(server_dir, "crash-reports")
        reports = []
        if os.path.isdir(crash_dir):
            try:
                txt_files = [f for f in os.listdir(crash_dir) if f.endswith(".txt")]
                txt_files.sort(key=lambda f: os.path.getmtime(os.path.join(crash_dir, f)), reverse=True)
                for fname in txt_files[:20]: # inspect last 20 crash files
                    fpath = os.path.join(crash_dir, fname)
                    try:
                        size_bytes = os.path.getsize(fpath)
                        size_kb = round(size_bytes / 1024.0, 1)
                        size_formatted = f"{size_kb} KB"
                        with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                            header = f.read(4000) # read first 4KB
                            time_m = re.search(r"Time:\s*(.*)", header)
                            desc_m = re.search(r"Description:\s*(.*)", header)
                            err_m = re.search(r"(java\.lang\.\w+:\s*.*)", header)

                            time_val = time_m.group(1).strip() if time_m else fname
                            desc_val = desc_m.group(1).strip() if desc_m else "Crash Report generato"
                            err_val = err_m.group(1).strip() if err_m else desc_val

                            reports.append({
                                "filename": fname,
                                "time": time_val,
                                "description": desc_val,
                                "error_summary": err_val[:140],
                                "size_formatted": size_formatted,
                                "path": fpath
                            })
                    except Exception:
                        pass
            except Exception:
                pass
        return reports

    def clear_errors(self):
        with self.lock:
            self.errors_list.clear()
        self._broadcast_error({"type": "clear"})

    # =========================================================
    # PARSING LOG LINES
    # =========================================================
    def _read_stdout(self):
        proc = self.process
        if not proc or not proc.stdout:
            return

        for line in iter(proc.stdout.readline, ''):
            if not line:
                break
            line_str = line.strip()
            self.add_log(line_str)
            self._parse_log_line(line_str)

        proc.stdout.close()

    def _parse_log_line(self, line: str):
        """Extracts status transitions, player connections, chat, and server errors."""
        now_time = datetime.now().strftime("%H:%M:%S")

        # 1. Server Ready Detection
        if self._RE_SERVER_READY.search(line) or \
           self._RE_SERVER_READY2.search(line) or \
           self._RE_SERVER_READY3.search(line):
            if self.state not in ("ONLINE", "STOPPING", "RESTARTING"):
                self.state = "ONLINE"
                self.add_log("[Pannello] SERVER MINECRAFT COMPLETAMENTE ONLINE E PRONTO!")

        # 2. Player Join Detection
        join_match = self._RE_PLAYER_JOIN.search(line)
        if not join_match:
            join_match = self._RE_PLAYER_JOIN2.search(line)
            
        if join_match:
            player_name = join_match.group(1)
            with self.lock:
                if player_name not in self.players:
                    self.players[player_name] = {
                        "name": player_name,
                        "joined_at": time.time(),
                        "joined_str": now_time
                    }
                    self.add_log(f"[Pannello] Giocatore entrato: {player_name} (Totale online: {len(self.players)})")
                    # Add system announcement to chat
                    chat_item = {
                        "id": str(uuid.uuid4())[:8],
                        "time": now_time,
                        "sender": "Server",
                        "text": f"{player_name} è entrato nel gioco.",
                        "type": "system"
                    }
                    self.chat_messages.append(chat_item)
                    self._broadcast_chat(chat_item)
                    self.send_push_notification(
                        title="Giocatore Entrato",
                        message=f"{player_name} è appena entrato nel server! (Giocatori online: {len(self.players)})",
                        priority="default",
                        tags="video_game,bust_in_silhouette"
                    )

        # 3. Player Leave Detection
        leave_match = self._RE_PLAYER_LEAVE.search(line)
        if not leave_match:
            leave_match = self._RE_PLAYER_LEAVE2.search(line)
        if not leave_match:
            leave_match = self._RE_PLAYER_LEAVE3.search(line)

        if leave_match:
            player_name = leave_match.group(1)
            with self.lock:
                if player_name in self.players:
                    del self.players[player_name]
                    self.add_log(f"[Pannello] Giocatore uscito: {player_name} (Totale online: {len(self.players)})")
                    chat_item = {
                        "id": str(uuid.uuid4())[:8],
                        "time": now_time,
                        "sender": "Server",
                        "text": f"{player_name} è uscito dal gioco.",
                        "type": "system"
                    }
                    self.chat_messages.append(chat_item)
                    self._broadcast_chat(chat_item)
                    self.send_push_notification(
                        title="Giocatore Uscito",
                        message=f"{player_name} è uscito dal server. (Rimanenti: {len(self.players)})",
                        priority="low",
                        tags="wave,door"
                    )

        # 4. In-Game Player Chat Detection
        # Examples:
        # [Server thread/INFO]: <PlayerName> Ciao a tutti!
        # [Server thread/INFO]: [Not Secure] <PlayerName> Messaggio
        # [Server thread/INFO]: [Server] Avviso
        chat_match = self._RE_CHAT_MSG.search(line)
        if chat_match:
            p_name = chat_match.group(1)
            p_text = chat_match.group(2)
            chat_item = {
                "id": str(uuid.uuid4())[:8],
                "time": now_time,
                "sender": p_name,
                "text": p_text,
                "type": "player",
                "avatar_url": f"https://mc-heads.net/avatar/{p_name}/32"
            }
            with self.lock:
                self.chat_messages.append(chat_item)
                self._broadcast_chat(chat_item)

        # 5. Server Errors & Warnings Detection
        # Catches: [ERROR], [FATAL], Exceptions, Can't keep up!, ServerHangWatchdog
        is_error = False
        level = "INFO"
        err_msg = ""

        level_match = self._RE_LOG_LEVEL.match(line)
        log_level = level_match.group(1) if level_match else None

        if any(h in line for h in self._HARMLESS_ERRORS):
            pass
        elif log_level in ("ERROR", "FATAL"):
            is_error = True
            level = "ERROR"
            # Extract readable message after log header
            parts = line.split("]: ", 1)
            err_msg = parts[1] if len(parts) > 1 else line
        elif "ServerHangWatchdog detected" in line or "Crash report saved to" in line:
            is_error = True
            level = "CRITICAL"
            err_msg = line
        elif "Can't keep up! Is the server overloaded?" in line:
            is_error = True
            level = "WARN"
            cant_up = self._RE_CANT_KEEP_UP.search(line)
            if cant_up:
                ms_behind = int(cant_up.group(1))
                ticks_behind = int(cant_up.group(2))
                loss_pct = min(0.75, ms_behind / (ms_behind + 1000.0))
                calc_tps = max(5.0, round(20.0 * (1.0 - loss_pct), 1))
                with self.lock:
                    self.current_tps = min(self.current_tps, calc_tps)
                    self.last_tps_drop_time = time.time()
                err_msg = f"Sovraccarico server: in ritardo di {ms_behind}ms ({ticks_behind} ticks). TPS stimati: {calc_tps}."
            else:
                with self.lock:
                    self.current_tps = min(self.current_tps, 15.0)
                    self.last_tps_drop_time = time.time()
                err_msg = "Il server è sovraccarico (Lag tick rilevato). Il tempo di esecuzione è superiore a 50ms."
        elif log_level is None and self._RE_EXCEPTION.search(line):
            # Eccezione senza intestazione: riga di uno stack trace. Con intestazione INFO/WARN la mod l'ha già gestita.
            is_error = True
            level = "ERROR"
            err_msg = line

        if is_error:
            error_item = {
                "id": str(uuid.uuid4())[:8],
                "time": now_time,
                "level": level,
                "message": err_msg[:250],
                "raw": line
            }
            with self.lock:
                self.errors_list.append(error_item)
                self._broadcast_error(error_item)

    def _monitor_exit(self):
        proc = self.process
        if not proc:
            return

        exit_code = proc.wait()
        
        with self.lock:
            if self.process is not None and self.process is not proc:
                return
            self.process = None
            self.psutil_proc = None
            self.players.clear()
            self.server_info["online"] = False

            if self.scheduled_restart_in_progress:
                self.scheduled_restart_in_progress = False
                self.state = "RESTARTING"
                self.add_log("[Pannello] Riavvio in corso... Avvio del server tra 3 secondi...")
                threading.Thread(target=self._delayed_restart_worker, args=(3,), daemon=True).start()
                return

            if self.intentional_stop or exit_code == 0:
                self.state = "STOPPED"
                self.add_log(f"[Pannello] Server arrestato (Codice di uscita: {exit_code}).")
                return

            # Check if another Minecraft Java process is still running online
            existing = self._find_running_minecraft_proc()
            if existing:
                self._attach_running_process(existing)
                self.add_log(f"[Pannello] Processo di lancio completato. Server Minecraft attivo (PID {existing.pid}).")
                return

            # UNINTENTIONAL TERMINATION -> CRASH!
            self.state = "CRASHED"
            crash_entry = {
                "timestamp": time.time(),
                "time_str": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "exit_code": exit_code
            }
            self.crash_history.append(crash_entry)
            self.add_log(f"[Pannello/ALLARME] IL SERVER MINECRAFT SI È CHIUSO INASPETTATAMENTE! (Codice di uscita: {exit_code})")

            # Record in error list
            err_item = {
                "id": str(uuid.uuid4())[:8],
                "time": datetime.now().strftime("%H:%M:%S"),
                "level": "CRITICAL",
                "message": f"Crash imprevisto del server Minecraft. Processo terminato con codice {exit_code}.",
                "text": f"Crash imprevisto del server Minecraft. Processo terminato con codice {exit_code}.",
                "raw": f"Process exited with code {exit_code}"
            }
            self.errors_list.append(err_item)
            self._broadcast_error(err_item)

            # Send instant Push Notification to smartphone via ntfy (0% phone battery)
            self.send_push_notification(
                title="Server Minecraft Crashato!",
                message=f"Il server si è arrestato inaspettatamente (Codice di uscita: {exit_code}). Tocca per aprire la gestione.",
                priority="max",
                tags="warning,skull"
            )

            now = time.time()
            recent_crashes = [c for c in self.crash_history if now - c["timestamp"] <= self.crash_window_seconds]
            
            if len(recent_crashes) >= self.max_crashes_in_window:
                self.auto_restart_paused = True
                self.add_log(f"[Pannello/CRITICO] Rilevati {len(recent_crashes)} crash negli ultimi {self.crash_window_seconds} secondi. "
                             f"Riavvio automatico SOSPESO per proteggere il sistema da crash loop. Controllare crash-reports!")
                return

            if self.auto_restart_on_crash and not self.auto_restart_paused:
                self.add_log(f"[Pannello] Riavvio automatico programmato tra {self.crash_retry_delay} secondi...")
                threading.Thread(target=self._crash_auto_restart_worker, daemon=True).start()
            else:
                self.add_log("[Pannello] Il riavvio automatico su crash è disattivato.")

    def _crash_auto_restart_worker(self):
        self.crash_countdown = self.crash_retry_delay
        while self.crash_countdown > 0:
            if self.state != "CRASHED":
                return
            time.sleep(1)
            self.crash_countdown -= 1
        
        with self.lock:
            if self.state == "CRASHED" and not self.auto_restart_paused:
                self.add_log("[Pannello] Esecuzione riavvio automatico di emergenza...")
                self.start_server()

    def _delayed_restart_worker(self, delay: int):
        time.sleep(delay)
        for _ in range(15):
            existing = self._find_running_minecraft_proc()
            port = self.get_server_port()
            slp = query_server_status(host="127.0.0.1", port=port, timeout=0.3)
            if not existing and not slp.get("online"):
                break
            time.sleep(1)
        time.sleep(1)
        with self.lock:
            # Nel frattempo è stato chiesto l'arresto (stato non più RESTARTING): niente riavvio
            if self.state != "RESTARTING":
                return
            self.start_server()

    def _ticker_loop(self):
        query_counter = 0
        while self.running:
            time.sleep(1)
            with self.lock:
                if self.state == "ONLINE" and self.scheduled_enabled:
                    self.scheduled_remaining_seconds -= 1
                    rem = self.scheduled_remaining_seconds
                    
                    if self.config.get("chat_warnings_enabled", True):
                        if rem == 900 and "w15m" not in self.warning_sent_flags:
                            self.warning_sent_flags.add("w15m")
                            self.send_command("say §e[Server] Riavvio programmato per ottimizzare le risorse del PC tra 15 minuti!")
                        elif rem == 600 and "w10m" not in self.warning_sent_flags:
                            self.warning_sent_flags.add("w10m")
                            self.send_command("say §e[Server] Riavvio programmato tra 10 minuti!")
                        elif rem == 300 and "w5m" not in self.warning_sent_flags:
                            self.warning_sent_flags.add("w5m")
                            self.send_command("say §6[Server] ATTENZIONE: Riavvio programmato tra 5 minuti!")
                        elif rem == 120 and "w2m" not in self.warning_sent_flags:
                            self.warning_sent_flags.add("w2m")
                            self.send_command("say §c[Server] ATTENZIONE: Riavvio programmato tra 2 minuti! Mettetevi al sicuro!")
                        elif rem == 60 and "w1m" not in self.warning_sent_flags:
                            self.warning_sent_flags.add("w1m")
                            self.send_command("say §c[Server] Riavvio tra 60 secondi! Salvataggio mondo imminente!")
                        elif rem == 30 and "w30s" not in self.warning_sent_flags:
                            self.warning_sent_flags.add("w30s")
                            self.send_command("say §c[Server] Riavvio tra 30 secondi...")
                        elif rem in (10, 5, 4, 3, 2, 1):
                            self.send_command(f"say §c[Server] Riavvio tra {rem}...")

                    if rem <= 0:
                        self.add_log("[Pannello] ⏱️ Timer 4 ore scaduto. Avvio riavvio programmato del server...")
                        self.scheduled_restart_in_progress = True
                        self.scheduled_remaining_seconds = self.scheduled_interval_seconds
                        self.warning_sent_flags.clear()
                        threading.Thread(target=self._graceful_stop_worker, args=("Riavvio programmato 4 ore per liberare RAM",), daemon=True).start()

                # --- ECO MODE CHECK ---
                if self.state == "ONLINE" and self.eco_mode_enabled:
                    online_players = len(self.players)
                    if online_players == 0:
                        if self.server_empty_since is None:
                            self.server_empty_since = time.time()
                        elif (time.time() - self.server_empty_since) >= (self.eco_mode_minutes * 60):
                            self.add_log(f"[Pannello] Modalità Eco: server vuoto da oltre {self.eco_mode_minutes} minuti. Spegnimento automatico per risparmiare RAM ed energia...")
                            self.send_push_notification(
                                title="Modalità Eco Attivata",
                                message=f"Server vuoto da {self.eco_mode_minutes} min: arrestato automaticamente per liberare RAM. Puoi riavviarlo con 1 tocco quando vuoi!",
                                priority="default",
                                tags="zzz,battery"
                            )
                            self.server_empty_since = None
                            threading.Thread(target=self._graceful_stop_worker, args=("Spegnimento automatico Modalità Eco",), daemon=True).start()
                    else:
                        self.server_empty_since = None
                else:
                    self.server_empty_since = None

                # --- ANTI-LAG WATCHDOG CHECK ---
                if self.state == "ONLINE" and self.anti_lag_enabled:
                    current_tps = self.get_current_tps()
                    if current_tps < 13.0:
                        if self.low_tps_start_time is None:
                            self.low_tps_start_time = time.time()
                        elif (time.time() - self.low_tps_start_time) >= 60.0: # Persistent low TPS for 1 minute
                            if (time.time() - self._last_lag_alert_time) > 300.0: # Alert at most once every 5 minutes
                                self._last_lag_alert_time = time.time()
                                self.add_log(f"[Pannello] Anti-Lag Watchdog: rilevato calo critico di prestazioni ({current_tps} TPS).")
                                if self.anti_lag_auto_clean:
                                    self.add_log("[Pannello] Esecuzione automatica pulizia entità a terra (/kill @e[type=item])...")
                                    self.send_command("kill @e[type=item]")
                                    self.send_push_notification(
                                        title="Anti-Lag Watchdog",
                                        message=f"Rilevato calo a {current_tps} TPS! Eseguita pulizia automatica drop a terra per ripristinare la fluidità.",
                                        priority="high",
                                        tags="warning,broom"
                                    )
                                else:
                                    self.send_push_notification(
                                        title="Allarme Lag Server",
                                        message=f"Il server sta girando a {current_tps} TPS! Possibile sovraccarico entità o chunk.",
                                        priority="high",
                                        tags="warning,turtle"
                                    )
                    else:
                        self.low_tps_start_time = None

                query_counter += 1
                if query_counter >= 2:
                    query_counter = 0
                    port = self.get_server_port()
                    threading.Thread(target=self._background_slp_query, args=(port,), daemon=True).start()

                self._record_metrics_snapshot()

    def _background_slp_query(self, port: int):
        res = query_server_status(host="127.0.0.1", port=port, timeout=1.0)
        with self.lock:
            self.server_info = res
            # ONLINE visto solo dal ping (nessun processo nostro) e il server non risponde più: chiuso fuori dal
            # pannello. Prima lo stato restava ONLINE per sempre. Tre controlli di fila (~6 s) per non sbagliare.
            no_process = not (self.process and self.process.poll() is None) and                 not (self.psutil_proc and self.psutil_proc.is_running())
            if not res.get("online") and self.state == "ONLINE" and no_process:
                self._slp_offline_count = getattr(self, "_slp_offline_count", 0) + 1
                if self._slp_offline_count >= 3 and not self._find_running_minecraft_proc():
                    self._slp_offline_count = 0
                    self.state = "STOPPED"
                    self.players.clear()
                    self.add_log("[Pannello] Il server non risponde più e non è in esecuzione: segnato come spento.")
            else:
                self._slp_offline_count = 0
            if res.get("online"):
                if self.state not in ("STOPPING", "RESTARTING"):
                    self.state = "ONLINE"
                self.auto_restart_paused = False
                self.crash_countdown = 0
                if not self.psutil_proc or not self.psutil_proc.is_running():
                    existing = self._find_running_minecraft_proc()
                    if existing:
                        self.psutil_proc = existing
                        try:
                            self.start_time = existing.create_time()
                        except Exception:
                            pass
                
                online_cnt = res.get("players_online", 0)
                sample = res.get("players_sample", [])
                
                # If Minecraft reports 0 players online, immediately purge any ghost players!
                if online_cnt == 0:
                    self.players.clear()
                else:
                    valid_names = set()
                    for p in sample:
                        p_name = p.get("name")
                        if p_name and not p_name.lower().startswith("anonymous") and p_name.lower() not in ("null", "unknown", "anonimo"):
                            valid_names.add(p_name)
                    
                    if valid_names:
                        for p_k in list(self.players.keys()):
                            if p_k not in valid_names:
                                del self.players[p_k]
                        for p_name in valid_names:
                            if p_name not in self.players:
                                self.players[p_name] = {
                                    "name": p_name,
                                    "joined_at": time.time(),
                                    "joined_str": datetime.now().strftime("%H:%M:%S")
                                }

    _cached_max_ram_val = None   # (cartella del server, ora della lettura, GB)
    _cached_disk_tuple = None
    _last_disk_check_time = 0.0
    _cached_heap_tuple = None
    _cached_heap_time = 0.0

    def _find_jcmd(self, pid: int) -> Optional[str]:
        """jcmd della stessa Java che fa girare il server (deve combaciare con la sua versione);
        altrimenti quello nel PATH o in una cartella di installazione di Java. Risultato memorizzato per processo."""
        cached = getattr(self, "_jcmd_cache", None)
        if cached and cached[0] == pid:
            return cached[1]
        exe_name = "jcmd.exe" if os.name == "nt" else "jcmd"
        found = None
        try:
            java_exe = psutil.Process(pid).exe()
            cand = os.path.join(os.path.dirname(java_exe), exe_name)
            if os.path.isfile(cand):
                found = cand
        except Exception:
            pass
        if not found:
            found = shutil.which("jcmd")
        if not found and os.name == "nt":
            # JDK installati senza PATH (Adoptium, Oracle, Microsoft, Zulu, ...): il più recente
            cands = []
            for root in {os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramW6432", "")}:
                if not root or not os.path.isdir(root):
                    continue
                for vendor in ("Eclipse Adoptium", "Java", "Microsoft", "Zulu", "Amazon Corretto", "BellSoft", "OpenJDK"):
                    vdir = os.path.join(root, vendor)
                    if not os.path.isdir(vdir):
                        continue
                    for jdk in os.listdir(vdir):
                        cand = os.path.join(vdir, jdk, "bin", exe_name)
                        if os.path.isfile(cand):
                            cands.append(cand)
            if cands:
                found = max(cands, key=os.path.getmtime)
        self._jcmd_cache = (pid, found)
        return found

    def _get_jvm_heap_usage(self, pid: int):
        """Queries actual Java Heap used by Minecraft Forge via jcmd GC.heap_info."""
        now = time.time()
        if self._cached_heap_tuple and (now - self._cached_heap_time) < 2.0:
            return self._cached_heap_tuple
        try:
            jcmd_bin = self._find_jcmd(pid)
            if not jcmd_bin:
                return self._cached_heap_tuple

            p = subprocess.run(
                [jcmd_bin, str(pid), "GC.heap_info"],
                capture_output=True,
                text=True,
                timeout=1.2,
                # Senza questo flag ogni lettura (ogni ~2 s) apriva e chiudeva una finestra cmd
                creationflags=NO_WINDOW
            )
            if p.returncode == 0 and p.stdout:
                m = re.search(r'total\s+(\d+)K,\s+used\s+(\d+)K', p.stdout, re.IGNORECASE)
                if m:
                    tot_k = int(m.group(1))
                    used_k = int(m.group(2))
                    used_gb = round(used_k / (1024**2), 2)
                    tot_gb = round(tot_k / (1024**2), 2)
                    self._cached_heap_tuple = (used_gb, tot_gb)
                    self._cached_heap_time = now
                    return self._cached_heap_tuple
        except Exception:
            pass
        return self._cached_heap_tuple

    def _uses_jvm_args_file(self) -> bool:
        """True se il server prende la RAM da user_jvm_args.txt (Forge/NeoForge moderni), False se dal comando java."""
        try:
            return "@user_jvm_args.txt" in " ".join(self.get_launch_command())
        except Exception:
            return False

    def _read_xmx_gb(self) -> Optional[float]:
        """-Xmx del server scelto: da user_jvm_args.txt o dal comando di avvio (run.bat, server.jar). None se manca."""
        server_dir = self.config.get("server_dir", "")
        if not server_dir:
            return None
        text = ""
        if self._uses_jvm_args_file():
            try:
                with open(os.path.join(server_dir, "user_jvm_args.txt"), "r", encoding="utf-8", errors="ignore") as f:
                    text = " ".join(l.strip() for l in f if l.strip() and not l.strip().startswith("#"))
            except Exception:
                text = ""
        else:
            try:
                text = " ".join(self.get_launch_command())
            except Exception:
                text = ""
        m = re.search(r'-Xmx(\d+)([GgMm])', text)
        if not m:
            return None
        val = float(m.group(1))
        return val if m.group(2) in "Gg" else round(val / 1024, 1)

    def get_configured_max_ram_gb(self) -> float:
        # Riletto ogni pochi secondi: prima restava in memoria per sempre e non seguiva le modifiche della RAM
        now = time.time()
        key = self.config.get("server_dir", "")
        cached = self._cached_max_ram_val
        if cached and cached[0] == key and now - cached[1] < 5.0:
            return cached[2]
        val = self._read_xmx_gb()
        if not val:
            # Senza -Xmx Java usa un quarto della RAM del PC
            try:
                val = round(psutil.virtual_memory().total / (1024 ** 3) / 4, 1)
            except Exception:
                val = 4.0
        self._cached_max_ram_val = (key, now, val)
        return val

    def _record_metrics_snapshot(self):
        try:
            host_cpu = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory()
            host_ram_used_gb = round(mem.used / (1024**3), 2)
            host_ram_total_gb = round(mem.total / (1024**3), 2)
            host_ram_percent = mem.percent

            mc_ram_mb = 0
            mc_ram_gb = 0
            mc_rss_gb = 0
            mc_cpu = 0
            max_mc_ram = self.get_configured_max_ram_gb()

            if self.psutil_proc and self.psutil_proc.is_running():
                try:
                    p_mem = self.psutil_proc.memory_info()
                    mc_rss_gb = round(p_mem.rss / (1024**3), 2)
                    # psutil misura il processo su un solo core (fino a 100% x core): lo riporta su tutta la CPU
                    mc_cpu = round(min(100.0, self.psutil_proc.cpu_percent(interval=None) / (psutil.cpu_count() or 1)), 1)

                    heap_info = self._get_jvm_heap_usage(self.psutil_proc.pid)
                    if heap_info:
                        heap_used_gb, heap_tot_gb = heap_info
                        mc_ram_gb = heap_used_gb
                        if heap_tot_gb > 0:
                            max_mc_ram = heap_tot_gb
                        mc_ram_mb = round(heap_used_gb * 1024, 1)
                    else:
                        mc_ram_gb = min(max_mc_ram, mc_rss_gb)
                        mc_ram_mb = round(mc_ram_gb * 1024, 1)
                except Exception:
                    pass

            # Cache disk usage every 30 seconds to avoid constant disk drive wakeups
            now = time.time()
            if self._cached_disk_tuple is None or (now - self._last_disk_check_time) > 30.0:
                try:
                    disk = psutil.disk_usage("C:\\")
                    self._cached_disk_tuple = (
                        round(disk.free / (1024**3), 1),
                        round(disk.total / (1024**3), 1),
                        disk.percent
                    )
                    self._last_disk_check_time = now
                except Exception:
                    if not self._cached_disk_tuple:
                        self._cached_disk_tuple = (100.0, 500.0, 50.0)

            disk_free_gb, disk_total_gb, disk_percent = self._cached_disk_tuple
            mc_ram_pct = min(100.0, round((mc_ram_gb / max_mc_ram) * 100, 1)) if (max_mc_ram and max_mc_ram > 0) else 0.0

            snap = {
                "timestamp": now,
                "time_str": datetime.now().strftime("%H:%M:%S"),
                "host_cpu": host_cpu,
                "host_ram_percent": host_ram_percent,
                "host_ram_used_gb": host_ram_used_gb,
                "host_ram_total_gb": host_ram_total_gb,
                "mc_ram_mb": mc_ram_mb,
                "mc_ram_gb": mc_ram_gb,
                "mc_rss_gb": mc_rss_gb,
                "mc_ram_max_gb": max_mc_ram,
                "mc_ram_pct": mc_ram_pct,
                "mc_ram_percent": mc_ram_pct,
                "mc_cpu": mc_cpu,
                "disk_free_gb": disk_free_gb,
                "disk_total_gb": disk_total_gb,
                "disk_percent": disk_percent
            }
            self.metrics_history.append(snap)

            # Record 24h metrics snapshot every 3 minutes (or if first snapshot)
            if self._last_24h_snapshot_time == 0.0 or (now - self._last_24h_snapshot_time) >= 180.0:
                self._last_24h_snapshot_time = now
                self.metrics_history_24h.append({
                    "timestamp": now,
                    "time_str": datetime.now().strftime("%H:%M"),
                    "host_cpu": host_cpu,
                    "mc_ram_gb": mc_ram_gb,
                    "players": len(self.players),
                    "tps": self.get_current_tps()
                })

            # Check Resource Overload Alerts (with cooldowns to prevent spam)
            if self.state == "ONLINE" and max_mc_ram > 0 and (mc_ram_gb / max_mc_ram) >= 0.90:
                if (now - self._last_ram_alert_time) > 600: # 10 min cooldown
                    self._last_ram_alert_time = now
                    pct_val = int((mc_ram_gb / max_mc_ram) * 100)
                    self.send_push_notification(
                        title="Allarme Memoria Server",
                        message=f"Il server Minecraft sta usando {mc_ram_gb} GB su {max_mc_ram} GB allocati ({pct_val}% RAM)! Rischio OutOfMemory.",
                        priority="high",
                        tags="warning,fire"
                    )

            if disk_free_gb < 10.0:
                if (now - self._last_disk_alert_time) > 3600: # 1 hour cooldown
                    self._last_disk_alert_time = now
                    self.send_push_notification(
                        title="Spazio Disco Critico",
                        message=f"Spazio libero su disco C: inferiore a 10 GB ({disk_free_gb} GB rimanenti)! I backup potrebbero non andare a buon fine.",
                        priority="high",
                        tags="floppy_disk,warning"
                    )
        except Exception:
            pass

    def get_full_status(self) -> Dict[str, Any]:
        with self.lock:
            uptime_seconds = 0
            if self.state in ("STARTING", "ONLINE"):
                if not self.start_time:
                    if self.psutil_proc and self.psutil_proc.is_running():
                        try:
                            self.start_time = self.psutil_proc.create_time()
                        except Exception:
                            self.start_time = time.time()
                    else:
                        self.start_time = time.time()
                uptime_seconds = max(0, int(time.time() - self.start_time))

            latest_metrics = self.metrics_history[-1] if self.metrics_history else {}

            slp_count = self.server_info.get("players_online", 0)
            if slp_count == 0 and len(self.players) > 0:
                self.players.clear()

            player_list = []
            for name, p_data in self.players.items():
                if not name or name.lower() in ("anonymous", "anonymous player", "anonimo", "null", "unknown"):
                    continue
                online_duration_s = int(time.time() - p_data.get("joined_at", time.time()))
                player_list.append({
                    "name": name,
                    "joined_str": p_data.get("joined_str", ""),
                    "online_seconds": online_duration_s,
                    "online_formatted": self.format_seconds(online_duration_s),
                    "avatar_url": f"https://mc-heads.net/avatar/{name}/64"
                })

            total_players_online = slp_count if slp_count > 0 else len(player_list)
            if total_players_online == 0:
                player_list = []

            server_dir = self.config.get("server_dir", "")
            server_dir_exists = bool(server_dir and os.path.isdir(server_dir))

            return {
                "state": self.state,
                "online": self.state == "ONLINE",
                "server_dir_exists": server_dir_exists,
                "server_dir": server_dir,
                # Porta vera da server.properties: senza, l'app mostrava sempre la sua predefinita (32769)
                "server_port": self.get_server_port(),
                "uptime_seconds": uptime_seconds,
                "uptime_formatted": self.format_seconds(uptime_seconds),
                "start_time": self.start_time,
                "server_ram_max_gb": self.get_configured_max_ram_gb(),
                "start_time_str": datetime.fromtimestamp(self.start_time).strftime("%d/%m/%Y %H:%M:%S") if self.start_time else None,
                "players": {
                    "count": total_players_online,
                    # A server acceso il valore vero arriva dal ping; spento, da server.properties (prima: sempre 20)
                    "max": self.server_info.get("players_max", 20) if self.state == "ONLINE" else self.get_configured_max_players(),
                    "list": player_list
                },
                "slp": {
                    "latency_ms": self.server_info.get("latency_ms"),
                    "motd": self.server_info.get("motd", ""),
                    "version": self.server_info.get("version", "")
                },
                "scheduled_restart": {
                    "enabled": self.scheduled_enabled,
                    "interval_minutes": self.config.get("scheduled_restart_interval_minutes", 240),
                    "remaining_seconds": max(0, self.scheduled_remaining_seconds),
                    "remaining_formatted": self.format_seconds(max(0, self.scheduled_remaining_seconds)),
                    "progress_percent": round(max(0, min(100, (1 - (self.scheduled_remaining_seconds / max(1, self.scheduled_interval_seconds))) * 100)), 1)
                },
                "crash_protection": {
                    "auto_restart_enabled": self.auto_restart_on_crash,
                    "paused": self.auto_restart_paused,
                    "countdown": self.crash_countdown,
                    "total_crashes": len(self.crash_history),
                    "latest_crash": self.crash_history[-1] if self.crash_history else None
                },
                "errors": {
                    "total": len(self.errors_list),
                    "recent": list(self.errors_list)[-10:]
                },
                "chat": {
                    "total": len(self.chat_messages),
                    "recent": list(self.chat_messages)[-15:]
                },
                "metrics": latest_metrics,
                "metrics_history": list(self.metrics_history),
                "metrics_history_24h": list(self.metrics_history_24h),
                "eco_mode": {
                    "enabled": self.eco_mode_enabled,
                    "minutes": self.eco_mode_minutes,
                    "empty_seconds": int(time.time() - self.server_empty_since) if (self.server_empty_since and self.state == "ONLINE") else 0
                },
                "anti_lag": {
                    "enabled": self.anti_lag_enabled,
                    "auto_clean": self.anti_lag_auto_clean
                },
                "tps": self.get_current_tps(),
                "tps_label": f"{self.get_current_tps()} TPS" if self.state == "ONLINE" else "0.0 TPS"
            }

    @staticmethod
    def format_seconds(seconds: int) -> str:
        if seconds < 0:
            seconds = 0
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        if h > 0:
            return f"{h:02d}h {m:02d}m {s:02d}s"
        return f"{m:02d}m {s:02d}s"

    def get_storage_breakdown(self) -> Dict[str, Any]:
        s_dir = self.config.get("server_dir", "")
        if not os.path.exists(s_dir):
            return {"status": "error", "message": "Cartella server non trovata"}

        categories = {
            "world": 0,
            "mods": 0,
            "logs": 0,
            "logs_cleanable": 0,
            "crash_reports": 0,
            "other": 0,
            "total": 0
        }
        total_files = 0
        cleanable_count = 0

        for root, dirs, files in os.walk(s_dir):
            total_files += len(files)
            for f in files:
                fp = os.path.join(root, f)
                try:
                    sz = os.path.getsize(fp)
                except Exception:
                    sz = 0
                categories["total"] += sz
                rel = os.path.relpath(fp, s_dir).lower().replace('\\', '/')
                if rel.startswith("world/") or rel.startswith("world_"):
                    categories["world"] += sz
                elif rel.startswith("mods/"):
                    categories["mods"] += sz
                elif rel.startswith("logs/"):
                    categories["logs"] += sz
                    if f.endswith(".log.gz") or f.endswith(".gz") or (f.endswith(".log") and f != "latest.log"):
                        categories["logs_cleanable"] += sz
                        cleanable_count += 1
                elif rel.startswith("crash-reports/"):
                    categories["crash_reports"] += sz
                    if f.endswith(".txt"):
                        categories["logs_cleanable"] += sz
                        cleanable_count += 1
                elif f.endswith(".hprof") or f.startswith("hs_err_pid"):
                    categories["logs_cleanable"] += sz
                    categories["other"] += sz
                    cleanable_count += 1
                else:
                    categories["other"] += sz

        def to_mb(b):
            return round(b / (1024 * 1024), 1)

        def to_gb(b):
            return round(b / (1024 * 1024 * 1024), 2)

        # Host disk space
        try:
            import shutil
            d_total, d_used, d_free = shutil.disk_usage(s_dir)
            disk_total_gb = round(d_total / (1024 ** 3), 1)
            disk_free_gb = round(d_free / (1024 ** 3), 1)
            disk_used_percent = round((d_used / d_total) * 100, 1)
        except Exception:
            disk_total_gb = 0
            disk_free_gb = 0
            disk_used_percent = 0

        return {
            "status": "success",
            "server_dir": s_dir,
            "total_bytes": categories["total"],
            "total_gb": to_gb(categories["total"]),
            "total_mb": to_mb(categories["total"]),
            "total_files": total_files,
            "files_count": total_files,
            "cleanable_mb": to_mb(categories["logs_cleanable"]),
            "cleanable_count": cleanable_count,
            "cleanable_files_count": cleanable_count,
            "world_gb": to_gb(categories["world"]),
            "world_mb": to_mb(categories["world"]),
            "mods_mb": to_mb(categories["mods"]),
            "logs_mb": to_mb(categories["logs"]),
            "logs_cleanable_mb": to_mb(categories["logs_cleanable"]),
            "crash_reports_mb": to_mb(categories["crash_reports"]),
            "other_mb": to_mb(categories["other"]),
            "disk_total_gb": disk_total_gb,
            "disk_free_gb": disk_free_gb,
            "disk_used_percent": disk_used_percent
        }

    def clean_old_logs(self) -> Dict[str, Any]:
        s_dir = self.config.get("server_dir", "")
        logs_dir = os.path.join(s_dir, "logs")
        deleted_count = 0
        freed_bytes = 0

        if os.path.exists(logs_dir):
            for f in os.listdir(logs_dir):
                if f.endswith(".log.gz") or f.endswith(".gz") or (f.endswith(".log") and f != "latest.log"):
                    fp = os.path.join(logs_dir, f)
                    try:
                        sz = os.path.getsize(fp)
                        os.remove(fp)
                        deleted_count += 1
                        freed_bytes += sz
                    except Exception:
                        pass

        crash_dir = os.path.join(s_dir, "crash-reports")
        if os.path.exists(crash_dir):
            for f in os.listdir(crash_dir):
                if f.endswith(".txt"):
                    fp = os.path.join(crash_dir, f)
                    try:
                        sz = os.path.getsize(fp)
                        os.remove(fp)
                        deleted_count += 1
                        freed_bytes += sz
                    except Exception:
                        pass

        for f in os.listdir(s_dir):
            if f.endswith(".hprof") or f.startswith("hs_err_pid"):
                fp = os.path.join(s_dir, f)
                try:
                    sz = os.path.getsize(fp)
                    os.remove(fp)
                    deleted_count += 1
                    freed_bytes += sz
                except Exception:
                    pass

        freed_mb = round(freed_bytes / (1024 * 1024), 1)
        self.add_log(f"[Pannello] Pulizia completata: rimossi {deleted_count} file inutili, liberati {freed_mb} MB.")
        return {
            "status": "success",
            "deleted_count": deleted_count,
            "freed_mb": freed_mb
        }

    def player_quick_action(self, player_name: str, action: str, value: Optional[str] = None) -> Dict[str, Any]:
        if not re.match(r"^[\.\*]?[a-zA-Z0-9_]{2,16}$", player_name):
            return {"status": "error", "message": "Nome giocatore non valido"}

        cmd = ""
        action_desc = ""
        # Il pannello manda "tp" con destinazione "spawn": in Minecraft vorrebbe dire "dal giocatore chiamato spawn"
        if action == "tp" and (value or "").strip().lower() == "spawn":
            action = "spawn"
        if action == "heal":
            cmd = f"effect give {player_name} minecraft:instant_health 1 255"
            cmd_feed = f"effect give {player_name} minecraft:saturation 1 255"
            res = self.send_command(cmd)
            if res.get("status") == "error":
                return res
            self.send_command(cmd_feed)
            action_desc = f"Salute e nutrizione di {player_name} ripristinate al massimo."
        elif action == "feed":
            cmd = f"effect give {player_name} minecraft:saturation 1 255"
            res = self.send_command(cmd)
            if res.get("status") == "error":
                return res
            action_desc = f"Fame di {player_name} saziata."
        elif action == "gamemode":
            mode = (value or "survival").lower()
            if mode not in ["survival", "creative", "adventure", "spectator"]:
                mode = "survival"
            cmd = f"gamemode {mode} {player_name}"
            res = self.send_command(cmd)
            if res.get("status") == "error":
                return res
            action_desc = f"Modalità di {player_name} impostata a {mode}."
        elif action == "tp":
            dest = value or "@p"
            cmd = f"tp {player_name} {dest}"
            res = self.send_command(cmd)
            if res.get("status") == "error":
                return res
            action_desc = f"Teletrasporto di {player_name} eseguito."
        elif action == "spawn":
            # Punto di spawn del mondo, letto da level.dat (in Minecraft non c'è un comando "vai allo spawn")
            spawn = read_world_spawn(self.config.get("server_dir", ""))
            if not spawn:
                return {"status": "error", "message": "Non riesco a leggere il punto di spawn del mondo (level.dat)."}
            x, y, z = spawn
            cmd = f"execute in minecraft:overworld run tp {player_name} {x + 0.5} {y} {z + 0.5}"
            res = self.send_command(cmd)
            if res.get("status") == "error":
                return res
            action_desc = f"{player_name} teletrasportato allo spawn del mondo ({x} {y} {z})."
        elif action == "give":
            item = value or "minecraft:cooked_beef 16"
            cmd = f"give {player_name} {item}"
            res = self.send_command(cmd)
            if res.get("status") == "error":
                return res
            action_desc = f"Oggetto {item} donato a {player_name}."
        elif action == "clear_inventory":
            cmd = f"clear {player_name}"
            res = self.send_command(cmd)
            if res.get("status") == "error":
                return res
            action_desc = f"Inventario di {player_name} svuotato."
        elif action == "op":
            cmd = f"op {player_name}"
            res = self.send_command(cmd)
            if res.get("status") == "error":
                return res
            action_desc = f"{player_name} promosso ad operatore (OP)."
        elif action == "deop":
            cmd = f"deop {player_name}"
            res = self.send_command(cmd)
            if res.get("status") == "error":
                return res
            action_desc = f"Privilegi operatore rimossi da {player_name}."
        elif action == "kick":
            reason = value or "Espulso dall'amministratore"
            cmd = f"kick {player_name} {reason}"
            res = self.send_command(cmd)
            if res.get("status") == "error":
                return res
            action_desc = f"Giocatore {player_name} espulso."
        elif action == "ban":
            reason = value or "Bannato dall'amministratore"
            cmd = f"ban {player_name} {reason}"
            res = self.send_command(cmd)
            if res.get("status") == "error":
                return res
            action_desc = f"Giocatore {player_name} bannato dal server."
        else:
            return {"status": "error", "message": f"Azione {action} non riconosciuta"}

        return {"status": "success", "message": action_desc, "command": cmd}

    AIKAR_FLAGS = [
        "-XX:+UseG1GC",
        "-XX:+ParallelRefProcEnabled",
        "-XX:MaxGCPauseMillis=200",
        "-XX:+UnlockExperimentalVMOptions",
        "-XX:+DisableExplicitGC",
        "-XX:+AlwaysPreTouch",
        "-XX:G1NewSizePercent=30",
        "-XX:G1MaxNewSizePercent=40",
        "-XX:G1ReservePercent=20",
        "-XX:G1HeapWastePercent=5",
        "-XX:G1MixedGCCountTarget=4",
        "-XX:InitiatingHeapOccupancyPercent=15",
        "-XX:G1MixedGCLiveThresholdPercent=90",
        "-XX:G1RSetUpdatingPauseTimePercent=5",
        "-XX:SurvivorRatio=32",
        "-XX:+PerfDisableSharedMem",
        "-XX:MaxTenuringThreshold=1"
    ]

    @staticmethod
    def _host_ram_gb() -> int:
        try:
            return max(1, int(round(psutil.virtual_memory().total / (1024 ** 3))))
        except Exception:
            return 16

    def get_jvm_tuning(self) -> Dict[str, Any]:
        s_dir = self.config.get("server_dir", "")
        jvm_file = os.path.join(s_dir, "user_jvm_args.txt") if s_dir else ""
        uses_file = self._uses_jvm_args_file()
        raw_text = ""
        if uses_file and os.path.isfile(jvm_file):
            try:
                with open(jvm_file, "r", encoding="utf-8", errors="ignore") as f:
                    raw_text = f.read()
            except Exception:
                pass
        elif not uses_file:
            try:
                raw_text = " ".join(self.get_launch_command())
            except Exception:
                pass
        xmx = self._read_xmx_gb()
        return {
            "status": "success",
            # None = nel file o nel comando non c'è -Xmx (Java usa un quarto della RAM del PC)
            "ram_gb": int(round(xmx)) if xmx else None,
            "use_aikar_flags": "G1NewSizePercent" in raw_text or "UseG1GC" in raw_text,
            "host_ram_gb": self._host_ram_gb(),
            "source": "user_jvm_args.txt" if uses_file else (os.path.basename(self._find_run_bat()) or "comando di avvio"),
            "file_exists": bool(jvm_file) and os.path.isfile(jvm_file),
            "raw_text": raw_text
        }

    def _rewrite_java_line(self, line: str, ram_gb: int, use_aikar: bool) -> str:
        """Stessa riga java con -Xms/-Xmx nuovi e le flag di Aikar aggiunte o tolte; il resto resta com'è."""
        indent = line[:len(line) - len(line.lstrip())]
        parts = line.strip().split(" ")
        rest = [p for p in parts[1:] if p and not p.startswith(("-Xmx", "-Xms")) and p not in self.AIKAR_FLAGS]
        mem = [f"-Xms{ram_gb}G", f"-Xmx{ram_gb}G"]
        return indent + " ".join([parts[0]] + mem + (self.AIKAR_FLAGS if use_aikar else []) + rest)

    def save_jvm_tuning(self, ram_gb: int, use_aikar: bool) -> Dict[str, Any]:
        s_dir = self.config.get("server_dir", "")
        if not s_dir or not os.path.isdir(s_dir):
            return {"status": "error", "message": "Nessun server collegato: scegli prima la cartella del server."}
        host_gb = self._host_ram_gb()
        if ram_gb < 1 or ram_gb > max(1, host_gb - 1):
            # Prima un valore fuori dai limiti diventava 16 GB senza dirlo
            return {"status": "error", "message": f"RAM non valida: scegli tra 1 e {max(1, host_gb - 1)} GB (il PC ne ha {host_gb})."}

        written = ""
        try:
            if self._uses_jvm_args_file():
                lines = ["# Configurazione JVM & RAM generata da Voxarium", f"-Xms{ram_gb}G", f"-Xmx{ram_gb}G"]
                if use_aikar:
                    lines.extend(self.AIKAR_FLAGS)
                with open(os.path.join(s_dir, "user_jvm_args.txt"), "w", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
                written = "user_jvm_args.txt"
            else:
                # Vanilla, Paper, Fabric...: la RAM è nella riga java di run.bat (o nel comando salvato)
                bat = self._find_run_bat()
                if bat:
                    with open(bat, "r", encoding="utf-8", errors="ignore") as f:
                        bat_lines = f.read().splitlines()
                    changed = False
                    for i, l in enumerate(bat_lines):
                        if l.strip().startswith("java "):
                            bat_lines[i] = self._rewrite_java_line(l, ram_gb, use_aikar)
                            changed = True
                            break
                    if changed:
                        with open(bat, "w", encoding="utf-8") as f:
                            f.write("\n".join(bat_lines) + "\n")
                        written = os.path.basename(bat)
                cmd = (self.config.get("server_command") or "").strip()
                if cmd.startswith("java "):
                    self.config["server_command"] = self._rewrite_java_line(cmd, ram_gb, use_aikar)
                    self.save_config()
                    written = written or "comando di avvio"
                if not written:
                    return {"status": "error", "message": "Non ho trovato dove il server imposta la RAM (run.bat o comando java)."}
        except Exception as e:
            return {"status": "error", "message": f"Errore nel salvataggio della RAM: {e}"}

        self._cached_max_ram_val = None
        self.add_log(f"[Pannello] RAM del server impostata a {ram_gb} GB in {written} "
                     f"(Aikar's Flags: {'Attive' if use_aikar else 'Disattive'}). Vale dal prossimo avvio del server.")
        return {"status": "success", "ram_gb": ram_gb, "use_aikar": use_aikar, "file": written,
                "restart_needed": self.state in ("ONLINE", "STARTING")}

    def set_eco_mode(self, enabled: bool, minutes: int = 45) -> Dict[str, Any]:
        with self.lock:
            self.eco_mode_enabled = enabled
            self.eco_mode_minutes = max(5, minutes)
            self.config["eco_mode_enabled"] = enabled
            self.config["eco_mode_minutes"] = self.eco_mode_minutes
            self.save_config()
            self.add_log(f"[Pannello] Modalità Eco impostata: {'Attiva' if enabled else 'Disattivata'} ({self.eco_mode_minutes} min vuoto).")
            return {"status": "success", "enabled": self.eco_mode_enabled, "minutes": self.eco_mode_minutes}

    def set_anti_lag(self, enabled: bool, auto_clean: bool = True) -> Dict[str, Any]:
        with self.lock:
            self.anti_lag_enabled = enabled
            self.anti_lag_auto_clean = auto_clean
            self.config["anti_lag_enabled"] = enabled
            self.config["anti_lag_auto_clean"] = auto_clean
            self.save_config()
            self.add_log(f"[Pannello] Anti-Lag Watchdog impostato: {'Attivo' if enabled else 'Disattivo'} (Auto-Clean: {'Attivo' if auto_clean else 'Disattivo'}).")
            return {"status": "success", "enabled": self.anti_lag_enabled, "auto_clean": self.anti_lag_auto_clean}

    def list_config_files(self) -> Dict[str, Any]:
        s_dir = self.config.get("server_dir", "")
        if not os.path.exists(s_dir):
            return {"status": "error", "message": "Cartella server non trovata", "files": []}

        result = []
        # Key root config files
        root_configs = ["server.properties", "user_jvm_args.txt", "whitelist.json", "ops.json"]
        for rf in root_configs:
            fpath = os.path.join(s_dir, rf)
            if os.path.isfile(fpath):
                bak_exists = os.path.isfile(fpath + ".bak")
                result.append({
                    "name": rf,
                    "rel_path": rf,
                    "category": "Server Core",
                    "size_bytes": os.path.getsize(fpath),
                    "size_str": f"{round(os.path.getsize(fpath) / 1024, 1)} KB",
                    "backup_exists": bak_exists
                })

        # Default configs in defaultconfigs/ directory (modern Forge / NeoForge server-side mod configs)
        def_dir = os.path.join(s_dir, "defaultconfigs")
        if os.path.isdir(def_dir):
            for root, dirs, files in os.walk(def_dir):
                for file in files:
                    ext = os.path.splitext(file)[1].lower()
                    if ext in (".toml", ".json", ".jsonc", ".cfg", ".txt", ".properties", ".yaml", ".yml"):
                        full_p = os.path.join(root, file)
                        rel_p = os.path.relpath(full_p, s_dir)
                        bak_exists = os.path.isfile(full_p + ".bak")
                        result.append({
                            "name": file,
                            "rel_path": rel_p.replace("\\", "/"),
                            "category": "Default Configs (Server)",
                            "size_bytes": os.path.getsize(full_p),
                            "size_str": f"{round(os.path.getsize(full_p) / 1024, 1)} KB",
                            "backup_exists": bak_exists
                        })

        # Mods config files in config/ directory
        cfg_dir = os.path.join(s_dir, "config")
        if os.path.isdir(cfg_dir):
            for root, dirs, files in os.walk(cfg_dir):
                for file in files:
                    ext = os.path.splitext(file)[1].lower()
                    if ext in (".toml", ".json", ".jsonc", ".cfg", ".txt", ".properties", ".yaml", ".yml"):
                        full_p = os.path.join(root, file)
                        rel_p = os.path.relpath(full_p, s_dir)
                        bak_exists = os.path.isfile(full_p + ".bak")
                        result.append({
                            "name": file,
                            "rel_path": rel_p.replace("\\", "/"),
                            "category": "Mod Config",
                            "size_bytes": os.path.getsize(full_p),
                            "size_str": f"{round(os.path.getsize(full_p) / 1024, 1)} KB",
                            "backup_exists": bak_exists
                        })

        cat_order = {"Server Core": 0, "Default Configs (Server)": 1, "Mod Config": 2}
        result.sort(key=lambda x: (cat_order.get(x["category"], 9), x["name"].lower()))
        return {"status": "success", "files": result}

    def read_config_file(self, rel_path: str) -> Dict[str, Any]:
        s_dir = self.config.get("server_dir", "")
        safe_path = os.path.normpath(os.path.join(s_dir, rel_path))
        if not safe_path.startswith(os.path.normpath(s_dir)):
            return {"status": "error", "message": "Accesso non autorizzato fuori dalla cartella server"}
        if not os.path.isfile(safe_path):
            return {"status": "error", "message": f"File non trovato: {rel_path}"}
        try:
            with open(safe_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            bak_path = safe_path + ".bak"
            bak_exists = os.path.isfile(bak_path)
            bak_size_str = f"{round(os.path.getsize(bak_path) / 1024, 1)} KB" if bak_exists else None
            return {
                "status": "success",
                "rel_path": rel_path,
                "content": content,
                "backup_exists": bak_exists,
                "backup_size_str": bak_size_str
            }
        except Exception as e:
            return {"status": "error", "message": f"Errore lettura file: {e}"}

    def save_config_file(self, rel_path: str, content: str) -> Dict[str, Any]:
        s_dir = self.config.get("server_dir", "")
        safe_path = os.path.normpath(os.path.join(s_dir, rel_path))
        if not safe_path.startswith(os.path.normpath(s_dir)):
            return {"status": "error", "message": "Accesso non autorizzato fuori dalla cartella server"}
        try:
            # Create a safety backup (.bak) if the file already exists
            if os.path.isfile(safe_path):
                bak_path = safe_path + ".bak"
                shutil.copy2(safe_path, bak_path)
            with open(safe_path, "w", encoding="utf-8") as f:
                f.write(content)
            self.add_log(f"[Pannello] File salvato: {rel_path} (backup di sicurezza .bak creato)")
            return {"status": "success", "rel_path": rel_path, "backup_created": True}
        except Exception as e:
            return {"status": "error", "message": f"Errore scrittura file: {e}"}

    def restore_config_file(self, rel_path: str) -> Dict[str, Any]:
        s_dir = self.config.get("server_dir", "")
        safe_path = os.path.normpath(os.path.join(s_dir, rel_path))
        if not safe_path.startswith(os.path.normpath(s_dir)):
            return {"status": "error", "message": "Accesso non autorizzato fuori dalla cartella server"}
        bak_path = safe_path + ".bak"
        if not os.path.isfile(bak_path):
            return {"status": "error", "message": f"Nessun file di backup (.bak) trovato per: {rel_path}"}
        try:
            shutil.copy2(bak_path, safe_path)
            with open(safe_path, "r", encoding="utf-8", errors="replace") as f:
                restored_content = f.read()
            self.add_log(f"[Pannello] Backup ripristinato per il file: {rel_path}")
            return {
                "status": "success",
                "rel_path": rel_path,
                "content": restored_content,
                "message": f"Backup di '{rel_path}' ripristinato con successo!"
            }
        except Exception as e:
            return {"status": "error", "message": f"Errore durante il ripristino del backup: {e}"}
