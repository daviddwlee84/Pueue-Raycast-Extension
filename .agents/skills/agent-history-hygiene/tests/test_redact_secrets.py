"""Unit tests for the pure functions in assets/redact_secrets.py.

These cover the redaction primitives independent of gitleaks, so they
pass even on a box without the binary installed. Integration coverage
of the full gitleaks call-path lives in `test_gitleaks_corpus.py`.
"""
from __future__ import annotations

import hashlib
import os
import shlex
import stat
import subprocess
import sys
from pathlib import Path

import pytest


def _safe_assert(condition: bool, message: str) -> None:
    """Fail with fixed text so pytest never renders sensitive operands."""
    if not condition:
        pytest.fail(message, pytrace=False)


def _assert_exit(result: subprocess.CompletedProcess, expected: int) -> None:
    _safe_assert(result.returncode == expected, "redactor returned an unexpected status")


def _assert_bytes_equal(actual: bytes, expected: bytes, message: str) -> None:
    actual_digest = hashlib.sha256(actual).digest()
    expected_digest = hashlib.sha256(expected).digest()
    _safe_assert(actual_digest == expected_digest, message)


def _assert_text_equal(actual: str, expected: str, message: str) -> None:
    _assert_bytes_equal(actual.encode(), expected.encode(), message)


def _assert_sensitive_absent(output: str | bytes, *values: str | bytes) -> None:
    for value in values:
        if value and value in output:
            pytest.fail("sensitive fixture bytes appeared in public output", pytrace=False)


def _assert_sensitive_present(output: str | bytes, value: str | bytes) -> None:
    _safe_assert(value in output, "expected sensitive fixture bytes were not preserved")

# PEM headers are assembled at runtime, never written as literals -- a
# contiguous `BEGIN <TYPE> PRIVATE KEY` in a file we ship fails
# detect-private-key in every downstream repo that installs this skill,
# and that hook honours no allowlist marker. See conftest for the detail.
from conftest import (
    OPENVPN_HEADER,
    PUTTY_HEADER,
    pem_block,
    pem_header,
)


class TestRedactSecret:
    """`redact_secret` turns a long secret into `first3...last3`."""

    def test_redacts_long_secret_to_prefix_ellipsis_suffix(self, redact_secrets):
        # secret ends with "xyzAA" → last 3 chars are "zAA"
        result = redact_secrets.redact_secret("sk-ant-api03-" + "a" * 90 + "xyzAA")
        assert result.startswith("sk-")
        assert result.endswith("zAA")
        assert "..." in result
        assert len(result) == 3 + 3 + 3  # prefix + ellipsis + suffix

    def test_redacts_short_secret_to_placeholder(self, redact_secrets):
        # Threshold is keep_chars*2 + 3 = 9. "short" (5 chars) is below.
        assert redact_secrets.redact_secret("short") == "[REDACTED]"

    def test_custom_keep_chars(self, redact_secrets):
        result = redact_secrets.redact_secret("a" * 30, keep_chars=5)
        assert result == "aaaaa...aaaaa"


class TestFilterByPrefixes:
    """`filter_by_prefixes` keeps findings whose File matches any prefix."""

    def test_filters_to_only_matching_prefixes(self, redact_secrets):
        findings = [
            {"File": ".claude/plans/p1.md", "Secret": "sk-real"},
            {"File": "src/main.py", "Secret": "sk-real"},
            {"File": ".specstory/history/2026.md", "Secret": "sk-real"},
        ]
        filtered = redact_secrets.filter_by_prefixes(
            findings, [".claude/plans", ".specstory/history"]
        )
        assert len(filtered) == 2
        assert {f["File"] for f in filtered} == {
            ".claude/plans/p1.md",
            ".specstory/history/2026.md",
        }

    def test_returns_empty_when_no_match(self, redact_secrets):
        findings = [{"File": "src/main.py", "Secret": "x"}]
        assert redact_secrets.filter_by_prefixes(findings, [".claude/plans"]) == []

    def test_trailing_slash_tolerance(self, redact_secrets):
        """The helper normalizes trailing slashes, so both forms work."""
        findings = [{"File": ".claude/plans/p1.md", "Secret": "x"}]
        with_slash = redact_secrets.filter_by_prefixes(findings, [".claude/plans/"])
        without_slash = redact_secrets.filter_by_prefixes(findings, [".claude/plans"])
        assert len(with_slash) == 1
        assert len(without_slash) == 1

    def test_rejects_prefix_lookalikes(self, redact_secrets):
        findings = [
            {"File": ".claude/plans-copy/p1.md", "Secret": "x"},
            {"File": "notes/.claude/plans/p1.md", "Secret": "x"},
        ]
        assert redact_secrets.filter_by_prefixes(
            findings, [".claude/plans"]
        ) == []


