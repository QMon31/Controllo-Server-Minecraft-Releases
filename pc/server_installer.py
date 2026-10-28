"""
Multi-Type Automated Minecraft Server Creator & Installer
Supports installing:
- Forge: Official Forge servers with Mod Loader (1.21.1, 1.20.1, 1.19.2, 1.18.2, 1.16.5, 1.12.2, etc.)
- Vanilla: Official pure Minecraft Server JARs directly from Mojang (1.21.4, 1.21.1, 1.20.1, 1.18.2, etc.)
"""

import os
import re
import json
import time
import shutil
import urllib.request
import subprocess
import threading

# Java e gli installer Forge/NeoForge senza finestra cmd (il pannello gira senza console)
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
from typing import Callable, Dict, Any, Optional, List
from platform_utils import (LAUNCH_SCRIPT, FORGE_ARGS_FILE, download_file, read_json_with_backup, write_json_atomic,
                            write_launch_script)

USER_AGENT = "Voxarium (+https://github.com/QMon31/Voxarium)"

FORGE_PROMOTIONS_URL = "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
NEOFORGE_MAVEN_URL = "https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml"
MOJANG_MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"

FALLBACK_NEOFORGE_VERSIONS = [
    {"mc": "26.3", "mc_version": "26.3", "neoforge_version": "26.3.0.95", "label": "Minecraft 26.3 (NeoForge 26.3.0.95) [Ultima NeoForge]", "recommended": True, "type": "release"},
    {"mc": "26.2", "mc_version": "26.2", "neoforge_version": "26.2.0.93", "label": "Minecraft 26.2 (NeoForge 26.2.0.93)", "recommended": False, "type": "release"},
    {"mc": "26.1", "mc_version": "26.1", "neoforge_version": "26.1.2.122", "label": "Minecraft 26.1 (NeoForge 26.1.2.122)", "recommended": False, "type": "release"},
    {"mc": "1.21.11", "mc_version": "1.21.11", "neoforge_version": "21.11.48", "label": "Minecraft 1.21.11 (NeoForge 21.11.48)", "recommended": False, "type": "release"},
    {"mc": "1.21.4", "mc_version": "1.21.4", "neoforge_version": "21.4.161", "label": "Minecraft 1.21.4 (NeoForge 21.4.161) ⭐ [Release 1.21]", "recommended": True, "type": "release"},
    {"mc": "1.21.1", "mc_version": "1.21.1", "neoforge_version": "21.1.264", "label": "Minecraft 1.21.1 (NeoForge 21.1.264) [Recente Stabile / Consigliata]", "recommended": True, "type": "release"},
    {"mc": "1.20.6", "mc_version": "1.20.6", "neoforge_version": "20.6.144", "label": "Minecraft 1.20.6 (NeoForge 20.6.144)", "recommended": False, "type": "release"},
    {"mc": "1.20.4", "mc_version": "1.20.4", "neoforge_version": "20.4.253", "label": "Minecraft 1.20.4 (NeoForge 20.4.253) [Ottimizzata / Standard Mod]", "recommended": True, "type": "release"},
    {"mc": "1.20.2", "mc_version": "1.20.2", "neoforge_version": "20.2.93", "label": "Minecraft 1.20.2 (NeoForge 20.2.93)", "recommended": False, "type": "release"},
    {"mc": "1.20.1", "mc_version": "1.20.1", "neoforge_version": "47.1.106", "label": "Minecraft 1.20.1 (NeoForge 47.1.106) [Modpack 1.20.1]", "recommended": True, "type": "release"}
]

FALLBACK_FORGE_VERSIONS = [
    {"mc": "26.3", "forge": "66.0.13", "label": "26.3 [Ultima Forge 26.3 - Build 66.0.13]", "recommended": True},
    {"mc": "26.1.2", "forge": "64.0.0", "label": "26.1.2 (Forge 64.0)", "recommended": True},
    {"mc": "1.21.1", "forge": "52.1.0", "label": "1.21.1 (Recente / Stabile)", "recommended": True},
    {"mc": "1.20.1", "forge": "47.4.10", "label": "1.20.1 (Più Usata / Modpack Consigliata)", "recommended": True},
    {"mc": "1.20.4", "forge": "49.2.0", "label": "1.20.4 (Forge 49.2)", "recommended": False},
    {"mc": "1.20.6", "forge": "50.2.0", "label": "1.20.6 (Forge 50.2)", "recommended": False},
    {"mc": "1.19.2", "forge": "43.4.0", "label": "1.19.2 (Modpack Classico)", "recommended": False},
    {"mc": "1.18.2", "forge": "40.2.21", "label": "1.18.2 (Caves & Cliffs)", "recommended": False},
    {"mc": "1.16.5", "forge": "36.2.42", "label": "1.16.5 (Legacy Modpack)", "recommended": False},
    {"mc": "1.12.2", "forge": "14.23.5.2860", "label": "1.12.2 (Classica Modpack Storica)", "recommended": False},
]

FALLBACK_VANILLA_VERSIONS = [
    {"mc": "26.3", "label": "26.3 [Ultima Release Ufficiale Mojang]", "url": "https://piston-data.mojang.com/v1/objects/33680f5f2ac32864d6d7cf5e56a705fdb3e05f4c/server.jar"},
    {"mc": "26.1.2", "label": "26.1.2 (Release Ufficiale Mojang)", "url": "https://piston-data.mojang.com/v1/objects/97ccd4c0ed3f81bbb7bfacddd1090b0c56f9bc51/server.jar"},
    {"mc": "1.21.4", "label": "1.21.4 ⭐ [Release Mojang 1.21]", "url": "https://piston-data.mojang.com/v1/objects/4707d00eb834b446575d89a61a11b5d548d8c001/server.jar"},
    {"mc": "1.21.1", "label": "1.21.1 [Recente Stabile / Consigliata]", "url": "https://piston-data.mojang.com/v1/objects/59353fb40c36d304f2035d51e7d6e6baa98dc05c/server.jar"},
    {"mc": "1.20.4", "label": "1.20.4 (Stabile)", "url": "https://piston-data.mojang.com/v1/objects/8dd1a28015f51b1803213892b50b7b4fc76e594d/server.jar"},
    {"mc": "1.20.1", "label": "1.20.1 [Riferimento Stabile]", "url": "https://piston-data.mojang.com/v1/objects/84194a2f286ef7c14ed7ce0090dba59902951553/server.jar"},
    {"mc": "1.19.4", "label": "1.19.4 (Wild Update)", "url": "https://piston-data.mojang.com/v1/objects/8f3112a1049751cc472ec13e397eade5336ca7ae/server.jar"},
    {"mc": "1.18.2", "label": "1.18.2 (Caves & Cliffs)", "url": "https://piston-data.mojang.com/v1/objects/c8f83c5655308435b3dcf03c06d9fe8740a77469/server.jar"},
    {"mc": "1.16.5", "label": "1.16.5 (Nether Update)", "url": "https://piston-data.mojang.com/v1/objects/1b557e7b033b583cd9f66746b7a9ab1ec1673ced/server.jar"},
    {"mc": "1.12.2", "label": "1.12.2 (Classica)", "url": "https://piston-data.mojang.com/v1/objects/886945bfb2b978778c3a0288fd7fab09d315b25f/server.jar"}
]

FALLBACK_PAPER_VERSIONS = [
    {"mc": "26.3", "label": "Paper 26.3 [Ultima Release PaperMC]", "recommended": True},
    {"mc": "26.1.2", "label": "Paper 26.1.2 (Release Stabile PaperMC)", "recommended": True},
    {"mc": "1.21.4", "label": "Paper 1.21.4 ⭐ [Release Stabile PaperMC]", "recommended": True},
    {"mc": "1.21.1", "label": "Paper 1.21.1 [Recente Stabile / Consigliata Plugin]", "recommended": True},
    {"mc": "1.20.4", "label": "Paper 1.20.4 [Ottimizzata / Standard Plugin Spigot]", "recommended": True},
    {"mc": "1.20.1", "label": "Paper 1.20.1 [Stabile / Supporto Mod-Plugin]", "recommended": True},
    {"mc": "1.19.4", "label": "Paper 1.19.4 [Wild Update]", "recommended": False},
    {"mc": "1.18.2", "label": "Paper 1.18.2 [Caves & Cliffs]", "recommended": False},
    {"mc": "1.16.5", "label": "Paper 1.16.5 [Nether Update]", "recommended": False},
    {"mc": "1.12.2", "label": "Paper 1.12.2 [Classica Legacy]", "recommended": False},
]

FALLBACK_FABRIC_VERSIONS = [
    {"mc": "26.3", "label": "Fabric 26.3 [Ultima Release Fabric]", "recommended": True},
    {"mc": "26.1.2", "label": "Fabric 26.1.2 (Release Stabile Fabric)", "recommended": True},
    {"mc": "1.21.4", "label": "Fabric 1.21.4 ⭐ [Release Stabile Fabric]", "recommended": True},
    {"mc": "1.21.1", "label": "Fabric 1.21.1 [Recente Stabile / Consigliata Mod]", "recommended": True},
    {"mc": "1.20.4", "label": "Fabric 1.20.4 [Ottimizzata / Standard Mod]", "recommended": True},
    {"mc": "1.20.1", "label": "Fabric 1.20.1 [Riferimento Modpack]", "recommended": True},
    {"mc": "1.19.4", "label": "Fabric 1.19.4 [Wild Update]", "recommended": False},
    {"mc": "1.18.2", "label": "Fabric 1.18.2 [Caves & Cliffs]", "recommended": False},
    {"mc": "1.16.5", "label": "Fabric 1.16.5 [Nether Update]", "recommended": False},
    {"mc": "1.14.4", "label": "Fabric 1.14.4 [Village & Pillage]", "recommended": False},
]

