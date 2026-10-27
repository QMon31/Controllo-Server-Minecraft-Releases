"""Controlli sull'aggiornamento automatico: PC Remoti (Launcher), annuncio dell'app (/api/app/version)."""

import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pc_updater  # noqa: E402
from pc_version import LEGACY_UPDATE_REPO, UPDATE_REPO  # noqa: E402


def _read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return fh.read()


class RemoteLauncherUpdateTest(unittest.TestCase):
    """I PC Remoti non hanno il backend: è il Launcher a controllare gli aggiornamenti, con gli stessi indirizzi."""

    def test_launcher_uses_same_manifest_and_repositories(self):
        src = _read("Launcher.cs")
        self.assertIn(f'ManifestUrl = "{pc_updater.MANIFEST_URL}"', src)
        for repo in (UPDATE_REPO, LEGACY_UPDATE_REPO):
            self.assertIn(f'"https://github.com/{repo}/releases/download/"', src)

    def test_launcher_checks_only_in_remote_mode_and_verifies_sha(self):
        src = _read("Launcher.cs")
        remote_branch = src[src.index("if (isRemoteMode)\n"):src.index("// Modalità Host Locale")]
        self.assertIn("RemoteUpdater.Start(trayIcon)", remote_branch)
        self.assertEqual(src.count("RemoteUpdater.Start("), 1)
        self.assertIn('throw new InvalidDataException("SHA-256")', src)
        self.assertIn('new ProcessStartInfo(setup, "--update")', src)

    def test_launcher_texts_in_all_languages(self):
        src = _read("Launcher.cs")
        block = src[src.index("static class LauncherText"):src.index("static class Program")]
        langs = re.findall(r'\{ "([a-z]{2})", new Dictionary<string, string>', block)
        self.assertEqual(sorted(langs), ["de", "en", "es", "fr", "it", "pt"])
        parts = re.split(r'\{ "[a-z]{2}", new Dictionary<string, string>', block)[1:]
        keys = [sorted(re.findall(r'\{ "(upd\.[a-z_]+)", "', p)) for p in parts]
        for k, lang in zip(keys, langs):
            self.assertEqual(k, keys[0], lang)
        for p in parts:  # stessi segnaposto in ogni lingua
            self.assertEqual(sorted(re.findall(r'\{\d\}', p)), sorted(re.findall(r'\{\d\}', parts[0])))


class InstallerUpdateKeepsChoicesTest(unittest.TestCase):
    """In aggiornamento automatico (--update) l'installer non deve rifare le scelte dell'installazione: prima riattivava
    l'avvio automatico spento dall'utente e ricreava i collegamenti cancellati."""

    def setUp(self):
        src = _read("Installer.cs")
        self.body = src[src.index("private void StartInstallation()"):src.index("// 4. Uninstaller Script and Registry")]

    def test_autostart_left_as_it_was(self):
        update_branch = self.body[self.body.index("if (update)"):
                                  self.body.index("else if (chkAutoStart != null && chkAutoStart.Checked)")]
        self.assertNotIn("DeleteValue", update_branch)
        self.assertIn("StartupShortcutExists() && !AutoStartRegistered()", update_branch)

    def test_shortcuts_only_renewed_where_present(self):
        self.assertIn("CreateDesktopShortcuts(mainExe, destDir, iconPath, shortcutName, shortcutDesc, update)", self.body)
        self.assertIn("chkStartMenu.Checked && (!update || hadStartMenu)", self.body)

    def test_no_admin_prompt_when_firewall_rules_exist(self):
        self.assertIn("!(Program.IsUpdateMode && FirewallRules.AlreadyPresent())", self.body)


class AppVersionFallbackTest(unittest.TestCase):
    def test_no_invented_app_version_when_version_json_missing(self):
        src = _read("app.py")
        body = src[src.index("def get_app_version"):src.index("def download_app_apk")]
        self.assertNotIn("version_code\": 68", body)
        self.assertIn("status_code=404", body)


if __name__ == "__main__":
    unittest.main()