class TestFindPrivateKeyFiles:
    """`find_private_key_files` detects PEM blocks + stray key *headers*, and
    IGNORES bare 'PRIVATE KEY' prose (which detect-private-key ignores too)."""

    def test_detects_pem_block(self, redact_secrets, tmp_path: Path):
        f = tmp_path / "p.md"
        f.write_text(
            "prose\n" + pem_block() + "more prose\n",
            encoding="utf-8",
        )
        results = redact_secrets.find_private_key_files([f])
        assert f in results
        # Description should mention at least one PEM block
        assert any("PEM" in desc for desc in results[f])

    def test_ignores_bare_mention_without_header(self, redact_secrets, tmp_path: Path):
        """Prose that merely says 'PRIVATE KEY' is not key material.
        detect-private-key ignores it, and flagging it caused a
        non-converging redact loop, so we ignore it too."""
        f = tmp_path / "p.md"
        f.write_text("hey here is a PRIVATE KEY mention\n", encoding="utf-8")
        assert redact_secrets.find_private_key_files([f]) == {}

    def test_detects_stray_header_without_block(self, redact_secrets, tmp_path: Path):
        """A key *header* quoted in prose (no matching END) is exactly what
        detect-private-key greps for, so it must be flagged."""
        f = tmp_path / "p.md"
        f.write_text(
            f'oops pasted {pem_header("OPENSSH")} then stopped\n',
            encoding="utf-8",
        )
        results = redact_secrets.find_private_key_files([f])
        assert f in results
        assert any("header" in desc for desc in results[f])

    def test_detects_non_pem_blacklist_headers(self, redact_secrets, tmp_path: Path):
        """PuTTY + OpenVPN headers have no 'PRIVATE KEY' text but are on the
        detect-private-key BLACKLIST. The OpenVPN token is built from split
        string literals in the source; this guards that it still matches."""
        f = tmp_path / "p.md"
        f.write_text(
            f"{PUTTY_HEADER}: ssh-rsa\n"
            f"{OPENVPN_HEADER}\n",
            encoding="utf-8",
        )
        results = redact_secrets.find_private_key_files([f])
        assert f in results
        assert any("header" in desc for desc in results[f])

    def test_ignores_non_md_suffix(self, redact_secrets, tmp_path: Path):
        f = tmp_path / "p.txt"
        f.write_text(pem_header() + "\n", encoding="utf-8")
        assert redact_secrets.find_private_key_files([f]) == {}

    def test_ignores_clean_file(self, redact_secrets, tmp_path: Path):
        f = tmp_path / "p.md"
        f.write_text("all clean here\n", encoding="utf-8")
        assert redact_secrets.find_private_key_files([f]) == {}


class TestRedactFile:
    """`redact_file` rewrites matching findings in place, returns True if modified."""

    def test_replaces_secret_in_place(self, redact_secrets, tmp_path: Path):
        secret = "sk-proj-" + "A" * 90
        f = tmp_path / "p.md"
        f.write_text(f"line1\nOPENAI={secret}\nline3\n", encoding="utf-8")
        findings = [{"File": str(f), "Secret": secret}]
        findings[0]["RuleID"] = "openai-project-key"
        modified = redact_secrets.redact_file(f, findings)
        assert modified is True
        content = f.read_text(encoding="utf-8")
        _assert_sensitive_absent(content, secret)
        # Same sentinel shape SpecStory >= 2.4.0 writes natively.
        assert "[REDACTED:openai-project-key]" in content

    def test_legacy_keeps_truncated_form(self, redact_secrets, tmp_path: Path):
        secret = "sk-proj-" + "A" * 90
        f = tmp_path / "p.md"
        f.write_text(f"OPENAI={secret}\n", encoding="utf-8")
        findings = [{"File": str(f), "Secret": secret, "RuleID": "openai-project-key"}]
        assert redact_secrets.redact_file(f, findings, legacy=True) is True
        content = f.read_text(encoding="utf-8")
        assert "sk-...AAA" in content  # first3 + ... + last3
        assert "[REDACTED:" not in content

    def test_returns_false_when_secret_not_present(
        self, redact_secrets, tmp_path: Path
    ):
        """Edge case: gitleaks found secret in staged diff but working
        copy was already redacted by a prior run."""
        f = tmp_path / "p.md"
        f.write_text("already redacted: sk-...AAA\n", encoding="utf-8")
        findings = [{"File": str(f), "Secret": "sk-proj-" + "A" * 90}]
        modified = redact_secrets.redact_file(f, findings)
        assert modified is False

    def test_ignores_findings_for_other_files(self, redact_secrets, tmp_path: Path):
        secret = "sk-proj-" + "A" * 90
        f = tmp_path / "p.md"
        other = tmp_path / "other.md"
        f.write_text(f"OPENAI={secret}\n", encoding="utf-8")
        findings = [{"File": str(other), "Secret": secret}]
        modified = redact_secrets.redact_file(f, findings)
        assert modified is False  # finding was for `other`, not `f`
        _assert_sensitive_present(f.read_text(encoding="utf-8"), secret)


