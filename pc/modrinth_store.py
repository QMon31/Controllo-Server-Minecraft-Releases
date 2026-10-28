import os
import json
import re
import time
import hashlib
import threading
import urllib.request
import urllib.parse
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

from platform_utils import download_file, write_json_atomic


def _safe_jar_name(name: Optional[str]) -> str:
    """Solo il nome del file (niente cartelle: "../config.json" diventerebbe un file fuori da mods/), e solo .jar
    o .jar.disabled. "" se il nome non va bene."""
    base = os.path.basename((name or "").replace("\\", "/"))
    low = base.lower()
    if not base or base.startswith(".") or not (low.endswith(".jar") or low.endswith(".jar.disabled")):
        return ""
    return base


def _modrinth_url_ok(url: Optional[str]) -> bool:
    """I file delle mod si scaricano solo da Modrinth (https)."""
    host = urllib.parse.urlparse(url or "").hostname or ""
    return (url or "").startswith("https://") and (host == "modrinth.com" or host.endswith(".modrinth.com"))

CACHE_STORE: Dict[str, Any] = {}
CACHE_TTL = 300  # 5 minutes cache

MOD_UPDATES_CACHE: Dict[str, Any] = {}
MOD_UPDATES_CACHE_TTL = 900  # 15 minutes cache for mod updates

USER_AGENT = "ControlloServerMinecraft/5.0.0 (admin@local)"


def calculate_file_sha1(file_path: str) -> str:
    """Calculates SHA1 hex digest of a file in 64KB chunks."""
    h = hashlib.sha1()
    try:
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def get_loader_filters(loader: str, folder: str = "mods") -> List[str]:
    """Returns the list of compatible loaders for Modrinth API filtering."""
    if folder == "plugins":
        return ["paper", "spigot", "purpur", "bukkit"]

    l_clean = (loader or "").strip().lower()
    if l_clean in ["paper", "spigot", "purpur", "bukkit", "plugin"]:
        return ["paper", "spigot", "purpur", "bukkit"]
    elif l_clean in ["fabric", "quilt"]:
        return ["fabric", "quilt"]
    elif l_clean in ["neoforge"]:
        return ["neoforge"]
    elif l_clean in ["forge"]:
        return ["forge"]
    elif l_clean in ["vanilla"]:
        return ["vanilla"]
    else:
        return [l_clean] if l_clean and l_clean not in ["all", "tutti"] else ["forge"]


def get_store_installed_file(server_dir: str) -> str:
    """Returns the path to the store installation ledger inside the server directory."""
    return os.path.join(server_dir, ".store_installed.json")


