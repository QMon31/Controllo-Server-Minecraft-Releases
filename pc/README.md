# Voxarium – PC program (Windows and Linux)

This folder is the **source code** of the Voxarium PC program. The same code runs on **Windows** and on **Linux**.
To use Voxarium you do not need it: download the ready-made version for your system.

| | **Windows 10 / 11** | **Linux** (Ubuntu / Debian) |
|---|---|---|
| **Download** | [⬇ Setup_Voxarium.exe](https://github.com/QMon31/Voxarium/releases/latest/download/Setup_Voxarium.exe) | [⬇ voxarium-linux.tar.gz](https://github.com/QMon31/Voxarium#download) (link in the Download section of the main page) |
| **Install** | open the file and follow the steps | `tar xzf voxarium-linux.tar.gz` then `sh voxarium/install.sh` |

## What is where

| | Windows | Linux |
|---|---|---|
| Control panel and server management (Python backend, web panel) | `app.py`, `server_manager.py`, …, `static/` | same files |
| Start and window | `Launcher.cs` (program window, WebView2) | service started by systemd: `linux/voxarium.service` |
| Installer | `Installer.cs` → `Setup_Voxarium.exe` | `linux/install.sh` |
| Building the package | `build_deploy.py`, `create_package.py` | `create_linux_package.py` |

How to build it yourself: [BUILDING.md](https://github.com/QMon31/Voxarium/blob/main/BUILDING.md).
Licence: GNU GPL v3 ([LICENSE](https://github.com/QMon31/Voxarium/blob/main/LICENSE)).

*Voxarium is not an official Minecraft product and is not associated with Mojang or Microsoft.*