class TestRedactPrivateKeys:
    """`redact_private_keys` scrubs PEM blocks + stray key *headers*, and
    LEAVES bare 'PRIVATE KEY' prose untouched."""

    def test_replaces_pem_block_wholesale(self, redact_secrets, tmp_path: Path):
        f = tmp_path / "p.md"
        f.write_text(
            "prose\n" + pem_block() + "more prose\n",
            encoding="utf-8",
        )
        modified = redact_secrets.redact_private_keys(f)
        assert modified is True
        content = f.read_text(encoding="utf-8")
        # Sentinel must contain neither a header token nor the bare phrase, so
        # a re-run is a no-op. Lowercase on purpose: the header regex matches
        # uppercase only. Same label SpecStory emits for this class.
        assert "[REDACTED:private-key]" in content
        assert "fake material" not in content
        # The original PEM header must be gone (it contains "PRIVATE KEY").
        _assert_sensitive_absent(content, pem_header())

    def test_legacy_writes_the_pre_2_4_0_sentinel(
        self, redact_secrets, tmp_path: Path
    ):
        """--legacy changes only the bytes written, never what is detected."""
        f = tmp_path / "p.md"
        f.write_text(pem_block(), encoding="utf-8")
        assert redact_secrets.redact_private_keys(f, legacy=True) is True
        content = f.read_text(encoding="utf-8")
        assert "[REDACTED PEM PRIVKEY BLOCK]" in content
        assert "[REDACTED:" not in content

    def test_leaves_bare_mention_untouched(self, redact_secrets, tmp_path: Path):
        """Bare prose mentions are not key material; redacting them mangled
        legitimate text and never converged against a live transcript writer."""
        f = tmp_path / "p.md"
        original = "mention PRIVATE KEY here\n"
        f.write_text(original, encoding="utf-8")
        modified = redact_secrets.redact_private_keys(f)
        assert modified is False
        assert f.read_text(encoding="utf-8") == original

    def test_redacts_isolated_header_in_prose(
        self, redact_secrets, tmp_path: Path
    ):
        """A quoted token without record-like data can safely converge."""
        f = tmp_path / "p.md"
        header = pem_header("OPENSSH")
        f.write_text(
            f"log quoted {header} and then ordinary explanatory prose\n",
            encoding="utf-8",
        )

        modified = redact_secrets.redact_private_keys(f)

        content = f.read_text(encoding="utf-8")
        _safe_assert(modified is True, "isolated header was not redacted")
        _assert_sensitive_absent(content, header)
        _safe_assert(
            "[REDACTED:private-key]" in content,
            "isolated header sentinel is missing",
        )

    def test_redacts_isolated_header_in_captured_test_output(
        self, redact_secrets, tmp_path: Path
    ):
        f = tmp_path / "p.md"
        header = pem_header("OPENSSH")
        f.write_text(
            f"E assertion displayed {header}\nCaptured stderr: fixed diagnostic\n",
            encoding="utf-8",
        )

        _safe_assert(
            redact_secrets.redact_private_keys(f) is True,
            "captured-output header was not redacted",
        )
        _assert_sensitive_absent(f.read_text(encoding="utf-8"), header)

    def test_rejects_header_followed_by_plausible_payload(
        self, redact_secrets, tmp_path: Path
    ):
        """Encoded data after a header remains fail-closed."""
        f = tmp_path / "p.md"
        original = f"{pem_header('OPENSSH')}\nQUJDRA==\n"
        f.write_text(original, encoding="utf-8")

        with pytest.raises(redact_secrets.IncompletePrivateKeyError):
            redact_secrets.redact_private_keys(f)

        _assert_text_equal(
            f.read_text(encoding="utf-8"),
            original,
            "plausibly truncated private-key record changed",
        )

    def test_leaves_clean_file_unchanged(self, redact_secrets, tmp_path: Path):
        f = tmp_path / "p.md"
        original = "plain prose\n"
        f.write_text(original, encoding="utf-8")
        modified = redact_secrets.redact_private_keys(f)
        assert modified is False
        assert f.read_text(encoding="utf-8") == original


class TestStructuredPrivateKeyRecords:
    def test_removes_complete_openvpn_record(self, redact_secrets):
        footer = OPENVPN_HEADER.replace("BEGIN", "END", 1)
        original = f"before\n{OPENVPN_HEADER}\n0011223344556677\n{footer}\nafter\n"
        transformed = redact_secrets.redact_private_key_content(original)
        _assert_text_equal(
            transformed,
            "before\n[REDACTED:private-key]\nafter\n",
            "complete private-key record did not converge",
        )

    def test_removes_complete_putty_record(self, redact_secrets):
        original = (
            f"before\n{PUTTY_HEADER}: ssh-rsa\n"
            "Encryption: none\n"
            "Comment: fixture\n"
            "Public-Lines: 1\n"
            "QUJDRA==\n"
            "Private-Lines: 1\n"
            "RUZHSA==\n"
            f"Private-MAC: {'a' * 40}\n"
            "after\n"
        )
        transformed = redact_secrets.redact_private_key_content(original)
        _assert_text_equal(
            transformed,
            "before\n[REDACTED:private-key]\nafter\n",
            "complete private-key record did not converge",
        )

    def test_rejects_openvpn_header_followed_by_hex_payload(self, redact_secrets):
        truncated = f"{OPENVPN_HEADER}\n{'0011223344556677' * 2}\n"
        with pytest.raises(redact_secrets.IncompletePrivateKeyError):
            redact_secrets.redact_private_key_content(truncated)

    def test_rejects_incomplete_putty_structure(self, redact_secrets):
        truncated = (
            f"{PUTTY_HEADER}: ssh-rsa\n"
            "Encryption: none\n"
            "Comment: fixture\n"
        )
        with pytest.raises(redact_secrets.IncompletePrivateKeyError):
            redact_secrets.redact_private_key_content(truncated)

    def test_pure_scanner_replacement_preserves_surrounding_text(
        self, redact_secrets
    ):
        secret = "synthetic-sensitive-value"
        original = f"prefix\r\n{secret}\r\nsuffix\n"
        transformed = redact_secrets.redact_content(
            original,
            [{"Secret": secret, "RuleID": "fixture-rule"}],
            strict=True,
        )
        _assert_text_equal(
            transformed,
            "prefix\r\n[REDACTED:fixture-rule]\r\nsuffix\n",
            "scanner replacement changed surrounding bytes",
        )

    def test_same_bytes_matching_multiple_rules_are_replaced_once(
        self, redact_secrets
    ):
        secret = "shared-sensitive-value"
        transformed = redact_secrets.redact_content(
            secret,
            [
                {"Secret": secret, "RuleID": "z-rule"},
                {"Secret": secret, "RuleID": "a-rule"},
            ],
            strict=True,
        )
        _assert_text_equal(
            transformed,
            "[REDACTED:a-rule]",
            "overlapping scanner rules did not converge",
        )


class TestRedactionPlaceholder:
    """`redaction_placeholder` mirrors SpecStory's `[REDACTED:%s]` shape."""

    def test_uses_rule_id(self, redact_secrets):
        assert (
            redact_secrets.redaction_placeholder("openai-project-key")
            == "[REDACTED:openai-project-key]"
        )

    def test_normalizes_odd_rule_ids(self, redact_secrets):
        assert (
            redact_secrets.redaction_placeholder("Generic API Key")
            == "[REDACTED:generic-api-key]"
        )

    def test_falls_back_when_rule_id_missing(self, redact_secrets):
        assert redact_secrets.redaction_placeholder("") == "[REDACTED:secret]"

    def test_placeholder_retains_no_secret_bytes(self, redact_secrets):
        """The whole point: a placeholder can never be re-flagged."""
        secret = "sk-proj-" + "A" * 90
        placeholder = redact_secrets.redaction_placeholder("openai-project-key")
        assert secret[:8] not in placeholder
        assert secret[-8:] not in placeholder


