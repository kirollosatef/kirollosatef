"""Turns the avatar into the ASCII portrait used by neofetch.py.

Only needed again if the avatar changes:
  curl -sL https://avatars.githubusercontent.com/u/74256854?s=460 -o avatar.png
  uvx --from "rembg[cpu,cli]" rembg i -m isnet-general-use avatar.png cutout.png
  uv run --with pillow python scripts/ascii.py cutout.png

Writes ascii-art.txt (dark mode), ascii-art-light.txt (light mode, inverted)
and ascii-eq.txt (Euler's equations placed in the empty background).
"""
import sys
from pathlib import Path
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

COLS, ROWS = 66, 41
RAMP = " .,:;~=+*xjk%#&@"
EQUATIONS = [  # most important first: placed while there is still room
    "e^(iπ) + 1 = 0",
    "e^(ix) = cos x + i·sin x",
    "V − E + F = 2",
    "Σ 1/n² = π²/6",
    "ζ(s) = Π 1/(1 − p^-s)",
    "a^φ(n) ≡ 1 (mod n)",
    "e = Σ 1/n!",
    "γ ≈ 0.5772156649",
    "y' = f(x, y)",
    "Γ(n) = (n − 1)!",
    "φ(n) = n·Π(1 − 1/p)",
    "∂L/∂q − d/dt ∂L/∂q' = 0",
    "1 + 1/4 + 1/9 + …",
    "Σ 1/p = ∞",
    "χ = 2 − 2g",
    "γ ≈ 0.5772",
    "e ≈ 2.71828",
    "Γ(n) = (n−1)!",
    "i² = −1",
    "f(x)",
]
ROOT = Path(__file__).resolve().parent.parent


def grids(src):
    im = Image.open(src).convert("RGBA")
    w, h = im.size
    im = im.crop((int(w * 0.12), int(h * 0.03), int(w * 0.92), int(h * 0.98)))
    alpha = im.getchannel("A").resize((COLS, ROWS), Image.LANCZOS)
    gray = ImageOps.grayscale(im.convert("RGB"))
    gray = ImageOps.equalize(gray, mask=im.getchannel("A").point(lambda a: 255 if a > 128 else 0))
    gray = ImageEnhance.Contrast(gray).enhance(1.15)
    gray = gray.resize((COLS, ROWS), Image.LANCZOS).filter(ImageFilter.UnsharpMask(1.2, 140, 2))
    fg = [[alpha.getpixel((x, y)) > 110 for x in range(COLS)] for y in range(ROWS)]
    lum = [[gray.getpixel((x, y)) / 255 for x in range(COLS)] for y in range(ROWS)]
    return fg, lum


def art(fg, lum, invert):
    lines = []
    for y in range(ROWS):
        row = ""
        for x in range(COLS):
            if not fg[y][x]:
                row += " "
                continue
            v = 1 - lum[y][x] if invert else lum[y][x]
            row += RAMP[max(1, min(len(RAMP) - 1, int(v * len(RAMP))))]  # never blank inside him
        lines.append(row.rstrip())
    return lines


def place_equations(fg):
    # A cell is free if no portrait cell is within 2 columns / 1 row of it.
    free = [[all(not fg[yy][xx]
                 for yy in range(max(0, y - 1), min(ROWS, y + 2))
                 for xx in range(max(0, x - 2), min(COLS, x + 3)))
             for x in range(COLS)] for y in range(ROWS)]
    grid = [[" "] * COLS for _ in range(ROWS)]
    placed = []  # (row, col-centre)
    for eq in EQUATIONS:
        best = None
        for y in range(1, ROWS - 1):
            if any(abs(y - py) < 2 for py, _ in placed):  # keep a blank row between equations
                continue
            for x in range(1, COLS - len(eq)):
                if not all(free[y][x + i] for i in range(len(eq))):
                    continue
                cx = x + len(eq) / 2
                # farthest from what is already placed, so they spread out
                score = min(((y - py) * 2) ** 2 + (cx - px) ** 2 for py, px in placed) if placed else -y
                if best is None or score > best[0]:
                    best = (score, y, x)
        if best:
            _, y, x = best
            grid[y][x:x + len(eq)] = list(eq)
            placed.append((y, x + len(eq) / 2))
            print(f"placed {eq!r} at row {y}, col {x}")
        else:
            print(f"no room for {eq!r}")
    return ["".join(r).rstrip() for r in grid]


def main():
    fg, lum = grids(sys.argv[1])
    (ROOT / "ascii-art.txt").write_text("\n".join(art(fg, lum, False)) + "\n")
    (ROOT / "ascii-art-light.txt").write_text("\n".join(art(fg, lum, True)) + "\n")
    (ROOT / "ascii-eq.txt").write_text("\n".join(place_equations(fg)) + "\n")


if __name__ == "__main__":
    main()
