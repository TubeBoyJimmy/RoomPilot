"""Build a relocatable folder; no installer, administrator rights, or REW needed."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

root = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument("--work-dir", type=Path, default=root / "build")
parser.add_argument("--dist-dir", type=Path, default=root / "dist")
parser.add_argument("--console", action="store_true", help="Diagnostic build with console output")
args = parser.parse_args()
args.work_dir.mkdir(parents=True, exist_ok=True)
command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--console" if args.console else "--windowed", "--onedir",
           "--name", "RoomPilot", "--distpath", str(args.dist_dir.resolve()),
           "--workpath", str((args.work_dir / "cache").resolve()),
           "--specpath", str(args.work_dir.resolve()),
           "--paths", str(root), "--add-data", str(root / "roompilot" / "ui") + ";roompilot/ui",
           "--collect-data", "javaobj", "--copy-metadata", "javaobj-py3"]
for module in ("matplotlib", "pandas", "IPython", "tkinter", "notebook", "pytest",
               "torch", "torchvision", "tensorflow", "jax", "jaxlib", "cupy", "dask", "numba",
               "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick"):
    command += ["--exclude-module", module]
command.append(str(root / "run.py"))
# Avoid accidentally collecting unrelated ICU/UCRT DLLs from developer tools
# on PATH. Windows Qt uses the operating system ICU implementation.
environment = dict(os.environ)
windows = Path(environment.get("WINDIR", "C:/Windows"))
environment["PATH"] = os.pathsep.join(map(str, [Path(sys.executable).parent,
    Path(sys.base_prefix), windows / "System32", windows]))
subprocess.run(command, cwd=root, env=environment, check=True)
folder = args.dist_dir.resolve() / "RoomPilot"
for name in ("README.md", "MODEL.md", "HISTORY.md", "KNOWN_LIMITATIONS.md", "THIRD_PARTY_NOTICES.md", "VALIDATION.md"):
    if (root / name).is_file():
        shutil.copy2(root / name, folder / name)
from collect_licenses import collect
collect(folder / "licenses")
print("Built:", folder / "RoomPilot.exe")
