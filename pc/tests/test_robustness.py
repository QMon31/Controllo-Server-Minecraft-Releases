"""Test di robustezza: file che non restano mai a metà, configurazione recuperata dopo un salvataggio interrotto,
timer che sopravvive agli errori, copie precedenti dei backup, ripristino che mette da parte il mondo di prima,
token di accesso che scadono. Lavorano solo in cartelle temporanee.

Avvio a mano:  python -m unittest tests.test_robustness -v
"""
import ast
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from typing import Dict, Optional
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import platform_utils  # noqa: E402
from platform_utils import read_json_with_backup, write_json_atomic, write_text_atomic  # noqa: E402


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class AtomicWriteTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "config.json")

    def test_no_tmp_left_and_backup_is_previous_version(self):
        write_json_atomic(self.path, {"v": 1}, keep_backup=True)
        write_json_atomic(self.path, {"v": 2}, keep_backup=True)
        self.assertEqual(json.loads(_read(self.path)), {"v": 2})
        self.assertEqual(json.loads(_read(self.path + ".bak")), {"v": 1})
        self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_broken_file_never_replaces_good_backup(self):
        write_json_atomic(self.path, {"pin": "987654"}, keep_backup=True)
        write_json_atomic(self.path, {"pin": "987654", "x": 1}, keep_backup=True)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write('{"pin": "98')  # salvataggio interrotto
        write_json_atomic(self.path, {"pin": "1234"}, keep_backup=True)
        self.assertEqual(json.loads(_read(self.path + ".bak"))["pin"], "987654")

    def test_failed_write_keeps_old_file(self):
        write_text_atomic(self.path, "vecchio")
        with mock.patch.object(platform_utils.os, "replace", side_effect=OSError("disco pieno")):
            with self.assertRaises(OSError):
                write_text_atomic(self.path, "nuovo")
        self.assertEqual(_read(self.path), "vecchio")

    def test_file_kept_open_is_written_in_place(self):
        # Paper acceso tiene aperto server.properties: lo scambio non riesce mai, la scrittura sì
        write_text_atomic(self.path, "vecchio")
        with mock.patch.object(platform_utils.os, "replace", side_effect=PermissionError("in uso")), \
                mock.patch.object(platform_utils.time, "sleep"):
            write_text_atomic(self.path, "nuovo")
        self.assertEqual(_read(self.path), "nuovo")
        self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_read_broken_json_uses_backup_and_keeps_copy(self):
        write_json_atomic(self.path, {"pin": "987654"}, keep_backup=True)
        write_json_atomic(self.path, {"pin": "987654"}, keep_backup=True)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write('{"pin": "98')
        self.assertEqual(read_json_with_backup(self.path), {"pin": "987654"})
        self.assertEqual(_read(self.path + ".corrotto"), '{"pin": "98')

    def test_first_good_read_creates_backup(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"pin": "987654"}, f)
        read_json_with_backup(self.path)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{")
        self.assertEqual(read_json_with_backup(self.path), {"pin": "987654"})

    def test_read_broken_json_without_backup_raises(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{")
        with self.assertRaises(ValueError):
            read_json_with_backup(self.path)
        self.assertTrue(os.path.isfile(self.path + ".corrotto"))


class ServerManagerRobustnessTest(unittest.TestCase):
    """Il gestore del server con un config.json scritto a metà e con valori sbagliati."""

    @classmethod
    def setUpClass(cls):
        import server_manager
        cls.sm = server_manager

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "config.json")
        self.managers = []

    def tearDown(self):
        for m in self.managers:
            m.running = False

    def _manager(self):
        m = self.sm.ServerManager(config_path=self.path)
        self.managers.append(m)
        return m

    def test_truncated_config_recovered_from_backup(self):
        orig = {"server_dir": "", "admin_pin": "987654", "push_topic": "mc_server_abcdef0123456789",
                "push_secret": "x" * 64, "remote_external_port": 32770}
        write_json_atomic(self.path, orig, keep_backup=True)
        write_json_atomic(self.path, orig, keep_backup=True)
        text = json.dumps(orig, indent=2)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text[: len(text) // 2])
        m = self._manager()
        for k in ("admin_pin", "push_topic", "push_secret", "remote_external_port"):
            self.assertEqual(m.config.get(k), orig[k], k)
        self.assertTrue(os.path.isfile(self.path + ".corrotto"))

    def test_ticker_survives_errors_and_bad_port(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"server_port": "", "push_secret": "y" * 64, "push_topic": "mc_server_0011223344556677"}, f)
        m = self._manager()
        self.assertEqual(m.get_server_port(), 25565)
        calls = []

        def boom(state: Dict[str, int]):
            calls.append(1)
            raise RuntimeError("errore di prova")
        m._ticker_step = boom
        time.sleep(3.5)
        self.assertTrue(m.ticker_thread.is_alive())
        self.assertGreaterEqual(len(calls), 2)
        # Lo stesso errore ripetuto ogni secondo finisce nel log una volta sola
        self.assertEqual(sum("errore di prova" in l for l in m.log_buffer), 1)

    def test_config_file_paths_stay_inside_server_dir(self):
        base = os.path.join(self.dir, "Server")
        os.makedirs(base)
        os.makedirs(os.path.join(self.dir, "Server2"))
        inside = self.sm.ServerManager._inside_dir
        self.assertTrue(inside(base, os.path.join(base, "config", "a.toml")))
        self.assertFalse(inside(base, os.path.join(self.dir, "Server2", "a.toml")))  # stesso inizio del nome
        self.assertFalse(inside("", os.path.join(base, "a")))
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"server_dir": base, "push_secret": "z" * 64, "push_topic": "mc_server_0011223344556677"}, f)
        m = self._manager()
        res = m.save_config_file(os.path.join("..", "Server2", "x.txt"), "ciao")
        self.assertEqual(res["status"], "error")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "Server2", "x.txt")))

    def test_disk_space_of_server_disk_never_invented(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"server_dir": self.dir, "push_secret": "z" * 64, "push_topic": "mc_server_0011223344556677"}, f)
        m = self._manager()
        m._cached_disk_tuple = None
        m._record_metrics_snapshot()
        free, total, _pct = m._cached_disk_tuple
        real = shutil.disk_usage(self.dir)
        self.assertAlmostEqual(total, round(real.total / (1024 ** 3), 1), delta=0.2)
        self.assertNotEqual((free, total), (100.0, 500.0))


