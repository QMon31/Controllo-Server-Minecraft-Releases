"""Versione Linux: script di avvio run.sh, comando java letto dallo script, aggiornamento dal pacchetto .tar.gz e
pacchetto per Linux. I test simulano Windows e Linux cambiando IS_WINDOWS/IS_LINUX: girano su entrambi i sistemi.

Avvio a mano:  python -m unittest discover -s tests -v
"""
import io
import os
import shutil
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import platform_utils  # noqa: E402
import pc_updater  # noqa: E402


class LaunchScriptTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="csm_linux_")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _read(self, path):
        with open(path, "rb") as f:
            return f.read().decode("utf-8")

    def test_windows_run_bat_unchanged(self):
        with mock.patch.object(platform_utils, "IS_WINDOWS", True), \
                mock.patch.object(platform_utils, "LAUNCH_SCRIPT", "run.bat"):
            path = platform_utils.write_launch_script(self.d, "Vanilla 1.21.4", "java -Xmx4G -jar server.jar nogui")
        self.assertEqual(os.path.basename(path), "run.bat")
        self.assertEqual(self._read(path).replace("\r\n", "\n"),
                         "@echo off\nREM Voxarium Vanilla 1.21.4 Launcher\njava -Xmx4G -jar server.jar nogui %*\npause\n")

    def test_linux_run_sh(self):
        with mock.patch.object(platform_utils, "IS_WINDOWS", False), \
                mock.patch.object(platform_utils, "LAUNCH_SCRIPT", "run.sh"):
            path = platform_utils.write_launch_script(self.d, "Paper 1.21.4", "java -Xmx4G -jar paper.jar nogui")
        text = self._read(path)
        self.assertEqual(os.path.basename(path), "run.sh")
        self.assertTrue(text.startswith("#!/bin/sh\n"))
        self.assertNotIn("\r", text)
        self.assertIn('java -Xmx4G -jar paper.jar nogui "$@"\n', text)
        if os.name != "nt":
            self.assertTrue(os.access(path, os.X_OK))

    def test_split_java_line_windows(self):
        with mock.patch.object(platform_utils, "IS_WINDOWS", True):
            self.assertEqual(platform_utils.split_java_line("java -Xmx4G -jar server.jar %*"),
                             ["java", "-Xmx4G", "-jar", "server.jar", "nogui"])
            self.assertEqual(platform_utils.split_java_line("java -jar server.jar"),
                             ["java", "-jar", "server.jar", "nogui"])

    def test_split_java_line_linux(self):
        with mock.patch.object(platform_utils, "IS_WINDOWS", False):
            self.assertEqual(platform_utils.split_java_line('java -Xmx4G -jar "forge 1.12.jar" nogui "$@"'),
                             ["java", "-Xmx4G", "-jar", "forge 1.12.jar", "nogui"])
            self.assertEqual(platform_utils.split_java_line('java -jar server.jar "$@"'),
                             ["java", "-jar", "server.jar", "nogui"])

    def test_linux_uses_unix_args_of_server_copied_from_windows(self):
        lib = os.path.join(self.d, "libraries", "net", "neoforged", "neoforge", "21.4.1")
        os.makedirs(lib)
        open(os.path.join(lib, "unix_args.txt"), "w").close()
        line = "java @user_jvm_args.txt @libraries/net/neoforged/neoforge/21.4.1/win_args.txt %*"
        with mock.patch.object(platform_utils, "IS_WINDOWS", False):
            parts = platform_utils.split_java_line(line, self.d)
        self.assertIn("@libraries/net/neoforged/neoforge/21.4.1/unix_args.txt", parts)

    def test_fallback_command(self):
        with mock.patch.object(platform_utils, "IS_WINDOWS", True):
            self.assertEqual(platform_utils.script_fallback_command("run.bat")[:2], ["cmd.exe", "/c"])
        with mock.patch.object(platform_utils, "IS_WINDOWS", False):
            self.assertEqual(platform_utils.script_fallback_command("run.sh"), ["sh", "run.sh", "nogui"])


class LinuxUpdaterTest(unittest.TestCase):
    MANIFEST = {"version": "4.20.0", "build": 97, "setup_url": "https://github.com/QMon31/Voxarium/releases/download/x/S.exe",
                "sha256": "aa", "notes": "novità",
                "linux": {"version": "4.20.0", "build": 97, "url": "https://github.com/QMon31/Voxarium/releases/download/x/v.tar.gz",
                          "sha256": "bb", "size": 10}}

    def test_windows_reads_manifest_as_before(self):
        with mock.patch.object(pc_updater, "IS_LINUX", False):
            self.assertIs(pc_updater.platform_manifest(self.MANIFEST), self.MANIFEST)

    def test_linux_reads_linux_entry(self):
        with mock.patch.object(pc_updater, "IS_LINUX", True):
            m = pc_updater.platform_manifest(self.MANIFEST)
        self.assertEqual((m["build"], m["setup_url"], m["sha256"], m["notes"]),
                         (97, self.MANIFEST["linux"]["url"], "bb", "novità"))

    def test_linux_without_linux_entry_has_no_update(self):
        only_windows = {k: v for k, v in self.MANIFEST.items() if k != "linux"}
        with mock.patch.object(pc_updater, "IS_LINUX", True):
            self.assertEqual(pc_updater.platform_manifest(only_windows)["build"], 0)

    def test_update_archive_with_unsafe_path_is_refused(self):
        d = tempfile.mkdtemp(prefix="csm_linux_")
        try:
            archive = os.path.join(d, "bad.tar.gz")
            with tarfile.open(archive, "w:gz") as tf:
                info = tarfile.TarInfo("../evil.sh")
                info.size = 1
                tf.addfile(info, io.BytesIO(b"x"))
            with mock.patch.object(pc_updater.subprocess, "Popen") as popen:
                with self.assertRaises(ValueError):
                    pc_updater._run_linux_update(archive)
                popen.assert_not_called()
        finally:
            shutil.rmtree(d, ignore_errors=True)


class LinuxPackageTest(unittest.TestCase):
    def test_package_contents(self):
        import create_linux_package
        d = tempfile.mkdtemp(prefix="csm_linux_")
        cwd = os.getcwd()
        try:
            os.chdir(ROOT)
            out = create_linux_package.build(os.path.join(d, "v.tar.gz"))
            with tarfile.open(out) as tf:
                names = set(tf.getnames())
                install = tf.extractfile("voxarium/install.sh").read()
                mode = tf.getmember("voxarium/install.sh").mode
        finally:
            os.chdir(cwd)
            shutil.rmtree(d, ignore_errors=True)
        for f in create_linux_package._py_files() + ["install.sh", "voxarium.service", "requirements.txt",
                                                     "static/index.html", "static/app.js"]:
            self.assertIn("voxarium/" + f, names)
        for f in ("config.json", "instances.json", "launcher_config.json"):
            self.assertNotIn("voxarium/" + f, names)
        self.assertFalse(any(n.startswith("voxarium/certs") for n in names))
        self.assertNotIn(b"\r\n", install)
        self.assertTrue(mode & 0o111)


if __name__ == "__main__":
    unittest.main()
