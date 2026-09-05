"""Collect installed and vendored notices without network access or modification."""
from importlib.metadata import distribution
from pathlib import Path
import shutil
import sys


def collect(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6", "numpy", "scipy", "javaobj-py3", "pyinstaller"):
        package = distribution(name)
        folder = destination / name
        folder.mkdir(exist_ok=True)
        (folder / "METADATA.txt").write_text(package.read_text("METADATA") or "", encoding="utf-8")
        for member in package.files or []:
            if any(key in member.name.lower() for key in ("license", "copying")):
                source = Path(package.locate_file(member))
                if source.is_file():
                    target = folder / str(member).replace("/", "_").replace("\\", "_")
                    shutil.copy2(source, target)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.exists():
        shutil.copy2(python_license, destination / "Python-LICENSE.txt")
    local_notices = Path(__file__).resolve().parent / "licenses"
    for name in ("LGPL-3.0.txt", "GPL-3.0.txt", "LGPL-2.1.txt", "LICENSE_SOURCES.json"):
        source = local_notices / name
        target = destination / name
        if not source.is_file():
            raise FileNotFoundError(f"Missing vendored license: {source}. Restore the source package's licenses folder.")
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)


if __name__ == "__main__":
    collect(sys.argv[1])
