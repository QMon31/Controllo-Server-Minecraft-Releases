"""
UPnP (Universal Plug and Play) Port Forwarding Manager for Minecraft Control Center
Enables 100% plug & play automatic router port opening for Minecraft Forge servers
without requiring router login, third-party accounts, credit cards, or external tunnels.
"""

import re
import socket
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
import threading
import time
from typing import Optional, Dict, Any

class UPnPManager:
    def __init__(self, config_path: Optional[str] = None):
        self.control_url: Optional[str] = None
        self.service_type: Optional[str] = None
        self.router_ip: Optional[str] = None
        self.external_ip: Optional[str] = None
        self.mapped_port: Optional[int] = None   # porta principale (Minecraft)
        self.is_mapped: bool = False
        self.mapped_ports: set = set()            # tutte le porte aperte da questo programma (Minecraft, HTTPS)
        self.last_error: Optional[str] = None
        # Porta di Minecraft: quella del server (interna) e quella aperta sul router (esterna, diversa se il router
        # rifiuta la stessa porta, es. box iliad che concedono solo porte alte)
        self.mc_internal_port: Optional[int] = None
        self.mc_external_port: Optional[int] = None
        self.last_discovery_time: float = 0
        self.lock = threading.RLock()
        self.config_path = config_path

    def _get_local_ip_for_target(self, target_ip: str = "8.8.8.8") -> str:
        """Determines the local LAN IP of this machine."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((target_ip, 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def discover_gateway(self, timeout: float = 3.0) -> bool:
        """Discovers UPnP Internet Gateway Device on the local network via SSDP."""
        with self.lock:
            # Re-use cached discovery if recent (within 5 minutes)
            if self.control_url and self.service_type and (time.time() - self.last_discovery_time < 300):
                return True

            ssdp_addr = "239.255.255.250"
            ssdp_port = 1900
            msg = (
                "M-SEARCH * HTTP/1.1\r\n"
                f"HOST: {ssdp_addr}:{ssdp_port}\r\n"
                "MAN: \"ssdp:discover\"\r\n"
                "MX: 2\r\n"
                "ST: ssdp:all\r\n"
                "\r\n"
            )

            discovered_locations = []
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(timeout)
                # Con più reti attive (es. hotspot di Windows 192.168.137.x) la ricerca deve partire dalla scheda che porta
                # al router: altrimenti Windows può mandarla sulla rete sbagliata e il router non risponde mai
                lan_ip = self._get_local_ip_for_target()
                if lan_ip and not lan_ip.startswith("127."):
                    try:
                        s.bind((lan_ip, 0))
                        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(lan_ip))
                    except OSError:
                        pass
                s.sendto(msg.encode("utf-8"), (ssdp_addr, ssdp_port))
                start_t = time.time()
                while time.time() - start_t < timeout:
                    try:
                        data, addr = s.recvfrom(4096)
                        text = data.decode("utf-8", errors="ignore")
                        if "WANIPConnection" in text or "WANPPPConnection" in text or "InternetGatewayDevice" in text:
                            for line in text.splitlines():
                                if line.upper().startswith("LOCATION:"):
                                    loc = line.split(":", 1)[1].strip()
                                    if loc not in discovered_locations:
                                        discovered_locations.append(loc)
                    except socket.timeout:
                        break
                    except Exception:
                        pass
                s.close()
            except Exception as e:
                self.last_error = f"Errore invio SSDP discovery: {e}"
                return False

            if not discovered_locations:
                self.last_error = "Nessun router con supporto UPnP rilevato nella rete locale."
                return False

            # Inspect discovered XML locations for WANIPConnection or WANPPPConnection service
            for loc in discovered_locations:
                try:
                    req = urllib.request.Request(loc, headers={"User-Agent": "ControlloServerMinecraft"})
                    with urllib.request.urlopen(req, timeout=2.5) as r:
                        xml_data = r.read().decode("utf-8", errors="ignore")

                    root = ET.fromstring(xml_data)
                    for service in root.iter():
                        if service.tag.endswith("service"):
                            sType = None
                            cUrl = None
                            for child in service:
                                if child.tag.endswith("serviceType"):
                                    sType = child.text
                                elif child.tag.endswith("controlURL"):
                                    cUrl = child.text

                            if sType and cUrl and ("WANIPConnection" in sType or "WANPPPConnection" in sType):
                                # Determine full control URL
                                if cUrl.startswith("http://") or cUrl.startswith("https://"):
                                    self.control_url = cUrl
                                else:
                                    # Extract base host from loc
                                    parsed_loc = loc.split("://", 1)[1]
                                    host_part = parsed_loc.split("/", 1)[0]
                                    scheme = loc.split("://", 1)[0]
                                    if not cUrl.startswith("/"):
                                        cUrl = "/" + cUrl
                                    self.control_url = f"{scheme}://{host_part}{cUrl}"

                                self.service_type = sType
                                self.router_ip = loc.split("://", 1)[1].split(":")[0]
                                self.last_discovery_time = time.time()
                                self.last_error = None
                                print(f"[UPnP] Gateway rilevato: {self.router_ip} ({self.service_type}) -> {self.control_url}")
                                return True
                except Exception as ex:
                    print(f"[UPnP] Errore parsing descrizione {loc}: {ex}")

            self.last_error = "Nessun servizio WANIPConnection o WANPPPConnection trovato nel router."
            return False

    def get_external_ip(self) -> Optional[str]:
        """Queries the router via UPnP for its external WAN IP address."""
        with self.lock:
            if not self.discover_gateway():
                return None

            soap_body = f"""<?xml version="1.0"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
