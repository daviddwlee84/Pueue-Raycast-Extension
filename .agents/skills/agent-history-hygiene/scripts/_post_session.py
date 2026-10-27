#!/usr/bin/env python3
"""Private post-session lifecycle primitives (stdlib only, POSIX hosts)."""

import argparse
import contextlib
import datetime as _datetime
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import unicodedata
import uuid
import importlib.util

# Inspection/preview never creates helper or redactor bytecode caches either.
sys.dont_write_bytecode = True

# Explicit sibling loading also works under python3 -I -B. Never search an
# ambient PYTHONPATH for the protocol implementation.
_v2_spec = importlib.util.spec_from_file_location(
    "_post_session_v2", os.path.join(os.path.dirname(os.path.realpath(__file__)), "_post_session_v2.py")
)
v2 = importlib.util.module_from_spec(_v2_spec)
_v2_spec.loader.exec_module(v2)
v2.p = sys.modules[__name__]

SCHEMA_VERSION = 1  # Immutable legacy parser; new outer runs explicitly select 2.
MAX_JSON_BYTES = 128 * 1024
MAX_MESSAGE_BYTES = 256 * 1024
MAX_COMMIT_OBJECT_BYTES = MAX_MESSAGE_BYTES + MAX_JSON_BYTES
MAX_GITLEAKS_CONFIG_BYTES = 1024 * 1024
MAX_GITLEAKS_REPORT_BYTES = 16 * 1024 * 1024
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
OID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
REF_RE = re.compile(r"^refs/heads/[^\x00-\x20\x7f]+$")
MANAGED_TRAILER_KEYS = frozenset(
    {
        b"AI-Assisted-By",
        b"Agent-Transcript",
        b"Agent-Plan",
        b"Agent-History-Request",
    }
)
MANAGED_TRAILER_KEYS_LOWER = frozenset(
    key.lower() for key in MANAGED_TRAILER_KEYS
)

JOURNAL_KEYS = frozenset(
    {
        "schema_version",
        "run_id",
        "request_path",
        "worktree_root",
        "git_dir",
        "state",
        "authorization_sha256",
        "child_exit_code",
        "child_signal",
        "sync_attempted",
        "sync_succeeded",
        "sync_session_id",
        "request_sha256",
        "composed_message_path",
        "composed_message_sha256",
        "message_ready",
        "staging_ready",
        "lazygit_draft_sha256",
        "commit_editmsg_draft_sha256",
        "gitleaks_config_path",
        "gitleaks_config_sha256",
        "expected_commit_parent",
        "expected_commit_tree",
        "commit_oid",
        "failure_code",
        "updated_at",
    }
)
JOURNAL_STATES = frozenset(
    {
        "running",
        "pending",
        "child_exited",
        "syncing",
        "synced",
        "prepared",
        "rotation_required",
        "committing",
        "done",
        "failed",
    }
)
REQUEST_KEYS = frozenset(
    {
        "schema_version",
        "request_id",
        "request_path",
        "action",
        "provider",
        "worktree_root",
        "git_dir",
        "head_ref",
        "head_oid",
        "index_tree",
        "session_id",
        "specstory_path",
        "plan_policy",
        "plan_path",
        "base_message_path",
        "base_message_sha256",
        "created_at",
    }
)


class LifecycleError(Exception):
    def __init__(self, code, message, exit_code=4, next_action="inspect_diagnostics"):
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.next_action = next_action


def now_utc():
    return (
        _datetime.datetime.now(_datetime.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def log(message):
    print(message, file=sys.stderr)


def emit(value):
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(rendered.encode("utf-8")) + 1 > MAX_JSON_BYTES:
        raise LifecycleError("oversized_public_output", "structured response exceeds its supported bound", 7,
                             "inspect_private_run_state_without_retrying")
    print(rendered, flush=True)


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def safe_text(value, *, allow_message_controls=False, label="value"):
    if not isinstance(value, str) or not value:
        raise LifecycleError("invalid_text", "%s must be non-empty safe UTF-8 text" % label)
    try:
        value.encode("utf-8", "strict")
    except UnicodeError:
        raise LifecycleError("invalid_text", "%s is not valid UTF-8 text" % label)
    for character in value:
        if allow_message_controls and character in "\n\t":
            continue
        if unicodedata.category(character) == "Cc":
            raise LifecycleError("invalid_text", "%s contains unsupported control bytes" % label)
    return value


def validate_uuid(value, label="session id"):
    safe_text(value, label=label)
    if not UUID_RE.match(value):
        raise LifecycleError("invalid_uuid", "%s must be a canonical lowercase UUID" % label)
    return value


def validate_sha256(value, label="digest", allow_none=False):
    if value is None and allow_none:
        return
    if not isinstance(value, str) or not SHA256_RE.match(value):
        raise LifecycleError("invalid_state", "%s is not a lowercase SHA-256 digest" % label)


def validate_oid(value, label="object id", allow_none=False):
    if value is None and allow_none:
        return
    if not isinstance(value, str) or not OID_RE.match(value):
        raise LifecycleError("invalid_state", "%s is not a canonical Git object id" % label)


def validate_absolute_path(value, label="path"):
    safe_text(value, label=label)
    if not os.path.isabs(value) or os.path.normpath(value) != value:
        raise LifecycleError("invalid_path", "%s must be a normalized absolute path" % label)
    return value


def canonical_json_bytes(value):
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _reject_constant(value):
    raise ValueError("non-finite JSON constant: %s" % value)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def parse_json_bytes(raw, label):
    if len(raw) > MAX_JSON_BYTES:
        raise LifecycleError("invalid_state", "%s exceeds the bounded JSON size" % label)
    try:
        text = raw.decode("utf-8", "strict")
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError):
        raise LifecycleError("invalid_state", "%s is not strict UTF-8 JSON" % label)
    if not isinstance(value, dict):
        raise LifecycleError("invalid_state", "%s must be one JSON object" % label)
    if raw != canonical_json_bytes(value):
        raise LifecycleError("invalid_state", "%s is not canonical JSON" % label)
    return value


def lstat_owned(path, expected_kind, expected_mode=None, allow_missing=False):
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        if allow_missing:
            return None
        raise LifecycleError("missing_state", "required lifecycle state is missing")
    if stat.S_ISLNK(info.st_mode):
        raise LifecycleError("unsafe_state", "lifecycle paths must not be symlinks")
    if expected_kind == "file" and not stat.S_ISREG(info.st_mode):
        raise LifecycleError("unsafe_state", "lifecycle state must be a regular file")
    if expected_kind == "dir" and not stat.S_ISDIR(info.st_mode):
        raise LifecycleError("unsafe_state", "lifecycle state must be a directory")
    if info.st_uid != os.getuid():
        raise LifecycleError("unsafe_owner", "lifecycle state must be owned by the current user")
    if expected_mode is not None and stat.S_IMODE(info.st_mode) != expected_mode:
        raise LifecycleError(
            "unsafe_mode",
            "lifecycle state has the wrong mode; expected %04o" % expected_mode,
        )
    return info


def assert_private_dir(path):
    lstat_owned(path, "dir", 0o700)
    if os.path.realpath(path) != path:
        raise LifecycleError("unsafe_state", "private lifecycle directories must be canonical")


def ensure_private_dir(path):
    try:
        os.mkdir(path, 0o700)
        os.chmod(path, 0o700)
    except FileExistsError:
        pass
    assert_private_dir(path)


def read_bounded_file(path, limit, *, strict_mode=None):
    if strict_mode is not None:
        lstat_owned(path, "file", strict_mode)
    else:
        lstat_owned(path, "file")
    with open(path, "rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise LifecycleError("oversized_file", "file exceeds the supported size bound")
    return data


def fsync_directory(path):
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write(path, data, mode=0o600, *, private_parent=True):
    parent = os.path.dirname(path)
    if private_parent:
        assert_private_dir(parent)
    else:
        lstat_owned(parent, "dir")
        if os.path.realpath(parent) != parent:
            raise LifecycleError("unsafe_state", "draft parent directory must be canonical")
    name = ".%s.tmp.%d.%s" % (os.path.basename(path), os.getpid(), secrets.token_hex(6))
    temporary = os.path.join(parent, name)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, mode)
    try:
        os.fchmod(descriptor, mode)
        offset = 0
        while offset < len(data):
            offset += os.write(descriptor, data[offset:])
        os.fsync(descriptor)
    except BaseException:
        with contextlib.suppress(OSError):
            os.close(descriptor)
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise
    else:
        os.close(descriptor)
    try:
        os.replace(temporary, path)
        os.chmod(path, mode)
        fsync_directory(parent)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def read_canonical_json(path, label, validator):
    raw = read_bounded_file(path, MAX_JSON_BYTES, strict_mode=0o600)
    value = parse_json_bytes(raw, label)
    validator(value)
    return value, raw


def validate_journal(value):
    if value.get("schema_version") == 2 and type(value.get("schema_version")) is int:
        if set(value) != JOURNAL_KEYS | {"v2"}:
            raise LifecycleError("invalid_state", "journal keys do not match schema version 2")
        v2.validate_metadata(value["v2"])
        legacy = {key: item for key, item in value.items() if key != "v2"}
        legacy["schema_version"] = 1
        validate_journal(legacy)
        if value["sync_succeeded"] and (not value["v2"]["freshness"] or value["v2"]["group_quiescent"] is not True):
            raise LifecycleError("invalid_state", "v2 sync lacks exact export proof")
        if value["staging_ready"] and (not value["v2"]["receipt_revision"] or not value["v2"]["publication_revision"]):
            raise LifecycleError("invalid_state", "v2 prepared state lacks durable evidence")
        return
    if set(value) != JOURNAL_KEYS:
        raise LifecycleError("invalid_state", "journal keys do not match schema version 1")
    if type(value["schema_version"]) is not int or value["schema_version"] != SCHEMA_VERSION:
        raise LifecycleError("invalid_state", "unsupported journal schema version")
    validate_uuid(value["run_id"], "run id")
    validate_absolute_path(value["request_path"], "journal request path")
    validate_absolute_path(value["worktree_root"], "journal worktree root")
    validate_absolute_path(value["git_dir"], "journal git directory")
    validate_absolute_path(value["composed_message_path"], "composed message path")
    validate_absolute_path(value["gitleaks_config_path"], "trusted gitleaks config path")
    if value["state"] not in JOURNAL_STATES:
        raise LifecycleError("invalid_state", "journal state is not recognized")
    validate_sha256(value["authorization_sha256"], "authorization digest")
    for key in (
        "request_sha256",
        "composed_message_sha256",
        "lazygit_draft_sha256",
        "commit_editmsg_draft_sha256",
        "gitleaks_config_sha256",
    ):
        validate_sha256(value[key], key, allow_none=True)
    for key in ("expected_commit_parent", "expected_commit_tree", "commit_oid"):
        validate_oid(value[key], key, allow_none=True)
    if value["child_exit_code"] is not None and (
        type(value["child_exit_code"]) is not int
        or not 0 <= value["child_exit_code"] <= 255
    ):
        raise LifecycleError("invalid_state", "journal child exit code is invalid")
    if value["child_signal"] is not None and (
        type(value["child_signal"]) is not int or not 1 <= value["child_signal"] <= 127
    ):
        raise LifecycleError("invalid_state", "journal child signal is invalid")
    for key in ("sync_attempted", "sync_succeeded", "message_ready", "staging_ready"):
        if type(value[key]) is not bool:
            raise LifecycleError("invalid_state", "%s must be a JSON boolean" % key)
    if value["sync_session_id"] is not None:
        validate_uuid(value["sync_session_id"], "sync session id")
    for key in ("failure_code", "updated_at"):
        if value[key] is not None:
            safe_text(value[key], label=key)

    done_without_commit = (
        value["state"] == "done" and value["failure_code"] == "no_request"
    )
    prepared_states = {"prepared", "rotation_required", "committing"}
    if value["state"] in prepared_states or (
        value["state"] == "done" and not done_without_commit
    ):
        if not value["staging_ready"]:
            raise LifecycleError(
                "invalid_state", "prepared journal state is missing staging proof"
            )
        if (
            value["expected_commit_parent"] is None
            or value["expected_commit_tree"] is None
        ):
            raise LifecycleError(
                "invalid_state", "prepared journal state is missing snapshot proof"
            )
        if value["gitleaks_config_sha256"] is None:
            raise LifecycleError(
                "invalid_state", "prepared journal state is missing trusted scanner policy"
            )
    if value["message_ready"] and value["composed_message_sha256"] is None:
        raise LifecycleError(
            "invalid_state", "message-ready journal has no composed-message digest"
        )
    if (
        value["state"] in {"committing", "done"}
        and not done_without_commit
        and not value["message_ready"]
    ):
        raise LifecycleError(
            "invalid_state", "commit-state journal is missing message proof"
        )
    if value["state"] == "done":
        if done_without_commit:
            if value["commit_oid"] is not None:
                raise LifecycleError(
                    "invalid_state", "no-request journal must not claim a commit object"
                )
        elif value["commit_oid"] is None:
            raise LifecycleError("invalid_state", "completed journal has no commit object")
    elif value["commit_oid"] is not None:
        raise LifecycleError(
            "invalid_state", "unfinished journal must not claim a commit object"
        )


def validate_request(value):
    if value.get("schema_version") == 2 and type(value.get("schema_version")) is int:
        if set(value) != REQUEST_KEYS | {"v2"} or not isinstance(value["v2"], dict) or set(value["v2"]) != {"helper_revision", "cloud_sync", "repository_identity"}:
            raise LifecycleError("invalid_request", "request keys do not match schema version 2")
        validate_sha256(value["v2"]["helper_revision"])
        v2.validate_repository_identity(value["v2"]["repository_identity"])
        if type(value["v2"]["cloud_sync"]) is not bool:
            raise LifecycleError("invalid_request", "invalid cloud choice")
        legacy = {key: item for key, item in value.items() if key != "v2"}
        legacy["schema_version"] = 1
        validate_request(legacy)
        return
    if set(value) != REQUEST_KEYS:
        raise LifecycleError("invalid_request", "request keys do not match schema version 1")
    if type(value["schema_version"]) is not int or value["schema_version"] != SCHEMA_VERSION:
        raise LifecycleError("invalid_request", "unsupported request schema version")
    validate_uuid(value["request_id"], "request id")
    validate_absolute_path(value["request_path"], "request path")
    if value["action"] != "commit" or value["provider"] != "claude":
        raise LifecycleError("invalid_request", "request action/provider is unsupported")
    validate_absolute_path(value["worktree_root"], "request worktree root")
    validate_absolute_path(value["git_dir"], "request git directory")
    if not isinstance(value["head_ref"], str) or not REF_RE.match(value["head_ref"]):
        raise LifecycleError("invalid_request", "request head ref is not an attached branch")
    validate_oid(value["head_oid"], "request head object")
    validate_oid(value["index_tree"], "request index tree")
    validate_uuid(value["session_id"], "session id")
    safe_text(value["specstory_path"], label="SpecStory path")
    if value["plan_policy"] not in ("path", "none"):
        raise LifecycleError("invalid_request", "request plan policy is invalid")
    if value["plan_policy"] == "path":
        safe_text(value["plan_path"], label="plan path")
    elif value["plan_path"] is not None:
        raise LifecycleError("invalid_request", "no-plan requests must store a null plan path")
    validate_absolute_path(value["base_message_path"], "base message path")
    validate_sha256(value["base_message_sha256"], "base message digest")
    safe_text(value["created_at"], label="creation timestamp")


def write_journal(path, value):
    validate_journal(value)
    atomic_write(path, canonical_json_bytes(value), 0o600)


def git_environment(extra=None):
    environment = os.environ.copy()
    for key in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_NAMESPACE",
    ):
        environment.pop(key, None)
    if extra:
        environment.update(extra)
    return environment


