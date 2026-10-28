"""Mod della cartella mods/: elenco, attiva/disattiva, carica, elimina."""
from fastapi import HTTPException
from fastapi import Request
from pydantic import BaseModel
import instance_manager
import mod_info
import modrinth_store
import os
import zipfile
from fastapi import APIRouter

from app import get_mods_dir, get_server_dir_path, manager, save_upload, verify_admin_access

router = APIRouter()


# =========================================================
# MODS MANAGEMENT ROUTES (PC ONLY)
# =========================================================
class ModActionRequest(BaseModel):
    filename: str

class ModBatchToggleRequest(BaseModel):
    action: str  # "enable_all" or "disable_all"

def get_mod_stem(fname: str) -> str:
    lower = fname.lower()
    if lower.endswith(".jar.disabled"):
        clean = lower[:-13]
    elif lower.endswith(".disabled"):
        clean = lower[:-9]
        if clean.endswith(".jar"):
            clean = clean[:-4]
    elif lower.endswith(".jar"):
        clean = lower[:-4]
    else:
        clean = lower
    return clean

@router.get("/api/mods")
def api_list_mods(request: Request):
    verify_admin_access(request)
    mods_dir = get_mods_dir()
    if not os.path.exists(mods_dir):
        return {
            "status": "success",
            "mods": [],
            "total": 0,
            "enabled_count": 0,
            "disabled_count": 0,
            "duplicate_count": 0,
            "total_size_bytes": 0,
            "total_size_formatted": "0 MB",
            "mods_dir": mods_dir,
            "exists": False
        }
    
    items = []
    enabled_count = 0
    disabled_count = 0
    total_size_bytes = 0
    
    try:
        filenames = sorted(os.listdir(mods_dir), key=lambda s: s.lower())
    except Exception as e:
        return {"status": "error", "message": str(e), "mods": []}

    for fname in filenames:
        fpath = os.path.join(mods_dir, fname)
        if not os.path.isfile(fpath):
            continue
        lower = fname.lower()
        if lower.endswith(".jar"):
            enabled = True
            enabled_count += 1
        elif lower.endswith(".jar.disabled") or lower.endswith(".disabled"):
            enabled = False
            disabled_count += 1
        else:
            continue
            
        try:
            stat = os.stat(fpath)
            size = stat.st_size
            mtime = stat.st_mtime
        except Exception:
            size = 0
            mtime = 0
            
        total_size_bytes += size

        if size < 1024:
            size_fmt = f"{size} B"
        elif size < 1024 * 1024:
            size_fmt = f"{size / 1024:.1f} KB"
        else:
            size_fmt = f"{size / (1024 * 1024):.1f} MB"
            
        items.append({
            "filename": fname,
            "enabled": enabled,
            "stem": get_mod_stem(fname),
            "size_bytes": size,
            "size_formatted": size_fmt,
            "mtime": mtime
        })

    # Duplicates & conflicts detection (same stem or multiple files for same mod)
    from collections import Counter
    stem_counts = Counter(it["stem"] for it in items)
    duplicate_count = 0
    for it in items:
        is_dup = stem_counts[it["stem"]] > 1
        it["is_duplicate"] = is_dup
        if is_dup:
            duplicate_count += 1
        
    if total_size_bytes < 1024 * 1024:
        total_size_fmt = f"{total_size_bytes / 1024:.1f} KB"
    else:
        total_size_fmt = f"{total_size_bytes / (1024 * 1024):.1f} MB"

    server_dir = get_server_dir_path()
    active_inst = instance_manager.get_active_instance()
    eff_loader = active_inst.get("server_type", "")
    eff_version = active_inst.get("mc_version", "")
    # Aggiornamenti solo se tipo e versione del server sono noti (altrimenti si proporrebbero mod di un altro loader)
    upd_data = (modrinth_store.get_cached_or_check_updates(server_dir, loader=eff_loader, mc_version=eff_version, force=False)
                if eff_loader and eff_version else {})
    upd_by_file = upd_data.get("updates_by_file", {})

    updates_available_count = 0
    for it in items:
        fn = it["filename"]
        upd = upd_by_file.get(fn)
        if upd:
            it["update_available"] = True
            it["update_info"] = upd
            updates_available_count += 1
        else:
            it["update_available"] = False

    return {
        "status": "success",
        "mods": items,
        "total": len(items),
        "enabled_count": enabled_count,
        "disabled_count": disabled_count,
        "duplicate_count": duplicate_count,
        "has_updates": updates_available_count > 0,
        "updates_count": updates_available_count,
        "total_size_bytes": total_size_bytes,
        "total_size_formatted": total_size_fmt,
        "mods_dir": mods_dir,
        "server_state": manager.state,
        "exists": True
    }

