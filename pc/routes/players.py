"""Whitelist, giocatori bannati e ban/sban."""
from datetime import datetime
from fastapi import HTTPException
from fastapi import Request
import asyncio
import json
import os
import re
from fastapi import APIRouter

import app as core  # loop cambia mentre il programma gira: si legge al momento dell'uso
from app import PlayerActionRequest, _async_broadcast, active_websockets, get_server_dir_path, manager, verify_admin_access

router = APIRouter()


# =========================================================
# WHITELIST & PLAYER SECURITY ROUTES
# =========================================================
@router.get("/api/whitelist")
def api_get_whitelist(request: Request):
    verify_admin_access(request)
    s_dir = get_server_dir_path()
    w_path = os.path.join(s_dir, "whitelist.json")
    items = []
    if os.path.exists(w_path):
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
    props = manager.get_server_properties()
    cur = str(props.get("white-list", "false")).lower() == "true"
    new_state = not cur
    props["white-list"] = "true" if new_state else "false"
    manager.save_server_properties(props)
    if manager.state == "ONLINE":
        manager.send_command("whitelist on" if new_state else "whitelist off")
        manager.send_command("whitelist reload")
    if core.loop and active_websockets:
        asyncio.run_coroutine_threadsafe(_async_broadcast({"type": "whitelist_status", "enabled": new_state}), core.loop)
    return {"status": "success", "enabled": new_state}

@router.post("/api/whitelist/add")
def api_whitelist_add(req: PlayerActionRequest, request: Request):
    verify_admin_access(request)
    player = req.target_player
    if not player or not re.match(r"^\w{2,16}$", player):
        raise HTTPException(status_code=400, detail="Nome giocatore Minecraft non valido (2-16 caratteri alfanumerici)")
    
    # 1. Send live command if server is ONLINE
    if manager.state == "ONLINE":
        manager.send_command(f"whitelist add {player}")
    
    # 2. Update whitelist.json on disk directly
    s_dir = get_server_dir_path()
    w_path = os.path.join(s_dir, "whitelist.json")
    try:
        current = []
        if os.path.exists(w_path):
            with open(w_path, "r", encoding="utf-8") as f:
                current = json.load(f)
        
        # Check if already present
        if not any(entry.get("name", "").lower() == player.lower() for entry in current):
            current.append({
                "uuid": "",
                "name": player
            })
            with open(w_path, "w", encoding="utf-8") as f:
                json.dump(current, f, indent=2)
    except Exception as e:
        print(f"Error updating whitelist.json: {e}")
        
    return {"status": "success", "message": f"Giocatore {player} aggiunto alla whitelist."}

@router.post("/api/whitelist/remove")
def api_whitelist_remove(req: PlayerActionRequest, request: Request):
    verify_admin_access(request)
    player = req.target_player
    if not player:
        raise HTTPException(status_code=400, detail="Nome giocatore non valido")
        
    if manager.state == "ONLINE":
        manager.send_command(f"whitelist remove {player}")
        
    s_dir = get_server_dir_path()
    w_path = os.path.join(s_dir, "whitelist.json")
    try:
        if os.path.exists(w_path):
            with open(w_path, "r", encoding="utf-8") as f:
                current = json.load(f)
            updated = [e for e in current if e.get("name", "").lower() != player.lower()]
            with open(w_path, "w", encoding="utf-8") as f:
                json.dump(updated, f, indent=2)
    except Exception as e:
        print(f"Error updating whitelist.json: {e}")
        
    return {"status": "success", "message": f"Giocatore {player} rimosso dalla whitelist."}

@router.get("/api/banned-players")
def api_get_banned_players(request: Request):
    verify_admin_access(request)
    s_dir = get_server_dir_path()
    b_path = os.path.join(s_dir, "banned-players.json")
    items = []
    if os.path.exists(b_path):
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

@router.post("/api/ban/add")
def api_ban_player(req: PlayerActionRequest, request: Request):
    verify_admin_access(request)
    player = req.target_player
    if not player:
        raise HTTPException(status_code=400, detail="Nome giocatore non specificato")
        
    reason = req.reason or "Espulsione amministrativa"
    if manager.state == "ONLINE":
        manager.send_command(f"ban {player} {reason}")
    else:
        # Update banned-players.json directly
        s_dir = get_server_dir_path()
        b_path = os.path.join(s_dir, "banned-players.json")
        try:
            current = []
            if os.path.exists(b_path):
                with open(b_path, "r", encoding="utf-8") as f:
                    current = json.load(f)
            if not any(entry.get("name", "").lower() == player.lower() for entry in current):
                current.append({
                    "uuid": "",
                    "name": player,
                    "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S +0200"),
                    "source": "Pannello Remoto",
                    "expires": "forever",
                    "reason": reason
                })
                with open(b_path, "w", encoding="utf-8") as f:
                    json.dump(current, f, indent=2)
        except Exception as e:
            print(f"Error updating banned-players.json: {e}")
            
    return {"status": "success", "message": f"Giocatore {player} bannato con successo."}

@router.post("/api/ban/remove")
def api_unban_player(req: PlayerActionRequest, request: Request):
    verify_admin_access(request)
    player = req.target_player
    if not player:
        raise HTTPException(status_code=400, detail="Nome giocatore non specificato")
        
    if manager.state == "ONLINE":
        manager.send_command(f"pardon {player}")
        
    s_dir = get_server_dir_path()
    b_path = os.path.join(s_dir, "banned-players.json")
    try:
        if os.path.exists(b_path):
            with open(b_path, "r", encoding="utf-8") as f:
                current = json.load(f)
            updated = [e for e in current if e.get("name", "").lower() != player.lower()]
            with open(b_path, "w", encoding="utf-8") as f:
                json.dump(updated, f, indent=2)
    except Exception as e:
        print(f"Error unbanning player: {e}")
        
    return {"status": "success", "message": f"Giocatore {player} sbannato con successo."}