class InstanceConfigSyncTest(unittest.TestCase):
    """Cambiare server con il pannello acceso salva config.json dal gestore (un solo punto che lo scrive)."""

    def test_switch_instance_saves_through_manager(self):
        import instance_manager
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        inst_file = os.path.join(d, "instances.json")
        cfg_file = os.path.join(d, "config.json")
        a, b = os.path.join(d, "A"), os.path.join(d, "B")
        os.makedirs(a)
        os.makedirs(b)
        with open(inst_file, "w", encoding="utf-8") as f:
            json.dump([{"id": "a", "server_dir": a, "is_active": True},
                       {"id": "b", "server_dir": b, "server_port": 30000, "is_active": False}], f)
        with open(cfg_file, "w", encoding="utf-8") as f:
            f.write("{rotto")

        class FakeManager:
            state = "STOPPED"
            config = {"admin_pin": "987654", "push_secret": "s" * 64, "server_dir": a}
            saved = 0

            def save_config(self):
                FakeManager.saved += 1
        m = FakeManager()
        with mock.patch.object(instance_manager, "INSTANCES_FILE", inst_file), \
                mock.patch.object(instance_manager, "CONFIG_FILE", cfg_file):
            res = instance_manager.switch_instance("b", manager=m)
        self.assertNotEqual(res.get("success"), False, res)
        self.assertEqual(FakeManager.saved, 1)
        self.assertEqual(m.config["server_dir"], b)
        self.assertEqual(m.config["admin_pin"], "987654")  # niente perso anche con config.json illeggibile

    def test_no_switch_or_config_change_while_server_starts_or_stops(self):
        import instance_manager
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        inst_file, cfg_file = os.path.join(d, "instances.json"), os.path.join(d, "config.json")
        a, b = os.path.join(d, "A"), os.path.join(d, "B")
        with open(inst_file, "w", encoding="utf-8") as f:
            json.dump([{"id": "a", "name": "A", "server_dir": a, "server_port": 25565, "is_active": True},
                       {"id": "b", "name": "B", "server_dir": b, "is_active": False}], f)

        class FakeManager:
            config = {"server_dir": a}

            def save_config(self):
                pass
        m = FakeManager()
        with mock.patch.object(instance_manager, "INSTANCES_FILE", inst_file), \
                mock.patch.object(instance_manager, "CONFIG_FILE", cfg_file):
            for st in ("STARTING", "STOPPING", "RESTARTING", "ONLINE"):
                m.state = st
                self.assertFalse(instance_manager.switch_instance("b", manager=m)["success"], st)
                self.assertFalse(instance_manager.update_instance("a", {"server_port": 25570}, manager=m)["success"], st)
            # Il nome si può cambiare anche a server acceso
            self.assertTrue(instance_manager.update_instance("a", {"name": "Nuovo"}, manager=m)["success"])
            for st in ("STOPPED", "CRASHED"):
                m.state = st
                self.assertTrue(instance_manager.switch_instance("b", manager=m)["success"], st)

    def test_first_created_server_is_listed_once(self):
        # Primo server creato: config.json ha già la cartella, instances.json non c'è ancora
        import instance_manager
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        inst_file, cfg_file, srv = os.path.join(d, "instances.json"), os.path.join(d, "config.json"), os.path.join(d, "S")
        os.makedirs(srv)
        with open(cfg_file, "w", encoding="utf-8") as f:
            json.dump({"server_dir": srv, "server_port": 25591}, f)
        with mock.patch.object(instance_manager, "INSTANCES_FILE", inst_file), \
                mock.patch.object(instance_manager, "CONFIG_FILE", cfg_file), \
                mock.patch.object(instance_manager, "switch_instance"):
            res = instance_manager.add_instance("Server Vanilla 1.21.11", srv, server_port=25591,
                                                server_type="vanilla", mc_version="1.21.11", set_active=True)
            items = instance_manager.load_instances_data()
        self.assertTrue(res["success"])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["name"], "Server Vanilla 1.21.11")
        self.assertTrue(items[0]["is_active"])


class BackupRotationAndRestoreTest(unittest.TestCase):
    def setUp(self):
        import backup_manager
        self.bm_mod = backup_manager
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.server = os.path.join(self.dir, "Mondo")
        self.desktop = os.path.join(self.dir, "Desktop")
        os.makedirs(os.path.join(self.server, "world"))
        os.makedirs(self.desktop)
        self.bm = backup_manager.BackupManager(self.server, self.desktop)

    def _write(self, rel, text):
        p = os.path.join(self.server, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)

    def _wait(self):
        for _ in range(200):
            if not self.bm.is_running:
                return
            time.sleep(0.05)
        self.fail("operazione non finita")

    def _backup(self, stamp):
        res = self.bm.start_backup(is_server_stopped=True)
        self.assertEqual(res.get("status"), "success", res)
        self._wait()
        self.assertFalse(self.bm.progress.get("error"), self.bm.progress)
        # Data diversa per ogni backup (il nome della copia precedente usa la data del file)
        os.utime(self.bm.target_path, (stamp, stamp))

    def test_keeps_last_backup_plus_two_previous(self):
        base = time.time() - 10 * 86400
        for i in range(5):
            self._write("world/level.dat", f"versione {i}")
            self._backup(base + i * 3600)
        old = self.bm.old_copies(self.bm.target_path)
        self.assertEqual(len(old), self.bm_mod.KEEP_OLD_BACKUPS)
        listed = self.bm.list_available_backups()
        self.assertEqual(listed[0]["id"], "desktop")  # l'ultimo backup resta in cima con l'id di sempre
        self.assertEqual(len(listed), 1 + self.bm_mod.KEEP_OLD_BACKUPS)

    def test_make_room_removes_oldest_copies_first(self):
        base = time.time() - 10 * 86400
        for i in range(3):
            self._write("world/level.dat", f"versione {i}")
            self._backup(base + i * 3600)
        old = self.bm.old_copies(self.bm.target_path)
        free = {"n": 0}

        def fake_usage(_p):
            return mock.Mock(free=free["n"])

        def fake_remove(p):
            os.unlink(p)
            free["n"] += 10  # ogni copia tolta libera spazio
        with mock.patch.object(self.bm_mod.shutil, "disk_usage", fake_usage), \
                mock.patch.object(self.bm_mod.os, "remove", fake_remove):
            self.bm._make_room(10)
        self.assertFalse(os.path.exists(old[-1]))   # la più vecchia tolta
        self.assertTrue(os.path.exists(old[0]))     # la più recente rimasta
        self.assertTrue(os.path.exists(self.bm.target_path))

    def test_last_backup_never_removed_to_make_room(self):
        for i in range(2):
            self._write("world/level.dat", f"versione {i}")
            self._backup(time.time() - 86400 + i * 3600)
        with mock.patch.object(self.bm_mod.shutil, "disk_usage", lambda _p: mock.Mock(free=0)):
            self.bm._make_room(10 ** 12)                # spazio che non basta mai
        self.assertTrue(os.path.exists(self.bm.target_path))
        self.assertEqual(self.bm.old_copies(self.bm.target_path), [])

    def test_disk_full_keeps_last_backup_and_says_so(self):
        self._write("world/level.dat", "buono")
        self._backup(time.time() - 3600)
        with open(self.bm.target_path, "rb") as f:
            good = f.read()
        import errno
        full = OSError(errno.ENOSPC, "No space left on device")
        self.assertTrue(self.bm_mod._is_disk_full(full))
        with mock.patch.object(self.bm_mod.zipfile.ZipFile, "close", side_effect=full):
            self.bm.start_backup(is_server_stopped=True)
            while self.bm.is_running:
                time.sleep(0.05)
        self.assertEqual(self.bm.progress["error"], self.bm_mod.DISK_FULL_MSG)
        with open(self.bm.target_path, "rb") as f:
            self.assertEqual(f.read(), good)

    def test_restore_sets_current_world_aside(self):
        self._write("world/level.dat", "mondo del backup")
        self._backup(time.time() - 3600)
        self._write("world/level.dat", "mondo di adesso")
        self._write("world/nuovo.dat", "creato dopo il backup")
        res = self.bm.start_restore(is_server_stopped=True, source_path="desktop", confirm_other_server=True)
        self.assertEqual(res.get("status"), "success", res)
        self._wait()
        self.assertFalse(self.bm.progress.get("error"), self.bm.progress)
        self.assertEqual(_read(os.path.join(self.server, "world", "level.dat")), "mondo del backup")
        self.assertFalse(os.path.exists(os.path.join(self.server, "world", "nuovo.dat")))
        safety = self.server + self.bm_mod.RESTORE_SAFETY_SUFFIX
        self.assertEqual(_read(os.path.join(safety, "world", "level.dat")), "mondo di adesso")
        self.assertTrue(os.path.isfile(os.path.join(safety, "world", "nuovo.dat")))

    def test_restore_refused_without_space_world_untouched(self):
        self._write("world/level.dat", "mondo del backup")
        self._backup(time.time() - 3600)
        self._write("world/level.dat", "mondo di adesso")
        with mock.patch.object(self.bm_mod.BackupManager, "_free_bytes", staticmethod(lambda p: 1000)):
            self.bm.start_restore(is_server_stopped=True, source_path="desktop", confirm_other_server=True)
            self._wait()
        self.assertIn("Spazio insufficiente", self.bm.progress.get("error") or "")
        self.assertEqual(_read(os.path.join(self.server, "world", "level.dat")), "mondo di adesso")

    def test_cancelled_restore_puts_world_back(self):
        for i in range(6):
            self._write(f"world/region/r.{i}.mca", f"regione {i} del backup")
        self._backup(time.time() - 3600)
        for i in range(6):
            self._write(f"world/region/r.{i}.mca", f"regione {i} di adesso")
        self._write("world/solo_adesso.dat", "nuovo")
        real_open = open
        count = {"n": 0}

        def cancel_midway(path, mode="r", *a, **k):
            if "w" in mode and str(path).endswith(".mca"):
                count["n"] += 1
                if count["n"] == 4:
                    self.bm.cancel_requested = True   # l'utente annulla a metà
            return real_open(path, mode, *a, **k)
        with mock.patch("builtins.open", cancel_midway):
            self.bm.start_restore(is_server_stopped=True, source_path="desktop", confirm_other_server=True)
            self._wait()
        self.assertEqual(self.bm.progress["status"], self.bm_mod.RESTORE_CANCELLED_MSG)
        for i in range(6):
            self.assertEqual(_read(os.path.join(self.server, "world", "region", f"r.{i}.mca")), f"regione {i} di adesso")
        self.assertEqual(_read(os.path.join(self.server, "world", "solo_adesso.dat")), "nuovo")

    def test_file_not_written_goes_back_in_place(self):
        self._write("world/level.dat", "mondo del backup")
        self._write("world/region.mca", "regione del backup")
        self._backup(time.time() - 3600)
        self._write("world/level.dat", "mondo di adesso")
        real_open = open

        def flaky_open(path, mode="r", *a, **k):
            if "w" in mode and str(path).endswith("level.dat"):
                raise PermissionError(13, "bloccato")
            return real_open(path, mode, *a, **k)
        with mock.patch("builtins.open", flaky_open):
            self.bm.start_restore(is_server_stopped=True, source_path="desktop", confirm_other_server=True)
            self._wait()
        # Il file non ripristinato resta quello di prima, non sparisce dal mondo
        self.assertEqual(_read(os.path.join(self.server, "world", "level.dat")), "mondo di adesso")
        self.assertEqual(_read(os.path.join(self.server, "world", "region.mca")), "regione del backup")