@router.get("/api/mods/info")
def api_mod_info(filename: str, request: Request):
    """Scheda di una mod: dati scritti nel .jar e, se la mod è su Modrinth, quelli del progetto."""
    verify_admin_access(request)
    clean_name = os.path.basename(filename or "").strip()
    file_path = os.path.join(get_mods_dir(), clean_name)
    if not clean_name or not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="File mod non trovato")
    return {"status": "success", **mod_info.get_mod_info(file_path)}

@router.post("/api/mods/batch-toggle")
def api_batch_toggle_mods(req: ModBatchToggleRequest, request: Request):
    verify_admin_access(request)
    mods_dir = get_mods_dir()
    if not os.path.exists(mods_dir):
        raise HTTPException(status_code=404, detail="Cartella mods non trovata")
    
    if req.action not in ("enable_all", "disable_all"):
        raise HTTPException(status_code=400, detail="Azione non valida. Usa 'enable_all' o 'disable_all'")

    try:
        filenames = sorted(os.listdir(mods_dir), key=lambda s: s.lower())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore lettura cartella mods: {e}")

    modified = 0
    errors = []
    
    for fname in filenames:
        fpath = os.path.join(mods_dir, fname)
        if not os.path.isfile(fpath):
            continue
        lower = fname.lower()
        
        if req.action == "enable_all":
            if lower.endswith(".jar.disabled") or lower.endswith(".disabled"):
                if lower.endswith(".jar.disabled"):
                    new_name = fname[:-9]
                else:
                    base_no_disabled = fname[:-9]
                    new_name = base_no_disabled if base_no_disabled.lower().endswith(".jar") else (base_no_disabled + ".jar")
                new_path = os.path.join(mods_dir, new_name)
                if os.path.exists(new_path):
                    errors.append(f"Conflitto per {fname}: esiste già {new_name}")
                    continue
                try:
                    os.rename(fpath, new_path)
                    modified += 1
                except Exception as e:
                    if isinstance(e, PermissionError) or "used by another process" in str(e).lower() or "[WinError 32]" in str(e):
                        errors.append(f"File bloccato dal server: {fname}")
                    else:
                        errors.append(f"{fname}: {e}")
        elif req.action == "disable_all":
            if lower.endswith(".jar"):
                new_name = fname + ".disabled"
                new_path = os.path.join(mods_dir, new_name)
                if os.path.exists(new_path):
                    errors.append(f"Conflitto per {fname}: esiste già {new_name}")
                    continue
                try:
                    os.rename(fpath, new_path)
                    modified += 1
                except Exception as e:
                    if isinstance(e, PermissionError) or "used by another process" in str(e).lower() or "[WinError 32]" in str(e):
                        errors.append(f"File bloccato dal server: {fname}")
                    else:
                        errors.append(f"{fname}: {e}")

    return {
        "status": "success",
        "action": req.action,
        "modified_count": modified,
        "errors": errors
    }

