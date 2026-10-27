"""
Pacchetto per Linux (Ubuntu/Debian): voxarium-linux.tar.gz con i moduli Python, il pannello web, install.sh e il
servizio systemd. A differenza di package.zip contiene i .py: il .pyc funziona solo con la versione di Python che lo ha
creato, quindi install.sh li compila con il Python del PC Linux e poi cancella i sorgenti (stesso risultato di Windows).
Uso: python create_linux_package.py   → voxarium-linux.tar.gz (escluso da git)
"""

import ast
import io
import os
import tarfile

OUT = "voxarium-linux.tar.gz"
TOP = "voxarium"
FORBIDDEN = ("config.json", "instances.json", "launcher_config.json", "certs")


def _py_files():
    """Stessa lista di moduli di create_package.py, letta senza eseguirlo (quello costruisce subito package.zip)."""
    with open("create_package.py", encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "py_files" for t in node.targets):
            return list(ast.literal_eval(node.value))
    raise SystemExit("ERRORE: py_files non trovato in create_package.py")


def _add(tf, path, arcname, mode=0o644):
    with open(path, "rb") as f:
        data = f.read()
    if arcname.endswith((".sh", ".service", ".py", ".txt")):
        data = data.replace(b"\r\n", b"\n")  # script e moduli con fine riga Linux
    info = tarfile.TarInfo(f"{TOP}/{arcname}")
    info.size, info.mode = len(data), mode
    tf.addfile(info, io.BytesIO(data))


def build(out=OUT):
    with tarfile.open(out, "w:gz") as tf:
        for f in _py_files():
            _add(tf, f, f)
        for f in sorted(os.listdir("routes")):
            if f.endswith(".py"):
                _add(tf, os.path.join("routes", f), "routes/" + f)
        for root, _dirs, files in os.walk("static"):
            for file in files:
                path = os.path.join(root, file)
                _add(tf, path, path.replace("\\", "/"))
        _add(tf, "requirements.txt", "requirements.txt")
        _add(tf, os.path.join("linux", "install.sh"), "install.sh", 0o755)
        _add(tf, os.path.join("linux", "voxarium.service"), "voxarium.service")
        for f in ("minecraft_block.png", "favicon.ico"):
            if os.path.exists(f):
                _add(tf, f, f)

    with tarfile.open(out) as tf:
        names = tf.getnames()
    for name in names:
        rel = name[len(TOP) + 1:]
        if rel.split("/")[0] in FORBIDDEN:
            raise SystemExit(f"ERRORE: {rel} non deve essere incluso nel pacchetto")
    print(f"Creato {out} ({len(names)} file)")
    return out


if __name__ == "__main__":
    build()