def bash_executable():
    # The supported macOS surface is the stock Bash 3.2. In particular, do not
    # let a PATH-installed shell with different/unstable substitution semantics
    # silently replace that native interpreter in exact-selector transactions.
    if sys.platform == "darwin":
        return "/bin/bash"
    return shutil.which("bash") or "/bin/bash"


def run_command(
    command, *, cwd, env=None, input_bytes=None, suppress_output=False
):
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env=env,
            input=input_bytes,
            stdout=subprocess.DEVNULL if suppress_output else subprocess.PIPE,
            stderr=subprocess.DEVNULL if suppress_output else subprocess.PIPE,
            check=False,
        )
    except OSError:
        raise LifecycleError("dependency_error", "required command could not be executed", 3)


def git_process(root, arguments, *, env_extra=None, input_bytes=None):
    return run_command(
        ["git", "-C", root] + list(arguments),
        cwd=root,
        env=git_environment(env_extra),
        input_bytes=input_bytes,
    )


def git_bytes(root, arguments, *, env_extra=None, input_bytes=None, code="git_error"):
    process = git_process(root, arguments, env_extra=env_extra, input_bytes=input_bytes)
    if process.returncode != 0:
        raise LifecycleError(code, "Git state could not be read safely")
    return process.stdout


def decode_line(raw, label):
    if raw.endswith(b"\n"):
        raw = raw[:-1]
    try:
        value = raw.decode("utf-8", "strict")
    except UnicodeError:
        raise LifecycleError("invalid_path", "%s is not valid UTF-8 text" % label)
    return safe_text(value, label=label)


def discover_repository(cwd=None):
    cwd = os.path.realpath(cwd or os.getcwd())
    if not shutil.which("git"):
        raise LifecycleError("dependency_error", "git is required", 3)
    probe = run_command(
        ["git", "-C", cwd, "rev-parse", "--path-format=absolute", "--show-toplevel"],
        cwd=cwd,
        env=git_environment(),
    )
    if probe.returncode != 0:
        raise LifecycleError("not_git_repository", "run this command inside a Git worktree", 2)
    root = os.path.realpath(decode_line(probe.stdout, "worktree root"))
    git_dir = os.path.realpath(
        decode_line(
            git_bytes(root, ["rev-parse", "--absolute-git-dir"]),
            "per-worktree Git directory",
        )
    )
    validate_absolute_path(root, "worktree root")
    validate_absolute_path(git_dir, "per-worktree Git directory")
    if not os.path.isdir(root) or not os.path.isdir(git_dir):
        raise LifecycleError("invalid_repository", "canonical worktree state is unavailable", 2)
    return root, git_dir


def resolve_git_path(root, relative):
    raw = git_bytes(
        root,
        ["rev-parse", "--path-format=absolute", "--git-path", relative],
    )
    value = os.path.normpath(decode_line(raw, "Git control path"))
    validate_absolute_path(value, "Git control path")
    return value


def current_head_state(root):
    ref_process = git_process(root, ["symbolic-ref", "-q", "HEAD"])
    if ref_process.returncode != 0:
        raise LifecycleError("detached_head", "an attached branch is required")
    head_ref = decode_line(ref_process.stdout, "HEAD ref")
    if not REF_RE.match(head_ref):
        raise LifecycleError("invalid_ref", "HEAD is not a canonical local branch")
    head_process = git_process(root, ["rev-parse", "--verify", "-q", "HEAD^{commit}"])
    if head_process.returncode != 0:
        any_head = git_process(root, ["rev-parse", "--verify", "-q", "HEAD"])
        if any_head.returncode != 0:
            raise LifecycleError(
                "initial_commit_required",
                "an initial commit is required before lifecycle v1 can queue a post-session commit",
                5,
                "create_initial_commit_then_start_a_fresh_wrapper",
            )
        raise LifecycleError("git_error", "HEAD is not a usable commit object")
    head_oid = decode_line(head_process.stdout, "HEAD object")
    validate_oid(head_oid, "HEAD object")
    return head_ref, head_oid


def index_tree(root, index_file=None):
    extra = {"GIT_INDEX_FILE": index_file} if index_file is not None else None
    value = decode_line(
        git_bytes(root, ["write-tree"], env_extra=extra, code="invalid_index"),
        "index tree",
    )
    validate_oid(value, "index tree")
    return value


def reject_git_operation(root):
    markers = (
        "MERGE_HEAD",
        "CHERRY_PICK_HEAD",
        "REVERT_HEAD",
        "BISECT_LOG",
        "rebase-merge",
        "rebase-apply",
        "sequencer",
        "index.lock",
    )
    for marker in markers:
        if os.path.lexists(resolve_git_path(root, marker)):
            raise LifecycleError(
                "git_operation_in_progress",
                "finish the current Git operation and retry with fresh authorization",
                6,
                "finish_git_operation_then_retry",
            )


def capture_snapshot(root, artifact_dirs):
    reject_git_operation(root)
    before_ref, before_head = current_head_state(root)
    tree = index_tree(root)
    after_ref, after_head = current_head_state(root)
    if (before_ref, before_head) != (after_ref, after_head):
        raise LifecycleError("repository_raced", "HEAD changed while the request was captured")
    changed = git_bytes(
        root,
        [
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "--no-renames",
            "-r",
            "-z",
            before_head,
            tree,
            "--",
        ],
    )
    paths = [item for item in changed.split(b"\0") if item]
    has_feature = False
    for raw_path in paths:
        try:
            path = raw_path.decode("utf-8", "strict")
        except UnicodeError:
            raise LifecycleError("invalid_index_path", "staged paths must be valid UTF-8 text")
        safe_text(path, label="staged path")
        if not any(path == prefix or path.startswith(prefix + "/") for prefix in artifact_dirs):
            has_feature = True
    if not has_feature:
        raise LifecycleError(
            "no_staged_feature_diff",
            "stage at least one non-artifact feature change before queueing",
            5,
            "stage_feature_paths_then_queue_again",
        )
    return before_ref, before_head, tree


def load_artifact_dirs(script_dir):
    path = os.path.realpath(os.path.join(script_dir, "..", "assets", "artifact-dirs.txt"))
    data = read_bounded_file(path, 64 * 1024)
    try:
        text = data.decode("utf-8", "strict")
    except UnicodeError:
        raise LifecycleError("invalid_configuration", "artifact directory config is not UTF-8")
    values = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip().rstrip("/")
        if line:
            safe_text(line, label="artifact directory")
            values.append(line)
    if ".specstory/history" not in values:
        raise LifecycleError("invalid_configuration", "artifact directory config is incomplete")
    return tuple(values)


def validate_run_layout(request_path, root, git_dir, expected_run_id=None):
    validate_absolute_path(request_path, "request path")
    if os.path.basename(request_path) != "request.json":
        raise LifecycleError("invalid_request_path", "request path must end in request.json")
    run_dir = os.path.dirname(request_path)
    run_id = os.path.basename(run_dir)
    validate_uuid(run_id, "run id")
    if expected_run_id is not None and run_id != expected_run_id:
        raise LifecycleError("run_mismatch", "run id does not match the request path")
    expected_runs = os.path.join(git_dir, "agent-history-hygiene", "runs")
    if os.path.dirname(run_dir) != expected_runs:
        raise LifecycleError(
            "request_outside_git_dir",
            "request path must be inside this worktree's private Git directory",
            5,
        )
    for directory in (
        os.path.join(git_dir, "agent-history-hygiene"),
        expected_runs,
        run_dir,
    ):
        assert_private_dir(directory)
    journal_path = os.path.join(run_dir, "journal.json")
    journal, _ = read_canonical_json(journal_path, "journal", validate_journal)
    if (
        journal["run_id"] != run_id
        or journal["request_path"] != request_path
        or journal["worktree_root"] != root
        or journal["git_dir"] != git_dir
        or journal["composed_message_path"] != os.path.join(run_dir, "composed-message.txt")
        or journal["gitleaks_config_path"] != os.path.join(run_dir, "gitleaks.toml")
    ):
        raise LifecycleError("run_mismatch", "journal identity does not match this worktree run")
    if journal["schema_version"] == 2 and journal["v2"]["repository_identity"] != v2.repository_identity(root, git_dir):
        v2.fail("repository_replaced", 6)
    return run_dir, journal_path, journal


def resolve_repo_file(root, raw_value, kind, artifact_dirs):
    safe_text(raw_value, label="%s path" % kind)
    if os.path.isabs(raw_value):
        if os.path.normpath(raw_value) != raw_value:
            raise LifecycleError("invalid_path", "%s path is not canonical" % kind)
        candidate = raw_value
    else:
        components = raw_value.split("/")
        if any(component in ("", ".", "..") for component in components):
            raise LifecycleError("invalid_path", "%s path is not canonical" % kind)
        candidate = os.path.normpath(os.path.join(root, raw_value))
    validate_absolute_path(candidate, "%s path" % kind)
    parent = os.path.dirname(candidate)
    if os.path.realpath(parent) != parent:
        raise LifecycleError("unsafe_path", "%s parent directories must not be symlinks" % kind)
    info = os.lstat(candidate) if os.path.lexists(candidate) else None
    if info is None or stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise LifecycleError("invalid_path", "%s path must be an existing regular non-symlink" % kind)
    if info.st_uid != os.getuid():
        raise LifecycleError("unsafe_owner", "%s path must be owned by the current user" % kind)
    relative = os.path.relpath(candidate, root)
    if relative == ".." or relative.startswith("../") or os.path.isabs(relative):
        raise LifecycleError("path_outside_worktree", "%s path must be inside the worktree" % kind)
    relative = relative.replace(os.sep, "/")
    safe_text(relative, label="canonical %s path" % kind)
    if not relative.endswith(".md"):
        raise LifecycleError("invalid_path", "%s path must be Markdown" % kind)
    if kind == "SpecStory":
        if os.path.dirname(relative) != ".specstory/history":
            raise LifecycleError(
                "invalid_path",
                "SpecStory path must be one direct child of .specstory/history",
            )
    elif kind == "plan":
        allowed = tuple(value for value in artifact_dirs if value != ".specstory/history")
        if not any(relative.startswith(prefix + "/") for prefix in allowed):
            raise LifecycleError("invalid_path", "plan path is outside configured artifact directories")
        if relative.startswith(".cursor/rules/"):
            raise LifecycleError("invalid_path", "Cursor rules are not eligible as commit plans")
    return relative, candidate


def read_message_input(root, raw_value):
    safe_text(raw_value, label="message file path")
    if os.path.isabs(raw_value) and os.path.normpath(raw_value) != raw_value:
        raise LifecycleError("invalid_path", "message file path is not canonical")
    if not os.path.isabs(raw_value):
        components = raw_value.split("/")
        if any(component in ("", ".", "..") for component in components):
            raise LifecycleError("invalid_path", "message file path is not canonical")
    candidate = raw_value if os.path.isabs(raw_value) else os.path.join(root, raw_value)
    candidate = os.path.normpath(candidate)
    validate_absolute_path(candidate, "message file path")
    if os.path.realpath(os.path.dirname(candidate)) != os.path.dirname(candidate):
        raise LifecycleError("unsafe_path", "message file parent must not be a symlink")
    data = read_bounded_file(candidate, MAX_MESSAGE_BYTES)
    if not data:
        raise LifecycleError("invalid_message", "message file must not be empty")
    try:
        text = data.decode("utf-8", "strict")
    except UnicodeError:
        raise LifecycleError("invalid_message", "message file must be valid UTF-8")
    safe_text(text, allow_message_controls=True, label="message file")
    return data


