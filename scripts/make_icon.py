"""Genera el ícono de PERSONAL_OS (PNG del portal, favicon y .ico para accesos directos). Uso: python scripts\\make_icon.py
Diseño: cuadro oscuro con un anillo de degradado índigo→cian (una esfera/órbita) y una palomita blanca: se reconoce a 16 px y no se confunde con Edge."""
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "portal" / "static"
S = 1024  # se dibuja grande y se reduce (bordes suaves)


def gradient(size: int, a=(79, 70, 229), b=(34, 211, 238)) -> Image.Image:
    g = Image.new("RGB", (size, size))
    px = g.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * size)
            px[x, y] = tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))
    return g


def master() -> Image.Image:
    base = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(base)
    d.rounded_rectangle((0, 0, S - 1, S - 1), radius=int(S * 0.23), fill=(15, 23, 42, 255))  # fondo azul noche
    ring = Image.new("L", (S, S), 0)
    rd = ImageDraw.Draw(ring)
    m, w = int(S * 0.16), int(S * 0.095)
    rd.ellipse((m, m, S - m, S - m), fill=255)
    rd.ellipse((m + w, m + w, S - m - w, S - m - w), fill=0)
    base.paste(gradient(S), (0, 0), ring)  # anillo con degradado
    chk = Image.new("L", (S, S), 0)
    cd = ImageDraw.Draw(chk)
    pts = [(int(S * 0.355), int(S * 0.515)), (int(S * 0.46), int(S * 0.625)), (int(S * 0.665), int(S * 0.385))]
    cd.line(pts, fill=255, width=int(S * 0.085), joint="curve")
    for p in (pts[0], pts[-1]):  # extremos redondeados
        r = int(S * 0.0425)
        cd.ellipse((p[0] - r, p[1] - r, p[0] + r, p[1] + r), fill=255)
    base.paste(Image.new("RGBA", (S, S), (255, 255, 255, 255)), (0, 0), chk)
    return base


def main() -> None:
    img = master()
    for name, px in (("icon-512.png", 512), ("icon-192.png", 192), ("apple-touch-icon.png", 180), ("favicon-32.png", 32)):
        img.resize((px, px), Image.LANCZOS).save(OUT / name)
    img.save(OUT / "favicon.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    print("íconos listos en", OUT)


if __name__ == "__main__":
    main()
