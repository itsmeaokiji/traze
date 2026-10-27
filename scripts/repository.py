"""Review/copy the selected local repository files; no chain or package requests."""
import argparse
import fnmatch
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
BLOCKED = {".hemi_lookup_data", ".git", "node_modules", "__pycache__", "output", ".codex", ".agents", ".aws"}
BINARY = {".bin", ".woff2", ".png", ".jpg", ".jpeg"}
CREDENTIALS = (
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{24,}")),
    ("github_fine_grained_token", re.compile(r"github_pat_[A-Za-z0-9_]{50,}")),
    ("openai_key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{32,}")),
    ("aws_access_key", re.compile(r"AKIA[A-Z0-9]{16}")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
)
USER_PATH = re.compile(r"(?:[A-Za-z]:[\\/]+Users[\\/]+[A-Za-z0-9_][A-Za-z0-9_ .-]*[\\/]|/Users/[A-Za-z0-9_][A-Za-z0-9_.-]*/)")
SESSION_TEXT = re.compile(r"\b(?:owner (?:approved|selected)|agent records (?:its|the) review|already-approved aggregate|authorize a new (?:live|validation) scope|current resume point|closed allowances|visual thesis|closed experiment|supplied AMA answer|Hemi PoP Q&A transcript, section)\b", re.IGNORECASE)
STAGE_COMMENT = re.compile(r'''(?im)^\s*(?://|\#|"{3}|'{3}).*\b(?:F\d+|H\d+)\s+(?:integration|worker result)\b''')
STAGE_FILE = re.compile(r"^(?:milestone\d+_|phase\d+_|test_h\d+_)", re.IGNORECASE)


def inspect_text(relative, raw):
    """Scan every UTF-8 file, regardless of its extension; never echo its data."""
    problems = []
    try:
        text = raw.decode('utf-8-sig')
        if '\0' in text:
            raise ValueError('NUL is not permitted in selected text')
    except (UnicodeDecodeError, ValueError):
        if PurePosixPath(relative).suffix.lower() not in BINARY:
            problems.append({'file': relative, 'kind': 'invalid_text_encoding'})
        return None, problems
    for name, pattern in CREDENTIALS:
        if pattern.search(text):
            problems.append({'file': relative, 'kind': name})
    if USER_PATH.search(text):
        problems.append({'file': relative, 'kind': 'machine_specific_user_path'})
    if relative != 'scripts/repository.py' and (SESSION_TEXT.search(text) or STAGE_COMMENT.search(text)):
        problems.append({'file': relative, 'kind': 'working_session_narrative'})
    if STAGE_FILE.search(PurePosixPath(relative).name):
        problems.append({'file': relative, 'kind': 'stage_specific_filename'})
    if relative.endswith('.json') and relative.startswith(('demo_results/', 'tests/fixtures/')):
        try:
            value = json.loads(text)
        except ValueError:
            problems.append({'file': relative, 'kind': 'invalid_json_example'})
        else:
            def bookkeeping(item):
                if isinstance(item, dict):
                    if 'milestone_usage' in item:
                        return True
                    resources = item.get('resources')
                    if isinstance(resources, dict) and resources.keys() & {'run_id', 'response_bytes_session_with_prior_validation'}:
                        return True
                    validation = item.get('validation')
                    if isinstance(validation, dict) and validation.keys() & {'milestone', 'new_bitcoin_bodies_permitted'}:
                        return True
                    return any(bookkeeping(child) for child in item.values())
                return isinstance(item, list) and any(bookkeeping(child) for child in item)
            if bookkeeping(value):
                problems.append({'file': relative, 'kind': 'development_example_bookkeeping'})
    return text, problems


def safe_file(root, relative):
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or ":" in relative or any(part in BLOCKED for part in path.parts):
        raise ValueError("Unsafe selected path: " + relative)
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("Selected path escapes repository: " + relative)
    return resolved


def selected(root):
    manifest = json.loads((root / "repository-files.json").read_text(encoding="utf-8"))
    if manifest["schema"] != "traze-repository-selection-v1":
        raise ValueError("Unsupported repository manifest")
    files = set()
    for pattern in manifest["include"]:
        if PurePosixPath(pattern).is_absolute() or ".." in PurePosixPath(pattern).parts or ":" in pattern:
            raise ValueError("Unsafe inclusion pattern")
        for path in root.glob(pattern):
            if path.is_file():
                relative = path.relative_to(root).as_posix()
                if any(fnmatch.fnmatchcase(relative, item) for item in manifest["exclude"]):
                    continue
                safe_file(root, relative)
                files.add(relative)
    missing = [item for item in manifest["required"] if item not in files]
    if missing:
        raise ValueError("Required files missing: " + ", ".join(missing))
    return sorted(files)


