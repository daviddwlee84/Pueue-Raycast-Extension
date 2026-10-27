"""V2 evidence/review regressions, entirely synthetic and network-free.

The fake runner below is a local process, not an agent launch. The native test
uses only isolated synthetic JSONL and SpecStory's explicit no-cloud sync/print.
"""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"
HELPER = SCRIPTS / "_post_session.py"
SESSION = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
SECRET_A = "synthetic-review-value-A"
SECRET_B = "synthetic-review-value-B"


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()


def revision(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def private_write(path, data):
    path.write_bytes(data)
    path.chmod(0o600)


def load_helper():
    spec = importlib.util.spec_from_file_location("test_post_session_core", HELPER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run(command, root, environment, check=True):
    process = subprocess.run([str(x) for x in command], cwd=root, env=environment,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    if check:
        assert process.returncode == 0, (process.returncode, process.stdout, process.stderr)
    return process


@pytest.fixture
def lifecycle(tmp_path, monkeypatch):
    root = (tmp_path / "repo").resolve()
    root.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    binary = tmp_path / "bin"
    binary.mkdir()
    environment = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / "config"),
                       XDG_CACHE_HOME=str(home / "cache"), GIT_CONFIG_GLOBAL="/dev/null",
                       GIT_CONFIG_SYSTEM="/dev/null", PYTHONDONTWRITEBYTECODE="1",
                       PATH=str(binary) + os.pathsep + str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
                       TEST_SCRIPTS=str(SCRIPTS), TEST_SESSION=SESSION,
                       TEST_EVENTS=str(tmp_path / "events"), TEST_REQUEST=str(tmp_path / "request"),
                       TEST_SECRET="", TEST_MODE="normal")
    for key in list(environment):
        if key.startswith("AGENT_HISTORY_") or key in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE"):
            environment.pop(key, None)
    git = lambda *args: run(["git", *args], root, environment).stdout
    git("init", "-q", "-b", "main")
    git("config", "user.name", "Synthetic Test")
    git("config", "user.email", "synthetic@example.invalid")
    git("config", "core.hooksPath", str(root / ".git" / "hooks"))
    (root / "product.txt").write_text("base\n")
    git("add", "product.txt")
    git("-c", "core.hooksPath=/dev/null", "commit", "-qm", "initial")
    (root / ".git" / "COMMIT_EDITMSG").unlink()
    (root / "product.txt").write_text("base\nfeature\n")
    git("add", "product.txt")
    (root / "message.txt").write_text("feat: preserve exact synthetic session\n\nKeep the feature and its exact synthetic review evidence together.\n")
    scanner = binary / "gitleaks"
    scanner.write_text('''#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
report = Path(args[args.index("--report-path") + 1])
findings = []
values = ["synthetic-review-value-A", "synthetic-review-value-B"]
if args[0] == "stdin":
    blobs = [("stdin", sys.stdin.buffer.read())]
else:
    paths = subprocess.check_output(["git", "diff", "--cached", "--name-only", "-z"]).split(b"\\0")
    blobs = []
    for path in paths:
        if path:
            result = subprocess.run(["git", "show", ":" + path.decode()], capture_output=True)
            if result.returncode == 0:
                blobs.append((path.decode(), result.stdout))
for path, data in blobs:
    for value in values:
        if value.encode() in data:
            findings.append({"File": path, "Secret": value, "Match": value,
                             "RuleID": os.environ.get("TEST_RULE_ID", "fixture-rule"), "StartLine": 1})
report.write_text(json.dumps(findings))
''')
    scanner.chmod(0o755)
    (binary / "lsof").write_text("#!/bin/sh\nexit 1\n")
    (binary / "lsof").chmod(0o755)
    native = binary / "specstory"
    native.write_text('''#!/usr/bin/env python3
import json, os, re, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
if "--help" in args:
    print("--print --silent --no-cloud-sync --no-stats --no-usage-analytics --no-version-check")
    sys.exit(0)
root = Path.cwd()
session = os.environ["TEST_SESSION"]
scripts = Path(os.environ["TEST_SCRIPTS"])
with open(os.environ["TEST_EVENTS"], "a") as stream:
    stream.write(json.dumps(args) + "\\n")
alias = root / ".specstory/history/session.md"
slug = re.sub("[^A-Za-z0-9]", "-", str(root))
source = Path.home() / ".claude/projects" / slug / (session + ".jsonl")
rendered = ("<!-- Generated by SpecStory, Markdown v2.1.0 -->\\n\\n# Synthetic session\\n\\n"
            "<!-- Claude Code Session " + session + " (2026-09-01 00:00:00Z) -->\\n\\n"
            "_**Agent (Claude Opus 5 2026-09-01 00:00:01Z)**_\\n" + os.environ.get("TEST_SECRET", "") + "\\n")
if args[0] == "run":
    alias.parent.mkdir(parents=True)
    alias.write_text(rendered)
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps({"type": "user", "sessionId": session, "cwd": str(root)}) + "\\n")
    Path(os.environ["TEST_REQUEST"]).write_text(os.environ["AGENT_HISTORY_REQUEST_PATH"])
    command = [sys.executable, "-I", "-B", str(scripts / "_post_session.py")]
    inspection = subprocess.run(command + ["inspect", "--context", "--json"], capture_output=True)
    if inspection.returncode or not json.loads(inspection.stdout)["authentic_context"]:
        sys.stdout.buffer.write(inspection.stdout); sys.stderr.buffer.write(inspection.stderr)
        sys.exit(79)
    queue = command + ["queue", "--script-dir", str(scripts), "--session-id", session,
                       "--specstory-path", ".specstory/history/session.md", "--no-plan", "--message-file", "message.txt"]
    if os.environ.get("TEST_NATIVE_DEV"):
        queue = [os.environ["TEST_NATIVE_DEV"], "--no-runtime", "prepare", str(root),
                 "--closeout", "co-commit", "--session", "claude:" + session,
                 "--specstory-path", ".specstory/history/session.md", "--no-plan",
                 "--message-file", "message.txt", "--closeout-helper", str(scripts), "--json"]
    first = subprocess.run(queue, capture_output=True)
    if os.environ.get("TEST_NATIVE_RESULT"):
        Path(os.environ["TEST_NATIVE_RESULT"]).write_bytes(first.stdout)
    sys.stdout.buffer.write(first.stdout); sys.stdout.buffer.flush()
    sys.stderr.buffer.write(first.stderr); sys.stderr.buffer.flush()
    if first.returncode:
        sys.exit(first.returncode)
    if os.environ["TEST_MODE"] != "old-session":
        output = first.stdout.decode()
        if os.environ.get("TEST_ACK_WRAPPER") == "native":
            output = json.dumps({"kind": "co_commit_handoff", "schema_version": 1,
                                 "status": "queued_but_not_bound", "queue_ack": json.loads(output)["queue_ack"]})
        tool_use = {"type": "assistant", "sessionId": session, "cwd": str(root),
                    "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu-synthetic-queue",
                                 "name": "Bash", "input": {"command": "synthetic queue"}}]}}
        tool_result = {"type": "user", "sessionId": session, "cwd": str(root),
                       "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu-synthetic-queue",
                                    "content": output, "is_error": os.environ.get("TEST_NATIVE_TOOL_ERROR") == "1"}]}}
        with source.open("a") as stream:
            stream.write(json.dumps(tool_use) + "\\n" + json.dumps(tool_result) + "\\n")
    if os.environ["TEST_MODE"] == "idempotent":
        sys.exit(subprocess.run(queue).returncode)
    if os.environ["TEST_MODE"] == "nonzero":
        sys.exit(37)
    sys.exit(0)
if args[0] == "sync":
    if "--print" in args:
        if os.environ["TEST_MODE"] == "source-during-export":
            source.write_text(source.read_text() + "{}\\n")
        if os.environ["TEST_MODE"] == "bad-export":
            print("unsupported export output")
        else:
            sys.stdout.write(rendered)
    elif os.environ["TEST_MODE"] == "stale-alias":
        (alias.parent / "other-alias.md").write_text(rendered)
        alias.write_text(rendered + "stale bytes\\n")
    elif os.environ["TEST_MODE"] == "source-during-sync":
        source.write_text(source.read_text() + "{}\\n")
    else:
        alias.write_text(rendered)
    sys.exit(0)
sys.exit(64)
''')
    native.chmod(0o755)

    class Context:
        env = environment
        repo = root
        bin = binary

        def git(self, *args):
            return git(*args)

        def start(self, *, allow=False, mode="normal", secret="", cloud=False, resume=None):
            self.env.update(TEST_MODE=mode, TEST_SECRET=secret)
            command = ["/bin/bash", SCRIPTS / "run-specstory-session.sh"]
            if allow:
                command.append("--allow-commit")
            if cloud:
                command.append("--cloud-sync")
            command.append("claude")
            if resume:
                command.extend(["--", "--resume", resume])
            result = run(command, root, self.env, check=False)
            self.request = Path(Path(self.env["TEST_REQUEST"]).read_text())
            self.run_dir = self.request.parent
            return result

        def call(self, subcommand, *args):
            return run([sys.executable, "-I", "-B", HELPER, subcommand,
                        "--script-dir", SCRIPTS, *args], root, self.env, check=False)

        def finalize(self, *args):
            return self.call("finalize", "--request", self.request, *args)

        def journal(self):
            return json.loads((self.run_dir / "journal.json").read_bytes())

        def preview(self):
            return self.finalize("--preview-review", "--json")

        def decisions(self, dispositions):
            preview = self.preview()
            assert preview.returncode == 0, (preview.stdout, preview.stderr)
            value = json.loads(preview.stdout)
            decisions = [{"finding_id": finding["finding_id"], "disposition": dispositions[i % len(dispositions)],
                          "reason": "Synthetic fixture generated solely for this isolated regression."}
                         for i, finding in enumerate(value["findings"])]
            review = {"schema_version": 2, "request_revision": value["request_revision"],
                      "receipt_revision": value["receipt_revision"], "prepared_tree": value["prepared_tree"],
                      "decisions": decisions}
            path = self.run_dir / "review.json"
            private_write(path, canonical(review))
            return path

    return Context()


def test_capabilities_readonly_and_isolated_import(tmp_path):
    before = list(tmp_path.iterdir())
    result = run([sys.executable, "-I", "-B", HELPER, "capabilities", "--json"], tmp_path, os.environ)
    value = json.loads(result.stdout)
    assert value["protocol_version"] == 2
    assert "review_v2" in value["capabilities"]
    assert list(tmp_path.iterdir()) == before


def test_clean_v2_commits_once_and_inspects_historical_object(lifecycle):
    c = lifecycle
    result = c.start(allow=True, mode="idempotent")
    assert result.returncode == 0, (result.stdout, result.stderr)
    journal = c.journal()
    assert journal["state"] == "done"
    assert journal["v2"]["review_status"] == "not_required"
    events = [json.loads(line) for line in Path(c.env["TEST_EVENTS"]).read_text().splitlines()]
    assert len(events) == 3
    assert all("--no-cloud-sync" in row for row in events)
    assert "--print" in events[-1] and "--no-stats" in events[-1]
    assert c.finalize("--allow-commit").returncode == 0
    assert c.git("rev-list", "--count", "HEAD").strip() == b"2"
    c.git("-c", "core.hooksPath=/dev/null", "commit", "--allow-empty", "-qm", "later")
    result = c.call("inspect", "--request", c.request, "--json")
    value = json.loads(result.stdout)
    assert value["commit_proven"] is True
    assert value["composed_message_sha256"] and value["normalized_message_sha256"]


def test_complete_private_receipt_preview_and_fixture_review(lifecycle):
    c = lifecycle
    result = c.start(allow=True, secret=SECRET_A + " repeated " + SECRET_A)
    assert result.returncode == 10, (result.stdout, result.stderr)
    assert SECRET_A.encode() not in result.stdout + result.stderr
    receipt_path = c.run_dir / "sanitation.json"
    receipt = json.loads(receipt_path.read_bytes())
    artifact = receipt["artifacts"][0]
    assert SECRET_A.encode() in base64.b64decode(artifact["source_before"])
    assert artifact["pre_oid"] != artifact["post_oid"]
    assert len(artifact["findings"][0]["occurrences"]) == 2
    assert any(row["path"] == "product.txt" for row in receipt["coverage"])
    assert receipt_path.stat().st_mode & 0o777 == 0o600
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in c.run_dir.iterdir() if path.is_file()}
    index_before = (c.repo / ".git/index").read_bytes()
    result = c.preview()
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert SECRET_A.encode() not in result.stdout + result.stderr
    assert before == {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in c.run_dir.iterdir() if path.is_file()}
    assert index_before == (c.repo / ".git/index").read_bytes()
    review = c.decisions(["reviewed_noncredential"])
    assert c.finalize("--review-file", review).returncode == 5
    result = c.finalize("--allow-commit", "--review-file", review)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert SECRET_A.encode() not in c.git("show", "HEAD:.specstory/history/session.md")
    assert c.journal()["v2"]["review_status"] == "reviewed_noncredential"


