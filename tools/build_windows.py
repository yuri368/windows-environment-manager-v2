"""Freeze the Qt GUI and build a complete x64 Windows installation wizard."""

from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import distribution
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def build_environment():
    """Keep DLL collection independent of unrelated tools on the caller's PATH."""
    environment = os.environ.copy()
    windows = Path(environment.get("SystemRoot", r"C:\Windows"))
    environment["PATH"] = os.pathsep.join(map(str, (
        Path(sys.executable).parent, Path(sys.base_prefix),
        windows / "System32", windows,
    )))
    for key in ("PYTHONHOME", "PYTHONPATH", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
        environment.pop(key, None)
    return environment


def smoke_bundle(bundle):
    """Open the bundled GUI from an unrelated directory without a Python PATH."""
    environment = build_environment()
    windows = Path(environment.get("SystemRoot", r"C:\Windows"))
    environment["PATH"] = os.pathsep.join(map(str, (windows / "System32", windows)))
    for key in ("VIRTUAL_ENV", "QT_QPA_PLATFORM"):
        environment.pop(key, None)
    with tempfile.TemporaryDirectory(prefix="envmanager-release-smoke-") as temporary:
        directory = Path(temporary)
        environment["LOCALAPPDATA"] = str(directory / "profile")
        executable = bundle / "EnvironmentManagerV2.exe"
        screenshot = directory / "gui.png"
        for arguments in (("--check",), ("--demo", "--screenshot", str(screenshot))):
            result = subprocess.run([str(executable), *arguments], cwd=directory,
                                    env=environment, capture_output=True, timeout=45)
            if result.returncode:
                raise RuntimeError(f"Standalone GUI smoke test failed ({result.returncode}): {result.stderr!r}")
        if not screenshot.is_file() or screenshot.stat().st_size < 1000:
            raise RuntimeError("Standalone GUI did not produce its screenshot.")
        print("Standalone configuration and GUI smoke tests passed.")


def build_icon(output):
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
    from PySide6.QtGui import QPainter, QPixmap
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    renderer = QSvgRenderer(str(ROOT / "envmanager/assets/app.svg"))
    sizes = (16, 24, 32, 48, 64, 128, 256)
    images = []
    for size in sizes:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        assert pixmap.save(buffer, "PNG")
        images.append(bytes(data))
    offset = 6 + 16 * len(sizes)
    entries = []
    for size, data in zip(sizes, images):
        entries.append(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    output.write_bytes(struct.pack("<HHH", 0, 1, len(sizes)) + b"".join(entries) + b"".join(images))
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iscc", type=Path, help="Inno Setup 6+ ISCC.exe")
    parser.add_argument("--freeze-only", action="store_true", help="Build the standalone GUI for smoke testing")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Build on Windows x64.")
    if not args.freeze_only and (not args.iscc or not args.iscc.is_file()):
        parser.error("ISCC.exe not found.")
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    build = ROOT / "build" / "windows"
    build.mkdir(parents=True, exist_ok=True)
    icon = build / "app.ico"
    app = build_icon(icon)
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
               "--name", "EnvironmentManagerV2", "--icon", str(icon),
               "--paths", str(ROOT), "--specpath", str(build), "--workpath", str(build / "work"),
               "--distpath", str(ROOT / "dist" / "windows"), "--noupx",
               "--add-data", f"{ROOT / 'env.example.toml'};.",
               "--add-data", f"{ROOT / 'LICENSE'};.",
               "--add-data", f"{ROOT / 'envmanager/assets'};envmanager/assets",
               "--copy-metadata", "PySide6", "--copy-metadata", "PySide6_Essentials",
               "--copy-metadata", "shiboken6", "--copy-metadata", "tomlkit",
               str(ROOT / "packaging" / "gui_entry.py")]
    subprocess.run(command, cwd=ROOT, env=build_environment(), check=True)
    bundle = ROOT / "dist" / "windows" / "EnvironmentManagerV2"
    shutil.copy2(icon, bundle / "app.ico")
    shutil.copy2(ROOT / "LICENSE", bundle / "LICENSE")
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", bundle / "THIRD_PARTY_NOTICES.md")
    license_dir = bundle / "THIRD_PARTY_LICENSES"
    license_dir.mkdir(exist_ok=True)
    shutil.copy2(ROOT / "packaging/ChineseSimplified-LICENSE.txt", license_dir / "ChineseSimplified-LICENSE.txt")
    for package in ("PyInstaller", "tomlkit"):
        package_info = distribution(package)
        for file in package_info.files or ():
            if "licenses" in file.parts or "LICENSE" in file.name:
                source = Path(package_info.locate_file(file))
                if source.is_file():
                    shutil.copy2(source, license_dir / f"{package}-{file.name}")
    # Python's standard library includes its redistributable license.
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.exists():
        shutil.copy2(python_license, bundle / "PYTHON_LICENSE.txt")
    smoke_bundle(bundle)
    if args.freeze_only:
        print(f"Standalone GUI: {bundle / 'EnvironmentManagerV2.exe'}")
        return
    subprocess.run([str(args.iscc.resolve()), f'/DAppVersion={metadata["version"]}',
                    f"/DBundleDir={bundle}", f"/DOutputDir={ROOT / 'dist'}", f"/DSourceDir={ROOT}",
                    str(ROOT / "packaging" / "installer.iss")], check=True)
    installer = ROOT / "dist" / f'EnvironmentManagerV2-{metadata["version"]}-windows-x64-setup.exe'
    checksum = installer.with_suffix(".exe.sha256")
    checksum.write_text(hashlib.sha256(installer.read_bytes()).hexdigest() + "  " + installer.name + "\n", encoding="ascii")
    print(f"Installer: {installer}\nSHA-256: {checksum}")
    assert app is not None


if __name__ == "__main__":
    main()
