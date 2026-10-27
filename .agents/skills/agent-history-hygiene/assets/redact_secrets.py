#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
# NOTE: stdlib-only, so a plain `python3` shebang is the portable default
# (works on macOS system 3.9, CI, and as a pre-commit `language: script` hook
# with no uv dependency). The PEP 723 block above still lets you run it under
# `uv run --script redact_secrets.py` if you prefer an isolated interpreter.
"""Check or redact secrets in agent artifact Markdown.

Index modes select changed stage-0 regular blobs from Git's effective index.
Gitleaks retains its staged-diff semantics: newly staged reachability is gated,
while unchanged parent lines/history remain remediation-audit scope. Structural
private-key records are intentionally checked against each selected full blob.
Check mode accepts canonical and temporary commit indexes. Mutation requires a
caller-owned noncanonical ``GIT_INDEX_FILE`` inside an isolated transaction.

Usage:
    ./redact_secrets.py --check-index
    ./redact_secrets.py --fix-index --files .claude/plans/exact.md
    ./redact_secrets.py --fix                 # edits worktree; never stages
    ./redact_secrets.py --working-dir
    ./redact_secrets.py --paths .specstory/history .claude/plans
    ./redact_secrets.py --fix --legacy
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATHS = [
    ".specstory/history",
    ".claude/plans",
    ".cursor/plans",
    ".cursor/rules",
    ".opencode/plans",
    ".specify",
    ".codex",
]

# Set once by ``main`` after resolving a repository-root-scoped config. Keeping
# the scanner config outside the inherited environment prevents a caller's Git
# routing variables from silently moving the staged scan to another repository.
GITLEAKS_CONFIG: Path | None = None
REPOSITORY_ROOT: Path | None = None
GIT_ENVIRONMENT: dict[str, str] | None = None
GIT_ROUTING_VARIABLES = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
    "GIT_PREFIX",
    "GIT_CEILING_DIRECTORIES",
    "GIT_CONFIG",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM",
    "GIT_CONFIG_NOSYSTEM",
)
MAX_REPORT_BYTES = 16 * 1024 * 1024
MAX_PRIVATE_KEY_RECORD_CHARS = 4 * 1024 * 1024
MAX_PUTTY_DATA_LINES = 4096
MAX_PUTTY_LINE_CHARS = 8192


class ScannerError(RuntimeError):
    """The external scanner did not produce a trustworthy result."""


class IndexOperationError(RuntimeError):
    """Git could not safely read or update the effective index."""


class PathSafetyError(ValueError):
    """A path is not an exact, safe repository-relative path."""


class TransformationError(RuntimeError):
    """Content could not be transformed without potentially retaining data."""


class IncompletePrivateKeyError(TransformationError):
    """A header is followed by plausible data but no complete bounded record."""

    def __init__(self, count: int):
        super().__init__("incomplete private-key record")
        self.count = count


def _safe_object_id(value: str) -> bool:
    """Return whether ``value`` is exactly a SHA-1 or SHA-256 Git object id."""
    return re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value) is not None


def _validate_effective_index(raw_index: str, invocation_root: Path) -> str:
    """Normalize one caller-supplied temporary index without following links."""
    if not raw_index or len(raw_index) > 4096 or "\x00" in raw_index:
        raise IndexOperationError("effective Git index path is not safe")
    candidate = Path(raw_index)
    if not candidate.is_absolute():
        candidate = invocation_root / candidate
    candidate = Path(os.path.abspath(os.fspath(candidate)))
    _inspect_nonsymlink_components(candidate, allow_missing=False)
    try:
        info = os.lstat(candidate)
    except OSError as exc:
        raise IndexOperationError("effective Git index could not be inspected") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise IndexOperationError("effective Git index must be a regular nonsymlink file")
    if info.st_uid != os.getuid():
        raise IndexOperationError("effective Git index is not owned by the current user")
    return os.fspath(candidate)


def _prepare_repository(config_argument: str | None) -> None:
    """Anchor Git at the physical root and remove inherited routing controls."""
    global GITLEAKS_CONFIG, REPOSITORY_ROOT, GIT_ENVIRONMENT

    invocation_root = Path.cwd()
    environment = os.environ.copy()
    raw_index = environment.pop("GIT_INDEX_FILE", None)
    for variable in GIT_ROUTING_VARIABLES:
        environment.pop(variable, None)
    # All paths below have already passed exact path validation. This blocks Git
    # magic pathspec interpretation in every subprocess, including scanner fakes.
    environment["GIT_LITERAL_PATHSPECS"] = "1"
    if raw_index is not None:
        environment["GIT_INDEX_FILE"] = _validate_effective_index(
            raw_index, invocation_root
        )

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=invocation_root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError as exc:
        raise IndexOperationError("Git repository root could not be identified") from exc
    if result.returncode != 0:
        raise IndexOperationError("Git repository root could not be identified")
    try:
        root_text = result.stdout.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise IndexOperationError("Git repository root is not valid UTF-8") from exc
    if not root_text or "\x00" in root_text:
        raise IndexOperationError("Git repository root could not be identified")
    root = Path(root_text)
    _inspect_nonsymlink_components(root, allow_missing=False)
    try:
        root_info = os.lstat(root)
    except OSError as exc:
        raise IndexOperationError("Git repository root could not be inspected") from exc
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise IndexOperationError("Git repository root is unsafe")

    requested = config_argument or ".gitleaks.toml"
    if not requested or len(requested) > 4096 or "\x00" in requested:
        raise ScannerError("gitleaks config path is not safe")
    config = Path(requested)
    if not config.is_absolute():
        config = root / config
    config = Path(os.path.abspath(os.fspath(config)))
    if config_argument is not None or config.exists():
        _inspect_nonsymlink_components(config, allow_missing=False)
        try:
            config_info = os.lstat(config)
        except OSError as exc:
            raise ScannerError("gitleaks config could not be inspected") from exc
        if stat.S_ISLNK(config_info.st_mode) or not stat.S_ISREG(config_info.st_mode):
            raise ScannerError("gitleaks config must be a regular nonsymlink file")
        GITLEAKS_CONFIG = config
    else:
        GITLEAKS_CONFIG = None

    REPOSITORY_ROOT = root
    GIT_ENVIRONMENT = environment
    os.chdir(root)


def _inspect_nonsymlink_components(path: Path, allow_missing: bool) -> bool:
    """Return existence only after proving every present component nonsymlink."""
    absolute = Path(os.path.abspath(os.fspath(path)))
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            if allow_missing:
                return False
            raise PathSafetyError("working-tree path could not be inspected")
        except OSError as exc:
            raise PathSafetyError("working-tree path could not be inspected") from exc
        if stat.S_ISLNK(info.st_mode):
            raise PathSafetyError("working-tree roots and files must not be symlinks")
    return True


def _reject_symlink_components(path: Path) -> None:
    """Reject symlinks in an existing path without resolving through them."""
    _inspect_nonsymlink_components(path, allow_missing=False)


def _open_parent_directory_nofollow(path: Path) -> tuple[int, str]:
    """Open every parent component by descriptor without traversing symlinks."""
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or directory is None:
        raise PathSafetyError("this platform cannot safely reject symlink targets")
    absolute = Path(os.path.abspath(os.fspath(path)))
    if not absolute.name:
        raise PathSafetyError("working-tree target must name a file")
    flags = os.O_RDONLY | directory | nofollow
    try:
        parent_descriptor = os.open(absolute.anchor, os.O_RDONLY | directory)
    except OSError as exc:
        raise PathSafetyError("working-tree root could not be opened safely") from exc
    try:
        for component in absolute.parts[1:-1]:
            next_descriptor = os.open(
                component, flags, dir_fd=parent_descriptor
            )
            os.close(parent_descriptor)
            parent_descriptor = next_descriptor
    except OSError as exc:
        os.close(parent_descriptor)
        raise PathSafetyError("working-tree parent contains an unsafe component") from exc
    return parent_descriptor, absolute.name


def _read_regular_bytes(path: Path) -> bytes:
    """Read one proven regular file through no-follow directory descriptors."""
    parent_descriptor, name = _open_parent_directory_nofollow(path)
    descriptor = -1
    try:
        before = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise PathSafetyError("working-tree targets must be regular files")
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=parent_descriptor,
        )
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (
            opened.st_dev,
            opened.st_ino,
        ) != (before.st_dev, before.st_ino):
            raise PathSafetyError("working-tree file changed while it was opened")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            return stream.read()
    except PathSafetyError:
        raise
    except OSError as exc:
        raise PathSafetyError("working-tree file could not be opened safely") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_descriptor)


def read_text(path: Path) -> str:
    """Read a nonsymlink regular file while preserving invalid UTF-8 bytes."""
    return _decode_content(_read_regular_bytes(path))


def _read_named_regular_bytes(
    parent_descriptor: int, name: str, expected_identity: tuple[int, int] | None = None
) -> tuple[bytes, tuple[int, int]]:
    """Read one directory-entry name without following it through a link."""
    descriptor = -1
    try:
        before = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        identity = (before.st_dev, before.st_ino)
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or (expected_identity is not None and identity != expected_identity)
        ):
            raise PathSafetyError("working-tree generation changed unexpectedly")
        descriptor = os.open(
            name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_descriptor
        )
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != identity:
            raise PathSafetyError("working-tree generation changed while it was opened")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read()
        if (opened.st_dev, opened.st_ino) != identity:
            raise PathSafetyError("working-tree generation changed while it was read")
        return data, identity
    except PathSafetyError:
        raise
    except OSError as exc:
        raise PathSafetyError("working-tree generation could not be read safely") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _reserve_owned_name(parent_descriptor: int, prefix: str) -> str:
    """Reserve a random private sibling name, then return it for atomic rename."""
    for _attempt in range(128):
        candidate = prefix + secrets.token_hex(12)
        descriptor = -1
        try:
            descriptor = os.open(
                candidate,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent_descriptor,
            )
        except FileExistsError:
            continue
        except OSError as exc:
            raise PathSafetyError("could not reserve a private redaction file") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        return candidate
    raise PathSafetyError("could not reserve a private redaction file")


def write_text(path: Path, content: str, expected_data: bytes | None = None) -> None:
    """Replace a stable regular file through an owned old-generation backup.

    The pathname is first renamed to a same-directory backup. A writer holding
    its old descriptor then appends to the backup rather than disappearing under
    an atomic replacement. Any observed mismatch restores that old generation;
    a new pathname created after the rename is never overwritten.
    """
    parent_descriptor, name = _open_parent_directory_nofollow(path)
    descriptor = -1
    temporary_name = ""
    temporary_identity: tuple[int, int] | None = None
    backup_name = ""
    backup_identity: tuple[int, int] | None = None
    backup_active = False

    def restore_backup_if_live_name_is_absent() -> None:
        nonlocal backup_active
        if not backup_active or backup_identity is None:
            return
        try:
            os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
            return
        except FileNotFoundError:
            pass
        except OSError:
            return
        try:
            saved = os.stat(
                backup_name, dir_fd=parent_descriptor, follow_symlinks=False
            )
            if (
                stat.S_ISREG(saved.st_mode)
                and (saved.st_dev, saved.st_ino) == backup_identity
            ):
                os.replace(
                    backup_name,
                    name,
                    src_dir_fd=parent_descriptor,
                    dst_dir_fd=parent_descriptor,
                )
                backup_active = False
        except OSError:
            pass

    try:
        initial_data, before_identity = _read_named_regular_bytes(parent_descriptor, name)
        if expected_data is None:
            expected_data = initial_data
        encoded = _encode_content(content)

        backup_name = _reserve_owned_name(parent_descriptor, ".agent-history-redact-backup-")
        # Replace the harmless reservation, never hard-link the live path. This
        # makes writes through an existing descriptor observable on the backup.
        os.replace(
            name,
            backup_name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        backup_active = True
        saved_data, backup_identity = _read_named_regular_bytes(
            parent_descriptor, backup_name, before_identity
        )
        if saved_data != expected_data:
            # A replacement or append won before our rename. The live name is
            # still absent, so exact restoration is lossless.
            os.replace(
                backup_name,
                name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
            backup_active = False
            raise PathSafetyError("working-tree file changed before replacement")

        temporary_name = _reserve_owned_name(parent_descriptor, ".agent-history-redact-")
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW,
            dir_fd=parent_descriptor,
        )
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.fchmod(descriptor, stat.S_IMODE(os.stat(
            backup_name, dir_fd=parent_descriptor, follow_symlinks=False
        ).st_mode))
        temporary_info = os.fstat(descriptor)
        temporary_identity = (temporary_info.st_dev, temporary_info.st_ino)

        # An uncooperative writer that opens/replaces the pathname after the
        # rename owns that new generation. Preserve it and leave the old backup
        # for recovery instead of overwriting either set of bytes.
        try:
            intervening = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            intervening = None
        if intervening is not None:
            raise PathSafetyError("working-tree path was recreated during replacement")
        named_temporary = os.stat(
            temporary_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (named_temporary.st_dev, named_temporary.st_ino) != temporary_identity:
            raise PathSafetyError("temporary redaction file changed unexpectedly")
        os.replace(
            temporary_name,
            name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        temporary_name = ""

        saved_after, _ = _read_named_regular_bytes(
            parent_descriptor, backup_name, backup_identity
        )
        if saved_after != expected_data:
            # A held descriptor wrote after the rename. Restore its generation
            # over our known temporary replacement, retaining the writer bytes.
            current = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
            if (
                stat.S_ISLNK(current.st_mode)
                or not stat.S_ISREG(current.st_mode)
                or (current.st_dev, current.st_ino) != temporary_identity
            ):
                raise PathSafetyError("working-tree path changed during replacement")
            os.replace(
                backup_name,
                name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
            backup_active = False
            raise PathSafetyError("working-tree file changed during replacement")

        os.unlink(backup_name, dir_fd=parent_descriptor)
        backup_active = False
    except PathSafetyError:
        restore_backup_if_live_name_is_absent()
        raise
    except OSError as exc:
        restore_backup_if_live_name_is_absent()
        raise PathSafetyError("working-tree file could not be replaced safely") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_name and temporary_identity is not None:
            try:
                temporary = os.stat(
                    temporary_name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
            except OSError:
                pass
            else:
                if (temporary.st_dev, temporary.st_ino) == temporary_identity:
                    try:
                        os.unlink(temporary_name, dir_fd=parent_descriptor)
                    except OSError:
                        pass
        # A post-rename failure deliberately retains the owned backup. It may
        # receive a delayed write through a recorder's already-open descriptor;
        # unlinking it here would lose that writer data. The caller receives a
        # failure and can inspect/recover the sibling under quiescence.
        os.close(parent_descriptor)


def _decode_content(data: bytes) -> str:
    # Git permits NUL-containing and non-UTF-8 blobs, and a recorder can flush a
    # partial write into one. `surrogateescape` round-trips those bytes exactly,
    # and every replacement below is range-based, so decoding cannot rewrite a
    # byte we did not match. Refusing here instead would abort sanitation of an
    # otherwise redactable artifact. Scanner coverage is unaffected: gitleaks
    # reads the staged diff itself rather than this decoded string.
    return data.decode("utf-8", errors="surrogateescape")


def _encode_content(content: str) -> bytes:
    # Exact inverse of `_decode_content`; lossless for arbitrary blob bytes.
    return content.encode("utf-8", errors="surrogateescape")


def _load_gitleaks_report(report_path: Path) -> list[dict]:
    """Parse a bounded report; an empty file is gitleaks' clean result."""
    try:
        size = report_path.stat().st_size
        if size == 0:
            return []
        if size > MAX_REPORT_BYTES:
            raise ScannerError("gitleaks report exceeded the safety limit")
        raw = report_path.read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except ScannerError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ScannerError("gitleaks returned malformed JSON") from exc

    if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
        raise ScannerError("gitleaks returned non-list JSON")
    return data


