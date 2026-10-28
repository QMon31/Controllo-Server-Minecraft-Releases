"""Test automatici eseguiti da build_deploy.py prima di ogni compilazione: se uno fallisce la compilazione si ferma.

Avvio a mano:  python -m unittest discover -s tests -v
Ogni test lavora su una copia dei moduli in una cartella temporanea: config.json e instances.json di questo PC
non vengono mai toccati e nessuna richiesta parte verso ntfy.sh.
"""
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile
import types
import urllib.parse
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pin_rules import is_default_pin, validate_new_pin  # noqa: E402

MISSING_DIR = r"C:\Users\nessuno_test_xyz\Desktop\Servere_Mc"   # cartella di un altro PC: il disco C: c'è, la cartella no
# Topic di prova trattato come il vecchio topic condiviso (del vero topic il codice conserva solo l'impronta)
LEGACY_TOPIC = "mc_server_vecchio_condiviso_prova"
# Dati personali che non devono finire nei file pubblicati (IP, vecchio topic, nome utente): elenco locale di questo
# PC, escluso da git; usato anche da publish_source.py
PERSONAL_STRINGS_FILE = os.path.join(ROOT, "personal_strings.txt")


def _personal_strings():
    try:
        with open(PERSONAL_STRINGS_FILE, encoding="utf-8") as fh:
            return [s.strip() for s in fh if s.strip() and not s.startswith("#")]
    except OSError:
        return []


def _sandbox(files, instances=None, config=None):
    """Copia i moduli indicati in una cartella temporanea, con instances.json/config.json di prova."""
    d = tempfile.mkdtemp(prefix="csm_test_")
    for f in files:
        shutil.copy(os.path.join(ROOT, f), d)
    if instances is not None:
        with open(os.path.join(d, "instances.json"), "w", encoding="utf-8") as fh:
            json.dump(instances, fh)
    if config is not None:
        with open(os.path.join(d, "config.json"), "w", encoding="utf-8") as fh:
            json.dump(config, fh)
    return d


def _load(d, name):
    spec = importlib.util.spec_from_file_location(f"{name}_{os.path.basename(d)}", os.path.join(d, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _auto_instance(path):
    return {"id": "inst_default", "name": "Server Principale", "server_dir": path,
            "is_active": True, "created_at": "Inizializzazione"}


class PinRulesTest(unittest.TestCase):
    def test_default_pin(self):
        self.assertTrue(is_default_pin("1234"))
        self.assertTrue(is_default_pin(" 1234 "))
        self.assertTrue(is_default_pin(""))
        self.assertTrue(is_default_pin(None))
        self.assertFalse(is_default_pin("583921"))

    def test_new_pin_rules(self):
        self.assertIsNone(validate_new_pin("583921"))
        self.assertIsNone(validate_new_pin("583921047"))
        self.assertIsNotNone(validate_new_pin("1234"))          # troppo corto
        self.assertIsNotNone(validate_new_pin("12345a"))        # non solo cifre
        self.assertIsNotNone(validate_new_pin("1111111"))       # cifre tutte uguali
        self.assertIsNotNone(validate_new_pin("123456"))        # sequenza
        self.assertIsNotNone(validate_new_pin("987654"))        # sequenza al contrario
        self.assertIsNotNone(validate_new_pin("1234567890123")) # troppo lungo


class StaleInstanceTest(unittest.TestCase):
    """Nuovo PC Host con una vecchia installazione: niente server fantasma."""

    def test_leftover_instance_removed(self):
        d = _sandbox(["instance_manager.py"], instances=[_auto_instance(MISSING_DIR)])
        self.assertEqual(_load(d, "instance_manager").load_instances_data(), [])

    def test_old_config_creates_no_instance(self):
        d = _sandbox(["instance_manager.py"], config={"server_dir": MISSING_DIR})
        self.assertEqual(_load(d, "instance_manager").load_instances_data(), [])

    def test_real_folder_kept(self):
        real = tempfile.mkdtemp()
        d = _sandbox(["instance_manager.py"], instances=[_auto_instance(real)])
        self.assertEqual(len(_load(d, "instance_manager").load_instances_data()), 1)

    @unittest.skipUnless(sys.platform == "win32", "lettere di unità: solo Windows")
    def test_disconnected_drive_kept(self):
        drive = next((c for c in "ZYXWVQ" if not os.path.exists(c + ":\\")), None)
        if not drive:
            self.skipTest("nessuna lettera di unità libera")
        d = _sandbox(["instance_manager.py"], instances=[_auto_instance(drive + r":\Servers\mc")])
        self.assertEqual(len(_load(d, "instance_manager").load_instances_data()), 1)

    def test_user_instance_kept_and_activated(self):
        user = {"id": "inst_abc", "name": "Mio", "server_dir": MISSING_DIR, "is_active": False, "created_at": "2026-10-01"}
        d = _sandbox(["instance_manager.py"], instances=[_auto_instance(MISSING_DIR), user])
        res = _load(d, "instance_manager").load_instances_data()
        self.assertEqual([i["id"] for i in res], ["inst_abc"])
        self.assertTrue(res[0]["is_active"])


class ServerManagerConfigTest(unittest.TestCase):
    FILES = ["server_manager.py", "instance_manager.py", "mc_ping.py", "platform_utils.py"]

    def _manager(self, config):
        d = _sandbox(self.FILES, config=config)
        sm = _load(d, "server_manager")
        cfg_path = os.path.join(d, "config.json")
        return sm, sm.ServerManager(config_path=cfg_path), cfg_path

    def test_legacy_config_gets_own_topic_and_secret(self):
        import hashlib
        d = _sandbox(self.FILES, config={"server_dir": MISSING_DIR, "server_run_bat": MISSING_DIR + r"\run.bat",
                                         "push_topic": LEGACY_TOPIC, "push_secret": "a" * 64, "admin_pin": "1234"})
        sm = _load(d, "server_manager")
        self.assertEqual(len(sm.LEGACY_TOPIC_HASHES), 1)  # il vero topic resta riconosciuto
        sm.LEGACY_TOPIC_HASHES = {hashlib.sha256(LEGACY_TOPIC.encode("utf-8")).hexdigest()}
        cfg_path = os.path.join(d, "config.json")
        sm.ServerManager(config_path=cfg_path)
        with open(cfg_path, encoding="utf-8") as fh:
            c = json.load(fh)
        self.assertNotEqual(c["push_topic"], LEGACY_TOPIC)
        self.assertTrue(c["push_topic"].startswith("mc_server_"))
        self.assertNotEqual(c["push_secret"], "a" * 64)
        self.assertEqual(c["server_dir"], "")            # cartella di un altro PC azzerata
        self.assertEqual(c["admin_pin"], "1234")          # PIN non toccato

    def test_alerts_are_signed(self):
        # Ogni avviso inviato a ntfy porta ts-<secondi> e sig-<HMAC(push_secret, titolo + a capo + messaggio + a capo + ts)>
        import hashlib
        import hmac
        sm, m, _ = self._manager({"server_dir": "", "push_topic": "mc_server_prova", "push_secret": "b" * 64})
        sent = []

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class SyncThread:
            def __init__(self, target=None, daemon=None):
                self.target = target

            def start(self):
                self.target()
        real_open, real_thread = sm.urllib.request.urlopen, sm.threading.Thread
        sm.urllib.request.urlopen = lambda req, timeout=None: (sent.append(json.loads(req.data.decode("utf-8"))), Resp())[1]
        sm.threading.Thread = SyncThread
        try:
            m.send_push_notification("Crash del server", "Il server si è chiuso", tags="warning")
        finally:
            sm.urllib.request.urlopen, sm.threading.Thread = real_open, real_thread
        self.assertEqual(len(sent), 1)
        tags = sent[0]["tags"]
        ts = next(t[3:] for t in tags if t.startswith("ts-"))
        sig = next(t[4:] for t in tags if t.startswith("sig-"))
        data = ("Crash del server" + "NL" + "Il server si è chiuso" + "NL" + ts).replace("NL", chr(10))
        self.assertEqual(sig, hmac.new(b"b" * 64, data.encode("utf-8"), hashlib.sha256).hexdigest())
        self.assertIn("warning", tags)

    def test_max_players_from_properties_when_offline(self):
        srv = tempfile.mkdtemp(prefix="csm_srv_")
        try:
            with open(os.path.join(srv, "server.properties"), "w", encoding="utf-8") as fh:
                fh.write("motd=prova\nmax-players=25\n")
            sm, m, _ = self._manager({"server_dir": srv})
            self.assertEqual(m.get_configured_max_players(), 25)
            self.assertIn('else self.get_configured_max_players()', open(sm.__file__, encoding="utf-8").read())
            # Appena acceso, prima che il ping risponda (server_info con il 20 di default): sempre server.properties
            m.state = "ONLINE"
            m.server_info = {"online": False, "players_max": 20}
            self.assertEqual(m.get_full_status()["players"]["max"], 25)
            m.server_info = {"online": True, "players_max": 30}
            self.assertEqual(m.get_full_status()["players"]["max"], 30)
        finally:
            shutil.rmtree(srv, ignore_errors=True)

    def test_jcmd_found_next_to_server_java(self):
        # Il jcmd va cercato accanto al java.exe del server, non in un percorso fisso di questo PC
        sm, m, _ = self._manager({"server_dir": ""})
        jdk = tempfile.mkdtemp(prefix="csm_jdk_")
        try:
            java = os.path.join(jdk, "java.exe")
            jcmd = os.path.join(jdk, "jcmd.exe" if os.name == "nt" else "jcmd")
            for f in (java, jcmd):
                open(f, "w").close()

            class _FakeProc:
                def __init__(self, pid):
                    pass

                def exe(self):
                    return java
            real = sm.psutil.Process
            sm.psutil.Process = _FakeProc
            try:
                self.assertEqual(m._find_jcmd(4242), jcmd)
            finally:
                sm.psutil.Process = real
            self.assertIn("_find_jcmd(pid)", open(sm.__file__, encoding="utf-8").read())
            self.assertNotIn("jdk-21.0.11", open(sm.__file__, encoding="utf-8").read())
        finally:
            shutil.rmtree(jdk, ignore_errors=True)

    def test_legacy_tunnel_keys_dropped(self):
        _, m, _ = self._manager({"push_topic": "mc_server_test_0000", "push_secret": "b" * 64,
                                 "tunnel_token": "segreto-vecchio", "ngrok_token": "x"})
        self.assertNotIn("tunnel_token", m.config)
        self.assertNotIn("ngrok_token", m.config)

    def test_network_sync_retried_after_ntfy_failure(self):
        sm, m, _ = self._manager({"push_topic": "mc_server_test_0000", "push_secret": "b" * 64})
        sent = []

        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fail(*a, **k): raise OSError("ntfy irraggiungibile")
        def ok(req, timeout=0): sent.append(json.loads(req.data)); return _Resp()

        sm.urllib.request.urlopen = fail
        m.broadcast_network_sync("https://81.0.0.1:8443", "http://192.168.1.50:8080", "f" * 64)
        time.sleep(0.5)
        self.assertTrue(m._network_sync_pending)
        sm.urllib.request.urlopen = ok
        m.broadcast_network_sync(m.current_remote_url, m.current_local_url, m.current_cert_fingerprint)
        time.sleep(0.5)
        self.assertFalse(m._network_sync_pending)
        msg = sent[-1]["message"]
        self.assertIn("https://81.0.0.1:8443", msg)
        self.assertIn("fp=" + "f" * 64, msg)
        # La firma copre anche l'impronta: chi legge il topic non può sostituire il certificato
        import hashlib
        import hmac
        expected = hmac.new(("b" * 64).encode(), f"https://81.0.0.1:8443|http://192.168.1.50:8080|{'f' * 64}".encode(),
                            hashlib.sha256).hexdigest()
        self.assertIn("sig=" + expected, msg)
        # sig2 firma anche l'ora: un messaggio vecchio non si può rimandare all'app
        import re
        ts = re.search(r"\bts=(\d+)", msg).group(1)
        self.assertLess(abs(int(ts) - time.time()), 60)
        expected2 = hmac.new(("b" * 64).encode(), f"https://81.0.0.1:8443|http://192.168.1.50:8080|{'f' * 64}|{ts}".encode(),
                             hashlib.sha256).hexdigest()
        self.assertIn("sig2=" + expected2, msg)
        self.assertEqual(re.search(r"sig=([0-9a-f]{64})", msg).group(1), expected)   # le app vecchie trovano ancora sig


class NoConsoleWindowsTest(unittest.TestCase):
    """Il pannello gira senza console: ogni processo avviato senza creationflags apre una finestra cmd
    (era la causa delle finestre che si aprivano e chiudevano di continuo a server acceso)."""

    def test_every_subprocess_has_creationflags(self):
        import re
        offenders = []
        for f in sorted(os.listdir(ROOT)):
            if not f.endswith(".py") or f in ("build_deploy.py", "build_runtime.py", "publish_release.py", "publish_source.py",
                                                 "create_package.py"):
                continue
            with open(os.path.join(ROOT, f), encoding="utf-8", errors="ignore") as fh:
                src = fh.read()
            for m in re.finditer(r"subprocess\.(run|Popen|call|check_output|check_call)\(", src):
                # argomenti della chiamata fino alla parentesi di chiusura corrispondente
                depth, i = 0, m.end() - 1
                while i < len(src):
                    depth += {"(": 1, ")": -1}.get(src[i], 0)
                    if depth == 0:
                        break
                    i += 1
                call = src[m.start():i + 1]
                if "creationflags" not in call and "xdg-open" not in call:
                    offenders.append(f"{f}:{src.count(chr(10), 0, m.start()) + 1}")
        self.assertEqual(offenders, [], "processi avviati senza nascondere la finestra")


class CrossSiteTest(unittest.TestCase):
    """Un sito web aperto nel browser del PC Host non deve poter comandare il pannello (localhost è senza PIN)."""

    @classmethod
    def setUpClass(cls):
        import ast
        from urllib.parse import urlsplit
        with open(os.path.join(ROOT, "app.py"), encoding="utf-8") as fh:
            cls.src = fh.read()
        # Solo la funzione, senza importare app.py (avvierebbe server, tunnel e UPnP)
        fn = next(n for n in ast.parse(cls.src).body if isinstance(n, ast.FunctionDef) and n.name == "is_cross_site_request")
        ns = {"urlsplit": urlsplit}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "app.py", "exec"), ns)
        cls.check = staticmethod(ns["is_cross_site_request"])

    def test_same_origin_allowed(self):
        for host, origin in (("localhost:8080", "http://localhost:8080"), ("192.168.1.50:8080", "http://192.168.1.50:8080"),
                             ("a-b-c.trycloudflare.com", "https://a-b-c.trycloudflare.com"), ("[::1]:8080", "http://[::1]:8080")):
            self.assertFalse(self.check({"host": host, "origin": origin}), origin)

    def test_native_clients_allowed(self):
        # APK, programma e curl non inviano Origin
        self.assertFalse(self.check({"host": "localhost:8080"}))
        self.assertFalse(self.check({"host": "localhost:8080", "sec-fetch-site": "none"}))

    def test_other_sites_blocked(self):
        for origin in ("https://evil.example", "http://localhost:3000", "http://localhost.evil.example:8080", "null"):
            self.assertTrue(self.check({"host": "localhost:8080", "origin": origin}), origin)
        self.assertTrue(self.check({"origin": "http://localhost:8080"}))   # senza Host non si può verificare
        self.assertTrue(self.check({"host": "localhost:8080", "sec-fetch-site": "cross-site", "sec-fetch-mode": "no-cors"}))

    def test_links_to_panel_still_open(self):
        # Link al pannello o al download dell'APK da un'altra pagina (es. notifica): navigazione permessa
        self.assertFalse(self.check({"host": "localhost:8080", "sec-fetch-site": "cross-site", "sec-fetch-mode": "navigate"}))
        self.assertFalse(self.check({"host": "localhost:8080", "sec-fetch-site": "cross-site", "sec-fetch-mode": "no-cors"}, False))

    def test_wired_into_http_and_websocket(self):
        self.assertNotIn("CORSMiddleware", self.src)
        self.assertIn("if is_cross_site_request(request.headers", self.src)
        ws = self.src[self.src.index("async def websocket_endpoint"):]
        self.assertLess(ws.index("is_cross_site_request(hdrs)"), ws.index("websocket.accept()"))


