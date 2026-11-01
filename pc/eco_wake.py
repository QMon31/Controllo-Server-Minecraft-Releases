"""
Custode della Modalità Eco: a server spento per inattività resta in ascolto sulla porta del server al posto di
Minecraft. Nell'elenco server del gioco il server si vede "in pausa"; quando qualcuno prova a entrare, il giocatore
viene disconnesso con un messaggio ("riprova tra un minuto") e il server vero si avvia. Guardare l'elenco dei server
non avvia nulla: conta solo il tentativo di entrare.
Parla il minimo del protocollo di Minecraft che serve (handshake, stato, ping, inizio del login), uguale dalla 1.7.
"""
import base64
import json
import os
import re
import socket
import struct
import threading
from typing import Callable, Optional, Tuple

# Testi mostrati nel gioco (§ = colori di Minecraft)
MOTD_TEXT = "§e[In pausa] §7Server spento dalla Modalità Eco\n§aEntra per avviarlo: ci vuole circa un minuto"
STARTING_TEXT = "§eIl server si sta avviando...\n\n§7Riprova a entrare tra circa un minuto."
BLOCKED_TEXT = "§cIl server non può avviarsi adesso.\n\n§7Riprova tra qualche minuto."
VERSION_NAME = "In pausa"

MAX_PACKET = 32 * 1024          # i pacchetti del client in questa fase sono piccoli
CLIENT_TIMEOUT = 5.0
MAX_CLIENTS = 32                # connessioni contemporanee: la porta è raggiungibile anche da internet
# Nome di un giocatore di Minecraft: arriva dal client e finisce nella console del pannello e nella notifica
_VALID_NAME = re.compile(r"^[A-Za-z0-9_]{1,16}$")


def _pack_varint(val: int) -> bytes:
    out = b""
    val &= 0xFFFFFFFF
    while True:
        byte = val & 0x7F
        val >>= 7
        if val:
            out += bytes([byte | 0x80])
        else:
            return out + bytes([byte])


def _pack_string(text: str) -> bytes:
    data = text.encode("utf-8")
    return _pack_varint(len(data)) + data


def _packet(packet_id: int, payload: bytes) -> bytes:
    body = _pack_varint(packet_id) + payload
    return _pack_varint(len(body)) + body


def _recv_exact(conn: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("connessione chiusa")
        buf.extend(chunk)
    return bytes(buf)


def _read_varint_sock(conn: socket.socket) -> int:
    result = 0
    for i in range(5):
        b = _recv_exact(conn, 1)[0]
        result |= (b & 0x7F) << (7 * i)
        if not b & 0x80:
            return result
    raise ValueError("VarInt troppo lungo")


def _read_varint_buf(data: bytes, pos: int) -> Tuple[int, int]:
    result = 0
    for i in range(5):
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << (7 * i)
        if not b & 0x80:
            if result & 0x80000000:
                result -= 1 << 32   # VarInt con segno (il protocollo -1 di alcuni client)
            return result, pos
    raise ValueError("VarInt troppo lungo")


def _read_string_buf(data: bytes, pos: int) -> Tuple[str, int]:
    length, pos = _read_varint_buf(data, pos)
    if length < 0 or pos + length > len(data):
        raise ValueError("stringa non valida")
    return data[pos:pos + length].decode("utf-8", errors="replace"), pos + length


def _read_packet(conn: socket.socket) -> Tuple[int, bytes]:
    """(id, contenuto) del prossimo pacchetto (senza compressione: in questa fase non c'è)."""
    length = _read_varint_sock(conn)
    if length <= 0 or length > MAX_PACKET:
        raise ValueError("pacchetto non valido")
    data = _recv_exact(conn, length)
    packet_id, pos = _read_varint_buf(data, 0)
    return packet_id, data[pos:]


def _favicon(server_dir: str) -> Optional[str]:
    """Icona del server (server-icon.png) come nell'elenco del gioco quando il server è acceso."""
    path = os.path.join(server_dir, "server-icon.png") if server_dir else ""
    try:
        if path and os.path.isfile(path) and os.path.getsize(path) <= 64 * 1024:
            with open(path, "rb") as f:
                return "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")
    except OSError:
        pass
    return None