def _run_gitleaks(
    command: list[str], input_data: bytes | None = None
) -> list[dict]:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as report:
        report_path = Path(report.name)

    cmd = command + [
        "--report-format",
        "json",
        "--report-path",
        str(report_path),
        "--exit-code",
        "0",
    ]
    if GITLEAKS_CONFIG is not None:
        cmd.extend(["--config", os.fspath(GITLEAKS_CONFIG)])

    try:
        try:
            result = subprocess.run(
                cmd,
                input=input_data,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=REPOSITORY_ROOT,
                env=GIT_ENVIRONMENT,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ScannerError("gitleaks is not installed") from exc
        except OSError as exc:
            raise ScannerError("gitleaks could not be executed") from exc
        if result.returncode != 0:
            raise ScannerError(
                f"gitleaks execution failed with status {result.returncode}"
            )
        return _load_gitleaks_report(report_path)
    finally:
        try:
            report_path.unlink()
        except FileNotFoundError:
            pass


def run_gitleaks_staged() -> list[dict]:
    """Gate newly staged reachability with ``gitleaks git --staged``.

    This deliberately preserves commit-scoped config/ignore and staged-diff
    behavior. It is not a scan of unchanged parent lines or repository history;
    newly added files still appear as full additions. Missing tools, nonzero
    execution, and invalid reports raise instead of being misreported as clean.
    """
    return _run_gitleaks(["gitleaks", "git", "--staged"])


def run_gitleaks_worktree_files(markdown_files: Sequence[Path]) -> list[dict]:
    """Scan proven nonsymlink Markdown files through ``gitleaks stdin``."""
    findings: list[dict] = []
    for md_file in markdown_files:
        try:
            data = _read_regular_bytes(md_file)
        except PathSafetyError:
            raise
        except OSError as exc:
            raise ScannerError("a working-directory target could not be read") from exc
        file_findings = _run_gitleaks(["gitleaks", "stdin"], input_data=data)
        for finding in file_findings:
            copied = dict(finding)
            copied["File"] = str(md_file)
            findings.append(copied)
    return findings


def run_gitleaks_workdir(target_path: str) -> list[dict]:
    """Compatibility helper for one validated, nonsymlink target root."""
    existing, _missing, markdown_files = _safe_worktree_targets([target_path])
    if not existing:
        return []
    return run_gitleaks_worktree_files(markdown_files)


def redact_secret(secret: str, keep_chars: int = 3) -> str:
    """Legacy first/last-N redaction retained for old on-disk placeholders.

    CLI diagnostics never call this function and never expose a fingerprint.
    """
    if len(secret) <= keep_chars * 2 + 3:
        return "[REDACTED]"
    return f"{secret[:keep_chars]}...{secret[-keep_chars:]}"


_RULE_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _rule_id_contains_secret(slug: str, secret: str | None) -> bool:
    """Do not turn a hostile scanner rule id into a new secret-bearing output."""
    if not secret:
        return False
    raw = secret.casefold()
    normalized = _RULE_SLUG_RE.sub("-", raw).strip("-")
    compact = re.sub(r"[^a-z0-9]", "", raw)
    candidates = (raw, normalized, compact)
    compact_slug = re.sub(r"[^a-z0-9]", "", slug)
    return any(
        candidate
        and (candidate in slug or candidate in compact_slug)
        for candidate in candidates
    )


def redaction_placeholder(rule_id: str, secret: str | None = None) -> str:
    """Return a secret-free SpecStory-compatible redaction sentinel.

    A scanner's rule id is normally safe metadata, but it is scanner-controlled
    input. If it contains the matched secret, using it verbatim would re-publish
    the secret in the replacement itself.
    """
    slug = _RULE_SLUG_RE.sub("-", rule_id or "").strip("-").lower()
    if _rule_id_contains_secret(slug, secret):
        slug = "secret"
    return f"[REDACTED:{slug or 'secret'}]"


def _trace_replace(content, start, end, replacement, trace):
    if trace is not None:
        if len(trace) >= 4096:
            raise TransformationError("transformation evidence exceeds supported bound")
        trace.append({"start": len(_encode_content(content[:start])),
                      "end": len(_encode_content(content[:end])),
                      "before": base64.b64encode(_encode_content(content[start:end])).decode("ascii"),
                      "after": base64.b64encode(_encode_content(replacement)).decode("ascii")})
    return content[:start] + replacement + content[end:]


def redact_content(
    content: str,
    findings: Sequence[dict],
    legacy: bool = False,
    strict: bool = False,
    trace: list | None = None,
) -> str:
    """Purely replace scanner-reported values, retaining every other byte.

    ``strict`` is used for index blobs: every finding must identify non-empty
    bytes present in that exact blob. The compatibility worktree fixer keeps
    ``strict=False`` because a user may already have edited the live file after
    staging it.
    """
    replacements: dict[str, str] = {}
    present_in_original: dict[str, bool] = {}

    for finding in findings:
        secret = finding.get("Secret")
        if not isinstance(secret, str) or not secret:
            raise TransformationError("scanner finding omitted replaceable bytes")
        present_in_original[secret] = secret in content
        if strict and not present_in_original[secret]:
            raise TransformationError("scanner finding did not match the index blob")
        if not present_in_original[secret]:
            continue
        replacement = (
            redact_secret(secret)
            if legacy
            else redaction_placeholder(str(finding.get("RuleID") or ""), secret)
        )
        prior = replacements.get(secret)
        # One byte sequence can match more than one gitleaks rule. Any complete
        # replacement removes it; choose a stable label rather than failing or
        # depending on scanner result order.
        replacements[secret] = replacement if prior is None else min(prior, replacement)

    transformed = content
    for secret in sorted(replacements, key=len, reverse=True):
        if trace is None:
            transformed = transformed.replace(secret, replacements[secret])
        else:
            # Record the actual existing replacement semantics, including every
            # occurrence; offsets refer to the successive exact byte generation.
            offsets = []
            cursor = 0
            while True:
                start = transformed.find(secret, cursor)
                if start < 0:
                    break
                offsets.append(start)
                cursor = start + len(secret)
            for start in reversed(offsets):
                transformed = _trace_replace(transformed, start, start + len(secret), replacements[secret], trace)
    if any(secret in transformed for secret in replacements):
        raise TransformationError("reported bytes remained after redaction")
    return transformed


def redact_file(file_path: Path, findings: list[dict], legacy: bool = False) -> bool:
    """Compatibility wrapper that redacts one stable working-tree file in place."""
    original_data = _read_regular_bytes(file_path)
    content = _decode_content(original_data)
    try:
        resolved = file_path.resolve()
        file_findings = [
            finding
            for finding in findings
            if isinstance(finding.get("File"), str)
            and Path(finding["File"]).resolve() == resolved
        ]
    except OSError as exc:
        raise TransformationError("working-tree path could not be resolved") from exc
    transformed = redact_content(content, file_findings, legacy=legacy, strict=False)
    if transformed == content:
        return False
    write_text(file_path, transformed, expected_data=original_data)
    return True


def _validate_repo_path(path: str) -> str:
    if not isinstance(path, str) or not path or len(path) > 4096:
        raise PathSafetyError("path must be a bounded repository-relative path")
    if path.startswith("/") or path.endswith("/") or "\x00" in path:
        raise PathSafetyError("path must be an exact repository-relative file")
    if any(ord(char) < 32 or ord(char) == 127 for char in path):
        raise PathSafetyError("path contains unsafe control bytes")
    if any(0xD800 <= ord(char) <= 0xDFFF for char in path):
        raise PathSafetyError("path is not valid UTF-8")
    components = path.split("/")
    if any(component in ("", ".", "..") for component in components):
        raise PathSafetyError("path contains an unsafe component")
    return path


def _normalize_prefix(prefix: str) -> str:
    normalized = prefix.rstrip("/")
    return _validate_repo_path(normalized)


def _path_has_prefix(path: str, prefix: str) -> bool:
    return path.startswith(prefix + "/")


def _is_artifact_markdown(path: str, prefixes: Sequence[str]) -> bool:
    return path.endswith(".md") and any(
        _path_has_prefix(path, prefix) for prefix in prefixes
    )


def _safe_worktree_targets(
    prefixes: Sequence[str],
) -> tuple[list[str], list[str], list[Path]]:
    """Validate roots and enumerate Markdown without traversing any symlink."""
    normalized = [_normalize_prefix(prefix) for prefix in prefixes]
    existing: list[str] = []
    missing: list[str] = []
    markdown_files: list[Path] = []
    seen: set[str] = set()

    for prefix in normalized:
        root = Path(prefix)
        if not _inspect_nonsymlink_components(root, allow_missing=True):
            missing.append(prefix)
            continue
        try:
            root_info = os.lstat(root)
        except OSError as exc:
            raise PathSafetyError("working-tree root could not be inspected") from exc
        if not stat.S_ISDIR(root_info.st_mode):
            raise PathSafetyError("working-tree roots must be nonsymlink directories")
        existing.append(prefix)

        try:
            walker = os.walk(root, topdown=True, followlinks=False)
            for directory, child_dirs, child_files in walker:
                directory_path = Path(directory)
                directory_info = os.lstat(directory_path)
                if stat.S_ISLNK(directory_info.st_mode) or not stat.S_ISDIR(
                    directory_info.st_mode
                ):
                    raise PathSafetyError(
                        "working-tree traversal encountered an unsafe directory"
                    )

                child_dirs.sort()
                child_files.sort()
                for name in child_dirs:
                    child = directory_path / name
                    child_info = os.lstat(child)
                    if stat.S_ISLNK(child_info.st_mode):
                        raise PathSafetyError(
                            "working-tree roots and files must not be symlinks"
                        )
                    if not stat.S_ISDIR(child_info.st_mode):
                        raise PathSafetyError(
                            "working-tree traversal encountered an unsafe directory"
                        )
                for name in child_files:
                    child = directory_path / name
                    child_info = os.lstat(child)
                    if stat.S_ISLNK(child_info.st_mode):
                        raise PathSafetyError(
                            "working-tree roots and files must not be symlinks"
                        )
                    if child.suffix != ".md":
                        continue
                    if not stat.S_ISREG(child_info.st_mode):
                        raise PathSafetyError(
                            "working-tree Markdown targets must be regular files"
                        )
                    rendered = str(child)
                    if rendered not in seen:
                        seen.add(rendered)
                        markdown_files.append(child)
        except PathSafetyError:
            raise
        except OSError as exc:
            raise PathSafetyError(
                "working-directory targets could not be enumerated safely"
            ) from exc

    return existing, missing, markdown_files


def _validate_selected_worktree_files(
    files: Sequence[Path], prefixes: Sequence[str]
) -> list[Path]:
    validated: list[Path] = []
    for path in files:
        rendered = _validate_repo_path(str(path))
        if not _is_artifact_markdown(rendered, prefixes):
            raise PathSafetyError("working-tree file is outside configured roots")
        _reject_symlink_components(path)
        try:
            info = os.lstat(path)
        except OSError as exc:
            raise PathSafetyError("working-tree target is missing") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise PathSafetyError(
                "working-tree targets must be nonsymlink regular files"
            )
        validated.append(path)
    return validated


def filter_by_prefixes(findings: list[dict], prefixes: list[str]) -> list[dict]:
    """Keep findings below component-anchored prefixes (never lookalikes)."""
    normalized = [_normalize_prefix(prefix) for prefix in prefixes]
    filtered = []
    for finding in findings:
        path = finding.get("File")
        if isinstance(path, str) and any(
            _path_has_prefix(path, prefix) for prefix in normalized
        ):
            filtered.append(finding)
    return filtered


# Header patterns mirror detect-private-key without embedding any one literal
# blacklist entry in this shipped source. Version digits remain regex tokens so
# compiled bytecode is safe too.
_PRIVATE_KEY_HEADER_RE = re.compile(
    r"BEGIN [A-Z0-9 ]*PRIVATE KEY"
    r"|PuTTY-User-Key-File-\d"
    r"|BEGIN OpenVPN Static key V\d"
)
_PEM_BEGIN_RE = re.compile(
    r"-----BEGIN (?P<label>(?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?)-----"
)
_OPENVPN_BEGIN_RE = re.compile(
    r"-----BEGIN OpenVPN Static key V(?P<version>\d+)-----"
)
_PUTTY_HEADER_LINE_RE = re.compile(
    r"PuTTY-User-Key-File-\d+:[ \t]*[^\s:][^\r\n]{0,127}"
)
_PUTTY_COUNT_RE = re.compile(r"([0-9]{1,6})")
_PUTTY_METADATA_RE = re.compile(
    r"(?:Key-Derivation|Argon2-[A-Za-z]+):[^\r\n]{0,256}"
)
_PUTTY_MAC_RE = re.compile(r"Private-MAC:[ \t]*[0-9A-Fa-f]{16,256}")
# A complete *line* is a credible fragment after a private-key header when it is
# either a long encoded run (>=16 characters) or a shorter run closed by explicit
# base64 padding -- `=` padding is itself an encoded-data signal, so a truncated
# record like `QUJDRA==` must stay fail-closed. Short ordinary words (including
# four-character base64-looking prose) carry neither signal and therefore do not
# make an isolated header unredactable.
_PRIVATE_DATA_LINE_RE = re.compile(
    r"(?:[A-Za-z0-9+/]{16,}={0,2}|[A-Za-z0-9+/]{4,}={1,2}|[0-9A-Fa-f]{16,})"
)
_PRIVATE_STRUCTURE_LINE_RE = re.compile(
    r"(?:Encryption|Comment|Public-Lines|Private-Lines|Private-MAC|"
    r"Key-Derivation|Argon2-[A-Za-z]+|Proc-Type|DEK-Info):"
)
_PRIVATE_FOOTER_LINE_RE = re.compile(r"-{5}END [^\r\n]{1,128}-{5}")
MAX_PRIVATE_KEY_LOOKAHEAD_LINES = 8


@dataclass(frozen=True)
class PrivateKeyRange:
    start: int
    end: int
    kind: str


@dataclass(frozen=True)
class PrivateKeyAnalysis:
    complete_ranges: tuple[PrivateKeyRange, ...]
    isolated_header_ranges: tuple[PrivateKeyRange, ...]
    incomplete_headers: int

    @property
    def has_findings(self) -> bool:
        return bool(
            self.complete_ranges
            or self.isolated_header_ranges
            or self.incomplete_headers
        )


def _bounded_footer_range(
    content: str,
    start: int,
    body_start: int,
    footer: str,
    kind: str,
) -> PrivateKeyRange | None:
    limit = min(len(content), start + MAX_PRIVATE_KEY_RECORD_CHARS)
    footer_start = content.find(footer, body_start, limit)
    if footer_start < 0:
        return None
    return PrivateKeyRange(start, footer_start + len(footer), kind)


def _pem_ranges(content: str) -> list[PrivateKeyRange]:
    ranges = []
    for match in _PEM_BEGIN_RE.finditer(content):
        footer = "-----END " + match.group("label") + "-----"
        found = _bounded_footer_range(
            content, match.start(), match.end(), footer, "PEM"
        )
        if found is not None:
            ranges.append(found)
    return ranges


def _openvpn_ranges(content: str) -> list[PrivateKeyRange]:
    ranges = []
    for match in _OPENVPN_BEGIN_RE.finditer(content):
        footer = (
            "-----END OpenVPN Static key V" + match.group("version") + "-----"
        )
        found = _bounded_footer_range(
            content, match.start(), match.end(), footer, "OpenVPN"
        )
        if found is not None:
            ranges.append(found)
    return ranges


def _strip_line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return line[:-2]
    if line.endswith(("\n", "\r")):
        return line[:-1]
    return line


def _putty_count(line: str, field: str) -> int | None:
    prefix = field + ":"
    if not line.startswith(prefix):
        return None
    match = _PUTTY_COUNT_RE.fullmatch(line[len(prefix) :].strip())
    if match is None:
        return None
    count = int(match.group(1))
    if count > MAX_PUTTY_DATA_LINES:
        return None
    return count


def _putty_record_end(lines: Sequence[str], start: int) -> int | None:
    """Return the exclusive ending line for one structurally complete PPK."""
    if not _PUTTY_HEADER_LINE_RE.fullmatch(_strip_line_ending(lines[start])):
        return None
    cursor = start + 1
    if cursor >= len(lines) or not _strip_line_ending(lines[cursor]).startswith(
        "Encryption:"
    ):
        return None
    cursor += 1
    if cursor >= len(lines) or not _strip_line_ending(lines[cursor]).startswith(
        "Comment:"
    ):
        return None
    cursor += 1
    if cursor >= len(lines):
        return None

    public_count = _putty_count(_strip_line_ending(lines[cursor]), "Public-Lines")
    if public_count is None:
        return None
    cursor += 1
    if cursor + public_count > len(lines):
        return None
    if any(
        not _strip_line_ending(line)
        or len(_strip_line_ending(line)) > MAX_PUTTY_LINE_CHARS
        for line in lines[cursor : cursor + public_count]
    ):
        return None
    cursor += public_count

    metadata_count = 0
    while cursor < len(lines) and not _strip_line_ending(lines[cursor]).startswith(
        "Private-Lines:"
    ):
        metadata = _strip_line_ending(lines[cursor])
        if metadata_count >= 16 or _PUTTY_METADATA_RE.fullmatch(metadata) is None:
            return None
        metadata_count += 1
        cursor += 1
    if cursor >= len(lines):
        return None

    private_count = _putty_count(_strip_line_ending(lines[cursor]), "Private-Lines")
    if private_count is None:
        return None
    cursor += 1
    if cursor + private_count > len(lines):
        return None
    if any(
        not _strip_line_ending(line)
        or len(_strip_line_ending(line)) > MAX_PUTTY_LINE_CHARS
        for line in lines[cursor : cursor + private_count]
    ):
        return None
    cursor += private_count
    if cursor >= len(lines) or _PUTTY_MAC_RE.fullmatch(
        _strip_line_ending(lines[cursor])
    ) is None:
        return None
    return cursor + 1


def _putty_ranges(content: str) -> list[PrivateKeyRange]:
    lines = content.splitlines(keepends=True)
    offsets = []
    offset = 0
    for line in lines:
        offsets.append(offset)
        offset += len(line)

    ranges = []
    for line_number, line in enumerate(lines):
        if _PUTTY_HEADER_LINE_RE.fullmatch(_strip_line_ending(line)) is None:
            continue
        end_line = _putty_record_end(lines, line_number)
        if end_line is None:
            continue
        last_line = end_line - 1
        end = offsets[last_line] + len(_strip_line_ending(lines[last_line]))
        if end - offsets[line_number] <= MAX_PRIVATE_KEY_RECORD_CHARS:
            ranges.append(PrivateKeyRange(offsets[line_number], end, "PuTTY"))
    return ranges


def _unwrap_record_line(line: str) -> str:
    stripped = _strip_line_ending(line).strip()
    while stripped.startswith(">"):
        stripped = stripped[1:].lstrip()
    return stripped


def _looks_like_private_data_line(line: str) -> bool:
    stripped = _unwrap_record_line(line)
    if not stripped:
        return False
    if _PRIVATE_STRUCTURE_LINE_RE.match(stripped) is not None:
        return True
    if _PRIVATE_FOOTER_LINE_RE.fullmatch(stripped) is not None:
        return True
    return _PRIVATE_DATA_LINE_RE.fullmatch(stripped) is not None


def _header_has_plausible_payload(content: str, header: re.Match[str]) -> bool:
    """Classify only immediate bounded record-like data after one header.

    A bare token copied into prose or test output is safely replaceable. Once a
    contiguous next line looks encoded or structured, removing only the header
    could strand private bytes, so the record remains fail-closed.
    """
    line_end = content.find("\n", header.end())
    if line_end < 0:
        line_end = len(content)
    suffix = content[header.end() : line_end]
    # Ignore only punctuation completing the header itself. The remaining text
    # must be one full encoded payload, not a word embedded in ordinary prose.
    same_line = suffix.strip()
    same_line = re.sub(r"^[`~:;,!.?()\[\]{}<>-]+", "", same_line).strip()
    same_line = re.sub(r"[`~:;,!.?()\[\]{}<>-]+$", "", same_line).strip()
    if _PRIVATE_DATA_LINE_RE.fullmatch(same_line) is not None:
        return True

    lookahead = content[line_end + (line_end < len(content)) :].splitlines(
        keepends=True
    )
    wrappers_seen = 0
    for line in lookahead[:MAX_PRIVATE_KEY_LOOKAHEAD_LINES]:
        stripped = _unwrap_record_line(line)
        if not stripped or stripped in ("```", "~~~"):
            wrappers_seen += 1
            if wrappers_seen > 3:
                return False
            continue
        return _looks_like_private_data_line(line)
    return False


def analyze_private_key_content(content: str) -> PrivateKeyAnalysis:
    """Classify complete, safely isolated, and plausibly truncated records."""
    headers = list(_PRIVATE_KEY_HEADER_RE.finditer(content))
    candidates = sorted(
        _pem_ranges(content) + _openvpn_ranges(content) + _putty_ranges(content),
        key=lambda item: (item.start, item.end),
    )

    complete = []
    for candidate in candidates:
        contained_headers = [
            header
            for header in headers
            if candidate.start <= header.start() < candidate.end
        ]
        if len(contained_headers) != 1:
            continue
        if complete and candidate.start < complete[-1].end:
            continue
        complete.append(candidate)

    isolated = []
    incomplete = 0
    for header in headers:
        if any(item.start <= header.start() < item.end for item in complete):
            continue
        if _header_has_plausible_payload(content, header):
            incomplete += 1
        else:
            isolated.append(
                PrivateKeyRange(header.start(), header.end(), "isolated-header")
            )
    return PrivateKeyAnalysis(tuple(complete), tuple(isolated), incomplete)


def private_key_descriptions(analysis: PrivateKeyAnalysis) -> list[str]:
    descriptions = []
    for kind in ("PEM", "OpenVPN", "PuTTY"):
        count = sum(item.kind == kind for item in analysis.complete_ranges)
        if count:
            descriptions.append(f"{count} complete {kind} private-key record(s)")
    if analysis.isolated_header_ranges:
        descriptions.append(
            f"{len(analysis.isolated_header_ranges)} isolated private-key header(s)"
        )
    if analysis.incomplete_headers:
        descriptions.append(
            f"{analysis.incomplete_headers} incomplete private-key header(s)"
        )
    return descriptions


def _private_key_placeholder(kind: str, legacy: bool) -> str:
    """One sentinel for every private-key finding, matching SpecStory's label.

    Complete records and isolated headers converge on the same placeholder so a
    re-run is a no-op and downstream diffs do not have to learn a second token.
    """
    del kind
    return "[REDACTED PEM PRIVKEY BLOCK]" if legacy else "[REDACTED:private-key]"


def redact_private_key_content(content: str, legacy: bool = False, trace: list | None = None) -> str:
    """Remove complete records/isolated tokens; reject plausible truncation."""
    analysis = analyze_private_key_content(content)
    if analysis.incomplete_headers:
        raise IncompletePrivateKeyError(analysis.incomplete_headers)
    ranges = sorted(
        analysis.complete_ranges + analysis.isolated_header_ranges,
        key=lambda item: (item.start, item.end),
    )
    if not ranges:
        return content

    # Build once from left to right. Repeated reverse slicing is quadratic for a
    # transcript containing many copied header snippets.
    parts: list[str] = []
    cursor = 0
    for item in ranges:
        if item.start < cursor or item.end < item.start:
            raise TransformationError("private-key ranges overlap unexpectedly")
        parts.append(content[cursor : item.start])
        parts.append(_private_key_placeholder(item.kind, legacy))
        cursor = item.end
    parts.append(content[cursor:])
    transformed = "".join(parts)
    if trace is not None:
        replay = content
        for item in reversed(ranges):
            replay = _trace_replace(replay, item.start, item.end, _private_key_placeholder(item.kind, legacy), trace)
        if replay != transformed:
            raise TransformationError("transformation evidence disagrees")

    if analyze_private_key_content(transformed).has_findings:
        raise TransformationError("private-key material remained after redaction")
    return transformed


def find_private_key_files(files: list[Path]) -> dict[Path, list[str]]:
    """Find complete, isolated, or incomplete key records without symlink I/O."""
    results: dict[Path, list[str]] = {}
    for path in files:
        if path.suffix != ".md":
            continue
        try:
            analysis = analyze_private_key_content(read_text(path))
        except OSError as exc:
            raise TransformationError("a working-tree target could not be read") from exc
        if analysis.has_findings:
            results[path] = private_key_descriptions(analysis)
    return results


def redact_private_keys(file_path: Path, legacy: bool = False) -> bool:
    """Redact complete records or isolated tokens in one stable regular file."""
    original_data = _read_regular_bytes(file_path)
    content = _decode_content(original_data)
    transformed = redact_private_key_content(content, legacy=legacy)
    if transformed == content:
        return False
    write_text(file_path, transformed, expected_data=original_data)
    return True


@dataclass(frozen=True)
class IndexBlob:
    path: str
    mode: str
    oid: str
    data: bytes


@dataclass(frozen=True)
class StagedArtifactAudit:
    """Staged-diff scanner findings plus intentional full-blob key structure."""

    entries: tuple[IndexBlob, ...]
    findings_by_path: dict[str, list[dict]]
    private_by_path: dict[str, PrivateKeyAnalysis]

    @property
    def has_findings(self) -> bool:
        return bool(self.findings_by_path or self.private_by_path)


def _run_git(args: Sequence[str], input_data: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(
            ["git"] + list(args),
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=REPOSITORY_ROOT,
            env=GIT_ENVIRONMENT,
            check=False,
        )
    except (FileNotFoundError, OSError) as exc:
        raise IndexOperationError("git could not be executed") from exc
    if result.returncode != 0:
        raise IndexOperationError("git index operation failed")
    return result.stdout


def _decode_git_path(raw: bytes) -> str:
    try:
        path = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PathSafetyError("index path is not valid UTF-8") from exc
    return _validate_repo_path(path)


def _parse_stage_record(record: bytes) -> tuple[str, str, int, str]:
    try:
        metadata, raw_path = record.split(b"\t", 1)
        raw_mode, raw_oid, raw_stage = metadata.split(b" ")
        mode = raw_mode.decode("ascii")
        oid = raw_oid.decode("ascii")
        stage = int(raw_stage.decode("ascii"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise IndexOperationError("git returned an invalid index record") from exc
    if re.fullmatch(r"[0-9]{6}", mode) is None or not _safe_object_id(oid):
        raise IndexOperationError("git returned an invalid index record")
    return mode, oid, stage, _decode_git_path(raw_path)


def _load_index_blob(path: str) -> IndexBlob:
    path = _validate_repo_path(path)
    output = _run_git(["ls-files", "--stage", "-z", "--", path])
    records = [record for record in output.split(b"\x00") if record]
    parsed = [_parse_stage_record(record) for record in records]
    if not parsed or any(item[3] != path for item in parsed):
        raise IndexOperationError("exact index path is not present")
    if len(parsed) != 1 or parsed[0][2] != 0:
        raise IndexOperationError("index path is unmerged")
    mode, oid, _stage, _parsed_path = parsed[0]
    if mode not in ("100644", "100755"):
        raise IndexOperationError("index path is not a regular blob")
    data = _run_git(["cat-file", "blob", oid])
    return IndexBlob(path=path, mode=mode, oid=oid, data=data)


def _reject_unmerged_artifacts(prefixes: Sequence[str]) -> None:
    output = _run_git(["ls-files", "--unmerged", "-z"])
    for record in (record for record in output.split(b"\x00") if record):
        _mode, _oid, _stage, path = _parse_stage_record(record)
        if _is_artifact_markdown(path, prefixes):
            raise IndexOperationError("an artifact index path is unmerged")


def list_staged_artifact_blobs(prefixes: Sequence[str]) -> list[IndexBlob]:
    """Enumerate changed artifact Markdown as stage-0 regular index blobs."""
    _reject_unmerged_artifacts(prefixes)
    output = _run_git(
        ["diff", "--cached", "--name-only", "-z", "--diff-filter=ACMRT", "--"]
    )
    paths = []
    seen = set()
    for raw_path in (part for part in output.split(b"\x00") if part):
        path = _decode_git_path(raw_path)
        if _is_artifact_markdown(path, prefixes) and path not in seen:
            seen.add(path)
            paths.append(path)
    return [_load_index_blob(path) for path in paths]


def _scanner_finding_path(finding: dict) -> str:
    path = finding.get("File")
    if not isinstance(path, str) or not path:
        raise ScannerError("gitleaks finding omitted its file path")
    path = path.removeprefix("./")
    try:
        return _validate_repo_path(path)
    except PathSafetyError as exc:
        raise ScannerError("gitleaks returned an unsafe file path") from exc


def _group_index_findings(
    findings: Sequence[dict],
    entries: Sequence[IndexBlob],
    prefixes: Sequence[str],
) -> dict[str, list[dict]]:
    candidate_paths = {entry.path for entry in entries}
    grouped: dict[str, list[dict]] = {}
    for finding in findings:
        path = _scanner_finding_path(finding)
        if not _is_artifact_markdown(path, prefixes):
            continue
        if path not in candidate_paths:
            raise ScannerError("gitleaks reported a non-candidate artifact path")
        grouped.setdefault(path, []).append(finding)
    return grouped


def audit_staged_reachability(prefixes: Sequence[str]) -> StagedArtifactAudit:
    """Audit newly staged scanner reachability and full-blob key structure."""
    entries = list_staged_artifact_blobs(prefixes)
    if not entries:
        return StagedArtifactAudit((), {}, {})
    findings = run_gitleaks_staged()
    grouped = _group_index_findings(findings, entries, prefixes)
    private: dict[str, PrivateKeyAnalysis] = {}
    for entry in entries:
        analysis = analyze_private_key_content(_decode_content(entry.data))
        if analysis.has_findings:
            private[entry.path] = analysis
    return StagedArtifactAudit(tuple(entries), grouped, private)


def audit_full_index() -> tuple[StagedArtifactAudit, list[dict]]:
    """V2 policy coverage: every regular stage-0 blob, not only added lines.

    Unknown modes, unmerged state, binary or oversized input fail explicitly;
    there is no silent scanner-success shortcut for an unscanned product file.
    Gitlinks are non-blob entries and explicitly recorded as outside this policy.
    """
    entries, grouped, private, coverage = [], {}, {}, []
    records = [record for record in _run_git(["ls-files", "--stage", "-z"]).split(b"\0") if record]
    if len(records) > 4096:
        raise ScannerError("full-index coverage exceeds supported entry bound")
    total = 0
    for record in records:
        mode, oid, stage, path = _parse_stage_record(record)
        if stage != 0:
            raise ScannerError("full-index coverage requires merged entries")
        if mode == "160000":
            coverage.append({"path": path, "mode": mode, "oid": oid, "status": "nonblob_gitlink"})
            continue
        if mode not in ("100644", "100755", "120000"):
            raise ScannerError("full-index coverage has an unsupported mode")
        size = int(_run_git(["cat-file", "-s", oid]).strip())
        total += size
        if size > 8 * 1024 * 1024 or total > 128 * 1024 * 1024:
            raise ScannerError("full-index coverage exceeds supported byte bound")
        data = _run_git(["cat-file", "blob", oid])
        if len(data) != size or b"\0" in data:
            raise ScannerError("full-index binary coverage is unsupported")
        entry = IndexBlob(path=path, mode=mode, oid=oid, data=data)
        findings = _run_gitleaks(["gitleaks", "stdin"], input_data=data)
        for finding in findings:
            copied = dict(finding)
            copied["File"] = path
            grouped.setdefault(path, []).append(copied)
        analysis = analyze_private_key_content(_decode_content(data))
        if analysis.has_findings:
            private[path] = analysis
        entries.append(entry)
        coverage.append({"path": path, "mode": mode, "oid": oid, "size": size,
                         "sha256": hashlib.sha256(data).hexdigest(), "status": "scanned_full_blob"})
    return StagedArtifactAudit(tuple(entries), grouped, private), coverage


def _write_index_blobs(replacements: Sequence[tuple[IndexBlob, bytes]]) -> None:
    updates = []
    for entry, data in replacements:
        raw_oid = _run_git(
            ["hash-object", "-w", "--stdin", "--no-filters"], input_data=data
        ).strip()
        try:
            oid = raw_oid.decode("ascii")
        except UnicodeDecodeError as exc:
            raise IndexOperationError("git returned an invalid object id") from exc
        if re.fullmatch(r"[0-9a-f]{40,64}", oid) is None:
            raise IndexOperationError("git returned an invalid object id")
        updates.append(
            entry.mode.encode("ascii")
            + b" "
            + oid.encode("ascii")
            + b"\t"
            + entry.path.encode("utf-8")
            + b"\x00"
        )
    if updates:
        _run_git(["update-index", "-z", "--index-info"], input_data=b"".join(updates))


def _safe_rule_id(value) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", value):
        return "unknown"
    return value


def _safe_line(value) -> str:
    if isinstance(value, int) and 0 < value <= 2**31 - 1:
        return str(value)
    return "?"


def _display_path(path: str) -> str:
    bounded = path if len(path) <= 512 else path[:509] + "..."
    return json.dumps(bounded, ensure_ascii=True)


def _report_staged_audit(audit: StagedArtifactAudit) -> None:
    scanner_count = sum(len(items) for items in audit.findings_by_path.values())
    private_count = sum(
        len(analysis.complete_ranges)
        + len(analysis.isolated_header_ranges)
        + analysis.incomplete_headers
        for analysis in audit.private_by_path.values()
    )
    if scanner_count:
        print(f"Found {scanner_count} newly staged scanner finding(s):")
        for path in sorted(audit.findings_by_path):
            for finding in audit.findings_by_path[path]:
                print(
                    "  path={} line={} rule={}".format(
                        _display_path(path),
                        _safe_line(finding.get("StartLine")),
                        _safe_rule_id(finding.get("RuleID")),
                    )
                )
    if private_count:
        print(f"Found {private_count} full-blob private-key structure finding(s):")
        for path in sorted(audit.private_by_path):
            for description in private_key_descriptions(audit.private_by_path[path]):
                print(f"  path={_display_path(path)} kind={description}")


def _check_index(prefixes: Sequence[str]) -> int:
    audit = audit_staged_reachability(prefixes)
    if not audit.entries:
        print("No changed artifact Markdown blobs in the staged diff.")
        return 0
    if audit.has_findings:
        _report_staged_audit(audit)
        return 1
    print(
        "No newly staged scanner findings or structural private-key records in "
        f"{len(audit.entries)} changed artifact blob(s)."
    )
    print(
        "Unchanged parent lines and repository history were not re-audited by "
        "this staged-diff gate."
    )
    return 0


def _require_noncanonical_alternate_index() -> None:
    """Refuse index mutation unless the caller supplied an isolated index.

    Check mode intentionally has no equivalent guard: Git commit hooks must be
    able to inspect both the canonical index and Git's own temporary indexes.
    Mutation is reserved for a caller that owns an explicit regular alternate
    index, such as stage-agent-artifacts.sh's locked transaction.
    """
    raw_index = os.environ.get("GIT_INDEX_FILE")
    if not raw_index:
        raise IndexOperationError(
            "--fix-index requires an explicit noncanonical GIT_INDEX_FILE"
        )
    if len(raw_index) > 4096 or "\x00" in raw_index:
        raise IndexOperationError("alternate index path is not safe")

    alternate = os.path.abspath(raw_index)
    try:
        alternate_info = os.lstat(alternate)
    except OSError as exc:
        raise IndexOperationError("alternate index is not an existing regular file") from exc
    if stat.S_ISLNK(alternate_info.st_mode) or not stat.S_ISREG(
        alternate_info.st_mode
    ):
        raise IndexOperationError("alternate index is not an existing regular file")
    if alternate_info.st_uid != os.getuid():
        raise IndexOperationError("alternate index is not owned by the current user")

    raw_git_dir = _run_git(["rev-parse", "--absolute-git-dir"]).strip()
    if not raw_git_dir or b"\x00" in raw_git_dir or b"\n" in raw_git_dir:
        raise IndexOperationError("canonical Git index could not be identified")
    canonical = os.path.join(os.fsdecode(raw_git_dir), "index")
    if os.path.realpath(alternate) == os.path.realpath(canonical):
        raise IndexOperationError("refusing to modify the canonical Git index")
    try:
        if os.path.exists(canonical) and os.path.samefile(alternate, canonical):
            raise IndexOperationError("refusing to modify an alias of the canonical Git index")
    except OSError as exc:
        raise IndexOperationError("canonical Git index could not be compared safely") from exc


def _fix_index(
    prefixes: Sequence[str], selected_paths: Sequence[str], legacy: bool,
    receipt_request: str | None = None,
) -> int:
    _require_noncanonical_alternate_index()
    selected = [_validate_repo_path(path) for path in selected_paths]
    if len(set(selected)) != len(selected):
        raise PathSafetyError("--files paths must be unique")
    if any(not _is_artifact_markdown(path, prefixes) for path in selected):
        raise PathSafetyError("--files must name exact artifact Markdown paths")

    lifecycle = None
    proof = None
    if receipt_request:
        path = Path(__file__).resolve().parent.parent / "scripts" / "_post_session.py"
        spec = importlib.util.spec_from_file_location("_receipt_lifecycle", path)
        lifecycle = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = lifecycle
        spec.loader.exec_module(lifecycle)
        try:
            proof = lifecycle.v2.scanner_proof(GITLEAKS_CONFIG, receipt_request)
        except lifecycle.LifecycleError as exc:
            raise ScannerError("trusted scanner identity was not proven") from exc
    audit = audit_full_index()[0] if receipt_request else audit_staged_reachability(prefixes)
    entries_by_path = {entry.path: entry for entry in audit.entries}
    if any(path not in entries_by_path for path in selected):
        raise IndexOperationError("an exact --files path is not staged")

    selected_set = set(selected)
    foreign_paths = (
        set(audit.findings_by_path) | set(audit.private_by_path)
    ) - selected_set
    if foreign_paths:
        if not receipt_request:
            _report_staged_audit(audit)
        print("Cannot sanitize findings outside the exact --files selection.", file=sys.stderr)
        return 1

    incomplete_count = sum(
        audit.private_by_path[path].incomplete_headers
        for path in selected
        if path in audit.private_by_path
    )
    if incomplete_count:
        if not receipt_request:
            _report_staged_audit(audit)
        print(
            "Cannot safely redact incomplete or truncated private-key records.",
            file=sys.stderr,
        )
        return 1

    planned: list[tuple[IndexBlob, bytes]] = []
    traces = {path: [] for path in selected}
    for path in selected:
        entry = entries_by_path[path]
        content = _decode_content(entry.data)
        path_findings = audit.findings_by_path.get(path, [])
        for finding in path_findings:
            secret = finding.get("Secret")
            if not isinstance(secret, str) or not secret or secret not in content:
                raise TransformationError(
                    "scanner finding did not match the index blob"
                )
        trace = traces[path] if receipt_request else None
        transformed = redact_private_key_content(content, legacy=legacy, trace=trace)
        # A complete key record may itself be one scanner finding. Its bytes
        # were already removed wholesale, so only still-present values need the
        # generic replacement pass.
        remaining_findings = [
            finding
            for finding in path_findings
            if str(finding.get("Secret")) in transformed
        ]
        transformed = redact_content(
            transformed,
            remaining_findings,
            legacy=legacy,
            strict=True,
            trace=trace,
        )
        transformed_data = _encode_content(transformed)
        if transformed_data != entry.data:
            planned.append((entry, transformed_data))

    _write_index_blobs(planned)

    coverage = []
    if receipt_request:
        post_scan, coverage = audit_full_index()
    else:
        post_scan = audit_staged_reachability(prefixes)
    if post_scan.has_findings:
        if not receipt_request:
            _report_staged_audit(post_scan)
        print(
            "Staged-diff post-scan still found newly staged or structural material.",
            file=sys.stderr,
        )
        return 1

    if receipt_request:
        # Reuse the canonical lifecycle evidence writer. Failure leaves only the
        # disposable alternate index changed; the stage helper cannot publish.
        try:
            lifecycle.v2.prepare_receipt(receipt_request, sys.modules[__name__], audit, selected, traces, coverage, proof)
        except lifecycle.LifecycleError as exc:
            raise TransformationError("private sanitation evidence was not proven") from exc
    print(
        f"Sanitized {len(planned)} changed staged artifact blob(s); worktree unchanged."
    )
    return 0


def _scoped_worktree_findings(
    findings: Sequence[dict],
    prefixes: Sequence[str],
    candidate_paths: set[str],
) -> list[dict]:
    scoped = []
    for finding in findings:
        path = _scanner_finding_path(finding)
        if _is_artifact_markdown(path, prefixes):
            if path not in candidate_paths:
                raise ScannerError("gitleaks reported a non-candidate worktree path")
            copied = dict(finding)
            copied["File"] = path
            scoped.append(copied)
    return scoped


def _staged_worktree_files(prefixes: Sequence[str]) -> list[Path]:
    output = _run_git(
        ["diff", "--cached", "--name-only", "-z", "--diff-filter=ACMRT", "--"]
    )
    files = []
    for raw_path in (part for part in output.split(b"\x00") if part):
        path = _decode_git_path(raw_path)
        if _is_artifact_markdown(path, prefixes):
            files.append(Path(path))
    return files


def _report_worktree_findings(
    findings: Sequence[dict], private: dict[Path, list[str]]
) -> None:
    if findings:
        print(f"Found {len(findings)} scanner finding(s):")
        for finding in findings:
            print(
                "  path={} line={} rule={}".format(
                    _display_path(str(finding.get("File"))),
                    _safe_line(finding.get("StartLine")),
                    _safe_rule_id(finding.get("RuleID")),
                )
            )
    if private:
        print(f"Found private-key records in {len(private)} file(s):")
        for path in sorted(private, key=str):
            for description in private[path]:
                print(f"  path={_display_path(str(path))} kind={description}")


def _worktree_mode(
    prefixes: Sequence[str], working_dir: bool, fix: bool, legacy: bool
) -> int:
    existing, missing, enumerated_files = _safe_worktree_targets(prefixes)
    for prefix in missing:
        print(f"Skipping missing path: {_display_path(prefix + '/')}")
    if not existing:
        print("No target directories found; nothing to scan.")
        return 0

    if working_dir:
        scan_files = enumerated_files
        findings = run_gitleaks_worktree_files(scan_files)
    else:
        scan_files = _validate_selected_worktree_files(
            _staged_worktree_files(existing), existing
        )
        candidate_paths = {str(path) for path in scan_files}
        findings = _scoped_worktree_findings(
            run_gitleaks_staged(), existing, candidate_paths
        )

    private = find_private_key_files(scan_files)
    if not findings and not private:
        if working_dir:
            print("No scanner or structural private-key findings in worktree targets.")
        else:
            print(
                "No newly staged scanner findings or structural private-key "
                "records in selected worktree targets."
            )
        return 0

    _report_worktree_findings(findings, private)
    if not fix:
        print("Run with --fix to redact matching working-tree files.")
        return 1

    by_path: dict[str, list[dict]] = {}
    for finding in findings:
        by_path.setdefault(str(finding["File"]), []).append(finding)
    target_paths = {Path(path) for path in by_path} | set(private)
    planned: dict[Path, tuple[bytes, str]] = {}

    # Compute every transformation before writing any file. One incomplete
    # record therefore leaves the entire worktree untouched. Each plan retains
    # the exact bytes it read so write_text can reject an intervening append.
    try:
        for path in target_paths:
            original_data = _read_regular_bytes(path)
            content = _decode_content(original_data)
            transformed = redact_private_key_content(content, legacy=legacy)
            transformed = redact_content(
                transformed,
                by_path.get(str(path), []),
                legacy=legacy,
                strict=False,
            )
            if transformed != content:
                planned[path] = (original_data, transformed)
    except IncompletePrivateKeyError:
        print(
            "Cannot safely redact incomplete or truncated private-key records; "
            "no files changed.",
            file=sys.stderr,
        )
        return 1
    except OSError as exc:
        raise TransformationError("a working-tree target could not be read") from exc

    for path, (original_data, transformed) in planned.items():
        write_text(path, transformed, expected_data=original_data)

    for path, (_original_data, transformed) in planned.items():
        if analyze_private_key_content(transformed).has_findings:
            raise TransformationError("private-key material remained after redaction")
        for finding in by_path.get(str(path), []):
            secret = finding.get("Secret")
            if isinstance(secret, str) and secret and secret in transformed:
                raise TransformationError("reported bytes remained after redaction")

    print(f"Sanitized {len(planned)} working-tree file(s).")
    print("The Git index was not changed and nothing was re-staged.")
    print("Review the worktree diff, then stage the exact files explicitly.")
    return 0


def _flatten_file_args(groups: list[list[str]] | None) -> list[str]:
    return [path for group in (groups or []) for path in group]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check or redact secrets in agent artifact Markdown."
    )
    parser.add_argument(
        "--check-index",
        action="store_true",
        help=(
            "Gate newly staged scanner reachability in changed artifact blobs; "
            "also check full-blob private-key structure. Unchanged parent lines "
            "and history are outside scope (default index mode)"
        ),
    )
    parser.add_argument(
        "--fix-index",
        action="store_true",
        help=(
            "Redact exact staged regular blobs only in an explicitly supplied "
            "noncanonical GIT_INDEX_FILE; never read or write worktree content"
        ),
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help=(
            "Compatibility mode: redact matching nonsymlink working-tree files "
            "only; does not update the Git index or re-stage files"
        ),
    )
    parser.add_argument(
        "--working-dir",
        action="store_true",
        help=(
            "Fully scan nonsymlink files under working-tree roots instead of "
            "using the staged-diff gate"
        ),
    )
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="Write pre-2.4.0 placeholders in either fix mode",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help=(
            "Trusted gitleaks config (relative to the repository root or "
            "absolute); defaults to the root .gitleaks.toml when present"
        ),
    )
    parser.add_argument(
        "--paths",
        nargs="+",
        default=DEFAULT_PATHS,
        metavar="PREFIX",
        help="Component-anchored artifact prefixes (default: %(default)s)",
    )
    parser.add_argument(
        "--files",
        nargs="+",
        action="append",
        metavar="PATH",
        help="Exact staged Markdown paths for --fix-index (repeatable)",
    )
    parser.add_argument("--full-index", action="store_true", help="V2: scan every frozen index blob; unsupported inputs block")
    parser.add_argument("--receipt-request", help="V2 private request; persist complete evidence before publication")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    files = _flatten_file_args(args.files)

    if args.check_index and (args.fix_index or args.fix or args.working_dir):
        parser.error("--check-index cannot be combined with a fix/worktree mode")
    if args.fix_index and (args.fix or args.working_dir):
        parser.error("--fix-index cannot be combined with a worktree mode")
    if args.fix_index and not files:
        parser.error("--fix-index requires at least one exact --files path")
    if files and not args.fix_index:
        parser.error("--files is valid only with --fix-index")
    if args.full_index and (not args.check_index or args.fix_index or args.fix or args.working_dir):
        parser.error("--full-index requires readonly --check-index")

    try:
        _prepare_repository(args.config)
        prefixes = [_normalize_prefix(prefix) for prefix in args.paths]
        if len(set(prefixes)) != len(prefixes):
            raise PathSafetyError("--paths prefixes must be unique")
        if args.receipt_request and (not args.fix_index or args.legacy):
            raise PathSafetyError("--receipt-request requires nonlegacy --fix-index")
        if args.fix_index:
            return _fix_index(prefixes, files, legacy=args.legacy, receipt_request=args.receipt_request)
        if args.full_index:
            audit, _coverage = audit_full_index()
            if audit.has_findings:
                # This v2 pass is also directly callable. Do not echo a hostile
                # scanner's rule id, which can itself contain a raw value.
                print("Full-index scan found blocking findings; index and source unchanged.")
                return 1
            return 0
        if args.fix or args.working_dir:
            return _worktree_mode(
                prefixes,
                working_dir=args.working_dir,
                fix=args.fix,
                legacy=args.legacy,
            )
        return _check_index(prefixes)
    except PathSafetyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ScannerError as exc:
        print(f"error: scanner unavailable or invalid: {exc}", file=sys.stderr)
        return 2
    except IndexOperationError as exc:
        print(f"error: effective index could not be processed: {exc}", file=sys.stderr)
        return 2
    except TransformationError as exc:
        print(f"error: safe redaction failed: {exc}", file=sys.stderr)
        return 2
    except OSError:
        print("error: filesystem operation failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
