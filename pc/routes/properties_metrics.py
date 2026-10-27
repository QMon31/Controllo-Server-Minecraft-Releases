"""Proprietà del server (server.properties), grafici delle ultime 24 ore e cartella cloud dei backup."""
from fastapi import HTTPException
from fastapi import Request
import os
import re
from fastapi import APIRouter

from app import CloudSyncRequest, PropertiesUpdateRequest, backup_mgr, manager, verify_admin_access

router = APIRouter()


# =========================================================
# SERVER.PROPERTIES, 24H METRICS & CLOUD BACKUP CONFIG
# =========================================================
@router.get("/api/server/properties")
def api_get_server_properties(request: Request):
    verify_admin_access(request)
    server_dir = manager.config.get("server_dir", "")
    props_path = os.path.join(server_dir, "server.properties")
    bak_path = props_path + ".bak"
    raw_text = ""
    if os.path.isfile(props_path):
        try:
            with open(props_path, "r", encoding="utf-8", errors="ignore") as f:
                raw_text = f.read()
        except Exception:
            pass

    props = manager.get_server_properties()
    # Mask rcon.password before returning raw_text / props
    raw_text = re.sub(r"(?m)^(rcon\.password=).*$", r"\1********", raw_text)
    if "rcon.password" in props:
        props["rcon.password"] = "********"

    backup_exists = os.path.isfile(bak_path)
    backup_info = {}
    if backup_exists:
        try:
            st = os.stat(bak_path)
            backup_info = {
                "size": st.st_size,
                "mtime": st.st_mtime
            }
        except Exception:
            pass

    return {
        "status": "success",
        "properties": props,
        "raw": raw_text,
        "backup_exists": backup_exists,
        "backup_info": backup_info,
        "server_state": manager.state
    }