class TestIdempotency:
    """A second pass must not rewrite a file the first pass already redacted.

    This is what stops the `git add` -> commit -> "files were modified by this
    hook" -> `git add` -> commit loop.
    """

    def test_second_redact_file_pass_is_a_noop(self, redact_secrets, tmp_path: Path):
        secret = "sk-proj-" + "A" * 90
        f = tmp_path / "p.md"
        f.write_text(f"OPENAI={secret}\n", encoding="utf-8")
        findings = [{"File": str(f), "Secret": secret, "RuleID": "openai-project-key"}]
        assert redact_secrets.redact_file(f, findings) is True
        after_first = f.read_text(encoding="utf-8")
        assert redact_secrets.redact_file(f, findings) is False
        assert f.read_text(encoding="utf-8") == after_first

    def test_specstory_placeholder_is_left_alone(self, redact_secrets, tmp_path: Path):
        """A transcript SpecStory already redacted must not be touched."""
        f = tmp_path / "p.md"
        original = "GITHUB_TOKEN=[REDACTED:github-pat]\nprose about a PRIVATE KEY\n"
        f.write_text(original, encoding="utf-8")
        assert redact_secrets.redact_private_keys(f) is False
        assert redact_secrets.find_private_key_files([f]) == {}
        assert f.read_text(encoding="utf-8") == original


class TestDefaultPaths:
    """The DEFAULT_PATHS list must cover every artifact dir we advertise."""

    def test_includes_all_advertised_dirs(self, redact_secrets):
        expected = {
            ".specstory/history",
            ".claude/plans",
            ".cursor/plans",
            ".cursor/rules",
            ".opencode/plans",
            ".specify",
            ".codex",
        }
        assert expected.issubset(set(redact_secrets.DEFAULT_PATHS))


REDACTOR = Path(__file__).resolve().parent.parent / "assets" / "redact_secrets.py"


def _git(
    repo: Path,
    *args: str,
    env=None,
    check: bool = True,
    input_data: bytes | None = None,
):
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        env=env,
        input=input_data,
        check=False,
        capture_output=True,
    )
    if check and result.returncode != 0:
        pytest.fail("git fixture setup failed", pytrace=False)
    return result


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "README.md").write_text("initial\n", encoding="utf-8")
    _git(repo, "add", "--", "README.md")
    _git(repo, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "init")
    return repo


def _stage_bytes(repo: Path, relative: str, data: bytes, executable: bool = False) -> Path:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(0o755 if executable else 0o644)
    _git(repo, "add", "--", relative)
    return path


def _index_bytes(repo: Path, relative: str, env=None) -> bytes:
    return _git(repo, "show", f":{relative}", env=env).stdout


def _index_oid(repo: Path, relative: str, env=None) -> str:
    record = _git(repo, "ls-files", "--stage", "--", relative, env=env).stdout
    return record.decode("ascii").split()[1]


def _index_mode(repo: Path, relative: str, env=None) -> str:
    record = _git(repo, "ls-files", "--stage", "--", relative, env=env).stdout
    return record.decode("ascii").split()[0]


