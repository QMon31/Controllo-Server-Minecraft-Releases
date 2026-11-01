import os
import py_compile
import sys
import tempfile
import zipfile

zip_filename = "package.zip"
# Moduli Python del pannello: nel pacchetto vanno solo compilati (.pyc), senza il codice sorgente leggibile
py_files = [
    "app.py",
    "server_manager.py",
    "backup_manager.py",
    "mc_ping.py",
    "eco_wake.py",
    "remote_access.py",
    "upnp_manager.py",
    "server_installer.py",
    "platform_utils.py",
    "instance_manager.py",
    "file_manager.py",
    "modrinth_store.py",
    "mod_info.py",
    "pc_version.py",
    "pc_updater.py",
    "pin_rules.py",
]
root_files = [
    "Controllo Server Minecraft.exe",
    "Microsoft.Web.WebView2.Core.dll",
    "Microsoft.Web.WebView2.WinForms.dll",
    "WebView2Loader.dll",
    "minecraft.ico",
    "voxarium.ico",
    "minecraft_block.png",
    "Avvia_Pannello_Minecraft.bat",
    "Arresta_Pannello_Minecraft.bat",
]

# Il .pyc funziona solo con la stessa versione di Python che lo ha creato: deve essere quella del runtime incluso
# (build_runtime.py lo prepara con la versione di chi compila, cioè questa)
runtime_tag = f"python{sys.version_info.major}{sys.version_info.minor}"
if not os.path.isfile(os.path.join("runtime", "python", f"{runtime_tag}.zip")):
    raise SystemExit(f"ERRORE: il runtime incluso non è {runtime_tag}: rieseguire build_runtime.py con questo Python")


def add_compiled(zf, src, arcname):
    """Compila src e lo aggiunge come arcname (.pyc). optimize=2 toglie anche le docstring; i commenti non
    finiscono mai nel .pyc. Nei messaggi d'errore compare solo il nome del file, non il percorso di questo PC."""
    fd, tmp = tempfile.mkstemp(suffix=".pyc")
    os.close(fd)
    try:
        py_compile.compile(src, cfile=tmp, dfile=arcname[:-1], doraise=True, optimize=2)
        zf.write(tmp, arcname)
    finally:
        os.remove(tmp)
    print(f"Added compiled module: {arcname}")


with zipfile.ZipFile(zip_filename, "w", zipfile.ZIP_DEFLATED) as zf:
    for f in py_files:
        add_compiled(zf, f, f + "c")

    for f in root_files:
        if os.path.exists(f):
            zf.write(f, f)
            print(f"Added root file: {f}")
    
    # Rotte del pannello divise per argomento (importate da app.py)
    for f in sorted(os.listdir("routes")):
        if f.endswith(".py"):
            add_compiled(zf, os.path.join("routes", f), "routes/" + f + "c")

    # Licenze: GPL di Voxarium e testi delle librerie incluse (WebView2, Python), obbligatori per ridistribuirle
    for f in sorted(os.listdir("licenses")):
        zf.write(os.path.join("licenses", f), "licenses/" + f)
        print(f"Added license file: licenses/{f}")

    if os.path.isdir("static"):
        for root, dirs, files in os.walk("static"):
            for file in files:
                filepath = os.path.join(root, file)
                arcname = filepath.replace("\\", "/")
                zf.write(filepath, arcname)
                print(f"Added static file: {arcname}")

    runtime_dir = os.path.join("runtime", "python")
    if not os.path.isfile(os.path.join(runtime_dir, "python.exe")):
        raise SystemExit("ERRORE: runtime/python mancante: eseguire prima build_runtime.py")
    for root, dirs, files in os.walk(runtime_dir):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for file in files:
            filepath = os.path.join(root, file)
            arcname = os.path.relpath(filepath, "runtime").replace("\\", "/")  # -> python/...
            zf.write(filepath, arcname)
    print("Added bundled Python runtime: python/")

print(f"Successfully created {zip_filename}")

with zipfile.ZipFile(zip_filename) as zf_check:
    for forbidden in ("config.json", "instances.json", "launcher_config.json"):
        if forbidden in zf_check.namelist():
            raise SystemExit(f"ERRORE: {forbidden} non deve essere incluso nel pacchetto")
    for name in zf_check.namelist():
        if name.endswith(".py") and not name.startswith("python/"):
            raise SystemExit(f"ERRORE: {name} è codice sorgente e non deve essere incluso nel pacchetto")
