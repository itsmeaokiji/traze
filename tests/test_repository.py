"""The selected distribution must import without excluded workspace modules."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from scripts.repository import check, check_startup, inspect_text, selected

ROOT = Path(__file__).resolve().parent.parent


class RepositoryContentTests(unittest.TestCase):
    def test_previously_skipped_text_files_reject_working_session_prose(self):
        names = ('.gitattributes', '.gitignore', 'LICENSE', 'deploy/Caddyfile',
                 'deploy/caddy-traze.conf', 'deploy/host.env.example',
                 'deploy/traze-collector.service', 'deploy/traze-monitor.service',
                 'deploy/traze-monitor.timer', 'deploy/traze-reader.service',
                 'frontend/.gitignore', 'frontend/src/assets/bitcoin-mark.svg',
                 'frontend/src/assets/hemi-mark.svg')
        raw = ('owner ' + 'approved').encode()
        for name in names:
            with self.subTest(file=name):
                text, problems = inspect_text(name, raw)
                self.assertEqual(text, raw.decode())
                self.assertIn({'file': name, 'kind': 'working_session_narrative'}, problems)

    def test_credentials_and_machine_paths_are_rejected_without_echoing_values(self):
        token = 'gh' + 'p_' + 'x' * 36
        personal = '/' + 'Users/' + 'example/' + 'project'
        for raw, kind in [(token, 'github_token'), (personal, 'machine_specific_user_path')]:
            with self.subTest(kind=kind):
                _, problems = inspect_text('deploy/host.env.example', raw.encode())
                self.assertEqual(problems, [{'file': 'deploy/host.env.example', 'kind': kind}])
                self.assertNotIn(raw, json.dumps(problems))

    def test_unknown_utf8_extensions_and_text_disguised_as_binary_are_scanned(self):
        marker = ('closed ' + 'experiment').encode()
        for name in ('settings.custom', 'fixtures/example.bin'):
            with self.subTest(file=name):
                _, problems = inspect_text(name, marker)
                self.assertEqual(problems[0]['kind'], 'working_session_narrative')

    def test_invalid_configuration_encoding_fails_but_known_binary_is_allowed(self):
        for raw in (b'\xff\xfe', b'key=value\0hidden'):
            with self.subTest(raw=raw):
                text, problems = inspect_text('deploy/host.env.example', raw)
                self.assertIsNone(text)
                self.assertEqual(problems[0]['kind'], 'invalid_text_encoding')
        self.assertEqual(inspect_text('tests/fixtures/body.bin', b'\x00\xff'), (None, []))

    def test_legitimate_notices_ownership_keys_and_stable_model_identifiers_pass(self):
        valid = ('Permission is hereby granted. The runner-lock owner releases capacity.\n'
                 '// F2 keyboard key\n<h2>Reference model</h2>\n'
                 'ama-signed-nine-block-model transcript_described_model')
        self.assertEqual(inspect_text('frontend/src/keys.ts', valid.encode()), (valid, []))

    def test_internal_stage_comment_and_filename_are_rejected(self):
        text = ('"' * 3 + 'F' + '2 integration using retained data.' + '"' * 3).encode()
        _, problems = inspect_text('tests/test_reference.py', text)
        self.assertEqual(problems[0]['kind'], 'working_session_narrative')
        name = 'demo_results/' + 'phase' + '1_sample.json'
        _, problems = inspect_text(name, b'{}')
        self.assertEqual(problems[0]['kind'], 'stage_specific_filename')

    def test_example_bookkeeping_is_rejected_while_evidence_fields_pass(self):
        for value in ({'milestone_usage': {}}, {'resources': {'run_id': 'local-run'}},
                      {'resources': {'response_bytes_session_with_prior_validation': 10}},
                      {'validation': {'milestone': 3}}):
            with self.subTest(value=value):
                _, problems = inspect_text('demo_results/example.json', json.dumps(value).encode())
                self.assertEqual(problems[0]['kind'], 'development_example_bookkeeping')
        valid = {'proofs': [], 'context': {'checked_at': 'historical'},
                 'resources': {'response_bytes_this_run': 10},
                 'validation': {'qualification': 'Retained body; not cold discovery'}}
        self.assertEqual(inspect_text('demo_results/example.json', json.dumps(valid).encode())[1], [])

    def test_complete_check_includes_deployment_text_and_returns_structured_failure(self):
        base = (ROOT / 'output/qa-repository-content').resolve()
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            root = Path(directory)
            self.assertTrue(root.resolve().is_relative_to(base))
            contents = {'LICENSE': 'Fixture license',
                        'frontend/public/THIRD_PARTY_LICENSES.txt': 'Fixture license',
                        'web/THIRD_PARTY_LICENSES.txt': 'Fixture license',
                        'deploy/host.env.example': 'owner ' + 'approved'}
            for name, text in contents.items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding='utf-8')
            corpus = root / 'tests/fixtures/bitcoin_blocks/manifest.json'
            corpus.parent.mkdir(parents=True)
            corpus.write_text(json.dumps({'bodies': [], 'receipts': [], 'body_count': 0, 'total_body_bytes': 0}))
            manifest = {'schema': 'traze-repository-selection-v1', 'include': ['**/*'], 'exclude': [], 'required': []}
            (root / 'repository-files.json').write_text(json.dumps(manifest))
            with patch('scripts.repository.check_startup', return_value={'status': 'passed', 'network_requests': 0}):
                report, _ = check(root)
            self.assertEqual(report['status'], 'failed')
            self.assertIn({'file': 'deploy/host.env.example', 'kind': 'working_session_narrative'}, report['problems'])
            self.assertEqual(report['text_files_checked'], 6)


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
