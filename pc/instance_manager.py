import os
import json
import uuid
from typing import List, Dict, Any

from platform_utils import is_java_line, read_json_with_backup, write_json_atomic

INSTANCES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instances.json")
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
# Script di avvio del server: run.bat su Windows, run.sh su Linux (come platform_utils.LAUNCH_SCRIPT)
DEFAULT_LAUNCH_SCRIPT = "run.bat" if os.name == "nt" else "run.sh"
LAUNCH_SCRIPT_SEARCH = (["run.bat", "start.bat", "launch.bat", "server.bat", "run.cmd", "run.sh"] if os.name == "nt"
                        else ["run.sh", "start.sh", "launch.sh", "server.sh", "run.bat", "start.bat"])


def _is_stale_path(server_dir: str) -> bool:
    """True se la cartella manca ma il suo disco c'è: è un percorso di un altro PC (vecchio config.json),
    non un disco esterno scollegato."""
    if not server_dir or os.path.isdir(server_dir):
        return False
    # Su Linux un percorso di Windows (config.json copiato da un PC Windows) è sempre di un altro PC
    if os.name != "nt" and len(server_dir) > 2 and server_dir[1] == ":" and server_dir[0].isalpha():
        return True
    drive = os.path.splitdrive(os.path.abspath(server_dir))[0]
    return bool(drive) and os.path.isdir(drive + os.sep)


