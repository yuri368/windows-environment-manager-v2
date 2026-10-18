"""Read-only bundle/source checks; writes a report outside the shipped directory."""
import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pefile

from PyInstaller.archive.readers import CArchiveReader


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def execution_level(path):
    with pefile.PE(str(path)) as pe:
        for resource in pe.DIRECTORY_ENTRY_RESOURCE.entries:
            if resource.id != 24:
                continue
            for name in resource.directory.entries:
                for language in name.directory.entries:
                    data = language.data.struct
                    xml = ET.fromstring(pe.get_data(data.OffsetToData, data.Size))
                    for node in xml.iter():
                        if node.tag.rsplit('}', 1)[-1] == 'requestedExecutionLevel':
                            return node.attrib['level']
    raise ValueError(f'Missing execution level in PE manifest: {path.name}')


def verify(output):
    root = Path(__file__).resolve().parent.parent
    asset_root = root / "ui-v3" if (root / "ui-v3" / "index.html").is_file() else root
    bundle = output / "dist" / "EnvManagerV3"
    internal = bundle / "_internal"
    manifest = json.loads((internal / "runtime-manifest.json").read_text(encoding="utf-8"))
    staged = output / "stage"
    changed = [name for name, digest in manifest["pythonSources"].items()
               if not (root / name).is_file() or sha(root / name) != digest]
    changed += [name for name, digest in manifest["assets"].items()
                if not (asset_root / name).is_file() or sha(asset_root / name) != digest]
    changed += ['packaging_v3/' + name for name, digest in manifest['packagingSources'].items()
                if not (root / 'packaging_v3' / name).is_file() or sha(root / 'packaging_v3' / name) != digest]
    current_python = {path.relative_to(root).as_posix() for path in (root / 'envmanager_v3').glob('*.py')}
    changed += sorted(current_python - set(manifest['pythonSources']))
    if sha(root / 'envmanager_v3' / 'engine_worker.cjs') != manifest['engineWorkerSha256']:
        changed.append('envmanager_v3/engine_worker.cjs')
    errors = []
    identity = json.loads((bundle / "system-package.json").read_text(encoding="utf-8"))
    if set(identity) != {"protocol", "package_id", "version", "entry", "rules", "files"} or identity["protocol"] != 2:
        errors.append("System package identity schema mismatch")
    for name, digest in identity["files"].items():
        if sha(bundle / name) != digest:
            errors.append(f"Protected package identity hash mismatch: {name}")
    if set(identity["files"]) != {p.relative_to(bundle).as_posix() for p in bundle.rglob('*') if p.is_file() and p.name != 'system-package.json'}:
        errors.append("System package identity does not cover all runtime files")
    if [rule['name'] for rule in identity['rules']] != ['demo-plan.js', 'config-format.js', 'engine_worker.cjs']:
        errors.append('System identity rule order mismatch')
    rule_hash = hashlib.sha256()
    for rule in identity['rules']:
        rule_hash.update(rule['name'].encode('ascii') + b'\0' + (bundle / rule['path']).read_bytes() + b'\0')
    for name, digest in manifest["pythonSources"].items():
        if sha(staged / name) != digest:
            errors.append(f"Staged Python differs from manifest: {name}")
    for name, digest in manifest["assets"].items():
        if sha(internal / "ui-v3" / name) != digest:
            errors.append(f"Bundled asset differs from manifest: {name}")
    if sha(internal / "runtime" / "node.exe") != manifest["node"]["sha256"]:
        errors.append("Bundled Node differs from runtime pin")
    if sha(internal / "envmanager_v3" / "engine_worker.cjs") != manifest["engineWorkerSha256"]:
        errors.append("Bundled worker differs from manifest")
    archive = CArchiveReader(str(bundle / "EnvManagerV3.exe")).open_embedded_archive("PYZ.pyz")
    modules = sorted(name for name in archive.toc if name == "envmanager_v3" or name.startswith("envmanager_v3."))
    for name in ("service", "workspace", "contributions", "native_ipc", "system_helper", "upgrade"):
        if f"envmanager_v3.{name}" not in modules:
            errors.append(f"Missing V3 module: {name}")
    if any(name == "envmanager" or name.startswith(("envmanager.", "PySide6")) for name in archive.toc):
        errors.append("V2 or PySide included in Python archive")
    licenses = json.loads((internal / "licenses" / "manifest.json").read_text(encoding="utf-8"))
    for name, digest in licenses["sha256"].items():
        if sha(internal / "licenses" / name) != digest:
            errors.append(f"License file mismatch: {name}")
    files = json.loads((output / "bundle-files.sha256.json").read_text(encoding="utf-8"))
    for name, digest in files.items():
        if sha(bundle / name) != digest:
            errors.append(f"Bundle file mismatch: {name}")
    smoke = json.loads((output / "smoke" / "smoke-result.json").read_text(encoding="utf-8"))
    if rule_hash.hexdigest() != smoke.get('check', {}).get('engineHash'):
        errors.append('Package triple-rule identity differs from frozen planner handshake')
    levels = {'EnvManagerV3.exe': execution_level(bundle / 'EnvManagerV3.exe')}
    if levels['EnvManagerV3.exe'] != 'asInvoker':
        errors.append('Main executable must remain asInvoker')
    for name, digest in json.loads((output / 'build-result.json').read_text(encoding='utf-8'))['artifacts'].items():
        path = bundle / 'EnvManagerV3.exe' if name == 'EnvManagerV3/EnvManagerV3.exe' else output / name
        if sha(path) != digest:
            errors.append(f"Artifact hash mismatch: {name}")
        if 'setup-' in name and name.endswith('.exe'):
            levels[name] = execution_level(path)
            # Inno's outer loader can be asInvoker even with PrivilegesRequired=admin;
            # it handles setup elevation internally. Record the observed PE metadata.
            if '-machine-' not in name and levels[name] != 'asInvoker':
                errors.append(f'Wrong installer privilege manifest: {name}')
    if smoke.get("status") != "passed":
        errors.append("File smoke not passed")
    upgrade_smoke = json.loads((output / "upgrade-smoke" / "upgrade-smoke-result.json").read_text(encoding="utf-8"))
    if upgrade_smoke.get('status') != 'passed':
        errors.append('Installer upgrade-helper smoke not passed')
    result = {"status": "passed" if not errors and not changed else "failed",
              "currentSourceMatchesStaged": not changed, "changedSourceFiles": changed,
              "errors": errors, "productionAssetsVerified": len(manifest["assets"]),
              "licenseFilesVerified": len(licenses["sha256"]), "bundleFilesVerified": len(files),
              "v3Modules": modules, "backend": "file", "hostInstallerExecuted": False}
    result['rulesSha256'] = rule_hash.hexdigest()
    result['peExecutionLevels'] = levels
    (output / "bundle-verification.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    raise SystemExit(verify(parser.parse_args().output.resolve()))
