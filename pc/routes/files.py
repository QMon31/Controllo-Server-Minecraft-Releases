"""Gestione file dal pannello."""
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import FileResponse
import file_manager
import os
from fastapi import APIRouter

from app import (
    CreateItemRequest, DeleteItemRequest, RenameItemRequest, SaveFileRequest, UnzipItemRequest, get_server_dir_path, save_upload,
    verify_admin_access,
)

router = APIRouter()


# =========================================================
# COMMERCIAL MODULE 2: WEB FILE MANAGER (DIR TREE, ZIP, UPLOAD, EDIT)
# =========================================================
@router.get("/api/files/list")
def api_files_list(path: str = "", subpath: str = "", request: Request = None):
    verify_admin_access(request)
    server_dir = get_server_dir_path()
    target_rel = path or subpath or ""
    res = file_manager.list_directory(server_dir, target_rel)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return res

@router.get("/api/files/read")
def api_files_read(path: str = "", subpath: str = "", request: Request = None):
    verify_admin_access(request)
    server_dir = get_server_dir_path()
    target_rel = path or subpath or ""
    res = file_manager.read_text_file(server_dir, target_rel)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return res

@router.post("/api/files/save")
def api_files_save(req: SaveFileRequest, request: Request):
    verify_admin_access(request)
    server_dir = get_server_dir_path()
    target_rel = req.path or req.subpath or ""
    res = file_manager.save_text_file(server_dir, target_rel, req.content)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return res

@router.post("/api/files/create")
def api_files_create(req: CreateItemRequest, request: Request):
    verify_admin_access(request)
    server_dir = get_server_dir_path()
    target_rel = req.path or req.subpath or ""
    if req.name:
        target_rel = os.path.join(target_rel, req.name).replace("\\", "/")
    res = file_manager.create_item(server_dir, target_rel, req.is_dir)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return res

@router.post("/api/files/delete")
def api_files_delete(req: DeleteItemRequest, request: Request):
    verify_admin_access(request)
    server_dir = get_server_dir_path()
    target_rel = req.path or req.subpath or ""
    res = file_manager.delete_item(server_dir, target_rel)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return res

@router.post("/api/files/rename")
def api_files_rename(req: RenameItemRequest, request: Request):
    verify_admin_access(request)
    server_dir = get_server_dir_path()
    target_rel = req.path or req.subpath or ""
    res = file_manager.rename_item(server_dir, target_rel, req.new_name)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return res

@router.post("/api/files/unzip")
def api_files_unzip(req: UnzipItemRequest, request: Request):
    verify_admin_access(request)
    server_dir = get_server_dir_path()
    target_rel = req.path or req.subpath or ""
    res = file_manager.unzip_archive(server_dir, target_rel)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return res

@router.get("/api/files/download")
def api_files_download(path: str = "", subpath: str = "", request: Request = None):
    verify_admin_access(request)
    server_dir = get_server_dir_path()
    target_rel = path or subpath or ""
    target_path = file_manager.resolve_safe_path(server_dir, target_rel)
    if not target_path or not os.path.isfile(target_path):
        raise HTTPException(status_code=404, detail="File non trovato.")
    filename = os.path.basename(target_path)
    return FileResponse(target_path, filename=filename)

@router.post("/api/files/upload")
async def api_files_upload(request: Request, filename: str, path: str = ""):
    verify_admin_access(request)
    server_dir = get_server_dir_path()
    target_dir = file_manager.resolve_safe_path(server_dir, path)
    if not target_dir or not os.path.isdir(target_dir):
        raise HTTPException(status_code=400, detail="Cartella di destinazione non valida.")
    
    clean_name = os.path.basename(filename).strip()
    if not clean_name:
        raise HTTPException(status_code=400, detail="Nome file non valido.")
    
    target_path = os.path.join(target_dir, clean_name)
    # Prima un file .part, al suo posto solo a caricamento finito: prima un caricamento interrotto lasciava un file a
    # metà (o rovinava quello che c'era con lo stesso nome)
    part_path = target_path + ".part"
    try:
        try:
            size = await save_upload(request, part_path)
            os.replace(part_path, target_path)
        finally:
            if os.path.exists(part_path):
                try:
                    os.remove(part_path)
                except OSError:
                    pass
        return {
            "status": "success",
            "filename": clean_name,
            "size": size,
            "message": f"File '{clean_name}' caricato con successo!"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore scrittura file: {e}")
