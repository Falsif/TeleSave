"""Сборка TeleSave: EXE (PyInstaller) и установщик (Inno Setup, если установлен)."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):   # русский текст в консоли Windows/GitHub
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import PyInstaller.__main__  # noqa: E402
from telesave.paths import APP_VERSION  # noqa: E402


def find_iscc():
    cands = [os.environ.get("ISCC"), shutil.which("iscc"), shutil.which("ISCC")]
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        if base:
            cands.append(os.path.join(base, "Inno Setup 6", "ISCC.exe"))
    return next((c for c in cands if c and os.path.isfile(c)), None)


def main():
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    shutil.rmtree(ROOT / "dist", ignore_errors=True)
    PyInstaller.__main__.run([
        "main.py", "--noconfirm", "--clean", "--windowed",
        "--name", "TeleSave",
        "--icon", str(ROOT / "assets" / "icon.ico"),
        "--add-data", f"assets{os.pathsep}assets",
        "--exclude-module", "tkinter",
        "--exclude-module", "PySide6.QtWebEngineCore",
        "--exclude-module", "PySide6.QtQml",
        "--exclude-module", "PySide6.QtQuick",
        "--exclude-module", "PySide6.Qt3DCore",
    ])
    print("\nEXE готов: dist\\TeleSave\\TeleSave.exe")

    iscc = find_iscc()
    if not iscc:
        print("\nInno Setup не найден — установщик не создан.\n"
              "Установите его командой:  winget install JRSoftware.InnoSetup\n"
              "и запустите сборку ещё раз.")
        return
    subprocess.check_call([iscc, f"/DAppVersion={APP_VERSION}", str(ROOT / "installer" / "TeleSave.iss")])
    print(f"\nУстановщик готов: output\\TeleSave-Setup-{APP_VERSION}.exe")


if __name__ == "__main__":
    main()
