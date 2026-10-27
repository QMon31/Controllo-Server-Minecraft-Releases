"""Compila l'APK di Controllo Server Minecraft dai sorgenti di questa cartella (android_project).

Uso (dalla cartella android_project o da qualunque altra):
    python build_apk.py                         compila soltanto (STAGING): nessun telefono riceve l'aggiornamento
    python build_apk.py --publish --notes "..." compila e pubblica in OTA (static/ + version.json) per i telefoni
    python build_apk.py --no-r8                 compila senza offuscamento (solo per capire un problema causato da R8)

La versione si cambia SOLO in AndroidManifest.xml (android:versionCode / android:versionName): questo script
la legge da lì, allinea CURRENT_VERSION_CODE / CURRENT_VERSION_NAME in MainActivity.java e la scrive in version.json.
Regole di pubblicazione: vedi AGENTS.md (sezione 1, Policy OTA). Mai copiare l'APK sul Desktop.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))           # android_project (repository Android)
BASE_DIR = os.path.dirname(PROJECT_DIR)                             # Progetti Gemini\Controllo Server Minecraft
SRC_DIR = os.path.join(PROJECT_DIR, "src", "com", "minecraftserver", "manager")
RES_DIR = os.path.join(PROJECT_DIR, "res")
BIN_DIR = os.path.join(PROJECT_DIR, "bin")
OBJ_DIR = os.path.join(PROJECT_DIR, "obj")
MANIFEST = os.path.join(PROJECT_DIR, "AndroidManifest.xml")
MAIN_ACTIVITY = os.path.join(SRC_DIR, "MainActivity.java")
PROGUARD_RULES = os.path.join(PROJECT_DIR, "proguard-rules.pro")

LOCAL_APPDATA = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
JDK_BIN = r"C:\Program Files\Eclipse Adoptium\jdk-21.0.11.10-hotspot\bin"
SDK_DIR = os.path.join(LOCAL_APPDATA, "Android", "Sdk")
BUILD_TOOLS = os.path.join(SDK_DIR, "build-tools", "35.0.0")
PLATFORM_JAR = os.path.join(SDK_DIR, "platforms", "android-35", "android.jar")

AAPT2 = os.path.join(BUILD_TOOLS, "aapt2.exe")
D8 = os.path.join(BUILD_TOOLS, "d8.bat")
D8_JAR = os.path.join(BUILD_TOOLS, "lib", "d8.jar")  # contiene anche R8
ZIPALIGN = os.path.join(BUILD_TOOLS, "zipalign.exe")
APKSIGNER = os.path.join(BUILD_TOOLS, "apksigner.bat")
JAVA = os.path.join(JDK_BIN, "java.exe")
JAVAC = os.path.join(JDK_BIN, "javac.exe")
KEYTOOL = os.path.join(JDK_BIN, "keytool.exe")
JAR = os.path.join(JDK_BIN, "jar.exe")

OUTPUT_APK_NAME = "Controllo_Server_Minecraft.apk"

# Firma dell'APK: keystore e password solo su questo PC (signing.properties e *.keystore sono esclusi da git).
# Chi ha la chiave può pubblicare aggiornamenti dell'app: non va mai condivisa.
SIGNING_PROPERTIES = os.path.join(PROJECT_DIR, "signing.properties")
# Cartelle in più di questo PC (es. una copia su Google Drive), una per riga: file locale, escluso da git
LOCAL_EXTRA_DIRS = os.path.join(PROJECT_DIR, "local_extra_dirs.txt")


def _extra_dirs(sub):
    try:
        with open(LOCAL_EXTRA_DIRS, encoding="utf-8") as f:
            return [os.path.join(line.strip(), sub) for line in f if line.strip() and not line.startswith("#")]
    except OSError:
        return []


# Cartelle static/ servite dal pannello PC, da cui i telefoni scaricano l'aggiornamento (mai il Desktop)
STATIC_DIRS = [
    os.path.join(BASE_DIR, "static"),
    os.path.join(LOCAL_APPDATA, "Programs", "Controllo Server Minecraft", "static"),
    os.path.join(os.path.expanduser("~"), "Desktop", "Controllo Server Minecraft", "static"),
] + _extra_dirs("static")


def log(msg):
    print(f"[APK BUILDER] {msg}")


def signing_config():
    """(keystore, alias, password del keystore, password della chiave) da signing.properties.
    Su un PC senza chiave ne crea una nuova con password casuali (gli aggiornamenti firmati con una chiave diversa
    non si installano sopra l'app già presente: per i telefoni esistenti serve sempre la chiave originale)."""
    props = {}
    if os.path.isfile(SIGNING_PROPERTIES):
        with open(SIGNING_PROPERTIES, encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1)
                    props[k.strip()] = v.strip()
    keystore = os.path.join(PROJECT_DIR, props.get("keystore", "debug.keystore"))
    alias = props.get("alias", "androiddebugkey")
    if not os.path.exists(keystore):
        import secrets
        store_pass = key_pass = secrets.token_hex(16)
        log("Nessuna chiave di firma: ne creo una nuova (signing.properties + keystore, mai su GitHub)...")
        run_cmd(
            f'"{KEYTOOL}" -genkeypair -v -keystore "{keystore}" -alias {alias} '
            f'-keyalg RSA -keysize 2048 -validity 10000 -storepass {store_pass} -keypass {key_pass} '
            f'-dname "CN=Voxarium,O=Voxarium,C=IT"',
            "keytool genkeypair"
        )
        with open(SIGNING_PROPERTIES, "w", encoding="utf-8") as f:
            f.write(f"keystore={os.path.basename(keystore)}\nalias={alias}\n"
                    f"store_password={store_pass}\nkey_password={key_pass}\n")
        return keystore, alias, store_pass, key_pass
    if "store_password" not in props:
        sys.exit(f"[APK BUILDER] Manca {SIGNING_PROPERTIES} con store_password e key_password del keystore esistente.")
    return keystore, alias, props["store_password"], props.get("key_password", props["store_password"])


def run_cmd(cmd, desc):
    log(f"Esecuzione: {desc}")
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"ERRORE in {desc}:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")
        sys.exit(1)
    return res.stdout


