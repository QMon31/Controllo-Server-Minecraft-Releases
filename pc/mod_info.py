"""Informazioni di una mod lette dal suo file .jar (scheda della mod nel pannello).

Formati letti: META-INF/neoforge.mods.toml (NeoForge), META-INF/mods.toml (Forge), fabric.mod.json (Fabric),
quilt.mod.json (Quilt), mcmod.info (Forge vecchio), plugin.yml / paper-plugin.yml (plugin Bukkit/Paper).
Se la mod è su Modrinth si aggiungono pagina, download, categorie e lati (client/server) con una ricerca per SHA-1.
"""

import base64
import json
import os
import re
import time
import tomllib
import urllib.error
import urllib.request
import zipfile
from typing import Any, Dict, List, Optional

import modrinth_store

MAX_ICON_BYTES = 256 * 1024
_MODRINTH_CACHE: Dict[str, Any] = {}
_MODRINTH_TTL = 3600


def _text(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


def _people(v: Any) -> List[str]:
    """Autori come elenco: stringa "a, b", lista di stringhe o di oggetti {"name": ...} (Fabric)."""
    if isinstance(v, str):
        return [p.strip() for p in re.split(r",|\band\b", v) if p.strip()]
    out = []
    if isinstance(v, list):
        for p in v:
            name = p.get("name", "") if isinstance(p, dict) else p
            if isinstance(name, str) and name.strip():
                out.append(name.strip())
    elif isinstance(v, dict):
        out = [k for k in v.keys() if isinstance(k, str)]
    return out


def _manifest_version(z: zipfile.ZipFile) -> str:
    """Versione da META-INF/MANIFEST.MF: la usa Forge quando mods.toml dice ${file.jarVersion}."""
    try:
        for line in z.read("META-INF/MANIFEST.MF").decode("utf-8", "ignore").splitlines():
            if line.lower().startswith("implementation-version:"):
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return ""


def _icon_data_uri(z: zipfile.ZipFile, path: str) -> str:
    path = (path or "").lstrip("/")
    if not path or not path.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
        return ""
    try:
        info = z.getinfo(path)
        if info.file_size > MAX_ICON_BYTES:
            return ""
        ext = path.rsplit(".", 1)[1].lower()
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
        return f"data:{mime};base64," + base64.b64encode(z.read(path)).decode("ascii")
    except Exception:
        return ""


def _from_mods_toml(z: zipfile.ZipFile, name: str, loader: str) -> Optional[Dict[str, Any]]:
    data = tomllib.loads(z.read(name).decode("utf-8", "ignore"))
    mods = data.get("mods") or []
    if not mods:
        return None
    m = mods[0]
    mod_id = _text(m.get("modId"))
    version = _text(m.get("version"))
    if not version or "${" in version:
        version = _manifest_version(z) or ""
    deps = []
    for d in (data.get("dependencies") or {}).get(mod_id, []) or []:
        dep_id = _text(d.get("modId"))
        if not dep_id:
            continue
        if "mandatory" in d:
            required = bool(d.get("mandatory"))
        else:
            required = _text(d.get("type")).lower() in ("", "required")
        deps.append({"id": dep_id, "version": _text(d.get("versionRange")), "required": required,
                     "side": _text(d.get("side")).lower()})
    return {
        "loader": loader,
        "mod_id": mod_id,
        "name": _text(m.get("displayName")),
        "version": version,
        "description": _text(m.get("description")),
        "authors": _people(m.get("authors")),
        "credits": _text(m.get("credits")),
        "license": _text(data.get("license")),
        "homepage": _text(m.get("displayURL")),
        "issues": _text(data.get("issueTrackerURL")),
        "icon": _icon_data_uri(z, _text(m.get("logoFile")) or _text(data.get("logoFile"))),
        "dependencies": deps,
    }


def _from_fabric_json(z: zipfile.ZipFile, name: str) -> Dict[str, Any]:
    data = json.loads(z.read(name).decode("utf-8", "ignore"), strict=False)
    if name == "quilt.mod.json":
        q = data.get("quilt_loader", {})
        meta = q.get("metadata", {})
        contact = meta.get("contact", {}) or {}
        deps = [{"id": d if isinstance(d, str) else _text(d.get("id")),
                 "version": "" if isinstance(d, str) else str(d.get("versions", "")),
                 "required": True, "side": ""} for d in q.get("depends", []) or []]
        icon = meta.get("icon", "")
        return {
            "loader": "quilt", "mod_id": _text(q.get("id")), "name": _text(meta.get("name")),
            "version": _text(q.get("version")), "description": _text(meta.get("description")),
            "authors": _people(meta.get("contributors")), "credits": "", "license": str(meta.get("license", "") or ""),
            "homepage": _text(contact.get("homepage")), "issues": _text(contact.get("issues")),
            "icon": _icon_data_uri(z, icon if isinstance(icon, str) else ""), "dependencies": deps,
        }
    contact = data.get("contact", {}) or {}
    icon = data.get("icon", "")
    if isinstance(icon, dict) and icon:
        icon = icon[max(icon.keys(), key=lambda k: int(k) if str(k).isdigit() else 0)]
    deps = []
    for kind, required in (("depends", True), ("recommends", False), ("suggests", False)):
        for dep_id, ver in (data.get(kind) or {}).items():
            deps.append({"id": dep_id, "version": ver if isinstance(ver, str) else ", ".join(map(str, ver)),
                         "required": required, "side": ""})
    lic = data.get("license", "")
    return {
        "loader": "fabric", "mod_id": _text(data.get("id")), "name": _text(data.get("name")),
        "version": _text(data.get("version")), "description": _text(data.get("description")),
        "authors": _people(data.get("authors")), "credits": ", ".join(_people(data.get("contributors"))),
        "license": ", ".join(lic) if isinstance(lic, list) else str(lic or ""),
        "homepage": _text(contact.get("homepage")) or _text(contact.get("sources")),
        "issues": _text(contact.get("issues")),
        "icon": _icon_data_uri(z, icon if isinstance(icon, str) else ""), "dependencies": deps,
    }


def _from_mcmod_info(z: zipfile.ZipFile) -> Optional[Dict[str, Any]]:
    data = json.loads(z.read("mcmod.info").decode("utf-8", "ignore"), strict=False)
    if isinstance(data, dict):
        data = data.get("modList") or data.get("modlist") or []
    if not data:
        return None
    m = data[0]
    return {
        "loader": "forge", "mod_id": _text(m.get("modid")), "name": _text(m.get("name")),
        "version": _text(m.get("version")), "description": _text(m.get("description")),
        "authors": _people(m.get("authorList") or m.get("authors")), "credits": _text(m.get("credits")),
        "license": "", "homepage": _text(m.get("url")), "issues": "",
        "icon": _icon_data_uri(z, _text(m.get("logoFile"))),
        "dependencies": [{"id": d, "version": "", "required": True, "side": ""}
                         for d in m.get("requiredMods", []) or [] if isinstance(d, str)],
    }


def _from_plugin_yml(z: zipfile.ZipFile, name: str) -> Dict[str, Any]:
    """plugin.yml senza librerie YAML: bastano le chiavi semplici e le liste di dipendenze."""
    fields: Dict[str, Any] = {}
    key = None
    for raw in z.read(name).decode("utf-8", "ignore").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw[0] not in " \t-" and ":" in raw:
            key, val = raw.split(":", 1)
            key, val = key.strip(), val.strip().strip("'\"")
            if val.startswith("[") and val.endswith("]"):
                fields[key] = [v.strip().strip("'\"") for v in val[1:-1].split(",") if v.strip()]
            else:
                fields[key] = val
        elif key and raw.strip().startswith("- "):
            if not isinstance(fields.get(key), list):
                fields[key] = []
            fields[key].append(raw.strip()[2:].strip().strip("'\""))
    authors = fields.get("authors") or fields.get("author") or []
    deps = [{"id": d, "version": "", "required": True, "side": ""} for d in fields.get("depend", []) or []]
    deps += [{"id": d, "version": "", "required": False, "side": ""} for d in fields.get("softdepend", []) or []]
    return {
        "loader": "paper" if name == "paper-plugin.yml" else "bukkit", "mod_id": fields.get("name", ""),
        "name": fields.get("name", ""), "version": fields.get("version", ""),
        "description": fields.get("description", ""),
        "authors": _people(authors), "credits": "", "license": "", "homepage": fields.get("website", ""),
        "issues": "", "icon": "", "dependencies": deps,
    }


def read_jar_metadata(path: str) -> Dict[str, Any]:
    """Dati scritti dentro il .jar della mod. {} se il file non ne contiene (o non è un .jar valido)."""
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            readers = [
                ("META-INF/neoforge.mods.toml", lambda: _from_mods_toml(z, "META-INF/neoforge.mods.toml", "neoforge")),
                ("META-INF/mods.toml", lambda: _from_mods_toml(z, "META-INF/mods.toml", "forge")),
                ("fabric.mod.json", lambda: _from_fabric_json(z, "fabric.mod.json")),
                ("quilt.mod.json", lambda: _from_fabric_json(z, "quilt.mod.json")),
                ("mcmod.info", lambda: _from_mcmod_info(z)),
                ("paper-plugin.yml", lambda: _from_plugin_yml(z, "paper-plugin.yml")),
                ("plugin.yml", lambda: _from_plugin_yml(z, "plugin.yml")),
            ]
            for entry, reader in readers:
                if entry in names:
                    try:
                        meta = reader()
                    except Exception:
                        meta = None
                    if meta:
                        return meta
    except Exception:
        pass
    return {}


def _modrinth_lookup(sha1: str) -> Dict[str, Any]:
    """Progetto Modrinth del file (cercato per SHA-1). {} se non c'è o se internet non risponde."""
    cached = _MODRINTH_CACHE.get(sha1)
    if cached and time.time() - cached[0] < _MODRINTH_TTL:
        return cached[1]
    result: Dict[str, Any] = {}
    try:
        headers = {"User-Agent": modrinth_store.USER_AGENT}
        req = urllib.request.Request(f"https://api.modrinth.com/v2/version_file/{sha1}?algorithm=sha1", headers=headers)
        with urllib.request.urlopen(req, timeout=6) as resp:
            ver = json.loads(resp.read().decode("utf-8"))
        req = urllib.request.Request(f"https://api.modrinth.com/v2/project/{ver['project_id']}", headers=headers)
        with urllib.request.urlopen(req, timeout=6) as resp:
            proj = json.loads(resp.read().decode("utf-8"))
        result = {
            "url": f"https://modrinth.com/{proj.get('project_type', 'mod')}/{proj.get('slug') or proj.get('id')}",
            "title": proj.get("title", ""),
            "description": proj.get("description", ""),
            "icon_url": proj.get("icon_url") or "",
            "downloads": proj.get("downloads", 0),
            "categories": proj.get("categories", []),
            "client_side": proj.get("client_side", ""),
            "server_side": proj.get("server_side", ""),
            "license": (proj.get("license") or {}).get("id", ""),
            "source_url": proj.get("source_url") or "",
            "issues_url": proj.get("issues_url") or "",
            "updated": proj.get("updated", ""),
            "version_number": ver.get("version_number", ""),
            "game_versions": ver.get("game_versions", []),
            "loaders": ver.get("loaders", []),
        }
        _MODRINTH_CACHE[sha1] = (time.time(), result)
    except urllib.error.HTTPError as e:
        if e.code == 404:  # non è su Modrinth: lo si ricorda come gli altri risultati
            _MODRINTH_CACHE[sha1] = (time.time(), {})
    except Exception:
        pass
    return result


_PROJECT_CACHE: Dict[str, Any] = {}
MAX_BODY_CHARS = 6000


def _get_json(url: str, timeout: float = 6) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": modrinth_store.USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_modrinth_project(project_id: str) -> Dict[str, Any]:
    """Scheda di un progetto dello Store (Modrinth): descrizione completa, autori, versioni, lati e link.
    Solleva ValueError se l'id non è valido, LookupError se il progetto non esiste."""
    pid = (project_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{2,64}", pid):
        raise ValueError("Progetto non valido")
    cached = _PROJECT_CACHE.get(pid)
    if cached and time.time() - cached[0] < _MODRINTH_TTL:
        return cached[1]
    base = "https://api.modrinth.com/v2/project/" + pid
    try:
        proj = _get_json(base)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise LookupError("Progetto non trovato su Modrinth")
        raise
    authors: List[str] = []
    try:
        # Gli autori sono nel gruppo del progetto (richiesta a parte: se non risponde la scheda si mostra lo stesso)
        for m in _get_json(base + "/members", timeout=4):
            name = ((m or {}).get("user") or {}).get("username")
            if name:
                authors.append(f"{name} ({m.get('role')})" if m.get("role") and m.get("role") != "Member" else name)
    except Exception:
        pass
    body = proj.get("body") or ""
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS].rsplit("\n", 1)[0] + "\n…"
    game_versions = [v for v in (proj.get("game_versions") or []) if re.fullmatch(r"\d+\.\d+(\.\d+)?", v)]
    result = {
        "project_id": proj.get("id", pid),
        "slug": proj.get("slug", ""),
        "project_type": proj.get("project_type", "mod"),
        "url": f"https://modrinth.com/{proj.get('project_type', 'mod')}/{proj.get('slug') or proj.get('id')}",
        "title": proj.get("title", ""),
        "description": proj.get("description", ""),
        "body": body,
        "icon_url": proj.get("icon_url") or "",
        "downloads": proj.get("downloads", 0),
        "followers": proj.get("followers", 0),
        "categories": proj.get("categories", []),
        "loaders": proj.get("loaders", []),
        "game_versions": game_versions[-12:],   # le più recenti (l'elenco completo può avere centinaia di voci)
        "client_side": proj.get("client_side", ""),
        "server_side": proj.get("server_side", ""),
        "license": (proj.get("license") or {}).get("id", ""),
        "source_url": proj.get("source_url") or "",
        "issues_url": proj.get("issues_url") or "",
        "wiki_url": proj.get("wiki_url") or "",
        "discord_url": proj.get("discord_url") or "",
        "published": proj.get("published", ""),
        "updated": proj.get("updated", ""),
        "authors": authors,
    }
    _PROJECT_CACHE[pid] = (time.time(), result)
    return result


def get_mod_info(path: str, online: bool = True) -> Dict[str, Any]:
    """Scheda completa della mod: dati del file, dati del .jar e, se c'è, il progetto Modrinth."""
    st = os.stat(path)
    meta = read_jar_metadata(path)
    sha1 = modrinth_store.calculate_file_sha1(path)
    return {
        "filename": os.path.basename(path),
        "size_bytes": st.st_size,
        "mtime": st.st_mtime,
        "sha1": sha1,
        "metadata": meta,
        "modrinth": _modrinth_lookup(sha1) if online and sha1 else {},
    }


def detect_loader_from_mods(mods_dir: str, limit: int = 30) -> str:
    """Loader di un server dedotto dalle sue mod (forge, neoforge, fabric, quilt). "" se non si capisce."""
    counts: Dict[str, int] = {}
    try:
        files = [f for f in os.listdir(mods_dir) if f.lower().endswith(".jar")][:limit]
    except Exception:
        return ""
    for f in files:
        try:
            with zipfile.ZipFile(os.path.join(mods_dir, f)) as z:
                names = set(z.namelist())
        except Exception:
            continue
        if "META-INF/neoforge.mods.toml" in names:
            loader = "neoforge"
        elif "META-INF/mods.toml" in names or "mcmod.info" in names:
            loader = "forge"
        elif "quilt.mod.json" in names:
            loader = "quilt"
        elif "fabric.mod.json" in names:
            loader = "fabric"
        else:
            continue
        counts[loader] = counts.get(loader, 0) + 1
    return max(counts, key=counts.get) if counts else ""
