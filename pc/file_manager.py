import os
import shutil
import zipfile
from typing import Dict, Any, Optional
from datetime import datetime


def format_file_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def safe_format_mtime(timestamp: float) -> str:
    try:
        if timestamp <= 0:
            return "--"
        return datetime.fromtimestamp(timestamp).strftime("%d/%m/%Y %H:%M")
    except Exception:
        return "--"


def resolve_safe_path(base_dir: str, rel_path: str = "") -> Optional[str]:
    """Resolves a relative path within base_dir and ensures it cannot escape base_dir."""
    if not base_dir or not os.path.isdir(base_dir):
        return None

    norm_base = os.path.abspath(base_dir)
    clean_rel = rel_path.strip().lstrip("/\\")
    full_path = os.path.abspath(os.path.join(norm_base, clean_rel))

    try:
        common = os.path.commonpath([norm_base, full_path])
        if common != norm_base:
            return None
    except Exception:
        return None

    return full_path


def list_directory(base_dir: str, rel_path: str = "") -> Dict[str, Any]:
    if not base_dir:
        return {"success": False, "error": "Nessun server collegato: scegli o crea un server per vederne i file.", "items": []}
    target_dir = resolve_safe_path(base_dir, rel_path)
    if not target_dir or not os.path.isdir(target_dir):
        return {"success": False, "error": "Cartella non trovata o non valida.", "items": []}

    norm_base = os.path.abspath(base_dir)
    curr_rel = os.path.relpath(target_dir, norm_base).replace("\\", "/")
    if curr_rel == ".":
        curr_rel = ""

    # Build breadcrumbs
    parts = [p for p in curr_rel.split("/") if p]
    breadcrumbs = [{"name": "Server Root", "path": ""}]
    acc = ""
    for p in parts:
        acc = f"{acc}/{p}" if acc else p
        breadcrumbs.append({"name": p, "path": acc})

    entries = []
    try:
        with os.scandir(target_dir) as it:
            for entry in it:
                name = entry.name
                if name.startswith(".") and name != ".bak":
                    continue
                try:
                    stat = entry.stat()
                    is_dir = entry.is_dir()
                    size = stat.st_size if not is_dir else 0
                    mtime = safe_format_mtime(stat.st_mtime)
                    ext = os.path.splitext(name)[1].lower() if not is_dir else ""
                    item_rel = f"{curr_rel}/{name}".lstrip("/")
                    entries.append({
                        "name": name,
                        "rel_path": item_rel,
                        "is_dir": is_dir,
                        "size": size,
                        "size_formatted": format_file_size(size) if not is_dir else "--",
                        "modified": mtime,
                        "ext": ext,
                        "is_editable": ext in [".txt", ".json", ".toml", ".yml", ".yaml", ".properties", ".cfg", ".log", ".bat", ".sh", ".md", ".csv"],
                        "is_zip": ext == ".zip"
                    })
                except Exception:
                    continue
    except Exception as e:
        return {"success": False, "error": f"Errore lettura cartella: {e}", "items": []}

    # Sort: folders first, then alphabetically
    entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))

    return {
        "success": True,
        "current_path": curr_rel,
        "parent_path": "/".join(parts[:-1]) if parts else None,
        "breadcrumbs": breadcrumbs,
        "items": entries,
        "files": entries,
        "count": len(entries)
    }


def read_text_file(base_dir: str, rel_path: str, max_size_mb: int = 5) -> Dict[str, Any]:
    file_path = resolve_safe_path(base_dir, rel_path)
    if not file_path or not os.path.isfile(file_path):
        return {"success": False, "error": "File non trovato."}

    size = os.path.getsize(file_path)
    if size > max_size_mb * 1024 * 1024:
        return {"success": False, "error": f"File troppo grande per l'editor web ({format_file_size(size)}, max {max_size_mb} MB)."}

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return {
            "success": True,
            "rel_path": rel_path,
            "filename": os.path.basename(file_path),
            "size": size,
            "size_formatted": format_file_size(size),
            "content": content
        }
    except Exception as e:
        return {"success": False, "error": f"Errore lettura file: {e}"}


