"""Build a clean source release without copying local configuration or state."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import tomllib
from zipfile import ZipFile, ZIP_DEFLATED


ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = (
    ".gitignore", "README.md", "LICENSE", "AGENTS.md", "DESIGN.md", "PRODUCT.md",
    "pyproject.toml", "requirements.txt", "requirements-build.txt", "THIRD_PARTY_NOTICES.md",
    "env.example.toml", "launcher.ps1", "启动.cmd", "演示.cmd",
)
SOURCE_PATTERNS = {
    "envmanager": ("*.py", "*.svg"),
    "tests": ("*.py",),
    "tools": ("*.py",),
    "docs": ("*.md", "*.png"),
    "packaging": ("*.py", "*.iss", "*.isl", "*.txt"),
}


def release_files(root: Path):
    files = [root / name for name in ROOT_FILES]
    for directory, patterns in SOURCE_PATTERNS.items():
        for pattern in patterns:
            files.extend(path for path in (root / directory).rglob(pattern)
                         if "__pycache__" not in path.parts)
    for path in files:
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing or unsupported release input: {path}")
    return sorted(set(files), key=lambda path: path.relative_to(root).as_posix())


def build_release(root=ROOT, output=None):
    root = Path(root).resolve()
    output = Path(output).resolve() if output else root / "dist"
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    directory = f'{metadata["name"]}-{metadata["version"]}'
    files = release_files(root)
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"{directory}-source.zip"
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as bundle:
        for path in files:
            bundle.write(path, f"{directory}/{path.relative_to(root).as_posix()}")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = archive.with_suffix(".zip.sha256")
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    return archive, checksum, len(files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="Output directory (default: dist)")
    args = parser.parse_args()
    archive, checksum, count = build_release(output=args.out)
    print(f"Source release: {archive}\nSHA-256: {checksum}\nFiles: {count}")


if __name__ == "__main__":
    main()