class TokenStoreTest(unittest.TestCase):
    """I token di accesso scadono se non usati e non crescono senza limite (classe letta da app.py senza avviarlo)."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "app.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_TokenStore")
        ns = {"Dict": Dict, "Optional": Optional, "threading": threading, "time": time}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "app.py", "exec"), ns)
        cls.Store = ns["_TokenStore"]

    def test_valid_then_expires_when_idle(self):
        s = self.Store()
        s.add("t1")
        self.assertIn("t1", s)
        self.assertNotIn("altro", s)
        s._last_used["t1"] -= self.Store.TOKEN_IDLE_DAYS * 86400 + 1
        self.assertNotIn("t1", s)

    def test_capped(self):
        s = self.Store()
        for i in range(self.Store.TOKEN_MAX + 20):
            s.add(f"t{i}")
        self.assertEqual(len(s), self.Store.TOKEN_MAX)
        self.assertIn(f"t{self.Store.TOKEN_MAX + 19}", s)

    def test_pin_change_revokes_other_devices(self):
        s = self.Store()
        for t in ("telefono", "pc_remoto", "chi_cambia"):
            s.add(t)
        s.revoke_all_except("chi_cambia")
        self.assertIn("chi_cambia", s)
        self.assertNotIn("telefono", s)
        self.assertNotIn("pc_remoto", s)
        s.revoke_all_except(None)      # cambio fatto dal PC stesso (senza token): fuori tutti
        self.assertEqual(len(s), 0)
        s.revoke_all_except("sconosciuto")
        self.assertNotIn("sconosciuto", s)

    def test_backup_restore_and_update_allowed_after_a_crash(self):
        with open(os.path.join(ROOT, "app.py"), encoding="utf-8") as f:
            src = f.read()
        tree = ast.parse(src)
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "server_fully_stopped")

        class P:
            def __init__(self, alive):
                self.alive = alive

            def poll(self):
                return None if self.alive else 0

        class M:
            process = psutil_proc = None
            state = "STOPPED"

            @staticmethod
            def _proc_alive(p):
                return p.alive
        ns = {"manager": M}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "app.py", "exec"), ns)
        stopped = ns["server_fully_stopped"]
        for st, expected in (("STOPPED", True), ("CRASHED", True), ("ONLINE", False), ("STARTING", False),
                             ("STOPPING", False), ("RESTARTING", False)):
            M.state = st
            self.assertEqual(stopped(), expected, st)
        M.state, M.process = "CRASHED", P(True)       # processo ancora acceso: niente backup
        self.assertFalse(stopped())
        M.process, M.psutil_proc = None, P(True)
        self.assertFalse(stopped())
        M.psutil_proc = P(False)
        self.assertTrue(stopped())
        self.assertNotIn('is_stopped = (manager.state', src)
        self.assertNotIn('if manager.state != "STOPPED"', src)

    def test_list_file_errors_reported_when_server_is_off(self):
        with open(os.path.join(ROOT, "routes", "players.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        routes = {n.name: ast.unparse(n) for n in tree.body if isinstance(n, ast.FunctionDef)}
        for name in ("api_whitelist_add", "api_whitelist_remove", "api_ban_player", "api_unban_player"):
            self.assertIn("_file_error(", routes[name], name)
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_file_error")

        class HTTPException(Exception):
            def __init__(self, status_code, detail=""):
                super().__init__(detail)
                self.status_code = status_code

        class M:
            state = "STOPPED"
        ns = {"HTTPException": HTTPException, "manager": M, "LIST_FILE_ERROR_MSG": "{0}"}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "players.py", "exec"), ns)
        with self.assertRaises(HTTPException):
            ns["_file_error"]("whitelist.json")
        M.state = "ONLINE"
        ns["_file_error"]("whitelist.json")   # a server acceso ci pensa il comando: nessun errore

    def test_whitelist_and_ban_refused_while_server_starts_or_stops(self):
        with open(os.path.join(ROOT, "routes", "players.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        routes = {n.name: ast.unparse(n) for n in tree.body if isinstance(n, ast.FunctionDef) and n.decorator_list}
        for name in ("api_whitelist_toggle", "api_whitelist_add", "api_whitelist_remove", "api_ban_player",
                     "api_unban_player"):
            self.assertIn("_refuse_while_changing()", routes[name], name)

    def test_update_config_revokes_tokens_when_pin_changes(self):
        with open(os.path.join(ROOT, "app.py"), encoding="utf-8") as f:
            src = f.read()
        body = src[src.index("def update_config("):src.index("manager.save_config(current)", src.index("def update_config("))
                   + 300]
        self.assertIn("valid_auth_tokens.revoke_all_except(", body)
        self.assertIn("_close_revoked_websockets()", body)


class RequirementsLockTest(unittest.TestCase):
    def test_lock_pins_every_requirement(self):
        def names(fname):
            with open(os.path.join(ROOT, fname), encoding="utf-8") as f:
                rows = [l.split("#")[0].strip() for l in f]
            return {r.split("=")[0].split("<")[0].split(">")[0].strip().lower().replace("_", "-") for r in rows if r}
        with open(os.path.join(ROOT, "requirements-lock.txt"), encoding="utf-8") as f:
            for l in f:
                l = l.split("#")[0].strip()
                if l:
                    self.assertRegex(l, r"^[A-Za-z0-9_.-]+==[0-9][0-9A-Za-z.]*$")
        self.assertLessEqual(names("requirements.txt"), names("requirements-lock.txt"))
        with open(os.path.join(ROOT, "build_runtime.py"), encoding="utf-8") as f:
            self.assertIn("requirements-lock.txt", f.read())


class _FileServer:
    """Piccolo server HTTP locale per provare i download (nessuna richiesta esce dal PC)."""

    def __init__(self, files: Dict[str, bytes], lie_length: bool = False):
        import http.server

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = files.get(self.path)
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body) * (2 if lie_length else 1)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _jar_bytes() -> bytes:
    import io as _io
    import zipfile
    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
    return buf.getvalue()


class DownloadFileTest(unittest.TestCase):
    def setUp(self):
        import hashlib
        self.jar = _jar_bytes()
        self.sha1 = hashlib.sha1(self.jar).hexdigest()
        self.srv = _FileServer({"/a.jar": self.jar, "/err.jar": b"<html>errore</html>"})
        self.addCleanup(self.srv.close)
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_good_download_with_hash(self):
        dest = os.path.join(self.dir, "server.jar")
        platform_utils.download_file(self.srv.url + "/a.jar", dest, "test", hashes={"sha1": self.sha1})
        self.assertEqual(open(dest, "rb").read(), self.jar)
        self.assertFalse(os.path.exists(dest + ".part"))

    def test_wrong_hash_leaves_no_file_and_keeps_old(self):
        dest = os.path.join(self.dir, "server.jar")
        with open(dest, "wb") as f:
            f.write(b"vecchio")
        with self.assertRaises(IOError):
            platform_utils.download_file(self.srv.url + "/a.jar", dest, "test", hashes={"sha1": "0" * 40})
        self.assertEqual(open(dest, "rb").read(), b"vecchio")
        self.assertFalse(os.path.exists(dest + ".part"))

    def test_error_page_instead_of_jar_rejected(self):
        dest = os.path.join(self.dir, "mod.jar")
        with self.assertRaises(IOError):
            platform_utils.download_file(self.srv.url + "/err.jar", dest, "test")
        self.assertFalse(os.path.exists(dest))

    def test_truncated_download_rejected(self):
        srv = _FileServer({"/a.jar": self.jar}, lie_length=True)
        self.addCleanup(srv.close)
        dest = os.path.join(self.dir, "server.jar")
        with self.assertRaises(Exception):
            platform_utils.download_file(srv.url + "/a.jar", dest, "test", timeout=3)
        self.assertFalse(os.path.exists(dest))


class ConsoleReaderTest(unittest.TestCase):
    """Un errore nell'elaborare una riga non deve fermare la lettura della console (Minecraft si bloccherebbe)."""

    def test_reader_keeps_reading_after_errors(self):
        import io as _io
        import server_manager
        lines = [f"riga {i}\n" for i in range(50)]
        proc = mock.Mock()
        proc.stdout = _io.StringIO("".join(lines))
        fake = types_ns(process=proc, add_log=lambda l: None, _RE_ANSI=server_manager.ServerManager._RE_ANSI)
        seen = []

        def parse(l):
            seen.append(l)
            if l.endswith("3"):
                raise ValueError("riga strana")
        fake._parse_log_line = parse
        server_manager.ServerManager._read_stdout(fake)
        self.assertEqual(len(seen), 50)


