# -*- mode: python ; coding: utf-8 -*-
import json
import os
from pathlib import Path

HERE = Path(SPECPATH)
STAGE = Path(os.environ["V3_BUILD_STAGE"])
contract = json.loads((HERE / "runtime-lock.json").read_text(encoding="utf-8"))
datas = [(str(STAGE / "ui-v3" / name), "ui-v3") for name in contract["assets"]]
datas += [(str(STAGE / "envmanager_v3" / "engine_worker.cjs"), "envmanager_v3")]
datas += [(str(path), "licenses") for path in (STAGE / "licenses").iterdir() if path.is_file()]
datas += [(str(STAGE / "runtime-manifest.json"), ".")]
a = Analysis(
    [str(HERE / "entrypoint.py")], pathex=[str(STAGE)],
    binaries=[(str(STAGE / "runtime" / "node.exe"), "runtime")], datas=datas,
    # V3 service/workspace are normal imports, not copies of V2 or data files.
    hiddenimports=["tomlkit", "envmanager_v3.system_helper"],
    excludes=["envmanager", "PySide6", "pytest", "tkinter"],
    hookspath=[], hooksconfig={}, runtime_hooks=[], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="EnvManagerV3",
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=True, manifest=str(HERE / "EnvManagerV3.manifest"))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="EnvManagerV3")
