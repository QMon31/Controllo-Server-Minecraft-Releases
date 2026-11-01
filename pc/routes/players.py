"""Whitelist, giocatori bannati e ban/sban."""
from datetime import datetime
from fastapi import HTTPException
from fastapi import Request
import asyncio
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from fastapi import APIRouter
from platform_utils import write_json_atomic

import app as core  # loop cambia mentre il programma gira: si legge al momento dell'uso
from app import PlayerActionRequest, _async_broadcast, active_websockets, backup_mgr, get_server_dir_path, manager, verify_admin_access

router = APIRouter()


# =========================================================
# WHITELIST & PLAYER SECURITY ROUTES
# =========================================================
@router.get("/api/whitelist")
def api_get_whitelist(request: Request):
    verify_admin_access(request)
    s_dir = (get_server_dir_path() or "").strip()
    w_path = os.path.join(s_dir, "whitelist.json") if s_dir else ""
    items = []
    if w_path and os.path.exists(w_path):
        try:
            with open(w_path, "r", encoding="utf-8") as f:
                items = json.load(f)
        except Exception:
            items = []
    props = manager.get_server_properties()
    is_enabled = str(props.get("white-list", "false")).lower() == "true"
    return {
        "status": "success",
        "whitelist": items,
        "count": len(items),
        "enabled": is_enabled
    }

@router.post("/api/whitelist/toggle")
def api_whitelist_toggle(request: Request):
    verify_admin_access(request)
    _refuse_while_changing()
    props = manager.get_server_properties()
    cur = str(props.get("white-list", "false")).lower() == "true"
    new_state = not cur
    props["white-list"] = "true" if new_state else "false"
    saved = manager.save_server_properties({"white-list": props["white-list"]})
    if saved.get("status") == "error":
        # Prima l'errore (es. server.properties mancante) veniva ignorato e il pannello mostrava la whitelist cambiata
        raise HTTPException(status_code=400, detail=saved.get("message"))
    if manager.state == "ONLINE":
        _send_or_fail("whitelist on" if new_state else "whitelist off")
        manager.send_command("whitelist reload")
    if core.loop and active_websockets:
        asyncio.run_coroutine_threadsafe(_async_broadcast({"type": "whitelist_status", "enabled": new_state}), core.loop)
    return {"status": "success", "enabled": new_state}

LIST_FILE_ERROR_MSG = "Impossibile aggiornare {0} (file illeggibile o bloccato): la modifica non è stata salvata."


def _file_error(name: str):
    """A server spento il file è l'unico posto della modifica: prima un errore (file rovinato o bloccato) veniva solo
    scritto nel registro e il pannello rispondeva comunque "fatto". A server acceso ci pensa il comando a Minecraft."""
    if manager.state != "ONLINE":
        raise HTTPException(status_code=500, detail=LIST_FILE_ERROR_MSG.format(name))


SERVER_CHANGING_MSG = "Il server si sta avviando o spegnendo: riprova tra qualche secondo."


def _refuse_while_changing():
    """Whitelist e ban scritti nei file mentre Minecraft si avvia o si spegne: Minecraft li legge o li riscrive in quel
    momento e la modifica si perdeva senza avviso. Si accettano solo a server acceso (comando) o spento (file)."""
    if manager.state in ("STARTING", "STOPPING", "RESTARTING"):
        raise HTTPException(status_code=409, detail=SERVER_CHANGING_MSG)
    if backup_mgr.is_running:
        raise HTTPException(status_code=409, detail="Attendi la fine del backup o del ripristino in corso.")
    if not (get_server_dir_path() or "").strip():
        raise HTTPException(status_code=400, detail="Nessun server collegato: configura prima una cartella del server.")


def _send_or_fail(cmd: str):
    """Comando a Minecraft acceso. Prima il risultato veniva ignorato: con un server riagganciato e RCON spento
    il comando non partiva ma il pannello rispondeva "aggiunto/bannato" lo stesso."""
    res = manager.send_command(cmd)
    if res.get("status") == "error":
        raise HTTPException(status_code=503, detail=res.get("message") or "Comando non inviato al server.")


COMMAND_FAILED_FILE_SAVED_MSG = "Modifica salvata nel file, ma il server acceso non l'ha ricevuta: {0}"


NOT_FOUND_MSG = "Il giocatore {0} non esiste su Minecraft: controlla il nome."
MOJANG_UNREACHABLE_MSG = ("Impossibile verificare il nome del giocatore (servizio Mojang non raggiungibile): "
                          "riprova tra poco oppure accendi il server.")


