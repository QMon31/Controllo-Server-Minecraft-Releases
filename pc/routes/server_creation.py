"""Crea nuovo server (Forge, NeoForge, Fabric, Paper, Vanilla)."""
from fastapi import HTTPException
from fastapi import Request
import asyncio
import os
from fastapi import APIRouter

import app as core  # loop cambia mentre il programma gira: si legge al momento dell'uso
from app import (
    ForgeInstallRequest, _async_broadcast, active_websockets, forge_installer, is_safe_server_path, manager, open_directory_in_explorer, verify_admin_access, verify_host_only_access,
)

router = APIRouter()


# =========================================================
# FORGE AUTOMATED SERVER INSTALLER ENDPOINTS
# =========================================================
@router.get("/api/forge/prerequisites")
def api_forge_prerequisites(request: Request, force: bool = False):
    verify_admin_access(request)
    verify_host_only_access(request)
    return forge_installer.check_prerequisites(force_refresh=force)

@router.post("/api/forge/refresh_versions")
def api_forge_refresh_versions(request: Request):
    verify_admin_access(request)
    verify_host_only_access(request)
    res = forge_installer.check_for_new_versions(force_refresh=True)
    if core.loop and active_websockets:
        asyncio.run_coroutine_threadsafe(
            _async_broadcast({
                "type": "versions_updated",
                "data": res
            }),
            core.loop
        )
    return res

@router.post("/api/forge/install")
def api_forge_install(req: ForgeInstallRequest, request: Request):
    verify_admin_access(request)
    verify_host_only_access(request)
    # Cartella vuota: il server finirebbe nella cartella del programma, mescolato ai suoi file
    if not (req.target_dir or "").strip():
        raise HTTPException(status_code=400, detail="Scegli la cartella in cui installare il server.")
    if not is_safe_server_path(req.target_dir):
        raise HTTPException(status_code=400, detail="Percorso di installazione non consentito per motivi di sicurezza.")
    # Prima si controllava solo ONLINE: durante avvio o spegnimento il nuovo server prendeva il posto di quello acceso
    if manager.state not in ("STOPPED", "CRASHED"):
        raise HTTPException(status_code=409, detail="Il server Minecraft è attualmente in esecuzione. Spegni il server attivo prima di procedere con una nuova installazione.")
    if core.backup_mgr.is_running:
        raise HTTPException(status_code=409, detail="Attendi la fine del backup o del ripristino in corso.")
    success = forge_installer.start_installation(
        target_dir=req.target_dir,
        server_type=req.server_type,
        mc_version=req.mc_version,
        ram_gb=req.ram_gb,
        port=req.port,
        server_manager=manager
    )
    if not success:
        raise HTTPException(status_code=409, detail="Installazione server già in corso.")
    return {"status": "started", "success": True, "target_dir": req.target_dir, "server_type": req.server_type, "mc_version": req.mc_version}

@router.post("/api/forge/open_dir")
def api_forge_open_dir(request: Request):
    verify_admin_access(request)
    verify_host_only_access(request)
    target = forge_installer.target_dir
    if not target or not os.path.isdir(target):
        raise HTTPException(status_code=404, detail="Cartella di installazione non trovata.")
    open_directory_in_explorer(target)
    return {"status": "success", "folder": target}

@router.get("/api/forge/status")
def api_forge_status(request: Request):
    verify_admin_access(request)
    return forge_installer.get_status()
