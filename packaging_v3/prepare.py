"""Stage explicit resources and record runtime provenance; no environment adapter imports."""
from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def assets_root():
    return ROOT / "ui-v3" if (ROOT / "ui-v3" / "index.html").is_file() else ROOT


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def stage(destination, node):
    source_assets = assets_root()
    lock = json.loads((HERE / "runtime-lock.json").read_text(encoding="utf-8"))
    if platform.system() != "Windows" or struct.calcsize("P") != 8:
        raise RuntimeError("Build requires Windows x64 CPython.")
    if platform.python_version() != lock["pythonVersion"]:
        raise RuntimeError(f"Use the pinned Python {lock['pythonVersion']}; detected {platform.python_version()}")
    node = Path(node).resolve()
    version = subprocess.check_output([str(node), "--version"], text=True).strip()
    if version != lock["nodeVersion"] or digest(node) != lock["nodeSha256"]:
        raise RuntimeError("Installed Node version/SHA256 differs from runtime-lock.json.")
    license_path = node.with_name("LICENSE")
    if not license_path.is_file() or digest(license_path) != lock["nodeLicenseSha256"]:
        raise RuntimeError("The installed Node LICENSE is missing or differs from its pinned hash.")
    official = HERE / "node-LICENSE.official"
    if digest(official) != lock["nodeOfficialLicenseSha256"]:
        raise RuntimeError("Pinned official Node license has changed.")
    if license_path.read_text(encoding="utf-8") != official.read_text(encoding="utf-8"):
        raise RuntimeError("Installed and official Node license texts differ.")
    checksums = (HERE / "node-SHASUMS256.txt").read_text(encoding="utf-8")
    if f"{lock['nodeSha256']}  win-x64/node.exe" not in checksums.splitlines():
        raise RuntimeError("Node official checksum evidence does not match the pin.")
    # New directory per run, never reuse stale product files or package user data.
    destination.mkdir(parents=True, exist_ok=False)
    for directory in ("ui-v3", "runtime", "licenses", "envmanager_v3"):
        (destination / directory).mkdir()
    # Other agents may keep editing the source tree while this isolated candidate builds.
    for source in (ROOT / "envmanager_v3").glob("*.py"):
        shutil.copyfile(source, destination / "envmanager_v3" / source.name)
    shutil.copyfile(ROOT / "envmanager_v3" / "engine_worker.cjs", destination / "envmanager_v3" / "engine_worker.cjs")
    for name in lock["assets"]:
        source = source_assets / name
        if not source.is_file():
            raise RuntimeError(f"Required production asset is missing: {name}; no demo fallback")
        shutil.copyfile(source, destination / "ui-v3" / name)
    shutil.copyfile(node, destination / "runtime" / "node.exe")
    shutil.copyfile(license_path, destination / "licenses" / "Node-LICENSE.txt")
    shutil.copyfile(official, destination / "licenses" / "Node-LICENSE-official.txt")
    shutil.copyfile(HERE / "node-SHASUMS256.txt", destination / "licenses" / "Node-SHASUMS256.txt")
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    shutil.copyfile(python_license, destination / "licenses" / "Python-LICENSE.txt")
    versions = {}
    for line in (HERE / "requirements-build.txt").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        requirement = line.split()[0]
        name, expected = requirement.split("==")
        dist = metadata.distribution(name)
        if dist.version != expected:
            raise RuntimeError(f"Build dependency {name}: expected {expected}, got {dist.version}")
        versions[name] = dist.version
        licenses = [file for file in (dist.files or []) if
                    ".dist-info/" in str(file).replace("\\", "/") and
                    ("license" in file.name.lower() or "copying" in file.name.lower())]
        if not licenses:
            raise RuntimeError(f"Missing license for build/runtime dependency {name}")
        for index, file in enumerate(licenses):
            shutil.copyfile(dist.locate_file(file), destination / "licenses" / f"{name}-{index}-{file.name}")
    shutil.copyfile(HERE / "requirements-build.txt", destination / "licenses" / "requirements-build.txt")
    shutil.copyfile(HERE / "Inno-LICENSE.txt", destination / "licenses" / "Inno-Setup-LICENSE.txt")
    license_manifest = {path.name: digest(path) for path in sorted((destination / "licenses").iterdir()) if path.is_file()}
    save(destination / "licenses" / "manifest.json", {"sha256": license_manifest})
    manifest = {
        "productVersion": lock["productVersion"], "architecture": "windows-x64",
        "python": platform.python_version(), "pythonExecutableSha256": digest(Path(sys.base_prefix) / "python.exe"),
        "node": {"version": version, "sha256": digest(node), "source": str(node),
                 "checksumsUrl": lock["nodeChecksumsUrl"], "binaryUrl": lock["nodeBinaryUrl"],
                 "licenseUrl": lock["nodeLicenseUrl"], "licenseSha256": digest(license_path),
                 "officialLicenseSha256": digest(official)},
        "dependencies": versions, "signed": False,
        "innoToolLockSha256": digest(HERE / "inno-tool-lock.json"),
        "licenseManifestSha256": digest(destination / "licenses" / "manifest.json"),
        "releaseStatus": "isolated-smoke-candidate; native installation and UAC acceptance pending",
        "assetsSource": source_assets.relative_to(ROOT).as_posix(),
        "assets": {name: digest(destination / "ui-v3" / name) for name in lock["assets"]},
        "packagingSources": {path.name: digest(path) for path in sorted(HERE.iterdir())
                             if path.is_file() and (path.suffix in {".py", ".ps1", ".iss", ".spec", ".manifest", ".json"}
                             or path.name == "requirements-build.txt") and path.name != "identity-interface-check.json"},
        "pythonSources": {str(path.relative_to(destination)).replace("\\", "/"): digest(path)
                          for path in sorted((destination / "envmanager_v3").glob("*.py"))},
        "engineWorkerSha256": digest(destination / "envmanager_v3" / "engine_worker.cjs"),
        "scope": "V3 only; no V2, tests, demo seed data, node_modules or user data",
    }
    save(destination / "runtime-manifest.json", manifest)