<s:Body>
<u:GetExternalIPAddress xmlns:u="{self.service_type}">
</u:GetExternalIPAddress>
</s:Body>
</s:Envelope>"""

            headers = {
                "SOAPAction": f'"{self.service_type}#GetExternalIPAddress"',
                "Content-Type": 'text/xml; charset="utf-8"'
            }

            try:
                req = urllib.request.Request(self.control_url, data=soap_body.encode("utf-8"), headers=headers)
                with urllib.request.urlopen(req, timeout=3.0) as r:
                    resp_xml = r.read().decode("utf-8", errors="ignore")
                    root = ET.fromstring(resp_xml)
                    ip_elem = root.find(".//NewExternalIPAddress")
                    if ip_elem is not None and ip_elem.text:
                        self.external_ip = ip_elem.text.strip()
                        return self.external_ip
            except Exception as e:
                self.last_error = f"Errore recupero IP esterno da router: {e}"

            # Fallback to public IP echo service
            try:
                req = urllib.request.Request("https://api.ipify.org", headers={"User-Agent": "ControlloServerMinecraft"})
                with urllib.request.urlopen(req, timeout=3.0) as r:
                    self.external_ip = r.read().decode("utf-8").strip()
                    return self.external_ip
            except Exception:
                pass

            return self.external_ip

    def open_port(self, port: int, protocol: str = "TCP", description: str = "Controllo Server Minecraft UPnP",
                  primary: bool = True, internal_port: Optional[int] = None) -> Dict[str, Any]:
        """Opens a port mapping on the router using UPnP AddPortMapping: external `port` -> `internal_port` (default: same)."""
        with self.lock:
            if not self.discover_gateway():
                return {
                    "success": False,
                    "message": self.last_error or "Router UPnP non raggiungibile"
                }

            local_ip = self._get_local_ip_for_target(self.router_ip or "192.168.1.1")

            soap_body = f"""<?xml version="1.0"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
