"""Isolated import check using only the selected distribution's local modules."""
import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import socket
import sys

MODULES = ('hemi_lookup_web', 'hemi_lookup_cli', 'hemi_catalogue',
           'hemi_lookup.evidence_worker', 'hemi_lookup.public',
           'hemi_lookup.snapshot_packet', 'traze_evidence.__main__')


def main():
    root = Path(sys.argv[1]).resolve()
    selected = set(json.load(sys.stdin))
    local_roots = {path.stem for path in root.glob('*.py')}
    local_roots.update(path.name for path in root.iterdir() if (path / '__init__.py').is_file())
    attempts = []

    def forbid_network(*args, **kwargs):
        attempts.append(True)
        raise RuntimeError('Startup imports must not use network connections')

    socket.socket.connect = forbid_network
    socket.socket.connect_ex = forbid_network
    socket.create_connection = forbid_network

    class SourceOnly(importlib.machinery.SourceFileLoader):
        def get_code(self, fullname):
            return compile(self.get_data(self.path), self.path, 'exec', dont_inherit=True)

    class SelectedImports(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split('.')[0] not in local_roots:
                return None
            relative = fullname.replace('.', '/')
            for name, package in ((relative + '/__init__.py', True), (relative + '.py', False)):
                candidate = root / name
                if candidate.is_file():
                    if name not in selected:
                        raise ModuleNotFoundError('Release selection excludes required runtime module: ' + fullname)
                    loader = SourceOnly(fullname, str(candidate))
                    return importlib.util.spec_from_file_location(fullname, candidate, loader=loader,
                        submodule_search_locations=[str(candidate.parent)] if package else None)
            raise ModuleNotFoundError('Required local runtime module is absent: ' + fullname)

    sys.meta_path.insert(0, SelectedImports())
    checked = []
    try:
        for module in MODULES:
            importlib.import_module(module)
            checked.append(module)
        if attempts:
            raise RuntimeError('Startup imports attempted network access')
    except Exception as exc:
        report = {'status': 'failed', 'modules_imported': checked, 'error': str(exc),
                  'network_attempts': len(attempts), 'network_requests': 0}
    else:
        report = {'status': 'passed', 'modules_imported': checked, 'network_attempts': 0, 'network_requests': 0}
    print(json.dumps(report))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