def read_request_tree_blob(root, commit_oid, path):
    """Read one bounded regular blob from the immutable queued commit tree."""
    listing = git_bytes(root, ["ls-tree", "-z", commit_oid, "--", path])
    records = [item for item in listing.split(b"\0") if item]
    if not records:
        return None
    if len(records) != 1:
        raise LifecycleError("trusted_policy_invalid", "request tree scanner policy is ambiguous", 7)
    metadata, separator, tree_path = records[0].partition(b"\t")
    fields = metadata.split(b" ")
    if (
        not separator
        or tree_path != path.encode("ascii")
        or len(fields) != 3
        or fields[0] not in (b"100644", b"100755")
        or fields[1] != b"blob"
    ):
        raise LifecycleError("trusted_policy_invalid", "request tree scanner policy is not a regular file", 7)
    try:
        blob_oid = fields[2].decode("ascii", "strict")
    except UnicodeError:
        raise LifecycleError("trusted_policy_invalid", "request tree scanner policy has an invalid object id", 7)
    validate_oid(blob_oid, "request tree scanner policy object")
    size_raw = git_bytes(root, ["cat-file", "-s", blob_oid]).strip()
    if not size_raw.isdigit() or int(size_raw) > MAX_GITLEAKS_CONFIG_BYTES:
        raise LifecycleError("trusted_policy_invalid", "request tree scanner policy exceeds the size bound", 7)
    data = git_bytes(root, ["cat-file", "blob", blob_oid])
    if len(data) > MAX_GITLEAKS_CONFIG_BYTES:
        raise LifecycleError("trusted_policy_invalid", "request tree scanner policy exceeds the size bound", 7)
    return data


def bundled_gitleaks_config(script_dir):
    assets_dir = os.path.normpath(os.path.join(script_dir, "..", "assets"))
    config_path = os.path.join(assets_dir, "gitleaks.toml.template")
    validate_absolute_path(config_path, "bundled scanner policy path")
    if os.path.realpath(assets_dir) != assets_dir:
        raise LifecycleError("trusted_policy_invalid", "bundled scanner policy directory is unsafe", 7)
    return read_bounded_file(config_path, MAX_GITLEAKS_CONFIG_BYTES)


def request_gitleaks_config(script_dir, root, request):
    data = read_request_tree_blob(root, request["head_oid"], ".gitleaks.toml")
    if data is None:
        data = bundled_gitleaks_config(script_dir)
    if not data:
        raise LifecycleError("trusted_policy_invalid", "trusted scanner policy must not be empty", 7)
    try:
        text = data.decode("utf-8", "strict")
    except UnicodeError:
        raise LifecycleError("trusted_policy_invalid", "trusted scanner policy is not valid UTF-8", 7)
    safe_text(text, allow_message_controls=True, label="trusted scanner policy")
    return data


def ensure_trusted_gitleaks_config(script_dir, root, request, journal_path, journal):
    """Materialize the request-HEAD scanner policy once in private run state."""
    config_data = request_gitleaks_config(script_dir, root, request)
    config_digest = sha256_bytes(config_data)
    path = journal["gitleaks_config_path"]
    recorded_digest = journal["gitleaks_config_sha256"]
    if recorded_digest is not None and not hmac.compare_digest(recorded_digest, config_digest):
        raise LifecycleError(
            "trusted_policy_changed",
            "the immutable request scanner policy no longer matches the prepared run",
            7,
            "start_a_fresh_wrapper_and_requeue",
        )
    if os.path.lexists(path):
        stored = read_bounded_file(path, MAX_GITLEAKS_CONFIG_BYTES, strict_mode=0o600)
        if not hmac.compare_digest(stored, config_data):
            raise LifecycleError(
                "trusted_policy_conflict",
                "private scanner policy changed outside this lifecycle",
                7,
                "start_a_fresh_wrapper_and_requeue",
            )
    else:
        atomic_write(path, config_data, 0o600)
        stored = read_bounded_file(path, MAX_GITLEAKS_CONFIG_BYTES, strict_mode=0o600)
        if not hmac.compare_digest(stored, config_data):
            raise LifecycleError("trusted_policy_invalid", "private scanner policy could not be proven", 7)
    if recorded_digest is None:
        journal = update_journal(
            journal_path,
            journal,
            gitleaks_config_sha256=config_digest,
            failure_code=None,
        )
    return journal, path


def initialize_run(root, git_dir, *, protocol_version=1, metadata=None):
    old_umask = os.umask(0o077)
    try:
        private_root = resolve_git_path(root, "agent-history-hygiene")
        runs_dir = resolve_git_path(root, "agent-history-hygiene/runs")
        if os.path.dirname(private_root) != git_dir or os.path.dirname(runs_dir) != private_root:
            raise LifecycleError("unsafe_state", "Git resolved lifecycle state outside its worktree directory")
        ensure_private_dir(private_root)
        ensure_private_dir(runs_dir)
        while True:
            run_id = str(uuid.uuid4())
            run_dir = resolve_git_path(root, "agent-history-hygiene/runs/%s" % run_id)
            if os.path.dirname(run_dir) != runs_dir:
                raise LifecycleError("unsafe_state", "Git resolved a run outside its private runs directory")
            try:
                os.mkdir(run_dir, 0o700)
                os.chmod(run_dir, 0o700)
                break
            except FileExistsError:
                continue
        assert_private_dir(run_dir)
        request_path = os.path.join(run_dir, "request.json")
        journal_path = os.path.join(run_dir, "journal.json")
        token = secrets.token_urlsafe(32)
        journal = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "request_path": request_path,
            "worktree_root": root,
            "git_dir": git_dir,
            "state": "running",
            "authorization_sha256": sha256_bytes(token.encode("ascii")),
            "child_exit_code": None,
            "child_signal": None,
            "sync_attempted": False,
            "sync_succeeded": False,
            "sync_session_id": None,
            "request_sha256": None,
            "composed_message_path": os.path.join(run_dir, "composed-message.txt"),
            "composed_message_sha256": None,
            "message_ready": False,
            "staging_ready": False,
            "lazygit_draft_sha256": None,
            "commit_editmsg_draft_sha256": None,
            "gitleaks_config_path": os.path.join(run_dir, "gitleaks.toml"),
            "gitleaks_config_sha256": None,
            "expected_commit_parent": None,
            "expected_commit_tree": None,
            "commit_oid": None,
            "failure_code": None,
            "updated_at": now_utc(),
        }
        if protocol_version == 2:
            journal["schema_version"] = 2
            journal["v2"] = metadata
        write_journal(journal_path, journal)
        return run_id, run_dir, request_path, journal_path, token
    finally:
        os.umask(old_umask)


def update_journal(journal_path, journal, **changes):
    updated = dict(journal)
    updated.update(changes)
    if updated["schema_version"] == 2:
        updated["v2"] = dict(updated["v2"], revision=journal["v2"]["revision"] + 1)
    updated["updated_at"] = now_utc()
    write_journal(journal_path, updated)
    return updated


class ExclusiveLock:
    def __init__(self, path):
        self.path = path
        self.descriptor = None
        self.identity = None

    def __enter__(self):
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            self.descriptor = os.open(self.path, flags, 0o600)
        except FileExistsError:
            raise LifecycleError("lifecycle_locked", "another lifecycle operation already owns this run", 6)
        os.fchmod(self.descriptor, 0o600)
        info = os.fstat(self.descriptor)
        self.identity = (info.st_dev, info.st_ino)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(self.descriptor)
        try:
            info = os.lstat(self.path)
            if self.identity == (info.st_dev, info.st_ino):
                os.unlink(self.path)
        except OSError:
            pass


def status_to_returncode(status):
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    if os.WIFSIGNALED(status):
        return -os.WTERMSIG(status)
    raise LifecycleError("process_error", "child process ended in an unsupported state", 3)


def process_group_is_empty(process_group):
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def foreground_tty_fd():
    for descriptor in (sys.stdin.fileno(), sys.stdout.fileno(), sys.stderr.fileno()):
        with contextlib.suppress(OSError):
            if os.isatty(descriptor):
                return descriptor
    return None


def supervise_foreground(command, cwd, environment, before_release=None):
    """Run one foreground process group, forwarding targeted termination signals."""
    gate_read, gate_write = os.pipe()
    child_pid = None
    received = {"signal": None}
    old_handlers = {}
    tty_fd = foreground_tty_fd()
    original_pgrp = None
    return_code = None
    group_empty = None
    if tty_fd is not None:
        with contextlib.suppress(OSError):
            original_pgrp = os.tcgetpgrp(tty_fd)

    def forward(signum, _frame):
        if received["signal"] is None:
            received["signal"] = signum
        if child_pid is not None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(child_pid, signum)

    for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        old_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, forward)

    try:
        child_pid = os.fork()
        if child_pid == 0:
            try:
                os.close(gate_write)
                os.setpgrp()
                if os.read(gate_read, 1) != b"1":
                    os._exit(127)
                os.close(gate_read)
                os.chdir(cwd)
                os.execvpe(command[0], command, environment)
            except BaseException:
                os._exit(127)

        os.close(gate_read)
        if tty_fd is not None:
            old_ttou = signal.getsignal(signal.SIGTTOU)
            signal.signal(signal.SIGTTOU, signal.SIG_IGN)
            try:
                os.tcsetpgrp(tty_fd, child_pid)
            except OSError:
                pass
            finally:
                signal.signal(signal.SIGTTOU, old_ttou)
        if before_release is not None:
            try:
                before_release(child_pid)
            except BaseException:
                os.close(gate_write)
                os.waitpid(child_pid, 0)
                raise
        os.write(gate_write, b"1")
        os.close(gate_write)
        if received["signal"] is not None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(child_pid, received["signal"])

        while True:
            try:
                waited_pid, status = os.waitpid(child_pid, os.WUNTRACED)
            except InterruptedError:
                continue
            if waited_pid != child_pid:
                continue
            if os.WIFSTOPPED(status):
                if tty_fd is not None and original_pgrp is not None:
                    old_ttou = signal.getsignal(signal.SIGTTOU)
                    signal.signal(signal.SIGTTOU, signal.SIG_IGN)
                    with contextlib.suppress(OSError):
                        os.tcsetpgrp(tty_fd, original_pgrp)
                    signal.signal(signal.SIGTTOU, old_ttou)
                stop_signal = os.WSTOPSIG(status)
                os.kill(os.getpid(), stop_signal)
                if tty_fd is not None:
                    old_ttou = signal.getsignal(signal.SIGTTOU)
                    signal.signal(signal.SIGTTOU, signal.SIG_IGN)
                    with contextlib.suppress(OSError):
                        os.tcsetpgrp(tty_fd, child_pid)
                    signal.signal(signal.SIGTTOU, old_ttou)
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(child_pid, signal.SIGCONT)
                continue
            return_code = status_to_returncode(status)
            group_empty = process_group_is_empty(child_pid) if os.WIFEXITED(status) else None
            break
    finally:
        with contextlib.suppress(OSError):
            os.close(gate_read)
        with contextlib.suppress(OSError):
            os.close(gate_write)
        if tty_fd is not None and original_pgrp is not None:
            old_ttou = signal.getsignal(signal.SIGTTOU)
            signal.signal(signal.SIGTTOU, signal.SIG_IGN)
            with contextlib.suppress(OSError):
                os.tcsetpgrp(tty_fd, original_pgrp)
            signal.signal(signal.SIGTTOU, old_ttou)
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)
    if return_code is None:
        raise LifecycleError("process_error", "child process outcome was unavailable", 3)
    return return_code, received["signal"], group_empty


def reraises_signal(signum):
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)
    return 128 + signum


def validate_request_matches_run(request, request_path, root, git_dir, run_id):
    if (
        request["request_id"] != run_id
        or request["request_path"] != request_path
        or request["worktree_root"] != root
        or request["git_dir"] != git_dir
    ):
        raise LifecycleError("request_mismatch", "request identity does not match this run")


def run_find_session(script_dir, root, request):
    find_script = os.path.join(script_dir, "find-session.sh")
    command = [
        bash_executable(),
        find_script,
        "--quiet",
        "--format",
        "both",
        "--session-id",
        request["session_id"],
        "--specstory-path",
        request["specstory_path"],
    ]
    process = run_command(command, cwd=root, env=git_environment())
    if process.returncode != 0:
        raise LifecycleError(
            "exact_selector_failed",
            "exact session selection failed after post-exit sync; start a fresh wrapper and requeue",
            7,
            "start_fresh_wrapper_and_requeue",
        )
    try:
        text = process.stdout.decode("utf-8", "strict")
    except UnicodeError:
        raise LifecycleError(
            "exact_selector_failed",
            "exact selector output was not valid UTF-8 after post-exit sync; start a fresh wrapper and requeue",
            7,
            "start_fresh_wrapper_and_requeue",
        )
    fields = {}
    for line in text.splitlines():
        key, separator, value = line.partition("\t")
        if not separator or key in fields:
            raise LifecycleError(
                "exact_selector_failed",
                "exact selector output was malformed after post-exit sync; start a fresh wrapper and requeue",
                7,
                "start_fresh_wrapper_and_requeue",
            )
        fields[key] = value
    expected_absolute = os.path.join(root, request["specstory_path"].replace("/", os.sep))
    if (
        fields.get("status") != "resolved"
        or fields.get("confidence") != "exact"
        or fields.get("claude_session_uuid") != request["session_id"]
        or os.path.realpath(fields.get("specstory_path", "")) != expected_absolute
    ):
        raise LifecycleError(
            "exact_selector_failed",
            "exact selector did not prove the queued session after post-exit sync; start a fresh wrapper and requeue",
            7,
            "start_fresh_wrapper_and_requeue",
        )
    return fields