<s:Body>
<u:AddPortMapping xmlns:u="{self.service_type}">
    <NewRemoteHost></NewRemoteHost>
    <NewExternalPort>{port}</NewExternalPort>
    <NewProtocol>{protocol.upper()}</NewProtocol>
    <NewInternalPort>{internal_port or port}</NewInternalPort>
    <NewInternalClient>{local_ip}</NewInternalClient>
    <NewEnabled>1</NewEnabled>
    <NewPortMappingDescription>{description}</NewPortMappingDescription>
    <NewLeaseDuration>0</NewLeaseDuration>
</u:AddPortMapping>
</s:Body>
</s:Envelope>"""

            headers = {
                "SOAPAction": f'"{self.service_type}#AddPortMapping"',
                "Content-Type": 'text/xml; charset="utf-8"'
            }

            try:
                req = urllib.request.Request(self.control_url, data=soap_body.encode("utf-8"), headers=headers)
                with urllib.request.urlopen(req, timeout=3.5) as r:
                    _ = r.read()

                self.mapped_ports.add(port)
                if primary:
                    self.mapped_port = port
                    self.is_mapped = True
                self.last_error = None
                self.get_external_ip()
                print(f"[UPnP] [OK] Porta {port} ({protocol}) aperta con successo sul router -> {local_ip}:{internal_port or port}")
                return {
                    "success": True,
                    "external_ip": self.external_ip,
                    "port": port,
                    "display_address": f"{self.external_ip}:{port}" if self.external_ip else None,
                    "message": f"Porta {port} aperta automaticamente con UPnP."
                }
            except urllib.error.HTTPError as he:
                err_detail = he.read().decode("utf-8", errors="ignore")
                code_match = re.search(r"<errorCode>\s*(\d+)\s*</errorCode>", err_detail)
                upnp_code = int(code_match.group(1)) if code_match else None
                if upnp_code == 718:
                    # Porta già usata dal router o fuori dalle porte concesse (IPv4 condiviso, es. iliad/Free)
                    self.last_error = f"Il router non concede la porta {port} (UPnP 718)"
                else:
                    self.last_error = f"Errore HTTP router ({he.code}, UPnP {upnp_code}): {err_detail[:120]}"
                print(f"[UPnP] [ERR] Errore HTTP AddPortMapping: {self.last_error}")
                return {"success": False, "message": self.last_error, "upnp_error": upnp_code}
            except Exception as e:
                self.last_error = f"Errore richiesta UPnP: {e}"
                print(f"[UPnP] [ERR] Errore AddPortMapping: {self.last_error}")
                return {"success": False, "message": self.last_error}

    def close_port(self, port: Optional[int] = None, protocol: str = "TCP") -> Dict[str, Any]:
        """Closes a port mapping on the router using UPnP DeletePortMapping."""
        with self.lock:
            target_port = port or self.mapped_port
            if not target_port:
                return {"success": True, "message": "Nessuna porta da chiudere."}

            if not self.discover_gateway():
                return {"success": False, "message": self.last_error or "Gateway non trovato"}

            soap_body = f"""<?xml version="1.0"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
<s:Body>
<u:DeletePortMapping xmlns:u="{self.service_type}">
    <NewRemoteHost></NewRemoteHost>
    <NewExternalPort>{target_port}</NewExternalPort>
    <NewProtocol>{protocol.upper()}</NewProtocol>
