"""Opt-in native notification browser fixture, ONLY in verified DefaultBox.

Creates one unique boxed User variable, serves the real Service/UI for at most
240 seconds, and restores absence in finally. No System writes or broadcasts.
The ready.json launch ticket is local runtime state and must not be committed.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import uuid


def require_defaultbox():
    if os.name != 'nt':
        raise RuntimeError('Requires Windows DefaultBox, never the host.')
    dll = ctypes.WinDLL(r'C:\Program Files\Sandboxie-Plus\SbieDll.dll')
    query = dll.SbieApi_QueryProcess
    query.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_void_p,
                      ctypes.c_void_p, ctypes.c_void_p]
    query.restype = ctypes.c_long
    box = ctypes.create_unicode_buffer(34)
    if query(os.getpid(), box, None, None, None) != 0 or box.value != 'DefaultBox':
        raise RuntimeError('Actual process must belong to DefaultBox.')


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=True, indent=2), encoding='utf-8')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verified-defaultbox-only', action='store_true', required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8792)
    parser.add_argument('--action', choices=('external', 'stop'))
    args = parser.parse_args()
    require_defaultbox()  # Before any environment adapter import/read/write.
    root = args.root.resolve()
    if args.action:
        if not (root / 'ready.json').is_file():
            raise RuntimeError('No known running fixture to control.')
        write_json(root / 'action.json', {'action': args.action})
        return
    root.mkdir(parents=True, exist_ok=False)
    from envmanager_v3.__main__ import InstanceLock, assets_directory
    from envmanager_v3.environment import WindowsEnvironment
    from envmanager_v3.http_server import create_server
    from envmanager_v3.service import Service

    native = WindowsEnvironment()
    name = 'V3_UI_NATIVE_' + uuid.uuid4().hex[:12].upper()
    initial = ' A;B;;中文\r\n尾部 '
    external = '外部;B;;中文 '
    service = server = controller = None
    done = threading.Event()
    result = {'scope': 'DefaultBox native notification + real browser UI',
              'name': name, 'hostWrites': False, 'systemWrites': False,
              'broadcastTested': False, 'changes': []}

    def inventory_hash():
        rows = sorted((r['scope'], r['name'], r['exists'], r['type'],
                       json.dumps(r['raw'], ensure_ascii=True, sort_keys=True))
                      for r in native.list()['items'])
        return hashlib.sha256(json.dumps(rows).encode()).hexdigest()

    before_hash = inventory_hash()
    if native.read('User', name)['exists']:
        raise RuntimeError('Generated test name already exists; refusing overwrite.')
    try:
        native.write('User', name, initial, 'REG_EXPAND_SZ')
        with InstanceLock(root / 'data' / 'instance.lock'):
            service = Service(root / 'data', environment=WindowsEnvironment(),
                              assets=assets_directory(), monitor_environment=True)
            config = ('[NativeNotification]\ndefault_option = "Current"\n'
                      '[NativeNotification.Current]\n' + name +
                      ' = { default_value = "B;C", scope = "User", mode = "+append", editable = true }\n')
            service.import_workspace({'text': config, 'filename': 'native-notification.toml'})
            monitor = service.environment.changes_status()
            if not monitor['scopes']['User'] or monitor['errors']:
                raise RuntimeError('Native User notification monitor did not start.')
            server = create_server(service, assets_directory(), args.port)
            write_json(root / 'ready.json', {'url': server.launch_url, 'name': name,
                                           'initialRaw': initial, 'externalRaw': external,
                                           'monitor': monitor, 'pid': os.getpid()})

            def control():
                deadline = time.monotonic() + 240
                try:
                    while not done.wait(0.1) and time.monotonic() < deadline:
                        command = root / 'action.json'
                        if not command.exists():
                            continue
                        action = json.loads(command.read_text(encoding='utf-8'))['action']
                        command.unlink()
                        if action == 'stop':
                            return
                        if action != 'external':
                            raise RuntimeError('Unsupported fixture action.')
                        before = native.read('User', name)
                        after = native.write('User', name, external, 'REG_EXPAND_SZ', expected=before)
                        result['changes'].append({'before': before, 'after': after})
                        write_json(root / 'external-result.json', result['changes'][-1])
                except Exception as error:
                    result['controlError'] = str(error)
                finally:
                    server.shutdown()

            controller = threading.Thread(target=control, daemon=True)
            controller.start()
            server.serve_forever(poll_interval=0.1)
            result['monitorAtEnd'] = service.environment.changes_status()
    finally:
        done.set()
        if controller:
            controller.join(timeout=5)
        if server:
            server.server_close()
        if service:
            service.close()
        current = native.read('User', name)
        if current['exists']:
            if current['raw'] not in (initial, external) or current['type'] != 'REG_EXPAND_SZ':
                raise RuntimeError('Unexpected third-party test value: do not overwrite it.')
            native.delete('User', name, expected=current)
        result['restoredAbsent'] = not native.read('User', name)['exists']
        result['environmentInventoryRestored'] = inventory_hash() == before_hash
        result['status'] = 'passed' if (result['restoredAbsent'] and
                           result['environmentInventoryRestored'] and
                           not result.get('controlError')) else 'failed'
        write_json(root / 'result.json', result)
        native.close()
    print(json.dumps(result, ensure_ascii=True), flush=True)
    if result['status'] != 'passed':
        raise RuntimeError('Native fixture cleanup/control did not pass.')


if __name__ == '__main__':
    main()