def read_manifest_version():
    """versionCode e versionName dall'AndroidManifest.xml: unica fonte della versione dell'app."""
    with open(MANIFEST, encoding="utf-8") as f:
        xml = f.read()
    code = re.search(r'android:versionCode="(\d+)"', xml)
    name = re.search(r'android:versionName="([^"]+)"', xml)
    if not code or not name:
        log("ERRORE: versionCode/versionName non trovati in AndroidManifest.xml")
        sys.exit(1)
    return int(code.group(1)), name.group(1)


def sync_main_activity_version(code, name):
    """Allinea le costanti di MainActivity (usate dal controllo aggiornamenti in-app) al manifest."""
    with open(MAIN_ACTIVITY, encoding="utf-8", newline="") as f:
        src = f.read()
    new = re.sub(r"(CURRENT_VERSION_CODE\s*=\s*)\d+;", rf"\g<1>{code};", src, count=1)
    new = re.sub(r'(CURRENT_VERSION_NAME\s*=\s*)"[^"]*";', rf'\g<1>"{name}";', new, count=1)
    if "CURRENT_VERSION_CODE" not in src or "CURRENT_VERSION_NAME" not in src:
        log("ERRORE: costanti CURRENT_VERSION_* non trovate in MainActivity.java")
        sys.exit(1)
    if new != src:
        with open(MAIN_ACTIVITY, "w", encoding="utf-8", newline="") as f:
            f.write(new)
        log(f"MainActivity.java allineato al manifest: v{name} (Build {code})")