@pytest.mark.parametrize("dispositions,rotation,expected", [
    (["reviewed_noncredential", "unresolved"], False, "unresolved_review"),
    (["reviewed_noncredential", "credential_rotation_required"], False, "rotation_confirmation_required"),
    (["reviewed_noncredential", "credential_rotation_required"], True, "committed"),
])
def test_mixed_reviews(lifecycle, dispositions, rotation, expected):
    c = lifecycle
    assert c.start(allow=True, secret=SECRET_A + " " + SECRET_B).returncode == 10
    review = c.decisions(dispositions)
    args = ["--allow-commit", "--review-file", review]
    if rotation:
        args.append("--rotation-confirmed")
    result = c.finalize(*args)
    assert json.loads(result.stdout)["status"] == expected, (result.stdout, result.stderr)
    if expected != "committed":
        assert c.git("rev-list", "--count", "HEAD").strip() == b"1"


@pytest.mark.parametrize("mode", ["stale-alias", "source-during-sync", "source-during-export", "bad-export"])
def test_exact_export_rejects_stale_or_unsupported_without_sanitation(lifecycle, mode):
    c = lifecycle
    c.git("write-tree")  # Queue may legitimately populate the Git tree cache.
    initial_index = (c.repo / ".git/index").read_bytes()
    result = c.start(allow=True, mode=mode, secret=SECRET_A)
    assert result.returncode != 0
    assert c.journal()["sync_succeeded"] is False
    assert not (c.run_dir / "sanitation.json").exists()
    assert initial_index == (c.repo / ".git/index").read_bytes()
    assert SECRET_A in (c.repo / ".specstory/history/session.md").read_text()
    if mode == "stale-alias":
        assert (c.repo / ".specstory/history/other-alias.md").exists()