@router.post("/api/mods/toggle")
def api_toggle_mod(req: ModActionRequest, request: Request):
    verify_admin_access(request)
    mods_dir = get_mods_dir()
    clean_name = os.path.basename(req.filename).strip()
    file_path = os.path.join(mods_dir, clean_name)
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File mod non trovato")
        
    lower = clean_name.lower()
    if lower.endswith(".jar"):
        new_name = clean_name + ".disabled"
    elif lower.endswith(".jar.disabled"):
        new_name = clean_name[:-9]
    elif lower.endswith(".disabled"):
        base_no_disabled = clean_name[:-9]
        new_name = base_no_disabled if base_no_disabled.lower().endswith(".jar") else (base_no_disabled + ".jar")
    else:
        raise HTTPException(status_code=400, detail="Formato file non supportato. Solo file .jar o .disabled")

    new_path = os.path.join(mods_dir, new_name)
    if os.path.exists(new_path):
        raise HTTPException(status_code=409, detail=f"Esiste già un file chiamato '{new_name}' nella cartella mods")

    try:
        os.rename(file_path, new_path)
        is_now_enabled = new_name.lower().endswith(".jar")
        return {"status": "success", "new_filename": new_name, "enabled": is_now_enabled}
    except Exception as e:
        if isinstance(e, PermissionError) or "used by another process" in str(e).lower() or "[WinError 32]" in str(e):
            raise HTTPException(status_code=409, detail="Impossibile rinominare la mod: il server Minecraft è in esecuzione e blocca il file. Spegni il server per disattivare o attivare le mod.")
        raise HTTPException(status_code=500, detail=f"Errore durante modifica stato mod: {e}")

@router.post("/api/mods/upload_file")
async def api_upload_mod_file(request: Request, name: str):
    verify_admin_access(request)
    mods_dir = get_mods_dir()
    os.makedirs(mods_dir, exist_ok=True)
    clean_name = os.path.basename(name).strip()
    if not clean_name.lower().endswith(".jar"):
        raise HTTPException(status_code=400, detail="Solo i file con estensione .jar possono essere installati nella cartella mods")
        
    target_path = os.path.join(mods_dir, clean_name)
    # Prima <mod>.part (non finisce in .jar: Minecraft non lo carica), poi il controllo del .jar e lo scambio.
    # Prima un caricamento interrotto lasciava un .jar a metà in mods/ (crash all'avvio) o rovinava la mod di prima
    part_path = target_path + ".part"
    try:
        try:
            size = await save_upload(request, part_path)
            if size == 0:
                raise HTTPException(status_code=400, detail="Il file caricato è vuoto")
            if not zipfile.is_zipfile(part_path):
                raise HTTPException(status_code=400, detail="Il file caricato non è un .jar valido (caricamento interrotto o file rovinato).")
            os.replace(part_path, target_path)
        finally:
            if os.path.exists(part_path):
                try:
                    os.remove(part_path)
                except OSError:
                    pass
        return {"status": "success", "filename": clean_name, "size": size}
    except HTTPException:
        raise
    except Exception as e:
        if isinstance(e, PermissionError) or "used by another process" in str(e).lower() or "[WinError 32]" in str(e):
            raise HTTPException(status_code=409, detail="Impossibile sovrascrivere la mod: il server Minecraft è in esecuzione e blocca il file. Spegni il server prima di installare o aggiornare le mod.")
        raise HTTPException(status_code=500, detail=f"Errore durante il salvataggio della mod: {e}")

@router.post("/api/mods/delete")
def api_delete_mod(req: ModActionRequest, request: Request):
    verify_admin_access(request)
    mods_dir = get_mods_dir()
    clean_name = os.path.basename(req.filename).strip()
    file_path = os.path.join(mods_dir, clean_name)
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File mod non trovato")
        
    try:
        os.remove(file_path)
        return {"status": "success", "filename": clean_name}
    except Exception as e:
        if isinstance(e, PermissionError) or "used by another process" in str(e).lower() or "[WinError 32]" in str(e):
            raise HTTPException(status_code=409, detail="Impossibile eliminare la mod: il server Minecraft è in esecuzione e blocca il file. Spegni il server prima di eliminare le mod.")
        raise HTTPException(status_code=500, detail=f"Errore durante l'eliminazione della mod: {e}")
