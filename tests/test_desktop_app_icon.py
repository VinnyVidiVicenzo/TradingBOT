"""The app icon: a well-formed .ico, drawn by tools/make_icon.py, that Tk accepts."""

from __future__ import annotations

import ast
import importlib.util
import struct
import sys
import tkinter as tk
import unittest
import zlib
from pathlib import Path
from unittest import mock

from desktop_app.app import ICON_PATH

from .tk_helpers import AppTestCase, TkTestCase

GENERATOR = Path(__file__).resolve().parent.parent / "tools" / "make_icon.py"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _load_generator():
    spec = importlib.util.spec_from_file_location("make_icon", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_directory(data: bytes) -> tuple[int, int, list[dict]]:
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    entries = []
    for i in range(count):
        width, height, colors, zero, planes, bits, size, offset = struct.unpack(
            "<BBBBHHII", data[6 + 16 * i : 22 + 16 * i]
        )
        entries.append(
            {
                "width": width or 256,
                "height": height or 256,
                "colors": colors,
                "reserved": zero,
                "planes": planes,
                "bits": bits,
                "size": size,
                "offset": offset,
                "blob": data[offset : offset + size],
            }
        )
    return reserved, kind, entries


def _pixels(entry: dict) -> list[list[tuple[int, int, int, int]]]:
    """Decode one entry back into rows of (r, g, b, a)."""
    blob, side = entry["blob"], entry["width"]
    if blob.startswith(PNG_SIGNATURE):
        position, compressed = len(PNG_SIGNATURE), b""
        while position < len(blob):
            (length,) = struct.unpack(">I", blob[position : position + 4])
            if blob[position + 4 : position + 8] == b"IDAT":
                compressed += blob[position + 8 : position + 8 + length]
            position += 12 + length
        raw = zlib.decompress(compressed)
        stride = side * 4 + 1
        rows = []
        for y in range(side):
            line = raw[y * stride : (y + 1) * stride]
            assert line[0] == 0, "the generator writes unfiltered rows"
            rows.append([tuple(line[1 + 4 * x : 5 + 4 * x]) for x in range(side)])
        return rows
    body = blob[40 : 40 + side * side * 4]
    rows = []
    for y in range(side):
        line = body[(side - 1 - y) * side * 4 : (side - y) * side * 4]
        rows.append([(line[4 * x + 2], line[4 * x + 1], line[4 * x], line[4 * x + 3]) for x in range(side)])
    return rows


class IconFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = ICON_PATH.read_bytes()
        cls.reserved, cls.kind, cls.entries = _read_directory(cls.data)
        cls.generator = _load_generator()

    def test_it_is_an_icon_file_holding_the_sizes_windows_asks_for(self) -> None:
        self.assertEqual((self.reserved, self.kind), (0, 1))
        self.assertEqual(sorted(e["width"] for e in self.entries), [16, 32, 48, 256])

    def test_every_picture_is_square_full_colour_and_single_plane(self) -> None:
        for entry in self.entries:
            with self.subTest(size=entry["width"]):
                self.assertEqual(entry["width"], entry["height"])
                self.assertEqual((entry["planes"], entry["bits"]), (1, 32))
                self.assertEqual((entry["colors"], entry["reserved"]), (0, 0))

    def test_the_pictures_follow_the_directory_and_end_where_the_file_ends(self) -> None:
        expected_offset = 6 + 16 * len(self.entries)
        for entry in self.entries:
            with self.subTest(size=entry["width"]):
                self.assertEqual(entry["offset"], expected_offset)
                self.assertEqual(len(entry["blob"]), entry["size"])
                expected_offset += entry["size"]
        self.assertEqual(expected_offset, len(self.data))

    def test_only_the_largest_picture_is_a_png(self) -> None:
        for entry in self.entries:
            with self.subTest(size=entry["width"]):
                self.assertEqual(
                    entry["blob"].startswith(PNG_SIGNATURE), entry["width"] == 256
                )

    def test_each_picture_is_what_the_generator_draws(self) -> None:
        for entry in self.entries:
            with self.subTest(size=entry["width"]):
                self.assertEqual(_pixels(entry), self.generator.render(entry["width"]))

    def test_the_corners_are_see_through_and_the_middle_is_solid(self) -> None:
        for entry in self.entries:
            side = entry["width"]
            rows = _pixels(entry)
            with self.subTest(size=side):
                self.assertEqual(rows[0][0][3], 0)
                self.assertEqual(rows[side - 1][side - 1][3], 0)
                self.assertEqual(rows[side // 2][side // 2][3], 255)

    def test_the_picture_uses_the_maps_green_and_red(self) -> None:
        colours = {px[:3] for row in _pixels(self.entries[-1]) for px in row if px[3] == 255}
        for name in ("D", "g", "l", "L"):
            self.assertIn(self.generator.CODES[name][:3], colours)

    def test_the_generator_needs_nothing_beyond_the_standard_library(self) -> None:
        tree = ast.parse(GENERATOR.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertEqual(imported - {"__future__", "math", "struct", "zlib", "pathlib"}, set())


@unittest.skipUnless(sys.platform == "win32", "only Windows reads .ico files as window icons")
class TkAcceptsTheIconTests(TkTestCase):
    def test_tk_can_load_it_as_a_window_icon(self) -> None:
        self.root.iconbitmap(default=str(ICON_PATH))


class IconInTheAppTests(AppTestCase):
    def test_the_app_ships_its_icon(self) -> None:
        self.assertTrue(ICON_PATH.is_file())
        self.assertEqual(ICON_PATH.parent.name, "assets")

    def test_the_app_gives_its_windows_the_icon(self) -> None:
        with mock.patch.object(self.app.root, "iconbitmap") as icon:
            self.app._set_icon()
        icon.assert_called_once_with(default=str(ICON_PATH))

    def test_an_icon_that_will_not_load_never_stops_the_app_starting(self) -> None:
        with mock.patch.object(self.app.root, "iconbitmap", side_effect=tk.TclError("no")):
            with self.assertLogs("desktop_app.app", level="INFO"):
                self.app._set_icon()


if __name__ == "__main__":
    unittest.main()