def types_ns(**kw):
    import types
    return types.SimpleNamespace(**kw)


class ServerInstallerTest(unittest.TestCase):
    def setUp(self):
        import server_installer
        self.si = server_installer
        self.inst = server_installer.ForgeServerInstaller(config_path=os.path.join(tempfile.mkdtemp(), "c.json"))

    def test_fallback_vanilla_urls_are_all_different(self):
        urls = [v["url"] for v in self.si.FALLBACK_VANILLA_VERSIONS]
        self.assertEqual(len(urls), len(set(urls)), "due versioni puntano allo stesso server.jar")

    def test_required_java_rules_without_mojang(self):
        with mock.patch.object(self.inst, "_mojang_version_info", return_value=None):
            self.assertEqual(self.inst.required_java("26.3"), 25)
            self.assertEqual(self.inst.required_java("1.21.1"), 21)
            self.assertEqual(self.inst.required_java("1.20.4"), 17)
            self.assertEqual(self.inst.required_java("1.20.1"), 17)
            self.assertEqual(self.inst.required_java("1.16.5"), 8)
        with mock.patch.object(self.inst, "_mojang_version_info", return_value={"java": 23}):
            self.assertEqual(self.inst.required_java("x"), 23)

    def test_java_major_parsing(self):
        for out, major in (('openjdk version "21.0.11" 2026-04-21 LTS', 21), ('java version "1.8.0_401"', 8),
                           ('openjdk version "25" 2025-09-16', 25)):
            res = mock.Mock(stderr=out, stdout="")
            with mock.patch.object(self.si.subprocess, "run", return_value=res):
                self.assertEqual(self.si.ForgeServerInstaller.java_major("java"), major)

    def test_old_java_stops_install_before_download(self):
        with mock.patch.object(self.inst, "required_java", return_value=25), \
                mock.patch.object(self.inst, "java_major", return_value=21), \
                mock.patch.object(self.inst, "_install_vanilla") as inst_v:
            self.inst._run_install_worker(tempfile.mkdtemp(), "vanilla", "26.3", 4, 25565, None)
        inst_v.assert_not_called()
        self.assertEqual(self.inst.status, "error")
        self.assertIn("Java 25", self.inst.error_message)

    def test_unknown_vanilla_version_is_an_error_not_1_20_1(self):
        d = tempfile.mkdtemp()
        with mock.patch.object(self.inst, "_mojang_version_info", return_value=None), \
                mock.patch.object(self.inst, "_download") as dl:
            with self.assertRaises(RuntimeError):
                self.inst._install_vanilla(d, "9.99", 4, 25565, None)
        dl.assert_not_called()

    def test_vanilla_uses_mojang_url_and_sha1(self):
        d = tempfile.mkdtemp()
        info = {"server_url": "https://piston-data.mojang.com/v1/objects/abc/server.jar", "server_sha1": "abc", "java": 25}
        with mock.patch.object(self.inst, "_mojang_version_info", return_value=info), \
                mock.patch.object(self.inst, "_download", side_effect=RuntimeError("stop")) as dl:
            with self.assertRaises(RuntimeError):
                self.inst._install_vanilla(d, "26.3", 4, 25565, None)
        self.assertEqual(dl.call_args[0][0], info["server_url"])
        self.assertEqual(dl.call_args[1]["hashes"], {"sha1": "abc"})


class JavaLineTest(unittest.TestCase):
    """Riga java di run.bat: percorso completo, virgolette, "@java". Prima i server Forge 1.16.5/1.12.2 creati dal
    pannello non partivano (virgolette passate a Java) e con il percorso completo il "pause" bloccava arresto e crash."""

    def test_recognized_lines(self):
        for line in ("java -jar server.jar", "@java -Xmx4G -jar server.jar", "java.exe -jar a.jar",
                     '"C:\\Program Files\\Java\\bin\\java.exe" -jar server.jar', '"%JAVA_HOME%\\bin\\java" -jar a.jar',
                     "/usr/lib/jvm/java-21/bin/java -jar server.jar", "  JAVA -jar server.jar"):
            self.assertTrue(platform_utils.is_java_line(line), line)
        for line in ("@echo off", "REM java -jar server.jar", "pause", "javascript.exe", 'cd "%~dp0"', ""):
            self.assertFalse(platform_utils.is_java_line(line), line)

    @unittest.skipUnless(platform_utils.IS_WINDOWS, "virgolette alla maniera di cmd")
    def test_quotes_not_passed_to_java(self):
        parts = platform_utils.split_java_line('"C:\\Program Files\\Java\\bin\\java.exe" -Xmx4G -jar "forge-1.16.5.jar" %*')
        self.assertEqual(parts, ["C:\\Program Files\\Java\\bin\\java.exe", "-Xmx4G", "-jar", "forge-1.16.5.jar", "nogui"])
        self.assertEqual(platform_utils.split_java_line("@java -jar server.jar")[0], "java")

    def test_ram_rewrite_keeps_quoted_java_path(self):
        import server_manager
        m = server_manager.ServerManager.__new__(server_manager.ServerManager)
        line = '"C:\\Program Files\\Java\\bin\\java.exe" -Xmx2G -jar "server.jar" nogui'
        self.assertEqual(m._rewrite_java_line(line, 6, False),
                         '"C:\\Program Files\\Java\\bin\\java.exe" -Xms6G -Xmx6G -jar "server.jar" nogui')
        self.assertEqual(m._rewrite_java_line("@java -Xmx2G -jar server.jar", 3, False),
                         "@java -Xms3G -Xmx3G -jar server.jar")

    def test_other_server_with_same_prefix_not_adopted(self):
        with open(os.path.join(ROOT, "server_manager.py"), encoding="utf-8") as f:
            self.assertNotIn("(server_dir in cmdline)", f.read())
        import re
        server_dir = "c:\\minecraft\\server"
        pat = re.escape(server_dir) + r'(?=[\\/"]|$)'
        with open(os.path.join(ROOT, "server_manager.py"), encoding="utf-8") as f:
            self.assertIn("re.escape(server_dir) + r'(?=[\\\\/\"]|$)'", f.read())
        for other in ("server creativo", "server2", "server_vecchio"):
            self.assertIsNone(re.search(pat, f'java -jar "c:\\minecraft\\{other}\\server.jar" nogui'), other)
        self.assertIsNotNone(re.search(pat, 'java -jar "c:\\minecraft\\server\\server.jar" nogui'))