def compile_apk():
    os.makedirs(BIN_DIR, exist_ok=True)
    compiled_res = os.path.join(BIN_DIR, "compiled_resources.zip")
    unaligned_apk = os.path.join(BIN_DIR, "app-unaligned.apk")
    gen_src_dir = os.path.join(PROJECT_DIR, "gen")
    os.makedirs(gen_src_dir, exist_ok=True)
    aapt_rules = os.path.join(BIN_DIR, "aapt_rules.pro")

    # 1-2. Risorse (aapt2 scrive anche le regole R8 per le classi nominate nel manifest e nei layout)
    run_cmd(f'"{AAPT2}" compile --dir "{RES_DIR}" -o "{compiled_res}"', "aapt2 compile")
    run_cmd(
        f'"{AAPT2}" link -I "{PLATFORM_JAR}" "{compiled_res}" --manifest "{MANIFEST}" '
        f'-o "{unaligned_apk}" --java "{gen_src_dir}" --proguard "{aapt_rules}" --auto-add-overlay',
        "aapt2 link"
    )

    # 3. javac
    if os.path.exists(OBJ_DIR):
        shutil.rmtree(OBJ_DIR)
    os.makedirs(OBJ_DIR, exist_ok=True)
    java_files = [os.path.join(SRC_DIR, f) for f in sorted(os.listdir(SRC_DIR)) if f.endswith(".java")]
    java_files.append(os.path.join(gen_src_dir, "com", "minecraftserver", "manager", "R.java"))
    java_files_str = " ".join(f'"{f}"' for f in java_files)
    run_cmd(
        f'"{JAVAC}" -encoding UTF-8 -cp "{PLATFORM_JAR}" -d "{OBJ_DIR}" -source 1.8 -target 1.8 -g:lines,source {java_files_str}',
        "javac compilation"
    )

    # 4. jar + R8 (il jar evita il limite di lunghezza della riga di comando di Windows).
    # R8 rinomina classi, metodi e campi e toglie il codice non usato: chi decompila l'APK non legge il codice originale.
    classes_jar = os.path.join(BIN_DIR, "classes_bundle.jar")
    if os.path.exists(classes_jar):
        os.remove(classes_jar)
    run_cmd(f'"{JAR}" -cf "{classes_jar}" -C "{OBJ_DIR}" .', "jar classes bundling")
    dex_output_dir = os.path.join(BIN_DIR, "dex")
    if os.path.exists(dex_output_dir):
        shutil.rmtree(dex_output_dir)
    os.makedirs(dex_output_dir, exist_ok=True)
    if "--no-r8" in sys.argv:
        run_cmd(f'call "{D8}" --min-api 26 --lib "{PLATFORM_JAR}" --output "{dex_output_dir}" "{classes_jar}"', "d8 dex generation")
    else:
        # Mappa nomi offuscati -> originali, una per versione: serve a leggere gli errori dell'APK pubblicato (retrace)
        code, name = read_manifest_version()
        mapping_dir = os.path.join(BIN_DIR, "mapping")
        os.makedirs(mapping_dir, exist_ok=True)
        mapping_file = os.path.join(mapping_dir, f"mapping-v{name}-b{code}.txt")
        run_cmd(
            f'"{JAVA}" -cp "{D8_JAR}" com.android.tools.r8.R8 --release --min-api 26 --lib "{PLATFORM_JAR}" '
            f'--pg-conf "{PROGUARD_RULES}" --pg-conf "{aapt_rules}" --pg-map-output "{mapping_file}" '
            f'--output "{dex_output_dir}" "{classes_jar}"',
            "R8 (offuscamento e dex)"
        )
        log(f"Mappa dei nomi offuscati: {mapping_file}")

    # 5. classes.dex nell'APK
    cwd_before = os.getcwd()
    os.chdir(dex_output_dir)
    run_cmd(f'"{JAR}" -uvf "{unaligned_apk}" classes.dex', "Inclusione classes.dex in APK")
    os.chdir(cwd_before)

    # 6. zipalign
    aligned_apk = os.path.join(BIN_DIR, "app-aligned.apk")
    if os.path.exists(aligned_apk):
        os.remove(aligned_apk)
    run_cmd(f'"{ZIPALIGN}" -v 4 "{unaligned_apk}" "{aligned_apk}"', "zipalign 4-byte")

    # 7-9. Firma (keystore e password locali, esclusi da git) e verifica
    keystore_path, alias, store_pass, key_pass = signing_config()
    final_apk = os.path.join(PROJECT_DIR, OUTPUT_APK_NAME)
    if os.path.exists(final_apk):
        os.remove(final_apk)
    shutil.copy2(aligned_apk, final_apk)
    run_cmd(
        f'call "{APKSIGNER}" sign --ks "{keystore_path}" --ks-key-alias {alias} '
        f'--ks-pass pass:{store_pass} --key-pass pass:{key_pass} "{final_apk}"',
        "apksigner sign"
    )
    log("Verifica APK:\n" + run_cmd(f'call "{APKSIGNER}" verify --verbose "{final_apk}"', "apksigner verify"))
    return final_apk


def publish_ota(final_apk, code, name, notes):
    version_info = {
        "version_code": code,
        "version_name": name,
        # Percorso relativo: l'app lo unisce all'indirizzo del PC che sta usando (LAN o tunnel attuale)
        "download_url": f"/static/{OUTPUT_APK_NAME}",
        "release_notes": notes,
        "min_sdk": 26,
        "target_sdk": 35,
    }
    for s_dir in STATIC_DIRS:
        try:
            if os.path.isdir(os.path.dirname(s_dir)):
                os.makedirs(s_dir, exist_ok=True)
                shutil.copy2(final_apk, os.path.join(s_dir, OUTPUT_APK_NAME))
                with open(os.path.join(s_dir, "version.json"), "w", encoding="utf-8") as f:
                    json.dump(version_info, f, indent=2, ensure_ascii=False)
                log(f"  -> OTA Static aggiornato: {s_dir}")
        except Exception as e:
            log(f"  -> Avviso distribuzione OTA su {s_dir}: {e}")
    log("  -> APK pubblicato in OTA: i telefoni ricevono l'aggiornamento.")


def arg_value(flag):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("--"):
            return sys.argv[i + 1]
    return ""


if __name__ == "__main__":
    if "--generate" in sys.argv:
        # La vecchia opzione rigenerava i sorgenti da una copia vecchia (v4.8.5) sovrascrivendo il codice reale
        log("ERRORE: --generate non esiste più. I sorgenti sono quelli in android_project (repository GitHub).")
        sys.exit(1)
    # Traduzioni con chiavi identificative: stesse chiavi in tutte le lingue, chiavi usate esistenti (check_i18n.py)
    import check_i18n
    i18n_errors = check_i18n.check()
    if i18n_errors:
        for e in i18n_errors:
            log(f"ERRORE traduzioni: {e}")
        log(f"Compilazione fermata: {len(i18n_errors)} errori nelle traduzioni.")
        sys.exit(1)
    log("Traduzioni: OK")
    code, name = read_manifest_version()
    log(f"Versione dal manifest: v{name} (Build {code})")
    sync_main_activity_version(code, name)
    apk = compile_apk()
    log(f"SUCCESS! APK: {apk} ({os.path.getsize(apk):,} bytes)")
    if "--publish" in sys.argv:
        publish_ota(apk, code, name, arg_value("--notes") or f"v{name} (Build {code})")
    else:
        log("  -> APK compilato su disco (STAGING). Per i telefoni: --publish --notes \"...\"")