def save_text_file(base_dir: str, rel_path: str, content: str) -> Dict[str, Any]:
    file_path = resolve_safe_path(base_dir, rel_path)
    if not file_path:
        return {"success": False, "error": "Percorso file non valido."}

    try:
        # Create safety backup .bak if file already exists
        bak_ok = False
        if os.path.isfile(file_path):
            try:
                shutil.copy2(file_path, file_path + ".bak")
                bak_ok = True
            except Exception:
                pass

        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

        msg = "File salvato con successo (creato backup .bak)!" if bak_ok else "File salvato con successo (backup .bak non creato)."
        return {"success": True, "message": msg}
    except Exception as e:
        return {"success": False, "error": f"Errore salvataggio: {e}"}


def create_item(base_dir: str, rel_path: str, is_dir: bool = False) -> Dict[str, Any]:
    target_path = resolve_safe_path(base_dir, rel_path)
    if not target_path:
        return {"success": False, "error": "Percorso non consentito."}

    if os.path.exists(target_path):
        return {"success": False, "error": "Elemento già esistente con questo nome."}

    try:
        if is_dir:
            os.makedirs(target_path, exist_ok=True)
        else:
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            with open(target_path, "w", encoding="utf-8") as f:
                f.write("")
        return {"success": True, "message": f"{'Cartella creata' if is_dir else 'File creato'} con successo!"}
    except Exception as e:
        return {"success": False, "error": f"Errore creazione: {e}"}


def delete_item(base_dir: str, rel_path: str) -> Dict[str, Any]:
    target_path = resolve_safe_path(base_dir, rel_path)
    if not target_path or not os.path.exists(target_path):
        return {"success": False, "error": "Elemento non trovato."}

    norm_base = os.path.abspath(base_dir)
    if os.path.abspath(target_path) == norm_base:
        return {"success": False, "error": "Impossibile eliminare la cartella principale del server."}

    try:
        if os.path.isdir(target_path):
            shutil.rmtree(target_path)
        else:
            os.remove(target_path)
        return {"success": True, "message": "Elemento eliminato con successo!"}
    except Exception as e:
        return {"success": False, "error": f"Errore eliminazione: {e}"}


def rename_item(base_dir: str, old_rel_path: str, new_name: str) -> Dict[str, Any]:
    old_path = resolve_safe_path(base_dir, old_rel_path)
    if not old_path or not os.path.exists(old_path):
        return {"success": False, "error": "File o cartella di origine non trovata."}

    clean_new_name = os.path.basename(new_name.strip())
    if not clean_new_name or "/" in clean_new_name or "\\" in clean_new_name:
        return {"success": False, "error": "Nome non valido."}

    new_path = os.path.join(os.path.dirname(old_path), clean_new_name)
    if os.path.exists(new_path):
        return {"success": False, "error": "Esiste già un file o cartella con questo nome."}

    try:
        os.rename(old_path, new_path)
        return {"success": True, "message": "Rinominato con successo!"}
    except Exception as e:
        return {"success": False, "error": f"Errore rinomina: {e}"}


def unzip_archive(base_dir: str, rel_path: str) -> Dict[str, Any]:
    zip_path = resolve_safe_path(base_dir, rel_path)
    if not zip_path or not os.path.isfile(zip_path) or not zip_path.lower().endswith(".zip"):
        return {"success": False, "error": "File ZIP non valido o non trovato."}

    dest_dir = os.path.dirname(zip_path)
    norm_base = os.path.abspath(base_dir)

    try:
        extracted_count = 0
        with zipfile.ZipFile(zip_path, "r") as z:
            for member in z.infolist():
                # Prevent ZipSlip vulnerability
                target = os.path.abspath(os.path.join(dest_dir, member.filename))
                if os.path.commonpath([norm_base, target]) != norm_base:
                    continue
                z.extract(member, dest_dir)
                extracted_count += 1
        return {"success": True, "message": f"Archivio ZIP estratto con successo ({extracted_count} elementi estratti)!"}
    except Exception as e:
        return {"success": False, "error": f"Errore estrazione ZIP: {e}"}
