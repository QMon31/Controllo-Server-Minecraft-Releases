# Building Voxarium from source

The repository has two parts:

| Folder | What it is |
|---|---|
| [`pc/`](pc) | The Windows program: Python backend (FastAPI), web control panel (`static/`), C# launcher window and installer |
| [`android/`](android) | The Android app (Java, built without Gradle) |

Official, ready-to-use installers are in the [Releases](https://github.com/QMon31/Voxarium/releases).

## Windows program (`pc/`)

Requirements: Windows 10/11 64-bit, Python 3 (64-bit), the .NET Framework C# compiler that ships with Windows
(`C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe`), Java for the Minecraft server you want to run.

1. Install the Python libraries:
   ```
   pip install -r requirements.txt
   ```
2. **Run the panel directly** (quickest way to try changes):
   ```
   python app.py
   ```
   then open <http://localhost:8080> in a browser.
3. **Build the launcher and the installer**:
   - Download the `Microsoft.Web.WebView2` NuGet package (version 1.0.4258 or later; a `.nupkg` is a ZIP file) and copy
     `lib/net462/Microsoft.Web.WebView2.Core.dll`, `lib/net462/Microsoft.Web.WebView2.WinForms.dll` and
     `runtimes/win-x64/native/WebView2Loader.dll` into `pc/`. These Microsoft libraries are not part of this repository
     (see [NOTICE.md](NOTICE.md)).
   - Prepare the bundled Python runtime: `python build_runtime.py`
   - Build: `python build_deploy.py --no-git`
     (tests, `Controllo Server Minecraft.exe`, `package.zip`, `Setup_Voxarium.exe`). Without `--no-git` the script also
     commits to the author's repository; `--publish` is only for official releases.

Tests: `python -m unittest discover -s tests` (from `pc/`).

## Android app (`android/`)

Requirements: JDK 21 (the path is set in `JDK_BIN` at the top of `build_apk.py`), Android SDK with
`build-tools/35.0.0` and `platforms/android-35` (in `%LOCALAPPDATA%\Android\Sdk`).

```
python build_apk.py
```

The first build creates **your own** signing key (`debug.keystore` + `signing.properties`, never committed).
An app signed with a different key cannot be installed over the official one: uninstall it first.
The version is set only in `AndroidManifest.xml`.

## Translations

The panel texts are in `pc/static/i18n_*.js` (`i18n_it.js` is the reference), the app texts in
`android/res/raw/i18n_*.json` (`i18n_it.json` is the reference). Every key must exist in all six languages:
the tests and `check_i18n.py` stop the build otherwise.