def test_explicit_cloud_choice_never_cloud_exports(lifecycle):
    c = lifecycle
    result = c.start(cloud=True)
    assert result.returncode == 0, (result.stdout, result.stderr)
    events = [json.loads(line) for line in Path(c.env["TEST_EVENTS"]).read_text().splitlines()]
    assert "--no-cloud-sync" not in events[0]
    assert "--no-cloud-sync" not in events[1]
    assert "--no-cloud-sync" in events[2]
    assert c.journal()["v2"]["cloud_sync"] is True


def test_product_finding_blocks_all_publication(lifecycle):
    c = lifecycle
    (c.repo / "product.txt").write_text(SECRET_B)
    c.git("add", "product.txt")
    c.git("write-tree")
    initial = (c.repo / ".git/index").read_bytes()
    result = c.start(allow=True, secret=SECRET_A)
    assert result.returncode == 7, (result.stdout, result.stderr)
    assert (c.repo / ".git/index").read_bytes() == initial
    assert SECRET_A in (c.repo / ".specstory/history/session.md").read_text()
    assert not (c.run_dir / "sanitation.json").exists()


@pytest.mark.parametrize("mutation", ["index", "source", "receipt", "review", "policy", "tool"])
def test_stale_replay_cannot_release_rotation(lifecycle, mutation):
    c = lifecycle
    assert c.start(allow=True, secret=SECRET_A).returncode == 10
    review = c.decisions(["reviewed_noncredential"])
    if mutation == "index":
        (c.repo / "product.txt").write_text("different\n")
        c.git("add", "product.txt")
    elif mutation == "source":
        path = c.repo / ".specstory/history/session.md"
        path.write_bytes(path.read_bytes() + b"changed\n")
    elif mutation == "receipt":
        path = c.run_dir / "sanitation.json"
        value = json.loads(path.read_bytes())
        value["artifacts"][0]["steps"] = []
        private_write(path, canonical(value))
    elif mutation == "review":
        value = json.loads(review.read_bytes())
        value["receipt_revision"] = "0" * 64
        private_write(review, canonical(value))
    elif mutation == "policy":
        path = c.run_dir / "gitleaks.toml"
        path.write_bytes(path.read_bytes() + b"\n# changed\n")
    else:
        path = c.bin / "gitleaks"
        path.write_text(path.read_text() + "\n# tool changed\n")
    result = c.finalize("--allow-commit", "--review-file", review)
    assert result.returncode != 0, result.stdout
    assert c.git("rev-list", "--count", "HEAD").strip() == b"1"


