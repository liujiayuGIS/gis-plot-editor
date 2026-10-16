"""Portable dependency/bootstrap helper. Does not launch a GUI or change global PATH.
Default is read-only; --install/--build-app/--register-app are explicit actions.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

SKILL = Path(__file__).resolve().parent.parent
MODULES = ("matplotlib", "numpy", "geopandas", "rasterio", "pyproj", "PIL", "tkinter")


def default_runtime():
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))
    return base / "GisPlotEditor" / "desktop-runtime"


def runtime_python(folder):
    return Path(folder) / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run(argv):
    subprocess.run([str(x) for x in argv], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=default_runtime())
    parser.add_argument("--install", action="store_true", help="Explicitly create isolated venv and install requirements")
    parser.add_argument("--build-app", action="store_true", help="Explicitly install PyInstaller and build Windows desktop app")
    parser.add_argument("--output-dir", type=Path, help="User-selected directory for built desktop app; required for --build-app")
    parser.add_argument("--register-app", action="store_true", help="Explicitly create Start-menu shortcut to built application")
    args = parser.parse_args()
    python = runtime_python(args.runtime)
    if args.build_app and (os.name != "nt" or args.output_dir is None):
        parser.error("--build-app requires Windows and --output-dir")
    if args.register_app and (os.name != "nt" or args.output_dir is None):
        parser.error("--register-app requires Windows and --output-dir")
    if args.install:
        if not python.exists():
            venv.EnvBuilder(with_pip=True).create(args.runtime)
        run([python, "-m", "pip", "install", "-r", SKILL / "scripts/requirements.txt"])
    if args.build_app:
        if not python.exists(): parser.error("Run with --install or choose an existing runtime")
        app = args.output_dir / "GisPlotEditor" / "GisPlotEditor.exe"
        if app.exists():
            parser.error("Application output already exists; choose a new directory, do not silently replace it")
        run([python, "-m", "pip", "install", "pyinstaller"])
        build = args.runtime.parent / "build"
        run([python, "-m", "PyInstaller", "--noconfirm", "--onedir", "--windowed", "--name", "GisPlotEditor",
             "--distpath", args.output_dir, "--workpath", build, "--specpath", args.runtime.parent,
             "--add-data", str(SKILL / "assets") + os.pathsep + "assets",
             "--hidden-import", "geopandas", "--hidden-import", "pyogrio", "--hidden-import", "rasterio._features",
             "--collect-submodules", "rasterio", "--collect-data", "pyproj", "--collect-data", "rasterio",
             "--copy-metadata", "matplotlib", "--copy-metadata", "numpy", "--copy-metadata", "geopandas",
             "--copy-metadata", "rasterio", "--copy-metadata", "pyproj",
             SKILL / "scripts/launch_gui.py"])
    if args.register_app:
        app = (args.output_dir / "GisPlotEditor" / "GisPlotEditor.exe").resolve()
        if not app.is_file(): parser.error("Built application not found")
        link = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/GIS 出图编辑器.lnk"
        if link.exists(): parser.error("Start-menu shortcut already exists; do not replace another installation")
        # Values passed through environment, never interpolated into PowerShell source.
        environment = dict(os.environ, GIS_EDITOR_APP=str(app), GIS_EDITOR_LINK=str(link))
        command = "$s=New-Object -ComObject WScript.Shell; $l=$s.CreateShortcut($env:GIS_EDITOR_LINK); $l.TargetPath=$env:GIS_EDITOR_APP; $l.Arguments=''; $l.Description='GIS editable plotting skill'; $l.Save()"
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command], env=environment, check=True)
    print(json.dumps({"python": sys.executable, "python_version": sys.version.split()[0],
                      "current_modules": {m: importlib.util.find_spec(m) is not None for m in MODULES},
                      "runtime_python": str(python), "runtime_exists": python.exists(),
                      "skill_dir": str(SKILL), "gui_started": False}, ensure_ascii=False, indent=2))
    if python.exists() and args.install:
        run([python, SKILL / "scripts/gis_editor.py", "doctor"])


if __name__ == "__main__":
    main()