class RemoteCertificateTest(unittest.TestCase):
    """Certificato HTTPS dell'accesso da internet: creato una volta, impronta stabile (APK e PC la memorizzano)."""

    def test_certificate_created_once_with_stable_fingerprint(self):
        import ssl
        import hashlib
        d = _sandbox(["remote_access.py"])
        ra = _load(d, "remote_access")
        first = ra.ensure_certificate(os.path.join(d, "certs"))
        second = ra.ensure_certificate(os.path.join(d, "certs"))
        self.assertEqual(first["fingerprint"], second["fingerprint"])
        self.assertRegex(first["fingerprint"], r"^[0-9a-f]{64}$")
        # Stessa impronta calcolata in modo indipendente (come fanno APK e programma PC: SHA-256 del DER)
        with open(first["cert_path"], encoding="ascii") as fh:
            der = ssl.PEM_cert_to_DER_cert(fh.read())
        self.assertEqual(hashlib.sha256(der).hexdigest(), first["fingerprint"])

    def test_router_refusing_8443_uses_fallback_port(self):
        """Box con IPv4 condiviso (iliad): la 8443 dà 718, la 32770 va; porta del PC sempre 8443."""
        d = _sandbox(["remote_access.py"])
        ra_mod = _load(d, "remote_access")

        class Router:
            def __init__(self, allowed):
                self.allowed, self.calls = allowed, []
            def open_port(self, port, internal_port=None, **kw):
                self.calls.append((port, internal_port))
                if port in self.allowed:
                    return {"success": True}
                return {"success": False, "message": "conflitto", "upnp_error": 718}
            def get_external_ip(self):
                return "81.0.0.1"

        saved = []
        router = Router({32770})
        ra = ra_mod.RemoteAccess(d, 8443, router, lambda force: "81.0.0.1", on_external_port=saved.append)
        st = ra.refresh()
        self.assertEqual(st["url"], "https://81.0.0.1:32770")
        self.assertEqual(saved, [32770])
        self.assertTrue(all(internal == 8443 for _, internal in router.calls))
        # Al riavvio si riparte dalla porta ricordata: un solo tentativo, stesso indirizzo
        router2 = Router({32770})
        ra2 = ra_mod.RemoteAccess(d, 8443, router2, lambda force: "81.0.0.1", external_port=32770)
        self.assertEqual(ra2.refresh()["url"], "https://81.0.0.1:32770")
        self.assertEqual([p for p, _ in router2.calls], [32770])

    def test_router_unreachable_does_not_scan_ports(self):
        d = _sandbox(["remote_access.py"])
        ra_mod = _load(d, "remote_access")
        calls = []

        class Router:
            def open_port(self, port, **kw):
                calls.append(port)
                return {"success": False, "message": "Router UPnP non raggiungibile"}
            def get_external_ip(self):
                return None

        ra = ra_mod.RemoteAccess(d, 8443, Router(), lambda force: "81.0.0.1")
        notified = []
        ra.url_subscribers.append(notified.append)
        st = ra.refresh()
        self.assertEqual(calls, [8443])
        self.assertFalse(st["active"])
        self.assertEqual(notified, [], "indirizzo mandato al telefono con la porta chiusa")

    def test_port_change_is_announced_once(self):
        d = _sandbox(["remote_access.py"])
        ra_mod = _load(d, "remote_access")

        class Router:
            allowed = {8443}
            def open_port(self, port, **kw):
                return {"success": True} if port in self.allowed else {"success": False, "upnp_error": 718}
            def get_external_ip(self):
                return "81.0.0.1"

        router = Router()
        ra = ra_mod.RemoteAccess(d, 8443, router, lambda force: "81.0.0.1")
        notified = []
        ra.url_subscribers.append(notified.append)
        ra.refresh(reopen=True)
        ra.refresh(reopen=False)                      # controllo periodico, nulla di nuovo
        router.allowed = {32770}                      # il router non concede più la 8443
        ra.refresh(reopen=False)
        self.assertEqual(notified, ["https://81.0.0.1:8443", "https://81.0.0.1:32770"])

    def test_private_key_never_committed(self):
        with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as fh:
            self.assertIn("certs/", fh.read().split())


class InternetAccessTest(unittest.TestCase):
    """Richieste arrivate dalla porta HTTPS aperta sul router: sempre trattate come internet."""

    @classmethod
    def setUpClass(cls):
        import ast
        with open(os.path.join(ROOT, "app.py"), encoding="utf-8") as fh:
            src = fh.read()
        wanted = ("is_internet_request", "is_local_request", "verify_admin_access", "pin_is_default",
                  "has_admin_access", "hide_private_paths", "is_public_client_ip", "public_network_payload")
        fns = [n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name in wanted]
        cls.config = {"admin_pin": "583921", "require_pin_for_remote": True}

        class HTTPException(Exception):
            def __init__(self, status_code, detail=""):
                self.status_code = status_code

        class Manager:
            config = cls.config

        cls.HTTPException = HTTPException
        cls.ns = {"REMOTE_PORT": 8443, "manager": Manager, "valid_auth_tokens": {"tok"}, "HTTPException": HTTPException,
                  "is_default_pin": is_default_pin, "DEFAULT_PIN_INTERNET_MSG": "", "Request": object,
                  "Dict": dict, "Any": object, "PRIVATE_PATH_KEYS": ("server_dir", "target_path", "cloud_sync_dir")}
        exec(compile(ast.Module(body=fns, type_ignores=[]), "app.py", "exec"), cls.ns)

    def _req(self, port, client="127.0.0.1", headers=None):
        class Req:
            pass
        r = Req()
        r.scope = {"server": ("0.0.0.0", port)}
        r.headers = {"host": f"localhost:{port}", **(headers or {})}
        r.client = type("C", (), {"host": client})()
        return r

    def _status(self, req):
        try:
            self.ns["verify_admin_access"](req)
            return 200
        except self.HTTPException as e:
            return e.status_code

    def setUp(self):
        self.config.update(admin_pin="583921", require_pin_for_remote=True)

    def test_home_network_unchanged(self):
        self.assertEqual(self._status(self._req(8080)), 200)                      # PC Host
        self.assertEqual(self._status(self._req(8080, "192.168.1.20")), 401)      # telefono in casa senza token
        self.config["require_pin_for_remote"] = False
        self.assertEqual(self._status(self._req(8080, "192.168.1.20")), 200)

    def test_internet_always_needs_pin(self):
        self.config["require_pin_for_remote"] = False                              # vale solo per la rete di casa
        self.assertEqual(self._status(self._req(8443, "81.0.0.9")), 401)
        self.assertEqual(self._status(self._req(8443, "127.0.0.1")), 401)         # anche se sembra locale
        self.assertEqual(self._status(self._req(8443, "81.0.0.9", {"X-Admin-Token": "tok"})), 200)

    def test_public_status_hides_pc_paths(self):
        # /api/status, /api/backup/status, /api/storage/breakdown si leggono senza PIN (anche da internet):
        # i percorsi del PC, con il nome utente di Windows, solo a chi ha accesso
        st = {"state": "ONLINE", "server_dir": "C:/Users/x/srv", "cloud_sync_dir": "G:/Drive"}
        hide = self.ns["hide_private_paths"]
        self.assertEqual(hide(dict(st), self._req(8443, "81.0.0.9")), {"state": "ONLINE"})
        self.assertEqual(hide(dict(st), self._req(8080, "192.168.1.20")), {"state": "ONLINE"})
        self.assertEqual(hide(dict(st), self._req(8080)), st)                                  # PC Host
        self.assertEqual(hide(dict(st), self._req(8443, "81.0.0.9", {"X-Admin-Token": "tok"})), st)

    def test_default_pin_blocks_internet(self):
        self.config["admin_pin"] = "1234"
        self.assertEqual(self._status(self._req(8443, "81.0.0.9", {"X-Admin-Token": "tok"})), 403)
        self.assertEqual(self._status(self._req(8080)), 200)                      # in casa funziona

    def test_public_address_on_panel_port_is_internet(self):
        # Porta 8080 aperta per sbaglio sul router (o DMZ): chi arriva da internet non è la rete di casa
        self.config["require_pin_for_remote"] = False
        self.assertEqual(self._status(self._req(8080, "81.0.0.9")), 401)
        self.assertEqual(self._status(self._req(8080, "2a01:e0a:1:2::5")), 401)
        self.assertEqual(self._status(self._req(8080, "81.0.0.9", {"X-Admin-Token": "tok"})), 200)
        self.config["admin_pin"] = "1234"
        self.assertEqual(self._status(self._req(8080, "81.0.0.9", {"X-Admin-Token": "tok"})), 403)
        # Rete di casa, hotspot di Windows, indirizzi locali IPv6 e IPv4 dentro IPv6 restano di casa
        for ip in ("192.168.1.20", "192.168.137.5", "10.0.0.3", "fe80::1", "fd00::5", "::ffff:192.168.1.5"):
            self.assertEqual(self._status(self._req(8080, ip)), 200, ip)
        self.assertFalse(self.ns["is_public_client_ip"]("testclient"))

    def test_public_network_payload_hides_home_network(self):
        full = {"hostname": "DESKTOP-X", "local_ip": "192.168.1.10", "public_ip": "81.0.0.1", "lan_url": "http://192.168.1.10:8080",
                "minecraft": {"public_port": 32769, "direct_address": "81.0.0.1:32769", "lan_address": "192.168.1.10:32769"},
                "apk": {"lan_url": "http://192.168.1.10:8080/api/app/download", "remote_url": "https://81.0.0.1:32770/x"},
                "upnp": {"active": True, "router_ip": "192.168.1.254"}, "remote": {"url": "https://81.0.0.1:32770"}}
        pub = self.ns["public_network_payload"](full)
        self.assertNotIn("192.168.", repr(pub))
        self.assertNotIn("DESKTOP-X", repr(pub))
        self.assertEqual(pub["remote"], {"url": "https://81.0.0.1:32770"})
        self.assertEqual(pub["minecraft"]["direct_address"], "81.0.0.1:32769")