def test_v1_parser_refuses_added_v2_authority(lifecycle):
    p = load_helper()
    root = str(lifecycle.repo)
    run_id, run_dir, request, journal_path, token = p.initialize_run(root, str(lifecycle.repo / ".git"))
    value, _raw = p.read_canonical_json(journal_path, "journal", p.validate_journal)
    assert value["schema_version"] == 1
    value["v2"] = {}
    with pytest.raises(p.LifecycleError):
        p.validate_journal(value)


def test_forged_environment_is_not_authentic_running_context(lifecycle):
    c = lifecycle
    assert c.start(mode="nonzero").returncode == 37
    c.env["AGENT_HISTORY_REQUEST_PATH"] = str(c.request)
    c.env["AGENT_HISTORY_RUN_ID"] = c.run_dir.name
    result = c.call("inspect", "--context", "--json")
    assert result.returncode == 5
    assert json.loads(result.stdout)["status"] == "requires_wrapper"


def test_native_specstory_exact_print_is_readonly_and_byte_identical(tmp_path):
    native = shutil.which("specstory")
    if not native:
        pytest.skip("native SpecStory is not installed; native compatibility NOT verified")
    root = (tmp_path / "synthetic-project").resolve()
    root.mkdir()
    home = (tmp_path / "isolated-home").resolve()
    home.mkdir()
    environment = {"PATH": os.environ["PATH"], "HOME": str(home), "TMPDIR": str(tmp_path),
                   "XDG_CONFIG_HOME": str(home / "config"), "XDG_CACHE_HOME": str(home / "cache"),
                   "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
    # Keep telemetry off explicitly without inheriting any real provider state.
    environment.update(OTEL_SDK_DISABLED="true", DO_NOT_TRACK="1")
    p = load_helper()
    probe_path = SCRIPTS / "probe-specstory-redaction.py"
    spec = importlib.util.spec_from_file_location("synthetic_specstory_probe", probe_path)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    old_home = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    try:
        source, session = probe.build_session(root, [])
    finally:
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home
    # Verify the actual native renderer also accepts the typed acknowledgement
    # shape used by a real CLI tool result, without launching an agent.
    request = {"request_id": SESSION, "request_path": str(tmp_path / "private-request.json"),
               "session_id": session}
    journal = {"v2": {"helper_revision": "0" * 64}}
    acknowledgement = p.v2.queue_ack(request, journal)
    prior = json.loads(source.read_text().splitlines()[-1])
    common = {"sessionId": session, "cwd": str(root), "timestamp": prior["timestamp"],
              "version": probe.CLAUDE_CODE_VERSION, "isSidechain": False, "userType": "external"}
    use = dict(common, type="assistant", uuid="cccccccc-cccc-4ccc-8ccc-cccccccccccc", parentUuid=prior["uuid"],
               message={"role": "assistant", "model": "claude-opus-4-8", "content": [
                   {"type": "tool_use", "id": "toolu-native-synthetic-queue", "name": "Bash", "input": {"command": "synthetic queue only"}}]})
    result = dict(common, type="user", uuid="dddddddd-dddd-4ddd-8ddd-dddddddddddd", parentUuid=use["uuid"],
                  message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu-native-synthetic-queue",
                            "content": json.dumps({"kind": "co_commit_handoff", "schema_version": 1, "status": "queued", "queue_ack": acknowledgement})}]})
    with source.open("a") as stream:
        stream.write(json.dumps(use) + "\n" + json.dumps(result) + "\n")
    before = source.read_bytes(), source.stat().st_mtime_ns
    assert p.v2.prove_native_ack(before[0], request, journal) == p.v2.digest(acknowledgement)
    help_result = run([native, "sync", "--help"], root, environment)
    for flag in (b"--print", b"--no-cloud-sync", b"--no-stats", b"--silent"):
        assert flag in help_result.stdout
    flags = ["--silent", "--no-cloud-sync", "--no-stats", "--no-usage-analytics", "--no-version-check"]
    run([native, "sync", "claude", "-s", session, *flags], root, environment)
    aliases = list((root / ".specstory/history").glob("*.md"))
    assert len(aliases) == 1
    filesystem = {path: (path.read_bytes(), path.stat().st_mtime_ns)
                  for parent in (root, home) for path in parent.rglob("*") if path.is_file()}
    export = run([native, "sync", "claude", "-s", session, "--print", *flags], root, environment)
    assert export.stdout == aliases[0].read_bytes()
    assert export.stdout.startswith(b"<!-- Generated by SpecStory, Markdown ")
    assert session.encode() in export.stdout
    assert b"agent_history_queue_ack" in export.stdout
    assert (source.read_bytes(), source.stat().st_mtime_ns) == before
    assert filesystem == {path: (path.read_bytes(), path.stat().st_mtime_ns)
                          for parent in (root, home) for path in parent.rglob("*") if path.is_file()}
    assert not (root / ".specstory/statistics.json").exists()


