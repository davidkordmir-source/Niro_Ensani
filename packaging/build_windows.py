"""Build a self-contained Windows x64 installer on Windows or Linux with NSIS."""

from __future__ import annotations

import argparse
from hashlib import file_digest
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from urllib.request import urlopen
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"
PYTHON_VERSION = "3.13.11"
PYTHON_SHA256 = "56060654f7a552992cf27f64ac427001446f2c3f15b6acb41b1b8f38a75b5403"
PYTHON_URL = f"https://api.nuget.org/v3-flatcontainer/python/{PYTHON_VERSION}/python.{PYTHON_VERSION}.nupkg"
QT_BINDINGS = ("QtCore", "QtGui", "QtWidgets")
QT_DLLS = {
    "Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll", "pyside6.abi3.dll",
    "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_codecvt_ids.dll",
    "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll",
}
QT_PLUGINS = {"plugins/platforms/qwindows.dll", "plugins/platforms/qoffscreen.dll"}


def checksum(path: Path) -> str:
    with path.open("rb") as handle:
        return file_digest(handle, "sha256").hexdigest()


def copy_member(archive: ZipFile, member: str, directory: Path, relative: str):
    parts = PurePosixPath(relative)
    if parts.is_absolute() or ".." in parts.parts:
        raise ValueError(f"Unexpected package path {relative}")
    target = directory.joinpath(*parts.parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    with archive.open(member) as src, target.open("wb") as dst:
        shutil.copyfileobj(src, dst)


def stage_python(cache: Path, payload: Path):
    bundle = cache / "python.nupkg"
    if not bundle.exists():
        temporary = bundle.with_suffix(".download")
        try:
            with urlopen(PYTHON_URL, timeout=60) as response, temporary.open("wb") as output:
                shutil.copyfileobj(response, output)
            if checksum(temporary) != PYTHON_SHA256:
                raise ValueError("CPython package checksum mismatch")
            temporary.replace(bundle)
        finally:
            temporary.unlink(missing_ok=True)
    if checksum(bundle) != PYTHON_SHA256:
        raise ValueError(f"CPython package checksum mismatch: {bundle}")
    with ZipFile(bundle) as archive:
        for name in archive.namelist():
            if name.endswith("/") or not name.startswith("tools/"):
                continue
            relative = name.removeprefix("tools/")
            if relative.startswith(("libs/", "include/", "Lib/site-packages/", "Lib/ensurepip/", "Lib/test/")):
                continue
            copy_member(archive, name, payload, relative)
    (payload / "python313._pth").write_text("Lib\nDLLs\n.\nvendor\n", encoding="ascii")
    (payload / "qt.conf").write_text("[Paths]\nPlugins=vendor/PySide6/plugins\n", encoding="ascii")


def included_wheel_member(name: str) -> bool:
    if name.endswith(("/", ".pyi", ".lib")) or ".data/" in name or name.endswith(".dist-info/RECORD"):
        return False
    if name.startswith("PySide6/"):
        path = name.removeprefix("PySide6/")
        return (path in QT_DLLS or path in QT_PLUGINS or path in {item + ".pyd" for item in QT_BINDINGS}
                or path.startswith("support/") or ("/" not in path and path.endswith(".py")))
    if name.startswith("shiboken6/"):
        return name.count("/") == 1
    return True


def stage_project(cache: Path, payload: Path):
    wheels = cache / "wheels"
    wheels.mkdir(exist_ok=True)
    subprocess.run([
        sys.executable, "-m", "pip", "--disable-pip-version-check", "download", "--only-binary=:all:",
        "--platform", "win_amd64", "--python-version", "3.13", "--require-hashes",
        "--requirement", str(PACKAGING / "requirements-win.lock"), "--dest", str(wheels),
    ], check=True)
    hashes = set(re.findall(r"--hash=sha256:([a-f0-9]+)", (PACKAGING / "requirements-win.lock").read_text()))
    selected_wheels = [path for path in wheels.glob("*.whl") if checksum(path) in hashes]
    if len(selected_wheels) != len(hashes):
        raise ValueError("Windows wheels are missing or their package checksums do not match")
    vendor = payload / "vendor"
    for wheel in selected_wheels:
        with ZipFile(wheel) as archive:
            for name in archive.namelist():
                if included_wheel_member(name):
                    copy_member(archive, name, vendor, name)
    shutil.copytree(ROOT / "niro", payload / "niro", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(PACKAGING / "licenses", payload / "licenses")
    shutil.copy2(PACKAGING / "launch.py", payload / "launch.py")


def compile_nsis(executable: str, script: str, **defines):
    flag = "/D" if os.name == "nt" else "-D"
    subprocess.run([executable, *(f"{flag}{key}={value}" for key, value in defines.items()), str(PACKAGING / script)], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--makensis", default="makensis")
    parser.add_argument("--cache", type=Path, default=ROOT / "build" / "package-cache")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--keep-payload", type=Path, help="Copy the staged runtime here for local smoke tests")
    options = parser.parse_args()
    output, cache = options.output.resolve(), options.cache.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    setup = output / "NiroDesk-Setup-x64.exe"
    version = re.search(r'__version__ = "([\d.]+)"', (ROOT / "niro/__init__.py").read_text())[1]
    with TemporaryDirectory(prefix="niro-stage-") as directory:
        stage = Path(directory).resolve()
        payload = stage / "payload"
        stage_python(cache, payload)
        stage_project(cache, payload)
        launcher = stage / "NiroDesk.exe"
        definitions = {"ICONFILE": ROOT / "niro" / "assets" / "niro.ico", "LAUNCHER": launcher, "VERSION": version}
        compile_nsis(options.makensis, "launcher.nsi", **definitions)
        compile_nsis(
            options.makensis, "installer.nsi", **definitions,
            SETUP=setup, PAYLOAD=payload, NOTICE=PACKAGING / "NOTICE.txt",
        )
        if options.keep_payload:
            shutil.copytree(payload, options.keep_payload)
    digest = checksum(setup)
    (output / (setup.name + ".sha256")).write_text(f"{digest}  {setup.name}\n", encoding="ascii")
    print(f"{setup} ({setup.stat().st_size:,} bytes, SHA-256 {digest})")


if __name__ == "__main__":
    main()
