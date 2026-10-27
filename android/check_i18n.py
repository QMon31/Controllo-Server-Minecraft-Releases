"""Controllo delle traduzioni dell'app (lo esegue build_apk.py prima di compilare; si può lanciare anche da solo).

Regole (chiavi identificative, come nel pannello del PC):
- res/raw/i18n_it.json è il riferimento chiave -> italiano; en/es/fr/de/pt hanno esattamente le stesse chiavi;
- {0}, {1}... in ordine nella frase italiana e gli stessi segnaposto in ogni lingua;
- ogni chiave usata nel codice (I18n.t("chiave", ...)) o nei layout (android:tag="t:chiave h:chiave d:chiave") esiste;
- ogni testo dei layout ha la sua chiave nel tag, oppure "notr" nel tag se non va tradotto (numeri, comandi,
  nomi, valori che il codice riscrive); il widget (RemoteViews) prende i testi dal codice;
- le chiavi delle sezioni ui/app/notif/widget/net sono usate; la sezione pc contiene le frasi che arrivano in
  italiano dal programma sul PC e che l'app riconosce dal testo (I18n.fromPc).
"""
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(ROOT, "res", "raw")
SRC = os.path.join(ROOT, "src", "com", "minecraftserver", "manager")
LANGS = ["en", "es", "fr", "de", "pt"]
PH = re.compile(r"\{(\d+)\}")
KEY = re.compile(r"^(ui|app|notif|widget|net|pc)\.[a-z0-9_]+$")
KEY_LIT = re.compile(r'"((?:ui|app|notif|widget|net|pc)\.[a-z0-9_]+)"')
TAG_PART = re.compile(r"^([thd]):([^|\s]+)((?:\|[^|\s]*)*)$")


def check():
    errors = []
    it = json.load(open(os.path.join(RAW, "i18n_it.json"), encoding="utf-8"))
    for k, v in it.items():
        if not KEY.match(k):
            errors.append(f"i18n_it.json: chiave non valida {k!r}")
        nums = [int(n) for n in PH.findall(v)]
        if nums != list(range(len(nums))):
            errors.append(f"i18n_it.json: {k}: segnaposto non in ordine {nums}")
        if not v.strip():
            errors.append(f"i18n_it.json: {k}: testo vuoto")
    for lang in LANGS:
        d = json.load(open(os.path.join(RAW, f"i18n_{lang}.json"), encoding="utf-8"))
        for k in sorted(set(it) - set(d)):
            errors.append(f"i18n_{lang}.json: manca la chiave {k}")
        for k in sorted(set(d) - set(it)):
            errors.append(f"i18n_{lang}.json: chiave in più {k} (non c'è in i18n_it.json)")
        for k in set(it) & set(d):
            if sorted(set(PH.findall(it[k]))) != sorted(set(PH.findall(d[k]))):
                errors.append(f"i18n_{lang}.json: {k}: segnaposto diversi dall'italiano")

    italian = {re.sub(r"\s+", " ", v).strip(): k for k, v in it.items()}
    used = set()
    for path in glob.glob(os.path.join(SRC, "*.java")):
        name = os.path.basename(path)
        src = open(path, encoding="utf-8").read()
        for k in KEY_LIT.findall(src):
            used.add(k)
            if k not in it:
                errors.append(f"{name}: chiave inesistente {k}")
        for m in re.finditer(r"I18n\.t\(\s*([^\"\s][^,)]*)", src):
            if "?" not in m.group(1):  # le chiavi vanno scritte per intero (anche nelle scelte a ? :)
                line = src.count("\n", 0, m.start()) + 1
                errors.append(f"{name}:{line}: I18n.t() senza una chiave scritta per intero")
        if name == "I18n.java":
            continue
        # una frase del dizionario scritta in italiano nel codice al posto della sua chiave
        for m in re.finditer(r'"((?:[^"\\\n]|\\.)*)"', src):
            try:
                v = re.sub(r"\s+", " ", json.loads('"' + m.group(1) + '"')).strip()
            except ValueError:
                continue
            before = src[src.rfind("\n", 0, m.start()) + 1:m.start()].lstrip()
            if before.startswith(("//", "*")):
                continue  # commento
            if v in italian and re.search(r"[a-zà-ù]{3}", v):
                line = src.count("\n", 0, m.start()) + 1
                errors.append(f"{name}:{line}: testo {v[:50]!r} scritto in italiano: usare I18n.t(\"{italian[v]}\")")

    for path in glob.glob(os.path.join(ROOT, "res", "layout", "*.xml")):
        name = os.path.basename(path)
        xml = open(path, encoding="utf-8").read()
        widget = name.startswith("widget_")
        for el in re.findall(r"<[A-Za-z][\w.]*\b[^<>]*?/?>", xml, flags=re.S):
            tm = re.search(r'android:tag="([^"]*)"', el)
            tag = tm.group(1) if tm else ""
            codes = set()
            notr = "notr" in tag.split()
            if tag:
                for part in tag.split():
                    if part == "notr":
                        continue
                    pm = TAG_PART.match(part)
                    if not pm:
                        errors.append(f"{name}: tag non valido {part!r}")
                        continue
                    codes.add(pm.group(1))
                    used.add(pm.group(2))
                    if pm.group(2) not in it:
                        errors.append(f"{name}: chiave inesistente {pm.group(2)}")
            if widget or notr:
                continue
            for attr, code in (("text", "t"), ("hint", "h"), ("contentDescription", "d")):
                am = re.search(r'android:%s="([^"@]*)"' % attr, el)
                if am and re.search(r"[A-Za-zÀ-ÿ]{2}", am.group(1)) and code not in codes:
                    errors.append(f"{name}: {attr}={am.group(1)[:60]!r} senza chiave (android:tag=\"{code}:chiave\" o \"notr\")")

    for k in sorted(it):
        if not k.startswith("pc.") and k not in used:
            errors.append(f"chiave non usata {k}: toglierla o spostarla nella sezione pc. se arriva dal PC")
    return errors


if __name__ == "__main__":
    errs = check()
    for e in errs:
        print("ERRORE traduzioni:", e)
    print(f"Traduzioni: {'OK' if not errs else str(len(errs)) + ' errori'}")
    sys.exit(1 if errs else 0)
