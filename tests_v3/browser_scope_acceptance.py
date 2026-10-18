"""Opt-in browser fixture using actual FileEnvironment/Service and contributions.

Starts a fresh disposable directory; never uses the Windows environment adapter.
Keep the process alive while checking undo: restarting revokes continuity.
"""
import argparse
import json
from pathlib import Path
import uuid

from envmanager_v3.__main__ import InstanceLock
from envmanager_v3.environment import FileEnvironment
from envmanager_v3.http_server import create_server
from envmanager_v3.service import Service


CONFIG = '''[ScopeA]
default_option = "Current"
[ScopeA.Current]
V3_SCOPE_X = { default_value = "A", scope = "User", mode = "+append", editable = true }
V3_SCOPE_Y = { default_value = "Q", scope = "User", mode = "+append", editable = true }
[ScopeA.Other]
V3_SCOPE_X = { default_value = "C", scope = "User", mode = "+append", editable = true }
[ScopeB]
default_option = "Current"
[ScopeB.Current]
V3_SCOPE_X = { default_value = "B", scope = "User", mode = "+append", editable = true }
[Ordering]
default_option = "Current"
[Ordering.Current]
V3_ORDER_U = { default_value = "U;V", scope = "User", mode = "append", editable = true }
V3_ORDER_T = { default_value = "T;W", scope = "User", mode = "append", editable = true }
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8789)
    args = parser.parse_args()
    if args.data_dir.exists():
        parser.error('Require a fresh directory; never silently reuse acceptance state.')
    with InstanceLock(args.data_dir / 'instance.lock'):
        env = FileEnvironment(args.data_dir / 'environment.json')
        for name, raw in [('V3_SCOPE_X', 'X'), ('V3_SCOPE_Y', 'Z'),
                          ('V3_ORDER_U', 'BASE;V'), ('V3_ORDER_T', 'BASE;W')]:
            env.write('User', name, raw, 'REG_EXPAND_SZ')
        service = Service(args.data_dir, environment=env)
        try:
            state = service.import_workspace({'text': CONFIG, 'filename': 'scope-acceptance.toml'})
            for app_name, profile_name in [('ScopeA', 'Current'), ('ScopeA', 'Other'), ('ScopeB', 'Current')]:
                app = next(a for a in state['apps'] if a['name'] == app_name)
                profile = next(p for p in app['profiles'] if p['name'] == profile_name)
                receipt = service.plan({'kind': 'apply', 'recordIds': [v['recordId'] for v in profile['vars']]})
                reviewed = service.review(receipt['planId'], {k: receipt[k] for k in ('version', 'digest')})
                result = service.execute(receipt['planId'], {**reviewed, 'idempotencyKey': uuid.uuid4().hex})
                if result['status'] != 'committed':
                    raise RuntimeError(result)
            server = create_server(service, Path(__file__).resolve().parent.parent, args.port)
            print(json.dumps({'url': server.launch_url, 'backend': 'file', 'seededRealOperations': 3}), flush=True)
            try:
                server.serve_forever()
            finally:
                server.server_close()
        finally:
            service.close()


if __name__ == '__main__':
    main()
