"""
Minecraft Server List Ping (SLP) Implementation in pure Python.
Queries status, protocol version, motd, max players, online players count,
player sample (names and UUIDs), and latency (ping).
"""

import socket
import struct
import json
import time
from typing import Dict, Any

def pack_varint(val: int) -> bytes:
    total = b''
    while True:
        byte = val & 0x7F
        val >>= 7
        if val:
            total += bytes([byte | 0x80])
        else:
            total += bytes([byte])
            break
    return total

def pack_packet(packet_id: int, payload: bytes) -> bytes:
    data = pack_varint(packet_id) + payload
    return pack_varint(len(data)) + data

def read_varint(sock: socket.socket) -> int:
    res, shift = 0, 0
    while True:
        b = sock.recv(1)
        if not b:
            return 0
        val = b[0]
        res |= (val & 0x7F) << shift
        if not (val & 0x80):
            break
        shift += 7
    return res

def query_server_status(host: str = "127.0.0.1", port: int = 25565, timeout: float = 1.5) -> Dict[str, Any]:
    """
    Pings the Minecraft server using standard modern Server List Ping protocol.
    Returns a dictionary with status, latency_ms, players_online, players_max,
    players_sample, motd, and raw json response.
    """
    start_time = time.perf_counter()
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        
        # 1. Handshake Packet (Packet ID 0x00)
        # Protocol version: 763 (Minecraft 1.20.1) or -1
        host_bytes = host.encode('utf-8')
        handshake_payload = (
            pack_varint(763) +
            pack_varint(len(host_bytes)) + host_bytes +
            struct.pack('>H', port) +
            pack_varint(1) # Next state: 1 (status)
        )
        sock.sendall(pack_packet(0x00, handshake_payload))
        
        # 2. Status Request Packet (Packet ID 0x00, no payload)
        sock.sendall(pack_packet(0x00, b''))
        
        # 3. Read Status Response
        _pkt_len = read_varint(sock)
        pkt_id = read_varint(sock)
        if pkt_id != 0x00:
            raise ValueError(f"ID pacchetto non valido: {pkt_id}")
            
        str_len = read_varint(sock)
        data = bytearray()
        while len(data) < str_len:
            chunk = sock.recv(min(4096, str_len - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            
        latency = round((time.perf_counter() - start_time) * 1000, 1)
        res_json = json.loads(data.decode('utf-8', errors='replace'))
        
        players_data = res_json.get("players", {})
        online_count = players_data.get("online", 0)
        max_count = players_data.get("max", 20)
        sample = players_data.get("sample", [])
        
        # Extract plain MOTD
        desc = res_json.get("description", "")
        if isinstance(desc, dict):
            motd_text = desc.get("text", "")
            if "extra" in desc and isinstance(desc["extra"], list):
                for part in desc["extra"]:
                    if isinstance(part, dict):
                        motd_text += part.get("text", "")
                    elif isinstance(part, str):
                        motd_text += part
        else:
            motd_text = str(desc)
            
        version_data = res_json.get("version", {})
        version_name = version_data.get("name", "Unknown")

        return {
            "online": True,
            "latency_ms": latency,
            "players_online": online_count,
            "players_max": max_count,
            "players_sample": sample,
            "motd": motd_text,
            "version": version_name,
            "raw": res_json
        }
    except Exception as e:
        return {
            "online": False,
            "latency_ms": None,
            "players_online": 0,
            "players_max": 20,
            "players_sample": [],
            "motd": "",
            "version": "",
            "error": str(e)
        }
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass
