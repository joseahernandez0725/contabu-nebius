#!/usr/bin/env python3
"""Regenera los SVG del logo ContaBu (la "B" exacta de Nunito-Black, como vector
auto-contenido). Luego saca los PNG con rsvg-convert (ver comandos abajo).

Requiere:  sudo apt install -y fonts-nunito librsvg2-bin python3-fonttools

Uso:
    python3 frontend/scripts/make_logo_assets.py
    cd frontend/public
    rsvg-convert -w 16  -h 16  icon.svg           -o favicon-16.png
    rsvg-convert -w 32  -h 32  icon.svg           -o favicon-32.png
    rsvg-convert -w 48  -h 48  icon.svg           -o favicon-48.png
    rsvg-convert -w 180 -h 180 icon-fullbleed.svg -o apple-touch-icon.png
    rsvg-convert -w 192 -h 192 icon-fullbleed.svg -o pwa-192x192.png
    rsvg-convert -w 512 -h 512 icon-fullbleed.svg -o pwa-512x512.png
    cp icon.svg favicon.svg

Paleta (fijada, ver docs/MARCA Y NOMBRE.md §8):
    verde #1A8754 · ámbar #F4B400 · ámbar profundo #D99400 · crema #FBF7EE · verde claro #3DBA7B
"""
import os
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen

TTF = "/usr/share/fonts/truetype/nunito/Nunito-Black.ttf"
GREEN, CREAM, AMBER = "#1A8754", "#FBF7EE", "#F4B400"
PUB = os.path.join(os.path.dirname(__file__), "..", "public")

font = TTFont(TTF)
upm = font["head"].unitsPerEm
gname = font.getBestCmap()[ord("B")]
gs = font.getGlyphSet()
pen = SVGPathPen(gs)
gs[gname].draw(pen)
d = pen.getCommands()
adv = font["hmtx"][gname][0]

# Mismos parámetros visuales que el diseño aprobado: font-size 74, anchor middle x=50, baseline y=73
s = 74.0 / upm
start_x = 50.0 - (adv * s) / 2.0

inner = (f'<g fill="{CREAM}" transform="translate({start_x:.3f} 73) scale({s:.5f} {-s:.5f})">'
         f'<path d="{d}"/></g><circle cx="74" cy="29" r="9" fill="{AMBER}"/>')

icon = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100" '
        f'role="img" aria-label="ContaBu"><title>ContaBu</title>'
        f'<rect width="100" height="100" rx="24" fill="{GREEN}"/>{inner}</svg>\n')

fullbleed = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100" '
             f'role="img" aria-label="ContaBu"><title>ContaBu</title>'
             f'<rect width="100" height="100" fill="{GREEN}"/>'
             f'<g transform="translate(12 12) scale(0.76)">{inner}</g></svg>\n')

with open(os.path.join(PUB, "icon.svg"), "w") as f:
    f.write(icon)
with open(os.path.join(PUB, "icon-fullbleed.svg"), "w") as f:
    f.write(fullbleed)
print("OK: icon.svg + icon-fullbleed.svg (ahora corre los rsvg-convert del docstring)")