@router.post("/api/server/properties/restore-backup")
def api_restore_server_properties_backup(request: Request):
    verify_admin_access(request)
    server_dir = manager.config.get("server_dir", "")
    props_path = os.path.join(server_dir, "server.properties")
    bak_path = props_path + ".bak"
    if not os.path.isfile(bak_path):
        raise HTTPException(status_code=404, detail="Nessun file di backup (server.properties.bak) trovato.")

    try:
        import shutil
        shutil.copy2(bak_path, props_path)
        manager.add_log("[Pannello] File server.properties ripristinato con successo dal backup (.bak).")
        raw_text = ""
        with open(props_path, "r", encoding="utf-8", errors="ignore") as f:
            raw_text = f.read()
        props = manager.get_server_properties()
        raw_text = re.sub(r"(?m)^(rcon\.password=).*$", r"\1********", raw_text)
        if "rcon.password" in props:
            props["rcon.password"] = "********"
        return {
            "status": "ok",
            "message": "File server.properties ripristinato dal backup (.bak)!",
            "properties": props,
            "raw": raw_text,
            "needs_restart": manager.state == "ONLINE"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore durante il ripristino del backup: {e}")

@router.post("/api/server/properties")
def api_save_server_properties(req: PropertiesUpdateRequest, request: Request):
    verify_admin_access(request)
    server_dir = manager.config.get("server_dir", "")
    props_path = os.path.join(server_dir, "server.properties")

    # Read current actual rcon.password to preserve if masked value '********' is sent back
    current_rcon_pw = ""
    if os.path.isfile(props_path):
        try:
            with open(props_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if line.strip().startswith("rcon.password="):
                        current_rcon_pw = line.strip().split("=", 1)[1]
                        break
        except Exception:
            pass

    if req.raw is not None:
        try:
            write_content = req.raw
            if "rcon.password=********" in write_content:
                write_content = re.sub(r"(?m)^rcon\.password=\*{8}$", f"rcon.password={current_rcon_pw}", write_content)
            if os.path.isfile(props_path):
                import shutil
                shutil.copy2(props_path, props_path + ".bak")
            with open(props_path, "w", encoding="utf-8") as f:
                f.write(write_content)
            manager.add_log("[Pannello] File server.properties modificato direttamente in modalità testo (creato backup server.properties.bak).")
            return {"status": "ok", "message": "File salvato con successo!", "needs_restart": manager.state == "ONLINE"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Errore scrittura: {e}")

    if req.properties is not None:
        props_to_save = dict(req.properties)
        if props_to_save.get("rcon.password") == "********":
            props_to_save["rcon.password"] = current_rcon_pw
        res = manager.save_server_properties(props_to_save)
        if res.get("status") == "error":
            raise HTTPException(status_code=400, detail=res.get("message"))
        res["status"] = "ok"
        return res

    raise HTTPException(status_code=400, detail="Dati non forniti")

@router.get("/api/metrics/history-24h")
def api_get_metrics_24h(request: Request):
    verify_admin_access(request)
    with manager.lock:
        hist = list(manager.metrics_history_24h)
        peak_ram = max((d.get("mc_ram_gb", 0) for d in hist), default=0.0)
        peak_cpu = max((d.get("host_cpu", 0) for d in hist), default=0.0)
        peak_players = max((d.get("players", 0) for d in hist), default=0)
        tps_list = [d.get("tps", 0) for d in hist if d.get("tps", 0) > 0]
        avg_tps = round(sum(tps_list) / len(tps_list), 1) if tps_list else 20.0

        return {
            "status": "success",
            "history": hist,
            "total_snapshots": len(hist),
            "summary": {
                "peak_ram_gb": peak_ram,
                "peak_cpu_percent": peak_cpu,
                "peak_players": peak_players,
                "avg_tps": avg_tps
            }
        }

@router.post("/api/metrics/reset-24h")
def api_reset_metrics_24h(request: Request):
    verify_admin_access(request)
    with manager.lock:
        manager.metrics_history_24h.clear()
        manager._last_24h_snapshot_time = 0.0
    manager.add_log("[Pannello] Storico metriche 24 ore azzerato.")
    return {"status": "success", "message": "Storico metriche 24h azzerato con successo"}

@router.get("/api/backup/cloud-config")
def api_get_backup_cloud_config(request: Request):
    verify_admin_access(request)
    return {
        "cloud_sync_dir": manager.config.get("cloud_backup_dir", "")
    }

@router.get("/api/backup/detect-cloud-drives")
def api_detect_cloud_drives(request: Request):
    verify_admin_access(request)
    candidates = []
    # 1. Google Drive Virtual Drive (G:)
    for g_path in [r"G:\Il mio Drive", r"G:\My Drive", r"G:"]:
        if os.path.exists(g_path):
            candidates.append({
                "type": "google_drive",
                "name": "Google Drive (G:)",
                "recommended_path": os.path.join(g_path, "Backup Minecraft")
            })
            break

    # 2. Local Google Drive folder in userprofile
    user_home = os.path.expanduser("~")
    for local_g in [os.path.join(user_home, "Google Drive"), os.path.join(user_home, "GoogleDrive")]:
        if os.path.exists(local_g):
            candidates.append({
                "type": "google_drive",
                "name": "Google Drive (Cartella Locale)",
                "recommended_path": os.path.join(local_g, "Backup Minecraft")
            })
            break

    # 3. OneDrive
    onedrive = os.environ.get("OneDrive") or os.environ.get("OneDriveConsumer") or os.path.join(user_home, "OneDrive")
    if onedrive and os.path.exists(onedrive):
        candidates.append({
            "type": "onedrive",
            "name": "Microsoft OneDrive",
            "recommended_path": os.path.join(onedrive, "Backup Minecraft")
        })

    return {
        "status": "success",
        "found": len(candidates) > 0,
        "drives": candidates
    }

@router.post("/api/backup/cloud-config")
def api_set_backup_cloud_config(req: CloudSyncRequest, request: Request):
    verify_admin_access(request)
    target = req.cloud_sync_dir.strip() if req.cloud_sync_dir and req.cloud_sync_dir.strip() else ""
    if target:
        try:
            os.makedirs(target, exist_ok=True)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Impossibile creare o accedere alla cartella cloud specificata: {e}")
    manager.config["cloud_backup_dir"] = target
    manager.save_config()
    backup_mgr.set_cloud_sync_dir(target if target else None)
    return {"status": "success", "cloud_sync_dir": target}