def parse_mc_version(v: str):
    """
    Robust SemVer parser for Minecraft versions (releases and snapshots/pre-releases).
    Handles '26.3', '1.21.11', '26.4-snapshot-5', '1.21.11-rc3', '26w14a', '1.14_combat-0'.
    Returns a tuple suitable for reverse sorting so newer versions are first,
    and full releases take precedence over pre-releases of the same base.
    """
    v_str = str(v).strip()
    cal_m = re.match(r'^(\d+)w(\d+)([a-z])?$', v_str, re.IGNORECASE)
    if cal_m:
        year = int(cal_m.group(1))
        week = int(cal_m.group(2))
        sub = ord(cal_m.group(3).lower()) if cal_m.group(3) else 0
        return ([year, week, sub], 0, [-1])

    m = re.match(r'^(\d+(?:\.\d+)*)(?:[-_\s](.+))?$', v_str)
    if not m:
        nums = [int(x) for x in re.findall(r'\d+', v_str)]
        return (nums, 0, [])
    
    base_str = m.group(1)
    suffix = m.group(2) or ''
    base_nums = [int(x) for x in base_str.split('.')]
    if not suffix:
        return (base_nums, 1, [])
    else:
        suffix_nums = [int(x) for x in re.findall(r'\d+', suffix)]
        return (base_nums, 0, suffix_nums)