def check_startup(root, files):
    """Do not let an unselected workspace module mask a broken release."""
    try:
        child = subprocess.run([sys.executable, '-I', '-B', str(Path(__file__).with_name('check_startup.py')), str(root)],
                               input=json.dumps(files), capture_output=True, text=True, timeout=20,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        result = json.loads(child.stdout)
        if result.get('status') == 'passed' and child.returncode == 0:
            return result
        return {**result, 'status': 'failed'}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {'status': 'failed', 'error': 'The isolated startup check could not finish.', 'network_requests': 0}


def check(root):
    files = selected(root)
    problems, links, total, text_files, binary_files = [], 0, 0, 0, 0
    for relative in files:
        path = safe_file(root, relative)
        raw = path.read_bytes()
        total += len(raw)
        if path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
            expected = b"\x89PNG\r\n\x1a\n" if path.suffix.lower() == ".png" else b"\xff\xd8\xff"
            if not raw.startswith(expected):
                problems.append({"file": relative, "kind": "image_extension_mismatch"})
        if relative.startswith("HEMI_") or relative in {"tests/test_milestone2.py", "tests/test_source_feasibility.py"}:
            problems.append({"file": relative, "kind": "experiment_only_material"})
        text, findings = inspect_text(relative, raw)
        problems.extend(findings)
        if text is None:
            binary_files += 1
            continue
        text_files += 1
        if path.suffix == ".md":
            for match in re.finditer(r"\[[^\]\n]*\]\(([^)\n]+)\)", text):
                target = match.group(1).strip()
                if target.startswith("#") or re.match(r"^(?:https?://|mailto:|data:)", target):
                    continue
                local = target.split("#", 1)[0].strip("<>")
                if not local:
                    continue
                resolved = (path.parent / local).resolve()
                links += 1
                if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
                    problems.append({"file": relative, "kind": "missing_local_link", "target": target})
                elif resolved.relative_to(root).as_posix() not in files:
                    problems.append({"file": relative, "kind": "unselected_link_target", "target": target})
    corpus_path = root / "tests/fixtures/bitcoin_blocks/manifest.json"
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    body_bytes = 0
    for body in corpus["bodies"]:
        if not re.fullmatch(r"[0-9a-f]{64}\.bin", body["file"]):
            raise ValueError("Unsafe fixture filename")
        path = safe_file(root, "tests/fixtures/bitcoin_blocks/" + body["file"])
        raw = path.read_bytes()
        body_bytes += len(raw)
        if len(raw) != body["bytes"] or hashlib.sha256(raw).hexdigest() != body["sha256"]:
            problems.append({"file": body["file"], "kind": "fixture_bytes_changed"})
    for receipt in corpus["receipts"]:
        raw = safe_file(root, receipt["path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != receipt["sha256"]:
            problems.append({"file": receipt["path"], "kind": "fixture_receipt_bytes_changed"})
    if len(corpus["bodies"]) != corpus["body_count"] or body_bytes != corpus["total_body_bytes"]:
        problems.append({"file": str(corpus_path.relative_to(root)), "kind": "fixture_manifest_totals"})
    distribution = (root / "frontend/public/THIRD_PARTY_LICENSES.txt").read_bytes()
    distribution_text = distribution.decode("utf-8").replace("\r\n", "\n")
    if (root / "LICENSE").read_text(encoding="utf-8").strip() not in distribution_text:
        problems.append({"file": "LICENSE", "kind": "missing_project_distribution_notice"})
    for notice in (root / "frontend/licenses").glob("*.txt"):
        if notice.read_text(encoding="utf-8").strip() not in distribution_text:
            problems.append({"file": notice.relative_to(root).as_posix(), "kind": "missing_distribution_notice"})
    if (root / "web/THIRD_PARTY_LICENSES.txt").read_bytes() != distribution:
        problems.append({"file": "web/THIRD_PARTY_LICENSES.txt", "kind": "build_notice_mismatch"})
    startup = check_startup(root, files)
    if startup['status'] != 'passed':
        problems.append({'file': 'repository-files.json', 'kind': 'runtime_dependency_missing',
                         'detail': startup.get('error', 'Runtime import failed')})
    return {"status": "passed" if not problems else "failed", "selected_files": len(files),
            "selected_bytes": total, "local_links_checked": links,
            "text_files_checked": text_files, "binary_files_checked": binary_files,
            "fixture_bodies": len(corpus["bodies"]), "fixture_bytes": body_bytes,
            "startup": startup, "problems": problems, "chain_requests": 0}, files


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check or copy the local repository candidate")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    copy = sub.add_parser("copy")
    copy.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args(argv)
    report, files = check(ROOT)
    if report["status"] != "passed":
        print(json.dumps(report, indent=2))
        return 1
    if args.command == "copy":
        destination = args.destination.resolve()
        if destination.exists() or ROOT.is_relative_to(destination):
            raise ValueError("Choose a new destination that is not the repository or its ancestor")
        destination.mkdir(parents=True)
        for relative in files:
            source = safe_file(ROOT, relative)
            target = (destination / relative).resolve()
            if not target.is_relative_to(destination):
                raise ValueError("Copy target escapes the candidate")
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as handle:
                handle.write(source.read_bytes())
        report.update(destination=str(destination), copied_files=len(files))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        raise SystemExit(1)
