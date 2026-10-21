"""The selected distribution must import without excluded workspace modules."""
from pathlib import Path
import unittest

from scripts.repository import check_startup, selected

ROOT = Path(__file__).resolve().parent.parent


class RepositoryStartupTests(unittest.TestCase):
    def test_selected_runtime_imports_without_network_or_private_state(self):
        result = check_startup(ROOT, selected(ROOT))
        self.assertEqual(result['status'], 'passed', result)
        self.assertEqual(result['network_attempts'], 0)
        self.assertEqual(result['network_requests'], 0)
        self.assertIn('hemi_lookup_web', result['modules_imported'])
        self.assertIn('traze_evidence.__main__', result['modules_imported'])
        self.assertIn('hemi_lookup.snapshot_packet', result['modules_imported'])

    def test_excluded_verifier_cannot_fall_back_to_the_workspace(self):
        files = [name for name in selected(ROOT) if not name.startswith('traze_evidence/')]
        result = check_startup(ROOT, files)
        self.assertEqual(result['status'], 'failed', result)
        self.assertIn('traze_evidence', result['error'])

    def test_excluded_transitive_dependency_fails_the_import_check(self):
        files = [name for name in selected(ROOT) if name != 'traze_evidence/secure_trie.py']
        result = check_startup(ROOT, files)
        self.assertEqual(result['status'], 'failed', result)
        self.assertIn('traze_evidence.secure_trie', result['error'])

    def test_excluded_snapshot_dependency_cannot_use_a_workspace_fallback(self):
        files = [name for name in selected(ROOT) if name != 'traze_evidence/snapshot.py']
        result = check_startup(ROOT, files)
        self.assertEqual(result['status'], 'failed', result)
        self.assertIn('traze_evidence.snapshot', result['error'])


if __name__ == '__main__':
    unittest.main()