class PinLockoutTest(unittest.TestCase):
    """Blocco per PIN errati: contano solo i PIN diversi, e la risposta dice quanto manca."""

    def setUp(self):
        import ast
        import hashlib
        import threading
        with open(os.path.join(ROOT, "app.py"), encoding="utf-8") as fh:
            src = fh.read()
        wanted = ("verify_pin", "_wait_text", "_too_many_pins")
        fn = [n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name in wanted]
        for f in fn:
            f.decorator_list = []   # senza @app.post: si prova solo la funzione

        class HTTPException(Exception):
            def __init__(self, status_code, detail=""):
                self.status_code, self.detail = status_code, detail

        class JSONResponse:
            def __init__(self, status_code, content, headers=None):
                self.status_code, self.content = status_code, content

        class Manager:
            config = {"admin_pin": "583921"}

        self.clock = [1000.0]
        self.ns = {"HTTPException": HTTPException, "JSONResponse": JSONResponse, "manager": Manager,
                   "_auth_lock": threading.Lock(), "_auth_failed_attempts": {}, "AUTH_LOCK_SECONDS": 600,
                   "AUTH_MAX_WRONG_PINS": 5, "hashlib": hashlib, "valid_auth_tokens": set(),
                   "is_internet_request": lambda r: r.client.host.startswith("81."), "pin_is_default": lambda: False,
                   "remote_access": type("R", (), {"fingerprint": "", "url": ""}), "uuid": __import__("uuid"),
                   "time": type("T", (), {"time": staticmethod(lambda: self.clock[0])}),
                   "VerifyPinRequest": object, "Request": object, "DEFAULT_PIN_INTERNET_MSG": "",
                   "_internet_failed_attempts": [], "AUTH_INTERNET_WINDOW_SECONDS": 3600,
                   "AUTH_INTERNET_MAX_WRONG_PINS": 20, "List": list,
                   "_alert_internet_pin_lock": lambda: self.alerts.append(1), "_host_id": lambda: "0123456789abcdef"}
        self.alerts = []
        exec(compile(ast.Module(body=fn, type_ignores=[]), "app.py", "exec"), self.ns)
        self.HTTPException = HTTPException

    def _try(self, pin, ip="192.168.1.50"):
        req = type("Q", (), {"pin": pin})()
        request = type("Rq", (), {"client": type("C", (), {"host": ip})()})()
        try:
            res = self.ns["verify_pin"](req, request)
            return getattr(res, "status_code", 200), res
        except self.HTTPException as e:
            return e.status_code, e

    def test_same_wrong_pin_does_not_lock(self):
        for _ in range(12):   # l'app che ritenta il vecchio PIN salvato
            self.assertEqual(self._try("1234")[0], 403)
        code, res = self._try("583921")
        self.assertEqual(code, 200)
        self.assertEqual(res["host_id"], "0123456789abcdef")   # l'app lo usa per riconoscere il PC all'indirizzo di casa

    def test_different_wrong_pins_lock_with_remaining_time(self):
        for pin in ("111111", "222222", "333333", "444444", "555555"):
            self.assertEqual(self._try(pin)[0], 403)
        self.clock[0] += 170   # 2 minuti e 50 secondi dopo il primo errore
        code, res = self._try("583921")
        self.assertEqual(code, 429)                       # durante il blocco neanche il PIN giusto
        self.assertEqual(res.content["retry_after"], 431)
        self.assertIn("7 minuti e 11 secondi", res.content["detail"])
        self.clock[0] += 431
        self.assertEqual(self._try("583921")[0], 200)     # finito il blocco si entra

    def test_many_internet_addresses_lock_internet_only(self):
        # Botnet: ogni indirizzo prova un solo PIN, quindi il limite per indirizzo non scatta mai
        for i in range(20):
            self.assertEqual(self._try(f"{100000 + i}", ip=f"81.0.0.{i}")[0], 403)
        self.assertEqual(self.alerts, [1])                       # avviso al telefono una sola volta
        code, res = self._try("583921", ip="81.0.9.9")
        self.assertEqual(code, 429)                              # neanche il PIN giusto da internet
        self.assertIn("in casa funziona", res.content["detail"])
        self.assertEqual(self._try("583921")[0], 200)            # dalla rete di casa si entra
        self.assertEqual(self._try("999999", ip="81.0.9.9")[0], 429)
        self.assertEqual(self.alerts, [1])
        self.clock[0] += 3601
        self.assertEqual(self._try("583921", ip="81.0.9.9")[0], 200)   # dopo un'ora si torna a entrare

    def test_home_wrong_pins_do_not_count_for_internet(self):
        for i in range(30):
            self._try(f"{200000 + i}", ip=f"192.168.1.{i + 2}")
        self.assertEqual(self._try("583921", ip="81.0.9.9")[0], 200)
        self.assertEqual(self.alerts, [])


class RenamedReleaseRepoTest(unittest.TestCase):
    """Repository pubblico rinominato in Voxarium (27/10/2026): i PC già installati devono continuare ad aggiornarsi."""

    def test_old_and_new_repo_names(self):
        import pc_updater
        import pc_version
        self.assertEqual(pc_version.UPDATE_REPO, "QMon31/Voxarium")
        old = "https://github.com/QMon31/Controllo-Server-Minecraft-Releases/releases/download/v1-b1/Setup_Voxarium.exe"
        new = "https://github.com/QMon31/Voxarium/releases/download/v1-b1/Setup_Voxarium.exe"
        self.assertTrue(old.startswith(pc_updater.SETUP_URL_PREFIXES))
        self.assertTrue(new.startswith(pc_updater.SETUP_URL_PREFIXES))
        self.assertFalse("https://github.com/altro/repo/releases/download/x.exe".startswith(pc_updater.SETUP_URL_PREFIXES))
        self.assertIn("/QMon31/Voxarium/releases/latest/download/pc_version.json", pc_updater.MANIFEST_URL)
        # Le release scrivono l'installer con il nome vecchio: i PC fino alla 4.17.1 accettano solo quello
        with open(os.path.join(ROOT, "publish_release.py"), encoding="utf-8") as fh:
            self.assertIn('"setup_url": f"https://github.com/{LEGACY_UPDATE_REPO}/releases/download/', fh.read())


class ReleasePageTest(unittest.TestCase):
    """Pagina pubblica della release: novità senza frasi interne e, sotto, cosa fa il programma (in inglese)."""

    def test_release_body(self):
        import publish_release
        body = publish_release.release_body("Fixed the console. Modifica autorizzata dall'utente.")
        self.assertTrue(body.startswith("## What's new\n\nFixed the console."))
        self.assertNotIn("autorizzata", body)
        self.assertIn("## About Voxarium", body)
        self.assertIn("releases/latest/download/Setup_Voxarium.exe", body)


class AntivirusFriendlyBuildTest(unittest.TestCase):
    """Comportamenti che fanno scattare gli antivirus: niente cmd/netsh nascosti, solo i propri processi chiusi,
    eseguibili con nome, produttore e versione."""

    @classmethod
    def setUpClass(cls):
        def read(name):
            with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
                return fh.read()
        cls.installer, cls.launcher, cls.deploy = read("Installer.cs"), read("Launcher.cs"), read("build_deploy.py")

    def test_update_keeps_remote_host_identity(self):
        # Prima ogni aggiornamento di un PC Remoto cancellava topic, identificativo e impronta dell'Host
        start = self.installer.index("// Write launcher_config.json")
        block = self.installer[start:self.installer.index("File.WriteAllText(launcherCfgPath", start)]
        for key in ("push_topic", "host_id", "cert_fingerprint", "language"):
            self.assertIn(f'\\"{key}\\"', block)
            self.assertIn(f'\\"{key}\\"', self.launcher)
        self.assertIn('ReadConfigString(File.ReadAllText(cfgRemote), "remote_url")', self.installer)

    def test_firewall_without_hidden_shell(self):
        self.assertNotIn("netsh advfirewall", self.installer)
        self.assertIn('"HNetCfg.FwPolicy2"', self.installer)
        self.assertIn("FirewallRules.RequestElevated()", self.installer)

    def test_installer_closes_only_its_own_processes(self):
        start = self.installer.index("public static void AutoCloseRunningApp()")
        body = self.installer[start:self.installer.index("private void StartInstallation()", start)]
        self.assertNotIn('IndexOf("app.py"', body)            # prima: qualsiasi Python con "app.py"
        self.assertNotIn("taskkill", body)
        self.assertNotIn("ngrok", body)
        self.assertIn("ExecutablePath", body)

    def test_executables_have_product_details(self):
        self.assertIn("[assembly: AssemblyTitle(", self.launcher)
        self.assertIn("[assembly: AssemblyTitle(", self.installer)
        self.assertEqual(self.deploy.count("BUILD_INFO_CS,"), 2)   # Launcher e Installer
        for attr in ("AssemblyCompany", "AssemblyProduct", "AssemblyFileVersion"):
            self.assertIn(attr, self.deploy)


class PackageContentsTest(unittest.TestCase):
    """Ogni modulo importato da app.py deve finire nel pacchetto per gli altri PC."""

    def test_local_modules_are_packaged(self):
        with open(os.path.join(ROOT, "create_package.py"), encoding="utf-8") as fh:
            pkg = fh.read()
        with open(os.path.join(ROOT, "app.py"), encoding="utf-8") as fh:
            app_src = fh.read()
        import re
        local = {f[:-3] for f in os.listdir(ROOT) if f.endswith(".py")}
        imported = set(re.findall(r"^(?:from|import)\s+(\w+)", app_src, re.M)) & local
        missing = [m for m in sorted(imported) if f'"{m}.py"' not in pkg]
        self.assertEqual(missing, [], "moduli usati da app.py ma assenti da create_package.py")
        # e ogni modulo del pacchetto va copiato anche nel programma installato su questo PC da build_deploy.py:
        # platform_utils.py mancava, e il pannello di questo PC non partiva più (ModuleNotFoundError)
        with open(os.path.join(ROOT, "build_deploy.py"), encoding="utf-8") as fh:
            deploy = fh.read()
        sync = deploy[deploy.index("FILES_TO_SYNC = ["):]
        sync = sync[:sync.index("]")]
        packaged = re.findall(r'^\s*"(\w+\.py)",', pkg[pkg.index("py_files = ["):], re.M)
        self.assertEqual([f for f in packaged if f'"{f}"' not in sync], [], "moduli del pacchetto assenti da FILES_TO_SYNC")
        # la cartella routes/ (rotte divise per argomento) va nel pacchetto per intero
        self.assertIn('os.listdir("routes")', pkg)
        for f in os.listdir(os.path.join(ROOT, "routes")):
            if f.endswith(".py") and f != "__init__.py":
                self.assertRegex(app_src, rf"from routes import [^\n]*\b{f[:-3]}\b", f)

    def test_route_modules_import_only_packaged_modules(self):
        import re
        with open(os.path.join(ROOT, "create_package.py"), encoding="utf-8") as fh:
            pkg = fh.read()
        local = {f[:-3] for f in os.listdir(ROOT) if f.endswith(".py")}
        for f in os.listdir(os.path.join(ROOT, "routes")):
            if f.endswith(".py"):
                with open(os.path.join(ROOT, "routes", f), encoding="utf-8") as fh:
                    used = set(re.findall(r"^(?:from|import)\s+(\w+)", fh.read(), re.M)) & local
                missing = [m for m in sorted(used) if m != "app" and f'"{m}.py"' not in pkg]
                self.assertEqual(missing, [], f"routes/{f}")

    def test_no_personal_data_in_static(self):
        personal_strings = _personal_strings()
        if not personal_strings:
            self.skipTest("personal_strings.txt non presente su questo PC")
        for root, _, files in os.walk(os.path.join(ROOT, "static")):
            for f in files:
                if f.endswith((".html", ".js", ".css", ".json")):
                    with open(os.path.join(root, f), encoding="utf-8", errors="ignore") as fh:
                        txt = fh.read()
                    for personal in personal_strings:
                        self.assertNotIn(personal, txt, f"{personal} in static/{f}")


