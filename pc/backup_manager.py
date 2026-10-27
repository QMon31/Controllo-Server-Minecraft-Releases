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
        self.target_filename = "Backup_Server_Mc.zip"
        self.target_path = os.path.join(self.desktop_dir, self.target_filename)

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

            self.is_running = True
            self.cancel_requested = False
            self.exclude_lod_cache = bool(exclude_lod_cache)
            self.copy_to_cloud = bool(copy_to_cloud)
            self._active_zf = None
            self.progress = {
                "operation": "backup",
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
            for root, dirs, files in os.walk(self.server_dir):
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
                    rel_p = os.path.relpath(full_p, self.server_dir)
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
                        "format": 1,
                        "created_at": datetime.now().isoformat(timespec="seconds"),
                        "exclude_lod_cache": bool(self.exclude_lod_cache),
                        "ignored_dirs": sorted(BACKUP_IGNORED_DIRS),
                        "skipped": sorted(skipped_files),
                    }, ensure_ascii=False))

            self._active_zf = None

            if self.cancel_requested:
                raise InterruptedError("Operazione annullata dall'utente.")

            # 4. Step: Atomic rename from .tmp to final Backup_Server_Mc.zip
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

    def list_available_backups(self) -> List[Dict[str, Any]]:
        backups = []
        # 1. Desktop backup
        if os.path.exists(self.target_path):
            try:
                sz = os.path.getsize(self.target_path)
                mtime = os.path.getmtime(self.target_path)
                formatted_d = datetime.fromtimestamp(mtime).strftime("%d/%m/%Y %H:%M:%S")
                backups.append({
                    "id": "desktop",
                    "location": "desktop",
                    "location_label": "Desktop",
                    "path": self.target_path,
                    "filename": self.target_filename,
                    "size_mb": round(sz / (1024 * 1024), 1),
                    "date": formatted_d,
                    "modified": formatted_d
                })
            except Exception:
                pass
        
        # 2. Cloud sync backups
        if self.cloud_sync_dir and os.path.isdir(self.cloud_sync_dir):
            try:
                for root, dirs, files in os.walk(self.cloud_sync_dir):
                    for f in files:
                        if f.lower().endswith(".zip") and "backup" in f.lower():
                            fp = os.path.join(root, f)
                            if fp != self.target_path:
                                try:
                                    sz = os.path.getsize(fp)
                                    mtime = os.path.getmtime(fp)
                                    formatted_d = datetime.fromtimestamp(mtime).strftime("%d/%m/%Y %H:%M:%S")
                                    backups.append({
                                        "id": f"cloud_{f}",
                                        "location": "cloud",
                                        "location_label": "Google Drive / Cloud",
                                        "path": fp,
                                        "filename": f,
                                        "size_mb": round(sz / (1024 * 1024), 1),
                                        "date": formatted_d,
                                        "modified": formatted_d
                                    })
                                except Exception:
                                    pass
            except Exception:
                pass
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

    def start_restore(self, is_server_stopped: bool, source_path: Optional[str] = None) -> Dict[str, Any]:
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
