"""
Minecraft Server Backup Manager
Performs safe, atomic, verified .zip backups of the Minecraft server to the Desktop.
Ensures zero data corruption by enforcing server STOPPED status before archiving.
Automatically detects and removes previous backups as requested by the user,
and calculates real-time percentage progress (0% - 100%) streamed via WebSockets.
"""

import os
import shutil
import time
import zipfile
import threading
import ctypes
import stat
import json
from datetime import datetime
from typing import Optional, Dict, Any, Callable, List

FILE_ATTRIBUTE_NORMAL = 0x80

def _make_writable(path: str):
    """Ensures destination file is writable and not blocked by Hidden/System/ReadOnly attributes."""
    if os.path.exists(path):
        try:
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            if os.name == "nt":
                attrs = ctypes.windll.kernel32.GetFileAttributesW(path)
                ctypes.windll.kernel32.SetFileAttributesW(path, FILE_ATTRIBUTE_NORMAL)
                return attrs
        except Exception:
            pass
    return None

BACKUP_INFO_NAME = "__backup_info.json"
BACKUP_IGNORED_DIRS = {"backups", ".git", "crash-reports", ".cache"}
BACKUP_IGNORED_FILES = {"desktop.ini", "thumbs.db", ".ds_store", "session.lock", "backup_server_mc.zip"}
# Nome dell'unico backup delle versioni fino alla 4.20.2, uguale per tutti i server
LEGACY_BACKUP_FILENAME = "Backup_Server_Mc.zip"


def _norm_dir(path: Optional[str]) -> str:
    return os.path.normcase(os.path.abspath(path)) if path else ""


def _slug(name: str) -> str:
    """Nome del server utilizzabile in un nome di file (lettere, numeri, - e _)."""
    out = "".join(c if (c.isalnum() or c in "-_") else "_" for c in (name or "").strip())
    out = "_".join(p for p in out.split("_") if p)[:40].strip("_-")
    return out or "Server"


def read_backup_info(path: str) -> Dict[str, Any]:
    """Metadati scritti dentro lo ZIP dal backup ({} se mancano: archivi vecchi o non creati da Voxarium)."""
    try:
        with zipfile.ZipFile(path, "r") as zf:
            if BACKUP_INFO_NAME in zf.namelist():
                meta = json.loads(zf.read(BACKUP_INFO_NAME).decode("utf-8"))
                return meta if isinstance(meta, dict) else {}
    except Exception:
        pass
    return {}


def _is_excluded_from_backup(rel_path: str, exclude_lod_cache: bool) -> bool:
    """True se il file (percorso relativo alla cartella del server) non viene mai messo nel backup.
    Deve rispecchiare esattamente le regole di _run_backup_worker."""
    parts = [p.lower() for p in rel_path.replace("\\", "/").split("/") if p and p != "."]
    if not parts:
        return True
    dirs, name = parts[:-1], parts[-1]
    if any(d in BACKUP_IGNORED_DIRS for d in dirs):
        return True
    if name in BACKUP_IGNORED_FILES or name.endswith(".lck") or name.endswith(".tmp"):
        return True
    if exclude_lod_cache and (any("distanthorizons" in d for d in dirs) or "distanthorizons" in name):
        return True
    return False

