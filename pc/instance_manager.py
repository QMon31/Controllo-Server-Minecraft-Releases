import os
import json
import shutil
import uuid
from typing import List, Dict, Any

INSTANCES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instances.json")
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def _is_stale_path(server_dir: str) -> bool:
    """True se la cartella manca ma il suo disco c'è: è un percorso di un altro PC (vecchio config.json),
    non un disco esterno scollegato."""
    if not server_dir or os.path.isdir(server_dir):
        return False
    drive = os.path.splitdrive(os.path.abspath(server_dir))[0]
    return bool(drive) and os.path.isdir(drive + os.sep)


def _drop_stale_default(data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Rimuove il "Server Principale" creato in automatico da un config.json rimasto da una vecchia installazione
    (le vecchie versioni dell'installer includevano il config.json del PC di sviluppo). Le istanze create
    dall'utente non vengono mai toccate."""
    kept = [i for i in data
            if not (i.get("id") == "inst_default" and i.get("created_at") == "Inizializzazione"
                    and _is_stale_path(i.get("server_dir", "")))]
    if len(kept) != len(data):
        if kept and not any(i.get("is_active") for i in kept):
            kept[0]["is_active"] = True
        save_instances_data(kept)
    return kept


def load_instances_data() -> List[Dict[str, Any]]:
    """Loads instances from instances.json. If missing, initializes with current config.json."""
    if os.path.isfile(INSTANCES_FILE):
        try:
            with open(INSTANCES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list) and len(data) > 0:
                    return _drop_stale_default(data)
        except Exception as e:
            # Il file verrebbe poi sovrascritto con la sola istanza predefinita: se ne conserva una copia
            print(f"[Istanze] instances.json non leggibile ({e}): copia salvata in instances.json.corrotto")
            try:
                shutil.copy2(INSTANCES_FILE, INSTANCES_FILE + ".corrotto")
            except Exception:
                pass

    # Initialize from config.json
    default_dir = ""
    default_bat = ""
    default_cmd = ""
    default_port = 25565

    if os.path.isfile(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                default_dir = cfg.get("server_dir", default_dir)
                default_bat = cfg.get("server_run_bat", default_bat)
                default_cmd = cfg.get("server_command", default_cmd)
                default_port = cfg.get("server_port", default_port)
        except Exception:
            pass

    if not default_dir or _is_stale_path(default_dir):
        return []

    # Nome, tipo e versione presi dal server della cartella; vuoti se non si riconoscono (mai valori inventati)
    detected = detect_server_details(default_dir)
    if not detected.get("success"):
        detected = {}
    default_instance = {
        "id": "inst_default",
        "name": detected.get("suggested_name") or os.path.basename(os.path.normpath(default_dir)),
        "server_dir": default_dir,
        "server_run_bat": default_bat or detected.get("server_run_bat", ""),
        "server_command": default_cmd or detected.get("server_command", ""),
        "server_port": detected.get("server_port") or default_port,
        "server_type": detected.get("server_type", ""),
        "mc_version": detected.get("mc_version", ""),
        "is_active": True,
        "created_at": "Inizializzazione"
    }
    save_instances_data([default_instance])
    return [default_instance]


def save_instances_data(instances: List[Dict[str, Any]]) -> bool:
    try:
        with open(INSTANCES_FILE, "w", encoding="utf-8") as f:
            json.dump(instances, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"[InstanceManager] Errore salvataggio {INSTANCES_FILE}: {e}")
        return False


def get_active_instance() -> Dict[str, Any]:
    instances = load_instances_data()
    for inst in instances:
        if inst.get("is_active"):
            return inst
    if instances:
        instances[0]["is_active"] = True
        save_instances_data(instances)
        return instances[0]
    return {}


def switch_instance(instance_id: str, manager=None) -> Dict[str, Any]:
    """Switches the active instance, synchronizing config.json and manager."""
    if manager and getattr(manager, "state", "") == "ONLINE":
        return {
            "success": False,
            "error": "Il server corrente è ONLINE. Spegni il server attivo prima di cambiare istanza."
        }

    instances = load_instances_data()
    target = None
    for inst in instances:
        if inst.get("id") == instance_id:
            target = inst
            inst["is_active"] = True
        else:
            inst["is_active"] = False

    if not target:
        return {"success": False, "error": f"Istanza con ID '{instance_id}' non trovata."}

    save_instances_data(instances)

    # Sync to config.json
    try:
        cfg = {}
        if os.path.isfile(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)

        cfg["server_dir"] = target.get("server_dir", cfg.get("server_dir"))
        cfg["server_run_bat"] = target.get("server_run_bat", cfg.get("server_run_bat"))
        cfg["server_command"] = target.get("server_command", cfg.get("server_command"))
        cfg["server_port"] = target.get("server_port", cfg.get("server_port"))

        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)

        if manager:
            manager.config = cfg
    except Exception as e:
        return {"success": False, "error": f"Errore aggiornamento config.json: {e}"}

    return {"success": True, "active_instance": target, "instances": instances}


def add_instance(name: str, server_dir: str, server_run_bat: str = "", server_command: str = "", server_port: int = 25565, server_type: str = "", mc_version: str = "", set_active: bool = False, manager=None) -> Dict[str, Any]:
    instances = load_instances_data()
    inst_id = "inst_" + uuid.uuid4().hex[:8]

    new_inst = {
        "id": inst_id,
        "name": name.strip() or f"Server {mc_version}",
        "server_dir": server_dir.strip(),
        "server_run_bat": server_run_bat.strip() or os.path.join(server_dir.strip(), "run.bat"),
        "server_command": server_command.strip() or "java -jar server.jar nogui",
        "server_port": int(server_port) if server_port else 25565,
        "server_type": server_type.strip().lower(),
        "mc_version": (mc_version or "").strip(),
        "is_active": False
    }

    # Senza un server attivo (es. primo server collegato) il nuovo diventa quello attivo: prima restava attivo solo
    # in instances.json e config.json non aveva la cartella, così il file manager mostrava la cartella del programma
    if not any(i.get("is_active") and i.get("server_dir") and os.path.isdir(i.get("server_dir")) for i in instances):
        set_active = True

    if set_active:
        if manager and getattr(manager, "state", "") == "ONLINE":
            return {"success": False, "error": "Il server attivo è ONLINE. Spegnilo prima di attivare la nuova istanza."}
        for inst in instances:
            inst["is_active"] = False
        new_inst["is_active"] = True

    instances.append(new_inst)
    save_instances_data(instances)

    if set_active:
        switch_instance(inst_id, manager=manager)

    return {"success": True, "instance": new_inst, "instances": instances}


def update_instance(instance_id: str, updates: Dict[str, Any], manager=None) -> Dict[str, Any]:
    instances = load_instances_data()
    target = None
    for inst in instances:
        if inst.get("id") == instance_id:
            target = inst
            break

    if not target:
        return {"success": False, "error": f"Istanza '{instance_id}' non trovata."}

    for k in ["name", "server_dir", "server_run_bat", "server_command", "server_port", "server_type", "mc_version"]:
        if k in updates:
            if k == "server_port":
                try:
                    target[k] = int(updates[k])
                except Exception:
                    pass
            else:
                target[k] = updates[k]

    save_instances_data(instances)

    if target.get("is_active"):
        # Resync config.json if active
        switch_instance(instance_id, manager=manager)

    return {"success": True, "instance": target, "instances": instances}


def delete_instance(instance_id: str) -> Dict[str, Any]:
    instances = load_instances_data()
    target = None
    for inst in instances:
        if inst.get("id") == instance_id:
            target = inst
            break

    if not target:
        return {"success": False, "error": "Istanza non trovata."}

    if target.get("is_active"):
        return {"success": False, "error": "Impossibile eliminare l'istanza attualmente attiva. Attiva prima un'altra istanza."}

    if len(instances) <= 1:
        return {"success": False, "error": "Impossibile eliminare l'unica istanza configurata."}

    instances = [i for i in instances if i.get("id") != instance_id]
    save_instances_data(instances)
    return {"success": True, "instances": instances}


def detect_server_details(folder_path: str) -> Dict[str, Any]:
    """
    Intelligently inspects a directory to determine if it contains a Minecraft server,
    identifying loader type (Forge, Fabric, Paper, Vanilla, NeoForge, Spigot, etc.),
    Minecraft version, server port, launch scripts, and generating an auto-configured profile.
    """
    import re
    import zipfile

    if not folder_path or not os.path.isdir(folder_path):
        return {
            "success": False,
            "error": "La cartella specificata non esiste o non è accessibile sul computer."
        }

    norm_dir = os.path.abspath(folder_path)
    folder_name = os.path.basename(norm_dir) or "Server"

    # Vuoti finché non si trova qualcosa nella cartella: nessun tipo o versione di un altro server
    detected_type = ""
    detected_version = ""
    detected_port = 25565
    detected_bat = "run.bat"
    detected_cmd = ""
    motd = ""
    level_name = "world"
    confidence = "medium"
    key_findings = []

    # 1. Inspect server.properties if present
    props_path = os.path.join(norm_dir, "server.properties")
    if os.path.isfile(props_path):
        key_findings.append("server.properties")
        try:
            with open(props_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip()
                        if k == "server-port":
                            try:
                                detected_port = int(v)
                            except Exception:
                                pass
                        elif k == "motd":
                            motd = v
                        elif k == "level-name":
                            level_name = v
        except Exception:
            pass

    # 2. Check for startup scripts (run.bat, start.bat, launch.bat, etc.)
    found_bat = False
    for bat_candidate in ["run.bat", "start.bat", "launch.bat", "server.bat", "run.cmd", "run.sh"]:
        p = os.path.join(norm_dir, bat_candidate)
        if os.path.isfile(p):
            detected_bat = bat_candidate
            found_bat = True
            key_findings.append(f"Script di avvio ({bat_candidate})")
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as bf:
                    content = bf.read()
                    content_lower = content.lower()
                    if "neoforged" in content_lower:
                        detected_type = "neoforge"
                    elif "minecraftforge" in content_lower:
                        detected_type = "forge"
                    elif "fabric" in content_lower:
                        detected_type = "fabric"
                    elif "paper" in content_lower:
                        detected_type = "paper"

                    # Try to extract actual launch command
                    for line in content.splitlines():
                        ls = line.strip()
                        if ls.startswith("java ") or ls.startswith('"%JAVA_HOME%') or "@user_jvm_args.txt" in ls:
                            detected_cmd = ls
                            break
            except Exception:
                pass
            break

    # 3. Detect Loader & Minecraft Version from library structures
    # NeoForge
    neoforge_lib = os.path.join(norm_dir, "libraries", "net", "neoforged", "neoforge")
    if os.path.isdir(neoforge_lib):
        detected_type = "neoforge"
        key_findings.append("Librerie NeoForge")
        try:
            sub = os.listdir(neoforge_lib)
            if sub:
                m = re.search(r"(\d+\.\d+(?:\.\d+)?)", sub[0])
                if m:
                    detected_version = m.group(1)
                    confidence = "high"
        except Exception:
            pass

    # Forge
    forge_lib = os.path.join(norm_dir, "libraries", "net", "minecraftforge", "forge")
    if os.path.isdir(forge_lib):
        detected_type = "forge"
        key_findings.append("Librerie Minecraft Forge")
        try:
            vers = os.listdir(forge_lib)
            if vers:
                # E.g. '1.20.1-47.2.21'
                m = re.search(r"^(\d+\.\d+(?:\.\d+)?)", vers[0])
                if m:
                    detected_version = m.group(1)
                    confidence = "high"
        except Exception:
            pass

    # Fabric
    fabric_jar = os.path.join(norm_dir, "fabric-server-launch.jar")
    fabric_lib = os.path.join(norm_dir, "libraries", "net", "fabricmc")
    fabric_dir = os.path.join(norm_dir, ".fabric")
    if os.path.isfile(fabric_jar) or os.path.isdir(fabric_lib) or os.path.isdir(fabric_dir):
        detected_type = "fabric"
        key_findings.append("Fabric Loader")
        # Try reading fabric-server-launcher.properties
        fl_prop = os.path.join(norm_dir, "fabric-server-launcher.properties")
        if os.path.isfile(fl_prop):
            try:
                with open(fl_prop, "r", encoding="utf-8", errors="ignore") as flf:
                    for l in flf:
                        if "serverJar=" in l:
                            m = re.search(r"(\d+\.\d+(?:\.\d+)?)", l)
                            if m:
                                detected_version = m.group(1)
                                confidence = "high"
            except Exception:
                pass

    # Paper / Purpur / Spigot
    for f in os.listdir(norm_dir):
        fl = f.lower()
        if fl.startswith("paper") and fl.endswith(".jar"):
            detected_type = "paper"
            key_findings.append(f"Jar Paper ({f})")
            m = re.search(r"paper[-_](\d+\.\d+(?:\.\d+)?)", fl)
            if m:
                detected_version = m.group(1)
                confidence = "high"
            break
        elif fl.startswith("purpur") and fl.endswith(".jar"):
            detected_type = "purpur"
            key_findings.append(f"Jar Purpur ({f})")
            m = re.search(r"purpur[-_](\d+\.\d+(?:\.\d+)?)", fl)
            if m:
                detected_version = m.group(1)
                confidence = "high"
            break
        elif fl.startswith("spigot") and fl.endswith(".jar"):
            detected_type = "spigot"
            key_findings.append(f"Jar Spigot ({f})")
            m = re.search(r"spigot[-_](\d+\.\d+(?:\.\d+)?)", fl)
            if m:
                detected_version = m.group(1)
                confidence = "high"
            break

    # CurseForge Manifest
    manifest_p = os.path.join(norm_dir, "manifest.json")
    if os.path.isfile(manifest_p):
        try:
            with open(manifest_p, "r", encoding="utf-8", errors="ignore") as mf:
                mdata = json.load(mf)
                if "minecraft" in mdata:
                    detected_type = "curseforge"
                    detected_version = mdata["minecraft"].get("version", detected_version)
                    key_findings.append("Manifest Modpack CurseForge")
                    confidence = "high"
        except Exception:
            pass

    # 4. Check server jars inside root or libraries/net/minecraft
    if confidence != "high":
        for f in os.listdir(norm_dir):
            if f.endswith(".jar"):
                jar_path = os.path.join(norm_dir, f)
                try:
                    with zipfile.ZipFile(jar_path, "r") as z:
                        if "version.json" in z.namelist():
                            v_data = json.loads(z.read("version.json").decode("utf-8", errors="ignore"))
                            if "id" in v_data:
                                detected_version = str(v_data["id"])
                                if not detected_type:
                                    detected_type = "vanilla"
                                confidence = "high"
                                key_findings.append(f"version.json in {f}")
                                break
                except Exception:
                    pass

    # Check logs/latest.log
    if confidence != "high":
        log_p = os.path.join(norm_dir, "logs", "latest.log")
        if os.path.isfile(log_p):
            try:
                with open(log_p, "r", encoding="utf-8", errors="ignore") as lf:
                    for idx, line in enumerate(lf):
                        if idx > 40:
                            break
                        m = re.search(r"(?:version|Minecraft)\s+([12]\.\d+(?:\.\d+)?)", line, re.IGNORECASE)
                        if m:
                            detected_version = m.group(1)
                            confidence = "high"
                            key_findings.append("Log di avvio recente")
                            break
            except Exception:
                pass

    # Check mods folder count
    mods_p = os.path.join(norm_dir, "mods")
    if os.path.isdir(mods_p):
        try:
            mod_count = len([f for f in os.listdir(mods_p) if f.lower().endswith(".jar")])
            if mod_count > 0:
                key_findings.append(f"{mod_count} mod presenti in mods/")
                if detected_type in ("", "vanilla"):
                    import mod_info
                    detected_type = mod_info.detect_loader_from_mods(mods_p) or detected_type
        except Exception:
            pass

    # Check plugins folder count
    plugins_p = os.path.join(norm_dir, "plugins")
    if os.path.isdir(plugins_p):
        try:
            plug_count = len([f for f in os.listdir(plugins_p) if f.lower().endswith(".jar")])
            if plug_count > 0:
                key_findings.append(f"{plug_count} plugin presenti in plugins/")
                if detected_type in ("", "vanilla"):
                    detected_type = "paper"
        except Exception:
            pass

    # 5. Format display name and default command
    type_display = {
        "forge": "Forge",
        "fabric": "Fabric",
        "neoforge": "NeoForge",
        "paper": "Paper",
        "purpur": "Purpur",
        "spigot": "Spigot",
        "curseforge": "CurseForge",
        "vanilla": "Vanilla",
        "quilt": "Quilt",
    }.get(detected_type, detected_type.capitalize())

    # Nome dal server stesso: MOTD di server.properties, altrimenti il nome della cartella
    clean_motd = ""
    if motd and len(motd) > 2 and "A Minecraft Server" not in motd:
        clean_motd = re.sub(r"§.", "", motd).replace("\\n", " ").strip()

    type_label = " ".join(x for x in (type_display, detected_version) if x)
    suggested_name = clean_motd or folder_name
    if type_label:
        suggested_name += f" ({type_label})"

    # Default command fallback
    if not detected_cmd:
        if found_bat:
            detected_cmd = detected_bat
        else:
            detected_cmd = "java -Xmx4G -Xms2G -jar server.jar nogui"

    return {
        "success": True,
        "server_dir": norm_dir,
        "folder_name": folder_name,
        "server_type": detected_type,
        "server_type_display": type_display,
        "mc_version": detected_version,
        "server_port": detected_port,
        "server_run_bat": detected_bat,
        "server_command": detected_cmd,
        "motd": clean_motd or motd,
        "level_name": level_name,
        "suggested_name": suggested_name,
        "key_findings": key_findings,
        "confidence": confidence,
        "message": (f"Server {type_label} rilevato con successo (porta {detected_port})." if type_label
                    else f"Cartella riconosciuta (porta {detected_port}): tipo e versione del server non individuati."),
    }
