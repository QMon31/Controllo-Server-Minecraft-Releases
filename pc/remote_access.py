"""
Accesso da internet senza tunnel: HTTPS sull'IP pubblico del PC Host (porta remote_port, aperta sul router con UPnP).

Il certificato è autofirmato e generato una sola volta in certs/: APK e programma PC lo riconoscono dalla sua
impronta SHA-256 (pinning), che resta uguale anche quando l'IP pubblico cambia. La porta HTTP del pannello
(web_port, 8080) non va mai aperta sul router: da internet si passa solo da qui.
"""

import datetime
import hashlib
import ipaddress
import os
import threading
from typing import Any, Callable, Dict, List, Optional

DEFAULT_REMOTE_PORT = 8443
CHECK_INTERVAL_SECONDS = 300
CERT_DIR_NAME = "certs"
# Porte esterne di riserva sul router se rifiuta la 8443 (UPnP 718): alcune box con IPv4 condiviso
# (iliad/Free) concedono solo un intervallo di porte alte. La porta del PC resta sempre remote_port.
FALLBACK_EXTERNAL_PORTS = list(range(32770, 32790)) + [40443, 44443]


def _cert_fingerprint(cert_pem: bytes) -> str:
    """SHA-256 del certificato in formato DER, esadecimale minuscolo (64 caratteri)."""
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding
    der = x509.load_pem_x509_certificate(cert_pem).public_bytes(Encoding.DER)
    return hashlib.sha256(der).hexdigest()


def _certificate_usable(key_path: str, crt_path: str) -> bool:
    """True se chiave e certificato si leggono e vanno insieme."""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        with open(crt_path, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())
        with open(key_path, "rb") as f:
            key = serialization.load_pem_private_key(f.read(), password=None)
        pub = serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        return cert.public_key().public_bytes(*pub) == key.public_key().public_bytes(*pub)
    except Exception:
        return False


