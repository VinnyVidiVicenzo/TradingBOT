"""Draw the app's icon and write it to desktop_app/assets/app.ico.

    python tools/make_icon.py

Standard library only. The icon is a small map of coloured squares in the same
colours as the "which starting months would have worked" map inside the app:
green where a plan gained, red where it lost. It is drawn at four times its
size and shrunk, so the edges are smooth, at each size Windows asks for.
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

OUTPUT = Path(__file__).resolve().parent.parent / "desktop_app" / "assets" / "app.ico"

Color = tuple[int, int, int, int]
Pixels = list[list[Color]]

SUPERSAMPLE = 4
SIZES = (16, 32, 48, 256)
# Entries this size or larger are stored as PNG, the rest as plain bitmaps:
# the .ico format only guarantees PNG for the 256 x 256 picture.
PNG_FROM = 256


def _hex(value: str) -> Color:
    return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16), 255)


CLEAR: Color = (0, 0, 0, 0)
TILE = _hex("#faf9f6")
TILE_EDGE = _hex("#c9c7bf")

# The four colours of the starting-month map (desktop_app/widgets/start_map.py).
CODES: dict[str, Color] = {
    "D": _hex("#0f6e56"),  # more than doubled
    "g": _hex("#9fe1cb"),  # gained
    "l": _hex("#f7c1c1"),  # lost some
    "L": _hex("#a32d2d"),  # lost most
}

# One picture per grid size: a run of bad starting months in the lower left,
# good ones elsewhere. Fewer squares at small sizes so each one stays readable.
PICTURES: dict[int, tuple[str, ...]] = {
    3: ("DDg", "lgD", "Llg"),
    4: ("gDDg", "lgDD", "Llgg", "LLlg"),
    5: ("gDDgg", "ggDDg", "llgDD", "LLlgD", "LLLlg"),
}
GRID_FOR_SIZE = {16: 3, 32: 4, 48: 5, 256: 5}

# Layout, as shares of the icon's width.
TILE_MARGIN = 0.03
TILE_RADIUS = 0.22
TILE_EDGE_WIDTH = 0.025
GRID_MARGIN = 0.17
GAP = 0.15  # between squares, as a share of one square's pitch
SQUARE_RADIUS = 0.2  # as a share of a square's side


def render(size: int) -> Pixels:
    """The icon at ``size`` x ``size`` pixels, as rows of (r, g, b, a)."""
    grid = GRID_FOR_SIZE[size]
    big = size * SUPERSAMPLE
    pixels: Pixels = [[CLEAR] * big for _ in range(big)]

    margin = round(TILE_MARGIN * big)
    edge = max(2, round(TILE_EDGE_WIDTH * big))
    radius = round(TILE_RADIUS * big)
    _round_rect(pixels, margin, margin, big - margin, big - margin, radius, TILE_EDGE)
    _round_rect(
        pixels, margin + edge, margin + edge, big - margin - edge, big - margin - edge,
        radius - edge, TILE,
    )

    origin = round(GRID_MARGIN * big)
    pitch = (big - 2 * origin) / grid
    gap = pitch * GAP
    side = pitch - gap
    for row, line in enumerate(PICTURES[grid]):
        for col, code in enumerate(line):
            left = origin + col * pitch + gap / 2
            top = origin + row * pitch + gap / 2
            _round_rect(
                pixels, round(left), round(top), round(left + side), round(top + side),
                round(SQUARE_RADIUS * side), CODES[code],
            )
    return _shrink(pixels, size)


def build_ico() -> bytes:
    """The whole .ico file: every size in ``SIZES``, smallest first."""
    images = []
    for size in SIZES:
        pixels = render(size)
        images.append((size, _png(size, pixels) if size >= PNG_FROM else _bitmap(size, pixels)))

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    directory = b""
    for size, data in images:
        # A width or height of 256 is written as 0 in the directory.
        directory += struct.pack(
            "<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset
        )
        offset += len(data)
    return header + directory + b"".join(data for _, data in images)


def _round_rect(
    pixels: Pixels, left: int, top: int, right: int, bottom: int, radius: float, color: Color
) -> None:
    for y in range(top, bottom):
        centre = y + 0.5
        if centre < top + radius:
            reach = top + radius - centre
        elif centre > bottom - radius:
            reach = centre - (bottom - radius)
        else:
            reach = 0.0
        inset = 0
        if reach > 0:
            inset = math.ceil(radius - math.sqrt(max(radius * radius - reach * reach, 0.0)))
        start, stop = left + inset, right - inset
        if stop > start:
            pixels[y][start:stop] = [color] * (stop - start)


def _shrink(pixels: Pixels, size: int) -> Pixels:
    """Average each SUPERSAMPLE x SUPERSAMPLE block, weighting colour by opacity."""
    scale = SUPERSAMPLE
    area = scale * scale
    out: Pixels = []
    for oy in range(size):
        band = pixels[oy * scale : (oy + 1) * scale]
        row: list[Color] = []
        for ox in range(size):
            red = green = blue = alpha = 0
            for line in band:
                for r, g, b, a in line[ox * scale : (ox + 1) * scale]:
                    red += r * a
                    green += g * a
                    blue += b * a
                    alpha += a
            if alpha == 0:
                row.append(CLEAR)
            else:
                row.append((round(red / alpha), round(green / alpha), round(blue / alpha), round(alpha / area)))
        out.append(row)
    return out


def _png(size: int, pixels: Pixels) -> bytes:
    raw = b"".join(b"\x00" + b"".join(bytes(px) for px in row) for row in pixels)

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def _bitmap(size: int, pixels: Pixels) -> bytes:
    """A 32-bit bitmap the way .ico stores it: bottom row first, then an empty mask."""
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    body = b"".join(b"".join(bytes((b, g, r, a)) for r, g, b, a in row) for row in reversed(pixels))
    mask = b"\x00" * (((size + 31) // 32) * 4) * size
    return header + body + mask


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    data = build_ico()
    OUTPUT.write_bytes(data)
    print(f"Wrote {OUTPUT} ({len(data):,} bytes)")


if __name__ == "__main__":
    main()