class ServerDetectionTest(unittest.TestCase):
    """Server collegati dal pannello: versione di Minecraft per NeoForge e caricatore dei modpack CurseForge."""

    def test_neoforge_numbers(self):
        from instance_manager import neoforge_to_mc_version as f
        for nf, mc in (("21.1.77", "1.21.1"), ("20.4.237", "1.20.4"), ("21.0.167", "1.21"), ("21.11.4-beta", "1.21.11"),
                       ("26.1.0.5", "26.1"), ("26.1.2.3", "26.1.2"), ("x", "")):
            self.assertEqual(f(nf), mc, nf)

    def test_detection(self):
        import instance_manager as im
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        os.makedirs(os.path.join(d, "libraries", "net", "neoforged", "neoforge", "21.1.77"))
        r = im.detect_server_details(d)
        self.assertEqual((r["server_type"], r["mc_version"]), ("neoforge", "1.21.1"))
        d2 = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d2, True)
        os.makedirs(os.path.join(d2, "libraries", "net", "minecraftforge", "forge", "1.20.1-47.3.0"))
        with open(os.path.join(d2, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump({"minecraft": {"version": "1.20.1", "modLoaders": [{"id": "forge-47.3.0", "primary": True}]}}, f)
        r = im.detect_server_details(d2)
        self.assertEqual((r["server_type"], r["mc_version"]), ("forge", "1.20.1"))

    def test_saved_instances_fixed_once(self):
        import instance_manager as im
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        pack = os.path.join(d, "pack")
        os.makedirs(os.path.join(pack, "libraries", "net", "fabricmc"))
        inst_file = os.path.join(d, "instances.json")
        with open(inst_file, "w", encoding="utf-8") as f:
            json.dump([{"id": "a", "server_dir": d, "server_type": "neoforge", "mc_version": "21.1.77", "is_active": True},
                       {"id": "b", "server_dir": pack, "server_type": "curseforge", "mc_version": "1.21.1"}], f)
        with mock.patch.object(im, "INSTANCES_FILE", inst_file):
            items = im.load_instances_data()
        self.assertEqual(items[0]["mc_version"], "1.21.1")
        self.assertEqual(items[1]["server_type"], "fabric")
        with open(inst_file, encoding="utf-8") as f:
            self.assertEqual(json.load(f)[0]["mc_version"], "1.21.1")


class ForgeJava8Test(unittest.TestCase):
    """Forge 1.12.2 e più vecchi solo con Java 8: prima si installavano con Java 21 e andavano in crash a ogni avvio."""

    def test_versions(self):
        from server_installer import forge_needs_java8
        for v in ("1.12.2", "1.7.10", "1.10.2"):
            self.assertTrue(forge_needs_java8(v), v)
        for v in ("1.16.5", "1.13.2", "1.20.1", "26.3", "", None):
            self.assertFalse(forge_needs_java8(v), v)

    def test_install_refused_with_newer_java(self):
        import server_installer
        inst = server_installer.ForgeServerInstaller.__new__(server_installer.ForgeServerInstaller)
        inst.lock = threading.Lock()
        inst.log_lines, inst.status, inst.error_message, inst.step_text = [], "downloading", "", ""
        inst.required_java = lambda v: 8
        inst.java_major = lambda *a: 21
        inst._install_forge = mock.Mock()
        inst._run_install_worker("x", "forge", "1.12.2", 4, 25565, None)
        inst._install_forge.assert_not_called()
        self.assertEqual(inst.status, "error")
        self.assertIn("solo con Java 8", inst.error_message)
        inst.java_major = lambda *a: 8
        inst._run_install_worker("x", "forge", "1.12.2", 4, 25565, None)
        inst._install_forge.assert_called_once()

    def test_crash_explained(self):
        import server_manager
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "config.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"push_secret": "z" * 64, "push_topic": "mc_server_0011223344556677"}, f)
        m = server_manager.ServerManager(config_path=p)
        self.addCleanup(setattr, m, "running", False)
        m.send_push_notification = lambda *a, **k: None
        m._parse_log_line("java.lang.ClassCastException: class jdk.internal.loader.ClassLoaders$AppClassLoader cannot be "
                          "cast to class java.net.URLClassLoader")
        self.assertEqual(list(m.errors_list)[-1]["level"], "CRITICAL")
        self.assertIn(server_manager.JAVA8_ONLY_RUNTIME_MSG, "\n".join(m.log_buffer))


class NewServerSafetyTest(unittest.TestCase):
    """Nuovo server: mai sopra un mondo esistente, mai sulle porte del pannello (che finirebbero aperte sul router)."""

    def test_folder_with_world_detected(self):
        from server_installer import folder_has_world
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        self.assertFalse(folder_has_world(d))
        with open(os.path.join(d, "server.properties"), "w", encoding="utf-8") as f:
            f.write("server-port=25565\n")   # installazione fallita a metà: si può riprovare
        self.assertFalse(folder_has_world(d))
        os.makedirs(os.path.join(d, "mondo"))
        with open(os.path.join(d, "mondo", "level.dat"), "wb") as f:
            f.write(b"x")
        self.assertTrue(folder_has_world(d))
        self.assertFalse(folder_has_world(os.path.join(d, "non_esiste")))

    def test_panel_ports_refused_for_minecraft(self):
        with open(os.path.join(ROOT, "app.py"), encoding="utf-8") as f:
            src = f.read()
        node = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "minecraft_port_error")
        ns = {"Optional": Optional, "panel_ports": lambda: {8080, 8443, 32770}}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "app.py", "exec"), ns)
        err = ns["minecraft_port_error"]
        for bad in (8080, 8443, 32770, "8080", 80, 70000, "abc", None):
            self.assertIsNotNone(err(bad), bad)
        self.assertIsNone(err(25565))
        self.assertIn("if port in panel_ports():", src[src.index("def sync_minecraft_port"):])
        with open(os.path.join(ROOT, "routes", "server_creation.py"), encoding="utf-8") as f:
            creation = f.read()
        self.assertIn("folder_has_world(req.target_dir)", creation)
        self.assertIn("minecraft_port_error(req.port)", creation)
        with open(os.path.join(ROOT, "routes", "instances.py"), encoding="utf-8") as f:
            self.assertIn("minecraft_port_error(updates[\"server_port\"])", f.read())