def ensure_certificate(cert_dir: str) -> Dict[str, str]:
    """Crea chiave e certificato se mancano; restituisce i percorsi e l'impronta."""
    os.makedirs(cert_dir, exist_ok=True)
    key_path = os.path.join(cert_dir, "remote.key")
    crt_path = os.path.join(cert_dir, "remote.crt")
    if os.path.isfile(key_path) and os.path.isfile(crt_path) and not _certificate_usable(key_path, crt_path):
        # Chiave o certificato rovinati (es. disco o PC spento a metà): prima il programma non partiva più.
        # Se ne crea uno nuovo; la nuova impronta arriva a telefono e PC Remoti con il messaggio firmato su ntfy.
        for p in (key_path, crt_path):
            try:
                os.replace(p, p + ".rotto")
            except OSError:
                pass
        print("[Accesso remoto] Certificato HTTPS illeggibile: ne creo uno nuovo (il vecchio è in certs/*.rotto)")
    if not (os.path.isfile(key_path) and os.path.isfile(crt_path)):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID

        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Controllo Server Minecraft")])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            # Il nome non conta (si verifica l'impronta), ma localhost evita errori nei test sul PC stesso
            .add_extension(x509.SubjectAlternativeName([
                x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .sign(key, hashes.SHA256())
        )
        tmp_key, tmp_crt = key_path + ".tmp", crt_path + ".tmp"
        with open(tmp_key, "wb") as f:
            f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
        with open(tmp_crt, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
        os.replace(tmp_key, key_path)
        os.replace(tmp_crt, crt_path)
    with open(crt_path, "rb") as f:
        fingerprint = _cert_fingerprint(f.read())
    return {"key_path": key_path, "cert_path": crt_path, "fingerprint": fingerprint}


class RemoteAccess:
    def __init__(self, base_dir: str, port: int, upnp_mgr, get_public_ip: Callable[[bool], str],
                 external_port: Optional[int] = None, on_external_port: Optional[Callable[[int], None]] = None):
        self.port = int(port or DEFAULT_REMOTE_PORT)   # porta del PC (HTTPS in ascolto)
        try:
            self.external_port = int(external_port or self.port)  # porta sul router (di solito la stessa)
        except (TypeError, ValueError):
            self.external_port = self.port
        self._on_external_port = on_external_port
        self.upnp = upnp_mgr
        self._get_public_ip = get_public_ip
        self.lock = threading.RLock()
        self.public_ip: Optional[str] = None
        self.port_open = False
        self.user_closed = False   # porta chiusa apposta dal pannello: il controllo periodico non la riapre
        self.error_message: Optional[str] = None
        self._advertised_url: Optional[str] = None   # ultimo indirizzo mandato a telefono e PC Remoti
        self.url_subscribers: List[Callable[[str], None]] = []
        self._stop = threading.Event()
        cert = ensure_certificate(os.path.join(base_dir, CERT_DIR_NAME))
        self.key_path = cert["key_path"]
        self.cert_path = cert["cert_path"]
        self.fingerprint = cert["fingerprint"]

    @property
    def url(self) -> Optional[str]:
        return f"https://{self.public_ip}:{self.external_port}" if self.public_ip else None

    def _open_router_port(self) -> Dict[str, Any]:
        """Apre la porta sul router: prima quella già in uso, poi la 8443, poi le porte di riserva se il router
        le rifiuta per conflitto (718). La porta trovata viene ricordata, così l'indirizzo resta lo stesso."""
        candidates = []
        for p in [self.external_port, self.port] + FALLBACK_EXTERNAL_PORTS:
            if p not in candidates:
                candidates.append(p)
        res: Dict[str, Any] = {"success": False, "message": "Nessuna porta disponibile sul router"}
        for ext in candidates:
            res = self.upnp.open_port(ext, description="Controllo Server Minecraft HTTPS", primary=False,
                                      internal_port=self.port)
            if res.get("success"):
                if ext != self.external_port:
                    self.external_port = ext
                    if self._on_external_port:
                        try:
                            self._on_external_port(ext)
                        except Exception as e:
                            print(f"[Accesso remoto] Porta esterna {ext} non salvata nella configurazione: {e}")
                return res
            if res.get("upnp_error") != 718:
                break   # router non raggiungibile o altro errore: inutile provare altre porte
        return res

    def _read_public_ip(self) -> Optional[str]:
        ip = None
        try:
            ip = self.upnp.get_external_ip()
        except Exception:
            ip = None
        try:
            if not ip or ipaddress.ip_address(ip).is_private:
                ip = self._get_public_ip(True) or None   # router senza UPnP o dietro un altro router
        except ValueError:
            ip = self._get_public_ip(True) or None
        return ip

    def refresh(self, reopen: bool = True) -> Dict[str, Any]:
        """Apre (o riapre) la porta sul router e rilegge l'IP pubblico; avvisa gli iscritti se l'indirizzo cambia."""
        with self.lock:
            if reopen:
                self.user_closed = False
            if not self.user_closed:
                res = self._open_router_port()
                self.port_open = bool(res.get("success"))
                self.error_message = None if self.port_open else res.get("message")
            ip = self._read_public_ip()
            if ip:
                self.public_ip = ip
            elif not self.error_message:
                self.error_message = "IP pubblico non rilevato"
            # Al telefono si manda solo un indirizzo raggiungibile (porta aperta), e solo se è nuovo o se richiesto
            new_url = self.url if self.port_open else None
            notify = bool(new_url) and (new_url != self._advertised_url or reopen)
            if notify:
                self._advertised_url = new_url
        if notify:
            for sub in list(self.url_subscribers):
                try:
                    sub(new_url)
                except Exception as e:
                    print(f"[Accesso remoto] Errore avviso nuovo indirizzo: {e}")
        return self.get_status()

    def close(self) -> Dict[str, Any]:
        with self.lock:
            self.user_closed = True
            try:
                self.upnp.close_port(self.external_port)
            except Exception:
                pass
            self.port_open = False
        return self.get_status()

    def start_watcher(self, interval_seconds: int = CHECK_INTERVAL_SECONDS):
        def _loop():
            # Chiuso dal pannello (ricordato in config.json): all'avvio non si riapre, e si toglie dal router una
            # mappatura rimasta (es. chiusura non riuscita perché il router non rispondeva)
            if self.user_closed:
                self.close()
            self.refresh(reopen=not self.user_closed)
            while not self._stop.wait(interval_seconds):
                self.refresh(reopen=False)
        threading.Thread(target=_loop, daemon=True).start()

    def stop_watcher(self):
        self._stop.set()

    def get_status(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "active": bool(self.port_open and self.public_ip),
                "starting": False,
                "url": self.url if self.port_open else None,
                "public_ip": self.public_ip,
                "port": self.external_port,
                "local_port": self.port,
                "port_open": self.port_open,
                "fingerprint": self.fingerprint,
                "error": self.error_message,
            }
