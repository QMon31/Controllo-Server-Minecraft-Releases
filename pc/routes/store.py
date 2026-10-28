"""Store mod e plugin (Modrinth) e aggiornamenti delle mod."""
from fastapi import HTTPException
from fastapi import Request
import instance_manager
import mod_info
import modrinth_store
from fastapi import APIRouter

from app import (
    InstallStoreRequest, UninstallStoreRequest, UpdateStoreRequest, get_server_dir_path, manager, verify_admin_access,
)

router = APIRouter()

# Stati in cui il server tiene aperti i file .jar delle mod
SERVER_RUNNING_STATES = ("ONLINE", "STARTING", "STOPPING", "RESTARTING")


# =========================================================
# COMMERCIAL MODULE 3: MODRINTH APP STORE
# =========================================================
@router.get("/api/store/search")
def api_store_search(q: str = "", query: str = "", loader: str = "", version: str = "", game_version: str = "", category: str = "", project_type: str = "mod", sort: str = "relevance", limit: int = 24, offset: int = 0, request: Request = None):
    verify_admin_access(request)
    server_dir = get_server_dir_path()
    active_inst = instance_manager.get_active_instance()
    eff_query = query or q or ""
    eff_loader = loader or active_inst.get("server_type", "")
    eff_version = game_version or version or active_inst.get("mc_version", "")
    res = modrinth_store.search_modrinth(
        query=eff_query,
        loader=eff_loader,
        version=eff_version,
        category=category,
        project_type=project_type,
        sort=sort,
        limit=limit,
        offset=offset,
        server_dir=server_dir
    )
    return res

@router.post("/api/store/install")
def api_store_install(req: InstallStoreRequest, request: Request):
    verify_admin_access(request)
    server_dir = get_server_dir_path()
    active_inst = instance_manager.get_active_instance()
    eff_loader = req.loader or active_inst.get("server_type", "")
    eff_version = req.game_version or req.version or active_inst.get("mc_version", "")
    res = modrinth_store.install_modrinth_project(
        project_id_or_slug=req.project_id,
        server_dir=server_dir,
        loader=eff_loader,
        version=eff_version,
        target_subdir=req.target_subdir or ""
    )
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return res

@router.post("/api/store/uninstall")
def api_store_uninstall(req: UninstallStoreRequest, request: Request):
    verify_admin_access(request)
    server_dir = get_server_dir_path()
    res = modrinth_store.uninstall_modrinth_project(
        project_id_or_slug=req.project_id,
        server_dir=server_dir,
        filename=req.filename
    )
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return res

@router.get("/api/store/project")
def api_store_project(project_id: str, request: Request):
    """Scheda di una mod o plugin dello Store: descrizione completa, autori, versioni e link di Modrinth."""
    verify_admin_access(request)
    try:
        return {"status": "success", **mod_info.get_modrinth_project(project_id)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception:
        raise HTTPException(status_code=502, detail="Modrinth non risponde: riprova tra poco.")

@router.get("/api/store/installed")
def api_store_installed(request: Request = None):
    verify_admin_access(request)
    server_dir = get_server_dir_path()
    active_inst = instance_manager.get_active_instance()
    eff_loader = active_inst.get("server_type", "")
    eff_version = active_inst.get("mc_version", "")
    res = modrinth_store.list_server_installed_mods(
        server_dir=server_dir,
        loader=eff_loader,
        mc_version=eff_version
    )
    return res

@router.get("/api/store/updates")
def api_store_updates(force: bool = False, request: Request = None):
    verify_admin_access(request)
    server_dir = get_server_dir_path()
    active_inst = instance_manager.get_active_instance()
    eff_loader = active_inst.get("server_type", "")
    eff_version = active_inst.get("mc_version", "")
    res = modrinth_store.check_installed_mods_updates(
        server_dir=server_dir,
        loader=eff_loader,
        mc_version=eff_version,
        force=force
    )
    return res

@router.post("/api/store/update")
def api_store_update(req: UpdateStoreRequest, request: Request):
    verify_admin_access(request)
    server_dir = get_server_dir_path()
    active_inst = instance_manager.get_active_instance()
    eff_loader = active_inst.get("server_type", "")
    eff_version = active_inst.get("mc_version", "")

    # If server is running, block mod file replacement to prevent WinError 32
    # (prima chiamava una funzione inesistente e l'aggiornamento finiva sempre con l'errore 500)
    if manager.state in SERVER_RUNNING_STATES:
        raise HTTPException(
            status_code=409,
            detail="Impossibile aggiornare la mod: il server Minecraft è in esecuzione e blocca i file .jar. Spegni il server prima di procedere."
        )

    res = modrinth_store.update_mod_file(
        server_dir=server_dir,
        current_filename=req.current_filename or "",
        folder=req.folder or "mods",
        download_url=req.download_url,
        new_filename=req.new_filename,
        project_id=req.project_id,
        version_number=req.version_number,
        loader=eff_loader,
        mc_version=eff_version
    )
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return res

@router.post("/api/store/update_all")
def api_store_update_all(request: Request):
    verify_admin_access(request)
    server_dir = get_server_dir_path()
    active_inst = instance_manager.get_active_instance()
    eff_loader = active_inst.get("server_type", "")
    eff_version = active_inst.get("mc_version", "")

    if manager.state in SERVER_RUNNING_STATES:
        raise HTTPException(
            status_code=409,
            detail="Impossibile aggiornare le mod in blocco: il server Minecraft è in esecuzione e blocca i file .jar. Spegni il server prima di procedere."
        )

    res = modrinth_store.update_all_mods(
        server_dir=server_dir,
        loader=eff_loader,
        mc_version=eff_version
    )
    return res