class RemoteClosedAndUploadsTest(unittest.TestCase):
    """Accesso da internet chiuso ricordato al riavvio; caricamenti interrotti senza file a metà."""

    def test_closed_remote_access_stays_closed_after_restart(self):
        import remote_access as ra

        class FakeUpnp:
            opened, closed = [], []

            def open_port(self, port, **kw):
                FakeUpnp.opened.append(port)
                return {"success": True}

            def close_port(self, port):
                FakeUpnp.closed.append(port)

            def get_external_ip(self):
                return "81.1.2.3"
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        r = ra.RemoteAccess(d, 8443, FakeUpnp(), lambda force: "81.1.2.3")
        r.user_closed = True            # come app.py con "remote_access_closed": true in config.json
        r._stop.set()                   # solo il primo giro del controllo
        done = threading.Event()
        orig = r.refresh
        r.refresh = lambda reopen=True: (orig(reopen=reopen), done.set())
        r.start_watcher()
        self.assertTrue(done.wait(5))
        self.assertEqual(FakeUpnp.opened, [])
        self.assertEqual(FakeUpnp.closed, [8443])
        self.assertFalse(r.get_status()["active"])

    def test_app_refuses_internet_when_closed_and_remembers_choice(self):
        with open(os.path.join(ROOT, "app.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn('remote_access.user_closed = bool(manager.config.get("remote_access_closed"))', src)
        mw = src[src.index("async def block_cross_site_middleware"):src.index("async def normalize_slashes_middleware")]
        self.assertIn("remote_access.user_closed and is_internet_request(request)", mw)
        self.assertIn("status_code=503", mw)   # mai 401/403: l'app segnerebbe il PIN salvato come sbagliato
        self.assertIn("_save_remote_closed(True)", src)
        self.assertIn("_save_remote_closed(False)", src)

    def test_interrupted_upload_keeps_old_mod(self):
        import asyncio
        with open(os.path.join(ROOT, "app.py"), encoding="utf-8") as f:
            node = next(n for n in ast.parse(f.read()).body
                        if isinstance(n, ast.AsyncFunctionDef) and n.name == "save_upload")
        ns = {"Request": object}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "app.py", "exec"), ns)

        class BrokenRequest:
            async def stream(self):
                yield b"PK\x03\x04 meta"
                raise ConnectionResetError("telefono disconnesso")
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        with self.assertRaises(ConnectionResetError):
            asyncio.run(ns["save_upload"](BrokenRequest(), os.path.join(d, "mod.jar.part")))
        for name in ("mods.py", "files.py"):
            with open(os.path.join(ROOT, "routes", name), encoding="utf-8") as f:
                route = f.read()
            self.assertIn('part_path = target_path + ".part"', route, name)
            self.assertIn("os.replace(part_path, target_path)", route, name)
        with open(os.path.join(ROOT, "routes", "mods.py"), encoding="utf-8") as f:
            self.assertIn("zipfile.is_zipfile(part_path)", f.read())


class LanWithoutPinTest(unittest.TestCase):
    """"Richiedi PIN" disattivato in casa: solo richieste all'IP o al nome del PC, mai a un nome di un altro sito."""

    @classmethod
    def setUpClass(cls):
        import socket
        with open(os.path.join(ROOT, "app.py"), encoding="utf-8") as f:
            cls.src = f.read()
        node = next(n for n in ast.parse(cls.src).body
                    if isinstance(n, ast.FunctionDef) and n.name == "host_is_direct_address")
        ns = {"socket": socket}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "app.py", "exec"), ns)
        cls.direct = staticmethod(ns["host_is_direct_address"])
        cls.pc = socket.gethostname().lower()

    def test_addresses(self):
        for host in ("192.168.1.10:8080", "192.168.1.10", "127.0.0.1:8080", "localhost:8080", "[::1]:8080",
                     "[fe80::1]", f"{self.pc}:8080", f"{self.pc}.local:8080"):
            self.assertTrue(self.direct({"host": host}), host)
        for host in ("sito-malevolo.com:8080", "192.168.1.10.nip.io:8080", "", "evil.local:8080"):
            self.assertFalse(self.direct({"host": host}), host)

    def test_used_where_pin_is_skipped(self):
        self.assertEqual(self.src.count("and host_is_direct_address("), 2)


class StoreUninstallTest(unittest.TestCase):
    """Disinstallazione dallo Store: la mod giusta (anche disattivata) e mai file fuori da mods/ e plugins/."""

    def setUp(self):
        import modrinth_store
        self.ms = modrinth_store
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        os.makedirs(os.path.join(self.d, "mods"))

    def _files(self, *names):
        for n in names:
            with open(os.path.join(self.d, "mods", n), "wb") as f:
                f.write(b"x")

    def test_disabled_mod_removed_not_a_similar_one(self):
        self._files("create-1.21.1-6.0.jar.disabled", "create_enchantment_industry-2.0.jar")
        self.ms.save_installed_projects(self.d, {"create": {"filename": "create-1.21.1-6.0.jar", "folder": "mods"}})
        res = self.ms.uninstall_modrinth_project("create", self.d)
        self.assertTrue(res["success"])
        self.assertEqual(os.listdir(os.path.join(self.d, "mods")), ["create_enchantment_industry-2.0.jar"])
        self.assertFalse(self.ms.uninstall_modrinth_project("create", self.d)["success"])

    def test_search_by_name_only_when_one_file_matches(self):
        self._files("sodium-fabric-0.6.0.jar", "lithium-0.14.jar", "lithium-extra-1.0.jar")
        self.assertEqual(self.ms.uninstall_modrinth_project("sodium", self.d)["filename"], "sodium-fabric-0.6.0.jar")
        self.assertEqual(self.ms.uninstall_modrinth_project("lithium", self.d)["filename"], "lithium-0.14.jar")
        self.assertIn("lithium-extra-1.0.jar", os.listdir(os.path.join(self.d, "mods")))

    def test_no_files_outside_the_mods_folder(self):
        outside = os.path.join(self.d, "segreto.txt")
        with open(outside, "w", encoding="utf-8") as f:
            f.write("x")
        for name in ("../segreto.txt", "..\\segreto.txt", "../segreto.jar"):
            self.assertFalse(self.ms.uninstall_modrinth_project("nessuno", self.d, filename=name)["success"])
        self.assertTrue(os.path.exists(outside))


class ModStoreSafetyTest(unittest.TestCase):
    def test_file_names_and_urls(self):
        import modrinth_store as ms
        self.assertEqual(ms._safe_jar_name("sodium-1.0.jar"), "sodium-1.0.jar")
        self.assertEqual(ms._safe_jar_name("../../config.json"), "")
        self.assertEqual(ms._safe_jar_name("..\\..\\evil.jar"), "evil.jar")
        self.assertEqual(ms._safe_jar_name("x.jar.disabled"), "x.jar.disabled")
        self.assertEqual(ms._safe_jar_name(".tmp.jar"), "")
        self.assertTrue(ms._modrinth_url_ok("https://cdn.modrinth.com/data/a/b.jar"))
        self.assertFalse(ms._modrinth_url_ok("http://cdn.modrinth.com/data/a/b.jar"))
        self.assertFalse(ms._modrinth_url_ok("https://modrinth.com.evil.example/b.jar"))


class CertificateRecoveryTest(unittest.TestCase):
    def test_broken_certificate_is_replaced(self):
        import remote_access
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        first = remote_access.ensure_certificate(d)
        self.assertEqual(remote_access.ensure_certificate(d)["fingerprint"], first["fingerprint"])
        with open(first["cert_path"], "wb") as f:
            f.write(b"-----BEGIN CERTIFICATE-----\nrotto")
        again = remote_access.ensure_certificate(d)
        self.assertRegex(again["fingerprint"], r"^[0-9a-f]{64}$")
        self.assertTrue(os.path.isfile(first["cert_path"] + ".rotto"))


class InstallerActivatesServerTest(unittest.TestCase):
    def test_new_server_is_announced_to_backups(self):
        import server_installer
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        inst = server_installer.ForgeServerInstaller(config_path=os.path.join(d, "config.json"))
        seen = []
        inst.on_server_ready = seen.append
        with mock.patch("instance_manager.add_instance"):
            inst._finalize_config(os.path.join(d, "srv"), os.path.join(d, "srv", "run.bat"), "java -jar s.jar", 25565, None)
        self.assertEqual(seen, [os.path.join(d, "srv")])