class PanelTranslationsTest(unittest.TestCase):
    """Lingue del pannello con chiavi identificative: i18n_it.js è il riferimento (chiave → italiano), ogni lingua del
    selettore ha le stesse chiavi con gli stessi segnaposto, e ogni chiave usata nel codice esiste. Così una scritta
    cambiata o dimenticata ferma la compilazione invece di restare non tradotta senza che nessuno se ne accorga."""

    @staticmethod
    def _read(name):
        with open(os.path.join(ROOT, "static", name), encoding="utf-8") as fh:
            return fh.read()

    def _dict(self, lang):
        src = self._read(f"i18n_{lang}.js")
        pairs = re.findall(r"^\s*'([a-z0-9_.]+)':\s*(\"(?:[^\"\\]|\\.)*\"),\s*$", src, re.M)
        d = {k: json.loads(v) for k, v in pairs}
        self.assertEqual(len(d), len(pairs), f"chiavi ripetute in i18n_{lang}.js")
        return d

    def _langs(self):
        return re.findall(r"(\w\w): '", re.search(r"const LANGS = \{([^}]*)\}", self._read("i18n.js")).group(1))

    @staticmethod
    def _slots(text):
        return sorted(set(re.findall(r"\{\d+\}", text)))

    def test_every_language_has_the_same_keys_and_placeholders(self):
        langs = self._langs()
        self.assertIn("it", langs)
        it = self._dict("it")
        self.assertGreater(len(it), 1500)
        for key, text in it.items():
            # segnaposto numerati in ordine: t('chiave', a, b) mette a in {0}, b in {1}
            self.assertEqual(self._slots(text), ["{%d}" % i for i in range(len(self._slots(text)))], key)
        for lang in langs:
            if lang == "it":
                continue
            d = self._dict(lang)
            self.assertEqual(set(d) - set(it), set(), f"{lang}: chiavi che non esistono in italiano")
            self.assertEqual(set(it) - set(d), set(), f"{lang}: chiavi senza traduzione")
            for key, out in d.items():
                self.assertTrue(out.strip(), f"{lang}: {key} vuota")
                self.assertEqual(self._slots(it[key]), self._slots(out), f"{lang}: {key}")

    def test_keys_used_in_the_panel_exist(self):
        it = self._dict("it")
        html = self._read("index.html")
        used = re.findall(r'data-i18n(?:-title|-placeholder|-aria)?="([^"]+)"', html)
        self.assertGreater(len(used), 800)
        for key in used:
            self.assertIn(key, it, f"index.html: {key}")
        js = self._read("app.js")
        calls = re.findall(r"\b(t|th)\('([a-z0-9_.]+)'", js)
        self.assertGreater(len(calls), 1200)
        for _, key in calls:
            self.assertIn(key, it, f"app.js: {key}")
        # ogni chiave del pannello (non quelle dei testi mandati dal PC né delle note) è usata da qualche parte
        used_all = set(used) | {k for _, k in calls}
        js_i18n = self._read("i18n.js")
        unused = [k for k in it if not k.startswith(("pc.", "notes.", "tpl.", "defaults.")) and k not in used_all
                  and f"'{k}'" not in js_i18n]
        self.assertLess(len(unused), 400, unused[:20])

    def test_dictionaries_load_before_the_panel(self):
        html = self._read("index.html")
        self.assertLess(html.index('src="i18n_it.js"'), html.index('src="i18n.js"'))
        self.assertLess(html.index('src="i18n.js"'), html.index('src="app.js"'))