def _install_fake_gitleaks(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "gitleaks"
    fake.write_text(
        """#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from pathlib import Path

args = sys.argv[1:]
report = Path(args[args.index("--report-path") + 1])
mode = os.environ.get("FAKE_GITLEAKS_MODE", "clean")
state_path = os.environ.get("FAKE_GITLEAKS_STATE")
call_number = 1
if state_path:
    state = Path(state_path)
    if state.exists():
        call_number = int(state.read_text(encoding="ascii")) + 1
    state.write_text(str(call_number), encoding="ascii")

if mode == "error":
    print(os.environ.get("FAKE_GITLEAKS_STDERR", "scanner-private-diagnostic"), file=sys.stderr)
    raise SystemExit(9)
if mode == "malformed" or (mode == "once-malformed" and call_number > 1):
    report.write_text("{not-json", encoding="utf-8")
    raise SystemExit(0)
if mode == "object":
    report.write_text("{}", encoding="utf-8")
    raise SystemExit(0)

find_once = mode in ("once", "once-malformed") and call_number == 1
staged_match = False
if mode == "staged-diff":
    relative = os.environ["FAKE_GITLEAKS_FILE"]
    secret = os.environ["FAKE_GITLEAKS_SECRET"].encode()
    process = subprocess.run(
        ["git", "diff", "--cached", "--unified=0", "--", relative],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if process.returncode != 0:
        raise SystemExit(8)
    added = b"\\n".join(
        line[1:]
        for line in process.stdout.splitlines()
        if line.startswith(b"+") and not line.startswith(b"+++")
    )
    staged_match = secret in added
if mode == "finding" or find_once or staged_match:
    data = [{
        "File": os.environ["FAKE_GITLEAKS_FILE"],
        "Secret": os.environ["FAKE_GITLEAKS_SECRET"],
        "Match": os.environ["FAKE_GITLEAKS_SECRET"],
        "RuleID": "fixture-rule",
        "StartLine": 2,
    }]
else:
    data = []
report.write_text(json.dumps(data), encoding="utf-8")
""",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    return bin_dir


def _redactor_env(bin_dir: Path, **values: str) -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    env.update(values)
    return env


def _with_alternate_index(
    repo: Path, tmp_path: Path, env: dict[str, str]
) -> dict[str, str]:
    alternate_index = tmp_path / "candidate-index"
    alternate_index.write_bytes((repo / ".git/index").read_bytes())
    updated = env.copy()
    updated["GIT_INDEX_FILE"] = str(alternate_index)
    return updated


def _run_redactor(repo: Path, env: dict[str, str], *args: str):
    return subprocess.run(
        [sys.executable, str(REDACTOR), *args],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


class TestIndexChecking:
    def test_unchanged_parent_secret_is_outside_staged_diff_gate(
        self, tmp_path: Path
    ):
        repo = _init_repo(tmp_path)
        relative = ".claude/plans/session.md"
        secret = "runtime-sensitive-" + "A" * 40  # gitleaks:allow
        worktree = _stage_bytes(repo, relative, f"{secret}\nstable\n".encode())
        _git(
            repo,
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "-q",
            "-m",
            "artifact parent",
        )
        with worktree.open("a", encoding="utf-8") as stream:
            stream.write("new clean line\n")
        _git(repo, "add", "--", relative)
        env = _redactor_env(
            _install_fake_gitleaks(tmp_path),
            FAKE_GITLEAKS_MODE="staged-diff",
            FAKE_GITLEAKS_FILE=relative,
            FAKE_GITLEAKS_SECRET=secret,
        )

        result = _run_redactor(repo, env, "--check-index")

        _assert_exit(result, 0)
        _assert_sensitive_absent(result.stdout + result.stderr, secret)
        normalized = " ".join(result.stdout.split())
        _safe_assert(
            "Unchanged parent lines and repository history were not re-audited"
            in normalized,
            "staged-diff scope disclaimer is absent",
        )
        _safe_assert(
            "No secrets found" not in normalized,
            "staged-diff pass was misrepresented as a full-index clean audit",
        )

    def test_new_selected_artifact_is_scanned_as_full_addition(
        self, tmp_path: Path
    ):
        repo = _init_repo(tmp_path)
        relative = ".claude/plans/new-session.md"
        secret = "runtime-sensitive-" + "B" * 40  # gitleaks:allow
        _stage_bytes(repo, relative, f"prefix\n{secret}\nsuffix\n".encode())
        env = _redactor_env(
            _install_fake_gitleaks(tmp_path),
            FAKE_GITLEAKS_MODE="staged-diff",
            FAKE_GITLEAKS_FILE=relative,
            FAKE_GITLEAKS_SECRET=secret,
        )

        result = _run_redactor(repo, env, "--check-index")

        _assert_exit(result, 1)
        _assert_sensitive_absent(result.stdout + result.stderr, secret)
        _safe_assert(
            "newly staged scanner finding" in result.stdout,
            "new artifact addition was not fully scanned",
        )

    def test_index_clean_worktree_leaky(self, tmp_path: Path):
        repo = _init_repo(tmp_path)
        relative = ".claude/plans/session.md"
        worktree = _stage_bytes(repo, relative, b"staged clean\n")
        worktree.write_text(pem_block(body="worktree-only-body"), encoding="utf-8")
        env = _redactor_env(_install_fake_gitleaks(tmp_path))

        result = _run_redactor(repo, env, "--check-index")

        _assert_exit(result, 0)
        assert _index_bytes(repo, relative) == b"staged clean\n"

    def test_index_leaky_worktree_clean(self, tmp_path: Path):
        repo = _init_repo(tmp_path)
        relative = ".claude/plans/session.md"
        body_marker = "index-private-body-marker"
        worktree = _stage_bytes(
            repo, relative, pem_block(body=body_marker).encode()
        )
        worktree.write_text("working tree clean\n", encoding="utf-8")
        env = _redactor_env(_install_fake_gitleaks(tmp_path))

        result = _run_redactor(repo, env, "--check-index")

        public_output = result.stdout + result.stderr
        _assert_exit(result, 1)
        _assert_sensitive_absent(public_output, body_marker, pem_header())
        assert worktree.read_text(encoding="utf-8") == "working tree clean\n"

    @pytest.mark.parametrize(
        ("mode", "expected_fragment"),
        [("error", "scanner unavailable"), ("malformed", "invalid"), ("object", "invalid")],
    )
    def test_scanner_errors_are_not_clean(
        self, tmp_path: Path, mode: str, expected_fragment: str
    ):
        repo = _init_repo(tmp_path)
        _stage_bytes(repo, ".claude/plans/session.md", b"staged clean\n")
        private_diagnostic = "scanner-must-not-echo-this-value"
        env = _redactor_env(
            _install_fake_gitleaks(tmp_path),
            FAKE_GITLEAKS_MODE=mode,
            FAKE_GITLEAKS_STDERR=private_diagnostic,
        )

        result = _run_redactor(repo, env, "--check-index")

        _assert_exit(result, 2)
        _safe_assert(
            expected_fragment in result.stderr,
            "scanner failure diagnostic classification is missing",
        )
        _assert_sensitive_absent(result.stdout + result.stderr, private_diagnostic)

    def test_missing_scanner_is_not_clean(self, tmp_path: Path):
        repo = _init_repo(tmp_path)
        _stage_bytes(repo, ".claude/plans/session.md", b"staged clean\n")
        empty_bin = tmp_path / "empty-bin"
        empty_bin.mkdir()
        env = os.environ.copy()
        env["PATH"] = str(empty_bin) + os.pathsep + "/usr/bin:/bin"

        result = _run_redactor(repo, env, "--check-index")

        _assert_exit(result, 2)
        _safe_assert("not installed" in result.stderr, "missing-scanner message is absent")

    def test_rejects_staged_symlink_candidate(self, tmp_path: Path):
        repo = _init_repo(tmp_path)
        relative = ".claude/plans/session.md"
        path = repo / relative
        path.parent.mkdir(parents=True)
        path.symlink_to("../../README.md")
        _git(repo, "add", "--", relative)
        env = _redactor_env(_install_fake_gitleaks(tmp_path))

        result = _run_redactor(repo, env, "--check-index")

        _assert_exit(result, 2)
        _safe_assert("regular blob" in result.stderr, "unsafe index mode was not rejected")

    def test_rejects_staged_gitlink_candidate(self, tmp_path: Path):
        repo = _init_repo(tmp_path)
        relative = ".claude/plans/module.md"
        commit_oid = _git(repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()
        _git(
            repo,
            "update-index",
            "--add",
            "--cacheinfo",
            "160000",
            commit_oid,
            relative,
        )
        env = _redactor_env(_install_fake_gitleaks(tmp_path))

        result = _run_redactor(repo, env, "--check-index")

        _assert_exit(result, 2)
        _safe_assert("regular blob" in result.stderr, "unsafe index mode was not rejected")

    def test_rejects_unmerged_artifact_candidate(self, tmp_path: Path):
        repo = _init_repo(tmp_path)
        relative = ".claude/plans/conflict.md"
        object_ids = []
        for value in (b"base\n", b"ours\n", b"theirs\n"):
            result = _git(repo, "hash-object", "-w", "--stdin", input_data=value)
            object_ids.append(result.stdout.decode("ascii").strip())
        index_info = "".join(
            f"100644 {oid} {stage}\t{relative}\n"
            for stage, oid in enumerate(object_ids, start=1)
        ).encode()
        _git(repo, "update-index", "--index-info", input_data=index_info)
        env = _redactor_env(_install_fake_gitleaks(tmp_path))

        result = _run_redactor(repo, env, "--check-index")

        _assert_exit(result, 2)
        _safe_assert("unmerged" in result.stderr, "unmerged index was not rejected")


class TestIndexFixing:
    def test_refuses_to_modify_the_canonical_index(self, tmp_path: Path):
        repo = _init_repo(tmp_path)
        relative = ".claude/plans/session.md"
        original = pem_block(body="canonical-index-body").encode()
        _stage_bytes(repo, relative, original)
        env = _redactor_env(_install_fake_gitleaks(tmp_path))

        result = _run_redactor(repo, env, "--fix-index", "--files", relative)

        _assert_exit(result, 2)
        _safe_assert(
            "noncanonical GIT_INDEX_FILE" in result.stderr,
            "canonical index refusal message is absent",
        )
        _assert_bytes_equal(
            _index_bytes(repo, relative),
            original,
            "canonical index changed after mutation refusal",
        )

    @pytest.mark.parametrize(
        ("executable", "expected_mode"), [(False, "100644"), (True, "100755")]
    )
    def test_preserves_mode_nonsecret_bytes_and_worktree(
        self, tmp_path: Path, executable: bool, expected_mode: str
    ):
        repo = _init_repo(tmp_path)
        relative = ".claude/plans/session.md"
        prefix = b"\xffbefore\r\n"
        suffix = b"\x00after\r\n"
        staged = prefix + pem_block(body="bounded-body").encode() + suffix
        worktree = _stage_bytes(repo, relative, staged, executable=executable)
        worktree.write_bytes(b"worktree remains clean\n")
        env = _with_alternate_index(
            repo, tmp_path, _redactor_env(_install_fake_gitleaks(tmp_path))
        )

        result = _run_redactor(
            repo, env, "--fix-index", "--files", relative
        )

        _assert_exit(result, 0)
        _assert_bytes_equal(
            _index_bytes(repo, relative, env=env),
            prefix + b"[REDACTED:private-key]\n" + suffix,
            "sanitized alternate-index bytes differ",
        )
        _safe_assert(
            _index_mode(repo, relative, env=env) == expected_mode,
            "sanitation changed the index mode",
        )
        _assert_bytes_equal(
            _index_bytes(repo, relative),
            staged,
            "canonical index changed during alternate-index sanitation",
        )
        _assert_bytes_equal(
            worktree.read_bytes(),
            b"worktree remains clean\n",
            "worktree changed during index-only sanitation",
        )

    def test_repeatable_files_selects_each_exact_blob(self, tmp_path: Path):
        repo = _init_repo(tmp_path)
        first = ".claude/plans/first.md"
        second = ".cursor/plans/second.md"
        _stage_bytes(repo, first, pem_block(body="first-body").encode())
        _stage_bytes(repo, second, pem_block(body="second-body").encode())
        env = _with_alternate_index(
            repo, tmp_path, _redactor_env(_install_fake_gitleaks(tmp_path))
        )

        result = _run_redactor(
            repo,
            env,
            "--fix-index",
            "--files",
            first,
            "--files",
            second,
        )

        _assert_exit(result, 0)
        _assert_bytes_equal(
            _index_bytes(repo, first, env=env),
            b"[REDACTED:private-key]\n",
            "first selected blob did not converge",
        )
        _assert_bytes_equal(
            _index_bytes(repo, second, env=env),
            b"[REDACTED:private-key]\n",
            "second selected blob did not converge",
        )

    def test_honors_inherited_alternate_index(self, tmp_path: Path):
        repo = _init_repo(tmp_path)
        relative = ".claude/plans/session.md"
        secret = "synthetic-index-only-sensitive-value"
        worktree = _stage_bytes(
            repo, relative, f"before\n{secret}\nafter\n".encode()
        )
        alternate_index = tmp_path / "candidate-index"
        alternate_index.write_bytes((repo / ".git/index").read_bytes())

        worktree.write_text("real-index-and-worktree-clean\n", encoding="utf-8")
        _git(repo, "add", "--", relative)

        state = tmp_path / "gitleaks-state"
        env = _redactor_env(
            _install_fake_gitleaks(tmp_path),
            GIT_INDEX_FILE=str(alternate_index),
            FAKE_GITLEAKS_MODE="once",
            FAKE_GITLEAKS_STATE=str(state),
            FAKE_GITLEAKS_FILE=relative,
            FAKE_GITLEAKS_SECRET=secret,
        )

        result = _run_redactor(
            repo, env, "--fix-index", "--files", relative
        )

        _assert_exit(result, 0)
        _assert_bytes_equal(
            _index_bytes(repo, relative, env=env),
            b"before\n[REDACTED:fixture-rule]\nafter\n",
            "alternate index did not converge",
        )
        _assert_bytes_equal(
            _index_bytes(repo, relative),
            b"real-index-and-worktree-clean\n",
            "canonical index changed",
        )
        _assert_bytes_equal(
            worktree.read_bytes(),
            b"real-index-and-worktree-clean\n",
            "worktree changed",
        )

    def test_blob_write_does_not_reapply_clean_filter(self, tmp_path: Path):
        repo = _init_repo(tmp_path)
        filter_program = tmp_path / "append-filter.py"
        filter_program.write_text(
            "import sys\n"
            "data = sys.stdin.buffer.read()\n"
            "sys.stdout.buffer.write(data + b'FILTER_PASS\\n')\n",
            encoding="utf-8",
        )
        command = f"{shlex.quote(sys.executable)} {shlex.quote(str(filter_program))}"
        _git(repo, "config", "filter.fixture.clean", command)
        _git(repo, "config", "filter.fixture.required", "true")
        (repo / ".gitattributes").write_text(
            ".claude/plans/*.md filter=fixture\n", encoding="utf-8"
        )
        _git(repo, "add", "--", ".gitattributes")

        relative = ".claude/plans/session.md"
        _stage_bytes(repo, relative, pem_block(body="filter-body").encode())
        assert _index_bytes(repo, relative).count(b"FILTER_PASS") == 1
        env = _with_alternate_index(
            repo, tmp_path, _redactor_env(_install_fake_gitleaks(tmp_path))
        )

        result = _run_redactor(
            repo, env, "--fix-index", "--files", relative
        )

        _assert_exit(result, 0)
        assert _index_bytes(repo, relative, env=env).count(b"FILTER_PASS") == 1

    def test_fix_is_idempotent(self, tmp_path: Path):
        repo = _init_repo(tmp_path)
        relative = ".claude/plans/session.md"
        _stage_bytes(repo, relative, pem_block(body="one-pass-body").encode())
        env = _with_alternate_index(
            repo, tmp_path, _redactor_env(_install_fake_gitleaks(tmp_path))
        )

        first = _run_redactor(repo, env, "--fix-index", "--files", relative)
        oid_after_first = _index_oid(repo, relative, env=env)
        second = _run_redactor(repo, env, "--fix-index", "--files", relative)

        _assert_exit(first, 0)
        _assert_exit(second, 0)
        _safe_assert(
            _index_oid(repo, relative, env=env) == oid_after_first,
            "idempotent pass changed the staged object id",
        )
        _safe_assert("Sanitized 0" in second.stdout, "idempotent status is absent")

    def test_rejects_lookalike_exact_path(self, tmp_path: Path):
        repo = _init_repo(tmp_path)
        relative = ".claude/plans-copy/session.md"
        original = pem_block(body="lookalike-body").encode()
        _stage_bytes(repo, relative, original)
        env = _with_alternate_index(
            repo, tmp_path, _redactor_env(_install_fake_gitleaks(tmp_path))
        )

        result = _run_redactor(
            repo, env, "--fix-index", "--files", relative
        )

        _assert_exit(result, 2)
        _assert_bytes_equal(
            _index_bytes(repo, relative, env=env),
            original,
            "lookalike index blob changed",
        )
        _assert_sensitive_absent(result.stdout + result.stderr, "lookalike-body")

    def test_incomplete_key_fails_without_changing_index(self, tmp_path: Path):
        repo = _init_repo(tmp_path)
        relative = ".claude/plans/session.md"
        body_marker = "QUJD" * 16
        original = f"{pem_header('OPENSSH')}\n{body_marker}\n".encode()
        worktree = _stage_bytes(repo, relative, original)
        worktree.write_text("worktree clean\n", encoding="utf-8")
        env = _with_alternate_index(
            repo, tmp_path, _redactor_env(_install_fake_gitleaks(tmp_path))
        )

        result = _run_redactor(
            repo, env, "--fix-index", "--files", relative
        )

        _assert_exit(result, 1)
        _assert_bytes_equal(
            _index_bytes(repo, relative, env=env),
            original,
            "incomplete-key index blob changed",
        )
        _assert_text_equal(
            worktree.read_text(encoding="utf-8"),
            "worktree clean\n",
            "incomplete-key worktree changed",
        )
        _assert_sensitive_absent(
            result.stdout + result.stderr, body_marker, pem_header("OPENSSH")
        )

    def test_malformed_post_scan_is_failure_and_output_is_safe(self, tmp_path: Path):
        repo = _init_repo(tmp_path)
        relative = ".claude/plans/session.md"
        secret = "post-scan-sensitive-value"
        _stage_bytes(repo, relative, f"before\n{secret}\nafter\n".encode())
        state = tmp_path / "gitleaks-state"
        env = _with_alternate_index(
            repo,
            tmp_path,
            _redactor_env(
                _install_fake_gitleaks(tmp_path),
                FAKE_GITLEAKS_MODE="once-malformed",
                FAKE_GITLEAKS_STATE=str(state),
                FAKE_GITLEAKS_FILE=relative,
                FAKE_GITLEAKS_SECRET=secret,
            ),
        )

        result = _run_redactor(
            repo, env, "--fix-index", "--files", relative
        )

        _assert_exit(result, 2)
        _assert_sensitive_absent(result.stdout + result.stderr, secret)
        _safe_assert(
            b"[REDACTED:fixture-rule]" in _index_bytes(repo, relative, env=env),
            "post-scan candidate was not sanitized before failure",
        )


class TestOverlappingScannerAndKeyFindings:
    def test_complete_key_removal_satisfies_header_finding(self, tmp_path: Path):
        repo = _init_repo(tmp_path)
        relative = ".claude/plans/session.md"
        header = pem_header()
        _stage_bytes(repo, relative, pem_block(body="scanner-overlap-body").encode())
        state = tmp_path / "gitleaks-state"
        env = _with_alternate_index(
            repo,
            tmp_path,
            _redactor_env(
                _install_fake_gitleaks(tmp_path),
                FAKE_GITLEAKS_MODE="once",
                FAKE_GITLEAKS_STATE=str(state),
                FAKE_GITLEAKS_FILE=relative,
                FAKE_GITLEAKS_SECRET=header,
            ),
        )

        result = _run_redactor(
            repo, env, "--fix-index", "--files", relative
        )

        _assert_exit(result, 0)
        _assert_bytes_equal(
            _index_bytes(repo, relative, env=env),
            b"[REDACTED:private-key]\n",
            "overlapping key finding did not converge",
        )
        _assert_sensitive_absent(result.stdout + result.stderr, header)


    def test_real_gitleaks_duplicate_rules_converge(
        self,
        tmp_path: Path,
        fixtures_dir: Path,
        assets_dir: Path,
        gitleaks_available: bool,
    ):
        if not gitleaks_available:
            pytest.skip("gitleaks is not installed")
        repo = _init_repo(tmp_path)
        (repo / ".gitleaks.toml").write_bytes(
            (assets_dir / "gitleaks.toml.template").read_bytes()
        )
        fixture = (fixtures_dir / "real_anthropic.md").read_text(encoding="utf-8")
        fixture = fixture.replace(" <!-- gitleaks:allow -->", "")
        secret = next(
            line.partition("=")[2]
            for line in fixture.splitlines()
            if line.startswith("ANTHROPIC_API_KEY=")
        )
        relative = ".claude/plans/session.md"
        worktree = _stage_bytes(repo, relative, fixture.encode())
        alternate_index = tmp_path / "real-gitleaks-index"
        alternate_index.write_bytes((repo / ".git/index").read_bytes())
        worktree.write_text("real index clean\n", encoding="utf-8")
        _git(repo, "add", "--", relative)
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(alternate_index)

        result = _run_redactor(
            repo,
            env,
            "--fix-index",
            "--files",
            relative,
        )

        _assert_exit(result, 0)
        _assert_sensitive_absent(_index_bytes(repo, relative, env=env), secret.encode())
        _assert_bytes_equal(
            _index_bytes(repo, relative),
            b"real index clean\n",
            "canonical index changed during alternate-index sanitation",
        )
        _assert_bytes_equal(
            worktree.read_bytes(),
            b"real index clean\n",
            "worktree changed during alternate-index sanitation",
        )
        _assert_sensitive_absent(result.stdout + result.stderr, secret)


class TestWorktreeFixCompatibility:
    def test_fix_changes_worktree_but_never_restages(self, tmp_path: Path):
        repo = _init_repo(tmp_path)
        relative = ".claude/plans/session.md"
        original = pem_block(body="compatibility-body").encode()
        worktree = _stage_bytes(repo, relative, original)
        env = _redactor_env(_install_fake_gitleaks(tmp_path))

        result = _run_redactor(repo, env, "--fix")

        _assert_exit(result, 0)
        _assert_bytes_equal(
            _index_bytes(repo, relative),
            original,
            "legacy fixer changed the Git index",
        )
        _assert_bytes_equal(
            worktree.read_bytes(),
            b"[REDACTED:private-key]\n",
            "legacy fixer did not converge",
        )
        _safe_assert("not changed" in result.stdout, "index status message is absent")
        _safe_assert(
            "nothing was re-staged" in result.stdout,
            "restaging status message is absent",
        )

    def test_symlink_root_is_rejected_and_external_target_is_preserved(
        self, tmp_path: Path
    ):
        repo = _init_repo(tmp_path)
        external_root = tmp_path / "outside-root"
        external_file = external_root / "plans/session.md"
        external_file.parent.mkdir(parents=True)
        original = pem_block(body="external-root-body").encode()
        external_file.write_bytes(original)
        (repo / ".claude").symlink_to(external_root, target_is_directory=True)
        env = _redactor_env(_install_fake_gitleaks(tmp_path))

        for arguments in (("--working-dir",), ("--fix", "--working-dir")):
            result = _run_redactor(repo, env, *arguments)
            _assert_exit(result, 2)
            _assert_sensitive_absent(
                result.stdout + result.stderr,
                pem_header(),
                "external-root-body",
            )
            _assert_bytes_equal(
                external_file.read_bytes(),
                original,
                "external file changed through a symlink root",
            )

        (repo / ".claude").unlink()
        (repo / ".claude").symlink_to(
            tmp_path / "missing-outside-root", target_is_directory=True
        )
        dangling_result = _run_redactor(repo, env, "--working-dir")
        _assert_exit(dangling_result, 2)

    def test_symlink_file_is_rejected_and_external_target_is_preserved(
        self, tmp_path: Path
    ):
        repo = _init_repo(tmp_path)
        plans = repo / ".claude/plans"
        plans.mkdir(parents=True)
        external_file = tmp_path / "outside.md"
        original = pem_block(body="external-file-body").encode()
        external_file.write_bytes(original)
        linked = plans / "linked.md"
        linked.symlink_to(external_file)
        _git(repo, "add", "--", ".claude/plans/linked.md")
        env = _redactor_env(_install_fake_gitleaks(tmp_path))

        for arguments in (
            ("--working-dir",),
            ("--fix",),
            ("--fix", "--working-dir"),
        ):
            result = _run_redactor(repo, env, *arguments)
            _assert_exit(result, 2)
            _assert_sensitive_absent(
                result.stdout + result.stderr,
                pem_header(),
                "external-file-body",
            )
            _assert_bytes_equal(
                external_file.read_bytes(),
                original,
                "external file changed through a symlink file",
            )

    def test_help_says_fix_does_not_update_index_or_restage(self):
        result = subprocess.run(
            [sys.executable, str(REDACTOR), "--help"],
            text=True,
            capture_output=True,
            check=False,
        )
        _assert_exit(result, 0)
        normalized_help = " ".join(result.stdout.split())
        _safe_assert(
            "does not update the Git index or re-stage files" in normalized_help,
            "legacy-fix help omits the index contract",
        )