def test_prepare_only_expected_revision_and_reconcile_boundaries(lifecycle):
    c = lifecycle
    assert c.start().returncode == 0
    inspection = json.loads(c.call("inspect", "--request", c.request, "--json").stdout)
    assert inspection["automatic_commit"] is False
    original = (c.repo / ".git/index").read_bytes()
    refused = c.finalize("--allow-commit", "--prepare-only", "--expected-revision", "0" * 64)
    assert json.loads(refused.stdout)["status"] == "stale_revision"
    assert original == (c.repo / ".git/index").read_bytes()
    prepared = c.finalize("--allow-commit", "--prepare-only", "--expected-revision", inspection["journal_revision"])
    assert json.loads(prepared.stdout)["status"] == "prepared", (prepared.stdout, prepared.stderr)
    assert c.git("rev-list", "--count", "HEAD").strip() == b"1"
    refused = c.finalize("--allow-commit", "--reconcile-only")
    assert json.loads(refused.stdout)["status"] == "reconciliation_required"
    assert c.finalize("--allow-commit", "--expected-revision", inspection["journal_revision"]).returncode == 6
    inspection = json.loads(c.call("inspect", "--request", c.request, "--json").stdout)
    committed = c.finalize("--allow-commit", "--expected-revision", inspection["journal_revision"])
    assert committed.returncode == 0, (committed.stdout, committed.stderr)
    assert c.finalize("--allow-commit", "--reconcile-only").returncode == 0
    assert c.git("rev-list", "--count", "HEAD").strip() == b"2"


def test_review_prepare_then_same_exact_review_is_idempotent(lifecycle):
    c = lifecycle
    assert c.start(allow=True, secret=SECRET_A).returncode == 10
    # A blanket rotation flag cannot replace complete per-finding accounting.
    refused = c.finalize("--allow-commit", "--rotation-confirmed")
    assert json.loads(refused.stdout)["status"] == "unresolved_review"
    review = c.decisions(["reviewed_noncredential"])
    prepared = c.finalize("--allow-commit", "--review-file", review, "--prepare-only")
    assert prepared.returncode == 0, (prepared.stdout, prepared.stderr)
    assert c.git("rev-list", "--count", "HEAD").strip() == b"1"
    result = c.finalize("--allow-commit", "--review-file", review)
    assert result.returncode == 0, (result.stdout, result.stderr)


def test_done_inspect_reproves_saved_review(lifecycle):
    c = lifecycle
    assert c.start(allow=True, secret=SECRET_A).returncode == 10
    review = c.decisions(["reviewed_noncredential"])
    assert c.finalize("--allow-commit", "--review-file", review).returncode == 0
    saved = c.run_dir / ("review-" + c.journal()["v2"]["review_revision"] + ".json")
    saved.unlink()
    value = json.loads(c.call("inspect", "--request", c.request, "--json").stdout)
    assert value["commit_proven"] is False and value["review_status"] == "stale"
    assert c.finalize("--allow-commit", "--reconcile-only").returncode != 0
    assert c.git("rev-list", "--count", "HEAD").strip() == b"2"