def _offline_mode(s_dir: str) -> bool:
    try:
        with open(os.path.join(s_dir, "server.properties"), "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.strip().lower().replace(" ", "").startswith("online-mode="):
                    return line.split("=", 1)[1].strip().lower() == "false"
    except OSError:
        pass
    return False


def _player_profile(player: str):
    """(uuid, nome) da scrivere in whitelist.json / banned-players.json. Prima l'uuid restava vuoto: Minecraft 1.21
    non riusciva a leggere il file, lo metteva da parte e ripartiva con l'elenco vuoto (tutti i ban persi).
    None se il nome non esiste; HTTPException 503 se Mojang non risponde."""
    if _offline_mode(get_server_dir_path()):
        # Server con online-mode=false: stesso uuid che calcola Minecraft (UUID.nameUUIDFromBytes)
        b = bytearray(hashlib.md5(f"OfflinePlayer:{player}".encode("utf-8")).digest())
        b[6] = (b[6] & 0x0F) | 0x30
        b[8] = (b[8] & 0x3F) | 0x80
        return str(uuid.UUID(bytes=bytes(b))), player
    url = "https://api.mojang.com/users/profiles/minecraft/" + urllib.parse.quote(player)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Voxarium"}), timeout=8) as r:
            data = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        if e.code in (204, 404):
            return None
        raise HTTPException(status_code=503, detail=MOJANG_UNREACHABLE_MSG)
    except (OSError, ValueError):
        raise HTTPException(status_code=503, detail=MOJANG_UNREACHABLE_MSG)
    raw = (data or {}).get("id") or ""
    if len(raw) != 32:
        return None
    return str(uuid.UUID(raw)), data.get("name") or player


def _add_or_repair_entry(current: list, player: str, profile, extra: dict) -> bool:
    """Aggiunge il giocatore a whitelist.json / banned-players.json (elenco già letto). Una voce con lo stesso nome
    ma l'uuid vuoto, scritta dalle versioni fino alla 4.22.2, riceve l'uuid giusto: prima restava vuota anche
    ripetendo il comando. True se l'elenco è cambiato e va salvato."""
    same = next((e for e in current if isinstance(e, dict) and e.get("name", "").lower() == player.lower()), None)
    if same is None:
        current.append({"uuid": profile[0], "name": profile[1], **extra})
        return True
    if not same.get("uuid"):
        same["uuid"], same["name"] = profile[0], profile[1]
        return True
    return False


@router.post("/api/whitelist/add")
def api_whitelist_add(req: PlayerActionRequest, request: Request):
    verify_admin_access(request)
    _refuse_while_changing()
    player = req.target_player
    if not player or not re.match(r"^\w{2,16}$", player):
        raise HTTPException(status_code=400, detail="Nome giocatore Minecraft non valido (2-16 caratteri alfanumerici)")
    
    # 1. Server acceso: lo scrive Minecraft stesso (con l'uuid giusto)
    if manager.state == "ONLINE":
        seq = manager.log_seq
        _send_or_fail(f"whitelist add {player}")
        if _console_replied(seq, "That player does not exist", f"Added {player}"):
            raise HTTPException(status_code=404, detail=NOT_FOUND_MSG.format(player))
        return {"status": "success", "message": f"Giocatore {player} aggiunto alla whitelist."}

    # 2. Server spento: whitelist.json aggiornato qui, con l'uuid del giocatore
    profile = _player_profile(player)
    if profile is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND_MSG.format(player))
    s_dir = get_server_dir_path()
    w_path = os.path.join(s_dir, "whitelist.json")
    try:
        current = []
        if os.path.exists(w_path):
            with open(w_path, "r", encoding="utf-8") as f:
                current = json.load(f)
        
        if _add_or_repair_entry(current, player, profile, {}):
            write_json_atomic(w_path, current)
    except Exception as e:
        print(f"Error updating whitelist.json: {e}")
        _file_error("whitelist.json")
        
    return {"status": "success", "message": f"Giocatore {player} aggiunto alla whitelist."}

@router.post("/api/whitelist/remove")
def api_whitelist_remove(req: PlayerActionRequest, request: Request):
    verify_admin_access(request)
    _refuse_while_changing()
    player = req.target_player
    if not player:
        raise HTTPException(status_code=400, detail="Nome giocatore non valido")
        
    online = manager.state == "ONLINE"
    if online:
        res = manager.send_command(f"whitelist remove {player}")
        
    s_dir = get_server_dir_path()
    w_path = os.path.join(s_dir, "whitelist.json")
    try:
        if os.path.exists(w_path):
            with open(w_path, "r", encoding="utf-8") as f:
                current = json.load(f)
            updated = [e for e in current if e.get("name", "").lower() != player.lower()]
            write_json_atomic(w_path, updated)
    except Exception as e:
        print(f"Error updating whitelist.json: {e}")
        _file_error("whitelist.json")
    if online and res.get("status") == "error":
        raise HTTPException(status_code=503, detail=COMMAND_FAILED_FILE_SAVED_MSG.format(res.get("message") or ""))
    return {"status": "success", "message": f"Giocatore {player} rimosso dalla whitelist."}