class PaperConsoleTest(unittest.TestCase):
    """Console di Paper: "[19:30:00 INFO]: ..." con codici colore. Prima giocatori e chat non si riconoscevano."""

    def setUp(self):
        import server_manager
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "config.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"push_secret": "z" * 64, "push_topic": "mc_server_0011223344556677"}, f)
        self.m = server_manager.ServerManager(config_path=p)
        self.addCleanup(setattr, self.m, "running", False)
        self.m.send_push_notification = lambda *a, **k: None

    def test_quick_action_on_missing_player_is_an_error(self):
        m = self.m

        def fake_send(cmd, reply):
            m.add_log(f"> {cmd}")
            m.add_log(reply)
            return {"status": "success", "message": "Comando inviato"}
        m.send_command = lambda c: fake_send(c, "[12:00:00] [Server thread/INFO]: No player was found")
        res = m.player_quick_action("Nessuno", "gamemode", "creative")
        self.assertEqual(res["status"], "error")
        self.assertIn("non è connesso", res["message"])
        m.send_command = lambda c: fake_send(c, "[12:00:00] [Server thread/INFO]: Set Notch's game mode to Creative Mode")
        self.assertEqual(m.player_quick_action("Notch", "gamemode", "creative")["status"], "success")

    def test_pvp_is_a_gamerule_from_1_21_9(self):
        import instance_manager
        m = self.m
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        props = os.path.join(d, "server.properties")
        with open(props, "w", encoding="utf-8") as f:
            f.write("motd=x\n")
        m.config["server_dir"] = d
        sent = []
        m.send_command = lambda c: sent.append(c) or {"status": "success"}
        with mock.patch.object(instance_manager, "get_active_instance", return_value={"mc_version": "1.21.8"}):
            self.assertFalse(m.pvp_is_gamerule())
            m.save_server_properties({"pvp": "false"})
            with open(props, encoding="utf-8") as f:
                self.assertIn("pvp=false", f.read())   # fino alla 1.21.8 resta in server.properties
        with mock.patch.object(instance_manager, "get_active_instance", return_value={"mc_version": "26.3"}):
            self.assertTrue(m.pvp_is_gamerule())
            m.state = "STOPPED"
            res = m.save_server_properties({"pvp": "true"})
            self.assertEqual(res["pvp_gamerule"], "pending")
            self.assertEqual(m.config["pvp_pending"], "true")
            m.state = "ONLINE"
            res = m.save_server_properties({"pvp": "false", "motd": "y"})
            self.assertEqual(res["pvp_gamerule"], "now")
            self.assertEqual(sent[-1], "gamerule pvp false")
            self.assertNotIn("pvp_pending", m.config)

    def test_heal_really_heals_and_chat_does_not_restart(self):
        m = self.m
        sent = []
        m.send_command = lambda c: sent.append(c) or {"status": "success"}
        m.player_quick_action("Notch", "heal")
        amp = int(sent[0].split()[-1])
        self.assertGreater(min(4 << (amp & 31), 2**31 - 1), 0)  # 4 << livello punti, come Minecraft (int a 32 bit)
        self.assertLess(amp, 29)
        m.restart_server = mock.Mock()
        m.start_server = mock.Mock()
        m.process = mock.Mock(poll=lambda: None, stdin=None)
        for msg in ("riavviamo dopo cena?", "riavviate il gioco", "Riavviato tutto?"):
            m.talk_to_server(msg)
        m.restart_server.assert_not_called()
        m.start_server.assert_not_called()
        m.talk_to_server("riavvia")
        m.restart_server.assert_called_once()

    def test_ram_change_keeps_other_forge_jvm_options(self):
        m = self.m
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        m.config["server_dir"] = d
        p = os.path.join(d, "user_jvm_args.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write("# Xmx and Xms set the maximum and minimum RAM usage\n-Xmx4G\n-Dfml.queryResult=confirm\n")
        m._uses_jvm_args_file = lambda: True
        m._host_ram_gb = lambda: 32
        self.assertEqual(m.save_jvm_tuning(6, True)["status"], "success")
        self.assertEqual(m.save_jvm_tuning(8, False)["status"], "success")
        with open(p, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("-Dfml.queryResult=confirm", text)
        self.assertIn("# Xmx and Xms set", text)
        self.assertIn("-Xmx8G", text)
        self.assertNotIn("-Xmx4G", text)
        self.assertNotIn("-Xmx6G", text)
        self.assertNotIn("UseG1GC", text)
        self.assertEqual(text.count("generata da Voxarium"), 1)

    def test_clean_logs_without_server(self):
        self.m.config["server_dir"] = ""
        self.assertEqual(self.m.clean_old_logs()["status"], "error")

    def test_automatic_stops_show_stopping_state(self):
        m = self.m
        m.send_command = lambda *a, **k: {"status": "success"}
        m.STOP_TIMEOUT_SECONDS = 0
        with mock.patch("server_manager.time.sleep"):
            m.state = "ONLINE"
            m.scheduled_restart_in_progress = False
            m._graceful_stop_worker("Modalità Eco")
            self.assertIn(m.state, ("STOPPING", "STOPPED"))
            m.state = "ONLINE"
            m.scheduled_restart_in_progress = True
            m._graceful_stop_worker("Riavvio programmato")
            self.assertEqual(m.state, "RESTARTING")

    def test_eco_stop_is_intentional(self):
        m = self.m
        started = []
        m.state, m.eco_mode_enabled, m.eco_mode_minutes = "ONLINE", True, 5
        m.players.clear()
        m.intentional_stop = False
        m.server_empty_since = time.time() - 6 * 60
        m.scheduled_enabled = m.anti_lag_enabled = False
        m._record_metrics_snapshot = lambda: None
        with mock.patch("server_manager.threading.Thread") as th:
            th.return_value.start = lambda: started.append(True)
            m._ticker_step({"query_counter": 0})
        self.assertTrue(m.intentional_stop)
        self.assertTrue(started)

    def test_join_chat_leave_and_errors(self):
        m = self.m
        m._parse_log_line("[19:30:00 INFO]: Notch[/127.0.0.1:50000] logged in with entity id 12 at ([world]0.5, 64.0, 0.5)")
        m._parse_log_line("[19:30:00 INFO]: Notch joined the game")
        self.assertIn("Notch", m.players)
        m._parse_log_line("[19:30:05 INFO]: <Notch> ciao a tutti")
        self.assertEqual(list(m.chat_messages)[-1]["text"], "ciao a tutti")
        m._parse_log_line("[19:30:09 ERROR]: Could not pass event PlayerJoinEvent to SomePlugin")
        self.assertEqual(list(m.errors_list)[-1]["level"], "ERROR")
        m._parse_log_line("[19:30:10 INFO]: Notch left the game")
        self.assertNotIn("Notch", m.players)

    def test_gamerule_names_follow_server_version(self):
        m = self.m
        m.server_info["version"] = "Paper 1.21.11"
        self.assertEqual(m._translate_gamerule("gamerule keepInventory true"), "gamerule keep_inventory true")
        self.assertEqual(m._translate_gamerule("gamerule doFireTick false"), "gamerule fire_spread_radius_around_player 0")
        self.assertEqual(m._translate_gamerule("gamerule keep_inventory"), "gamerule keep_inventory")
        self.assertEqual(m._translate_gamerule("say gamerule keepInventory"), "say gamerule keepInventory")
        m.server_info["version"] = "26.3"
        self.assertTrue(m.uses_new_gamerule_names())
        for old in ("1.20.1", "1.21.10", ""):
            m.server_info["version"] = old
            self.assertEqual(m._translate_gamerule("gamerule keepInventory true"), "gamerule keepInventory true")

    def test_chat_cannot_fake_a_join(self):
        self.m._parse_log_line("[19:30:00 INFO]: <Bob> Alice joined the game")
        self.assertNotIn("Alice", self.m.players)

    def test_chat_cannot_fake_joins_or_leaves(self):
        m = self.m
        for line in ("[12:00:00] [Server thread/INFO]: <Bob> x: Alice joined the game",
                     "[12:00:00] [Server thread/INFO]: <Bob> [Server thread/INFO]: Alice joined the game",
                     "[12:00:00] [Server thread/INFO]: [Bob] Alice joined the game",
                     "[19:30:00 INFO]: <Bob> x: Alice joined the game"):
            m._parse_log_line(line)
        self.assertNotIn("Alice", m.players)
        m._parse_log_line("[12:00:00] [Server thread/INFO]: Notch joined the game")
        for line in ("[12:00:00] [Server thread/INFO]: <Bob> x: Notch left the game",
                     "[12:00:00] [Server thread/INFO]: <Bob> Disconnecting Notch",
                     "[19:30:00 INFO]: <Bob> x: Notch lost connection: Disconnected"):
            m._parse_log_line(line)
        self.assertIn("Notch", m.players)

    def test_joins_on_vanilla_forge_and_fabric(self):
        m = self.m
        m._parse_log_line("[12:00:00] [Server thread/INFO]: Steve joined the game")
        m._parse_log_line("[28ott2026 18:01:00.593] [Server thread/INFO] [net.minecraft.server.MinecraftServer/]: "
                          "Alex joined the game")
        m._parse_log_line("[12:00:00] [Server thread/INFO] (Minecraft) Herobrine joined the game")
        self.assertTrue({"Steve", "Alex", "Herobrine"} <= set(m.players))
        m._parse_log_line("[28ott2026] [Netty Server IO #1/INFO] [x/SERVERHOOKS]: Disconnecting Alex (/1.2.3.4): bye")
        self.assertNotIn("Alex", m.players)

    def test_ansi_colours_removed(self):
        import server_manager
        raw = "[19:26:46 INFO]: \x1b[38;5;9mIncorrect argument for command\x1b[0m"
        self.assertEqual(server_manager.ServerManager._RE_ANSI.sub("", raw),
                         "[19:26:46 INFO]: Incorrect argument for command")


class PingWhileForgeStartsTest(unittest.TestCase):
    """Forge in avvio risponde al ping con un messaggio di disconnessione: non è un server acceso."""

    def _ping_with_reply(self, payload: dict):
        import socket
        import mc_ping
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        self.addCleanup(srv.close)

        def serve():
            conn, _ = srv.accept()
            with conn:
                conn.recv(1024)
                text = json.dumps(payload).encode()
                body = mc_ping.pack_varint(0) + mc_ping.pack_varint(len(text)) + text
                conn.sendall(mc_ping.pack_varint(len(body)) + body)
                time.sleep(0.2)

        threading.Thread(target=serve, daemon=True).start()
        return mc_ping.query_server_status(port=srv.getsockname()[1], timeout=2)

    def test_still_starting_is_not_online(self):
        res = self._ping_with_reply({"text": "Server is still starting! Please wait before reconnecting."})
        self.assertFalse(res["online"])

    def test_status_reply_is_online(self):
        res = self._ping_with_reply({"version": {"name": "1.20.1", "protocol": 763},
                                     "players": {"max": 7, "online": 0}, "description": {"text": "ciao"}})
        self.assertTrue(res["online"])
        self.assertEqual(res["players_max"], 7)


class PlayerProfileTest(unittest.TestCase):
    """uuid scritto in whitelist.json / banned-players.json a server spento (mai vuoto: Minecraft 1.21 svuotava l'elenco)."""

    def setUp(self):
        import hashlib
        import urllib.error
        import urllib.parse
        import urllib.request
        import uuid
        with open(os.path.join(ROOT, "routes", "players.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        wanted = {"_offline_mode", "_player_profile", "_add_or_repair_entry", "NOT_FOUND_MSG", "MOJANG_UNREACHABLE_MSG"}
        body = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name in wanted) or
                (isinstance(n, ast.Assign) and n.targets[0].id in wanted if isinstance(n, ast.Assign) and
                 isinstance(n.targets[0], ast.Name) else False)]

        class HTTPException(Exception):
            def __init__(self, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code

        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.ns = {"os": os, "json": json, "hashlib": hashlib, "uuid": uuid, "urllib": urllib,
                   "HTTPException": HTTPException, "get_server_dir_path": lambda: self.dir}
        exec(compile(ast.Module(body=body, type_ignores=[]), "players.py", "exec"), self.ns)
        self.urllib = urllib

    def _props(self, online):
        with open(os.path.join(self.dir, "server.properties"), "w", encoding="utf-8") as f:
            f.write("motd=x\nonline-mode=" + ("true" if online else "false") + "\n")

    def test_offline_mode_uuid_matches_minecraft(self):
        self._props(False)
        self.assertEqual(self.ns["_player_profile"]("Notch"), ("b50ad385-829d-3141-a216-7e7d7539ba7f", "Notch"))

    def test_online_mode_uses_mojang(self):
        self._props(True)
        import io
        resp = io.BytesIO(b'{"id": "069a79f444e94726a5befca90e38aaf5", "name": "Notch"}')
        with mock.patch.object(self.urllib.request, "urlopen", return_value=resp):
            self.assertEqual(self.ns["_player_profile"]("notch"), ("069a79f4-44e9-4726-a5be-fca90e38aaf5", "Notch"))

    def test_unknown_name_and_no_internet(self):
        self._props(True)
        err = self.urllib.error.HTTPError("u", 404, "no", {}, None)
        self.addCleanup(err.close)
        with mock.patch.object(self.urllib.request, "urlopen", side_effect=err):
            self.assertIsNone(self.ns["_player_profile"]("Nessuno"))
        with mock.patch.object(self.urllib.request, "urlopen", side_effect=self.urllib.error.URLError("offline")):
            with self.assertRaises(Exception) as cm:
                self.ns["_player_profile"]("Notch")
        self.assertEqual(cm.exception.status_code, 503)

    def test_old_entry_with_empty_uuid_is_repaired(self):
        add = self.ns["_add_or_repair_entry"]
        profile = ("069a79f4-44e9-4726-a5be-fca90e38aaf5", "Notch")
        current = [{"uuid": "", "name": "notch", "reason": "x"}]
        self.assertTrue(add(current, "Notch", profile, {"reason": "y"}))
        self.assertEqual(current, [{"uuid": profile[0], "name": "Notch", "reason": "x"}])
        self.assertFalse(add(current, "NOTCH", profile, {}))
        self.assertTrue(add(current, "Steve", ("8667ba71-b85a-4004-af54-457a9734eed7", "Steve"), {}))
        self.assertEqual(len(current), 2)


class WebPortTest(unittest.TestCase):
    def test_invalid_web_port_falls_back_to_8080(self):
        with open(os.path.join(ROOT, "app.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_valid_web_port")
        ns = {"REMOTE_PORT": 8443}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "app.py", "exec"), ns)
        f = ns["_valid_web_port"]
        self.assertEqual(f(8080), 8080)
        self.assertEqual(f("9000"), 9000)
        for bad in (None, "", "abc", 0, 80, 70000, 8443):
            self.assertEqual(f(bad), 8080, bad)


class WrongJavaLogTest(unittest.TestCase):
    def test_unsupported_class_version_gives_clear_error(self):
        import server_manager
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "config.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"push_secret": "z" * 64, "push_topic": "mc_server_0011223344556677"}, f)
        m = server_manager.ServerManager(config_path=p)
        self.addCleanup(setattr, m, "running", False)
        m._parse_log_line("Exception in thread \"main\" java.lang.UnsupportedClassVersionError: net/minecraft/server/Main "
                          "has been compiled by a more recent version of the Java Runtime (class file version 69.0), "
                          "this version of the Java Runtime only recognizes class file versions up to 65.0")
        self.assertIn("Java 25", list(m.errors_list)[-1]["message"])


if __name__ == "__main__":
    unittest.main()