def test_journal_only_native_source_rebind_is_rejected(lifecycle):
    c = lifecycle
    assert c.start(allow=True, secret=SECRET_A).returncode == 10
    review = c.decisions(["reviewed_noncredential"])
    journal = c.journal()
    source = Path(journal["v2"]["freshness"]["source"]["path"])
    with source.open("a") as stream:
        stream.write(json.dumps({"type": "user", "sessionId": SESSION, "cwd": str(c.repo),
                                "message": {"role": "user", "content": "late turn"}}) + "\n")
    p = load_helper()
    journal["v2"]["freshness"]["source"] = p.v2.read_generation(str(source))[1]
    private_write(c.run_dir / "journal.json", canonical(journal))
    result = c.finalize("--allow-commit", "--review-file", review)
    assert result.returncode != 0
    assert c.git("rev-list", "--count", "HEAD").strip() == b"1"


def test_historical_commit_parent_cannot_be_rebound_only_in_journal(lifecycle):
    c = lifecycle
    assert c.start(allow=True).returncode == 0
    journal = c.journal()
    message = (c.run_dir / "composed-message.txt").read_text()
    forged = c.git("commit-tree", journal["expected_commit_tree"], "-p", journal["commit_oid"], "-m", message).decode().strip()
    journal["expected_commit_parent"] = journal["commit_oid"]
    journal["commit_oid"] = forged
    private_write(c.run_dir / "journal.json", canonical(journal))
    value = json.loads(c.call("inspect", "--request", c.request, "--json").stdout)
    assert value["commit_proven"] is False


def test_policy_is_bound_before_first_full_scan(lifecycle, monkeypatch):
    c = lifecycle
    assert c.start().returncode == 0
    p = load_helper()
    for key, value in c.env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(c.repo)
    request = json.loads(c.request.read_bytes())
    journal, policy = p.ensure_trusted_gitleaks_config(str(SCRIPTS), str(c.repo), request,
                                                     str(c.run_dir / "journal.json"), c.journal())
    private_write(Path(policy), b"title = 'permissive substituted policy'\n")
    with pytest.raises(p.LifecycleError) as error:
        p.v2.scanner_proof(policy, str(c.request))
    assert error.value.code == "trusted_policy_changed"
    assert not (c.run_dir / "sanitation.json").exists()