class EcoWakeListener:
    """on_login(nome_giocatore) -> (testo per il giocatore, True se il server si avvia e il custode si ferma)."""

    def __init__(self, port: int, server_dir: str, on_login: Callable[[str], Tuple[str, bool]],
                 max_players: int = 20, host: str = "0.0.0.0"):
        self.port = int(port)
        self.server_dir = server_dir
        self.on_login = on_login
        self.max_players = max_players
        self.host = host
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stopping = threading.Event()
        self._wake_lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(MAX_CLIENTS)
        self.woken = False

    def start(self):
        """Apre la porta; solleva OSError se è occupata (es. Minecraft non l'ha ancora liberata)."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name == "nt":
            # Su Windows SO_REUSEADDR permetterebbe a due programmi di usare la stessa porta: si chiede l'esclusiva
            sock.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", socket.SO_REUSEADDR), 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.host, self.port))
            sock.listen(16)
        except OSError:
            sock.close()
            raise
        sock.settimeout(0.5)
        self._sock = sock
        self._thread = threading.Thread(target=self._accept_loop, daemon=True, name="EcoWake")
        self._thread.start()

    def stop(self):
        """Chiude la porta (il server vero la può usare subito dopo)."""
        self._stopping.set()
        sock, self._sock = self._sock, None
        if sock:
            try:
                sock.close()
            except OSError:
                pass
        t = self._thread
        if t and t is not threading.current_thread():
            t.join(timeout=3)

    @property
    def running(self) -> bool:
        return self._sock is not None and not self._stopping.is_set()

    def _accept_loop(self):
        while not self._stopping.is_set():
            sock = self._sock
            if sock is None:
                break
            try:
                conn, _addr = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            if not self._slots.acquire(blocking=False):
                conn.close()   # troppe connessioni insieme
                continue
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _status_json(self, protocol: int) -> str:
        status = {
            # Stesso numero del client: il gioco non segna il server come "versione diversa"
            "version": {"name": VERSION_NAME, "protocol": protocol},
            "players": {"max": self.max_players, "online": 0, "sample": []},
            "description": {"text": MOTD_TEXT},
        }
        icon = _favicon(self.server_dir)
        if icon:
            status["favicon"] = icon
        return json.dumps(status, ensure_ascii=False)

    def _handle(self, conn: socket.socket):
        try:
            conn.settimeout(CLIENT_TIMEOUT)
            first = conn.recv(1, socket.MSG_PEEK)
            if not first or first[0] == 0xFE:
                return   # ping dei client più vecchi della 1.7: non gestito
            packet_id, data = _read_packet(conn)
            if packet_id != 0x00:
                return
            protocol, pos = _read_varint_buf(data, 0)
            _host, pos = _read_string_buf(data, pos)
            pos += 2   # porta
            next_state, pos = _read_varint_buf(data, pos)

            if next_state == 1:   # elenco dei server: stato e ping
                packet_id, _ = _read_packet(conn)
                if packet_id != 0x00:
                    return
                conn.sendall(_packet(0x00, _pack_string(self._status_json(protocol))))
                try:
                    packet_id, payload = _read_packet(conn)
                    if packet_id == 0x01 and len(payload) == 8:
                        conn.sendall(_packet(0x01, payload))
                except (OSError, ValueError, ConnectionError):
                    pass
                return

            if next_state in (2, 3):   # tentativo di entrare (3 = trasferimento, dalla 1.20.5)
                name = "?"
                try:
                    packet_id, payload = _read_packet(conn)
                    if packet_id == 0x00:
                        name, _ = _read_string_buf(payload, 0)
                except (OSError, ValueError, ConnectionError, IndexError):
                    pass
                if not _VALID_NAME.match(name):
                    name = "?"   # prima qualunque testo, anche con "a capo", finiva nella console del pannello
                with self._wake_lock:
                    if self.woken:
                        text = STARTING_TEXT
                    else:
                        text, wake = self.on_login(name)
                        self.woken = bool(wake)
                # Disconnessione nella fase di login: testo JSON, stesso pacchetto in tutte le versioni
                conn.sendall(_packet(0x00, _pack_string(json.dumps({"text": text}, ensure_ascii=False))))
        except (OSError, ValueError, ConnectionError, IndexError, struct.error):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass
            self._slots.release()