class ForgeServerInstaller:
    """Multi-type server installer: Forge, NeoForge, Fabric, Paper and Vanilla."""
    def __init__(self, config_path: str = "config.json"):
        self.config_path = config_path
        self.status = "idle"  # idle, downloading, installing, configuring, completed, error
        self.progress_pct = 0
        self.step_text = "In attesa"
        self.log_lines: List[str] = []
        self.error_message = ""
        self.target_dir = ""
        self.server_type = "forge"  # "forge", "neoforge", "fabric", "paper", "vanilla"
        self.selected_mc_version = ""
        self.selected_forge_version = ""
        self.selected_neoforge_version = ""
        self.lock = threading.Lock()
        # Chiamata con la cartella del nuovo server quando diventa quello attivo (il programma allinea i backup)
        self.on_server_ready: Optional[Callable[[str], None]] = None
        self._thread: Optional[threading.Thread] = None
        self._watcher_thread: Optional[threading.Thread] = None
        self._watcher_running: bool = False
        self._watcher_callback = None
        self.CACHE_TTL: int = 900  # 15 minutes TTL before automatic cache refresh
        self._cache_timestamps: Dict[str, float] = {}
        self._known_versions_set: Dict[str, set] = {}
        self._cached_forge_versions: Optional[List[Dict[str, Any]]] = None
        self._cached_neoforge_versions: Optional[List[Dict[str, Any]]] = None
        self._cached_vanilla_versions: Optional[List[Dict[str, Any]]] = None
        self._cached_paper_versions: Optional[List[Dict[str, Any]]] = None
        self._cached_fabric_versions: Optional[List[Dict[str, Any]]] = None

    def get_forge_versions(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Queries official Forge promotions API and returns ALL available MC versions with Forge builds."""
        now = time.time()
        if not force_refresh and self._cached_forge_versions and (now - self._cache_timestamps.get("forge", 0)) < self.CACHE_TTL:
            return self._cached_forge_versions

        try:
            req = urllib.request.Request(FORGE_PROMOTIONS_URL, headers={"User-Agent": "ControlloServerMinecraft/5.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                promos = data.get("promos", {})
                
                # Extract all unique MC versions without any prefix restriction
                raw_mc_set = set(k.split("-")[0] for k in promos.keys() if k)
                mc_versions = sorted(list(raw_mc_set), key=parse_mc_version, reverse=True)

                version_list = []
                for mc in mc_versions:
                    forge_build = promos.get(f"{mc}-recommended") or promos.get(f"{mc}-latest")
                    if forge_build:
                        is_rec = f"{mc}-recommended" in promos
                        tag = "Raccomandata" if is_rec else "Latest"
                        label = f"{mc} (Forge {forge_build} - {tag})"
                        if mc == "26.3":
                            label = f"{mc} [Ultima Forge 26.3 - Build {forge_build}]"
                        elif mc == "1.20.1":
                            label = f"{mc} ⭐ [Consigliata Modpack / Più usata - Forge {forge_build}]"
                        elif mc == "1.21.1":
                            label = f"{mc} [Recente Stabile - Forge {forge_build}]"
                        
                        version_list.append({
                            "mc": mc,
                            "mc_version": mc,
                            "forge": forge_build,
                            "forge_version": forge_build,
                            "label": label,
                            "type": "release",
                            "recommended": is_rec or mc in ["26.3", "1.20.1", "1.21.1"]
                        })

                if version_list:
                    self._cached_forge_versions = version_list
                    self._cache_timestamps["forge"] = now
                    return version_list
        except Exception as e:
            print(f"[ServerInstaller] Notice fetching Forge promos: {e}")

        fallback = []
        for fb in FALLBACK_FORGE_VERSIONS:
            item = dict(fb)
            item["mc_version"] = fb["mc"]
            item["forge_version"] = fb["forge"]
            fallback.append(item)
        self._cached_forge_versions = fallback
        self._cache_timestamps["forge"] = now
        return fallback

    def get_neoforge_versions(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Queries official NeoForge Maven metadata and returns ALL available MC versions with latest NeoForge builds."""
        now = time.time()
        if not force_refresh and getattr(self, "_cached_neoforge_versions", None) and (now - self._cache_timestamps.get("neoforge", 0)) < self.CACHE_TTL:
            return self._cached_neoforge_versions

        try:
            import xml.etree.ElementTree as ET
            req = urllib.request.Request(NEOFORGE_MAVEN_URL, headers={"User-Agent": "MinecraftServerControlCenter/5.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                tree = ET.fromstring(resp.read().decode("utf-8"))

            raw_versions = [v.text.strip() for v in tree.findall(".//version") if v.text]

            def map_nf_to_mc(v: str) -> str:
                if v.startswith("1.20.1"):
                    return "1.20.1"
                parts = v.split(".")
                if len(parts) >= 2:
                    major = parts[0]
                    minor = parts[1]
                    if major == "20":
                        return f"1.20.{minor}"
                    elif major == "21":
                        if minor == "0":
                            return "1.21"
                        return f"1.21.{minor}"
                    elif major.isdigit() and int(major) >= 26:
                        return f"{major}.{minor}"
                    return f"{major}.{minor}"
                return v

            mc_groups = {}
            for v in raw_versions:
                mc = map_nf_to_mc(v)
                if mc.startswith("0."):
                    continue
                if mc not in mc_groups:
                    mc_groups[mc] = []
                mc_groups[mc].append(v)

            # Ensure 1.20.1 is included (historical fork release)
            if "1.20.1" not in mc_groups:
                mc_groups["1.20.1"] = ["47.1.106"]

            sorted_mcs = sorted(mc_groups.keys(), key=parse_mc_version, reverse=True)

            v_list = []
            for mc in sorted_mcs:
                builds = mc_groups[mc]
                non_beta = [b for b in builds if "-beta" not in b and "-alpha" not in b]
                best_build = non_beta[-1] if non_beta else builds[-1]
                is_beta = "-beta" in best_build or "-alpha" in best_build
                vtype = "snapshot" if is_beta else "release"

                rec = False
                tag = ""
                if mc == "26.3":
                    tag = " [Ultima NeoForge 26.3]"
                    rec = True
                elif mc == "1.21.4":
                    tag = " ⭐ [Release 1.21 Stabile]"
                    rec = True
                elif mc == "1.21.1":
                    tag = " [Recente Stabile / Consigliata Mod]"
                    rec = True
                elif mc == "1.20.4":
                    tag = " [Ottimizzata / Standard Mod]"
                    rec = True
                elif mc == "1.20.1":
                    tag = " [Modpack 1.20.1 Compatibile]"
                    rec = True

                type_label = "NeoForge Mod Loader" if not is_beta else "NeoForge Beta / Dev"
                label = f"Minecraft {mc} (NeoForge {best_build} - {type_label}){tag}"

                v_list.append({
                    "mc": mc,
                    "mc_version": mc,
                    "neoforge_version": best_build,
                    "version": best_build,
                    "label": label,
                    "type": vtype,
                    "recommended": rec,
                    "all_builds": list(reversed(builds))
                })

            if v_list:
                self._cached_neoforge_versions = v_list
                self._cache_timestamps["neoforge"] = now
                return v_list
        except Exception as e:
            print(f"[ServerInstaller] Notice fetching NeoForge versions: {e}")

        fallback = []
        for fb in FALLBACK_NEOFORGE_VERSIONS:
            item = dict(fb)
            item["version"] = fb["neoforge_version"]
            fallback.append(item)
        self._cached_neoforge_versions = fallback
        self._cache_timestamps["neoforge"] = now
        return fallback

    def get_vanilla_versions(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Queries official Mojang version manifest and returns ALL available Vanilla releases and snapshots."""
        now = time.time()
        if not force_refresh and self._cached_vanilla_versions and (now - self._cache_timestamps.get("vanilla", 0)) < self.CACHE_TTL:
            return self._cached_vanilla_versions

        try:
            req = urllib.request.Request(MOJANG_MANIFEST_URL, headers={"User-Agent": "ControlloServerMinecraft/5.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                manifest_versions = data.get("versions", [])
                
                v_list = []
                found_ids = set()
                
                for v in manifest_versions:
                    vid = v.get("id", "")
                    vtype = v.get("type", "release")
                    if not vid or vid in found_ids or vtype not in ("release", "snapshot"):
                        continue
                    found_ids.add(vid)
                    
                    is_rel = (vtype == "release")
                    tag = ""
                    rec = False
                    if vid == "26.3":
                        tag = " [Ultima Release Ufficiale Mojang]"
                        rec = True
                    elif vid == "1.21.4":
                        tag = " ⭐ [Release Stabile 1.21]"
                        rec = True
                    elif vid == "1.21.1":
                        tag = " [Recente Stabile]"
                        rec = True
                    elif vid == "1.20.4":
                        tag = " [Stabile]"
                        rec = True
                    elif vid == "1.20.1":
                        tag = " [Consigliata Riferimento]"
                        rec = True
                    elif vid == "1.16.5":
                        tag = " [Nether Update]"
                    elif vid == "1.12.2":
                        tag = " [Classica]"

                    type_label = "Vanilla Release" if is_rel else "Snapshot Sperimentale"
                    label = f"Minecraft {vid} ({type_label}){tag}"

                    v_list.append({
                        "mc": vid,
                        "mc_version": vid,
                        "label": label,
                        "type": vtype,
                        "url": v.get("url"),
                        "recommended": rec
                    })

                v_list.sort(key=lambda item: parse_mc_version(item["mc"]), reverse=True)

                if v_list:
                    self._cached_vanilla_versions = v_list
                    self._cache_timestamps["vanilla"] = now
                    return v_list
        except Exception as e:
            print(f"[ServerInstaller] Notice fetching Vanilla versions: {e}")

        fallback = []
        for fb in FALLBACK_VANILLA_VERSIONS:
            item = dict(fb)
            item["mc_version"] = fb["mc"]
            fallback.append(item)
        self._cached_vanilla_versions = fallback
        self._cache_timestamps["vanilla"] = now
        return fallback

    def get_paper_versions(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Queries PaperMC Fill v3 API and returns ALL available Paper releases and pre-releases/RCs sorted chronologically."""
        now = time.time()
        if not force_refresh and getattr(self, "_cached_paper_versions", None) and (now - self._cache_timestamps.get("paper", 0)) < self.CACHE_TTL:
            return self._cached_paper_versions

        try:
            url = "https://fill.papermc.io/v3/projects/paper"
            req = urllib.request.Request(url, headers={"User-Agent": "Voxarium (+https://github.com/QMon31/Voxarium)"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                raw_versions_dict = data.get("versions", {})

                # Collect all versions from all groups
                all_v = []
                for group, v_list in raw_versions_dict.items():
                    for v in v_list:
                        if v not in all_v:
                            all_v.append(v)

                # Sort strictly in descending semver order
                sorted_v = sorted(all_v, key=parse_mc_version, reverse=True)

                v_list = []
                for vid in sorted_v:
                    is_prerelease = any(x in vid.lower() for x in ["-rc", "-pre", "-beta", "-alpha"])
                    vtype = "snapshot" if is_prerelease else "release"
                    tag = ""
                    rec = False
                    if vid == "26.3":
                        tag = " [Ultima Release PaperMC]"
                        rec = True
                    elif vid == "1.21.4":
                        tag = " ⭐ [Release Stabile PaperMC]"
                        rec = True
                    elif vid == "1.21.1":
                        tag = " [Recente Stabile / Consigliata Plugin]"
                        rec = True
                    elif vid == "1.20.4":
                        tag = " [Ottimizzata / Standard Plugin Spigot]"
                        rec = True
                    elif vid == "1.20.1":
                        tag = " [Stabile / Supporto Mod-Plugin]"
                        rec = True

                    type_label = "PaperMC High Performance" if not is_prerelease else "PaperMC RC / Pre-Release"
                    label = f"Paper {vid} ({type_label}){tag}"

                    v_list.append({
                        "mc": vid,
                        "mc_version": vid,
                        "label": label,
                        "type": vtype,
                        "recommended": rec
                    })

                if v_list:
                    self._cached_paper_versions = v_list
                    self._cache_timestamps["paper"] = now
                    return v_list
        except Exception as e:
            print(f"[ServerInstaller] Notice fetching Paper versions: {e}")

        fallback = []
        for fb in FALLBACK_PAPER_VERSIONS:
            item = dict(fb)
            item["mc_version"] = fb["mc"]
            fallback.append(item)
        self._cached_paper_versions = fallback
        self._cache_timestamps["paper"] = now
        return fallback

    def get_fabric_versions(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Queries official Fabric Meta API and returns ALL supported Minecraft versions (stable and snapshots)."""
        now = time.time()
        if not force_refresh and getattr(self, "_cached_fabric_versions", None) and (now - self._cache_timestamps.get("fabric", 0)) < self.CACHE_TTL:
            return self._cached_fabric_versions

        try:
            url = "https://meta.fabricmc.net/v2/versions/game"
            req = urllib.request.Request(url, headers={"User-Agent": "MinecraftServerControlCenter/5.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                
                # Sort all versions descending
                raw_versions = [v for v in data if v.get("version")]
                raw_versions.sort(key=lambda v: parse_mc_version(v.get("version", "")), reverse=True)

                v_list = []
                for item in raw_versions:
                    vid = item.get("version")
                    is_stable = bool(item.get("stable"))
                    vtype = "release" if is_stable else "snapshot"
                    tag = ""
                    rec = False
                    if vid == "26.3":
                        tag = " [Ultima Release Fabric]"
                        rec = True
                    elif vid == "1.21.4":
                        tag = " ⭐ [Release Stabile Fabric]"
                        rec = True
                    elif vid == "1.21.1":
                        tag = " [Recente Stabile / Consigliata Mod]"
                        rec = True
                    elif vid == "1.20.1":
                        tag = " [Riferimento Modpack]"
                        rec = True

                    type_label = "Fabric Mod Loader" if is_stable else "Fabric Snapshot / Dev"
                    label = f"Fabric {vid} ({type_label}){tag}"

                    v_list.append({
                        "mc": vid,
                        "mc_version": vid,
                        "label": label,
                        "type": vtype,
                        "stable": is_stable,
                        "recommended": rec
                    })

                if v_list:
                    self._cached_fabric_versions = v_list
                    self._cache_timestamps["fabric"] = now
                    return v_list
        except Exception as e:
            print(f"[ServerInstaller] Notice fetching Fabric versions: {e}")

        fallback = []
        for fb in FALLBACK_FABRIC_VERSIONS:
            item = dict(fb)
            item["mc_version"] = fb["mc"]
            fallback.append(item)
        self._cached_fabric_versions = fallback
        self._cache_timestamps["fabric"] = now
        return fallback

    # ---- Download e controlli comuni ----
    def _download(self, url: str, dest: str, pct_from: int, pct_to: int, hashes: Optional[Dict[str, str]] = None,
                  timeout: int = 40):
        """Download con file .part, impronta (se nota) e controllo del .jar: mai un file rotto nella cartella."""
        def _progress(done: int, total: int):
            if total:
                with self.lock:
                    self.progress_pct = min(pct_to, pct_from + int(done * (pct_to - pct_from) / total))
        download_file(url, dest, USER_AGENT, timeout=timeout, hashes=hashes, progress=_progress)

    def _maven_hashes(self, url: str) -> Optional[Dict[str, str]]:
        """SHA-1 pubblicato accanto al file su Maven (Forge, NeoForge); None se non si trova."""
        try:
            req = urllib.request.Request(url + ".sha1", headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=10) as resp:
                sha1 = (resp.read(200).decode("ascii", "ignore").strip().split() or [""])[0].lower()
            return {"sha1": sha1} if re.fullmatch(r"[0-9a-f]{40}", sha1) else None
        except Exception:
            return None

    def _fabric_installer_version(self) -> str:
        try:
            req = urllib.request.Request("https://meta.fabricmc.net/v2/versions/installer", headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            stable = [i for i in data if i.get("stable")] or data
            if stable and stable[0].get("version"):
                return stable[0]["version"]
        except Exception as e:
            self._append_log(f"[INFO] Avviso recupero installer Fabric: {e}")
        return "1.0.1"

    def _mojang_version_info(self, mc_version: str) -> Optional[Dict[str, Any]]:
        """Dal manifest ufficiale di Mojang: indirizzo e SHA-1 del server.jar e Java richiesta. None se non si trova."""
        cache = getattr(self, "_mojang_info_cache", None)
        if cache is None:
            cache = self._mojang_info_cache = {}
        if mc_version in cache:
            return cache[mc_version]
        try:
            req = urllib.request.Request(MOJANG_MANIFEST_URL, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=10) as resp:
                mdata = json.loads(resp.read().decode("utf-8"))
            entry = next((v for v in mdata.get("versions", []) if v.get("id") == mc_version), None)
            if not entry or not entry.get("url"):
                return None
            req = urllib.request.Request(entry["url"], headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=10) as resp:
                pkg = json.loads(resp.read().decode("utf-8"))
            server = (pkg.get("downloads") or {}).get("server") or {}
            info = {"server_url": server.get("url"), "server_sha1": server.get("sha1"),
                    "java": (pkg.get("javaVersion") or {}).get("majorVersion")}
            cache[mc_version] = info
            return info
        except Exception as e:
            self._append_log(f"[INFO] Elenco versioni Mojang non raggiungibile: {e}")
            return None

    def required_java(self, mc_version: str) -> int:
        """Versione di Java che serve per questa versione di Minecraft (dal manifest Mojang; se non risponde, la
        regola nota: 26.x -> 25, da 1.20.5 -> 21, da 1.18 -> 17, 1.17 -> 16, prima -> 8)."""
        info = self._mojang_version_info(mc_version)
        if info and isinstance(info.get("java"), int):
            return info["java"]
        parsed = parse_mc_version(mc_version)
        v = tuple(parsed[0]) if parsed and isinstance(parsed[0], list) else ()
        if v and v[0] >= 26:
            return 25
        if v >= (1, 20, 5):
            return 21
        if v >= (1, 18):
            return 17
        if v >= (1, 17):
            return 16
        return 8

    @staticmethod
    def java_major(java_cmd: Optional[str] = None) -> Optional[int]:
        """Versione principale della Java del PC (8, 17, 21, 25...); None se non c'è o non si legge."""
        java_cmd = java_cmd or shutil.which("java")
        if not java_cmd:
            return None
        try:
            res = subprocess.run([java_cmd, "-version"], capture_output=True, text=True, timeout=10, creationflags=NO_WINDOW)
            m = re.search(r'version "(\d+)(?:\.(\d+))?', (res.stderr or "") + (res.stdout or ""))
            if not m:
                return None
            major = int(m.group(1))
            return int(m.group(2) or 0) if major == 1 else major   # "1.8.0_401" = Java 8
        except Exception:
            return None

    def check_prerequisites(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Checks Java installation, version, suggests a default path, and returns version lists for all types."""
        java_cmd = shutil.which("java")
        java_ok = False
        java_ver_str = "Non trovato"
        
        if java_cmd:
            try:
                res = subprocess.run([java_cmd, "-version"], capture_output=True, text=True, timeout=5, creationflags=NO_WINDOW)
                out = res.stderr or res.stdout
                java_ver_str = out.split("\n")[0] if out else "Installato"
                java_ok = True
            except Exception as e:
                java_ver_str = f"Errore: {e}"
        
        user_desktop = os.path.join(os.environ.get("USERPROFILE", os.path.expanduser("~")), "Desktop")
        default_dir = os.path.join(user_desktop, "Minecraft_Server")
        forge_vers = self.get_forge_versions(force_refresh=force_refresh)
        neoforge_vers = self.get_neoforge_versions(force_refresh=force_refresh)
        fabric_vers = self.get_fabric_versions(force_refresh=force_refresh)
        paper_vers = self.get_paper_versions(force_refresh=force_refresh)
        vanilla_vers = self.get_vanilla_versions(force_refresh=force_refresh)

        return {
            "java_installed": bool(java_cmd),
            "java_major": self.java_major(java_cmd) if java_cmd else None,
            "java_ok": java_ok,
            "java_available": java_ok,
            "java_version": java_ver_str,
            "default_dir": default_dir,
            "desktop_dir": user_desktop,
            "server_types": ["forge", "neoforge", "fabric", "paper", "vanilla"],
            "forge_versions": forge_vers,
            "neoforge_versions": neoforge_vers,
            "fabric_versions": fabric_vers,
            "paper_versions": paper_vers,
            "vanilla_versions": vanilla_vers,
            "stats": {
                "forge_total": len(forge_vers),
                "neoforge_total": len(neoforge_vers),
                "neoforge_releases": len([v for v in neoforge_vers if v.get("type") == "release"]),
                "neoforge_snapshots": len([v for v in neoforge_vers if v.get("type") != "release"]),
                "fabric_total": len(fabric_vers),
                "fabric_releases": len([v for v in fabric_vers if v.get("type") == "release"]),
                "fabric_snapshots": len([v for v in fabric_vers if v.get("type") != "release"]),
                "paper_total": len(paper_vers),
                "paper_releases": len([v for v in paper_vers if v.get("type") == "release"]),
                "paper_snapshots": len([v for v in paper_vers if v.get("type") != "release"]),
                "vanilla_total": len(vanilla_vers),
                "vanilla_releases": len([v for v in vanilla_vers if v.get("type") == "release"]),
                "vanilla_snapshots": len([v for v in vanilla_vers if v.get("type") != "release"]),
            },
            "versions": forge_vers  # Backwards compatibility
        }

    def check_for_new_versions(self, force_refresh: bool = True) -> Dict[str, Any]:
        """
        Polls upstream official APIs, updates version lists, and identifies any brand-new releases or snapshots.
        Returns a dictionary with new versions found, updated stats, and full version lists.
        """
        prereqs = self.check_prerequisites(force_refresh=force_refresh)
        loaders = {
            "forge": prereqs.get("forge_versions", []),
            "neoforge": prereqs.get("neoforge_versions", []),
            "fabric": prereqs.get("fabric_versions", []),
            "paper": prereqs.get("paper_versions", []),
            "vanilla": prereqs.get("vanilla_versions", [])
        }

        new_versions = []
        for loader_name, versions in loaders.items():
            current_keys = set()
            for item in versions:
                k = item.get("mc_version") or item.get("mc")
                if k:
                    current_keys.add(k)

            known = self._known_versions_set.get(loader_name)
            if known is not None:
                diff = current_keys - known
                if diff:
                    for d in sorted(list(diff), key=parse_mc_version, reverse=True):
                        match_item = next((x for x in versions if (x.get("mc_version") or x.get("mc")) == d), None)
                        label = match_item.get("label", f"{loader_name.capitalize()} {d}") if match_item else f"{loader_name.capitalize()} {d}"
                        vtype = match_item.get("type", "release") if match_item else "release"
                        new_versions.append({
                            "loader": loader_name,
                            "version": d,
                            "label": label,
                            "type": vtype
                        })
            self._known_versions_set[loader_name] = current_keys

        return {
            "success": True,
            "has_new_versions": len(new_versions) > 0,
            "new_versions": new_versions,
            "stats": prereqs.get("stats", {}),
            "server_types": prereqs.get("server_types", []),
            "forge_versions": prereqs.get("forge_versions", []),
            "neoforge_versions": prereqs.get("neoforge_versions", []),
            "fabric_versions": prereqs.get("fabric_versions", []),
            "paper_versions": prereqs.get("paper_versions", []),
            "vanilla_versions": prereqs.get("vanilla_versions", []),
            "timestamp": time.time()
        }

    def start_version_watcher(self, interval_seconds: int = 1200, callback=None):
        """Starts a background daemon thread that periodically checks for new releases across all modloaders and Mojang."""
        if getattr(self, "_watcher_running", False):
            return
        self._watcher_running = True
        self._watcher_callback = callback

        def _watcher_worker():
            # Initial seed of known versions without broadcasting notifications
            try:
                self.check_for_new_versions(force_refresh=False)
            except Exception as ex:
                print(f"[VersionWatcher] Notice initial scan: {ex}")

            while self._watcher_running:
                try:
                    time.sleep(interval_seconds)
                    if not self._watcher_running:
                        break
                    res = self.check_for_new_versions(force_refresh=True)
                    if res.get("has_new_versions"):
                        new_list = res.get("new_versions", [])
                        print(f"[VersionWatcher] RILEVATE {len(new_list)} NUOVE VERSIONI MINECRAFT:")
                        for nv in new_list:
                            print(f"  -> {nv['loader'].upper()}: {nv['version']} ({nv.get('label')})")
                        if self._watcher_callback and callable(self._watcher_callback):
                            try:
                                self._watcher_callback(res)
                            except Exception as cb_err:
                                print(f"[VersionWatcher] Errore esecuzione callback: {cb_err}")
                except Exception as e:
                    print(f"[VersionWatcher] Errore ciclo di monitoraggio: {e}")
                    time.sleep(60)

        self._watcher_thread = threading.Thread(target=_watcher_worker, daemon=True, name="VersionWatcherThread")
        self._watcher_thread.start()
        print(f"[ServerInstaller] Automatic Version Watcher avviato (intervallo: {interval_seconds}s).")

    def stop_version_watcher(self):
        self._watcher_running = False

    def get_status(self) -> Dict[str, Any]:
        with self.lock:
            in_prog = self.status in ["downloading", "installing", "configuring", "extracting"]
            is_done = self.status in ["completed", "error"]
            is_ok = self.status == "completed"
            return {
                "status": self.status,
                "in_progress": in_prog,
                "completed": is_done,
                "success": is_ok,
                "server_type": self.server_type,
                "progress_pct": self.progress_pct,
                "progress": self.progress_pct,
                "step_text": self.step_text,
                "step": self.step_text,
                "error_message": self.error_message,
                "error": self.error_message,
                "target_dir": self.target_dir,
                "mc_version": self.selected_mc_version,
                "forge_version": self.selected_forge_version,
                "neoforge_version": self.selected_neoforge_version,
                "log_lines": self.log_lines[-120:],
                "logs": self.log_lines[-120:]
            }

    def start_installation(
        self,
        target_dir: str,
        server_type: str = "forge",
        mc_version: str = "1.20.1",
        ram_gb: int = 8,
        port: int = 32769,
        server_manager=None
    ) -> bool:
        with self.lock:
            if self.status in ["downloading", "installing", "configuring", "extracting"]:
                return False
            self.status = "downloading"
            self.progress_pct = 5
            self.step_text = "Inizializzazione installazione..."
            self.log_lines = []
            self.error_message = ""
            self.server_type = server_type
            self.target_dir = os.path.abspath(target_dir)
            self.selected_mc_version = mc_version
            self.selected_forge_version = ""

        self._thread = threading.Thread(
            target=self._run_install_worker,
            args=(self.target_dir, server_type, mc_version, ram_gb, port, server_manager),
            daemon=True
        )
        self._thread.start()
        return True

    def _append_log(self, line: str):
        with self.lock:
            clean = line.strip()
            if clean:
                self.log_lines.append(clean)
                if len(self.log_lines) > 300:
                    self.log_lines = self.log_lines[-300:]

    def _run_install_worker(
        self,
        target_dir: str,
        server_type: str,
        mc_version: str,
        ram_gb: int,
        port: int,
        server_manager
    ):
        try:
            # Java giusta prima di scaricare: prima l'installazione riusciva e poi il server non partiva
            # (UnsupportedClassVersionError), senza un messaggio chiaro
            with self.lock:
                self.step_text = "Controllo della versione di Java..."
            need = self.required_java(mc_version)
            have = self.java_major()
            if have is None:
                raise RuntimeError(f"Java non trovata sul PC: installa Java {need} (per esempio da adoptium.net) e riprova.")
            if have < need:
                raise RuntimeError(f"Minecraft {mc_version} richiede Java {need}, ma sul PC c'è Java {have}: "
                                   "installa la versione richiesta (per esempio da adoptium.net) e riprova.")
            self._append_log(f"[OK] Java {have} (richiesta Java {need} o più recente).")
            if server_type == "fabric":
                self._install_fabric(target_dir, mc_version, ram_gb, port, server_manager)
            elif server_type == "paper":
                self._install_paper(target_dir, mc_version, ram_gb, port, server_manager)
            elif server_type == "neoforge":
                self._install_neoforge(target_dir, mc_version, ram_gb, port, server_manager)
            elif server_type == "vanilla":
                self._install_vanilla(target_dir, mc_version, ram_gb, port, server_manager)
            else:
                self._install_forge(target_dir, mc_version, ram_gb, port, server_manager)
        except Exception as e:
            err = str(e)
            self._append_log(f"[ERRORE CRITICO] {err}")
            with self.lock:
                self.status = "error"
                self.error_message = err
                self.step_text = f"Errore: {err}"

    # =========================================================
    # 1. VANILLA SERVER INSTALLER
    # =========================================================
    def _install_vanilla(self, target_dir: str, mc_version: str, ram_gb: int, port: int, server_manager):
        self._append_log(f"=== INSTALLAZIONE SERVER MINECRAFT VANILLA {mc_version} ===")
        self._append_log(f"Cartella destinazione: {target_dir}")
        self._append_log(f"RAM assegnata: {ram_gb} GB | Porta UPnP: {port}")

        # Step 1: Create directory
        with self.lock:
            self.step_text = f"Creazione cartella per Vanilla {mc_version}..."
            self.progress_pct = 10
        os.makedirs(target_dir, exist_ok=True)
        self._append_log("[OK] Cartella server creata.")

        # Step 2: Resolve official Mojang server.jar download URL
        with self.lock:
            self.step_text = f"Risoluzione download ufficiale Mojang per {mc_version}..."
            self.progress_pct = 15

        # Prima l'elenco ufficiale di Mojang (indirizzo e impronta sempre giusti); l'elenco interno solo se Mojang
        # non risponde. Prima si usava subito l'elenco interno, che per 26.3 e 26.1.2 puntava a file di altre versioni.
        info = self._mojang_version_info(mc_version)
        jar_hashes = None
        if info and info.get("server_url"):
            server_jar_url = info["server_url"]
            jar_hashes = {"sha1": info.get("server_sha1")}
        else:
            server_jar_url = next((fb["url"] for fb in FALLBACK_VANILLA_VERSIONS if fb["mc"] == mc_version and fb.get("url")), None)
            if server_jar_url:
                self._append_log("[INFO] Elenco Mojang non raggiungibile: uso l'indirizzo salvato nel programma.")
                m_sha = re.search(r"/objects/([0-9a-f]{40})/", server_jar_url)
                jar_hashes = {"sha1": m_sha.group(1)} if m_sha else None
        if not server_jar_url:
            # Prima si installava in silenzio la 1.20.1 al posto della versione scelta
            raise RuntimeError(f"Minecraft {mc_version} non trovato nell'elenco ufficiale di Mojang (o Mojang non "
                               "raggiungibile): controlla la connessione e riprova.")

        # Step 3: Download server.jar
        with self.lock:
            self.step_text = f"Download server.jar ufficiale Mojang ({mc_version})..."
            self.progress_pct = 25

        target_jar = os.path.join(target_dir, "server.jar")
        self._append_log(f"Download da: {server_jar_url}")
        
        self._download(server_jar_url, target_jar, 25, 60, hashes=jar_hashes, timeout=30)

        self._append_log(f"[OK] Download completato ({os.path.getsize(target_jar)} bytes).")

        # Step 4: Accept EULA
        with self.lock:
            self.status = "configuring"
            self.step_text = "Accettazione automatica EULA Mojang..."
            self.progress_pct = 70

        eula_path = os.path.join(target_dir, "eula.txt")
        with open(eula_path, "w", encoding="utf-8") as f:
            f.write("# By changing the setting below to TRUE you are indicating your agreement to the EULA (https://aka.ms/MinecraftEULA).\n")
            f.write(f"# Auto-accepted by Voxarium on {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("eula=true\n")
        self._append_log("[OK] EULA accettata (eula=true).")

        # Step 5: Generate optimized server.properties
        with self.lock:
            self.progress_pct = 80
            self.step_text = "Configurazione server.properties e porta..."

        props_path = os.path.join(target_dir, "server.properties")
        with open(props_path, "w", encoding="utf-8") as f:
            f.write(f"# Minecraft Vanilla Server Properties - {mc_version}\n")
            f.write("# Generato automaticamente da Voxarium\n")
            f.write(f"server-port={port}\n")
            f.write(f"motd=Server Minecraft Vanilla {mc_version} - Pronto a Giocare\n")
            f.write("max-players=20\n")
            f.write("online-mode=true\n")
            f.write("difficulty=easy\n")
            f.write("gamemode=survival\n")
            f.write("enable-command-block=true\n")
            f.write("view-distance=10\n")
            f.write("simulation-distance=8\n")
            f.write("sync-chunk-writes=true\n")
            f.write("enable-rcon=false\n")
        self._append_log(f"[OK] server.properties configurato (porta UPnP: {port}).")

        # Step 6: Create run.bat (run.sh su Linux)
        with self.lock:
            self.progress_pct = 90
            self.step_text = f"Creazione script di avvio {LAUNCH_SCRIPT}..."

        launch_cmd = f"java -Xms{ram_gb}G -Xmx{ram_gb}G -jar server.jar nogui"
        run_bat_path = write_launch_script(target_dir, f"Vanilla {mc_version}", launch_cmd)
        self._append_log(f"[OK] {LAUNCH_SCRIPT} creato con allocazione {ram_gb} GB RAM.")

        # Step 7: Update config.json & reload manager
        self._finalize_config(target_dir, run_bat_path, launch_cmd, port, server_manager)

        with self.lock:
            self.status = "completed"
            self.progress_pct = 100
            self.step_text = f"Installazione completata con successo! Server Vanilla {mc_version} pronto all'uso."

        self._append_log(f"=== INSTALLAZIONE VANILLA {mc_version} COMPLETATA! ===")

    # =========================================================
    # 2. PAPER SERVER INSTALLER (PaperMC High Performance)
    # =========================================================
    def _install_paper(self, target_dir: str, mc_version: str, ram_gb: int, port: int, server_manager):
        self.server_type = "paper"
        self.selected_mc_version = mc_version
        self._append_log(f"=== INSTALLAZIONE SERVER MINECRAFT PAPER {mc_version} (PaperMC) ===")
        self._append_log(f"Cartella destinazione: {target_dir}")
        self._append_log(f"RAM assegnata: {ram_gb} GB | Porta UPnP: {port}")

        # Step 1: Create directory & plugins folder
        with self.lock:
            self.step_text = f"Creazione cartella per Paper {mc_version}..."
            self.progress_pct = 10
        os.makedirs(target_dir, exist_ok=True)
        plugins_dir = os.path.join(target_dir, "plugins")
        os.makedirs(plugins_dir, exist_ok=True)
        self._append_log("[OK] Cartella server e directory plugins/ create.")

        # Step 2: Query PaperMC Fill v3 API for the latest stable build
        with self.lock:
            self.step_text = f"Risoluzione ultima build PaperMC per {mc_version}..."
            self.progress_pct = 15

        server_jar_url = None
        build_id = None
        jar_name = f"paper-{mc_version}.jar"
        jar_hashes = None

        try:
            api_url = f"https://fill.papermc.io/v3/projects/paper/versions/{mc_version}/builds"
            req = urllib.request.Request(api_url, headers={"User-Agent": "Voxarium (+https://github.com/QMon31/Voxarium)"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                builds = json.loads(resp.read().decode("utf-8"))
                if isinstance(builds, list) and len(builds) > 0:
                    stable_builds = [b for b in builds if b.get("channel") == "STABLE"]
                    target_build = max(stable_builds or builds, key=lambda b: b.get("id", 0))
                    build_id = target_build.get("id")
                    dl_info = target_build.get("downloads", {}).get("server:default", {})
                    server_jar_url = dl_info.get("url")
                    jar_hashes = {"sha256": (dl_info.get("checksums") or {}).get("sha256")}
                    if dl_info.get("name"):
                        jar_name = dl_info.get("name")
                    self._append_log(f"[INFO] Trovata build Paper #{build_id} (Canale: {target_build.get('channel', 'STABLE')}).")
        except Exception as e:
            self._append_log(f"[INFO] Avviso recupero build PaperMC API: {e}")

        # Fallback download URLs if Fill v3 API couldn't be reached
        if not server_jar_url:
            if build_id:
                server_jar_url = f"https://fill.papermc.io/v3/projects/paper/versions/{mc_version}/builds/{build_id}/downloads/paper-{mc_version}-{build_id}.jar"
            else:
                server_jar_url = f"https://fill.papermc.io/v3/projects/paper/versions/{mc_version}/builds/latest/downloads/paper-{mc_version}.jar"
            self._append_log(f"[INFO] Utilizzo URL fallback per Paper {mc_version}.")

        # Step 3: Download paper.jar
        with self.lock:
            self.step_text = f"Download Paper {mc_version} JAR ({jar_name})..."
            self.progress_pct = 25

        target_jar = os.path.join(target_dir, "paper.jar")
        self._append_log(f"Download in corso da: {server_jar_url}")

        self._download(server_jar_url, target_jar, 25, 60, hashes=jar_hashes, timeout=40)

        # Also create a copy as server.jar for universal compatibility
        try:
            shutil.copy2(target_jar, os.path.join(target_dir, "server.jar"))
        except Exception as e:
            self._append_log(f"[AVVISO] Copia di server.jar non riuscita: {e}")

        self._append_log(f"[OK] Download Paper completato ({os.path.getsize(target_jar)} bytes).")

        # Step 4: Accept EULA
        with self.lock:
            self.status = "configuring"
            self.step_text = "Accettazione automatica EULA Mojang..."
            self.progress_pct = 70

        eula_path = os.path.join(target_dir, "eula.txt")
        with open(eula_path, "w", encoding="utf-8") as f:
            f.write("# By changing the setting below to TRUE you are indicating your agreement to the EULA (https://aka.ms/MinecraftEULA).\n")
            f.write(f"# Auto-accepted by Voxarium on {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("eula=true\n")
        self._append_log("[OK] EULA accettata (eula=true).")

        # Step 5: Generate optimized server.properties
        with self.lock:
            self.progress_pct = 80
            self.step_text = "Configurazione server.properties e porta UPnP..."

        props_path = os.path.join(target_dir, "server.properties")
        with open(props_path, "w", encoding="utf-8") as f:
            f.write(f"# Minecraft Paper Server Properties - {mc_version}\n")
            f.write("# Generato automaticamente da Voxarium\n")
            f.write(f"server-port={port}\n")
            f.write(f"motd=Server Minecraft Paper {mc_version} - High Performance Plugins\n")
            f.write("max-players=20\n")
            f.write("online-mode=true\n")
            f.write("difficulty=easy\n")
            f.write("gamemode=survival\n")
            f.write("enable-command-block=true\n")
            f.write("view-distance=10\n")
            f.write("simulation-distance=8\n")
            f.write("sync-chunk-writes=true\n")
            f.write("enable-rcon=false\n")
        self._append_log(f"[OK] server.properties configurato (porta UPnP: {port}).")

        # Step 6: Create run.bat (run.sh su Linux)
        with self.lock:
            self.progress_pct = 90
            self.step_text = f"Creazione script di avvio {LAUNCH_SCRIPT}..."

        launch_cmd = f"java -Xms{ram_gb}G -Xmx{ram_gb}G -jar paper.jar nogui"
        run_bat_path = write_launch_script(target_dir, f"Paper {mc_version}", launch_cmd)
        self._append_log(f"[OK] {LAUNCH_SCRIPT} creato con allocazione {ram_gb} GB RAM.")

        # Step 7: Update config.json & reload manager
        self._finalize_config(target_dir, run_bat_path, launch_cmd, port, server_manager)

        with self.lock:
            self.status = "completed"
            self.progress_pct = 100
            self.step_text = f"Installazione completata con successo! Server Paper {mc_version} pronto all'uso."

        self._append_log(f"=== INSTALLAZIONE PAPER {mc_version} COMPLETATA! ===")

    # =========================================================
    # 3. FABRIC SERVER INSTALLER (Fabric Loader Lightweight Mods)
    # =========================================================
    def _install_fabric(self, target_dir: str, mc_version: str, ram_gb: int, port: int, server_manager):
        self.server_type = "fabric"
        self.selected_mc_version = mc_version
        self._append_log(f"=== INSTALLAZIONE SERVER MINECRAFT FABRIC {mc_version} ===")
        self._append_log(f"Cartella destinazione: {target_dir}")
        self._append_log(f"RAM assegnata: {ram_gb} GB | Porta UPnP: {port}")

        # Step 1: Create directory & mods folder
        with self.lock:
            self.step_text = f"Creazione cartella per Fabric {mc_version}..."
            self.progress_pct = 10
        os.makedirs(target_dir, exist_ok=True)
        mods_dir = os.path.join(target_dir, "mods")
        os.makedirs(mods_dir, exist_ok=True)
        self._append_log("[OK] Cartella server e directory mods/ create.")

        # Step 2: Query latest Fabric loader version
        with self.lock:
            self.step_text = f"Risoluzione ultimo Fabric Loader per {mc_version}..."
            self.progress_pct = 15

        loader_ver = "0.16.9"
        try:
            lurl = "https://meta.fabricmc.net/v2/versions/loader"
            req_l = urllib.request.Request(lurl, headers={"User-Agent": "Voxarium (+https://github.com/QMon31/Voxarium)"})
            with urllib.request.urlopen(req_l, timeout=5) as lresp:
                loaders_data = json.loads(lresp.read().decode("utf-8"))
                if isinstance(loaders_data, list) and len(loaders_data) > 0:
                    # Il primo dell'elenco può essere una versione di prova: si prende il primo stabile
                    stable = [l for l in loaders_data if l.get("stable")]
                    loader_ver = (stable or loaders_data)[0].get("version", loader_ver)
            self._append_log(f"[INFO] Trovato Fabric Loader v{loader_ver}.")
        except Exception as e:
            self._append_log(f"[INFO] Avviso recupero Fabric loader: {e}")

        # Step 3: Download official fabric server jar
        with self.lock:
            self.step_text = f"Download Fabric Server {mc_version} (Loader {loader_ver})..."
            self.progress_pct = 25

        installer_ver = self._fabric_installer_version()
        server_jar_url = f"https://meta.fabricmc.net/v2/versions/loader/{mc_version}/{loader_ver}/{installer_ver}/server/jar"
        jar_hashes = None  # Fabric non pubblica l'impronta di questo file: si controlla che sia un .jar valido
        target_jar = os.path.join(target_dir, "fabric-server-launch.jar")
        self._append_log(f"Download da: {server_jar_url}")

        self._download(server_jar_url, target_jar, 25, 60, hashes=jar_hashes, timeout=40)

        # Also copy as server.jar for universal compatibility
        try:
            shutil.copy2(target_jar, os.path.join(target_dir, "server.jar"))
        except Exception as e:
            self._append_log(f"[AVVISO] Copia di server.jar non riuscita: {e}")

        self._append_log(f"[OK] Download Fabric Server completato ({os.path.getsize(target_jar)} bytes).")

        # Step 4: Accept EULA
        with self.lock:
            self.status = "configuring"
            self.step_text = "Accettazione automatica EULA Mojang..."
            self.progress_pct = 70

        eula_path = os.path.join(target_dir, "eula.txt")
        with open(eula_path, "w", encoding="utf-8") as f:
            f.write("# By changing the setting below to TRUE you are indicating your agreement to the EULA (https://aka.ms/MinecraftEULA).\n")
            f.write(f"# Auto-accepted by Voxarium on {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("eula=true\n")
        self._append_log("[OK] EULA accettata (eula=true).")

        # Step 5: Configure server.properties
        with self.lock:
            self.progress_pct = 80
            self.step_text = "Configurazione server.properties e porta UPnP..."

        props_path = os.path.join(target_dir, "server.properties")
        with open(props_path, "w", encoding="utf-8") as f:
            f.write(f"# Minecraft Fabric Server Properties - {mc_version}\n")
            f.write("# Generato automaticamente da Voxarium\n")
            f.write(f"server-port={port}\n")
            f.write(f"motd=Server Minecraft Fabric {mc_version} - Mod Leggere & Alte Prestazioni\n")
            f.write("max-players=20\n")
            f.write("online-mode=true\n")
            f.write("difficulty=easy\n")
            f.write("gamemode=survival\n")
            f.write("enable-command-block=true\n")
            f.write("view-distance=10\n")
            f.write("simulation-distance=8\n")
            f.write("sync-chunk-writes=true\n")
            f.write("enable-rcon=false\n")
        self._append_log(f"[OK] server.properties configurato (porta UPnP: {port}).")

        # Step 6: Create run.bat (run.sh su Linux)
        with self.lock:
            self.progress_pct = 90
            self.step_text = f"Creazione script di avvio {LAUNCH_SCRIPT}..."

        launch_cmd = f"java -Xms{ram_gb}G -Xmx{ram_gb}G -jar fabric-server-launch.jar nogui"
        run_bat_path = write_launch_script(target_dir, f"Fabric {mc_version}", launch_cmd)
        self._append_log(f"[OK] {LAUNCH_SCRIPT} creato con allocazione {ram_gb} GB RAM.")

        # Step 7: Finalize config and register instance
        self._finalize_config(target_dir, run_bat_path, launch_cmd, port, server_manager)

        with self.lock:
            self.status = "completed"
            self.progress_pct = 100
            self.step_text = f"Installazione completata con successo! Server Fabric {mc_version} pronto all'uso."

        self._append_log(f"=== INSTALLAZIONE FABRIC {mc_version} COMPLETATA! ===")

    # =========================================================
    # 4. FORGE SERVER INSTALLER
    # =========================================================
    def _install_forge(self, target_dir: str, mc_version: str, ram_gb: int, port: int, server_manager):
        # Step 0: Resolve Forge build
        forge_build = None
        versions = self.get_forge_versions()
        for v in versions:
            if v["mc"] == mc_version:
                forge_build = v["forge"]
                break
        
        if not forge_build:
            if mc_version == "1.20.1": forge_build = "47.4.10"
            elif mc_version == "1.21.1": forge_build = "52.1.0"
            elif mc_version == "1.12.2": forge_build = "14.23.5.2860"
            else: forge_build = "47.4.10"

        with self.lock:
            self.selected_forge_version = forge_build

        self._append_log(f"=== INSTALLAZIONE SERVER FORGE {mc_version} (Build {forge_build}) ===")
        self._append_log(f"Cartella destinazione: {target_dir}")
        self._append_log(f"RAM assegnata: {ram_gb} GB | Porta UPnP: {port}")

        # Step 1: Create target directory
        with self.lock:
            self.step_text = f"Creazione cartella per Minecraft {mc_version}..."
            self.progress_pct = 10
        os.makedirs(target_dir, exist_ok=True)
        self._append_log("[OK] Cartella creata/verificata.")

        # Step 2: Download Forge installer JAR
        with self.lock:
            self.step_text = f"Download Installer Forge {mc_version}-{forge_build}..."
            self.progress_pct = 15

        installer_filename = f"forge-{mc_version}-{forge_build}-installer.jar"
        installer_url = f"https://maven.minecraftforge.net/net/minecraftforge/forge/{mc_version}-{forge_build}/{installer_filename}"
        installer_jar = os.path.join(target_dir, installer_filename)
        self._append_log(f"Download da: {installer_url}")
        
        self._download(installer_url, installer_jar, 15, 30, hashes=self._maven_hashes(installer_url), timeout=30)

        self._append_log(f"[OK] Download completato ({os.path.getsize(installer_jar)} bytes).")

        # Step 3: Run headless installation
        with self.lock:
            self.status = "installing"
            self.step_text = f"Installazione Forge {mc_version} e download librerie Mojang..."
            self.progress_pct = 35

        self._append_log("Esecuzione comando Forge: java -jar installer.jar --installServer")
        self._append_log("Nota: Questa fase scarica i file ufficiali di Minecraft da Mojang e le librerie Forge.")

        cmd = ["java", "-jar", os.path.basename(installer_jar), "--installServer"]
        proc = subprocess.Popen(
            cmd,
            cwd=target_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=NO_WINDOW
        )

        for line in proc.stdout:
            self._append_log(line)
            with self.lock:
                if self.progress_pct < 80:
                    self.progress_pct += 1

        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"L'installer Forge si è interrotto con codice {proc.returncode}.")

        self._append_log(f"[OK] Installazione server Forge {mc_version} completata con successo!")

        # Step 4: Accept EULA
        with self.lock:
            self.status = "configuring"
            self.step_text = "Configurazione automatica EULA, RAM e Porte..."
            self.progress_pct = 85

        eula_path = os.path.join(target_dir, "eula.txt")
        with open(eula_path, "w", encoding="utf-8") as f:
            f.write("# By changing the setting below to TRUE you are indicating your agreement to the EULA (https://aka.ms/MinecraftEULA).\n")
            f.write(f"# Auto-accepted by Voxarium on {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("eula=true\n")
        self._append_log("[OK] EULA accettata (eula=true).")

        # Step 5: Generate optimized server.properties
        props_path = os.path.join(target_dir, "server.properties")
        with open(props_path, "w", encoding="utf-8") as f:
            f.write(f"# Minecraft server properties - Forge {mc_version}\n")
            f.write("# Configurato automaticamente da Voxarium\n")
            f.write(f"server-port={port}\n")
            f.write(f"motd=Server Minecraft Forge {mc_version} - Pronto a Giocare\n")
            f.write("max-players=20\n")
            f.write("online-mode=true\n")
            f.write("difficulty=easy\n")
            f.write("gamemode=survival\n")
            f.write("enable-command-block=true\n")
            f.write("allow-flight=true\n")
            f.write("view-distance=10\n")
            f.write("simulation-distance=8\n")
            f.write("sync-chunk-writes=true\n")
            f.write("enable-rcon=false\n")
        self._append_log(f"[OK] server.properties creato (porta: {port}).")

        # Step 6: Configure user_jvm_args.txt
        jvm_args_path = os.path.join(target_dir, "user_jvm_args.txt")
        with open(jvm_args_path, "w", encoding="utf-8") as f:
            f.write("# Configurazione JVM & RAM generata automaticamente da Voxarium\n")
            f.write(f"-Xms{ram_gb}G\n")
            f.write(f"-Xmx{ram_gb}G\n")
            f.write("-XX:+UseG1GC\n")
            f.write("-XX:+ParallelRefProcEnabled\n")
            f.write("-XX:MaxGCPauseMillis=200\n")
            f.write("-XX:+UnlockExperimentalVMOptions\n")
            f.write("-XX:+DisableExplicitGC\n")
            f.write("-XX:+AlwaysPreTouch\n")
            f.write("-XX:G1NewSizePercent=30\n")
            f.write("-XX:G1MaxNewSizePercent=40\n")
            f.write("-XX:G1ReservePercent=20\n")
            f.write("-XX:G1HeapWastePercent=5\n")
            f.write("-XX:G1MixedGCCountTarget=4\n")
            f.write("-XX:InitiatingHeapOccupancyPercent=15\n")
            f.write("-XX:G1MixedGCLiveThresholdPercent=90\n")
            f.write("-XX:G1RSetUpdatingPauseTimePercent=5\n")
            f.write("-XX:SurvivorRatio=32\n")
            f.write("-XX:+PerfDisableSharedMem\n")
            f.write("-XX:MaxTenuringThreshold=1\n")
        self._append_log(f"[OK] user_jvm_args.txt generato ({ram_gb} GB RAM con flag Aikar).")

        # Step 7: Create/verify run.bat and win_args.txt
        win_args_rel = f"libraries/net/minecraftforge/forge/{mc_version}-{forge_build}/{FORGE_ARGS_FILE}"
        win_args_full = os.path.join(target_dir, win_args_rel)

        legacy_jar = None
        if not os.path.exists(win_args_full):
            for f in os.listdir(target_dir):
                if f.startswith("forge-") and f.endswith(".jar") and "-installer" not in f:
                    legacy_jar = f
                    break

        if legacy_jar:
            launch_cmd = f"java -Xms{ram_gb}G -Xmx{ram_gb}G -jar \"{legacy_jar}\" nogui"
            self._append_log(f"[INFO] Struttura Forge Legacy rilevata ({legacy_jar}).")
        else:
            launch_cmd = f"java @user_jvm_args.txt @{win_args_rel} nogui"
            self._append_log(f"[INFO] Struttura Forge Modern rilevata ({FORGE_ARGS_FILE}).")

        run_bat_path = write_launch_script(target_dir, f"Forge {mc_version}", launch_cmd)
        self._append_log(f"[OK] {LAUNCH_SCRIPT} creato.")

        # Create mods folder
        os.makedirs(os.path.join(target_dir, "mods"), exist_ok=True)
        self._append_log("[OK] Cartella mods pronta per installare qualsiasi mod.")

        # Step 8: Update config.json & sync
        self._finalize_config(target_dir, run_bat_path, launch_cmd, port, server_manager)

        with self.lock:
            self.status = "completed"
            self.progress_pct = 100
            self.step_text = f"Installazione completata con successo! Server Forge {mc_version} pronto all'uso."

        self._append_log(f"=== INSTALLAZIONE FORGE {mc_version} COMPLETATA! ===")

    # =========================================================
    # NEOFORGE SERVER INSTALLER
    # =========================================================
    def _install_neoforge(self, target_dir: str, mc_version: str, ram_gb: int, port: int, server_manager):
        # Step 0: Resolve NeoForge build
        neoforge_build = None
        versions = self.get_neoforge_versions()
        for v in versions:
            if v["mc"] == mc_version or v.get("neoforge_version") == mc_version:
                neoforge_build = v.get("neoforge_version")
                mc_version = v["mc"]
                break
        
        if not neoforge_build:
            if mc_version == "1.20.1": neoforge_build = "47.1.106"
            elif mc_version == "1.20.4": neoforge_build = "20.4.253"
            elif mc_version == "1.21.1": neoforge_build = "21.1.264"
            elif mc_version == "1.21.4": neoforge_build = "21.4.161"
            elif mc_version == "26.3": neoforge_build = "26.3.0.95"
            else: neoforge_build = mc_version

        with self.lock:
            self.selected_neoforge_version = neoforge_build
            self.selected_mc_version = mc_version

        self._append_log(f"=== INSTALLAZIONE SERVER NEOFORGE {mc_version} (Build {neoforge_build}) ===")
        self._append_log(f"Cartella destinazione: {target_dir}")
        self._append_log(f"RAM assegnata: {ram_gb} GB | Porta UPnP: {port}")

        # Step 1: Create target directory
        with self.lock:
            self.step_text = f"Creazione cartella per NeoForge {mc_version}..."
            self.progress_pct = 10
        os.makedirs(target_dir, exist_ok=True)
        self._append_log("[OK] Cartella creata/verificata.")

        # Step 2: Download NeoForge installer JAR
        with self.lock:
            self.step_text = f"Download Installer NeoForge {neoforge_build}..."
            self.progress_pct = 15

        if mc_version == "1.20.1" or str(neoforge_build).startswith("47."):
            installer_filename = f"forge-1.20.1-{neoforge_build}-installer.jar"
            installer_url = f"https://maven.neoforged.net/releases/net/neoforged/forge/1.20.1-{neoforge_build}/{installer_filename}"
        else:
            installer_filename = f"neoforge-{neoforge_build}-installer.jar"
            installer_url = f"https://maven.neoforged.net/releases/net/neoforged/neoforge/{neoforge_build}/{installer_filename}"

        installer_jar = os.path.join(target_dir, installer_filename)
        self._append_log(f"Download da: {installer_url}")

        self._download(installer_url, installer_jar, 15, 30, hashes=self._maven_hashes(installer_url), timeout=30)

        self._append_log(f"[OK] Download completato ({os.path.getsize(installer_jar)} bytes).")

        # Step 3: Run headless installation
        with self.lock:
            self.status = "installing"
            self.step_text = f"Installazione NeoForge {mc_version} e download librerie Mojang/NeoForge..."
            self.progress_pct = 35

        self._append_log("Esecuzione comando NeoForge: java -jar installer.jar --installServer")
        self._append_log("Nota: Questa fase scarica i file ufficiali Minecraft e le librerie modulari NeoForge.")

        cmd = ["java", "-jar", os.path.basename(installer_jar), "--installServer"]
        proc = subprocess.Popen(
            cmd,
            cwd=target_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=NO_WINDOW
        )

        for line in proc.stdout:
            self._append_log(line)
            with self.lock:
                if self.progress_pct < 80:
                    self.progress_pct += 1

        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"L'installer NeoForge si è interrotto con codice {proc.returncode}.")

        self._append_log(f"[OK] Installazione server NeoForge {mc_version} completata con successo!")

        # Step 4: Accept EULA
        with self.lock:
            self.status = "configuring"
            self.step_text = "Configurazione automatica EULA, RAM e Porte..."
            self.progress_pct = 85

        eula_path = os.path.join(target_dir, "eula.txt")
        with open(eula_path, "w", encoding="utf-8") as f:
            f.write("# By changing the setting below to TRUE you are indicating your agreement to the EULA (https://aka.ms/MinecraftEULA).\n")
            f.write(f"# Auto-accepted by Voxarium on {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("eula=true\n")
        self._append_log("[OK] EULA accettata (eula=true).")

        # Step 5: Generate optimized server.properties
        props_path = os.path.join(target_dir, "server.properties")
        with open(props_path, "w", encoding="utf-8") as f:
            f.write(f"# Minecraft server properties - NeoForge {mc_version}\n")
            f.write("# Configurato automaticamente da Voxarium\n")
            f.write(f"server-port={port}\n")
            f.write(f"motd=Server Minecraft NeoForge {mc_version} - Pronto a Giocare\n")
            f.write("max-players=20\n")
            f.write("online-mode=true\n")
            f.write("difficulty=easy\n")
            f.write("gamemode=survival\n")
            f.write("enable-command-block=true\n")
            f.write("allow-flight=true\n")
            f.write("view-distance=10\n")
            f.write("simulation-distance=8\n")
            f.write("sync-chunk-writes=true\n")
            f.write("enable-rcon=false\n")
        self._append_log(f"[OK] server.properties creato (porta: {port}).")

        # Step 6: Configure user_jvm_args.txt
        jvm_args_path = os.path.join(target_dir, "user_jvm_args.txt")
        with open(jvm_args_path, "w", encoding="utf-8") as f:
            f.write("# Configurazione JVM & RAM generata automaticamente da Voxarium per NeoForge\n")
            f.write(f"-Xms{ram_gb}G\n")
            f.write(f"-Xmx{ram_gb}G\n")
            f.write("-XX:+UseG1GC\n")
            f.write("-XX:+ParallelRefProcEnabled\n")
            f.write("-XX:MaxGCPauseMillis=200\n")
            f.write("-XX:+UnlockExperimentalVMOptions\n")
            f.write("-XX:+DisableExplicitGC\n")
            f.write("-XX:+AlwaysPreTouch\n")
            f.write("-XX:G1NewSizePercent=30\n")
            f.write("-XX:G1MaxNewSizePercent=40\n")
            f.write("-XX:G1ReservePercent=20\n")
            f.write("-XX:G1HeapWastePercent=5\n")
            f.write("-XX:G1MixedGCCountTarget=4\n")
            f.write("-XX:InitiatingHeapOccupancyPercent=15\n")
            f.write("-XX:G1MixedGCLiveThresholdPercent=90\n")
            f.write("-XX:G1RSetUpdatingPauseTimePercent=5\n")
            f.write("-XX:SurvivorRatio=32\n")
            f.write("-XX:+PerfDisableSharedMem\n")
            f.write("-XX:MaxTenuringThreshold=1\n")
        self._append_log(f"[OK] user_jvm_args.txt generato ({ram_gb} GB RAM con flag Aikar).")

        # Step 7: Locate win_args.txt or run.bat
        win_args_path = None
        for root, dirs, files in os.walk(os.path.join(target_dir, "libraries")):
            if FORGE_ARGS_FILE in files:
                win_args_path = os.path.relpath(os.path.join(root, FORGE_ARGS_FILE), target_dir)
                break

        if win_args_path:
            launch_cmd = f"java @user_jvm_args.txt @{win_args_path} nogui"
            self._append_log(f"[INFO] Trovato file argomenti NeoForge: {win_args_path}")
        else:
            launch_cmd = "java @user_jvm_args.txt -jar server.jar nogui"

        run_bat_path = write_launch_script(target_dir, f"NeoForge {mc_version}", launch_cmd)
        self._append_log(f"[OK] {LAUNCH_SCRIPT} creato.")

        # Create mods folder
        os.makedirs(os.path.join(target_dir, "mods"), exist_ok=True)
        self._append_log("[OK] Cartella mods pronta per installare qualsiasi mod NeoForge.")

        # Step 8: Update config.json & sync
        self._finalize_config(target_dir, run_bat_path, launch_cmd, port, server_manager)

        with self.lock:
            self.status = "completed"
            self.progress_pct = 100
            self.step_text = f"Installazione completata con successo! Server NeoForge {mc_version} pronto all'uso."

        self._append_log(f"=== INSTALLAZIONE NEOFORGE {mc_version} COMPLETATA! ===")

    def _finalize_config(self, target_dir: str, run_bat_path: str, launch_cmd: str, port: int, server_manager):
        with self.lock:
            self.progress_pct = 95
            self.step_text = "Salvataggio configurazione del pannello..."

        # Con il pannello acceso si parte dalla sua configurazione in memoria e si salva da lì: un solo punto che
        # scrive config.json (due copie diverse si cancellavano a vicenda le modifiche)
        if server_manager is not None:
            cfg = server_manager.config
        else:
            cfg = {}
            if os.path.exists(self.config_path):
                try:
                    cfg = read_json_with_backup(self.config_path)
                except Exception as e:
                    # Senza questo si riscriverebbe config.json da zero, perdendo PIN e chiavi
                    self._append_log(f"[AVVISO] config.json non leggibile ({e}): non viene modificato.")
                    cfg = None

        if cfg is not None:
            cfg["server_dir"] = target_dir
            cfg["server_run_bat"] = run_bat_path
            cfg["server_command"] = launch_cmd
            cfg["server_port"] = port
            if server_manager is not None:
                server_manager.save_config()
            else:
                write_json_atomic(self.config_path, cfg, keep_backup=True)
            self._append_log("[OK] config.json aggiornato con successo.")

        if server_manager:
            try:
                server_manager.server_dir = target_dir
                server_manager.run_bat = run_bat_path
                self._append_log("[OK] Gestore server sincronizzato con il nuovo percorso.")
            except Exception as ex:
                self._append_log(f"Notice sync: {ex}")

        # Auto-register new instance in Multi-Instance Manager
        try:
            import instance_manager
            inst_name = f"Server {self.server_type.capitalize()} {self.selected_mc_version or '1.20.1'}"
            instance_manager.add_instance(
                name=inst_name,
                server_dir=target_dir,
                server_run_bat=run_bat_path,
                server_command=launch_cmd,
                server_port=port,
                server_type=self.server_type,
                mc_version=self.selected_mc_version or "1.20.1",
                set_active=True,
                manager=server_manager
            )
            self._append_log(f"[OK] Istanza '{inst_name}' registrata con successo nel Gestore Multi-Server.")
        except Exception as ex_inst:
            self._append_log(f"Notice instance registration: {ex_inst}")

        if self.on_server_ready:
            # Prima backup e ripristino restavano sul server precedente fino al riavvio del programma
            try:
                self.on_server_ready(target_dir)
            except Exception as ex_ready:
                self._append_log(f"Notice sync: {ex_ready}")