def install_faulty_copy(c, tmp_path, monkeypatch, suffix):
    copy = tmp_path / "installed" / "agent-history-hygiene"
    shutil.copytree(SKILL, copy, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    module = copy / "scripts/_post_session_v2.py"
    content = module.read_text()
    marker = '    parent = os.path.dirname(path)\n    p.assert_private_dir(parent)\n    if os.path.lexists(path):'
    assert marker in content
    module.write_text(content.replace(marker,
        '    if path.endswith("/' + suffix + '"):\n        raise OSError("synthetic receipt storage failure")\n' + marker, 1))
    scripts = copy / "scripts"
    monkeypatch.setattr(sys.modules[__name__], "SCRIPTS", scripts)
    monkeypatch.setattr(sys.modules[__name__], "HELPER", scripts / "_post_session.py")
    c.env["TEST_SCRIPTS"] = str(scripts)


def test_receipt_write_failure_does_not_publish(lifecycle, tmp_path, monkeypatch):
    c = lifecycle
    install_faulty_copy(c, tmp_path, monkeypatch, "sanitation.json")
    c.git("write-tree")
    before = (c.repo / ".git/index").read_bytes()
    result = c.start(allow=True, secret=SECRET_A)
    assert result.returncode == 7, (result.stdout, result.stderr)
    assert before == (c.repo / ".git/index").read_bytes()
    assert SECRET_A in (c.repo / ".specstory/history/session.md").read_text()
    assert not (c.run_dir / "sanitation.json").exists()
    assert SECRET_A.encode() not in result.stdout + result.stderr


def test_publication_receipt_failure_retains_partial_effects_without_rollback_claim(lifecycle, tmp_path, monkeypatch):
    c = lifecycle
    install_faulty_copy(c, tmp_path, monkeypatch, "publication.json")
    c.git("write-tree")
    before = (c.repo / ".git/index").read_bytes()
    result = c.start(allow=True, secret=SECRET_A)
    assert result.returncode == 8, (result.stdout, result.stderr)
    assert b'"status":"publication_unproven"' in result.stdout
    assert b"rolled back" not in result.stdout + result.stderr
    assert before != (c.repo / ".git/index").read_bytes()
    assert SECRET_A not in (c.repo / ".specstory/history/session.md").read_text()
    assert (c.run_dir / "sanitation.json").exists()
    assert not (c.run_dir / "publication.json").exists()
    assert c.finalize("--allow-commit").returncode != 0
    assert c.git("rev-list", "--count", "HEAD").strip() == b"1"


@pytest.mark.parametrize("mutation", ["drop-occurrence", "drop-step", "drop-coverage", "duplicate-decision", "unknown-decision", "symlink-review"])
def test_hostile_complete_accounting_and_private_review(lifecycle, mutation):
    c = lifecycle
    assert c.start(allow=True, secret=SECRET_A + " " + SECRET_A).returncode == 10
    review = c.decisions(["reviewed_noncredential"])
    if mutation in ("drop-occurrence", "drop-step", "drop-coverage"):
        path = c.run_dir / "sanitation.json"
        receipt = json.loads(path.read_bytes())
        if mutation == "drop-occurrence":
            receipt["artifacts"][0]["findings"][0]["occurrences"].pop()
        elif mutation == "drop-step":
            receipt["artifacts"][0]["steps"].pop()
        else:
            receipt["coverage"].pop()
        private_write(path, canonical(receipt))
        # Even if someone rebinds mutable journal digests, semantic receipt
        # completeness and frozen-tree coverage must still reject the forgery.
        journal = c.journal()
        journal["v2"]["receipt_revision"] = revision(receipt)
        publication_path = c.run_dir / "publication.json"
        publication = json.loads(publication_path.read_bytes())
        publication["receipt_revision"] = revision(receipt)
        private_write(publication_path, canonical(publication))
        journal["v2"]["publication_revision"] = revision(publication)
        private_write(c.run_dir / "journal.json", canonical(journal))
    elif mutation == "symlink-review":
        target = c.run_dir / "moved-review.json"
        review.rename(target)
        review.symlink_to(target.name)
    else:
        value = json.loads(review.read_bytes())
        if mutation == "duplicate-decision":
            value["decisions"].append(dict(value["decisions"][0]))
        else:
            value["decisions"][0]["disposition"] = "allow_everything"
        private_write(review, canonical(value))
    result = c.finalize("--allow-commit", "--review-file", review)
    assert result.returncode != 0
    assert SECRET_A.encode() not in result.stdout + result.stderr
    assert c.git("rev-list", "--count", "HEAD").strip() == b"1"


def test_hostile_rule_id_never_becomes_public_review_value(lifecycle):
    c = lifecycle
    c.env["TEST_RULE_ID"] = SECRET_A
    result = c.start(allow=True, secret=SECRET_A)
    assert result.returncode == 10, (result.stdout, result.stderr)
    preview = c.preview()
    assert preview.returncode == 0
    assert SECRET_A.encode() not in result.stdout + result.stderr + preview.stdout + preview.stderr
    assert b"[REDACTED]" in preview.stdout


def test_uncertain_commit_never_auto_retries(lifecycle):
    c = lifecycle
    assert c.start().returncode == 0
    assert c.finalize("--allow-commit", "--prepare-only").returncode == 0
    journal = c.journal()
    journal["state"] = "committing"
    private_write(c.run_dir / "journal.json", canonical(journal))
    for args in (("--allow-commit",), ("--allow-commit", "--reconcile-only")):
        result = c.finalize(*args)
        assert result.returncode == 8, (result.stdout, result.stderr)
    assert c.git("rev-list", "--count", "HEAD").strip() == b"1"


def test_explicit_resume_rejects_another_session_before_queue(lifecycle):
    c = lifecycle
    result = c.start(allow=True, resume="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    assert result.returncode == 5, (result.stdout, result.stderr)
    assert b'"status":"session_mismatch"' in result.stdout
    assert not c.request.exists()
    assert not (c.run_dir / "sanitation.json").exists()


def test_nonresume_old_session_without_native_queue_ack_is_not_proof(lifecycle):
    c = lifecycle
    result = c.start(allow=True, mode="old-session")
    assert result.returncode != 0
    assert b'"status":"source_session_unproven"' in result.stdout
    assert c.journal()["sync_attempted"] is False
    assert not (c.run_dir / "sanitation.json").exists()
    assert c.git("rev-list", "--count", "HEAD").strip() == b"1"


def test_native_outer_queue_ack_is_accepted(lifecycle):
    c = lifecycle
    c.env["TEST_ACK_WRAPPER"] = "native"
    result = c.start(allow=True, resume=SESSION)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert c.journal()["v2"]["resume_session_id"] == SESSION


def test_partial_native_queue_ack_is_evidence_even_in_error_tool_result(lifecycle):
    c = lifecycle
    c.env["TEST_ACK_WRAPPER"] = "native"
    c.env["TEST_NATIVE_TOOL_ERROR"] = "1"
    result = c.start(allow=True)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert c.journal()["state"] == "done"


def test_full_index_checker_does_not_print_hostile_raw_rule_ids(lifecycle):
    c = lifecycle
    c.env["TEST_RULE_ID"] = SECRET_A
    (c.repo / "product.txt").write_text(SECRET_A)
    c.git("add", "product.txt")
    result = run([sys.executable, SKILL / "assets/redact_secrets.py", "--config",
                  SKILL / "assets/gitleaks.toml.template", "--check-index", "--full-index"],
                 c.repo, c.env, check=False)
    assert result.returncode == 1
    assert SECRET_A.encode() not in result.stdout + result.stderr


def test_public_output_bound_refuses_without_printing_raw_payload(capsys):
    p = load_helper()
    with pytest.raises(p.LifecycleError):
        p.emit({"status": "x" * p.MAX_JSON_BYTES})
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("options", [
    ["--protocol-version", "1", "--no-cloud-sync", "claude"],
    ["--protocol-version", "1", "--cloud-sync", "claude"],
    ["claude", "--", "--", "--only-cloud-sync"],
    ["claude", "--", "--resume", SESSION, "--resume=" + SESSION],
])
def test_unsupported_cloud_or_resume_options_never_start_child(lifecycle, options):
    c = lifecycle
    result = run(["/bin/bash", SCRIPTS / "run-specstory-session.sh", *options], c.repo, c.env, check=False)
    assert result.returncode != 0
    assert not Path(c.env["TEST_REQUEST"]).exists()
    assert not Path(c.env["TEST_EVENTS"]).exists()


def test_real_dev_cocommit_adapter_with_canonical_helper(lifecycle, tmp_path, monkeypatch):
    executable = os.environ.get("DEV_COCOMMIT_TEST_BINARY")
    if not executable:
        pytest.skip("set DEV_COCOMMIT_TEST_BINARY to verify the real native adapter contract")
    c = lifecycle
    copied = (tmp_path / "canonical-copy" / "agent-history-hygiene").resolve()
    shutil.copytree(SKILL, copied, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    scripts = copied / "scripts"
    monkeypatch.setattr(sys.modules[__name__], "SCRIPTS", scripts)
    monkeypatch.setattr(sys.modules[__name__], "HELPER", scripts / "_post_session.py")
    c.env["TEST_SCRIPTS"] = str(scripts)
    native = c.bin / "dev-native-test"
    shutil.copy2(executable, native)
    native.chmod(0o700)
    c.env["TEST_NATIVE_DEV"] = str(native)
    c.env["TEST_NATIVE_RESULT"] = str(tmp_path / "native-queue.json")
    c.env["XDG_STATE_HOME"] = str(tmp_path / "state")
    c.env["XDG_DATA_HOME"] = str(tmp_path / "data")
    c.env["XDG_RUNTIME_DIR"] = str(tmp_path / "runtime")
    for path in ("state", "data", "runtime"):
        (tmp_path / path).mkdir(mode=0o700)
    # No inherited agent/provider/credential environment enters the native CLI.
    keep = {"PATH", "HOME", "TMPDIR", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "PYTHONDONTWRITEBYTECODE"}
    c.env = {key: value for key, value in c.env.items()
             if key in keep or key.startswith("TEST_") or key.startswith("XDG_")}
    # Context methods close over the fixture's original environment for Git;
    # native subprocesses use this explicit isolated per-test mapping.
    hooks_log = tmp_path / "ordinary-hooks"
    hooks = c.repo / ".git/hooks"
    for name in ("pre-commit", "prepare-commit-msg", "commit-msg", "post-commit"):
        hook = hooks / name
        hook.write_text("#!/bin/sh\nprintf '%s\\n' '" + name + "' >> '" + str(hooks_log) + "'\n")
        hook.chmod(0o700)
    result = c.start(allow=False, resume=SESSION)
    assert result.returncode == 0, (result.stdout, result.stderr)
    queued = json.loads(Path(c.env["TEST_NATIVE_RESULT"]).read_bytes())
    assert queued["kind"] == "co_commit_handoff" and queued["status"] == "queued", queued
    assert queued["queue_ack"]["request_id"] == c.run_dir.name
    assert c.journal()["state"] == "synced"
    assert c.git("rev-list", "--count", "HEAD").strip() == b"1"
    intent = queued["intent"]["id"]
    completed = run([native, "--no-runtime", "artifact", "finalize", "--intent", intent,
                     "--allow-commit", "--json"], c.repo, c.env, check=False)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    value = json.loads(completed.stdout)
    assert value["status"] == "committed", value
    assert c.journal()["state"] == "done"
    assert hooks_log.read_text().splitlines() == ["pre-commit", "prepare-commit-msg", "commit-msg", "post-commit"]
    assert c.git("rev-list", "--count", "HEAD").strip() == b"2"
    again = run([native, "--no-runtime", "artifact", "finalize", "--intent", intent,
                 "--allow-commit", "--json"], c.repo, c.env, check=False)
    assert again.returncode == 0, (again.stdout, again.stderr)
    assert c.git("rev-list", "--count", "HEAD").strip() == b"2"


@pytest.mark.skipif(sys.platform != "darwin", reason="stock Bash selection is a Darwin contract")
def test_darwin_uses_bound_stock_bash_not_a_path_substitute(lifecycle):
    c = lifecycle
    fake = c.bin / "bash"
    fake.write_text("#!/bin/sh\nexit 97\n")
    fake.chmod(0o700)
    result = c.start(allow=True)
    assert result.returncode == 0, (result.stdout, result.stderr)
    receipt = json.loads((c.run_dir / "sanitation.json").read_bytes())
    assert receipt["bash"]["path"] == "/bin/bash"


def test_repository_inode_replacement_invalidates_the_run(lifecycle):
    c = lifecycle
    assert c.start().returncode == 0
    moved = c.repo.with_name("original-repository")
    c.repo.rename(moved)
    c.repo.mkdir()
    for child in moved.iterdir():
        child.rename(c.repo / child.name)
    result = c.finalize("--allow-commit")
    assert result.returncode == 6
    assert json.loads(result.stdout)["status"] == "repository_replaced"
    assert c.git("rev-list", "--count", "HEAD").strip() == b"1"