def cmd_run(arguments):
    if arguments.provider != "claude":
        raise LifecycleError("unsupported_provider", "v1 supports only --provider claude", 2)
    specstory = shutil.which("specstory")
    if not specstory:
        raise LifecycleError("dependency_error", "specstory is required on PATH", 3)
    root, git_dir = discover_repository()
    forwarded = list(arguments.specstory_args)
    if forwarded and forwarded[0] == "--":
        forwarded = forwarded[1:]
    protocol = arguments.protocol_version
    resume_session_id = v2.validate_forwarded(forwarded, arguments.cloud_sync) if protocol == 2 else None
    if protocol == 1 and arguments.cloud_sync:
        v2.fail("unsupported_cloud_choice", 2)
    native_flags = []
    metadata = None
    if protocol == 2:
        native_flags = v2.native_flags(specstory, root, arguments.cloud_sync)
        metadata = v2.initial_metadata(arguments.script_dir, arguments.cloud_sync, specstory, arguments.allow_commit, resume_session_id)
    run_id, _run_dir, request_path, journal_path, token = initialize_run(
        root, git_dir, protocol_version=protocol, metadata=metadata
    )
    log("agent-history: foreground session started; queue path is available to the child")

    child_environment = os.environ.copy()
    for key in tuple(child_environment):
        if key.startswith("AGENT_HISTORY_"):
            child_environment.pop(key, None)
    for key in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_NAMESPACE",
    ):
        child_environment.pop(key, None)
    child_environment["AGENT_HISTORY_REQUEST_PATH"] = request_path
    child_environment["AGENT_HISTORY_RUN_ID"] = run_id
    def record_child(pid):
        _dir, _path, current = validate_run_layout(request_path, root, git_dir, run_id)
        v2.update(journal_path, current, child_pid=pid)
    direct_return_code, forwarded_signal, group_empty = supervise_foreground(
        [specstory, "run", "claude"] + forwarded + native_flags,
        root,
        child_environment,
        before_release=record_child if protocol == 2 else None,
    )

    direct_signal = -direct_return_code if direct_return_code < 0 else None
    signal_number = forwarded_signal if forwarded_signal is not None else direct_signal
    process_group_unproven = (
        signal_number is None
        and direct_return_code == 0
        and group_empty is not True
    )
    run_succeeded = (
        direct_return_code == 0
        and signal_number is None
        and not process_group_unproven
    )
    exit_code = None if signal_number is not None else direct_return_code
    if run_succeeded:
        failure_code = None
    elif signal_number is not None:
        failure_code = "child_signaled"
    elif direct_return_code != 0:
        failure_code = "child_exit_nonzero"
    else:
        failure_code = "process_group_not_quiescent"
    try:
        _run_dir, _journal_path, journal = validate_run_layout(
            request_path, root, git_dir, run_id
        )
        journal = update_journal(
            journal_path,
            journal,
            child_exit_code=exit_code,
            child_signal=signal_number,
            state="child_exited" if run_succeeded else "failed",
            failure_code=failure_code,
            **({"v2": dict(journal["v2"], group_quiescent=group_empty)} if protocol == 2 else {}),
        )
    except (LifecycleError, OSError):
        journal = None
        with contextlib.suppress(OSError):
            log("agent-history: lifecycle state could not be updated after child exit")

    if signal_number is not None:
        with contextlib.suppress(OSError):
            emit(
                {
                    "status": "child_signaled",
                    "run_id": run_id,
                    "request_retained": os.path.lexists(request_path),
                    "child_status": 128 + signal_number,
                }
            )
        return reraises_signal(signal_number)
    if direct_return_code != 0:
        emit(
            {
                "status": "child_exit_nonzero",
                "run_id": run_id,
                "request_retained": os.path.lexists(request_path),
                "child_status": direct_return_code,
            }
        )
        return direct_return_code
    if process_group_unproven:
        emit(
            {
                "status": "process_group_not_quiescent",
                "run_id": run_id,
                "request_retained": os.path.lexists(request_path),
                "child_status": 0,
                "finalizer_called": False,
                "next_action": "stop_remaining_session_processes_then_start_new_run",
            }
        )
        log("agent-history: status=process_group_not_quiescent; no sync or finalizer ran")
        return 22

    if journal is None:
        raise LifecycleError(
            "invalid_state",
            "successful child exit could not establish lifecycle proof",
            23,
            "inspect_private_run_state",
        )
    if not os.path.lexists(request_path):
        update_journal(journal_path, journal, state="done", failure_code="no_request")
        emit({"status": "no_request", "run_id": run_id, "finalizer_called": False})
        return 0

    request, request_raw = read_canonical_json(request_path, "request", validate_request)
    validate_request_matches_run(request, request_path, root, git_dir, run_id)
    request_digest = sha256_bytes(request_raw)
    try:
        source_before = v2.capture_source(arguments.script_dir, root, request) if protocol == 2 else None
    except LifecycleError as error:
        update_journal(journal_path, journal, state="failed", failure_code=error.code)
        raise
    journal = update_journal(
        journal_path,
        journal,
        state="syncing",
        sync_attempted=True,
        sync_succeeded=False,
        sync_session_id=request["session_id"],
        request_sha256=request_digest,
        failure_code=None,
    )
    sync_process = run_command(
        [
            specstory,
            "sync",
            "claude",
            "-s",
            request["session_id"],
            "--silent",
        ] + native_flags,
        cwd=root,
        env=git_environment(),
    )
    if sync_process.returncode != 0:
        update_journal(
            journal_path,
            journal,
            state="failed",
            failure_code="sync_failed",
        )
        emit(
            {
                "status": "sync_failed",
                "run_id": run_id,
                "request_retained": True,
                "finalizer_called": False,
                "next_action": "start_fresh_wrapper_and_requeue",
            }
        )
        log("agent-history: status=sync_failed; next=start a fresh wrapper and requeue")
        return 21

    try:
        run_find_session(arguments.script_dir, root, request)
        if protocol == 2:
            freshness = v2.prove_export(arguments.script_dir, root, request, journal, source_before, native_flags)
            journal = v2.update(journal_path, journal, freshness=freshness)
    except LifecycleError as error:
        update_journal(
            journal_path,
            journal,
            state="failed",
            failure_code=error.code,
        )
        raise
    journal = update_journal(
        journal_path,
        journal,
        state="synced",
        sync_succeeded=True,
        failure_code=None,
    )
    if not arguments.allow_commit:
        # The run itself succeeded: the child exited 0 and the exact sync is
        # journaled. Only the commit is outstanding, and withholding
        # authorization is the caller's own choice -- not a runner failure. Exit
        # 0 and let `status` carry the outstanding action.
        emit(
            {
                "status": "authorization_required",
                "run_id": run_id,
                "request_retained": True,
                "finalizer_called": False,
                "next_action": "run_finalizer_with_allow_commit",
            }
        )
        log(
            "agent-history: status=authorization_required; "
            "run finalize-agent-commit.sh --allow-commit to commit this queued history"
        )
        return 0

    finalizer = os.path.join(arguments.script_dir, "finalize-agent-commit.sh")
    process = run_command(
        [
            bash_executable(),
            finalizer,
            "--request",
            request_path,
            "--runner-token=%s" % token,
        ],
        cwd=root,
        env=git_environment(),
    )
    if process.stdout:
        sys.stdout.buffer.write(process.stdout)
        sys.stdout.buffer.flush()
    if process.stderr:
        sys.stderr.buffer.write(process.stderr)
        sys.stderr.buffer.flush()
    return process.returncode


def cmd_queue(arguments):
    request_path = os.environ.get("AGENT_HISTORY_REQUEST_PATH", "")
    run_id = os.environ.get("AGENT_HISTORY_RUN_ID", "")
    if not request_path or not run_id:
        raise LifecycleError(
            "missing_runner_context",
            "queueing requires the environment created by run-specstory-session.sh",
            5,
            "launch_through_run_specstory_session",
        )
    validate_uuid(run_id, "run id")
    root, git_dir = discover_repository()
    artifact_dirs = load_artifact_dirs(arguments.script_dir)
    run_dir, journal_path, journal = validate_run_layout(
        request_path, root, git_dir, run_id
    )
    if journal["state"] not in ("running", "pending"):
        raise LifecycleError(
            "lifecycle_closed",
            "this run no longer accepts commit requests",
            5,
        )
    lock_path = os.path.join(run_dir, "queue.lock")
    with ExclusiveLock(lock_path):
        _dir, _path, journal = validate_run_layout(request_path, root, git_dir, run_id)
        if journal["schema_version"] == 2:
            if not v2.authentic_context(journal):
                v2.fail("requires_wrapper", 5)
            if journal["v2"]["helper_revision"] != v2.helper_revision(arguments.script_dir):
                v2.fail("helper_changed", 5)
            if journal["v2"]["resume_session_id"] not in (None, arguments.session_id):
                v2.fail("session_mismatch", 5)
        specstory_relative, _specstory_absolute = resolve_repo_file(
            root, arguments.specstory_path, "SpecStory", artifact_dirs
        )
        plan_relative = None
        if arguments.plan is not None:
            plan_relative, _plan_absolute = resolve_repo_file(
                root, arguments.plan, "plan", artifact_dirs
            )
        message_data = read_message_input(root, arguments.message_file)
        message_digest = sha256_bytes(message_data)
        base_message_path = os.path.join(run_dir, "base-message.txt")

        existing_request = None
        existing_raw = None
        if os.path.lexists(request_path):
            existing_request, existing_raw = read_canonical_json(
                request_path, "request", validate_request
            )
            existing_digest = sha256_bytes(existing_raw)
            if journal["request_sha256"] not in (None, existing_digest):
                raise LifecycleError(
                    "request_mismatch",
                    "existing request digest does not match the journal",
                    5,
                )
        elif journal["request_sha256"] is not None or journal["state"] == "pending":
            raise LifecycleError(
                "missing_state",
                "journal names a request that is no longer present",
                5,
            )
        head_ref, head_oid, tree = capture_snapshot(root, artifact_dirs)
        if journal["schema_version"] == 2:
            changed = git_bytes(root, ["diff-tree", "--no-commit-id", "--name-only", "--no-renames", "-r", "-z", head_oid, tree])
            for raw in changed.split(b"\0"):
                if raw:
                    path = raw.decode("utf-8", "strict")
                    if any(path == prefix or path.startswith(prefix + "/") for prefix in artifact_dirs):
                        v2.fail("feature_only_index_required", 5)
        created_at = existing_request["created_at"] if existing_request else now_utc()
        request = {
            "schema_version": SCHEMA_VERSION,
            "request_id": run_id,
            "request_path": request_path,
            "action": "commit",
            "provider": "claude",
            "worktree_root": root,
            "git_dir": git_dir,
            "head_ref": head_ref,
            "head_oid": head_oid,
            "index_tree": tree,
            "session_id": arguments.session_id,
            "specstory_path": specstory_relative,
            "plan_policy": "path" if plan_relative is not None else "none",
            "plan_path": plan_relative,
            "base_message_path": base_message_path,
            "base_message_sha256": message_digest,
            "created_at": created_at,
        }
        if journal["schema_version"] == 2:
            request["schema_version"] = 2
            request["v2"] = {key: journal["v2"][key] for key in ("helper_revision", "cloud_sync", "repository_identity")}
        validate_request(request)
        request_raw = canonical_json_bytes(request)

        if existing_request is not None:
            if existing_request != request or existing_raw != request_raw:
                raise LifecycleError(
                    "request_conflict",
                    "a different request already exists for this run",
                    5,
                    "keep_existing_request_or_start_new_run",
                )
            existing_message = read_bounded_file(
                base_message_path, MAX_MESSAGE_BYTES, strict_mode=0o600
            )
            if existing_message != message_data:
                raise LifecycleError(
                    "request_conflict",
                    "the owned base message does not match the existing request",
                    5,
                )
            idempotent = True
        else:
            if os.path.lexists(base_message_path):
                existing_message = read_bounded_file(
                    base_message_path, MAX_MESSAGE_BYTES, strict_mode=0o600
                )
                if existing_message != message_data:
                    raise LifecycleError(
                        "request_conflict",
                        "an unrelated base message already exists for this run",
                        5,
                    )
            else:
                atomic_write(base_message_path, message_data, 0o600)
            atomic_write(request_path, request_raw, 0o600)
            idempotent = False

        request_digest = sha256_bytes(request_raw)
        journal = update_journal(
            journal_path,
            journal,
            state="pending",
            request_sha256=request_digest,
            failure_code=None,
        )
    emit(
        {
            "status": "queued",
            "request_id": run_id,
            "request_path": request_path,
            "idempotent": idempotent,
            "action": "commit",
            "next_action": "exit_agent_session",
            **({"schema_version": 2, "protocol_version": 2, "request_revision": request_digest,
                "journal_revision": v2.digest(journal), "helper_revision": journal["v2"]["helper_revision"],
                "queue_ack": v2.queue_ack(request, journal)}
               if journal["schema_version"] == 2 else {}),
        }
    )
    log("agent-history: finalization queued; exit the agent session without more repository actions")
    return 0


def fingerprint(path):
    try:
        info = os.stat(path, follow_symlinks=False)
    except OSError:
        raise LifecycleError("active_writer", "the selected transcript changed during validation", 7)
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def reject_active_writer(path):
    lsof = shutil.which("lsof")
    if not lsof:
        return
    process = run_command([lsof, "-F", "pfa", "--", path], cwd=os.path.dirname(path), env=os.environ.copy())
    if process.returncode not in (0, 1):
        raise LifecycleError(
            "writer_check_failed",
            "the transcript writer check could not complete safely",
            7,
        )
    if process.returncode == 1:
        return
    current_pid = None
    current_fd = None
    try:
        lines = process.stdout.decode("ascii", "strict").splitlines()
    except UnicodeError:
        raise LifecycleError("writer_check_failed", "the writer check returned malformed data", 7)
    for line in lines:
        if not line:
            continue
        tag, value = line[0], line[1:]
        if tag == "p":
            try:
                current_pid = int(value)
            except ValueError:
                raise LifecycleError("writer_check_failed", "the writer check returned malformed data", 7)
            current_fd = None
        elif tag == "f":
            current_fd = value
        elif tag == "a" and current_pid is not None and current_fd is not None:
            if value in ("w", "u") and current_pid != os.getpid():
                raise LifecycleError(
                    "active_writer",
                    "a process still has the selected transcript open for writing",
                    7,
                    "stop_transcript_writer_then_retry",
                )


