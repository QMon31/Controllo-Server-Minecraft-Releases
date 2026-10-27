"""
Icona di Voxarium ("cubo aperto"): un cubo senza la faccia di sopra, i cui due lati formano una V.
Disegnata da zero da questo script (nessuna immagine o risorsa di Minecraft): rigenera tutti i file dell'icona.

Uso: python voxarium_icon.py [cartella_android]
  - minecraft.ico, favicon.ico (radice e static/): 16-256 px; 16, 20 e 24 px ritoccati a pixel
  - voxarium.ico: la stessa icona per i collegamenti di Desktop e Menu Start (un nome nuovo, così Windows non
    continua a mostrare la vecchia icona che tiene in memoria per minecraft.ico)
  - minecraft_block.png e static/minecraft_block.png: 256 px (pannello, favicon PNG, pacchetto Linux)
  - <cartella_android>/res/drawable/ic_launcher.png: 192 px (icona dell'app), se la cartella è indicata
I nomi dei file restano quelli di prima perché li usano installer, Launcher, pannello e aggiornamenti.
"""

import os
import sys

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.abspath(__file__))

TILE = (15, 15, 16, 255)        # fondo dell'icona
LIGHT = (156, 204, 101, 255)    # lato sinistro, #9ccc65 (verde del pannello)
SHADE = (85, 139, 47, 255)      # lato destro, #558b2f

# Geometria in unità 0-100: riquadro con angoli arrotondati e le due facce del cubo
RADIUS = 22
LEFT_FACE = [(17.6, 22), (48.8, 40), (48.8, 76), (17.6, 58)]
RIGHT_FACE = [(82.4, 22), (51.2, 40), (51.2, 76), (82.4, 58)]


def draw_vector(size):
    """Icona disegnata 8 volte più grande e ridotta: bordi lisci, per 32 px e oltre."""
    big = size * 8
    k = big / 100.0
    im = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, big - 1, big - 1), radius=int(RADIUS * k), fill=TILE)
    d.polygon([(x * k, y * k) for x, y in LEFT_FACE], fill=LIGHT)
    d.polygon([(x * k, y * k) for x, y in RIGHT_FACE], fill=SHADE)
    return im.reduce(8)  # media di ogni blocco 8x8: bordi lisci senza aloni


def draw_pixels(size):
    """Versione a pixel per le misure piccole: ogni colonna delle facce riempita a pixel interi, così a 16 px
    il cubo resta leggibile invece di diventare una macchia sfumata."""
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    corner = max(2, round(size * RADIUS / 100 * 0.8))
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=corner, fill=TILE)
    s = size / 100.0
    x0, x1 = LEFT_FACE[0][0] * s, LEFT_FACE[1][0] * s          # bordo esterno e spigolo centrale
    top0, top1 = LEFT_FACE[0][1] * s, LEFT_FACE[1][1] * s
    bot0, bot1 = LEFT_FACE[3][1] * s, LEFT_FACE[2][1] * s
    half = size // 2
    for c in range(size):
        xc = c + 0.5
        mirrored = c >= half
        xm = size - xc if mirrored else xc                      # il lato destro è lo specchio del sinistro
        if not (x0 <= xm <= x1 + 0.6):
            continue
        t = (xm - x0) / (x1 - x0)
        top, bot = top0 + (top1 - top0) * t, bot0 + (bot1 - bot0) * t
        for r in range(size):
            if top <= r + 0.5 <= bot:
                im.putpixel((c, r), SHADE if mirrored else LIGHT)
    return im


def icon(size):
    return draw_pixels(size) if size <= 24 else draw_vector(size)


def save_ico(path, sizes):
    images = [icon(s) for s in sizes]
    images[-1].save(path, format="ICO", sizes=[(s, s) for s in sizes], append_images=images[:-1])


def main():
    ico_sizes = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
    for rel in ("minecraft.ico", "voxarium.ico", "favicon.ico", os.path.join("static", "minecraft.ico"), os.path.join("static", "favicon.ico")):
        save_ico(os.path.join(ROOT, rel), ico_sizes)
    for rel in ("minecraft_block.png", os.path.join("static", "minecraft_block.png")):
        icon(256).save(os.path.join(ROOT, rel))
    if len(sys.argv) > 1:
        icon(192).save(os.path.join(sys.argv[1], "res", "drawable", "ic_launcher.png"))
    print("Icona Voxarium rigenerata.")


if __name__ == "__main__":
    main()
