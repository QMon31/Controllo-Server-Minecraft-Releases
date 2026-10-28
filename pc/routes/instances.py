"""Più server (istanze): elenco, cambio, riconoscimento da cartella, aggiunta, modifica, eliminazione."""
from fastapi import HTTPException
from fastapi import Request
import instance_manager
import os
from fastapi import APIRouter

from app import (
    AddInstanceRequest, DeleteInstanceRequest, DetectInstanceRequest, SwitchInstanceRequest, UpdateInstanceRequest, backup_mgr, is_safe_server_path, manager,
    minecraft_port_error, request_minecraft_port_sync, verify_admin_access,
)

router = APIRouter()


# =========================================================
# COMMERCIAL MODULE 1: MULTI-INSTANCE SERVER MANAGEMENT
# =========================================================
@router.get("/api/instances")
def api_get_instances(request: Request):
    verify_admin_access(request)
    instances = instance_manager.load_instances_data()
    active = instance_manager.get_active_instance()
    # Segnala le istanze la cui cartella non esiste su questo PC (es. instances.json copiato da un altro computer)
    for inst in instances:
        inst["dir_exists"] = bool(inst.get("server_dir")) and os.path.isdir(inst.get("server_dir"))
    if active:
        active["dir_exists"] = bool(active.get("server_dir")) and os.path.isdir(active.get("server_dir"))
    return {
        "status": "success",
        "instances": instances,
        "active_instance": active,
        "server_state": manager.state
    }

@router.post("/api/instances/switch")
def api_switch_instance(req: SwitchInstanceRequest, request: Request):
    verify_admin_access(request)
    if backup_mgr.is_running:
        # Il backup o il ripristino lavora sulla cartella del server attivo
        raise HTTPException(status_code=409, detail="Attendi la fine del backup o del ripristino in corso.")
    res = instance_manager.switch_instance(req.instance_id, manager=manager)
    if not res.get("success"):
        raise HTTPException(status_code=409, detail=res.get("error", "Impossibile cambiare istanza"))
    try:
        backup_mgr.server_dir = manager.config.get("server_dir", "")
    except Exception:
        pass
    request_minecraft_port_sync()
    return {"status": "success", **res}

@router.post("/api/instances/detect")
def api_detect_instance(req: DetectInstanceRequest, request: Request):
    verify_admin_access(request)
    if not is_safe_server_path(req.folder_path):
        raise HTTPException(status_code=400, detail="Percorso di sistema protetto o non consentito.")
    res = instance_manager.detect_server_details(req.folder_path)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return res

@router.get("/api/instances/detect")
def api_detect_instance_get(folder_path: str = "", request: Request = None):
    verify_admin_access(request)
    if not is_safe_server_path(folder_path):
        raise HTTPException(status_code=400, detail="Percorso di sistema protetto o non consentito.")
    res = instance_manager.detect_server_details(folder_path)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return res

@router.post("/api/instances/add")
def api_add_instance(req: AddInstanceRequest, request: Request):
    verify_admin_access(request)
    if not is_safe_server_path(req.server_dir):
        raise HTTPException(status_code=400, detail="Percorso cartella non consentito per motivi di sicurezza.")

    # Auto-detect if parameters are missing
    name = req.name
    server_type = req.server_type or req.type
    mc_version = req.mc_version or req.version
    server_port = req.server_port or req.port
    server_run_bat = req.server_run_bat or req.run_bat or ""
    server_command = req.server_command or req.launch_cmd or ""

    if not name or not server_type or not mc_version or not server_port:
        detected = instance_manager.detect_server_details(req.server_dir)
        if detected.get("success"):
            name = name or detected.get("suggested_name")
            server_type = server_type or detected.get("server_type")
            mc_version = mc_version or detected.get("mc_version")
            server_port = server_port or detected.get("server_port")
            server_run_bat = server_run_bat or detected.get("server_run_bat")
            server_command = server_command or detected.get("server_command")

    res = instance_manager.add_instance(
        name=name or os.path.basename(os.path.normpath(req.server_dir)) or "Server",
        server_dir=req.server_dir,
        server_run_bat=server_run_bat or "run.bat",
        server_command=server_command or "java -Xmx4G -Xms2G -jar server.jar nogui",
        server_port=server_port or 25565,
        server_type=server_type or "",
        mc_version=mc_version or "",
        set_active=req.set_active or False,
        manager=manager
    )
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return {"status": "success", **res}

@router.post("/api/instances/update")
def api_update_instance(req: UpdateInstanceRequest, request: Request):
    verify_admin_access(request)
    updates = {k: v for k, v in req.model_dump().items() if v is not None and k != "instance_id"}
    if "run_bat" in updates and "server_run_bat" not in updates:
        updates["server_run_bat"] = updates.pop("run_bat")
    if "launch_cmd" in updates and "server_command" not in updates:
        updates["server_command"] = updates.pop("launch_cmd")
    if "port" in updates and "server_port" not in updates:
        updates["server_port"] = updates.pop("port")
    if "type" in updates and "server_type" not in updates:
        updates["server_type"] = updates.pop("type")
    if "version" in updates and "mc_version" not in updates:
        updates["mc_version"] = updates.pop("version")
    if "server_dir" in updates and not is_safe_server_path(updates["server_dir"]):
        raise HTTPException(status_code=400, detail="Percorso cartella non consentito.")
    if "server_port" in updates:
        port_error = minecraft_port_error(updates["server_port"])
        if port_error:
            raise HTTPException(status_code=400, detail=port_error)
    res = instance_manager.update_instance(req.instance_id, updates, manager=manager)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    if "server_port" in updates:
        # La porta vale solo se è scritta anche in server.properties del server
        try:
            manager.write_port_to_properties((res.get("instance") or {}).get("server_dir", ""), int(updates["server_port"]))
        except Exception:
            pass
        request_minecraft_port_sync()
    return {"status": "success", **res}

@router.post("/api/instances/delete")
def api_delete_instance(req: DeleteInstanceRequest, request: Request):
    verify_admin_access(request)
    res = instance_manager.delete_instance(req.instance_id)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return {"status": "success", **res}
