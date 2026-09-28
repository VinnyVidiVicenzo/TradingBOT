"""tools/make_shortcuts.ps1 puts a working shortcut in the folders it is pointed at.

Every test aims the script at a scratch folder with ``-Into``, so nothing here
touches the real Desktop or Start menu.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "make_shortcuts.ps1"
LAUNCHER = ROOT / "run_desktop_app.pyw"
ICON = ROOT / "desktop_app" / "assets" / "app.ico"
NAME = "TraderBot Plan Tester.lnk"
POWERSHELL = shutil.which("powershell")


def _quote(text: object) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def _powershell(command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True,
        text=True,
        timeout=120,
    )


def _make(*folders: Path, remove: bool = False) -> subprocess.CompletedProcess[str]:
    targets = ",".join(_quote(folder) for folder in folders)
    return _powershell(f"& {_quote(SCRIPT)} {'-Remove ' if remove else ''}-Into {targets}")


def _read(link: Path) -> dict[str, str]:
    result = _powershell(
        f"(New-Object -ComObject WScript.Shell).CreateShortcut({_quote(link)}) | "
        "Select-Object TargetPath, Arguments, WorkingDirectory, IconLocation, Description | "
        "ConvertTo-Json"
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@unittest.skipUnless(
    sys.platform == "win32" and POWERSHELL, "Windows PowerShell is needed to make and read .lnk files"
)
class MakeShortcutsTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.desktop = Path(scratch.name) / "Desktop"
        self.start_menu = Path(scratch.name) / "Start Menu" / "Programs"

    def test_what_the_shortcut_points_at_exists(self) -> None:
        self.assertTrue(SCRIPT.is_file())
        self.assertTrue(LAUNCHER.is_file())
        self.assertTrue(ICON.is_file())

    def test_it_makes_a_shortcut_in_every_folder_it_is_given_creating_the_folder(self) -> None:
        result = _make(self.desktop, self.start_menu)
        self.assertEqual(result.returncode, 0, result.stderr)
        for folder in (self.desktop, self.start_menu):
            self.assertTrue((folder / NAME).is_file(), folder)

    def test_the_shortcut_starts_the_launcher_without_a_console_window(self) -> None:
        _make(self.desktop)
        link = _read(self.desktop / NAME)
        target = Path(link["TargetPath"])
        self.assertEqual(target.name.lower(), "pythonw.exe")
        self.assertTrue(target.is_file())
        self.assertEqual(link["Arguments"], f'"{LAUNCHER}"')
        self.assertEqual(os.path.normcase(link["WorkingDirectory"]), os.path.normcase(str(ROOT)))

    def test_the_shortcut_carries_the_app_icon_and_says_it_is_practice_only(self) -> None:
        _make(self.desktop)
        link = _read(self.desktop / NAME)
        icon_file, _, index = link["IconLocation"].rpartition(",")
        self.assertEqual(os.path.normcase(icon_file), os.path.normcase(str(ICON)))
        self.assertEqual(index, "0")
        self.assertIn("never real money", link["Description"])

    def test_running_it_again_just_refreshes_the_shortcut(self) -> None:
        _make(self.desktop)
        first = _read(self.desktop / NAME)
        again = _make(self.desktop)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(_read(self.desktop / NAME), first)

    def test_remove_deletes_only_its_own_shortcut(self) -> None:
        _make(self.desktop, self.start_menu)
        other = self.desktop / "Something else.lnk"
        other.write_bytes(b"not ours")
        result = _make(self.desktop, self.start_menu, remove=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.desktop / NAME).exists())
        self.assertFalse((self.start_menu / NAME).exists())
        self.assertTrue(other.exists())

    def test_remove_is_fine_when_there_is_nothing_to_remove(self) -> None:
        result = _make(self.desktop, remove=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