class InstallerTranslationsTest(unittest.TestCase):
    """Tabella delle lingue dell'installer (InstallerI18n.cs): 6 colonne, stessi {0}, nessun testo di I18n.T dimenticato."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "InstallerI18n.cs"), encoding="utf-8") as fh:
            src = fh.read()
        table = src[src.index("static readonly string[][] Rows"):src.index("static Dictionary<string, string[]> exact")]
        cls.rows = [[json.loads(s) for s in re.findall(r'"(?:[^"\\]|\\.)*"', block)]
                    for block in re.findall(r"new\[\] \{(.*?)\},\s*$", table, re.S | re.M)]
        with open(os.path.join(ROOT, "Installer.cs"), encoding="utf-8") as fh:
            cls.installer = fh.read()

    def test_rows_are_complete(self):
        self.assertGreater(len(self.rows), 80)
        for row in self.rows:
            self.assertEqual(len(row), 6, row[0])
            for out in row[1:]:
                self.assertTrue(out, row[0])
                self.assertEqual(sorted(re.findall(r"\{\d\}", row[0])), sorted(re.findall(r"\{\d\}", out)), row[0])
        keys = [r[0] for r in self.rows]
        self.assertEqual(len(keys), len(set(keys)))

    def test_texts_passed_to_translation_are_in_the_table(self):
        keys = {r[0] for r in self.rows}
        for text in re.findall(r'I18n\.T\(("(?:[^"\\]|\\.)*")\)', self.installer):
            self.assertIn(json.loads(text), keys)

    def test_uninstaller_texts_are_safe_in_batch(self):
        start = self.rows.index(next(r for r in self.rows if r[0] == "DISINSTALLAZIONE VOXARIUM"))
        for row in self.rows[start:]:
            for out in row:
                self.assertFalse(set(out) & set("&|<>^%\""), out)


class PanelAssetVersionTest(unittest.TestCase):
    """CSS e JS del pannello con ?v=<data di modifica>: dopo un aggiornamento non restano copie vecchie in cache."""

    def test_versions_follow_file_dates(self):
        import ast
        import re
        with open(os.path.join(ROOT, "app.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        nodes = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name == "_with_asset_versions")
                 or (isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "_ASSET_REF_RE")]
        ns = {"re": re, "os": os, "STATIC_DIR": os.path.join(ROOT, "static"), "PC_BUILD": 0}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "app.py", "exec"), ns)
        with open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8") as fh:
            html = ns["_with_asset_versions"](fh.read())
        for f in ("app.js", "i18n.js", "style.css", "theme.css"):
            ver = int(os.path.getmtime(os.path.join(ROOT, "static", f)))
            self.assertIn(f'{f}?v={ver}"', html, f)


class SecretsNotOnDriveTest(unittest.TestCase):
    """config.json e instances.json (PIN, push_secret, elenco server): un'unica copia, quella installata."""

    def test_build_deploy_never_copies_config(self):
        # Né su Drive né sopra la configurazione del programma installato (sovrascriveva il PIN cambiato dal pannello)
        with open(os.path.join(ROOT, "build_deploy.py"), encoding="utf-8") as fh:
            src = fh.read()
        sync = src[src.index("FILES_TO_SYNC = ["):src.index("]", src.index("FILES_TO_SYNC = ["))]
        self.assertNotIn('"config.json"', sync)
        self.assertNotIn('"instances.json"', sync)

    def test_build_deploy_restarts_installed_backend(self):
        with open(os.path.join(ROOT, "build_deploy.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("app_target_dir = LOCAL_APP_DIR if installed else BASE_DIR", src)


class UpnpDiscoveryInterfaceTest(unittest.TestCase):
    """Con più reti attive (hotspot di Windows) la ricerca del router deve partire dalla scheda verso internet."""

    def test_ssdp_sent_from_lan_interface(self):
        import socket as real_socket
        import upnp_manager
        calls = []

        class FakeSock:
            def __init__(self, *a):
                pass

            def settimeout(self, t):
                pass

            def bind(self, addr):
                calls.append(("bind", addr[0]))

            def setsockopt(self, level, opt, val):
                if opt == real_socket.IP_MULTICAST_IF:
                    calls.append(("multicast_if", real_socket.inet_ntoa(val)))

            def sendto(self, data, addr):
                calls.append(("sendto", addr[0]))

            def recvfrom(self, n):
                raise real_socket.timeout()

            def close(self):
                pass

        m = upnp_manager.UPnPManager()
        m._get_local_ip_for_target = lambda *a: "192.168.1.50"
        real = upnp_manager.socket.socket
        upnp_manager.socket.socket = FakeSock
        try:
            self.assertFalse(m.discover_gateway(timeout=0.1))
        finally:
            upnp_manager.socket.socket = real
        self.assertEqual(calls[:3], [("bind", "192.168.1.50"), ("multicast_if", "192.168.1.50"), ("sendto", "239.255.255.250")])


@unittest.skipUnless(sys.platform == "win32", "avvio automatico dal registro: solo Windows (su Linux è systemd)")
class AutostartTest(unittest.TestCase):
    """Avvio automatico solo dalla voce del registro: i collegamenti in Esecuzione automatica (vecchio nome e Voxarium)
    vanno tolti, altrimenti con l'avvio spento dal pannello il programma partiva lo stesso."""

    def _run(self, enable):
        import ast
        import types
        with open(os.path.join(ROOT, "app.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        names = {"RUN_KEY_PATH", "APP_RUN_NAME", "STARTUP_SHORTCUT_NAMES"}
        nodes = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name == "set_windows_autostart")
                 or (isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in names)]
        reg = {}
        fake = types.ModuleType("winreg")
        fake.HKEY_CURRENT_USER, fake.KEY_SET_VALUE, fake.REG_SZ = 1, 2, 1

        class Key:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        fake.OpenKey = lambda *a: Key()
        fake.SetValueEx = lambda key, name, res, typ, val: reg.__setitem__(name, val)
        fake.DeleteValue = lambda key, name: reg.pop(name)
        appdata = tempfile.mkdtemp(prefix="csm_appdata_")
        startup = os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
        os.makedirs(startup)
        for f in ("Controllo Server Minecraft.lnk", "Voxarium.lnk", "Altro programma.lnk"):
            open(os.path.join(startup, f), "w").close()
        real_winreg, real_appdata = sys.modules.get("winreg"), os.environ.get("APPDATA")
        sys.modules["winreg"] = fake
        os.environ["APPDATA"] = appdata
        try:
            ns = {"os": os, "sys": types.SimpleNamespace(platform="win32"), "__file__": os.path.join(ROOT, "app.py")}
            exec(compile(ast.Module(body=nodes, type_ignores=[]), "app.py", "exec"), ns)
            ok = ns["set_windows_autostart"](enable)
            return ok, reg, sorted(os.listdir(startup))
        finally:
            if real_winreg is not None:
                sys.modules["winreg"] = real_winreg
            else:
                sys.modules.pop("winreg", None)
            if real_appdata is not None:
                os.environ["APPDATA"] = real_appdata
            shutil.rmtree(appdata, ignore_errors=True)

    def test_disable_removes_all_startup_shortcuts(self):
        ok, reg, left = self._run(False)
        self.assertTrue(ok)
        self.assertEqual(left, ["Altro programma.lnk"])

    def test_enable_uses_registry_only(self):
        ok, reg, left = self._run(True)
        self.assertTrue(ok)
        self.assertIn("ControlloServerMinecraft", reg)
        self.assertEqual(left, ["Altro programma.lnk"])

    def test_installer_no_longer_creates_startup_shortcut(self):
        with open(os.path.join(ROOT, "Installer.cs"), encoding="utf-8") as fh:
            src = fh.read()
        block = src[src.index("// 3. Auto-start on Windows Boot"):src.index("// 4. Uninstaller Script and Registry")]
        self.assertNotIn("CreateShortcut(", block)
        self.assertEqual(block.count("RemoveStartupShortcuts();"), 3)  # aggiornamento, avvio acceso, avvio spento
        self.assertIn("Voxarium*.lnk", src)


class ModUpdatesOfflineTest(unittest.TestCase):
    """Con Modrinth irraggiungibile il pannello non deve dire che le mod sono tutte aggiornate."""

    def test_network_error_is_reported(self):
        d = _sandbox(["modrinth_store.py", "file_manager.py"])
        srv = os.path.join(d, "srv")
        os.makedirs(os.path.join(srv, "mods"))
        with open(os.path.join(srv, "mods", "prova-1.0.jar"), "wb") as fh:
            fh.write(b"jar di prova")
        sys.path.insert(0, d)
        try:
            ms = _load(d, "modrinth_store")
            def _offline(*a, **k):
                raise OSError("rete assente")
            real_urlopen = ms.urllib.request.urlopen   # modulo condiviso: va rimesso a posto
            ms.urllib.request.urlopen = _offline
            res = ms.check_installed_mods_updates(srv, loader="forge", mc_version="1.20.1", force=True)
            self.assertFalse(res["success"])
            self.assertIn("Modrinth", res["error"])
            self.assertNotIn(srv, ms.MOD_UPDATES_CACHE)
            self.assertFalse(ms.update_all_mods(srv, loader="forge", mc_version="1.20.1")["success"])
        finally:
            ms.urllib.request.urlopen = real_urlopen
            sys.path.remove(d)
            shutil.rmtree(d, ignore_errors=True)



def _jar(path, files):
    import zipfile
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)


class ModInfoTest(unittest.TestCase):
    """Scheda di una mod: dati letti dal .jar e, con Modrinth raggiungibile, quelli del progetto."""

    def setUp(self):
        self.d = _sandbox(["mod_info.py", "modrinth_store.py"])
        sys.path.insert(0, self.d)
        self.mi = _load(self.d, "mod_info")

    def tearDown(self):
        sys.path.remove(self.d)
        shutil.rmtree(self.d, ignore_errors=True)

    def test_forge_mods_toml_with_jar_version(self):
        jar = os.path.join(self.d, "jei.jar")
        _jar(jar, {
            "META-INF/MANIFEST.MF": "Manifest-Version: 1.0\nImplementation-Version: 15.2.0.27\n",
            "META-INF/mods.toml": 'license="MIT"\n[[mods]]\nmodId="jei"\nversion="${file.jarVersion}"\n'
                                  'displayName="Just Enough Items"\nauthors="mezz"\nlogoFile="logo.png"\n'
                                  '[[dependencies.jei]]\nmodId="forge"\nmandatory=true\nversionRange="[47,)"\n',
            "logo.png": b"\x89PNG\r\n\x1a\n",
        })
        meta = self.mi.read_jar_metadata(jar)
        self.assertEqual((meta["loader"], meta["name"], meta["version"]), ("forge", "Just Enough Items", "15.2.0.27"))
        self.assertEqual(meta["authors"], ["mezz"])
        self.assertTrue(meta["icon"].startswith("data:image/png;base64,"))
        self.assertEqual(meta["dependencies"][0]["id"], "forge")

    def test_fabric_and_unknown_jar(self):
        fabric = os.path.join(self.d, "sodium.jar")
        _jar(fabric, {"fabric.mod.json": json.dumps({"id": "sodium", "name": "Sodium", "version": "0.5.11",
                                                     "authors": [{"name": "JellySquid"}, "IMS"],
                                                     "depends": {"fabric-api": "*"}})})
        meta = self.mi.read_jar_metadata(fabric)
        self.assertEqual((meta["loader"], meta["name"], meta["authors"]), ("fabric", "Sodium", ["JellySquid", "IMS"]))
        other = os.path.join(self.d, "altro.jar")
        _jar(other, {"a/B.class": b"x"})
        self.assertEqual(self.mi.read_jar_metadata(other), {})
        self.assertEqual(self.mi.detect_loader_from_mods(self.d), "fabric")

    def test_modrinth_lookup_and_offline(self):
        jar = os.path.join(self.d, "m.jar")
        _jar(jar, {"a/B.class": b"x"})
        answers = [{"project_id": "AANobbMI", "version_number": "1.2", "game_versions": ["1.21.1"], "loaders": ["fabric"]},
                   {"slug": "sodium", "project_type": "mod", "title": "Sodium", "downloads": 10, "server_side": "unsupported"}]

        class _Resp:
            def __init__(self, data): self.data = json.dumps(data).encode()
            def read(self): return self.data
            def __enter__(self): return self
            def __exit__(self, *a): return False

        real_urlopen = self.mi.urllib.request.urlopen   # modulo condiviso: va rimesso a posto
        try:
            self.mi.urllib.request.urlopen = lambda req, timeout=0: _Resp(answers.pop(0))
            info = self.mi.get_mod_info(jar)
            self.assertEqual(info["modrinth"]["url"], "https://modrinth.com/mod/sodium")
            self.assertEqual(info["modrinth"]["server_side"], "unsupported")
            def _offline(*a, **k):
                raise OSError("rete assente")
            self.mi.urllib.request.urlopen = _offline
            self.mi._MODRINTH_CACHE.clear()
            self.assertEqual(self.mi.get_mod_info(jar)["modrinth"], {})
        finally:
            self.mi.urllib.request.urlopen = real_urlopen


class FirstInstallNamesTest(unittest.TestCase):
    """Prima installazione: nome, tipo e versione vengono dal server scelto, mai valori di un altro server."""

    def test_detection_without_clues_is_empty(self):
        d = _sandbox(["instance_manager.py", "mod_info.py", "modrinth_store.py"])
        sys.path.insert(0, d)
        try:
            im = _load(d, "instance_manager")
            srv = os.path.join(d, "Il Mio Server")
            os.makedirs(srv)
            res = im.detect_server_details(srv)
            self.assertEqual((res["suggested_name"], res["server_type"], res["mc_version"]), ("Il Mio Server", "", ""))
            with open(os.path.join(srv, "server.properties"), "w", encoding="utf-8") as fh:
                fh.write("motd=Amici del Sabato\nserver-port=25570\n")
            os.makedirs(os.path.join(srv, "mods"))
            _jar(os.path.join(srv, "mods", "x.jar"), {"fabric.mod.json": json.dumps({"id": "x"})})
            res = im.detect_server_details(srv)
            self.assertEqual((res["suggested_name"], res["server_type"], res["mc_version"], res["server_port"]),
                             ("Amici del Sabato (Fabric)", "fabric", "", 25570))
        finally:
            sys.path.remove(d)
            shutil.rmtree(d, ignore_errors=True)

    def test_no_forge_defaults_in_source(self):
        for name in ("instance_manager.py", "server_manager.py", "routes/instances.py", "routes/store.py"):
            with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
                src = fh.read()
            for invented in ('"Server Principale",', "forge/1.20.1-47.2.21/win_args", '"server_type", "forge"'):
                self.assertFalse(invented in src, f"{invented} in {name}")



class PanelFixesOct26Test(unittest.TestCase):
    """Correzioni del 26/10: RAM, porta, file manager senza server, primo server attivo, UPnP, Store."""
    FILES = ["server_manager.py", "instance_manager.py", "mc_ping.py", "platform_utils.py", "file_manager.py"]

    def setUp(self):
        self.d = _sandbox(self.FILES, instances=[], config={"server_dir": ""})
        self.srv = os.path.join(self.d, "srv")
        os.makedirs(self.srv)
        self.sm = _load(self.d, "server_manager")
        # set_server_port importa instance_manager: deve essere quello della cartella di prova, non quello vero
        self.im = _load(self.d, "instance_manager")
        self._old_im = sys.modules.get("instance_manager")
        sys.modules["instance_manager"] = self.im

    def tearDown(self):
        if self._old_im is not None:
            sys.modules["instance_manager"] = self._old_im
        else:
            sys.modules.pop("instance_manager", None)
        shutil.rmtree(self.d, ignore_errors=True)

    def _write(self, name, text):
        with open(os.path.join(self.srv, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def _read(self, name):
        with open(os.path.join(self.srv, name), encoding="utf-8") as fh:
            return fh.read()

    def _manager(self):
        m = self.sm.ServerManager(config_path=os.path.join(self.d, "config.json"))
        m.config["server_dir"] = self.srv
        m.config["server_run_bat"] = os.path.join(self.srv, "run.bat")
        return m

    def test_ram_forge_saved_and_read_back_at_once(self):
        self._write("run.bat", "@echo off\njava @user_jvm_args.txt @libraries/x/win_args.txt %*\npause\n")
        self._write("user_jvm_args.txt", "-Xms16G\n-Xmx16G\n")
        m = self._manager()
        self.assertEqual(m.get_configured_max_ram_gb(), 16.0)
        self.assertEqual(m.save_jvm_tuning(8, True)["status"], "success")
        self.assertIn("-Xmx8G", self._read("user_jvm_args.txt"))
        # Prima il massimo restava 16 GB in memoria fino al riavvio del programma
        self.assertEqual(m.get_configured_max_ram_gb(), 8.0)
        self.assertEqual(m.get_jvm_tuning()["ram_gb"], 8)
        # Un valore impossibile è rifiutato (prima diventava 16 GB senza dirlo)
        self.assertEqual(m.save_jvm_tuning(100000, True)["status"], "error")
        self.assertIn("-Xmx8G", self._read("user_jvm_args.txt"))

    def test_ram_vanilla_run_bat(self):
        self._write("run.bat", "@echo off\njava -Xms4G -Xmx4G -jar server.jar %*\npause\n")
        m = self._manager()
        self.assertEqual(m.get_configured_max_ram_gb(), 4.0)
        self.assertEqual(m.save_jvm_tuning(6, True)["status"], "success")
        self.assertEqual(m.save_jvm_tuning(6, True)["status"], "success")   # due volte: niente flag doppie
        line = [l for l in self._read("run.bat").splitlines() if l.startswith("java ")][0]
        self.assertTrue(line.startswith("java -Xms6G -Xmx6G -XX:+UseG1GC"))
        self.assertEqual(line.count("-XX:+UseG1GC"), 1)
        self.assertTrue(line.endswith("-jar server.jar %*"))
        self.assertFalse(os.path.exists(os.path.join(self.srv, "user_jvm_args.txt")))
        self.assertEqual(m.get_configured_max_ram_gb(), 6.0)

    def test_port_written_to_server_properties(self):
        self._write("server.properties", "motd=Ciao\nserver-port=32769\nmax-players=10\n")
        m = self._manager()
        res = m.set_server_port(32800)
        self.assertEqual(res["status"], "success")
        props = self._read("server.properties")
        self.assertIn("server-port=32800", props)
        self.assertIn("motd=Ciao", props)
        self.assertIn("max-players=10", props)
        self.assertEqual(m.get_server_port(), 32800)
        self.assertEqual(m.config["server_port"], 32800)

    def test_chat_with_semicolon_reaches_console(self):
        import io
        m = self._manager()

        class Proc:
            stdin = io.StringIO()
            def poll(self): return None

        m.process = Proc()
        # Prima ; & $ | facevano scartare il messaggio in silenzio (non c'è nessuna shell di mezzo)
        self.assertEqual(m.send_command('say alle 21; ci siete? & co')['status'], 'success')
        self.assertEqual(m.send_command('say a\nstop')['status'], 'success')
        sent = m.process.stdin.getvalue().splitlines()
        self.assertEqual(sent, ['say alle 21; ci siete? & co', 'say astop'])   # mai due comandi da uno

    def test_rcon_enabled_for_commands_after_panel_restart(self):
        # Server nuovo (RCON spento) avviato fuori dal pannello: il comando non arriva, ma ora lo si vede in console
        # e RCON viene attivato per il prossimo avvio
        self._write("server.properties", "motd=Ciao\nenable-rcon=false\nrcon.password=\nserver-port=32769\n")
        m = self._manager()

        class Alive:
            pid = 999999
            def is_running(self): return True

        m.psutil_proc = Alive()
        res = m.send_command("list")
        self.assertEqual(res["status"], "error")
        self.assertIn("riavvia il server", res["message"])
        self.assertTrue(any("Comando non inviato" in l for l in m.log_buffer))
        props = self._read("server.properties")
        self.assertIn("enable-rcon=true", props)
        self.assertRegex(props, r"rcon\.password=[0-9a-f]{32}")
        self.assertIn("motd=Ciao", props)
        self.assertIn("server-port=32769", props)
        # Già attivo: non si tocca (la password scelta dall'utente resta)
        self.assertFalse(m.ensure_rcon_enabled())

    def test_console_lines_since(self):
        # Il pannello senza WebSocket chiede solo le righe nuove della console (prima restava ferma)
        m = self._manager()
        m.add_log("[Pannello] uno")
        first = m.logs_since(0)
        self.assertTrue(first["reset"])
        seq = first["seq"]
        m.add_log("> list")
        m.add_log("There are 0 of a max of 20 players online")
        nxt = m.logs_since(seq)
        self.assertFalse(nxt["reset"])
        self.assertEqual([l.split("] ", 1)[-1] for l in nxt["lines"]], ["> list", "There are 0 of a max of 20 players online"])
        self.assertEqual(m.logs_since(nxt["seq"])["lines"], [])
        m.log_buffer.clear()   # console svuotata: il pannello riparte da capo
        self.assertTrue(m.logs_since(nxt["seq"] - 1)["reset"])

    def test_backup_size_estimate(self):
        # Il backup salva sempre tutta la cartella: il peso previsto è quello dei file che finiranno nello ZIP
        shutil.copy(os.path.join(ROOT, "backup_manager.py"), self.d)
        bm = _load(self.d, "backup_manager")
        for rel, size in {"world/region/r.0.0.mca": 300, "world/data/DistantHorizons.sqlite": 500,
                          "mods/a.jar": 200, "logs/latest.log": 10, "backups/vecchio.zip": 9999,
                          "world/session.lock": 5}.items():
            os.makedirs(os.path.dirname(os.path.join(self.srv, rel)), exist_ok=True)
            with open(os.path.join(self.srv, rel), "wb") as fh:
                fh.write(b"x" * size)
        desk = os.path.join(self.d, "desk")
        os.makedirs(desk)
        mgr = bm.BackupManager(self.srv, desktop_dir=desk)
        e = mgr.estimate_backup()
        self.assertEqual((e["total_bytes"], e["files"]), (1010, 4))   # niente backups/ né session.lock
        self.assertTrue(e["desktop_fits"])
        self.assertFalse(e["cloud_configured"])
        mgr.cloud_sync_dir = os.path.join(self.d, "drive_spento")      # Drive non avviato: cartella assente
        e = mgr.estimate_backup()
        self.assertTrue(e["cloud_configured"])
        self.assertFalse(e["cloud_available"])
        # Il backup vero contiene esattamente quei file (DistantHorizons compreso)
        import zipfile
        self.assertEqual(mgr.start_backup(True)["status"], "success")
        mgr._worker_thread.join(30)
        with zipfile.ZipFile(mgr.target_path) as z:
            packed = sum(i.file_size for i in z.infolist() if i.filename != bm.BACKUP_INFO_NAME)
            self.assertIn("world/data/DistantHorizons.sqlite", z.namelist())
        self.assertEqual(packed, e["total_bytes"])

    def test_file_manager_without_server(self):
        fm = _load(self.d, "file_manager")
        res = fm.list_directory("")
        self.assertFalse(res["success"])
        self.assertIn("Nessun server", res["error"])

    def test_first_server_becomes_active(self):
        res = self.im.add_instance(name="Mio", server_dir=self.srv)
        self.assertTrue(res["instance"]["is_active"])
        with open(os.path.join(self.d, "config.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["server_dir"], self.srv)

    def test_upnp_minecraft_port_fallback(self):
        import upnp_manager
        m = upnp_manager.UPnPManager()
        m.external_ip = "81.0.0.1"
        opened, closed = [], []

        def fake_open(port, protocol="TCP", description="", primary=True, internal_port=None):
            if port < 32768:   # come le box iliad: solo porte alte
                return {"success": False, "upnp_error": 718, "message": "718"}
            m.mapped_ports.add(port)
            opened.append((port, internal_port))
            return {"success": True}

        def fake_close(port=None, protocol="TCP"):
            m.mapped_ports.discard(port)
            closed.append(port)
            return {"success": True}

        m.open_port, m.close_port = fake_open, fake_close
        res = m.open_minecraft_port(25565, avoid={32770})
        self.assertTrue(res["success"])
        self.assertEqual(opened[-1], (32769, 25565))
        st = m.get_status(25565)
        self.assertTrue(st["active"])
        self.assertEqual((st["external_port"], st["display_address"]), (32769, "81.0.0.1:32769"))
        # Nuova porta del server: la vecchia mappatura viene chiusa
        m.open_minecraft_port(32790, avoid={32770})
        self.assertEqual(opened[-1], (32790, 32790))
        self.assertEqual(closed, [32769])
        self.assertFalse(m.get_status(25565)["active"])

    def test_store_project_info(self):
        shutil.copy(os.path.join(ROOT, "mod_info.py"), self.d)
        shutil.copy(os.path.join(ROOT, "modrinth_store.py"), self.d)
        sys.path.insert(0, self.d)
        mi = _load(self.d, "mod_info")
        answers = [{"id": "AANobbMI", "slug": "sodium", "project_type": "mod", "title": "Sodium", "body": "# Titolo\ntesto",
                    "game_versions": ["1.20.1", "23w13a", "1.21.1"], "license": {"id": "LGPL-3.0"}},
                   [{"role": "Owner", "user": {"username": "jellysquid3"}}]]

        class _Resp:
            def __init__(self, data): self.data = json.dumps(data).encode()
            def read(self): return self.data
            def __enter__(self): return self
            def __exit__(self, *a): return False

        real_urlopen = mi.urllib.request.urlopen
        try:
            mi.urllib.request.urlopen = lambda req, timeout=0: _Resp(answers.pop(0))
            p = mi.get_modrinth_project("AANobbMI")
            self.assertEqual((p["title"], p["license"], p["url"]), ("Sodium", "LGPL-3.0", "https://modrinth.com/mod/sodium"))
            self.assertEqual(p["authors"], ["jellysquid3 (Owner)"])
            self.assertEqual(p["game_versions"], ["1.20.1", "1.21.1"])   # niente snapshot
            with self.assertRaises(ValueError):
                mi.get_modrinth_project("../../etc")
        finally:
            mi.urllib.request.urlopen = real_urlopen
            sys.path.remove(self.d)

    def test_panel_bars_buttons_and_network(self):
        with open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        # Il tema toglie le sfumature (background-image): una barra colorata solo con una sfumatura resta invisibile
        for bar in ("forge-progress-bar", "download-progress-fill", "restore-progress-bar"):
            tag = html[html.index(f'id="{bar}"'):]
            tag = tag[:tag.index(">")]
            self.assertNotIn("gradient", tag, bar)
        with open(os.path.join(ROOT, "static", "theme.css"), encoding="utf-8") as fh:
            self.assertIn(".btn-danger-subtle", fh.read())
        with open(os.path.join(ROOT, "static", "app.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn("msg.type === 'network'", js)      # IP aggiornato senza riavviare il programma
        self.assertNotIn("_initialJvmRam", js)          # RAM confrontata con il valore letto dal server

class StatusServerPortTest(unittest.TestCase):
    """/api/status manda la porta vera del server (server.properties): l'app mostrava sempre la sua predefinita 32769."""

    def test_status_has_real_port(self):
        d = _sandbox(["server_manager.py", "instance_manager.py", "mc_ping.py", "platform_utils.py"], instances=[])
        try:
            srv = os.path.join(d, "srv")
            os.makedirs(srv)
            with open(os.path.join(srv, "server.properties"), "w", encoding="utf-8") as fh:
                fh.write("motd=prova\nserver-port=35565\n")
            with open(os.path.join(d, "config.json"), "w", encoding="utf-8") as fh:
                json.dump({"server_dir": srv}, fh)
            sm = _load(d, "server_manager")
            m = sm.ServerManager(config_path=os.path.join(d, "config.json"))
            self.assertEqual(m.get_full_status()["server_port"], 35565)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class RequirementsTest(unittest.TestCase):
    """Senza una libreria WebSocket uvicorn rifiuta /ws: il pannello perdeva il collegamento in tempo reale sui PC
    senza Python installato (qui funzionava solo grazie al Python personale dell'utente)."""

    def test_websockets_in_requirements_and_runtime_check(self):
        with open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as fh:
            reqs = [l.strip().lower() for l in fh if l.strip()]
        self.assertIn("websockets", reqs)
        with open(os.path.join(ROOT, "build_runtime.py"), encoding="utf-8") as fh:
            self.assertIn("uvicorn, websockets", fh.read())


class McCpuPercentTest(unittest.TestCase):
    """La CPU di Minecraft è in percentuale di tutta la CPU (psutil la dà per un solo core: 466% su 8 core)."""

    def setUp(self):
        self.d = _sandbox(["server_manager.py", "instance_manager.py", "mc_ping.py", "platform_utils.py"], instances=[], config={"server_dir": ""})
        self.sm = _load(self.d, "server_manager")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_cpu_divisa_per_i_core(self):
        class FakeProc:
            pid = 1
            def is_running(self): return True
            def memory_info(self): return types.SimpleNamespace(rss=2 * 1024 ** 3)
            def cpu_percent(self, interval=None): return 400.0
        m = self.sm.ServerManager(config_path=os.path.join(self.d, "config.json"))
        m.psutil_proc = FakeProc()
        m._get_jvm_heap_usage = lambda pid: None
        with mock.patch.object(self.sm.psutil, "cpu_count", return_value=8):
            m._record_metrics_snapshot()
        self.assertEqual(m.metrics_history[-1]["mc_cpu"], 50.0)
        with mock.patch.object(self.sm.psutil, "cpu_count", return_value=2):
            m._record_metrics_snapshot()
        self.assertEqual(m.metrics_history[-1]["mc_cpu"], 100.0)


class ErrorClassificationTest(unittest.TestCase):
    """Scheda Errori: conta il livello vero della riga, non la parola "Error" o un'eccezione citata in un WARN."""

    def setUp(self):
        self.d = _sandbox(["server_manager.py", "instance_manager.py", "mc_ping.py", "platform_utils.py"], instances=[], config={"server_dir": ""})
        self.sm = _load(self.d, "server_manager")
        self.m = self.sm.ServerManager(config_path=os.path.join(self.d, "config.json"))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def levels(self, *lines):
        self.m.errors_list.clear()
        for line in lines:
            self.m._parse_log_line(line)
        return [e["level"] for e in self.m.errors_list]

    def test_avvisi_e_frasi_innocue_non_sono_errori(self):
        self.assertEqual(self.levels(
            "[14:15:16] [main/WARN] [mixin/]: Error loading class: noobanidus/mods/lootr/config/ConfigManager "
            "(java.lang.ClassNotFoundException: noobanidus.mods.lootr.config.ConfigManager)",
            "[14:15:26] [modloading-worker-0/FATAL] [TrustManager/]: Could not determine mod trust worthiness, "
            "Assuming Jar was downloaded from trusted source!",
            "[14:15:30] [Server thread/INFO] [minecraft/MinecraftServer]: Player said [x/ERROR] in chat",
        ), [])

    def test_errori_veri_restano(self):
        self.assertEqual(self.levels(
            "[14:15:39] [main/ERROR] [minecraft/RecipeManager]: Parsing error loading recipe naturalist:frontier_cap",
            "[14:15:41] [Server thread/FATAL]: Something broke",
            "java.lang.NullPointerException: Cannot invoke \"x.m_77762_()\" because \"savedData\" is null",
            "[14:15:49] [Server thread/WARN] [minecraft/MinecraftServer]: Can't keep up! Is the server overloaded? "
            "Running 2847ms or 56 ticks behind",
        ), ["ERROR", "ERROR", "ERROR", "WARN"])


class CloudBackupCheckTest(unittest.TestCase):
    """Avviso se la copia su Google Drive manca, è più vecchia dell'ultimo backup sul Desktop o pesa diversamente."""

    def setUp(self):
        self.d = _sandbox(["backup_manager.py"], instances=[], config={"server_dir": ""})
        bm = _load(self.d, "backup_manager")
        srv, desk, self.drive = (os.path.join(self.d, n) for n in ("srv", "desk", "drive"))
        for x in (srv, desk, self.drive):
            os.makedirs(x)
        self.mgr = bm.BackupManager(srv, desktop_dir=desk)
        self.mgr.cloud_sync_dir = self.drive
        self.desk_zip = self.mgr.target_path
        self.drive_zip = os.path.join(self.drive, self.mgr.target_filename)
        # Backup vero di questo server (con i metadati): un file senza metadati sarebbe di un server sconosciuto
        with zipfile.ZipFile(self.desk_zip, "w") as zf:
            zf.writestr(bm.BACKUP_INFO_NAME, json.dumps({"server_dir": srv}))
            zf.writestr("world/level.dat", b"x" * 4 * 1024 * 1024)
        os.utime(self.desk_zip, (1_000_000, 1_000_000))
        self.assertEqual(self.mgr.target_path, self.desk_zip)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _write(self, path, size, mtime):
        with open(path, "wb") as fh:
            fh.write(b"x" * size)
        os.utime(path, (mtime, mtime))

    def test_controlli(self):
        self.assertEqual(self.mgr.cloud_backup_check()["problem"], "missing")
        self._write(self.drive_zip, 2 * 1024 * 1024, 1_000_000 - 3 * 86400)   # copia di 3 giorni prima
        self.assertEqual(self.mgr.cloud_backup_check()["problem"], "older")
        self._write(self.drive_zip, 2 * 1024 * 1024, 1_000_000)               # stessa data, metà peso
        self.assertEqual(self.mgr.cloud_backup_check()["problem"], "size")
        self._write(self.drive_zip, 4 * 1024 * 1024, 1_000_000)               # copia uguale
        self.assertIsNone(self.mgr.cloud_backup_check())
        self.mgr.cloud_sync_dir = os.path.join(self.d, "drive_spento")       # Drive non raggiungibile: niente avviso
        self.assertIsNone(self.mgr.cloud_backup_check())


class BackupPhasesAndStartGuardTest(unittest.TestCase):
    """Copia su Google Drive con avanzamento fino al 100%, e server che non parte durante backup o ripristino."""

    def setUp(self):
        self.d = _sandbox(["backup_manager.py", "server_manager.py", "instance_manager.py", "mc_ping.py", "platform_utils.py"],
                          instances=[], config={"server_dir": ""})
        self.bm = _load(self.d, "backup_manager")
        self.sm = _load(self.d, "server_manager")
        self.srv, desk, self.drive = (os.path.join(self.d, n) for n in ("srv", "desk", "drive"))
        for x in (self.srv, desk, self.drive):
            os.makedirs(x)
        os.makedirs(os.path.join(self.srv, "world"))
        with open(os.path.join(self.srv, "world", "level.dat"), "wb") as fh:
            fh.write(os.urandom(3 * 1024 * 1024))
        self.mgr = self.bm.BackupManager(self.srv, desktop_dir=desk)
        self.mgr.cloud_sync_dir = self.drive

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_copia_drive_fino_al_100(self):
        phases = []
        self.mgr.subscribe_progress(lambda prog: phases.append((prog.get("phase"), prog.get("percent"))))
        self.assertEqual(self.mgr.start_backup(is_server_stopped=True, copy_to_cloud=True)["status"], "success")
        for _ in range(400):
            if not self.mgr.progress.get("active"):
                break
            time.sleep(0.05)
        p = self.mgr.progress
        self.assertEqual((p["percent"], p["phase"], p["cloud_synced"]), (100, "done", True), p.get("status"))
        self.assertIn("copiato anche su Google Drive", p["status"])
        cloud_pcts = [pct for ph, pct in phases if ph == "cloud"]
        self.assertTrue(cloud_pcts and min(cloud_pcts) >= 50, phases)   # la copia occupa la seconda metà della barra
        self.assertTrue(os.path.isfile(os.path.join(self.drive, self.mgr.target_filename)))
        self.assertFalse(os.path.exists(os.path.join(self.drive, self.mgr.target_filename + ".tmp")))
        self.assertIsNone(self.mgr.cloud_backup_check())

    def test_server_non_parte_durante_backup(self):
        m = self.sm.ServerManager(config_path=os.path.join(self.d, "config.json"))
        m.start_blocker = self.mgr.busy_message
        self.mgr.is_running = True
        self.mgr.progress = {"operation": "backup"}
        res = m.start_server()
        self.assertEqual(res["status"], "error")
        self.assertIn("Backup in corso", res["message"])
        self.mgr.progress = {"operation": "restore"}
        self.assertIn("Ripristino in corso", m.start_server()["message"])
        self.mgr.is_running = False
        self.assertIsNone(self.mgr.busy_message())


class BackupPerServerTest(unittest.TestCase):
    """Più server: ognuno ha il suo backup (nessuno sovrascrive l'altro) e il ripristino di un backup di un altro
    server nel server attivo si fa solo con conferma."""

    def setUp(self):
        self.d = _sandbox(["backup_manager.py"], instances=[], config={"server_dir": ""})
        self.bm = _load(self.d, "backup_manager")
        self.a, self.b, self.desk = (os.path.join(self.d, n) for n in ("Sopravvivenza", "Creativo", "desk"))
        for srv, content in ((self.a, b"mondo A"), (self.b, b"mondo B")):
            os.makedirs(os.path.join(srv, "world"))
            with open(os.path.join(srv, "world", "level.dat"), "wb") as fh:
                fh.write(content)
        os.makedirs(self.desk)
        self.servers = {self.a: {"id": "inst_a", "name": "Sopravvivenza"}, self.b: {"id": "inst_b", "name": "Creativo"}}
        self.mgr = self.bm.BackupManager(self.a, desktop_dir=self.desk)
        self.mgr.server_info_cb = lambda d: dict(self.servers.get(os.path.abspath(d), {}), servers=len(self.servers))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _wait(self):
        for _ in range(400):
            if not self.mgr.is_running:
                break
            time.sleep(0.05)
        self.assertFalse(self.mgr.is_running)

    def _backup(self, srv):
        self.mgr.server_dir = srv
        self.assertEqual(self.mgr.start_backup(is_server_stopped=True)["status"], "success")
        self._wait()
        self.assertEqual(self.mgr.progress["percent"], 100, self.mgr.progress.get("status"))

    def _level(self, srv):
        with open(os.path.join(srv, "world", "level.dat"), "rb") as fh:
            return fh.read()

    def test_un_backup_per_server(self):
        self._backup(self.a)
        self._backup(self.b)
        self.assertEqual(sorted(os.listdir(self.desk)), ["Backup_Creativo.zip", "Backup_Sopravvivenza.zip"])
        info = self.bm.read_backup_info(os.path.join(self.desk, "Backup_Sopravvivenza.zip"))
        self.assertEqual((info["server_id"], info["server_name"]), ("inst_a", "Sopravvivenza"))
        # L'elenco dice di quale server è ognuno, prima quello attivo (ora B)
        lst = self.mgr.list_available_backups()
        self.assertEqual([(x["server_name"], x["same_server"], x["id"]) for x in lst],
                         [("Creativo", True, "desktop"), ("Sopravvivenza", False, "desktop_Backup_Sopravvivenza.zip")])
        self.assertEqual(self.mgr.get_status()["target_filename"], "Backup_Creativo.zip")

    def test_ripristino_di_un_altro_server_chiede_conferma(self):
        self._backup(self.a)
        self.mgr.server_dir = self.b
        src = os.path.join(self.desk, "Backup_Sopravvivenza.zip")
        res = self.mgr.start_restore(is_server_stopped=True, source_path=src)
        self.assertEqual((res["status"], res["code"], res["backup_server"], res["current_server"]),
                         ("error", "other_server", "Sopravvivenza", "Creativo"))
        self.assertEqual(self._level(self.b), b"mondo B")          # nessun file toccato
        # Confermato: si ripristina
        self.assertEqual(self.mgr.start_restore(True, src, confirm_other_server=True)["status"], "success")
        self._wait()
        self.assertEqual(self._level(self.b), b"mondo A")
        # Finito il ripristino lo stato parla di nuovo del file del server attivo, non dell'archivio usato
        self.assertEqual(self.mgr.get_status()["target_filename"], "Backup_Creativo.zip")

    def test_ripristino_nello_stesso_server_senza_domande(self):
        self._backup(self.a)
        with open(os.path.join(self.a, "world", "level.dat"), "wb") as fh:
            fh.write(b"rovinato")
        self.assertEqual(self.mgr.start_restore(True, "desktop")["status"], "success")
        self._wait()
        self.assertEqual(self._level(self.a), b"mondo A")

    def test_backup_vecchio_unico(self):
        # Backup_Server_Mc.zip delle versioni precedenti: resta nell'elenco, non viene sovrascritto,
        # e con più server il ripristino chiede conferma (con un solo server no)
        legacy = os.path.join(self.desk, self.bm.LEGACY_BACKUP_FILENAME)
        with zipfile.ZipFile(legacy, "w") as zf:
            zf.writestr("world/level.dat", b"vecchio")
        self._backup(self.a)
        self.assertTrue(os.path.isfile(legacy))
        lst = self.mgr.list_available_backups()
        self.assertEqual([(x["filename"], x["same_server"]) for x in lst],
                         [("Backup_Sopravvivenza.zip", True), ("Backup_Server_Mc.zip", None)])
        self.assertEqual(self.mgr.start_restore(True, legacy)["code"], "unknown_server")
        del self.servers[self.b]
        self.assertIsNone(self.mgr.restore_check(legacy))
        # Altri zip "Backup_..." dell'utente sul Desktop (non di Voxarium) non compaiono
        with zipfile.ZipFile(os.path.join(self.desk, "Backup_foto.zip"), "w") as zf:
            zf.writestr("a.jpg", b"x")
        self.assertNotIn("Backup_foto.zip", [x["filename"] for x in self.mgr.list_available_backups()])

    def test_due_server_con_lo_stesso_nome(self):
        self.servers[self.b]["name"] = "Sopravvivenza"
        self._backup(self.a)
        self._backup(self.b)
        names = sorted(os.listdir(self.desk))
        self.assertEqual(len(names), 2, names)
        self.assertIn("Backup_Sopravvivenza.zip", names)
        # Il backup di A non è stato toccato
        self.mgr.server_dir = self.a
        self.assertEqual(self.mgr.target_filename, "Backup_Sopravvivenza.zip")

    def test_fine_backup_non_tocca_il_backup_successivo(self):
        # Il backup A finisce (is_running già False) e intanto parte il backup B: la chiusura di A non deve
        # cancellare la destinazione di B
        self.mgr._job_server_dir = self.a
        self.mgr._job_target = os.path.join(self.desk, "Backup_A.zip")

        def job_a(server_dir):
            self.mgr._job_target = os.path.join(self.desk, "Backup_B.zip")   # start_backup di B, a metà strada

        self.mgr._run_backup_job = job_a
        self.mgr._run_backup_worker()
        self.assertEqual(self.mgr._job_target, os.path.join(self.desk, "Backup_B.zip"))
        # Caso normale: nessun backup nuovo, la destinazione si azzera
        self.mgr._run_backup_job = lambda server_dir: None
        self.mgr._run_backup_worker()
        self.assertIsNone(self.mgr._job_target)

    def test_server_rinominato(self):
        self._backup(self.a)
        self.servers[self.a]["name"] = "Sopravvivenza 2"
        self.mgr.server_dir = self.a
        # Il nuovo backup ha il nome nuovo; quello vecchio resta, riconosciuto come dello stesso server (stesso id)
        self.assertEqual(self.mgr.target_filename, "Backup_Sopravvivenza_2.zip")
        old = os.path.join(self.desk, "Backup_Sopravvivenza.zip")
        self.assertIsNone(self.mgr.restore_check(old))
        self.assertEqual({x["filename"]: x["same_server"] for x in self.mgr.list_available_backups()},
                         {"Backup_Sopravvivenza.zip": True})
        # Server tolto e aggiunto di nuovo (id nuovo) sulla stessa cartella: stessi file, nessun avviso
        self.servers[self.a]["id"] = "inst_a_nuovo"
        self.assertIsNone(self.mgr.restore_check(old))

    def test_due_server_con_nomi_che_differiscono_solo_per_maiuscole(self):
        # Su Windows Backup_Test.zip e Backup_test.zip sono lo stesso file
        self.servers[self.a]["name"] = "Test"
        self.servers[self.b]["name"] = "test"
        self._backup(self.a)
        self._backup(self.b)
        self.assertEqual(len(os.listdir(self.desk)), 2, os.listdir(self.desk))
        self.assertEqual(self.bm.read_backup_info(os.path.join(self.desk, "Backup_Test.zip"))["server_id"], "inst_a")

    def test_cambio_server_durante_il_backup(self):
        # La destinazione è fissata all'avvio: il file resta quello del server di cui si fa il backup
        self.mgr.server_dir = self.a
        self.assertEqual(self.mgr.start_backup(is_server_stopped=True)["status"], "success")
        self.mgr.server_dir = self.b
        self._wait()
        self.assertEqual(os.listdir(self.desk), ["Backup_Sopravvivenza.zip"])
        info = self.bm.read_backup_info(os.path.join(self.desk, "Backup_Sopravvivenza.zip"))
        self.assertEqual(info["server_id"], "inst_a")
        self.assertEqual(self.mgr.target_filename, "Backup_Creativo.zip")


class ExistingMethodsTest(unittest.TestCase):
    """Gli indirizzi del programma chiamano solo funzioni che esistono (manager.get_status non esisteva: l'aggiornamento
    delle mod dal negozio finiva sempre con l'errore 500)."""

    def test_calls_to_existing_members(self):
        import ast
        import glob

        def members(path, cls=None):
            tree = ast.parse(open(os.path.join(ROOT, path), encoding="utf-8").read())
            names = set()
            for n in ast.walk(tree):
                if cls is None:
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        names.add(n.name)
                    if isinstance(n, ast.Assign):
                        names.update(t.id for t in n.targets if isinstance(t, ast.Name))
                elif isinstance(n, ast.ClassDef) and n.name == cls:
                    for b in ast.walk(n):
                        if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            names.add(b.name)
                        if isinstance(b, ast.Attribute) and isinstance(b.ctx, ast.Store) \
                                and isinstance(b.value, ast.Name) and b.value.id == "self":
                            names.add(b.attr)
                        if isinstance(b, ast.Assign):
                            names.update(t.id for t in b.targets if isinstance(t, ast.Name))
            return names

        objs = {"manager": members("server_manager.py", "ServerManager"),
                "backup_mgr": members("backup_manager.py", "BackupManager"),
                "remote_access": members("remote_access.py", "RemoteAccess"),
                "upnp_mgr": members("upnp_manager.py", "UPnPManager"),
                "forge_installer": members("server_installer.py", "ForgeServerInstaller"),
                "instance_manager": members("instance_manager.py"), "modrinth_store": members("modrinth_store.py"),
                "pc_updater": members("pc_updater.py"), "file_manager": members("file_manager.py"),
                "mod_info": members("mod_info.py")}
        bad = []
        for f in ["app.py"] + [os.path.relpath(p, ROOT) for p in glob.glob(os.path.join(ROOT, "routes", "*.py"))]:
            src = open(os.path.join(ROOT, f), encoding="utf-8").read()
            for obj, names in objs.items():
                for m in re.finditer(r"\b%s\.(\w+)" % obj, src):
                    if m.group(1) not in names and not m.group(1).startswith("__"):
                        bad.append(f"{f}:{src.count(chr(10), 0, m.start()) + 1} {obj}.{m.group(1)}")
        self.assertEqual(bad, [])


class StopDuringRestartTest(unittest.TestCase):
    """Arresto chiesto durante un riavvio: il riavvio si annulla (prima il server si riaccendeva da solo)."""

    def setUp(self):
        self.d = _sandbox(["server_manager.py", "instance_manager.py", "mc_ping.py", "platform_utils.py"],
                          instances=[], config={"server_dir": ""})
        self.sm = _load(self.d, "server_manager")
        self.m = self.sm.ServerManager(config_path=os.path.join(self.d, "config.json"))
        self.m._find_running_minecraft_proc = lambda: None

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_stop_cancels_pending_restart(self):
        started = []
        self.m.start_server = lambda: started.append(1) or {"status": "success"}
        self.m.state = "RESTARTING"            # vecchio server già chiuso, nuovo avvio tra qualche secondo
        res = self.m.stop_server()
        self.assertEqual((res["status"], self.m.state), ("success", "STOPPED"))
        self.assertIn("Riavvio annullato", res["message"])
        self.sm.query_server_status = lambda **kw: {"online": False}
        self.m._delayed_restart_worker(0)      # il lavoro del riavvio arriva dopo l'arresto
        self.assertEqual(started, [])
        self.assertEqual(self.m.stop_server()["status"], "error")   # ora è davvero già spento

    def test_no_server_configured_adopts_nothing(self):
        # Un server Minecraft di qualcun altro acceso sul PC: senza cartella configurata non è "nostro"
        del self.m._find_running_minecraft_proc
        other = types.SimpleNamespace(info={"pid": 1, "name": "java.exe", "cwd": r"D:\altro\server",
                                            "cmdline": ["java", "-jar", "server.jar", "nogui"]})
        with mock.patch.object(self.sm.psutil, "process_iter", lambda attrs=None: [other]):
            self.m.config["server_dir"] = ""
            self.assertIsNone(self.m._find_running_minecraft_proc())
            self.m.config["server_dir"] = r"D:\altro\server"
            self.assertIs(self.m._find_running_minecraft_proc(), other)

    def test_teleport_to_world_spawn(self):
        # "Teletrasporta allo spawn": coordinate dello spawn del mondo da level.dat (prima "spawnpoint" + "tp 0 100 0"
        # nell'app e "tp giocatore spawn" nel pannello, cioè dal giocatore chiamato "spawn")
        import gzip
        import struct

        def tag(t, name, payload):
            n = name.encode()
            return bytes([t]) + struct.pack(">H", len(n)) + n + payload

        def level(data_tags, folder):
            os.makedirs(folder, exist_ok=True)
            body = tag(10, "", tag(10, "Data", b"".join(data_tags) + b"\x00") + b"\x00")
            with gzip.open(os.path.join(folder, "level.dat"), "wb") as fh:
                fh.write(body)

        srv = os.path.join(self.d, "srv")
        level([tag(3, "SpawnX", struct.pack(">i", -120)), tag(3, "SpawnY", struct.pack(">i", 71)),
               tag(3, "SpawnZ", struct.pack(">i", 48)), tag(8, "LevelName", struct.pack(">H", 5) + b"world")],
              os.path.join(srv, "world"))
        self.assertEqual(self.sm.read_world_spawn(srv), (-120, 71, 48))
        # Mondo con un altro nome e formato dopo la 1.21.4 (gruppo "spawn" con "pos")
        with open(os.path.join(srv, "server.properties"), "w") as fh:
            fh.write("level-name=mondo2\n")
        pos = tag(11, "pos", struct.pack(">i", 3) + struct.pack(">iii", 5, 80, -7))
        level([tag(10, "spawn", pos + b"\x00")], os.path.join(srv, "mondo2"))
        self.assertEqual(self.sm.read_world_spawn(srv), (5, 80, -7))
        self.assertIsNone(self.sm.read_world_spawn(os.path.join(self.d, "nessuno")))

        sent = []
        self.m.send_command = lambda c: sent.append(c) or {"status": "success"}
        self.m.config["server_dir"] = srv
        self.assertEqual(self.m.player_quick_action("Alex", "tp", "spawn")["status"], "success")   # pulsante del pannello
        self.assertEqual(self.m.player_quick_action("Alex", "spawn")["status"], "success")          # app
        self.assertEqual(sent, ["execute in minecraft:overworld run tp Alex 5.5 80 -6.5"] * 2)
        self.m.config["server_dir"] = os.path.join(self.d, "nessuno")
        self.assertEqual(self.m.player_quick_action("Alex", "spawn")["status"], "error")

    def test_online_without_process_goes_stopped(self):
        # ONLINE solo perché qualcosa rispondeva sulla porta; poi sparisce: dopo 3 controlli il server è spento
        self.m.state = "ONLINE"
        answers = iter([{"online": False}] * 2 + [{"online": True, "players_online": 0}] + [{"online": False}] * 3)
        self.sm.query_server_status = lambda **kw: next(answers)
        for _ in range(2):
            self.m._background_slp_query(25565)
        self.assertEqual(self.m.state, "ONLINE")             # due controlli non bastano
        self.m._background_slp_query(25565)                  # risponde di nuovo: si ricomincia a contare
        for _ in range(2):
            self.m._background_slp_query(25565)
        self.assertEqual(self.m.state, "ONLINE")
        self.m._background_slp_query(25565)
        self.assertEqual(self.m.state, "STOPPED")

    def test_restart_still_works(self):
        started = []
        self.m.start_server = lambda: started.append(1) or {"status": "success"}
        self.m.state = "RESTARTING"
        self.sm.query_server_status = lambda **kw: {"online": False}
        self.m._delayed_restart_worker(0)
        self.assertEqual(started, [1])


class StoreInstallVersionTest(unittest.TestCase):
    """Installazione dal negozio delle mod: versione per il server attivo, mai una qualunque se non è compatibile."""

    def setUp(self):
        self.d = _sandbox(["modrinth_store.py", "file_manager.py"], instances=[], config={"server_dir": ""})
        self.ms = _load(self.d, "modrinth_store")
        self.srv = os.path.join(self.d, "srv")
        os.makedirs(self.srv)
        self.urls = []

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _fake(self, versions):
        import io

        def urlopen(req, timeout=0):
            url = req.full_url if hasattr(req, "full_url") else req
            self.urls.append(url)
            if "/version" in url:
                return io.BytesIO(json.dumps(versions).encode())
            return io.BytesIO(b"jar")
        self.ms.urllib.request.urlopen = urlopen

    def test_no_compatible_version_installs_nothing(self):
        self._fake([])
        res = self.ms.install_modrinth_project("P7dR8mSH", self.srv, loader="forge", version="1.20.1")
        self.assertFalse(res["success"])
        self.assertIn("compatibile", res["error"])
        self.assertEqual(len(self.urls), 1)                  # prima seguiva una seconda richiesta senza filtri
        self.assertEqual(os.listdir(os.path.join(self.srv, "mods")), [])

    def test_loader_families(self):
        self._fake([])
        self.ms.install_modrinth_project("x", self.srv, loader="quilt", version="1.21.4")
        self.assertIn("fabric", urllib.parse.unquote(self.urls[-1]))
        self.ms.install_modrinth_project("x", self.srv, loader="paper", version="1.21.4")
        self.assertIn("bukkit", urllib.parse.unquote(self.urls[-1]))
        self.ms.install_modrinth_project("x", self.srv, loader="", version="")
        self.assertNotIn("loaders", self.urls[-1])           # nessun filtro: tutte le versioni

    def test_route_uses_active_server(self):
        src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
        model = src[src.index("class InstallStoreRequest"):src.index("class UninstallStoreRequest")]
        self.assertIn("loader: Optional[str] = None", model)
        self.assertIn("version: Optional[str] = None", model)
        self.assertNotIn("'1.20.1' : version", open(os.path.join(ROOT, "static", "app.js"), encoding="utf-8").read())


class PublicDownloadsTest(unittest.TestCase):
    """README pubblico: Windows e Linux scaricabili uno accanto all'altro; il link Linux segue la sua ultima release."""

    def test_download_block(self):
        import publish_source as ps
        readme = "# Voxarium\n\n## Download\n\n" + ps.DOWNLOAD_START + "\nvecchio\n" + ps.DOWNLOAD_END + "\n\n## Altro\n"
        out = ps.set_download_block(readme, "linux-v4.19.2-b97", "4.19.2", 97)
        self.assertIn("releases/latest/download/Setup_Voxarium.exe", out)
        self.assertIn("releases/download/linux-v4.19.2-b97/voxarium-linux.tar.gz", out)
        self.assertIn("v4.19.2 (Build 97)", out)
        self.assertNotIn("vecchio", out)
        self.assertTrue(out.endswith("\n\n## Altro\n"))
        again = ps.set_download_block(out, "linux-v4.20.0-b98", "4.20.0", 98)
        self.assertNotIn("linux-v4.19.2-b97", again)
        self.assertEqual(again.count(ps.DOWNLOAD_START), 1)
        with self.assertRaises(ValueError):
            ps.set_download_block("# senza sezione", "linux-v1-b1", "1", 1)

    def test_linux_release_updates_readme_and_windows_page_points_to_linux(self):
        with open(os.path.join(ROOT, "publish_linux_release.py"), encoding="utf-8") as fh:
            self.assertIn("update_readme_download(tag", fh.read())
        with open(os.path.join(ROOT, "release_about.md"), encoding="utf-8") as fh:
            self.assertIn("Voxarium#download", fh.read())


if __name__ == "__main__":
    unittest.main()