class BackupManager:
    def __init__(self, server_dir: str, desktop_dir: Optional[str] = None):
        self.server_dir = os.path.abspath(server_dir) if server_dir else ""
        if not desktop_dir:
            # Fallback to current user's Desktop
            user_profile = os.environ.get("USERPROFILE", os.path.expanduser("~"))
            desktop_dir = os.path.join(user_profile, "Desktop")
            if not os.path.exists(desktop_dir):
                desktop_dir = user_profile
        self.desktop_dir = os.path.abspath(desktop_dir)
        # Chi usa il backup manager può dire come si chiama il server di una cartella e quanti server ci sono:
        # server_info_cb(server_dir) -> {"id": ..., "name": ..., "servers": n}
        self.server_info_cb: Optional[Callable[[str], Dict[str, Any]]] = None
        self._info_cache: Dict[str, Any] = {}
        # Destinazione del backup in corso: resta quella anche se nel frattempo si cambia server attivo
        self._job_target: Optional[str] = None

        self.is_running: bool = False
        self.cancel_requested: bool = False
        self.lock = threading.RLock()
        self._active_zf: Optional[zipfile.ZipFile] = None
        self._worker_thread: Optional[threading.Thread] = None
        self.exclude_lod_cache: bool = False  # Default: False -> Backup 100% of world and Distant Horizons data
        
        self.cloud_sync_dir: Optional[str] = None
        
        self.progress: Dict[str, Any] = {
            "active": False,
            "percent": 0,
            "processed_mb": 0.0,
            "total_mb": 0.0,
            "current_file": "",
            "files_processed": 0,
            "files_total": 0,
            "status": "In attesa",
            "error": None,
            "target_path": self.target_path,
            "target_filename": self.target_filename,
            "completed_at": None,
            "elapsed_seconds": 0,
            "cloud_synced": False,
            "cloud_error": None
        }
        
        self.progress_subscribers: List[Callable[[Dict[str, Any]], None]] = []

    # ---- Un backup per ogni server ----
    def server_identity(self) -> Dict[str, Any]:
        """Il server di cui si fa il backup ora: id dell'istanza, nome, cartella e quanti server ci sono."""
        info: Dict[str, Any] = {}
        if self.server_info_cb and self.server_dir:
            try:
                info = self.server_info_cb(self.server_dir) or {}
            except Exception:
                info = {}
        name = info.get("name") or (os.path.basename(os.path.normpath(self.server_dir)) if self.server_dir else "")
        return {"server_id": info.get("id") or "", "server_name": name or "Server",
                "server_dir": self.server_dir, "servers": info.get("servers")}

    def _cached_info(self, path: str) -> Dict[str, Any]:
        """read_backup_info con una cache (gli ZIP grandi hanno migliaia di file da elencare)."""
        try:
            st = os.stat(path)
        except OSError:
            return {}
        key = (os.path.normcase(path), st.st_mtime, st.st_size)
        hit = self._info_cache.get(key[0])
        if hit and hit[0] == key:
            return hit[1]
        meta = read_backup_info(path)
        self._info_cache[key[0]] = (key, meta)
        return meta

    def belongs_to_current(self, meta: Dict[str, Any]) -> Optional[bool]:
        """True se il backup è del server attivo, False se è di un altro, None se non si sa (backup vecchio)."""
        ident = self.server_identity()
        known = False
        if meta.get("server_id") and ident["server_id"]:
            known = True
            if meta["server_id"] == ident["server_id"]:
                return True
        if meta.get("server_dir") and self.server_dir:
            # Stessa cartella = stessi file, anche se il server è stato tolto e aggiunto di nuovo (id nuovo)
            known = True
            if _norm_dir(meta["server_dir"]) == _norm_dir(self.server_dir):
                return True
        return False if known else None

    @property
    def target_filename(self) -> str:
        """Backup_<NomeServer>.zip; se esiste già un file con quel nome di un altro server (due server con lo
        stesso nome) si aggiunge un codice ricavato dal server, così non si sovrascrivono mai a vicenda."""
        if self._job_target:
            return os.path.basename(self._job_target)
        ident = self.server_identity()
        name = f"Backup_{_slug(ident['server_name'])}.zip"
        path = os.path.join(self.desktop_dir, name)
        if name.lower() == LEGACY_BACKUP_FILENAME.lower() or (
                os.path.isfile(path) and self.belongs_to_current(self._cached_info(path)) is not True):
            import hashlib
            code = hashlib.sha1((ident["server_id"] or _norm_dir(self.server_dir)).encode("utf-8")).hexdigest()[:6]
            name = f"Backup_{_slug(ident['server_name'])}_{code}.zip"
        return name

    @property
    def target_path(self) -> str:
        return self._job_target or os.path.join(self.desktop_dir, self.target_filename)

    def subscribe_progress(self, callback: Callable[[Dict[str, Any]], None]):
        with self.lock:
            if callback not in self.progress_subscribers:
                self.progress_subscribers.append(callback)

    def _notify_subscribers(self):
        snapshot = self.get_status()
        for cb in list(self.progress_subscribers):
            try:
                cb(snapshot)
            except Exception:
                pass

    def get_status(self) -> Dict[str, Any]:
        with self.lock:
            st = dict(self.progress)
            if not self.is_running:
                # Finita l'operazione, il file è quello del server attivo (dopo un ripristino o un cambio di server
                # restava il nome dell'archivio usato l'ultima volta, magari di un altro server)
                st["target_path"] = self.target_path
                st["target_filename"] = self.target_filename
            st["existing_backup_exists"] = os.path.exists(self.target_path)
            if st["existing_backup_exists"] and not self.is_running:
                try:
                    st["existing_backup_size_mb"] = round(os.path.getsize(self.target_path) / (1024 * 1024), 1)
                    st["existing_backup_date"] = datetime.fromtimestamp(os.path.getmtime(self.target_path)).strftime("%d/%m/%Y %H:%M:%S")
                except Exception:
                    st["existing_backup_size_mb"] = 0
            st["cloud_sync_dir"] = self.cloud_sync_dir
            return st

    def set_cloud_sync_dir(self, dir_path: Optional[str]):
        with self.lock:
            self.cloud_sync_dir = dir_path

    def start_backup(self, is_server_stopped: bool, exclude_lod_cache: bool = False, copy_to_cloud: bool = False) -> Dict[str, Any]:
        """
        Starts the backup process in a background thread if the server is stopped.
        exclude_lod_cache defaults to False to ensure ALL world data (including Distant Horizons) is backed up.
        copy_to_cloud specifies whether to also mirror the completed backup into the configured Google Drive cloud folder.
        """
        with self.lock:
            if self.is_running:
                return {
                    "status": "error",
                    "message": "Un processo di backup è già in corso. Attendi il completamento o clicca Annulla."
                }

            if not is_server_stopped:
                return {
                    "status": "error",
                    "message": "Il server Minecraft deve essere COMPLETAMENTE SPENTO per effettuare il backup, in modo tale da non rischiare di corrompere i file di gioco."
                }

            if not self.server_dir or not os.path.exists(self.server_dir):
                return {
                    "status": "error",
                    "message": f"Cartella del server Minecraft non configurata o non trovata: {self.server_dir}"
                }

            self._job_target = None
            self._job_identity = self.server_identity()
            self._job_target = os.path.join(self.desktop_dir, self.target_filename)
            self._job_server_dir = self.server_dir
            self.is_running = True
            self.cancel_requested = False
            self.exclude_lod_cache = bool(exclude_lod_cache)
            self.copy_to_cloud = bool(copy_to_cloud)
            self._active_zf = None
            self.progress = {
                "operation": "backup",
                "server_name": self._job_identity["server_name"],
                "active": True,
                "percent": 0,
                "processed_mb": 0.0,
                "total_mb": 0.0,
                "current_file": "Scansione rapida file del server...",
                "files_processed": 0,
                "files_total": 0,
                "speed_mb_s": 0.0,
                "eta_seconds": 0,
                "eta_formatted": "Calcolo in corso...",
                "status": "Inizializzazione scansione...",
                "error": None,
                "target_path": self.target_path,
                "target_filename": self.target_filename,
                "completed_at": None,
                "elapsed_seconds": 0,
                "exclude_lod_cache": self.exclude_lod_cache,
                "copy_to_cloud": self.copy_to_cloud,
                "cloud_synced": False,
                "cloud_error": None
            }
            self._notify_subscribers()

            self._worker_thread = threading.Thread(target=self._run_backup_worker, daemon=True)
            self._worker_thread.start()

            return {
                "status": "success",
                "message": "Backup avviato in background. Monitoraggio percentuale attivo.",
                "target": self.target_path
            }

    def cancel_backup(self, force: bool = False) -> Dict[str, Any]:
        with self.lock:
            if not self.is_running and not self.progress.get("active"):
                return {"status": "error", "message": "Nessun backup attualmente in corso."}
            self.cancel_requested = True
            self.progress["status"] = "Interruzione del backup richiesta dall'utente..."
            self._notify_subscribers()

        if force:
            try:
                if self._active_zf:
                    self._active_zf.close()
            except Exception:
                pass
            t = self._worker_thread
            if t and t.is_alive():
                t.join(timeout=15)
            with self.lock:
                if self._worker_thread is t and t is not None and t.is_alive():
                    # fallback nel caso in cui il thread non risponda
                    self.is_running = False
                    self.progress["active"] = False
                    self.progress["percent"] = 0
                    self.progress["status"] = "Creazione backup annullata e bloccata con successo."
                    self.progress["error"] = "Operazione annullata dall'utente."
                    self._notify_subscribers()

        return {"status": "success", "message": "Richiesta di blocco / annullamento inviata."}

    def _run_backup_worker(self):
        my_target = self._job_target
        try:
            self._run_backup_job(self._job_server_dir)
        finally:
            with self.lock:
                # Solo la propria destinazione: un nuovo backup può essere già partito (is_running torna False
                # prima di qui, e dopo un annullamento forzato anche con questo thread ancora vivo)
                if self._job_target == my_target:
                    self._job_target = None

    def _run_backup_job(self, server_dir: str):
        start_time = time.time()
        # Con la copia su Google Drive la barra è divisa: compressione 0-50%, copia 50-100%
        pct_scale = 0.5 if self._will_copy_to_cloud() else 1.0
        temp_zip_path = self.target_path + ".tmp"
        self._active_zf = None
        
        try:
            # 1. Step: Rimozione residui temporanei (.tmp) di sessioni precedenti
            if os.path.exists(temp_zip_path):
                try:
                    os.remove(temp_zip_path)
                except Exception:
                    pass

            # 2. Step: File collection and size calculation
            with self.lock:
                self.progress["status"] = "Scansione e calcolo dimensione dei file..."
            self._notify_subscribers()

            files_to_pack = []
            total_bytes = 0
            for root, dirs, files in os.walk(server_dir):
                if self.cancel_requested:
                    raise InterruptedError("Operazione annullata dall'utente.")

                # Prune ignored directories to prevent walking into huge backup or cache folders
                dirs[:] = [
                    d for d in dirs
                    if d.lower() not in BACKUP_IGNORED_DIRS
                    and not (self.exclude_lod_cache and "distanthorizons" in d.lower())
                ]

                for f in files:
                    if self.cancel_requested:
                        raise InterruptedError("Operazione annullata dall'utente.")
                    full_p = os.path.join(root, f)
                    rel_p = os.path.relpath(full_p, server_dir)
                    if f == self.target_filename or _is_excluded_from_backup(rel_p, self.exclude_lod_cache):
                        continue
                    try:
                        sz = os.path.getsize(full_p)
                        files_to_pack.append((full_p, rel_p, sz))
                        total_bytes += sz
                    except Exception:
                        continue

            total_mb = round(total_bytes / (1024 * 1024), 1)
            total_files = len(files_to_pack)

            with self.lock:
                self.progress["total_mb"] = total_mb
                self.progress["files_total"] = total_files
                self.progress["status"] = f"Creazione archivio ZIP ({total_mb} MB in {total_files} file)..."
            self._notify_subscribers()

            # Se lo spazio su disco non basta per conservare entrambi i backup, rimuovi preventivamente il vecchio
            if os.path.exists(self.target_path):
                try:
                    free_space = shutil.disk_usage(self.desktop_dir).free
                    if free_space < total_bytes * 1.05:
                        os.remove(self.target_path)
                except Exception:
                    pass

            # 3. Step: Stream archive into ZIP using chunked streaming for ultra-responsiveness
            processed_bytes = 0
            processed_files = 0
            last_notify_time = time.time()
            CHUNK_SIZE = 2 * 1024 * 1024  # 2 MB chunk for sub-second cancel responsiveness

            with zipfile.ZipFile(temp_zip_path, 'w', allowZip64=True) as zf:
                self._active_zf = zf
                skipped_files = []

                for full_p, rel_p, sz in files_to_pack:
                    if self.cancel_requested:
                        raise InterruptedError("Operazione annullata dall'utente.")

                    # Determine optimal compression method per file
                    ext = os.path.splitext(rel_p)[1].lower()
                    if ext in ('.mca', '.sqlite', '.sqlite-shm', '.sqlite-wal', '.jar', '.png', '.jpg', '.ogg', '.gz', '.nbt', '.dat', '.zip'):
                        compress_type = zipfile.ZIP_STORED
                    else:
                        compress_type = zipfile.ZIP_DEFLATED

                    try:
                        mtime = time.localtime(os.path.getmtime(full_p))[:6]
                    except Exception:
                        mtime = time.localtime()[:6]

                    zinfo = zipfile.ZipInfo(filename=rel_p.replace('\\', '/'), date_time=mtime)
                    zinfo.compress_type = compress_type
                    zinfo.external_attr = 0o644 << 16

                    file_basename = os.path.basename(rel_p)
                    with self.lock:
                        self.progress["current_file"] = file_basename

                    try:
                        with open(full_p, 'rb') as src:
                            with zf.open(zinfo, 'w', force_zip64=True) as dest:
                                while True:
                                    if self.cancel_requested:
                                        raise InterruptedError("Operazione annullata dall'utente.")

                                    chunk = src.read(CHUNK_SIZE)
                                    if not chunk:
                                        break
                                    dest.write(chunk)
                                    processed_bytes += len(chunk)

                                    now = time.time()
                                    if now - last_notify_time >= 0.15:
                                        last_notify_time = now
                                        elapsed_sec = max(0.1, now - start_time)
                                        proc_mb = round(processed_bytes / (1024 * 1024), 1)
                                        speed_mb = round(proc_mb / elapsed_sec, 1)

                                        if total_bytes > 0:
                                            pct = min(99, int((processed_bytes / total_bytes) * 100 * pct_scale))
                                        else:
                                            pct = 99

                                        rem_bytes = max(0, total_bytes - processed_bytes)
                                        if speed_mb > 0 and rem_bytes > 0:
                                            eta_sec = int((rem_bytes / (1024 * 1024)) / speed_mb)
                                            if eta_sec < 60:
                                                eta_fmt = f"{eta_sec}s rimanenti"
                                            elif eta_sec < 3600:
                                                eta_fmt = f"{eta_sec // 60}m {eta_sec % 60}s rimanenti"
                                            else:
                                                h = eta_sec // 3600
                                                m = (eta_sec % 3600) // 60
                                                eta_fmt = f"{h}h {m}m rimanenti"
                                        elif pct >= 99:
                                            eta_sec = 0
                                            eta_fmt = "Quasi finito (finalizzazione)..."
                                        else:
                                            eta_sec = 0
                                            eta_fmt = "Calcolo in corso..."

                                        with self.lock:
                                            self.progress["percent"] = pct
                                            self.progress["processed_mb"] = proc_mb
                                            self.progress["files_processed"] = processed_files
                                            self.progress["speed_mb_s"] = speed_mb
                                            self.progress["eta_seconds"] = eta_sec
                                            self.progress["eta_formatted"] = eta_fmt
                                            self.progress["status"] = f"Compressione in corso: {pct}% ({proc_mb}/{total_mb} MB - {speed_mb} MB/s)"
                                            self.progress["elapsed_seconds"] = int(elapsed_sec)
                                        self._notify_subscribers()

                    except InterruptedError:
                        raise
                    except Exception:
                        # Non-critical skipped file (e.g. transient log or locked cache file)
                        processed_bytes += sz
                        skipped_files.append(rel_p.replace('\\', '/'))

                    processed_files += 1

                # Metadati del backup: servono al ripristino per sapere cosa era stato escluso
                if not self.cancel_requested:
                    zf.writestr(BACKUP_INFO_NAME, json.dumps({
                        "format": 2,
                        "created_at": datetime.now().isoformat(timespec="seconds"),
                        # Di quale server è: il ripristino avvisa se lo si vuole estrarre in un altro
                        "server_id": self._job_identity.get("server_id", ""),
                        "server_name": self._job_identity.get("server_name", ""),
                        "server_dir": server_dir,
                        "exclude_lod_cache": bool(self.exclude_lod_cache),
                        "ignored_dirs": sorted(BACKUP_IGNORED_DIRS),
                        "skipped": sorted(skipped_files),
                    }, ensure_ascii=False))

            self._active_zf = None

            if self.cancel_requested:
                raise InterruptedError("Operazione annullata dall'utente.")

            # 4. Step: Atomic rename from .tmp to final Backup_<NomeServer>.zip
            if os.path.exists(temp_zip_path):
                os.replace(temp_zip_path, self.target_path)

            elapsed = int(time.time() - start_time)
            final_size_mb = round(os.path.getsize(self.target_path) / (1024 * 1024), 1)

            # 5. Step: Optional Cloud Sync (Google Drive / OneDrive) - ONLY if opt-in checkbox was checked
            cloud_synced = False
            cloud_err = None
            if self._will_copy_to_cloud():
                try:
                    self._copy_to_cloud(os.path.join(self.cloud_sync_dir, self.target_filename))
                    cloud_synced = True
                except Exception as ce:
                    cloud_err = str(ce)

            with self.lock:
                self.progress["percent"] = 100
                self.progress["active"] = False
                avg_speed = round(final_size_mb / max(0.1, elapsed), 1)
                self.progress["speed_mb_s"] = avg_speed
                self.progress["eta_seconds"] = 0
                self.progress["eta_formatted"] = "Completato"
                if cloud_synced:
                    status_msg = f"Backup completato in {elapsed}s: {final_size_mb} MB, copiato anche su Google Drive."
                elif cloud_err:
                    status_msg = f"Backup completato in {elapsed}s: {final_size_mb} MB, ma la copia su Google Drive non è riuscita: {cloud_err}"
                else:
                    status_msg = f"Backup completato in {elapsed}s: {final_size_mb} MB."
                self.progress["status"] = status_msg
                self.progress["phase"] = "done"
                self.progress["cloud_synced"] = cloud_synced
                self.progress["cloud_error"] = cloud_err
                self.progress["completed_at"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
                self.progress["processed_mb"] = final_size_mb
                self.progress["elapsed_seconds"] = elapsed
                self.is_running = False
            self._notify_subscribers()

        except InterruptedError:
            self._active_zf = None
            if os.path.exists(temp_zip_path):
                try:
                    os.remove(temp_zip_path)
                except Exception:
                    pass
            with self.lock:
                self.is_running = False
                self.cancel_requested = False
                self.progress["active"] = False
                self.progress["percent"] = 0
                self.progress["speed_mb_s"] = 0
                self.progress["eta_seconds"] = 0
                self.progress["eta_formatted"] = "Annullato"
                self.progress["status"] = "Creazione backup annullata e bloccata con successo."
                self.progress["error"] = "Operazione annullata dall'utente."
            self._notify_subscribers()

        except Exception as e:
            self._active_zf = None
            if os.path.exists(temp_zip_path):
                try:
                    os.remove(temp_zip_path)
                except Exception:
                    pass
            with self.lock:
                self.is_running = False
                self.cancel_requested = False
                self.progress["active"] = False
                self.progress["status"] = f"Errore durante il backup: {e}"
                self.progress["error"] = str(e)
            self._notify_subscribers()

    @staticmethod
    def _free_bytes(path: Optional[str]) -> Optional[int]:
        """Spazio libero del disco della cartella (per Google Drive per desktop è lo spazio libero dell'account)."""
        try:
            return shutil.disk_usage(path).free if path and os.path.isdir(path) else None
        except Exception:
            return None

    def estimate_backup(self) -> Dict[str, Any]:
        """Quanto peserà il backup: i file che il backup salverebbe (tutta la cartella del server, con le stesse
        regole di _run_backup_worker), più lo spazio libero sul Desktop e nella cartella di Google Drive."""
        if not self.server_dir or not os.path.isdir(self.server_dir):
            return {"status": "error", "message": "Nessun server collegato."}
        total_bytes = 0
        files = 0
        for root, dirs, names in os.walk(self.server_dir):
            dirs[:] = [d for d in dirs if d.lower() not in BACKUP_IGNORED_DIRS]
            for f in names:
                rel_p = os.path.relpath(os.path.join(root, f), self.server_dir)
                if f == self.target_filename or _is_excluded_from_backup(rel_p, False):
                    continue
                try:
                    total_bytes += os.path.getsize(os.path.join(root, f))
                    files += 1
                except OSError:
                    pass
        cloud_dir = self.cloud_sync_dir
        cloud_ok = bool(cloud_dir) and os.path.isdir(cloud_dir)
        # Il backup vecchio viene sostituito: il suo spazio torna libero
        old_desktop = os.path.getsize(self.target_path) if os.path.isfile(self.target_path) else 0
        old_cloud = 0
        if cloud_ok:
            old = os.path.join(cloud_dir, self.target_filename)
            old_cloud = os.path.getsize(old) if os.path.isfile(old) else 0
        desk_free = self._free_bytes(self.desktop_dir)
        cloud_free = self._free_bytes(cloud_dir) if cloud_ok else None
        return {
            "status": "success",
            "total_bytes": total_bytes,
            "total_gb": round(total_bytes / (1024 ** 3), 2),
            "files": files,
            "desktop_free_gb": round((desk_free + old_desktop) / (1024 ** 3), 1) if desk_free is not None else None,
            "desktop_fits": desk_free is None or desk_free + old_desktop > total_bytes,
            "cloud_configured": bool(cloud_dir),
            "cloud_available": cloud_ok,
            "cloud_free_gb": round((cloud_free + old_cloud) / (1024 ** 3), 1) if cloud_free is not None else None,
            "cloud_fits": cloud_free is None or cloud_free + old_cloud > total_bytes,
            "cloud_check": self.cloud_backup_check(),
        }

    def _will_copy_to_cloud(self) -> bool:
        return bool(getattr(self, "copy_to_cloud", False) and self.cloud_sync_dir and os.path.isdir(self.cloud_sync_dir))

    def _copy_to_cloud(self, dest: str):
        """Copia lo ZIP nella cartella di Google Drive a blocchi, con avanzamento (barra 50-100%) e annullabile.
        Scrive prima un file .tmp: la copia vecchia su Drive resta finché quella nuova non è completa."""
        size = os.path.getsize(self.target_path)
        total_mb = round(size / (1024 * 1024), 1)
        tmp = dest + ".tmp"
        start = last = time.time()
        done = 0
        with self.lock:
            self.progress["phase"] = "cloud"
            self.progress["current_file"] = self.target_filename
        try:
            with open(self.target_path, "rb") as src, open(tmp, "wb") as out:
                while True:
                    if self.cancel_requested:
                        raise InterruptedError("copia annullata dall'utente")
                    chunk = src.read(8 * 1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    now = time.time()
                    if now - last >= 0.5 or done == size:
                        last = now
                        pct = int(done * 100 / size) if size else 100
                        speed = round(done / (1024 * 1024) / max(0.1, now - start), 1)
                        eta = int((size - done) / (1024 * 1024) / speed) if speed > 0 else 0
                        with self.lock:
                            self.progress["percent"] = min(99, 50 + pct // 2)
                            self.progress["processed_mb"] = round(done / (1024 * 1024), 1)
                            self.progress["total_mb"] = total_mb
                            self.progress["speed_mb_s"] = speed
                            self.progress["eta_seconds"] = eta
                            self.progress["eta_formatted"] = (f"{eta}s rimanenti" if eta < 60 else
                                                              f"{eta // 60}m {eta % 60}s rimanenti" if eta < 3600 else
                                                              f"{eta // 3600}h {(eta % 3600) // 60}m rimanenti")
                            self.progress["status"] = (f"Copia su Google Drive in corso: {pct}% "
                                                       f"({round(done / (1024 * 1024), 1)}/{total_mb} MB - {speed} MB/s)")
                        self._notify_subscribers()
            shutil.copystat(self.target_path, tmp)
            os.replace(tmp, dest)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            with self.lock:
                self.cancel_requested = False
            raise

    def busy_message(self) -> Optional[str]:
        """Motivo per non avviare il server mentre un backup o un ripristino legge o scrive i suoi file."""
        with self.lock:
            if not self.is_running:
                return None
            restore = self.progress.get("operation") == "restore"
        if restore:
            return "Ripristino in corso: il server si potrà avviare quando sarà finito."
        return "Backup in corso: il server si potrà avviare quando sarà finito."

    def cloud_backup_check(self) -> Optional[Dict[str, Any]]:
        """Confronta la copia su Google Drive con l'ultimo backup sul Desktop. None se va bene o se non si può
        confrontare (nessun backup sul Desktop, Drive non collegato); altrimenti il problema:
        "missing" (su Drive non c'è), "older" (su Drive è più vecchia), "size" (stessa data ma peso diverso)."""
        cloud_dir = self.cloud_sync_dir
        if not os.path.isfile(self.target_path) or not cloud_dir or not os.path.isdir(cloud_dir):
            return None
        desk_mtime = os.path.getmtime(self.target_path)
        desk_size = os.path.getsize(self.target_path)
        info = {
            "desktop_date": datetime.fromtimestamp(desk_mtime).strftime("%d/%m/%Y %H:%M"),
            "desktop_mb": round(desk_size / (1024 * 1024), 1),
        }
        cloud_path = os.path.join(cloud_dir, self.target_filename)
        if not os.path.isfile(cloud_path):
            return dict(info, problem="missing")
        cloud_mtime = os.path.getmtime(cloud_path)
        cloud_size = os.path.getsize(cloud_path)
        info.update({
            "cloud_date": datetime.fromtimestamp(cloud_mtime).strftime("%d/%m/%Y %H:%M"),
            "cloud_mb": round(cloud_size / (1024 * 1024), 1),
        })
        # La copia su Drive conserva la data del file (shutil.copy2): un'ora di margine per gli orologi
        if cloud_mtime < desk_mtime - 3600:
            return dict(info, problem="older")
        if abs(cloud_size - desk_size) > max(1024 * 1024, desk_size * 0.02):
            return dict(info, problem="size")
        return None

    def _backup_entry(self, path: str, location: str, entry_id: str) -> Optional[Dict[str, Any]]:
        try:
            sz = os.path.getsize(path)
            formatted_d = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%d/%m/%Y %H:%M:%S")
        except OSError:
            return None
        meta = self._cached_info(path)
        return {
            "id": entry_id,
            "location": location,
            "location_label": "Desktop" if location == "desktop" else "Google Drive / Cloud",
            "path": path,
            "filename": os.path.basename(path),
            "size_mb": round(sz / (1024 * 1024), 1),
            "date": formatted_d,
            "modified": formatted_d,
            # Di quale server è (None per i backup delle versioni precedenti, che non lo scrivevano)
            "server_name": meta.get("server_name") or None,
            "same_server": self.belongs_to_current(meta),
        }

    @staticmethod
    def _is_our_backup_name(filename: str) -> bool:
        low = filename.lower()
        return low.endswith(".zip") and low.startswith("backup_")

    def list_available_backups(self) -> List[Dict[str, Any]]:
        """Backup sul Desktop (uno per server, più quello unico delle versioni precedenti) e su Google Drive.
        Prima quelli del server attivo."""
        backups = []
        current = os.path.normcase(self.target_path)
        # 1. Desktop: "desktop" resta l'id del backup del server attivo (lo usano le versioni precedenti dell'app)
        try:
            names = sorted(os.listdir(self.desktop_dir)) if os.path.isdir(self.desktop_dir) else []
        except OSError:
            names = []
        for f in names:
            fp = os.path.join(self.desktop_dir, f)
            if not self._is_our_backup_name(f) or not os.path.isfile(fp):
                continue
            # Sul Desktop solo i file creati da Voxarium (con i metadati) o il backup unico delle versioni precedenti
            if f.lower() != LEGACY_BACKUP_FILENAME.lower() and not self._cached_info(fp):
                continue
            is_current = os.path.normcase(fp) == current
            entry = self._backup_entry(fp, "desktop", "desktop" if is_current else f"desktop_{f}")
            if entry:
                backups.append(entry)

        # 2. Cloud sync backups
        if self.cloud_sync_dir and os.path.isdir(self.cloud_sync_dir):
            try:
                for root, dirs, files in os.walk(self.cloud_sync_dir):
                    for f in files:
                        if f.lower().endswith(".zip") and "backup" in f.lower():
                            fp = os.path.join(root, f)
                            if os.path.normcase(fp) != current:
                                entry = self._backup_entry(fp, "cloud", f"cloud_{f}")
                                if entry:
                                    backups.append(entry)
            except Exception:
                pass
        # Prima il server attivo, poi i backup vecchi senza nome del server, poi gli altri server
        order = {True: 0, None: 1, False: 2}
        backups.sort(key=lambda b: (order[b["same_server"]], b["location"] != "desktop"))
        return backups

    def _resolve_known_backup(self, id_or_path: Optional[str]) -> Optional[str]:
        if not id_or_path or id_or_path in ("desktop", self.target_filename):
            return self.target_path if os.path.exists(self.target_path) else None
        wanted = os.path.normcase(os.path.abspath(id_or_path)) if os.path.isabs(id_or_path) else None
        for b in self.list_available_backups():
            if b.get("id") == id_or_path or b.get("filename") == id_or_path:
                return b["path"]
            if wanted and os.path.normcase(os.path.abspath(b["path"])) == wanted:
                return b["path"]
        return None

    def delete_backup(self, backup_id_or_path: str) -> Dict[str, Any]:
        with self.lock:
            if self.is_running:
                return {"status": "error", "message": "Impossibile eliminare backup mentre un'operazione è in corso."}

        target_path = self._resolve_known_backup(backup_id_or_path)
        if not target_path or not os.path.exists(target_path):
            return {"status": "error", "message": f"File di backup non trovato: {backup_id_or_path}"}

        if not target_path.lower().endswith(".zip"):
            return {"status": "error", "message": "Operazione non consentita: il file selezionato non è un archivio .zip"}

        try:
            os.remove(target_path)
            fname = os.path.basename(target_path)
            return {"status": "success", "message": f"Archivio backup '{fname}' eliminato con successo!"}
        except Exception as e:
            return {"status": "error", "message": f"Errore durante l'eliminazione del backup: {e}"}

    def restore_check(self, src: str) -> Optional[Dict[str, Any]]:
        """None se il backup si può ripristinare nel server attivo senza chiedere; altrimenti l'errore da mostrare
        (code "other_server": è di un altro server; "unknown_server": backup vecchio, con più server non si sa di quale)."""
        meta = self._cached_info(src)
        same = self.belongs_to_current(meta)
        ident = self.server_identity()
        if same is True:
            return None
        if same is None:
            # Con un solo server il backup vecchio non può che essere suo
            if ident.get("servers") is None or ident["servers"] <= 1:
                return None
            return {"status": "error", "code": "unknown_server", "backup_server": None,
                    "current_server": ident["server_name"],
                    "message": (f"Non si sa di quale server sia questo backup (creato con una versione precedente). "
                                f"Il ripristino sostituisce tutti i file del server attivo \"{ident['server_name']}\": "
                                f"conferma dal pannello sul PC se è davvero suo.")}
        other = meta.get("server_name") or "?"
        return {"status": "error", "code": "other_server", "backup_server": other,
                "current_server": ident["server_name"],
                "message": (f"Questo backup è del server \"{other}\", ma il server attivo è \"{ident['server_name']}\". "
                            f"Il ripristino sostituirebbe tutti i suoi file con quelli dell'altro server: "
                            f"attiva prima il server \"{other}\" oppure conferma dal pannello sul PC.")}

    def start_restore(self, is_server_stopped: bool, source_path: Optional[str] = None,
                      confirm_other_server: bool = False) -> Dict[str, Any]:
        with self.lock:
            if self.is_running:
                return {"status": "error", "message": "Un'operazione di backup o ripristino è già in corso."}
            if not is_server_stopped:
                return {"status": "error", "message": "Il server Minecraft deve essere COMPLETAMENTE SPENTO per eseguire il ripristino sicuro."}

            if not self.server_dir or not os.path.exists(self.server_dir):
                return {"status": "error", "message": f"Cartella del server Minecraft non configurata o non trovata: {self.server_dir}"}

            src = self._resolve_known_backup(source_path)
            if not src or not os.path.exists(src):
                return {"status": "error", "message": f"File di backup non trovato: {source_path or self.target_filename}"}

            # Il ripristino cancella i file che non sono nel backup: un backup di un altro server sostituirebbe
            # tutto il server attivo. Si fa solo se l'utente lo conferma.
            check = self.restore_check(src)
            if check and not confirm_other_server:
                return check

            self.is_running = True
            self.cancel_requested = False
            self.progress = {
                "operation": "restore",
                "active": True,
                "percent": 0,
                "processed_mb": 0.0,
                "total_mb": 0.0,
                "current_file": "Apertura archivio di backup...",
                "files_processed": 0,
                "files_total": 0,
                "speed_mb_s": 0.0,
                "eta_seconds": 0,
                "eta_formatted": "Calcolo in corso...",
                "elapsed_seconds": 0,
                "status": "Preparazione al ripristino dell'archivio...",
                "error": None,
                "target_path": src,
                "target_filename": os.path.basename(src),
                "completed_at": None,
                "cloud_synced": False,
                "cloud_error": None
            }
        self._notify_subscribers()

        def _worker():
            start_time = time.time()
            last_notify = time.time()
            done_bytes = 0
            files_done = 0
            failed_files = []
            try:
                with zipfile.ZipFile(src, 'r') as zf:
                    infolist = zf.infolist()
                    meta = {}
                    if BACKUP_INFO_NAME in zf.namelist():
                        try:
                            meta = json.loads(zf.read(BACKUP_INFO_NAME).decode("utf-8"))
                        except Exception:
                            meta = {}
                    # Archivi senza metadati (creati prima di questa versione): per sicurezza Distant Horizons
                    # si considera escluso, quindi i suoi file non vengono mai cancellati
                    exclude_lod = bool(meta.get("exclude_lod_cache", True))

                    infolist = [i for i in infolist if i.filename != BACKUP_INFO_NAME]
                    total_bytes = sum(i.file_size for i in infolist if not i.is_dir())
                    files_total = len([i for i in infolist if not i.is_dir()])
                    server_root = os.path.abspath(self.server_dir)
                    total_mb = round(total_bytes / (1024 * 1024), 1)

                    with self.lock:
                        self.progress["total_mb"] = total_mb
                        self.progress["files_total"] = files_total
                        self.progress["status"] = f"Avvio estrazione archivio ({total_mb} MB in {files_total} file)..."
                    self._notify_subscribers()

                    CHUNK_SIZE = 2 * 1024 * 1024  # 2 MB chunks
                    for info in infolist:
                        if self.cancel_requested:
                            raise InterruptedError("Ripristino annullato dall'utente.")

                        dest = os.path.abspath(os.path.join(server_root, info.filename))
                        if os.path.commonpath([server_root, dest]) != server_root:
                            failed_files.append((info.filename, "percorso fuori dalla cartella del server"))
                            continue

                        if info.is_dir():
                            os.makedirs(dest, exist_ok=True)
                            continue

                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        old_attrs = _make_writable(dest)
                        try:
                            with zf.open(info) as src_f, open(dest, "wb") as out_f:
                                while True:
                                    if self.cancel_requested:
                                        raise InterruptedError("Ripristino annullato dall'utente.")
                                    chunk = src_f.read(CHUNK_SIZE)
                                    if not chunk:
                                        break
                                    out_f.write(chunk)
                                    done_bytes += len(chunk)
                                    now = time.time()
                                    if now - last_notify >= 0.15:
                                        elapsed = max(0.1, now - start_time)
                                        pct = min(99, int((done_bytes / max(1, total_bytes)) * 100))
                                        speed_mb_s = round((done_bytes / (1024 * 1024)) / elapsed, 1)
                                        rem_bytes = max(0, total_bytes - done_bytes)
                                        rem_sec = int(rem_bytes / max(1, (done_bytes / elapsed)))
                                        mins, secs = divmod(rem_sec, 60)
                                        eta_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
                                        with self.lock:
                                            self.progress["percent"] = pct
                                            self.progress["processed_mb"] = round(done_bytes / (1024 * 1024), 1)
                                            self.progress["speed_mb_s"] = speed_mb_s
                                            self.progress["eta_seconds"] = rem_sec
                                            self.progress["eta_formatted"] = eta_str
                                            self.progress["elapsed_seconds"] = int(elapsed)
                                            self.progress["files_processed"] = files_done
                                            self.progress["current_file"] = os.path.basename(info.filename)
                                            self.progress["status"] = f"Ripristino in corso: {pct}% ({round(done_bytes / (1024 * 1024), 1)}/{total_mb} MB)"
                                        self._notify_subscribers()
                                        last_notify = now

                            files_done += 1
                            # Restore timestamp
                            if len(info.date_time) >= 6 and info.date_time[0] >= 1980:
                                try:
                                    t = time.mktime(info.date_time + (0, 0, -1))
                                    os.utime(dest, (t, t))
                                except Exception:
                                    pass
                            # Restore file attributes if previously saved
                            if old_attrs not in (None, -1, 0xFFFFFFFF):
                                try:
                                    ctypes.windll.kernel32.SetFileAttributesW(dest, old_attrs)
                                except Exception:
                                    pass
                        except InterruptedError:
                            raise
                        except Exception as fe:
                            failed_files.append((info.filename, str(fe)))

                    # Pulizia dei file creati dopo il backup. Si eliminano SOLO i file che il backup avrebbe salvato:
                    # quelli esclusi (backups/, crash-reports/, Distant Horizons se escluso, file di sistema...) non si toccano.
                    backup_file_set = {
                        os.path.normcase(os.path.normpath(i.filename))
                        for i in infolist
                        if not i.is_dir()
                    }
                    skipped_set = {
                        os.path.normcase(os.path.normpath(p))
                        for p in meta.get("skipped", [])
                    }
                    for root, dirs, files in os.walk(server_root, topdown=False):
                        if self.cancel_requested:
                            raise InterruptedError("Ripristino annullato dall'utente.")
                        for f in files:
                            full_f = os.path.join(root, f)
                            rel_f = os.path.relpath(full_f, server_root)
                            if _is_excluded_from_backup(rel_f, exclude_lod):
                                continue
                            norm_rel = os.path.normcase(os.path.normpath(rel_f))
                            if norm_rel not in backup_file_set and norm_rel not in skipped_set:
                                try:
                                    _make_writable(full_f)
                                    os.remove(full_f)
                                except Exception:
                                    pass
                        rel_root = os.path.relpath(root, server_root)
                        if rel_root != "." and not _is_excluded_from_backup(os.path.join(rel_root, "_"), exclude_lod):
                            try:
                                if not os.listdir(root):
                                    os.rmdir(root)
                            except Exception:
                                pass

                    elapsed = max(1, int(time.time() - start_time))
                    final_mb = round(done_bytes / (1024 * 1024), 1)
                    avg_speed = round(final_mb / max(0.1, elapsed), 1)
                    status_text = f"Ripristino completato con successo in {elapsed}s! Mondo ripristinato ({files_done} file, {final_mb} MB)."
                    if failed_files:
                        status_text += f" (Avviso: {len(failed_files)} file secondari saltati)"

                    with self.lock:
                        self.is_running = False
                        self.progress["active"] = False
                        self.progress["percent"] = 100
                        self.progress["processed_mb"] = final_mb
                        self.progress["speed_mb_s"] = avg_speed
                        self.progress["eta_seconds"] = 0
                        self.progress["eta_formatted"] = "Completato"
                        self.progress["elapsed_seconds"] = elapsed
                        self.progress["files_processed"] = files_done
                        self.progress["completed_at"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
                        self.progress["status"] = status_text
                    self._notify_subscribers()

            except InterruptedError:
                with self.lock:
                    self.is_running = False
                    self.cancel_requested = False
                    self.progress["active"] = False
                    self.progress["percent"] = 0
                    self.progress["speed_mb_s"] = 0
                    self.progress["eta_seconds"] = 0
                    self.progress["eta_formatted"] = "Annullato"
                    self.progress["status"] = "Ripristino annullato e bloccato dall'utente."
                    self.progress["error"] = "Operazione annullata dall'utente."
                self._notify_subscribers()
            except Exception as e:
                with self.lock:
                    self.is_running = False
                    self.cancel_requested = False
                    self.progress["active"] = False
                    self.progress["status"] = f"Errore nel ripristino: {e}"
                    self.progress["error"] = str(e)
                self._notify_subscribers()

        threading.Thread(target=_worker, daemon=True).start()
        return {"status": "success", "message": "Ripristino avviato in background."}