@router.get("/api/banned-players")
def api_get_banned_players(request: Request):
    verify_admin_access(request)
    s_dir = (get_server_dir_path() or "").strip()
    b_path = os.path.join(s_dir, "banned-players.json") if s_dir else ""
    items = []
    if b_path and os.path.exists(b_path):
        try:
            with open(b_path, "r", encoding="utf-8") as f:
                items = json.load(f)
        except Exception:
            items = []
    return {
        "status": "success",
        "bans": items,
        "count": len(items)
    }

def _console_replied(seq: int, text: str, ok_text: str, timeout: float = 3.0) -> bool:
    """True se dopo la riga numero seq la console di Minecraft scrive text; False appena scrive ok_text o dopo
    timeout secondi (attesa breve della risposta al comando)."""
    end = time.time() + timeout
    while time.time() < end:
        for line in manager.logs_since(seq).get("lines", []):
            if text in line:
                return True
            if ok_text in line:
                return False
        time.sleep(0.25)
    return False


@router.post("/api/ban/add")
def api_ban_player(req: PlayerActionRequest, request: Request):
    verify_admin_access(request)
    _refuse_while_changing()
    player = req.target_player
    if not player:
        raise HTTPException(status_code=400, detail="Nome giocatore non specificato")
        
    if not re.match(r"^\w{2,16}$", player):
        raise HTTPException(status_code=400, detail="Nome giocatore Minecraft non valido (2-16 caratteri alfanumerici)")

    reason = req.reason or "Espulsione amministrativa"
    if manager.state == "ONLINE":
        seq = manager.log_seq
        _send_or_fail(f"ban {player} {reason}")
        # Nome che non esiste: Minecraft risponde solo in console e prima il pannello diceva "bannato con successo"
        if _console_replied(seq, "That player does not exist", f"Banned {player}"):
            raise HTTPException(status_code=404, detail=NOT_FOUND_MSG.format(player))
    else:
        # Server spento: banned-players.json aggiornato qui, con l'uuid del giocatore
        profile = _player_profile(player)
        if profile is None:
            raise HTTPException(status_code=404, detail=NOT_FOUND_MSG.format(player))
        s_dir = get_server_dir_path()
        b_path = os.path.join(s_dir, "banned-players.json")
        try:
            current = []
            if os.path.exists(b_path):
                with open(b_path, "r", encoding="utf-8") as f:
                    current = json.load(f)
            if _add_or_repair_entry(current, player, profile, {
                    # Fuso orario del PC (prima sempre +0200, anche con l'ora solare)
                    "created": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z"),
                    "source": "Pannello Remoto",
                    "expires": "forever",
                    "reason": reason
            }):
                write_json_atomic(b_path, current)
        except Exception as e:
            print(f"Error updating banned-players.json: {e}")
            _file_error("banned-players.json")
            
    return {"status": "success", "message": f"Giocatore {player} bannato con successo."}

@router.post("/api/ban/remove")
def api_unban_player(req: PlayerActionRequest, request: Request):
    verify_admin_access(request)
    _refuse_while_changing()
    player = req.target_player
    if not player:
        raise HTTPException(status_code=400, detail="Nome giocatore non specificato")
        
    online = manager.state == "ONLINE"
    if online:
        res = manager.send_command(f"pardon {player}")
        
    s_dir = get_server_dir_path()
    b_path = os.path.join(s_dir, "banned-players.json")
    try:
        if os.path.exists(b_path):
            with open(b_path, "r", encoding="utf-8") as f:
                current = json.load(f)
            updated = [e for e in current if e.get("name", "").lower() != player.lower()]
            write_json_atomic(b_path, updated)
    except Exception as e:
        print(f"Error unbanning player: {e}")
        _file_error("banned-players.json")
    if online and res.get("status") == "error":
        raise HTTPException(status_code=503, detail=COMMAND_FAILED_FILE_SAVED_MSG.format(res.get("message") or ""))
    return {"status": "success", "message": f"Giocatore {player} sbannato con successo."}