def parse_request_for_finalize(request_path, root, git_dir):
    run_id = os.path.basename(os.path.dirname(request_path))
    run_dir, journal_path, journal = validate_run_layout(
        request_path, root, git_dir, run_id
    )
    request, request_raw = read_canonical_json(request_path, "request", validate_request)
    validate_request_matches_run(request, request_path, root, git_dir, run_id)
    if request["base_message_path"] != os.path.join(run_dir, "base-message.txt"):
        raise LifecycleError("request_mismatch", "base message is outside its owning run")
    digest = sha256_bytes(request_raw)
    if journal["request_sha256"] != digest:
        raise LifecycleError("request_mismatch", "request digest does not match the journal")
    if request["schema_version"] != journal["schema_version"]:
        raise LifecycleError("request_mismatch", "request/journal protocol versions differ")
    if request["schema_version"] == 2 and any(request["v2"][key] != journal["v2"][key] for key in request["v2"]):
        raise LifecycleError("request_mismatch", "request/journal v2 policy identities differ")
    return run_id, run_dir, journal_path, journal, request, digest


def authorize_finalize(arguments, journal):
    if arguments.allow_commit and arguments.runner_token is not None:
        raise LifecycleError("invalid_authorization", "choose one authorization mechanism", 2)
    if arguments.allow_commit:
        return
    if arguments.runner_token is None:
        raise LifecycleError(
            "authorization_required",
            "automatic finalization requires parent authorization or explicit recovery approval",
            5,
            "retry_with_allow_commit",
        )
    try:
        token_digest = sha256_bytes(arguments.runner_token.encode("utf-8", "strict"))
    except UnicodeError:
        raise LifecycleError("invalid_authorization", "runner authorization is invalid", 5)
    if not hmac.compare_digest(token_digest, journal["authorization_sha256"]):
        raise LifecycleError("invalid_authorization", "runner authorization did not match this run", 5)


def request_lifecycle_is_proven(journal, request):
    return (
        journal["child_exit_code"] == 0
        and journal["child_signal"] is None
        and journal["sync_attempted"] is True
        and journal["sync_succeeded"] is True
        and journal["sync_session_id"] == request["session_id"]
        and journal["state"]
        in ("synced", "prepared", "rotation_required", "committing", "done")
    )


def normalize_commit_message(root, message_data):
    process = git_process(root, ["stripspace"], input_bytes=message_data)
    if process.returncode != 0 or len(process.stdout) > MAX_MESSAGE_BYTES:
        raise LifecycleError(
            "message_validation_failed",
            "commit message normalization could not be proven",
            7,
        )
    return process.stdout


def commit_matches_request(root, oid, request, journal, *, require_trailer):
    if request["schema_version"] == 2 and journal["expected_commit_parent"] != request["head_oid"]:
        return False
    if not oid or not journal["expected_commit_parent"] or not journal["expected_commit_tree"]:
        return False
    try:
        exists = git_process(root, ["cat-file", "-e", "%s^{commit}" % oid])
        if exists.returncode != 0:
            return False
        parents = (
            git_bytes(root, ["show", "-s", "--format=%P", oid])
            .decode("ascii", "strict")
            .strip()
            .split()
        )
        tree = (
            git_bytes(root, ["show", "-s", "--format=%T", oid])
            .decode("ascii", "strict")
            .strip()
        )
        if (
            parents != [journal["expected_commit_parent"]]
            or tree != journal["expected_commit_tree"]
        ):
            return False
        if not require_trailer:
            return True

        composed = read_bounded_file(
            journal["composed_message_path"],
            MAX_MESSAGE_BYTES,
            strict_mode=0o600,
        )
        if not journal["composed_message_sha256"] or not hmac.compare_digest(
            sha256_bytes(composed), journal["composed_message_sha256"]
        ):
            return False
        raw_size = git_bytes(root, ["cat-file", "-s", oid]).strip()
        if not raw_size.isdigit() or int(raw_size) > MAX_COMMIT_OBJECT_BYTES:
            return False
        raw_commit = git_bytes(root, ["cat-file", "commit", oid])
        _headers, separator, committed_message = raw_commit.partition(b"\n\n")
        if not separator or len(committed_message) > MAX_MESSAGE_BYTES:
            return False
        if not hmac.compare_digest(
            normalize_commit_message(root, committed_message),
            normalize_commit_message(root, composed),
        ):
            return False
        expected_managed = validate_managed_message(root, composed, request)
        committed_managed = validate_managed_message(root, committed_message, request)
        return committed_managed == expected_managed
    except (LifecycleError, UnicodeError):
        return False


def remove_matching_lazygit_draft(git_dir, journal):
    digest = journal["lazygit_draft_sha256"]
    if digest is None:
        return
    path = os.path.join(git_dir, "LAZYGIT_PENDING_COMMIT")
    try:
        info = os.lstat(path)
        if stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode) and info.st_uid == os.getuid():
            data = read_bounded_file(path, MAX_MESSAGE_BYTES)
            if hmac.compare_digest(sha256_bytes(data), digest):
                os.unlink(path)
    except (FileNotFoundError, LifecycleError, OSError):
        return


def reconcile_commit(root, git_dir, request, journal_path, journal):
    if journal["state"] == "done" and journal["commit_oid"]:
        if commit_matches_request(
            root, journal["commit_oid"], request, journal, require_trailer=True
        ):
            remove_matching_lazygit_draft(git_dir, journal)
            return journal["commit_oid"]
        raise LifecycleError(
            "commit_recovery_unproven",
            "recorded commit identity could not be proven; no retry is allowed",
            8,
            "inspect_recorded_commit_without_retrying",
        )
    if journal["state"] == "committing":
        try:
            head_ref, oid = current_head_state(root)
        except LifecycleError:
            head_ref, oid = None, None
        if (
            head_ref == request["head_ref"]
            and oid
            and commit_matches_request(
                root, oid, request, journal, require_trailer=True
            )
        ):
            journal = update_journal(
                journal_path,
                journal,
                state="done",
                commit_oid=oid,
                failure_code=None,
            )
            remove_matching_lazygit_draft(git_dir, journal)
            return oid
        raise LifecycleError(
            "commit_recovery_unproven",
            "a prior commit attempt cannot be proven complete; no retry is allowed",
            8,
            "inspect_head_and_journal_without_retrying",
        )
    return None


def validate_current_snapshot(root, request, artifact_dirs):
    reject_git_operation(root)
    head_ref, head_oid = current_head_state(root)
    tree = index_tree(root)
    head_ref_after, head_oid_after = current_head_state(root)
    if (head_ref, head_oid) != (head_ref_after, head_oid_after):
        raise LifecycleError("repository_raced", "HEAD changed during finalizer validation")
    if head_ref != request["head_ref"] or head_oid != request["head_oid"] or tree != request["index_tree"]:
        raise LifecycleError(
            "stale_state",
            "HEAD, branch, or staged tree changed after the request was queued",
            6,
            "restage_feature_diff_and_queue_a_new_request",
        )
    # Re-check the feature guard against the exact stored tree, not live pathnames.
    capture_ref, capture_head, capture_tree = capture_snapshot(root, artifact_dirs)
    if (capture_ref, capture_head, capture_tree) != (head_ref, head_oid, tree):
        raise LifecycleError("repository_raced", "repository state changed during validation")


def validate_prepared_snapshot(root, request, journal, artifact_dirs):
    if (
        not journal["staging_ready"]
        or journal["expected_commit_parent"] != request["head_oid"]
        or journal["expected_commit_tree"] is None
    ):
        raise LifecycleError(
            "invalid_state",
            "prepared recovery is missing its exact parent/tree proof",
            4,
        )
    reject_git_operation(root)
    head_ref, head_oid = current_head_state(root)
    tree = index_tree(root)
    head_ref_after, head_oid_after = current_head_state(root)
    if (head_ref, head_oid) != (head_ref_after, head_oid_after):
        raise LifecycleError(
            "repository_raced", "HEAD changed during prepared-state validation", 6
        )
    if (
        head_ref != request["head_ref"]
        or head_oid != journal["expected_commit_parent"]
        or tree != journal["expected_commit_tree"]
    ):
        raise LifecycleError(
            "stale_prepared_state",
            "HEAD, branch, or prepared staged tree no longer matches the journal",
            6,
            "queue_a_new_request_without_retrying_this_snapshot",
        )
    capture_ref, capture_head, capture_tree = capture_snapshot(root, artifact_dirs)
    if (capture_ref, capture_head, capture_tree) != (head_ref, head_oid, tree):
        raise LifecycleError(
            "repository_raced", "prepared repository state changed during validation", 6
        )