</u:DeletePortMapping>
</s:Body>
</s:Envelope>"""

            headers = {
                "SOAPAction": f'"{self.service_type}#DeletePortMapping"',
                "Content-Type": 'text/xml; charset="utf-8"'
            }

            try:
                req = urllib.request.Request(self.control_url, data=soap_body.encode("utf-8"), headers=headers)
                with urllib.request.urlopen(req, timeout=3.0) as r:
                    _ = r.read()
                self.mapped_ports.discard(target_port)
                if target_port == self.mapped_port:
                    self.is_mapped = False
                    self.mapped_port = None
                print(f"[UPnP] [STOP] Porta {target_port} chiusa con successo sul router.")
                return {"success": True, "message": f"Porta {target_port} chiusa."}
            except Exception as e:
                print(f"[UPnP] Avviso chiusura porta: {e}")
                return {"success": False, "message": str(e)}

    def open_minecraft_port(self, port: int, avoid: Optional[set] = None) -> Dict[str, Any]:
        """Apre sul router la porta del server Minecraft. Se il router rifiuta la stessa porta (UPnP 718) prova le
        porte alte di riserva verso la porta del server; chiude la mappatura precedente se è cambiata."""
        avoid = {p for p in (avoid or set()) if p}
        with self.lock:
            old_ext = self.mc_external_port
            candidates = []
            if self.mc_internal_port == port and old_ext:
                candidates.append(old_ext)   # stessa porta esterna di prima: l'indirizzo non cambia
            candidates.append(port)
            # Porte alte di riserva: 32769 e poi 32800-32829, separate da quelle dell'accesso da internet
            # (remote_access.FALLBACK_EXTERNAL_PORTS, 32770-32789) perché una mappatura non prenda il posto dell'altra
            candidates.append(32769)
            candidates.extend(range(32800, 32830))
            res: Dict[str, Any] = {"success": False, "message": "Nessuna porta disponibile sul router"}
            tried = set()
            for ext in candidates:
                if ext in tried or ext in avoid:
                    continue
                tried.add(ext)
                res = self.open_port(ext, description="Controllo Server Minecraft", primary=True, internal_port=port)
                if res.get("success"):
                    if old_ext and old_ext != ext:
                        self.close_port(old_ext)
                    self.mc_internal_port = port
                    self.mc_external_port = ext
                    self.mapped_port = ext
                    self.is_mapped = True
                    if ext != port:
                        res["message"] = f"Il router non concede la porta {port}: aperta la {ext} che porta al server."
                    res["external_port"] = ext
                    return res
                if res.get("upnp_error") != 718:
                    break   # router non raggiungibile o altro errore: inutile provare altre porte
            # Nessuna porta aperta: la vecchia mappatura (se c'era) non porta più al server giusto
            if old_ext and self.mc_internal_port != port:
                self.close_port(old_ext)
                self.mc_external_port = None
            self.mc_internal_port = port
            if self.mc_external_port is None:
                self.is_mapped = False
            return res

    def ensure_port_open(self, port: int) -> Dict[str, Any]:
        """Ensures that the given port is opened via UPnP, discovering gateway if needed."""
        with self.lock:
            if self.mc_internal_port == port and self.mc_external_port in self.mapped_ports and self.external_ip:
                return {
                    "success": True,
                    "external_ip": self.external_ip,
                    "port": port,
                    "external_port": self.mc_external_port,
                    "display_address": f"{self.external_ip}:{self.mc_external_port}",
                    "message": "Porta già aperta su UPnP."
                }
            return self.open_minecraft_port(port)

    def get_status(self, port: Optional[int] = None) -> Dict[str, Any]:
        """Stato della porta di Minecraft sul router (port = porta del server)."""
        with self.lock:
            active_p = port or self.mc_internal_port or 32769
            ext = self.mc_external_port if self.mc_internal_port == active_p else None
            mapped = bool(ext) and ext in self.mapped_ports
            addr = f"{self.external_ip}:{ext}" if (self.external_ip and mapped) else None
            return {
                "supported": bool(self.control_url),
                "active": mapped,
                "router_ip": self.router_ip,
                "external_ip": self.external_ip,
                "port": active_p,
                "external_port": ext if mapped else None,
                "display_address": addr,
                "last_error": self.last_error,
                "message": (
                    ("Porta aperta automaticamente sul router (UPnP attivo)" if ext == active_p
                     else f"Il router non concede la porta {active_p}: aperta la {ext} che porta al server")
                    if mapped
                    else (self.last_error or "In attesa apertura automatica porta router...")
                )
            }