def get_installed_projects(server_dir: str) -> Dict[str, Any]:
    """
    Returns a dictionary of installed store projects keyed by project_id and slug.
    Also syncs with actual .jar files present in mods/ and plugins/.
    """
    if not server_dir or not os.path.isdir(server_dir):
        return {}

    installed: Dict[str, Any] = {}
    ledger_path = get_store_installed_file(server_dir)
    if os.path.exists(ledger_path):
        try:
            with open(ledger_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    installed = data
        except Exception:
            installed = {}

    # Verify that files in ledger still exist on disk; if not, prune
    to_delete = []
    for pid, info in installed.items():
        folder = info.get("folder", "mods")
        filename = info.get("filename", "")
        if filename:
            file_path = os.path.join(server_dir, folder, filename)
            if not os.path.isfile(file_path):
                to_delete.append(pid)
    for pid in to_delete:
        installed.pop(pid, None)

    # Also scan disk for jars to detect mods/plugins installed outside the store
    disk_jars: Dict[str, str] = {}  # lowercase_base -> (folder, actual_filename)
    for sub in ["mods", "plugins"]:
        dir_path = os.path.join(server_dir, sub)
        if os.path.isdir(dir_path):
            try:
                for fname in os.listdir(dir_path):
                    if fname.lower().endswith(".jar"):
                        clean_name = fname.lower()
                        disk_jars[clean_name] = (sub, fname)
            except Exception:
                pass

    return installed


def save_installed_projects(server_dir: str, installed: Dict[str, Any]) -> None:
    """Saves the store installation ledger."""
    if not server_dir or not os.path.isdir(server_dir):
        return
    ledger_path = get_store_installed_file(server_dir)
    try:
        write_json_atomic(ledger_path, installed)
    except Exception as e:
        print(f"[Store] Impossibile salvare l'elenco delle mod installate ({ledger_path}): {e}")


def is_project_installed_on_disk(server_dir: str, project_id: str, slug: str, title: str, installed_dict: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Checks if a project is installed by ID, slug, cached hash match, or disk filename match."""
    # 1. Direct match in ledger
    if project_id in installed_dict:
        return installed_dict[project_id]
    if slug and slug in installed_dict:
        return installed_dict[slug]

    # 2. Check if identified via Modrinth version_files cryptographic hash scan
    cached_upd = MOD_UPDATES_CACHE.get(server_dir, {}).get("data", {})
    if project_id and project_id in cached_upd.get("updates_by_project", {}):
        u = cached_upd["updates_by_project"][project_id]
        return {
            "project_id": project_id,
            "slug": slug,
            "filename": u.get("current_filename"),
            "folder": u.get("folder", "mods"),
            "installed_at": "Rilevato su disco"
        }

    # 3. Check if any file in mods/ or plugins/ matches the slug or ID
    import re
    slug_norm = (slug or "").lower().replace("-", "_").replace(" ", "_")
    title_norm = (title or "").lower().replace("-", "_").replace(" ", "_")
    slug_clean = re.sub(r"[^a-z0-9]", "", (slug or "").lower())
    title_clean = re.sub(r"[^a-z0-9]", "", (title or "").lower())
    p_id_norm = (project_id or "").lower()

    for sub in ["mods", "plugins"]:
        dir_path = os.path.join(server_dir, sub)
        if os.path.isdir(dir_path):
            try:
                for fname in os.listdir(dir_path):
                    if not (fname.lower().endswith(".jar") or fname.lower().endswith(".disabled")):
                        continue
                    fn_norm = fname.lower().replace("-", "_").replace(" ", "_")
                    fn_clean = re.sub(r"[^a-z0-9]", "", fname.lower().replace(".jar", "").replace(".disabled", ""))

                    if (slug_norm and (fn_norm.startswith(slug_norm + "_") or fn_norm.startswith(slug_norm + "-") or fn_norm == slug_norm + ".jar")) or \
                       (slug_clean and len(slug_clean) >= 3 and (fn_clean.startswith(slug_clean) or fn_clean == slug_clean)) or \
                       (p_id_norm and p_id_norm in fn_norm) or \
                       (title_norm and len(title_norm) > 4 and (fn_norm.startswith(title_norm + "_") or fn_norm.startswith(title_norm + "-"))) or \
                       (title_clean and len(title_clean) >= 4 and (fn_clean.startswith(title_clean) or fn_clean == title_clean)):
                        return {
                            "project_id": project_id,
                            "slug": slug,
                            "filename": fname,
                            "folder": sub,
                            "installed_at": "Rilevato su disco"
                        }
            except Exception:
                pass
    return None


def get_cached_or_check_updates(server_dir: str, loader: str = "forge", mc_version: str = "1.20.1", force: bool = False) -> Dict[str, Any]:
    """Retrieves cached mod updates or triggers a check if expired/forced."""
    if not server_dir or not os.path.isdir(server_dir):
        return {"success": False, "has_updates": False, "updates_count": 0, "updates": [], "updates_by_file": {}, "updates_by_project": {}}

    now = time.time()
    cache_entry = MOD_UPDATES_CACHE.get(server_dir)
    if not force and cache_entry and (now - cache_entry.get("time", 0) < MOD_UPDATES_CACHE_TTL):
        return cache_entry.get("data", {})

    return check_installed_mods_updates(server_dir, loader=loader, mc_version=mc_version, force=force)


def check_installed_mods_updates(
    server_dir: str,
    loader: str = "forge",
    mc_version: str = "1.20.1",
    force: bool = False
) -> Dict[str, Any]:
    """
    Checks all .jar files in mods/ and plugins/ against Modrinth version_files/update API.
    Detects which mods have newer versions available compatible with the server's loader and MC version.
    """
    if not loader or not mc_version:
        # Tipo o versione del server sconosciuti: nessun aggiornamento proposto (sarebbero di un altro loader)
        return {"success": True, "has_updates": False, "updates_count": 0, "updates": [],
                "updates_by_file": {}, "updates_by_project": {}}
    if not server_dir or not os.path.isdir(server_dir):
        return {
            "success": False,
            "error": "Cartella server non valida",
            "has_updates": False,
            "updates_count": 0,
            "updates": [],
            "updates_by_file": {},
            "updates_by_project": {}
        }

    from file_manager import format_file_size

    now = time.time()
    cache_entry = MOD_UPDATES_CACHE.get(server_dir)
    if not force and cache_entry and (now - cache_entry.get("time", 0) < MOD_UPDATES_CACHE_TTL):
        return cache_entry.get("data", {})

    eff_mc_version = (mc_version or "1.20.1").strip()
    updates: List[Dict[str, Any]] = []
    updates_by_file: Dict[str, Any] = {}
    updates_by_project: Dict[str, Any] = {}
    check_error = ""

    # Scan mods/ and plugins/ folders
    for sub in ["mods", "plugins"]:
        dir_path = os.path.join(server_dir, sub)
        if not os.path.isdir(dir_path):
            continue

        try:
            entries = os.listdir(dir_path)
        except Exception:
            continue

        # Map sha1 -> (filename, is_disabled)
        hash_to_file: Dict[str, Tuple[str, bool]] = {}
        for fname in entries:
            lower = fname.lower()
            is_disabled = lower.endswith(".disabled")
            if not (lower.endswith(".jar") or is_disabled):
                continue
            full_path = os.path.join(dir_path, fname)
            if not os.path.isfile(full_path):
                continue
            sha1 = calculate_file_sha1(full_path)
            if sha1:
                hash_to_file[sha1] = (fname, is_disabled)

        if not hash_to_file:
            continue

        req_loaders = get_loader_filters(loader, folder=sub)
        hashes_list = list(hash_to_file.keys())

        # Batch in chunks of 80 to prevent oversized requests
        chunk_size = 80
        for i in range(0, len(hashes_list), chunk_size):
            chunk = hashes_list[i:i + chunk_size]
            payload = {
                "hashes": chunk,
                "algorithm": "sha1",
                "loaders": req_loaders,
                "game_versions": [eff_mc_version] if eff_mc_version and eff_mc_version.lower() not in ["all", "tutte"] else []
            }
            try:
                post_data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    "https://api.modrinth.com/v2/version_files/update",
                    data=post_data,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Content-Type": "application/json"
                    }
                )
                with urllib.request.urlopen(req, timeout=12) as resp:
                    resp_data = json.loads(resp.read().decode("utf-8"))
                    if isinstance(resp_data, dict):
                        for input_h, ver_info in resp_data.items():
                            if not ver_info or not isinstance(ver_info, dict):
                                continue
                            file_info = hash_to_file.get(input_h)
                            if not file_info:
                                continue
                            curr_fname, is_disabled = file_info

                            # Find primary jar file
                            files = ver_info.get("files", [])
                            primary_file = None
                            for f in files:
                                if f.get("primary") and f.get("filename", "").lower().endswith(".jar"):
                                    primary_file = f
                                    break
                            if not primary_file:
                                for f in files:
                                    if f.get("filename", "").lower().endswith(".jar"):
                                        primary_file = f
                                        break
                            if not primary_file:
                                continue

                            new_sha1 = primary_file.get("hashes", {}).get("sha1", "")
                            # If hash differs, a newer/compatible version is available
                            if new_sha1 and new_sha1.lower() != input_h.lower():
                                new_fname = primary_file.get("filename", "")
                                if is_disabled and not new_fname.lower().endswith(".disabled"):
                                    new_fname_final = new_fname + ".disabled"
                                else:
                                    new_fname_final = new_fname

                                p_id = ver_info.get("project_id", "")
                                v_num = ver_info.get("version_number", ver_info.get("name", ""))
                                v_id = ver_info.get("id", "")
                                dl_url = primary_file.get("url", "")
                                new_size = primary_file.get("size", 0)

                                upd_item = {
                                    "current_filename": curr_fname,
                                    "new_filename": new_fname_final,
                                    "folder": sub,
                                    "project_id": p_id,
                                    "version_id": v_id,
                                    "version_number": v_num,
                                    "version_name": ver_info.get("name", ""),
                                    "download_url": dl_url,
                                    "new_sha1": new_sha1,
                                    "new_size": new_size,
                                    "new_size_formatted": format_file_size(new_size),
                                    "date_published": ver_info.get("date_published", ""),
                                    "is_disabled": is_disabled
                                }
                                updates.append(upd_item)
                                updates_by_file[curr_fname] = upd_item
                                if p_id:
                                    updates_by_project[p_id] = upd_item
            except Exception as e:
                # Senza questo, con Modrinth irraggiungibile il pannello direbbe "tutto aggiornato"
                check_error = f"Modrinth non raggiungibile: {e}"

    res_data = {
        "success": True,
        "has_updates": len(updates) > 0,
        "updates_count": len(updates),
        "updates": updates,
        "updates_by_file": updates_by_file,
        "updates_by_project": updates_by_project,
        "checked_at": datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    }
    if check_error:
        res_data["success"] = False
        res_data["error"] = check_error
        return res_data  # non in cache: al prossimo controllo si riprova
    MOD_UPDATES_CACHE[server_dir] = {
        "time": now,
        "data": res_data
    }
    return res_data


def search_modrinth(
    query: str = "",
    loader: str = "",
    version: str = "",
    category: str = "",
    project_type: str = "mod",
    sort: str = "relevance",
    limit: int = 24,
    offset: int = 0,
    server_dir: Optional[str] = None
) -> Dict[str, Any]:
    """Queries Modrinth API with caching, pagination, sorting and installation detection."""
    cache_key = f"{query}_{loader}_{version}_{category}_{project_type}_{sort}_{limit}_{offset}"
    now = time.time()
    cached_res = None
    if cache_key in CACHE_STORE:
        entry = CACHE_STORE[cache_key]
        if now - entry["time"] < CACHE_TTL:
            cached_res = entry["data"]

    if not cached_res:
        facets = []
        if project_type and project_type.lower() in ["mod", "plugin"]:
            facets.append(["project_type:mod", "project_type:plugin"])
        elif project_type and project_type.lower() not in ["all", "tutti"]:
            facets.append([f"project_type:{project_type}"])
        if loader and loader.lower() not in ["all", "tutti"]:
            facets.append([f"categories:{loader.lower()}"])
        if version and version.lower() not in ["all", "tutte"]:
            facets.append([f"versions:{version.strip()}"])
        if category and category.lower() not in ["all", "tutte"]:
            facets.append([f"categories:{category.strip().lower()}"])

        index_val = sort if sort in ["relevance", "downloads", "follows", "newest", "updated"] else "relevance"

        params = {
            "query": query.strip(),
            "limit": min(limit, 100),
            "offset": max(0, offset),
            "index": index_val
        }
        if facets:
            params["facets"] = json.dumps(facets)

        url = f"https://api.modrinth.com/v2/search?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                hits = data.get("hits", [])
                results = []
                for h in hits:
                    results.append({
                        "project_id": h.get("project_id"),
                        "slug": h.get("slug"),
                        "title": h.get("title"),
                        "description": h.get("description"),
                        "categories": h.get("categories", []),
                        "icon_url": h.get("icon_url"),
                        "downloads": h.get("downloads", 0),
                        "follows": h.get("follows", 0),
                        "author": h.get("author"),
                        "license": h.get("license"),
                        "date_modified": h.get("date_modified"),
                        "latest_version": h.get("latest_version"),
                        "client_side": h.get("client_side"),
                        "server_side": h.get("server_side"),
                        "project_type": h.get("project_type", "mod")
                    })
                cached_res = {
                    "success": True,
                    "hits": results,
                    "total_hits": data.get("total_hits", 0),
                    "limit": data.get("limit", limit),
                    "offset": data.get("offset", offset)
                }
                CACHE_STORE[cache_key] = {"time": now, "data": cached_res}
        except Exception as e:
            return {"success": False, "error": f"Errore ricerca Modrinth: {e}", "hits": [], "total_hits": 0, "limit": limit, "offset": offset}

    # Annotate with installation status and update availability if server_dir is provided
    hits_copy = [dict(h) for h in cached_res.get("hits", [])]
    if server_dir and os.path.isdir(server_dir):
        installed = get_installed_projects(server_dir)
        upd_data = get_cached_or_check_updates(server_dir, loader=loader, mc_version=version, force=False)
        upd_by_proj = upd_data.get("updates_by_project", {})
        upd_by_file = upd_data.get("updates_by_file", {})

        for h in hits_copy:
            match = is_project_installed_on_disk(server_dir, h.get("project_id", ""), h.get("slug", ""), h.get("title", ""), installed)
            if match:
                h["is_installed"] = True
                h["installed_info"] = match
                p_id = h.get("project_id", "")
                fn = match.get("filename", "")
                upd = upd_by_proj.get(p_id) or upd_by_file.get(fn)
                if upd:
                    h["update_available"] = True
                    h["update_info"] = upd
                    h["latest_version"] = upd.get("version_number", h.get("latest_version"))
                else:
                    h["update_available"] = False
            else:
                h["is_installed"] = False
                h["update_available"] = False

    return {
        "success": True,
        "hits": hits_copy,
        "total_hits": cached_res.get("total_hits", 0),
        "limit": cached_res.get("limit", limit),
        "offset": cached_res.get("offset", offset),
        "sort": sort
    }


def install_modrinth_project(
    project_id_or_slug: str,
    server_dir: str,
    loader: str = "",
    version: str = "",
    target_subdir: str = ""
) -> Dict[str, Any]:
    """Finds compatible version file, downloads .jar into server's mods or plugins folder, and registers in ledger."""
    if not server_dir or not os.path.isdir(server_dir):
        return {"success": False, "error": "Cartella del server non trovata."}

    # Decide target folder: if server_type or loader is paper/spigot/purpur -> plugins, else mods
    if not target_subdir:
        if (loader or "").lower() in ["paper", "spigot", "purpur", "bukkit", "plugin"]:
            target_subdir = "plugins"
        else:
            target_subdir = "mods"

    dest_folder = os.path.join(server_dir, target_subdir)
    os.makedirs(dest_folder, exist_ok=True)

    # 1. Fetch versions for project (stesse regole dei controlli degli aggiornamenti: Paper accetta i plugin
    # Spigot/Bukkit, Quilt le mod Fabric). Senza loader o versione si cercano tutte.
    has_loader = bool(loader) and loader.lower() not in ["all", "tutti", "vanilla"]
    has_version = bool(version) and version.lower() not in ["all", "tutte"]
    query_params = {}
    if has_loader:
        query_params["loaders"] = json.dumps(get_loader_filters(loader, folder=target_subdir))
    if has_version:
        query_params["game_versions"] = json.dumps([version.strip()])

    q_str = f"?{urllib.parse.urlencode(query_params)}" if query_params else ""
    url = f"https://api.modrinth.com/v2/project/{urllib.parse.quote(project_id_or_slug)}/version{q_str}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            v_list = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"success": False, "error": f"Errore recupero versioni da Modrinth: {e}"}

    # Nessuna versione per questo server: si dice, senza scaricare quella più recente qualunque (prima finiva nella
    # cartella una mod per un'altra versione di Minecraft o un altro loader, che può far crashare il server)
    if (not v_list or not isinstance(v_list, list)) and query_params:
        what = " ".join(x for x in (loader if has_loader else "", version if has_version else "") if x)
        return {"success": False, "error": f"Nessuna versione di questo progetto è compatibile con il server ({what})."}

    if not v_list:
        return {"success": False, "error": "Nessuna versione disponibile per questo progetto."}

    chosen_file = None
    version_name = ""
    for v in v_list:
        files = v.get("files", [])
        for f in files:
            fname = f.get("filename", "")
            if fname.lower().endswith(".jar"):
                chosen_file = f
                version_name = v.get("version_number", v.get("name", ""))
                break
        if chosen_file:
            break

    if not chosen_file:
        return {"success": False, "error": "Nessun file .jar trovato nella release di questo progetto."}

    dl_url = chosen_file.get("url")
    filename = _safe_jar_name(chosen_file.get("filename"))
    if not filename or not _modrinth_url_ok(dl_url):
        return {"success": False, "error": "File della mod non valido nella risposta di Modrinth."}
    target_file_path = os.path.join(dest_folder, filename)
    file_hashes = chosen_file.get("hashes") or {}

    try:
        # Prima <file>.part, poi controllo dell'impronta e del .jar: un download interrotto non lascia più un .jar
        # rotto nella cartella mods (il server poi non partiva)
        download_file(dl_url, target_file_path, USER_AGENT, timeout=30,
                      hashes={"sha512": file_hashes.get("sha512"), "sha1": file_hashes.get("sha1")})

        file_size = os.path.getsize(target_file_path)
        from file_manager import format_file_size

        # Register in installed ledger
        installed = get_installed_projects(server_dir)
        record = {
            "project_id": project_id_or_slug,
            "filename": filename,
            "version": version_name,
            "folder": target_subdir,
            "size": file_size,
            "size_formatted": format_file_size(file_size),
            "installed_at": datetime.now().strftime("%d/%m/%Y %H:%M")
        }
        installed[project_id_or_slug] = record
        save_installed_projects(server_dir, installed)

        # Clear search cache so updated hits show is_installed = True
        CACHE_STORE.clear()

        return {
            "success": True,
            "filename": filename,
            "version": version_name,
            "size": file_size,
            "size_formatted": format_file_size(file_size),
            "dest_folder": f"{target_subdir}/",
            "path": target_file_path,
            "installed_info": record,
            "message": f"'{filename}' ({format_file_size(file_size)}) installato con successo nella cartella {target_subdir}/!"
        }
    except Exception as e:
        return {"success": False, "error": f"Errore durante il download del file: {e}"}


def uninstall_modrinth_project(
    project_id_or_slug: str,
    server_dir: str,
    filename: Optional[str] = None
) -> Dict[str, Any]:
    """Uninstalls a mod or plugin by deleting its .jar file from mods/ or plugins/ and removing from ledger."""
    if not server_dir or not os.path.isdir(server_dir):
        return {"success": False, "error": "Cartella del server non trovata."}

    installed = get_installed_projects(server_dir)
    # Solo il nome del file dentro mods/ o plugins/: prima "../" nel nome faceva cancellare file fuori dalla cartella
    target_filename = _safe_jar_name(filename) if filename else ""
    if filename and not target_filename:
        return {"success": False, "error": "Nome del file della mod non valido."}
    target_folder = ""

    # Check ledger
    record = installed.get(project_id_or_slug)
    if record:
        target_filename = _safe_jar_name(record.get("filename")) or target_filename
        target_folder = record.get("folder") if record.get("folder") in ("mods", "plugins") else "mods"

    deleted = False

    # If filename is known, look in target folder or both mods/ and plugins/. Anche disattivata dalla lista delle
    # mod (.jar.disabled): prima non si trovava e si passava alla ricerca per nome, che cancellava un'altra mod
    if target_filename:
        base = target_filename[:-len(".disabled")] if target_filename.lower().endswith(".disabled") else target_filename
        for folder in ([target_folder] if target_folder else ["mods", "plugins"]):
            for name in (target_filename, base, base + ".disabled"):
                candidate = os.path.join(server_dir, folder, name)
                if os.path.isfile(candidate):
                    try:
                        os.remove(candidate)
                        deleted = True
                        target_filename = name
                        break
                    except Exception as e:
                        return {"success": False, "error": f"Impossibile eliminare il file '{name}': {e}"}
            if deleted:
                break

    # Se il file non si trova, si cerca per nome del progetto ("sodium-0.6.jar", "sodium-fabric-0.6.jar"), e si
    # cancella solo se il file è uno solo. Prima bastava che il nome contenesse il progetto: disinstallando "create"
    # si cancellava "create_enchantment_industry"
    if not deleted:
        slug_norm = project_id_or_slug.lower().replace("-", "_").replace(" ", "_")
        pattern = re.compile(re.escape(slug_norm) + r"(_(fabric|forge|neoforge|quilt|paper|bukkit|spigot))?"
                             r"(_(v|mc)?\d.*)?\.jar(\.disabled)?$")
        matches = []
        for folder in ["mods", "plugins"]:
            dir_path = os.path.join(server_dir, folder)
            if os.path.isdir(dir_path):
                for fname in os.listdir(dir_path):
                    if pattern.match(fname.lower().replace("-", "_").replace(" ", "_")):
                        matches.append((dir_path, fname))
        if len(matches) == 1:
            dir_path, fname = matches[0]
            try:
                os.remove(os.path.join(dir_path, fname))
                deleted = True
                target_filename = fname
            except Exception as e:
                return {"success": False, "error": f"Impossibile eliminare il file '{fname}': {e}"}

    # Clean from ledger
    installed.pop(project_id_or_slug, None)
    # Also clean any matching filename
    to_pop = [k for k, v in installed.items() if v.get("filename") == target_filename]
    for k in to_pop:
        installed.pop(k, None)
    save_installed_projects(server_dir, installed)

    # Invalidate search cache
    CACHE_STORE.clear()

    if not deleted:
        return {"success": False, "error": f"Nessun file installato trovato sul server per '{project_id_or_slug}'."}

    return {
        "success": True,
        "filename": target_filename,
        "message": f"'{target_filename}' disinstallato con successo dal server!"
    }


def list_server_installed_mods(
    server_dir: str,
    loader: str = "forge",
    mc_version: str = "1.20.1"
) -> Dict[str, Any]:
    """Returns list of all mods and plugins currently installed in the server, with update status."""
    if not server_dir or not os.path.isdir(server_dir):
        return {"success": False, "error": "Cartella non trovata", "items": [], "count": 0, "has_updates": False, "updates_count": 0}

    from file_manager import format_file_size, safe_format_mtime
    installed_ledger = get_installed_projects(server_dir)
    upd_data = get_cached_or_check_updates(server_dir, loader=loader, mc_version=mc_version, force=False)
    upd_by_file = upd_data.get("updates_by_file", {})
    upd_by_proj = upd_data.get("updates_by_project", {})

    items = []
    updates_count = 0

    for folder in ["mods", "plugins"]:
        dir_path = os.path.join(server_dir, folder)
        if os.path.isdir(dir_path):
            try:
                for fname in sorted(os.listdir(dir_path)):
                    if not fname.lower().endswith(".jar"):
                        continue
                    full_p = os.path.join(dir_path, fname)
                    try:
                        stat = os.stat(full_p)
                        size = stat.st_size
                        mtime = safe_format_mtime(stat.st_mtime)

                        matched_project_id = ""
                        matched_version = ""
                        for pid, rec in installed_ledger.items():
                            if rec.get("filename") == fname:
                                matched_project_id = pid
                                matched_version = rec.get("version", "")
                                break

                        upd = upd_by_file.get(fname) or (upd_by_proj.get(matched_project_id) if matched_project_id else None)
                        has_upd = bool(upd)
                        if has_upd:
                            updates_count += 1

                        items.append({
                            "filename": fname,
                            "folder": folder,
                            "size": size,
                            "size_formatted": format_file_size(size),
                            "modified": mtime,
                            "project_id": matched_project_id,
                            "version": matched_version,
                            "update_available": has_upd,
                            "update_info": upd
                        })
                    except Exception:
                        continue
            except Exception:
                pass

    return {
        "success": True,
        "items": items,
        "count": len(items),
        "has_updates": updates_count > 0,
        "updates_count": updates_count
    }


def update_mod_file(
    server_dir: str,
    current_filename: str,
    folder: str = "mods",
    download_url: Optional[str] = None,
    new_filename: Optional[str] = None,
    project_id: Optional[str] = None,
    version_number: Optional[str] = None,
    loader: str = "forge",
    mc_version: str = "1.20.1"
) -> Dict[str, Any]:
    """
    Downloads the updated .jar file, safely removes previous version,
    and updates the store ledger.
    """
    if not server_dir or not os.path.isdir(server_dir):
        return {"success": False, "error": "Cartella del server non trovata."}

    if folder not in ["mods", "plugins"]:
        folder = "mods"

    dest_folder = os.path.join(server_dir, folder)
    os.makedirs(dest_folder, exist_ok=True)

    # If download_url or new_filename is missing, check cached updates or fetch
    if not download_url or not new_filename:
        cached = MOD_UPDATES_CACHE.get(server_dir, {}).get("data", {})
        upd = cached.get("updates_by_file", {}).get(current_filename) or (cached.get("updates_by_project", {}).get(project_id) if project_id else None)
        if not upd:
            fresh = check_installed_mods_updates(server_dir, loader=loader, mc_version=mc_version, force=True)
            upd = fresh.get("updates_by_file", {}).get(current_filename) or (fresh.get("updates_by_project", {}).get(project_id) if project_id else None)

        if upd:
            download_url = download_url or upd.get("download_url")
            new_filename = new_filename or upd.get("new_filename")
            project_id = project_id or upd.get("project_id")
            version_number = version_number or upd.get("version_number")

    if not download_url or not new_filename:
        return {"success": False, "error": f"Nessun aggiornamento disponibile o URL non trovato per '{current_filename}'."}

    current_filename = _safe_jar_name(current_filename)
    new_filename = _safe_jar_name(new_filename)
    if not current_filename or not new_filename or not _modrinth_url_ok(download_url):
        return {"success": False, "error": "Nome del file o indirizzo dell'aggiornamento non valido."}

    old_file_path = os.path.join(dest_folder, current_filename)
    new_file_path = os.path.join(dest_folder, new_filename)
    # Il file temporaneo non finisce in .jar: se il PC si spegne a metà, Forge non prova a caricarlo come mod
    tmp_file_path = os.path.join(dest_folder, f".tmp_update_{new_filename}.download")
    expected_sha1 = None
    cached = MOD_UPDATES_CACHE.get(server_dir, {}).get("data", {})
    upd_known = cached.get("updates_by_file", {}).get(current_filename)
    if upd_known and upd_known.get("download_url") == download_url:
        expected_sha1 = upd_known.get("new_sha1")

    # 1. Download to temporary file
    try:
        download_file(download_url, tmp_file_path, USER_AGENT, timeout=35, hashes={"sha1": expected_sha1},
                      require_zip=True)
        new_file_size = os.path.getsize(tmp_file_path)
    except Exception as e:
        if os.path.exists(tmp_file_path):
            try:
                os.remove(tmp_file_path)
            except Exception:
                pass
        return {"success": False, "error": f"Errore durante il download dell'aggiornamento: {e}"}

    # 2. Check file lock / delete old file
    if os.path.isfile(old_file_path):
        try:
            os.remove(old_file_path)
        except Exception as e:
            if os.path.exists(tmp_file_path):
                try:
                    os.remove(tmp_file_path)
                except Exception:
                    pass
            err_msg = str(e)
            if "used by another process" in err_msg.lower() or "[WinError 32]" in err_msg or isinstance(e, PermissionError):
                return {
                    "success": False,
                    "error": "Impossibile aggiornare la mod: il file è bloccato dal processo Java del server Minecraft in esecuzione. Arresta il server prima di aggiornare le mod."
                }
            return {"success": False, "error": f"Errore eliminazione versione precedente '{current_filename}': {e}"}

    # 3. Rename tmp to new file
    try:
        if os.path.exists(new_file_path):
            try:
                os.remove(new_file_path)
            except Exception:
                pass
        os.replace(tmp_file_path, new_file_path)
    except Exception as e:
        return {"success": False, "error": f"Errore posizionamento file aggiornato '{new_filename}': {e}"}

    from file_manager import format_file_size

    # 4. Update ledger
    installed = get_installed_projects(server_dir)
    ledger_key = project_id or current_filename
    installed[ledger_key] = {
        "project_id": project_id or ledger_key,
        "filename": new_filename,
        "version": version_number or "",
        "folder": folder,
        "size": new_file_size,
        "size_formatted": format_file_size(new_file_size),
        "installed_at": datetime.now().strftime("%d/%m/%Y %H:%M")
    }
    # Clean previous record if under filename
    if current_filename != new_filename:
        for k in list(installed.keys()):
            if installed[k].get("filename") == current_filename:
                installed.pop(k, None)
    save_installed_projects(server_dir, installed)

    # 5. Invalidate caches
    CACHE_STORE.clear()
    if server_dir in MOD_UPDATES_CACHE:
        c_data = MOD_UPDATES_CACHE[server_dir].get("data", {})
        c_upd = c_data.get("updates", [])
        c_data["updates"] = [u for u in c_upd if u.get("current_filename") != current_filename]
        c_data["updates_count"] = len(c_data["updates"])
        c_data["has_updates"] = c_data["updates_count"] > 0
        c_data.get("updates_by_file", {}).pop(current_filename, None)
        if project_id:
            c_data.get("updates_by_project", {}).pop(project_id, None)

    return {
        "success": True,
        "old_filename": current_filename,
        "new_filename": new_filename,
        "version": version_number,
        "size_formatted": format_file_size(new_file_size),
        "message": f"Mod aggiornata con successo: '{current_filename}' ➔ '{new_filename}' ({format_file_size(new_file_size)})"
    }


def update_all_mods(
    server_dir: str,
    loader: str = "forge",
    mc_version: str = "1.20.1"
) -> Dict[str, Any]:
    """Updates all outdated mods and plugins sequentially."""
    if not server_dir or not os.path.isdir(server_dir):
        return {"success": False, "error": "Cartella del server non trovata."}

    check_res = check_installed_mods_updates(server_dir, loader=loader, mc_version=mc_version, force=True)
    if not check_res.get("success", True):
        return {"success": False, "error": check_res.get("error", "Controllo aggiornamenti non riuscito.")}
    updates_list = check_res.get("updates", [])
    if not updates_list:
        return {
            "success": True,
            "message": "Tutte le mod e i plugin sono già aggiornati all'ultima versione disponibile!",
            "updated_count": 0,
            "total_count": 0,
            "updated": [],
            "errors": []
        }

    updated: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for item in updates_list:
        res = update_mod_file(
            server_dir=server_dir,
            current_filename=item.get("current_filename", ""),
            folder=item.get("folder", "mods"),
            download_url=item.get("download_url"),
            new_filename=item.get("new_filename"),
            project_id=item.get("project_id"),
            version_number=item.get("version_number"),
            loader=loader,
            mc_version=mc_version
        )
        if res.get("success"):
            updated.append(res)
        else:
            errors.append({
                "filename": item.get("current_filename"),
                "error": res.get("error")
            })

    # Clear cache so next check is completely fresh
    if server_dir in MOD_UPDATES_CACHE:
        del MOD_UPDATES_CACHE[server_dir]

    return {
        "success": True,
        "updated_count": len(updated),
        "total_count": len(updates_list),
        "updated": updated,
        "errors": errors,
        "message": f"Aggiornamento completato: {len(updated)} mod aggiornate su {len(updates_list)} totali." + (f" ({len(errors)} errori)" if errors else "")
    }


class ModUpdatesWatcher:
    """Background continuous watcher for installed mod updates across all servers."""
    def __init__(self, get_server_dir_cb, get_server_info_cb, interval_seconds=900, callback=None):
        self.get_server_dir = get_server_dir_cb
        self.get_server_info = get_server_info_cb
        self.interval = interval_seconds
        self.callback = callback
        self.stop_event = threading.Event()
        self.thread = None
        self.last_hash = ""

    def run(self):
        time.sleep(15)  # Initial delay after server boot
        while not self.stop_event.is_set():
            try:
                s_dir = self.get_server_dir()
                info = self.get_server_info()
                loader = info.get("server_type", "")
                version = info.get("mc_version", "")
                if s_dir and os.path.isdir(s_dir) and loader and version:
                    res = check_installed_mods_updates(s_dir, loader=loader, mc_version=version, force=True)
                    if not res.get("success", True):
                        raise RuntimeError(res.get("error"))  # rete assente: non azzerare i badge
                    updates = res.get("updates", [])
                    curr_hash = ",".join(sorted([f"{u.get('current_filename')}:{u.get('new_filename')}" for u in updates]))
                    if curr_hash != self.last_hash:
                        self.last_hash = curr_hash
                        if self.callback:
                            self.callback(res)
            except Exception:
                pass
            self.stop_event.wait(self.interval)

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self.run, daemon=True, name="ModUpdatesWatcherThread")
        self.thread.start()

    def stop(self):
        self.stop_event.set()


_global_mod_watcher: Optional[ModUpdatesWatcher] = None


def start_mod_updates_watcher(get_server_dir_cb, get_server_info_cb, interval_seconds=900, callback=None):
    global _global_mod_watcher
    if _global_mod_watcher:
        _global_mod_watcher.stop()
    _global_mod_watcher = ModUpdatesWatcher(get_server_dir_cb, get_server_info_cb, interval_seconds=interval_seconds, callback=callback)
    _global_mod_watcher.start()


def stop_mod_updates_watcher():
    global _global_mod_watcher
    if _global_mod_watcher:
        _global_mod_watcher.stop()
        _global_mod_watcher = None