def seal(bundle):
    """Installed identity consumed by the native owner; trust also requires ACLs."""
    rules = [{"name": "demo-plan.js", "path": "_internal/ui-v3/demo-plan.js"},
             {"name": "config-format.js", "path": "_internal/ui-v3/config-format.js"},
             {"name": "engine_worker.cjs", "path": "_internal/envmanager_v3/engine_worker.cjs"}]
    files = {path.relative_to(bundle).as_posix(): digest(path)
             for path in sorted(bundle.rglob("*")) if path.is_file() and path != bundle / "system-package.json"}
    for rule in rules:
        if rule["path"] not in files:
            raise RuntimeError(f"Package identity is missing rule {rule['name']}")
    runtime = json.loads((bundle / "_internal" / "runtime-manifest.json").read_text(encoding="utf-8"))
    save(bundle / "system-package.json", {"protocol": 2, "package_id": "EnvManagerV3",
         "version": runtime["productVersion"], "entry": "EnvManagerV3.exe", "rules": rules, "files": files})


def finalize(bundle, output, installer=None):
    files = {str(path.relative_to(bundle)).replace("\\", "/"): digest(path)
             for path in sorted(bundle.rglob("*")) if path.is_file()}
    forbidden = ("envmanager/", "tests/", "tests_v3/", "node_modules/", "design-evidence/", "workspaces/")
    if any(any(part in name.lower() for part in forbidden) or
           name.lower().endswith((".sqlite3", ".sqlite3-wal", ".sqlite3-shm")) for name in files):
        raise RuntimeError("Unexpected V2/test/user-data content in bundle")
    save(output / "bundle-files.sha256.json", files)
    archive = output / "EnvManagerV3-3.0.0-windows-x64.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name in files:
            z.write(bundle / name, "EnvManagerV3/" + name)
    artifacts = {archive.name: digest(archive), "EnvManagerV3/EnvManagerV3.exe": digest(bundle / "EnvManagerV3.exe")}
    for path in installer or []:
        artifacts[path.name] = digest(path)
    smoke = output / "smoke" / "smoke-result.json"
    validation = json.loads(smoke.read_text(encoding="utf-8")) if smoke.is_file() else {"status": "not-passed"}
    upgrade_smoke = output / "upgrade-smoke" / "upgrade-smoke-result.json"
    upgrade_validation = json.loads(upgrade_smoke.read_text(encoding="utf-8")) if upgrade_smoke.is_file() else {"status": "not-passed"}
    save(output / "build-result.json", {"bundle": str(bundle.resolve()), "artifacts": artifacts,
                                       "installerBuilt": bool(installer), "signed": False,
                                       "installers": [path.name for path in installer or []],
                                       "validation": validation,
                                       "upgradeValidation": upgrade_validation,
                                       "releaseStatus": "isolated-smoke-candidate"})
    (output / "SHA256SUMS.txt").write_text("".join(f"{sha}  {name}\n" for name, sha in artifacts.items()), encoding="ascii")
    print(json.dumps({"output": str(output), "artifacts": artifacts, "installerBuilt": bool(installer)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("stage")
    p.add_argument("--destination", type=Path, required=True)
    p.add_argument("--node", required=True)
    p = sub.add_parser("seal")
    p.add_argument("--bundle", type=Path, required=True)
    p = sub.add_parser("finalize")
    p.add_argument("--bundle", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--installer", type=Path, action="append")
    args = parser.parse_args()
    if args.action == "stage":
        stage(args.destination, args.node)
    elif args.action == "seal":
        seal(args.bundle)
    else:
        finalize(args.bundle, args.output, args.installer)