def atomic_publish_draft(path, data, owned_digest):
    """Create one handoff draft without replacing an existing user draft."""
    parent = os.path.dirname(path)
    lstat_owned(parent, "dir")
    if os.path.realpath(parent) != parent:
        raise LifecycleError("unsafe_draft_path", "commit draft parent is not canonical", 9)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    temporary = os.path.join(
        parent,
        ".%s.draft.%d.%s" % (os.path.basename(path), os.getpid(), secrets.token_hex(6)),
    )
    descriptor = None
    try:
        descriptor = os.open(temporary, flags, 0o600)
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(data):
            offset += os.write(descriptor, data[offset:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        # link(2) is an atomic no-replace publication primitive. os.replace()
        # would clobber a draft created by an editor after preflight.
        os.link(temporary, path)
        fsync_directory(parent)
        os.unlink(temporary)
        fsync_directory(parent)
        return sha256_bytes(data)
    except FileExistsError:
        # A draft is a convenience handoff only. Never make its existence a
        # commit blocker and never overwrite a user/editor generation.
        try:
            existing = read_stable_owned_regular(path, MAX_MESSAGE_BYTES)
        except LifecycleError:
            return None
        if existing is not None and owned_digest is not None and hmac.compare_digest(
            sha256_bytes(existing), owned_digest
        ):
            return owned_digest
        return None
    finally:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(descriptor)
        with contextlib.suppress(OSError):
            os.unlink(temporary)


def head_message_digest(root):
    """Digest of HEAD's commit message, or None when it cannot be read.

    Git leaves the previous successful commit message in COMMIT_EDITMSG. That
    exact scratch value is ours to replace; any other content is someone's work.
    """
    process = git_process(root, ["cat-file", "commit", "HEAD"])
    if process.returncode != 0:
        return None
    _headers, separator, message = process.stdout.partition(b"\n\n")
    if not separator:
        return None
    return sha256_bytes(message)


def classify_draft(path, message_data, owned_digest, replaceable_digests=()):
    """Decide whether one handoff draft may be written, without writing it.

    Returns "publish" (nothing is there), "owned" (already exactly ours, leave
    it), "replace" (a known-stale scratch value we may overwrite), or
    "conflict" (someone else's content, or something we cannot read safely).
    """
    try:
        existing = read_stable_owned_regular(path, MAX_MESSAGE_BYTES)
    except LifecycleError:
        return "conflict"
    if existing is None:
        return "publish"
    digest = sha256_bytes(existing)
    if hmac.compare_digest(digest, sha256_bytes(message_data)):
        return "owned"
    if owned_digest is not None and hmac.compare_digest(digest, owned_digest):
        return "owned"
    for candidate in replaceable_digests:
        if candidate is not None and hmac.compare_digest(digest, candidate):
            return "replace"
    return "conflict"


def write_handoff_drafts(root, git_dir, journal, request, message_data):
    reject_git_operation(root)
    head_ref, head_oid = current_head_state(root)
    if (
        head_ref != request["head_ref"]
        or head_oid != journal["expected_commit_parent"]
    ):
        raise LifecycleError(
            "stale_prepared_state",
            "HEAD changed before commit drafts could be written",
            6,
            "queue_a_new_request_without_overwriting_drafts",
        )
    lazygit_path = os.path.join(git_dir, "LAZYGIT_PENDING_COMMIT")
    commit_editmsg_path = resolve_git_path(root, "COMMIT_EDITMSG")
    if os.path.dirname(commit_editmsg_path) != git_dir:
        raise LifecycleError("unsafe_draft_path", "COMMIT_EDITMSG is outside the worktree Git directory", 9)

    # Classify BOTH drafts before writing EITHER. Publishing one and then
    # refusing on the other would leave a half-written handoff next to somebody
    # else's draft, which is worse than refusing the whole thing.
    lazygit_state = classify_draft(
        lazygit_path, message_data, journal["lazygit_draft_sha256"]
    )
    edit_state = classify_draft(
        commit_editmsg_path,
        message_data,
        journal["commit_editmsg_draft_sha256"],
        (head_message_digest(root),),
    )
    conflicts = [
        name
        for name, state in (
            ("LAZYGIT_PENDING_COMMIT", lazygit_state),
            ("COMMIT_EDITMSG", edit_state),
        )
        if state == "conflict"
    ]
    if conflicts:
        raise LifecycleError(
            "draft_conflict",
            "an unrelated or edited commit draft would be overwritten: "
            + ", ".join(conflicts),
            9,
            "preserve_or_remove_the_foreign_draft_then_recover_with_fresh_authorization",
        )

    lazygit_digest = publish_classified_draft(
        lazygit_path, message_data, journal["lazygit_draft_sha256"], lazygit_state
    )
    edit_digest = publish_classified_draft(
        commit_editmsg_path,
        message_data,
        journal["commit_editmsg_draft_sha256"],
        edit_state,
    )
    return lazygit_digest, edit_digest


def publish_classified_draft(path, message_data, owned_digest, state):
    if state == "owned":
        return owned_digest if owned_digest is not None else sha256_bytes(message_data)
    if state == "replace":
        return replace_owned_draft(path, message_data)
    return atomic_publish_draft(path, message_data, owned_digest)


def replace_owned_draft(path, data):
    """Overwrite a known-stale scratch draft atomically, preserving 0600."""
    parent = os.path.dirname(path)
    temporary = os.path.join(
        parent,
        ".%s.draft.%d.%s" % (os.path.basename(path), os.getpid(), secrets.token_hex(6)),
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = None
    try:
        descriptor = os.open(temporary, flags, 0o600)
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(data):
            offset += os.write(descriptor, data[offset:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
        fsync_directory(parent)
        return sha256_bytes(data)
    except OSError:
        return None
    finally:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(descriptor)
        with contextlib.suppress(OSError):
            os.unlink(temporary)


def exact_selector_arguments(request):
    values = [
        "--session-only",
        "--session-id",
        request["session_id"],
        "--specstory-path",
        request["specstory_path"],
    ]
    if request["plan_policy"] == "path":
        values.extend(["--plan", request["plan_path"]])
    else:
        values.append("--no-plan")
    return values


def require_recovery_authorization(arguments, journal):
    state = journal["state"]
    if state in ("prepared", "rotation_required") and not arguments.allow_commit:
        raise LifecycleError(
            "authorization_required",
            "prepared recovery requires fresh explicit --allow-commit",
            5,
            "retry_with_allow_commit",
        )
    if state == "rotation_required":
        if journal["schema_version"] == 2 and journal["v2"]["review_status"] in ("unresolved", "stale", "not_required"):
            v2.fail("unresolved_review", 10)
        reviewed_fixture = journal["schema_version"] == 2 and journal["v2"]["review_status"] == "reviewed_noncredential"
        if not arguments.rotation_confirmed and not reviewed_fixture:
            raise LifecycleError(
                "rotation_confirmation_required",
                "rotate the exposed credential, then explicitly confirm recovery",
                10,
                "rotate_then_retry_with_allow_commit_and_rotation_confirmed",
            )
    elif arguments.rotation_confirmed:
        raise LifecycleError(
            "invalid_authorization",
            "--rotation-confirmed is valid only for an existing rotation-required run",
            5,
            "retry_without_rotation_confirmed",
        )


def run_stage_transaction(script_dir, root, request, gitleaks_config):
    stage_script = os.path.realpath(os.path.join(script_dir, "stage-agent-artifacts.sh"))
    if not os.path.isfile(stage_script):
        raise LifecycleError("dependency_error", "exact staging helper is missing", 3)
    command = [
        bash_executable(),
        stage_script,
    ] + exact_selector_arguments(request) + [
        "--gitleaks-config",
        gitleaks_config,
        "--expect-index-tree",
        request["index_tree"],
        "--sanitize-index",
        "--materialize-sanitized",
    ]
    if request["schema_version"] == 2:
        command += ["--receipt-request", request["request_path"]]
    process = run_command(
        command, cwd=root, env=git_environment({"AGENT_HISTORY_PYTHON": sys.executable} if request["schema_version"] == 2 else None), suppress_output=True
    )
    if process.returncode not in (0, 10):
        raise LifecycleError(
            "artifact_staging_failed",
            "exact artifact staging or sanitation failed; private output was suppressed",
            7,
            "inspect_dependencies_and_exact_paths_then_retry",
        )
    return process.returncode == 10


def scan_staged_index(script_dir, root, gitleaks_config, *, index_file=None, label):
    scanner = os.path.realpath(os.path.join(script_dir, "scan-staged.sh"))
    if not os.path.isfile(scanner):
        raise LifecycleError("dependency_error", "staged scanner helper is missing", 3)
    environment = git_environment(
        {"GIT_INDEX_FILE": index_file} if index_file is not None else None
    )
    process = run_command(
        [bash_executable(), scanner, "--config", gitleaks_config],
        cwd=root,
        env=environment,
        suppress_output=True,
    )
    if process.returncode != 0:
        raise LifecycleError(
            "staged_secret_scan_failed",
            "%s staged index did not pass the trusted gitleaks scan" % label,
            7,
            "inspect_prepared_snapshot_without_committing",
        )


def scan_composed_message(gitleaks_config, message_data):
    """Fail closed if the bytes passed to ``git commit -F`` contain a secret."""
    gitleaks = shutil.which("gitleaks")
    if not gitleaks:
        raise LifecycleError("dependency_error", "gitleaks is required for commit-message scanning", 3)
    descriptor, report_path = tempfile.mkstemp(prefix="agent-history-message-gitleaks.")
    try:
        os.fchmod(descriptor, 0o600)
        os.close(descriptor)
        descriptor = None
        process = run_command(
            [
                gitleaks,
                "stdin",
                "--config",
                gitleaks_config,
                "--report-format",
                "json",
                "--report-path",
                report_path,
                "--exit-code",
                "0",
            ],
            cwd=os.getcwd(),
            env=git_environment(),
            input_bytes=message_data,
            suppress_output=True,
        )
        if process.returncode != 0:
            raise LifecycleError(
                "message_secret_scan_failed",
                "composed commit message could not be scanned safely",
                7,
            )
        report = read_bounded_file(report_path, MAX_GITLEAKS_REPORT_BYTES, strict_mode=0o600)
        if report:
            try:
                findings = json.loads(
                    report.decode("utf-8", "strict"), parse_constant=_reject_constant
                )
            except (UnicodeError, ValueError, json.JSONDecodeError):
                raise LifecycleError(
                    "message_secret_scan_failed",
                    "composed commit message scan returned an invalid report",
                    7,
                )
            if not isinstance(findings, list) or any(
                not isinstance(finding, dict) for finding in findings
            ):
                raise LifecycleError(
                    "message_secret_scan_failed",
                    "composed commit message scan returned an invalid report",
                    7,
                )
            if findings:
                raise LifecycleError(
                    "message_secret_detected",
                    "composed commit message contains scanner-detected secret material",
                    7,
                    "replace_the_base_message_and_start_a_fresh_wrapper",
                )
    finally:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(descriptor)
        with contextlib.suppress(OSError):
            os.unlink(report_path)


def verify_prepared_artifacts(script_dir, root, request, journal, artifact_dirs, gitleaks_config):
    stage_script = os.path.realpath(os.path.join(script_dir, "stage-agent-artifacts.sh"))
    redactor = os.path.realpath(os.path.join(script_dir, "..", "assets", "redact_secrets.py"))
    if not os.path.isfile(stage_script) or not os.path.isfile(redactor):
        raise LifecycleError("dependency_error", "prepared-state verifier is missing", 3)
    stage_process = run_command(
        [bash_executable(), stage_script]
        + exact_selector_arguments(request)
        + [
            "--gitleaks-config",
            gitleaks_config,
            "--expect-index-tree",
            journal["expected_commit_tree"],
            "--check-staged",
        ],
        cwd=root,
        env=git_environment(),
        suppress_output=True,
    )
    if stage_process.returncode != 0:
        raise LifecycleError(
            "prepared_artifacts_invalid",
            "prepared exact artifacts no longer validate; private output was suppressed",
            7,
            "inspect_prepared_snapshot_without_restaging",
        )
    scan_process = run_command(
        [sys.executable, redactor, "--config", gitleaks_config, "--check-index", "--paths"]
        + list(artifact_dirs),
        cwd=root,
        env=git_environment(),
        suppress_output=True,
    )
    if scan_process.returncode != 0:
        raise LifecycleError(
            "prepared_index_not_clean",
            "prepared staged artifacts did not pass the trusted secret check",
            7,
            "inspect_prepared_snapshot_without_committing",
        )
    scan_staged_index(
        script_dir, root, gitleaks_config, label="prepared"
    )


def _safe_message_bytes(data, label):
    if not data or len(data) > MAX_MESSAGE_BYTES:
        raise LifecycleError("invalid_message", "%s has an invalid size" % label, 7)
    try:
        text = data.decode("utf-8", "strict")
    except UnicodeError:
        raise LifecycleError("invalid_message", "%s is not valid UTF-8" % label, 7)
    safe_text(text, allow_message_controls=True, label=label)
    return data


def derive_metadata(script_dir, root):
    metadata_script = os.path.realpath(
        os.path.join(script_dir, "agent-commit-metadata.sh")
    )
    if not os.path.isfile(metadata_script):
        raise LifecycleError("dependency_error", "metadata helper is missing", 3)
    process = run_command(
        [bash_executable(), metadata_script],
        cwd=root,
        env=git_environment(),
    )
    if process.returncode != 0:
        raise LifecycleError(
            "metadata_failed",
            "canonical staged metadata could not be derived; private output was suppressed",
            7,
            "inspect_staged_provenance_without_committing",
        )
    data = _safe_message_bytes(process.stdout, "metadata output")
    if not data.endswith(b"\n"):
        raise LifecycleError("metadata_failed", "metadata output was not canonical", 7)
    lines = data.splitlines()
    if not lines or any(
        not line.startswith(
            (b"AI-Assisted-By: ", b"Agent-Transcript: ", b"Agent-Plan: ")
        )
        for line in lines
    ):
        raise LifecycleError("metadata_failed", "metadata output was not canonical", 7)
    if not any(line.startswith(b"AI-Assisted-By: ") for line in lines):
        raise LifecycleError("metadata_failed", "metadata omitted assistant provenance", 7)
    return data


def parse_message_trailers(root, message_data):
    process = git_process(root, ["interpret-trailers", "--parse"], input_bytes=message_data)
    if process.returncode != 0 or len(process.stdout) > MAX_MESSAGE_BYTES:
        raise LifecycleError("message_validation_failed", "Git could not parse commit trailers", 7)
    return process.stdout.splitlines()


def validate_managed_message(root, message_data, request, expected_metadata_lines=None):
    parsed = parse_message_trailers(root, message_data)
    managed = []
    for line in parsed:
        key, separator, _value = line.partition(b":")
        if separator and key.lower() in MANAGED_TRAILER_KEYS_LOWER:
            if key not in MANAGED_TRAILER_KEYS:
                raise LifecycleError(
                    "message_validation_failed",
                    "managed commit trailer names must use canonical casing",
                    7,
                )
            managed.append(line)

    if len(managed) != len(set(managed)):
        raise LifecycleError(
            "message_validation_failed",
            "managed commit trailers must each occur exactly once",
            7,
        )

    request_line = (
        "Agent-History-Request: %s" % request["request_id"]
    ).encode("ascii")
    transcript_line = (
        "Agent-Transcript: %s" % request["specstory_path"]
    ).encode("utf-8")
    assistant_lines = [
        line for line in managed if line.startswith(b"AI-Assisted-By: ")
    ]
    transcript_lines = [
        line for line in managed if line.startswith(b"Agent-Transcript: ")
    ]
    plan_lines = [line for line in managed if line.startswith(b"Agent-Plan: ")]
    request_lines = [
        line for line in managed if line.startswith(b"Agent-History-Request: ")
    ]
    if not assistant_lines or transcript_lines != [transcript_line] or request_lines != [request_line]:
        raise LifecycleError(
            "message_validation_failed",
            "managed commit provenance is incomplete or noncanonical",
            7,
        )
    if request["plan_policy"] == "path":
        plan_line = ("Agent-Plan: %s" % request["plan_path"]).encode("utf-8")
        if plan_lines != [plan_line]:
            raise LifecycleError(
                "message_validation_failed",
                "managed plan provenance is incomplete or noncanonical",
                7,
            )
    elif plan_lines:
        raise LifecycleError(
            "message_validation_failed",
            "a no-plan request must not claim managed plan provenance",
            7,
        )

    if expected_metadata_lines is not None:
        expected = list(expected_metadata_lines) + [request_line]
        if managed != expected:
            raise LifecycleError(
                "message_validation_failed",
                "composed message did not retain its complete canonical trailer set",
                7,
            )
    return managed


def compose_message(root, base_data, metadata_data, request):
    _safe_message_bytes(base_data, "base message")
    base_trailers = parse_message_trailers(root, base_data)
    for line in base_trailers:
        key, separator, _value = line.partition(b":")
        if separator and key.lower() in MANAGED_TRAILER_KEYS_LOWER:
            raise LifecycleError(
                "message_validation_failed",
                "base message must not contain lifecycle-managed trailers",
                7,
            )

    base = base_data.rstrip(b"\n")
    separator = b"\n" if base_trailers else b"\n\n"
    request_line = (
        "Agent-History-Request: %s" % request["request_id"]
    ).encode("ascii")
    metadata_lines = metadata_data.rstrip(b"\n").splitlines()
    composed = base + separator + b"\n".join(metadata_lines + [request_line]) + b"\n"
    _safe_message_bytes(composed, "composed message")

    validate_managed_message(
        root, composed, request, expected_metadata_lines=metadata_lines
    )
    return composed


def write_owned_composed(path, data, owned_digest):
    if os.path.lexists(path):
        existing = read_bounded_file(path, MAX_MESSAGE_BYTES, strict_mode=0o600)
        existing_digest = sha256_bytes(existing)
        if existing == data:
            return
        if owned_digest is None or not hmac.compare_digest(existing_digest, owned_digest):
            raise LifecycleError(
                "message_conflict",
                "owned composed message changed outside this lifecycle",
                9,
                "preserve_or_remove_changed_composed_message",
            )
    atomic_write(path, data, 0o600)


def validate_composed_message(
    script_dir, root, composed_path, composed_data, metadata_data, request
):
    stored = read_bounded_file(composed_path, MAX_MESSAGE_BYTES, strict_mode=0o600)
    if not hmac.compare_digest(stored, composed_data):
        raise LifecycleError(
            "message_validation_failed",
            "owned composed message did not match its canonical in-memory form",
            7,
        )
    metadata_lines = metadata_data.rstrip(b"\n").splitlines()
    validate_managed_message(
        root, stored, request, expected_metadata_lines=metadata_lines
    )

    checker = os.path.realpath(
        os.path.join(
            script_dir,
            "..",
            "..",
            "git-workflow",
            "scripts",
            "check-commit-msg.sh",
        )
    )
    if not os.path.isfile(checker):
        return
    process = run_command(
        [
            bash_executable(),
            checker,
            "--agentic",
            "--staged",
            "--file",
            composed_path,
        ],
        cwd=root,
        env=git_environment(),
        suppress_output=True,
    )
    if process.returncode != 0:
        raise LifecycleError(
            "message_validation_failed",
            "composed commit message failed companion style validation; output was suppressed",
            7,
            "fix_the_base_message_and_queue_a_new_request",
        )


@contextlib.contextmanager
def private_umask():
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


def file_generation(info):
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def copy_private_regular(source, destination):
    source_flags = os.O_RDONLY
    destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        source_flags |= os.O_NOFOLLOW
        destination_flags |= os.O_NOFOLLOW
    source_descriptor = os.open(source, source_flags)
    destination_descriptor = None
    destination_identity = None
    try:
        source_info = os.fstat(source_descriptor)
        if not stat.S_ISREG(source_info.st_mode) or source_info.st_uid != os.getuid():
            raise LifecycleError(
                "unsafe_index",
                "canonical index must be an owned regular file",
                6,
            )
        destination_descriptor = os.open(destination, destination_flags, 0o600)
        destination_info = os.fstat(destination_descriptor)
        destination_identity = (destination_info.st_dev, destination_info.st_ino)
        os.fchmod(destination_descriptor, 0o600)
        while True:
            chunk = os.read(source_descriptor, 1024 * 1024)
            if not chunk:
                break
            offset = 0
            while offset < len(chunk):
                written = os.write(destination_descriptor, chunk[offset:])
                if written <= 0:
                    raise OSError("short alternate-index write")
                offset += written
        os.fsync(destination_descriptor)
        source_after = os.fstat(source_descriptor)
        path_after = os.lstat(source)
        if (
            file_generation(source_info) != file_generation(source_after)
            or (path_after.st_dev, path_after.st_ino)
            != (source_after.st_dev, source_after.st_ino)
            or stat.S_ISLNK(path_after.st_mode)
        ):
            raise LifecycleError(
                "repository_raced",
                "canonical index changed while its frozen copy was created",
                6,
            )
    except BaseException:
        if destination_descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(destination_descriptor)
            destination_descriptor = None
        if destination_identity is not None:
            with contextlib.suppress(OSError):
                current = os.lstat(destination)
                if destination_identity == (current.st_dev, current.st_ino):
                    os.unlink(destination)
        raise
    finally:
        if destination_descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(destination_descriptor)
        with contextlib.suppress(OSError):
            os.close(source_descriptor)


def remove_owned_regular(path):
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
    ):
        return False
    try:
        os.unlink(path)
    except OSError:
        return False
    return True


def cleanup_frozen_index(frozen_index_path):
    lock_removed = remove_owned_regular(frozen_index_path + ".lock")
    index_removed = remove_owned_regular(frozen_index_path)
    if lock_removed and index_removed:
        with contextlib.suppress(OSError):
            fsync_directory(os.path.dirname(frozen_index_path))
        return True
    return False


def staged_regular_blob_oid(root, path, index_file=None):
    extra = {"GIT_INDEX_FILE": index_file} if index_file is not None else None
    records = [
        item
        for item in git_bytes(
            root, ["ls-files", "--stage", "-z", "--", path], env_extra=extra
        ).split(b"\0")
        if item
    ]
    if len(records) != 1:
        raise LifecycleError("prepared_artifacts_invalid", "an exact prepared artifact index entry is unavailable", 7)
    metadata, separator, raw_path = records[0].partition(b"\t")
    fields = metadata.split(b" ")
    if not separator or len(fields) != 3:
        raise LifecycleError("prepared_artifacts_invalid", "an exact prepared artifact index entry is malformed", 7)
    try:
        mode = fields[0].decode("ascii", "strict")
        oid = fields[1].decode("ascii", "strict")
        stage = fields[2].decode("ascii", "strict")
        decoded_path = raw_path.decode("utf-8", "strict")
    except UnicodeError:
        raise LifecycleError("prepared_artifacts_invalid", "an exact prepared artifact index entry is malformed", 7)
    if decoded_path != path or mode not in ("100644", "100755") or stage != "0":
        raise LifecycleError("prepared_artifacts_invalid", "an exact prepared artifact index entry is unsafe", 7)
    validate_oid(oid, "prepared artifact object")
    return oid


def verify_prepared_artifact_clean_hashes(
    root, request, artifact_dirs, expected_tree, index_file
):
    """Prove selected live files still filter-hash to the prepared index blobs.

    `index_file` must be a private copy of the commit index, never the canonical
    one: the caller holds `.git/index.lock` for the whole check, and `git
    write-tree`/`ls-files` against the canonical index would need that same lock.
    Reading the frozen copy is also what makes the check meaningful -- it proves
    the bytes about to be committed, not whatever the live index holds.
    """
    if index_tree(root, index_file) != expected_tree:
        raise LifecycleError(
            "stale_prepared_state",
            "prepared staged tree changed before the frozen commit index was created",
            6,
            "queue_a_new_request_without_retrying_this_snapshot",
        )
    selectors = [("SpecStory", request["specstory_path"])]
    if request["plan_policy"] == "path":
        selectors.append(("plan", request["plan_path"]))
    for kind, path in selectors:
        relative, absolute = resolve_repo_file(root, path, kind, artifact_dirs)
        if relative != path:
            raise LifecycleError("prepared_artifacts_invalid", "an exact artifact selector changed form", 7)
        before = fingerprint(absolute)
        expected_oid = staged_regular_blob_oid(root, relative, index_file)
        process = git_process(
            root,
            ["hash-object", "--path=%s" % relative, "--", absolute],
        )
        after = fingerprint(absolute)
        if process.returncode != 0 or before != after:
            raise LifecycleError(
                "prepared_artifact_changed",
                "an exact artifact changed while its prepared hash was checked",
                7,
                "start_a_fresh_wrapper_and_requeue",
            )
        try:
            actual_oid = decode_line(process.stdout, "prepared artifact filtered hash")
        except LifecycleError:
            raise LifecycleError("prepared_artifact_changed", "an exact artifact hash could not be proven", 7)
        validate_oid(actual_oid, "prepared artifact filtered hash")
        if not hmac.compare_digest(actual_oid, expected_oid):
            raise LifecycleError(
                "prepared_artifact_not_clean",
                "an exact artifact no longer matches its prepared staged blob",
                7,
                "start_a_fresh_wrapper_and_requeue",
            )
    if index_tree(root, index_file) != expected_tree:
        raise LifecycleError(
            "stale_prepared_state",
            "prepared staged tree changed during exact artifact hash verification",
            6,
            "queue_a_new_request_without_retrying_this_snapshot",
        )


def freeze_commit_index(root, git_dir, request, journal, artifact_dirs):
    canonical_index = resolve_git_path(root, "index")
    if os.path.dirname(canonical_index) != git_dir:
        raise LifecycleError(
            "unsafe_index", "canonical index is outside the worktree Git directory", 6
        )
    canonical_lock = canonical_index + ".lock"
    frozen_index = os.path.join(
        git_dir,
        "index.agent-history-commit.%s.%s"
        % (request["request_id"], secrets.token_hex(8)),
    )
    frozen_index_owned = False
    try:
        with private_umask():
            with ExclusiveLock(canonical_lock):
                before_ref, before_parent = current_head_state(root)
                copy_private_regular(canonical_index, frozen_index)
                frozen_index_owned = True
                frozen_tree = index_tree(root, frozen_index)
                os.chmod(frozen_index, 0o600)
                verify_prepared_artifact_clean_hashes(
                    root,
                    request,
                    artifact_dirs,
                    journal["expected_commit_tree"],
                    frozen_index,
                )
                after_ref, after_parent = current_head_state(root)
                if (before_ref, before_parent) != (after_ref, after_parent):
                    raise LifecycleError(
                        "repository_raced",
                        "HEAD changed while the commit index was frozen",
                        6,
                    )
                if (
                    before_ref != request["head_ref"]
                    or before_parent != journal["expected_commit_parent"]
                    or frozen_tree != journal["expected_commit_tree"]
                ):
                    raise LifecycleError(
                        "stale_prepared_state",
                        "prepared parent, branch, or tree changed before commit",
                        6,
                        "queue_a_new_request_without_retrying_this_snapshot",
                    )
                fsync_directory(git_dir)
        return frozen_index
    except BaseException:
        if frozen_index_owned:
            cleanup_frozen_index(frozen_index)
        raise


def read_stable_owned_regular(path, limit):
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError:
        raise LifecycleError("unsafe_state", "commit draft is not safely readable")
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid():
            raise LifecycleError("unsafe_state", "commit draft is not an owned regular file")
        chunks = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise LifecycleError("oversized_file", "commit draft exceeds the size bound")
        after = os.fstat(descriptor)
        path_after = os.lstat(path)
        if (
            file_generation(before) != file_generation(after)
            or (path_after.st_dev, path_after.st_ino) != (after.st_dev, after.st_ino)
            or stat.S_ISLNK(path_after.st_mode)
        ):
            raise LifecycleError("unsafe_state", "commit draft changed while it was read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def adopt_failed_commit_editmsg(root, git_dir, previous_digest):
    try:
        path = resolve_git_path(root, "COMMIT_EDITMSG")
        if os.path.dirname(path) != git_dir:
            return previous_digest
        data = read_stable_owned_regular(path, MAX_MESSAGE_BYTES)
    except (LifecycleError, OSError):
        return previous_digest
    if data is None:
        return previous_digest
    return sha256_bytes(data)


def commit_environment(request, frozen_index_path):
    environment = git_environment()
    for key in tuple(environment):
        if (
            key.startswith("AGENT_HISTORY_")
            or key.startswith("GIT_CONFIG_KEY_")
            or key.startswith("GIT_CONFIG_VALUE_")
        ):
            environment.pop(key, None)
    for key in (
        "SKIP",
        "GIT_CONFIG",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
        "GIT_CONFIG_NOSYSTEM",
    ):
        environment.pop(key, None)
    environment["GIT_INDEX_FILE"] = frozen_index_path
    environment["AGENT_HISTORY_SESSION_ID"] = request["session_id"]
    environment["AGENT_HISTORY_SPECSTORY_PATH"] = request["specstory_path"]
    if request["plan_policy"] == "path":
        environment["AGENT_HISTORY_PLAN"] = request["plan_path"]
    else:
        environment["AGENT_HISTORY_NO_PLAN"] = "1"
    return environment


def snapshot_matches_expected(root, request, journal):
    try:
        head_ref, head_oid = current_head_state(root)
        tree = index_tree(root)
    except LifecycleError:
        return False
    return (
        head_ref == request["head_ref"]
        and head_oid == journal["expected_commit_parent"]
        and tree == journal["expected_commit_tree"]
    )


def cmd_finalize(arguments):
    root, git_dir = discover_repository()
    request_path = arguments.request
    validate_absolute_path(request_path, "request path")
    run_id, run_dir, journal_path, journal, request, _request_digest = parse_request_for_finalize(
        request_path, root, git_dir
    )
    if arguments.prepare_only and (journal["schema_version"] != 2 or not arguments.allow_commit or arguments.runner_token):
        v2.fail("invalid_authorization", 5)
    if arguments.preview_review:
        if arguments.allow_commit or arguments.runner_token or arguments.review_file or arguments.rotation_confirmed or arguments.prepare_only or not arguments.json:
            raise LifecycleError("invalid_arguments", "preview requires only --preview-review --json", 1)
        if arguments.reconcile_only:
            v2.fail("invalid_arguments", 1)
        if arguments.expected_revision is not None:
            validate_sha256(arguments.expected_revision)
            if journal["schema_version"] != 2 or arguments.expected_revision != v2.digest(journal):
                v2.fail("stale_revision", 6)
        return v2.preview(arguments, root, git_dir, journal, request)
    if arguments.review_file and journal["schema_version"] != 2:
        v2.fail("legacy_review_unsupported", 4)
    lock_path = os.path.join(run_dir, "finalize.lock")
    with ExclusiveLock(lock_path):
        # Re-read under the lock so authorization and state cannot race another finalizer.
        run_id, run_dir, journal_path, journal, request, _request_digest = parse_request_for_finalize(
            request_path, root, git_dir
        )
        if arguments.expected_revision is not None:
            validate_sha256(arguments.expected_revision)
            if journal["schema_version"] != 2 or arguments.expected_revision != v2.digest(journal):
                v2.fail("stale_revision", 6)
        if arguments.reconcile_only and (journal["schema_version"] != 2 or journal["state"] not in ("committing", "done")):
            v2.fail("reconciliation_required", 8)
        authorize_finalize(arguments, journal)
        if journal["schema_version"] == 2 and journal["state"] in ("committing", "done"):
            v2.reprove_receipt(arguments.script_dir, root, request, journal, committed=True)
        recovered = reconcile_commit(root, git_dir, request, journal_path, journal)
        if recovered is not None:
            emit(
                {
                    "status": "already_committed",
                    "request_id": run_id,
                    "commit_oid": recovered,
                    "commit_attempted": False,
                }
            )
            return 0
        if not request_lifecycle_is_proven(journal, request):
            raise LifecycleError(
                "lifecycle_unproven",
                "a successful child exit and exact completed sync are required",
                6,
                "return_to_the_outer_runner_or_start_a_new_run",
            )

        artifact_dirs = load_artifact_dirs(arguments.script_dir)
        # Pin the scanner policy to the request's own HEAD once, into private run
        # state, before anything scans. Every later sanitation/validation pass
        # must read that exact immutable copy rather than a worktree file the
        # session could still be editing.
        journal, gitleaks_config = ensure_trusted_gitleaks_config(
            arguments.script_dir, root, request, journal_path, journal
        )
        if journal["schema_version"] == 2:
            if journal["state"] in ("prepared", "rotation_required"):
                journal = v2.apply_review(arguments, root, request, journal_path, journal)
            else:
                if arguments.review_file:
                    v2.fail("review_not_ready", 5)
                if journal["v2"]["receipt_revision"] or os.path.lexists(os.path.join(run_dir, "sanitation.json")):
                    v2.fail("publication_unproven", 8)
                v2.reprove_freshness(arguments.script_dir, root, request, journal)
        require_recovery_authorization(arguments, journal)
        recovering_prepared = journal["state"] in ("prepared", "rotation_required")
        recovering_rotation = journal["state"] == "rotation_required"
        if recovering_prepared:
            validate_prepared_snapshot(root, request, journal, artifact_dirs)
        else:
            validate_current_snapshot(root, request, artifact_dirs)

        specstory_relative, specstory_absolute = resolve_repo_file(
            root, request["specstory_path"], "SpecStory", artifact_dirs
        )
        if specstory_relative != request["specstory_path"]:
            raise LifecycleError("request_mismatch", "SpecStory selector changed canonical form", 7)
        before = fingerprint(specstory_absolute)
        run_find_session(arguments.script_dir, root, request)
        after = fingerprint(specstory_absolute)
        if before != after:
            raise LifecycleError(
                "active_writer",
                "the selected transcript changed during exact validation",
                7,
                "stop_transcript_writer_then_retry",
            )
        reject_active_writer(specstory_absolute)
        if request["plan_policy"] == "path":
            plan_relative, _plan_absolute = resolve_repo_file(
                root, request["plan_path"], "plan", artifact_dirs
            )
            if plan_relative != request["plan_path"]:
                raise LifecycleError("request_mismatch", "plan selector changed canonical form", 7)

        if recovering_prepared:
            verify_prepared_artifacts(
                arguments.script_dir,
                root,
                request,
                journal,
                artifact_dirs,
                gitleaks_config,
            )
        else:
            try:
                rotation_required = run_stage_transaction(
                    arguments.script_dir, root, request, gitleaks_config
                )
                if journal["schema_version"] == 2:
                    _dir, _path, journal = validate_run_layout(request_path, root, git_dir, run_id)
                    journal = v2.record_publication(root, request, journal_path, journal)
            except (LifecycleError, OSError) as error:
                if journal["schema_version"] == 2:
                    # The stage process may have published some/all sanitized
                    # paths before an index/receipt I/O failed. Never claim a
                    # rollback and never authorize a replay of that transaction.
                    _dir, _path, journal = validate_run_layout(request_path, root, git_dir, run_id)
                    partial = os.path.lexists(os.path.join(run_dir, "sanitation.json"))
                    update_journal(journal_path, journal, state="failed",
                                   failure_code="publication_unproven" if partial else "artifact_staging_failed")
                    if partial:
                        v2.fail("publication_unproven", 8)
                else:
                    update_journal(journal_path, journal, failure_code=getattr(error, "code", "filesystem_error"))
                raise
            try:
                prepared_ref, prepared_parent = current_head_state(root)
                prepared_tree = index_tree(root)
                after_ref, after_parent = current_head_state(root)
            except LifecycleError as error:
                update_journal(
                    journal_path,
                    journal,
                    state="failed",
                    failure_code="prepared_snapshot_unreadable",
                )
                raise error
            if (
                (prepared_ref, prepared_parent) != (after_ref, after_parent)
                or prepared_ref != request["head_ref"]
                or prepared_parent != request["head_oid"]
            ):
                update_journal(
                    journal_path,
                    journal,
                    state="failed",
                    failure_code="repository_raced_after_staging",
                )
                raise LifecycleError(
                    "repository_raced",
                    "HEAD or branch changed while exact artifacts were prepared",
                    6,
                    "queue_a_new_request_without_committing",
                )
            prepared_state = "rotation_required" if rotation_required else "prepared"
            journal = update_journal(
                journal_path,
                journal,
                state=prepared_state,
                staging_ready=True,
                expected_commit_parent=prepared_parent,
                expected_commit_tree=prepared_tree,
                commit_oid=None,
                failure_code=None,
            )

        base_data = read_bounded_file(
            request["base_message_path"], MAX_MESSAGE_BYTES, strict_mode=0o600
        )
        if sha256_bytes(base_data) != request["base_message_sha256"]:
            journal = update_journal(
                journal_path, journal, failure_code="message_mismatch"
            )
            raise LifecycleError("message_mismatch", "owned base message digest changed", 7)

        try:
            metadata_data = derive_metadata(arguments.script_dir, root)
            composed_data = compose_message(root, base_data, metadata_data, request)
            composed_path = journal["composed_message_path"]
            write_owned_composed(
                composed_path, composed_data, journal["composed_message_sha256"]
            )
            validate_composed_message(
                arguments.script_dir,
                root,
                composed_path,
                composed_data,
                metadata_data,
                request,
            )
        except LifecycleError as error:
            update_journal(journal_path, journal, failure_code=error.code)
            raise

        composed_digest = sha256_bytes(composed_data)
        journal = update_journal(
            journal_path,
            journal,
            composed_message_sha256=composed_digest,
            message_ready=True,
            failure_code=None,
        )
        try:
            lazy_digest, edit_digest = write_handoff_drafts(
                root, git_dir, journal, request, composed_data
            )
        except LifecycleError as error:
            update_journal(journal_path, journal, failure_code=error.code)
            raise
        journal = update_journal(
            journal_path,
            journal,
            lazygit_draft_sha256=lazy_digest,
            commit_editmsg_draft_sha256=edit_digest,
            failure_code=None,
        )

        if journal["state"] == "rotation_required" and not recovering_rotation:
            emit(
                {
                    "status": "rotation_required",
                    "request_id": run_id,
                    "commit_attempted": False,
                    "staging_ready": True,
                    "message_ready": True,
                    "drafts_ready": True,
                    "next_action": "rotate_then_recover_with_rotation_confirmed",
                }
            )
            log(
                "agent-history: status=rotation_required; next=rotate credential, then confirm recovery"
            )
            return 10

        if arguments.prepare_only:
            v2.reprove_receipt(arguments.script_dir, root, request, journal)
            emit({"schema_version": 2, "protocol_version": 2, "status": "prepared",
                  "request_id": run_id, "prepared_tree": journal["expected_commit_tree"],
                  "commit_attempted": False, "next_action": "revalidate_external_guard_then_finalize"})
            return 0

        # Message derivation and draft I/O can take time. Re-prove the exact
        # prepared parent/ref/tree, then freeze the canonical index under its
        # own lock before granting one ordinary commit process authority.
        validate_prepared_snapshot(root, request, journal, artifact_dirs)
        try:
            frozen_index = freeze_commit_index(
                root, git_dir, request, journal, artifact_dirs
            )
        except LifecycleError as error:
            update_journal(journal_path, journal, failure_code=error.code)
            raise
        failed_editmsg_digest = None
        try:
            if journal["schema_version"] == 2:
                receipt, _revision = v2.reprove_receipt(arguments.script_dir, root, request, journal)
                checker = v2.redactor_module(root, gitleaks_config, frozen_index)
                try:
                    audit, coverage = checker.audit_full_index()
                except (checker.ScannerError, checker.IndexOperationError, checker.PathSafetyError, checker.TransformationError):
                    v2.fail("frozen_index_coverage_unproven")
                if audit.has_findings or coverage != receipt["coverage"]:
                    v2.fail("frozen_index_coverage_unproven")
                # Scanner/tool/source drift during this last pass is not approval.
                v2.reprove_receipt(arguments.script_dir, root, request, journal)
            journal = update_journal(
                journal_path,
                journal,
                state="committing",
                commit_oid=None,
                failure_code=None,
            )
            try:
                with private_umask():
                    commit_process = run_command(
                        ["git", "commit", "-F", composed_path, "--cleanup=whitespace"],
                        cwd=root,
                        env=commit_environment(request, frozen_index),
                        suppress_output=True,
                    )
            except LifecycleError as error:
                failed_editmsg_digest = adopt_failed_commit_editmsg(
                    root, git_dir, journal["commit_editmsg_draft_sha256"]
                )
                if snapshot_matches_expected(root, request, journal):
                    update_journal(
                        journal_path,
                        journal,
                        state="prepared",
                        commit_editmsg_draft_sha256=failed_editmsg_digest,
                        failure_code=error.code,
                    )
                raise
            if commit_process.returncode != 0:
                failed_editmsg_digest = adopt_failed_commit_editmsg(
                    root, git_dir, journal["commit_editmsg_draft_sha256"]
                )
        finally:
            if not cleanup_frozen_index(frozen_index):
                log("agent-history: private commit-index cleanup was incomplete")

        if commit_process.returncode != 0:
            if snapshot_matches_expected(root, request, journal):
                journal = update_journal(
                    journal_path,
                    journal,
                    state="prepared",
                    commit_oid=None,
                    commit_editmsg_draft_sha256=failed_editmsg_digest,
                    failure_code="commit_failed",
                )
                emit(
                    {
                        "status": "commit_failed",
                        "request_id": run_id,
                        "commit_attempted": True,
                        "commit_oid": None,
                        "prepared_snapshot_retained": True,
                        "next_action": "fix_hook_failure_then_retry_with_allow_commit",
                    }
                )
                log(
                    "agent-history: status=commit_failed; prepared snapshot and drafts retained"
                )
                return 11

            try:
                failed_ref, failed_head = current_head_state(root)
            except LifecycleError:
                failed_ref, failed_head = None, None
            if (
                failed_ref != request["head_ref"]
                or failed_head != journal["expected_commit_parent"]
            ):
                update_journal(
                    journal_path,
                    journal,
                    state="committing",
                    failure_code="commit_outcome_unproven",
                )
                emit(
                    {
                        "status": "commit_recovery_required",
                        "request_id": run_id,
                        "commit_attempted": True,
                        "commit_oid": None,
                        "next_action": "reconcile_exact_head_without_retrying",
                    }
                )
                log(
                    "agent-history: status=commit_recovery_required; do not retry the commit"
                )
                return 8

            update_journal(
                journal_path,
                journal,
                state="failed",
                failure_code="commit_failed_snapshot_changed",
            )
            emit(
                {
                    "status": "commit_failed_snapshot_changed",
                    "request_id": run_id,
                    "commit_attempted": True,
                    "commit_oid": None,
                    "next_action": "inspect_changed_index_without_retrying",
                }
            )
            log(
                "agent-history: status=commit_failed_snapshot_changed; do not retry this request"
            )
            return 11

        try:
            committed_ref, committed_oid = current_head_state(root)
        except LifecycleError:
            committed_ref, committed_oid = None, None
        if (
            committed_ref != request["head_ref"]
            or not committed_oid
            or not commit_matches_request(
                root, committed_oid, request, journal, require_trailer=True
            )
        ):
            update_journal(
                journal_path,
                journal,
                state="committing",
                failure_code="commit_outcome_unproven",
            )
            emit(
                {
                    "status": "commit_recovery_required",
                    "request_id": run_id,
                    "commit_attempted": True,
                    "commit_oid": None,
                    "next_action": "reconcile_exact_head_without_retrying",
                }
            )
            log(
                "agent-history: status=commit_recovery_required; do not retry the commit"
            )
            return 8

        journal = update_journal(
            journal_path,
            journal,
            state="done",
            commit_oid=committed_oid,
            failure_code=None,
        )
        remove_matching_lazygit_draft(git_dir, journal)
        emit(
            {
                "status": "committed",
                "request_id": run_id,
                "commit_oid": committed_oid,
                "commit_attempted": True,
            }
        )
        log("agent-history: status=committed; exact prepared snapshot recorded")
        return 0


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message):
        raise LifecycleError("invalid_arguments", "invalid lifecycle arguments; values suppressed", 2,
                             "inspect_public_help")


def build_parser():
    parser = SafeArgumentParser(add_help=False)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", add_help=False)
    run_parser.add_argument("--script-dir", required=True)
    run_parser.add_argument("--provider", required=True)
    run_parser.add_argument("--allow-commit", action="store_true")
    run_parser.add_argument("--protocol-version", type=int, choices=(1, 2), default=2)
    run_parser.add_argument("--cloud-sync", action="store_true")
    run_parser.add_argument("specstory_args", nargs=argparse.REMAINDER)
    run_parser.set_defaults(function=cmd_run)

    queue_parser = subparsers.add_parser("queue", add_help=False)
    queue_parser.add_argument("--script-dir", required=True)
    queue_parser.add_argument("--session-id", required=True)
    queue_parser.add_argument("--specstory-path", required=True)
    plan_group = queue_parser.add_mutually_exclusive_group(required=True)
    plan_group.add_argument("--plan")
    plan_group.add_argument("--no-plan", action="store_true")
    queue_parser.add_argument("--message-file", required=True)
    queue_parser.set_defaults(function=cmd_queue)

    final_parser = subparsers.add_parser("finalize", add_help=False)
    final_parser.add_argument("--script-dir", required=True)
    final_parser.add_argument("--request", required=True)
    final_parser.add_argument("--allow-commit", action="store_true")
    final_parser.add_argument("--runner-token")
    final_parser.add_argument("--rotation-confirmed", action="store_true")
    final_parser.add_argument("--prepare-only", action="store_true")
    final_parser.add_argument("--expected-revision")
    final_parser.add_argument("--reconcile-only", action="store_true")
    final_parser.add_argument("--preview-review", action="store_true")
    final_parser.add_argument("--review-file")
    final_parser.add_argument("--json", action="store_true")
    final_parser.set_defaults(function=cmd_finalize)
    for name, function in (("capabilities", v2.capabilities), ("inspect", v2.inspect)):
        api = subparsers.add_parser(name, add_help=False)
        api.add_argument("--script-dir", default=os.path.dirname(os.path.realpath(__file__)))
        api.add_argument("--json", action="store_true", required=True)
        if name == "inspect":
            selector = api.add_mutually_exclusive_group(required=True)
            selector.add_argument("--request")
            selector.add_argument("--context", action="store_true")
        api.set_defaults(function=function)
    return parser


def main():
    parser = build_parser()
    try:
        arguments = parser.parse_args()
        if arguments.command == "queue":
            validate_uuid(arguments.session_id, "session id")
        return arguments.function(arguments)
    except LifecycleError as error:
        emit(
            {
                "status": error.code,
                "next_action": error.next_action,
                "commit_attempted": False,
            }
        )
        log("agent-history: status=%s; next=%s" % (error.code, error.next_action))
        return error.exit_code
    except OSError:
        with contextlib.suppress(OSError):
            emit(
                {
                    "status": "filesystem_error",
                    "next_action": "inspect_private_run_state_before_retrying",
                }
            )
        with contextlib.suppress(OSError):
            log("agent-history: status=filesystem_error; private details suppressed")
        return 4
    except (TypeError, ValueError, KeyError, AttributeError, UnicodeError, RecursionError):
        # Hostile private JSON must never make Python render raw values in a
        # traceback. Strict evidence failures remain finite secret-free output.
        emit({"status": "invalid_evidence", "next_action": "inspect_private_run_state_without_retrying"})
        log("agent-history: status=invalid_evidence; private details suppressed")
        return 4
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