def _drop_stale_default(data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Rimuove il "Server Principale" creato in automatico da un config.json rimasto da una vecchia installazione
    (le vecchie versioni dell'installer includevano il config.json del PC di sviluppo). Le istanze create
    dall'utente non vengono mai toccate."""
    kept = [i for i in data
            if not (i.get("id") == "inst_default" and i.get("created_at") == "Inizializzazione"
                    and _is_stale_path(i.get("server_dir", "")))]
    fixed = _fix_detected_versions(kept)
    if len(kept) != len(data) or fixed:
        if kept and not any(i.get("is_active") for i in kept):
            kept[0]["is_active"] = True
        save_instances_data(kept)
    return kept


def _fix_detected_versions(instances: List[Dict[str, Any]]) -> bool:
    """Server collegati con le versioni precedenti: NeoForge con il numero di NeoForge come versione di Minecraft
    (21.1.77) e modpack CurseForge con tipo "curseforge". Si correggono una volta; True se qualcosa è cambiato."""
    import re
    changed = False
    for inst in instances:
        t = str(inst.get("server_type") or "").lower()
        v = str(inst.get("mc_version") or "")
        m = re.match(r"^(\d+)\.", v)
        if t == "neoforge" and m and 2 <= int(m.group(1)) < 26:
            inst["mc_version"] = neoforge_to_mc_version(v) or v
            changed = changed or inst["mc_version"] != v
        if t == "curseforge" and inst.get("server_dir") and os.path.isdir(inst["server_dir"]):
            try:
                det = detect_server_details(inst["server_dir"])
            except Exception:
                det = {}
            if det.get("server_type") and det["server_type"] != "curseforge":
                inst["server_type"] = det["server_type"]
                changed = True
    return changed


def load_instances_data() -> List[Dict[str, Any]]:
    """Loads instances from instances.json. If missing, initializes with current config.json."""
    if os.path.isfile(INSTANCES_FILE):
        try:
            # File rovinato: copia in instances.json.corrotto e si usa instances.json.bak (salvataggio precedente)
            data = read_json_with_backup(INSTANCES_FILE)
            if isinstance(data, list) and len(data) > 0:
                return _drop_stale_default(data)
        except Exception as e:
            # Il file verrebbe poi sovrascritto con la sola istanza predefinita: la copia resta in instances.json.corrotto
            print(f"[Istanze] instances.json non leggibile ({e}): copia salvata in instances.json.corrotto")

    # Initialize from config.json
    default_dir = ""
    default_bat = ""
    default_cmd = ""
    default_port = 25565

    if os.path.isfile(CONFIG_FILE):
        try:
            cfg = read_json_with_backup(CONFIG_FILE)
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
        write_json_atomic(INSTANCES_FILE, instances, keep_backup=True)
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


# Stati in cui si può cambiare il server attivo: nessun processo di Minecraft in avvio, acceso o in arresto
_SWITCH_OK_STATES = ("STOPPED", "CRASHED", "OFFLINE")


def switch_instance(instance_id: str, manager=None) -> Dict[str, Any]:
    """Switches the active instance, synchronizing config.json and manager."""
    # Prima si controllava solo ONLINE: durante avvio o spegnimento il pannello passava all'altro server e perdeva di
    # vista quello che stava ancora girando (il messaggio resta lo stesso, già tradotto nel pannello)
    if manager and getattr(manager, "state", "STOPPED") not in _SWITCH_OK_STATES:
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
        if manager:
            # Si parte dalla configurazione in memoria del pannello e si salva da lì: un solo punto che scrive
            # config.json, così un file illeggibile non fa perdere PIN e chiavi
            cfg = manager.config
        else:
            cfg = read_json_with_backup(CONFIG_FILE) if os.path.isfile(CONFIG_FILE) else {}

        cfg["server_dir"] = target.get("server_dir", cfg.get("server_dir"))
        cfg["server_run_bat"] = target.get("server_run_bat", cfg.get("server_run_bat"))
        cfg["server_command"] = target.get("server_command", cfg.get("server_command"))
        cfg["server_port"] = target.get("server_port", cfg.get("server_port"))

        if manager:
            manager.save_config()
        else:
            write_json_atomic(CONFIG_FILE, cfg, keep_backup=True)
    except Exception as e:
        return {"success": False, "error": f"Errore aggiornamento config.json: {e}"}

    return {"success": True, "active_instance": target, "instances": instances}


def add_instance(name: str, server_dir: str, server_run_bat: str = "", server_command: str = "", server_port: int = 25565, server_type: str = "", mc_version: str = "", set_active: bool = False, manager=None) -> Dict[str, Any]:
    instances = load_instances_data()
    inst_id = "inst_" + uuid.uuid4().hex[:8]
    # Stessa cartella già in elenco (es. il primo server creato: load_instances_data lo aggiunge già da
    # config.json): si aggiorna quella voce invece di avere lo stesso server due volte
    wanted = os.path.normcase(os.path.abspath(server_dir.strip())) if server_dir.strip() else None
    same = next((i for i in instances if wanted and i.get("server_dir")
                 and os.path.normcase(os.path.abspath(i["server_dir"])) == wanted), None)
    pos = len(instances)
    if same:
        pos = instances.index(same)
        instances.remove(same)
        inst_id = same["id"]

    new_inst = {
        "id": inst_id,
        "name": name.strip() or f"Server {mc_version}",
        "server_dir": server_dir.strip(),
        "server_run_bat": server_run_bat.strip() or os.path.join(server_dir.strip(), DEFAULT_LAUNCH_SCRIPT),
        "server_command": server_command.strip() or "java -jar server.jar nogui",
        "server_port": int(server_port) if server_port else 25565,
        "server_type": server_type.strip().lower(),
        "mc_version": (mc_version or "").strip(),
        "is_active": bool(same and same.get("is_active"))
    }

    # Senza un server attivo (es. primo server collegato) il nuovo diventa quello attivo: prima restava attivo solo
    # in instances.json e config.json non aveva la cartella, così il file manager mostrava la cartella del programma
    if not new_inst["is_active"] and not any(i.get("is_active") and i.get("server_dir") and os.path.isdir(i.get("server_dir")) for i in instances):
        set_active = True

    if set_active:
        if manager and getattr(manager, "state", "STOPPED") not in _SWITCH_OK_STATES:
            return {"success": False, "error": "Il server attivo è ONLINE. Spegnilo prima di attivare la nuova istanza."}
        for inst in instances:
            inst["is_active"] = False
        new_inst["is_active"] = True

    instances.insert(pos, new_inst)
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

    # Cartella, avvio e porta del server attivo vanno anche in config.json, che a server acceso non si aggiorna: prima
    # instances.json cambiava e config.json no (si vedevano due valori diversi fino al cambio di server successivo)
    config_keys = {"server_dir", "server_run_bat", "server_command", "server_port"}
    if (target.get("is_active") and manager and getattr(manager, "state", "STOPPED") not in _SWITCH_OK_STATES
            and any(k in updates and str(updates[k]) != str(target.get(k, "")) for k in config_keys)):
        return {"success": False,
                "error": "Il server corrente è ONLINE. Spegni il server attivo prima di cambiare istanza."}

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


def neoforge_to_mc_version(neoforge_version: str) -> str:
    """Versione di Minecraft da quella di NeoForge: 21.1.77 -> 1.21.1, 20.4.237 -> 1.20.4, 21.0.167 -> 1.21;
    dalla numerazione per anni (26.1.0.5 -> 26.1, 26.1.2.3 -> 26.1.2). "" se non si riconosce."""
    import re
    m = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?(?:\.(\d+))?", str(neoforge_version or "").strip())
    if not m:
        return ""
    a, b, c, d = m.group(1), m.group(2), m.group(3), m.group(4)
    if int(a) >= 26:
        return f"{a}.{b}" + (f".{c}" if c and d and c != "0" else "")
    return f"1.{a}" + (f".{b}" if b != "0" else "")


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
    detected_bat = DEFAULT_LAUNCH_SCRIPT
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
    for bat_candidate in LAUNCH_SCRIPT_SEARCH:
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
                        if is_java_line(ls) or "@user_jvm_args.txt" in ls:
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
                # Il numero di NeoForge non è quello di Minecraft: prima un server 1.21.1 risultava "21.1.77"
                mc = neoforge_to_mc_version(sub[0])
                if mc:
                    detected_version = mc
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
                    # Tipo dal caricatore del modpack (forge-47.3.0, neoforge-21.1.77, fabric-0.16.5): prima "curseforge",
                    # per cui lo Store cercava mod per un caricatore inesistente e non ne trovava
                    loaders = [str(l.get("id", "")).lower() for l in (mdata["minecraft"].get("modLoaders") or [])
                               if isinstance(l, dict)]
                    pack_type = next((t for l in loaders for t in ("neoforge", "forge", "fabric", "quilt")
                                      if l.startswith(t + "-")), "")
                    detected_type = pack_type or detected_type or "curseforge"
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
