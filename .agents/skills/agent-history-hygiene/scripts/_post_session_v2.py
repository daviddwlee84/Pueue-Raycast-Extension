"""Versioned evidence for the existing post-session transaction; no commit engine.

All raw beforeimages and transformation inputs live only in private run state.
The importing lifecycle module supplies ``p`` to share its strict primitives.
"""
import base64
import contextlib
import hashlib
import json
import os
import re
import selectors
import shutil
import stat
import subprocess
import sys
import time

p = None
CAPABILITIES = ["inspect_v2", "authentic_context_v2", "queue_idempotent_v2",
                "sanitation_receipt_v2", "review_v2", "exact_export_v2", "no_cloud_v2",
                "prepare_only_v2", "revision_guard_v2", "reconcile_only_v2"]
MAX_SOURCE = 8 * 1024 * 1024
MAX_RECEIPT = 64 * 1024 * 1024
MAX_FINDINGS = 4096
JOURNAL_FIELDS = {"helper_revision", "revision", "cloud_sync", "automatic_commit", "resume_session_id", "repository_identity", "wrapper", "child_pid",
                  "group_quiescent", "freshness", "receipt_revision", "publication_revision",
                  "review_revision", "review_status", "native_tool"}
REVIEW_STATES = {"not_required", "unresolved", "reviewed_noncredential",
                 "credential_rotation_required", "stale"}
DISPOSITIONS = {"unresolved", "reviewed_noncredential", "credential_rotation_required"}


def fail(code="evidence_unproven", exit_code=7):
    raise p.LifecycleError(code, "versioned evidence could not be proven", exit_code,
                           "inspect_private_run_state_without_retrying")


def digest(value):
    return p.sha256_bytes(p.canonical_json_bytes(value))


def generation(info):
    return p.file_generation(info) + (stat.S_IMODE(info.st_mode), info.st_uid)


def read_generation(path, limit=MAX_SOURCE, private=False):
    if os.path.realpath(path) != path:
        fail("unsafe_source")
    before = os.lstat(path)
    if private and stat.S_IMODE(before.st_mode) != 0o600:
        fail("unsafe_mode")
    data = p.read_stable_owned_regular(path, limit)
    after = os.lstat(path)
    if data is None or generation(before) != generation(after):
        fail("source_changed")
    return data, {"path": path, "generation": list(generation(after)),
                  "sha256": p.sha256_bytes(data)}


def native_tool(path):
    if not isinstance(path, str) or not path:
        fail("unsupported_tool", 3)
    path = os.path.realpath(path)
    info = os.stat(path)
    if not stat.S_ISREG(info.st_mode) or not os.access(path, os.X_OK):
        fail("unsupported_tool")
    # Tools can be system-owned. Identity and full digest, never execute a
    # version command just to inspect capabilities.
    with open(path, "rb") as stream:
        hasher = hashlib.sha256()
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    if generation(info) != generation(os.stat(path)):
        fail("tool_changed")
    return {"path": path, "generation": list(generation(info)),
            "sha256": hasher.hexdigest()}


def helper_revision(script_dir):
    names = ["_post_session.py", "_post_session_v2.py", "run-specstory-session.sh",
             "queue-agent-commit.sh", "finalize-agent-commit.sh", "stage-agent-artifacts.sh",
             "find-session.sh", "scan-staged.sh", "agent-commit-metadata.sh",
             "../assets/redact_secrets.py", "../assets/artifact-dirs.txt",
             "../assets/gitleaks.toml.template", "../../git-workflow/scripts/check-commit-msg.sh"]
    entries = []
    for name in names:
        path = os.path.realpath(os.path.join(script_dir, name))
        if name.startswith("../../git-workflow/") and not os.path.lexists(path):
            entries.append([name, "optional_not_installed"])
            continue
        data, _identity = read_generation(path, 2 * MAX_SOURCE)
        entries.append([name, p.sha256_bytes(data)])
    return digest(entries)


def process_identity(pid):
    if type(pid) is not int or pid <= 1:
        fail("requires_wrapper", 5)
    proc = subprocess.run(["ps", "-p", str(pid), "-o", "pid=,ppid=,pgid=,lstart="],
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
                          env=dict(os.environ, LC_ALL="C"))
    fields = proc.stdout.decode("ascii", "strict").strip().split(None, 3)
    if proc.returncode or len(fields) != 4 or not all(x.isdigit() for x in fields[:3]):
        fail("requires_wrapper", 5)
    return {"pid": int(fields[0]), "ppid": int(fields[1]), "pgid": int(fields[2]),
            "started": fields[3]}


def authentic_context(journal):
    try:
        if journal["schema_version"] != 2 or journal["state"] not in ("running", "pending"):
            return False
        meta = journal["v2"]
        if os.environ.get("AGENT_HISTORY_RUN_ID") != journal["run_id"] or \
                os.environ.get("AGENT_HISTORY_REQUEST_PATH") != journal["request_path"]:
            return False
        wrapper = process_identity(meta["wrapper"]["pid"])
        if wrapper != meta["wrapper"] or type(meta["child_pid"]) is not int:
            return False
        current = process_identity(os.getpid())
        child_seen = False
        for _ in range(128):
            if current["pid"] == meta["child_pid"]:
                child_seen = current["pgid"] == meta["child_pid"]
            if current["pid"] == wrapper["pid"]:
                return child_seen
            if current["ppid"] <= 1:
                return False
            current = process_identity(current["ppid"])
    except (p.LifecycleError, OSError, ValueError):
        pass
    return False


def repository_identity(root, git_dir):
    identities = {}
    for name, path in (("root", root), ("git_dir", git_dir)):
        info = p.lstat_owned(path, "dir")
        if os.path.realpath(path) != path:
            fail("unsafe_repository")
        # Directory timestamps legitimately change as a session adds artifacts;
        # native inode/owner/mode identify the repository without confusing that
        # activity with a path replacement.
        identities[name] = [info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode)]
    return identities


def validate_repository_identity(value):
    if not isinstance(value, dict) or set(value) != {"root", "git_dir"}:
        fail("invalid_state", 4)
    if any(not isinstance(row, list) or len(row) != 4 or any(type(item) is not int for item in row) for row in value.values()):
        fail("invalid_state", 4)


def initial_metadata(script_dir, cloud_sync, specstory, automatic_commit=False, resume_session_id=None):
    require_platform()
    root, git_dir = p.discover_repository()
    return {"helper_revision": helper_revision(script_dir), "revision": 1,
            "automatic_commit": automatic_commit, "resume_session_id": resume_session_id,
            "repository_identity": repository_identity(root, git_dir),
            "cloud_sync": cloud_sync, "wrapper": process_identity(os.getpid()),
            "child_pid": None, "group_quiescent": None, "freshness": None,
            "receipt_revision": None, "publication_revision": None,
            "review_revision": None, "review_status": "not_required",
            "native_tool": native_tool(specstory)}


def validate_metadata(meta):
    if not isinstance(meta, dict) or set(meta) != JOURNAL_FIELDS:
        fail("invalid_state", 4)
    p.validate_sha256(meta["helper_revision"])
    validate_repository_identity(meta["repository_identity"])
    if meta["resume_session_id"] is not None:
        p.validate_uuid(meta["resume_session_id"])
    if type(meta["revision"]) is not int or meta["revision"] < 1 or \
            type(meta["cloud_sync"]) is not bool or type(meta["automatic_commit"]) is not bool or \
            meta["review_status"] not in REVIEW_STATES:
        fail("invalid_state", 4)
    if meta["group_quiescent"] is not None and type(meta["group_quiescent"]) is not bool:
        fail("invalid_state", 4)
    if meta["child_pid"] is not None and (type(meta["child_pid"]) is not int or meta["child_pid"] <= 1):
        fail("invalid_state", 4)
    for key in ("receipt_revision", "publication_revision", "review_revision"):
        p.validate_sha256(meta[key], allow_none=True)
    wrapper = meta["wrapper"]
    if not isinstance(wrapper, dict) or set(wrapper) != {"pid", "ppid", "pgid", "started"}:
        fail("invalid_state", 4)
    for key in ("pid", "ppid", "pgid"):
        if type(wrapper[key]) is not int or wrapper[key] < 1:
            fail("invalid_state", 4)
    p.safe_text(wrapper["started"])
    validate_identity(meta["native_tool"])
    fresh = meta["freshness"]
    if fresh is not None:
        if not isinstance(fresh, dict) or set(fresh) != {"source", "alias", "export_sha256", "queue_ack_sha256", "tool", "complete"}:
            fail("invalid_state", 4)
        for key in ("source", "alias", "tool"):
            validate_identity(fresh[key])
        p.validate_sha256(fresh["export_sha256"])
        p.validate_sha256(fresh["queue_ack_sha256"])
        if fresh["complete"] is not True or fresh["export_sha256"] != fresh["alias"]["sha256"]:
            fail("invalid_state", 4)


def validate_identity(value):
    if not isinstance(value, dict) or set(value) != {"path", "generation", "sha256"}:
        fail("invalid_evidence", 4)
    p.validate_absolute_path(value["path"])
    p.validate_sha256(value["sha256"])
    if not isinstance(value["generation"], list) or len(value["generation"]) != 7 or \
            any(type(x) is not int for x in value["generation"]):
        fail("invalid_evidence", 4)


def update(path, journal, **changes):
    meta = dict(journal["v2"])
    meta.update(changes)
    return p.update_journal(path, journal, v2=meta)


def bounded_command(command, root, limit, destination=None):
    """Bounded private capture; neither provider output nor errors reach logs."""
    process = subprocess.Popen(command, cwd=root, env=p.git_environment(),
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               start_new_session=True)
    data = bytearray()
    deadline = time.monotonic() + 90
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        while selector.get_map():
            if time.monotonic() > deadline:
                fail("freshness_unproven")
            for key, _mask in selector.select(0.2):
                chunk = os.read(key.fileobj.fileno(), min(65536, limit + 1 - len(data)))
                if not chunk:
                    selector.unregister(key.fileobj)
                    break
                data.extend(chunk)
                if len(data) > limit:
                    fail("freshness_unproven")
        if process.wait(timeout=max(0.1, deadline - time.monotonic())) != 0:
            fail("freshness_unproven")
        if destination is not None:
            immutable_write(destination, bytes(data))
        return bytes(data)
    finally:
        selector.close()
        process.stdout.close()
        if process.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, 9)
            process.wait()


def native_flags(tool, root, cloud_sync):
    sync_help = bounded_command([tool, "sync", "--help"], root, 128 * 1024)
    run_help = bounded_command([tool, "run", "--help"], root, 128 * 1024)
    required = [b"--print", b"--silent", b"--no-cloud-sync", b"--no-stats",
                b"--no-usage-analytics", b"--no-version-check"]
    if any(flag not in sync_help for flag in required) or \
            any(flag not in run_help for flag in required[2:]):
        fail("unsupported_native_export", 3)
    flags = ["--no-stats", "--no-usage-analytics", "--no-version-check"]
    if not cloud_sync:
        flags.append("--no-cloud-sync")
    return flags


def validate_forwarded(forwarded, cloud_sync):
    # Output/policy/config overrides would make run and exact export disagree.
    blocked = {"--config-dir", "--output-dir", "--only-cloud-sync", "--only-stats",
               "--no-redact-secrets", "--local-time-zone", "--print", "--telemetry-endpoint"}
    for item in forwarded:
        flag = item.split("=", 1)[0]
        if item == "--" or flag in blocked or (flag == "--no-cloud-sync" and "=" in item):
            fail("unsupported_run_option", 2)
    if cloud_sync and "--no-cloud-sync" in forwarded:
        fail("conflicting_cloud_choice", 2)
    resume = []
    for index, item in enumerate(forwarded):
        if item == "--resume":
            if index + 1 >= len(forwarded):
                fail("unsupported_run_option", 2)
            resume.append(forwarded[index + 1])
        elif item.startswith("--resume="):
            resume.append(item.split("=", 1)[1])
    if len(resume) > 1:
        fail("unsupported_run_option", 2)
    if resume:
        p.validate_uuid(resume[0])
    return resume[0] if resume else None


def queue_ack(request, journal):
    return {"kind": "agent_history_queue_ack", "schema_version": 2,
            "request_id": request["request_id"], "request_path": request["request_path"],
            "request_revision": digest(request), "helper_revision": journal["v2"]["helper_revision"]}


def prove_native_ack(data, request, journal):
    """Require the current queue acknowledgement in a real typed tool result.

    User prose, arbitrary UUID substrings, filesystem mtimes and writer-supplied
    marker files are not session/run evidence. The whole native generation and
    native rendered digest are independently bound by the caller as well.
    """
    expected = queue_ack(request, journal)
    uses = set()
    decoder = json.JSONDecoder(object_pairs_hook=p._unique_object, parse_constant=p._reject_constant)
    for line in data.splitlines():
        try:
            row = json.loads(line.decode("utf-8"), object_pairs_hook=p._unique_object, parse_constant=p._reject_constant)
        except (ValueError, UnicodeError):
            fail("source_session_unproven")
        if not isinstance(row, dict) or row.get("sessionId") != request["session_id"]:
            continue
        message = row.get("message", {})
        if not isinstance(message, dict) or not isinstance(message.get("content"), list):
            continue
        for block in message["content"]:
            if not isinstance(block, dict):
                continue
            if row.get("type") == "assistant" and block.get("type") == "tool_use" and isinstance(block.get("id"), str):
                uses.add(block["id"])
            if row.get("type") != "user" or block.get("type") != "tool_result" or block.get("tool_use_id") not in uses:
                continue
            # A native binding write can fail AFTER the helper really queued.
            # Its exact queued_but_not_bound acknowledgement remains source
            # evidence even when the enclosing Bash result has is_error=true;
            # it grants neither binding success nor commit authorization.
            content = block.get("content")
            texts = [content] if isinstance(content, str) else [part.get("text") for part in content if isinstance(part, dict) and part.get("type") == "text"] if isinstance(content, list) else []
            for text in texts:
                if not isinstance(text, str):
                    continue
                lines = text.splitlines(keepends=True)
                for index, candidate in enumerate(lines):
                    if not candidate.lstrip().startswith("{"):
                        continue
                    try:
                        value, _end = decoder.raw_decode("".join(lines[index:]).lstrip())
                    except ValueError:
                        continue
                    if not isinstance(value, dict):
                        continue
                    acknowledgement = value if value.get("kind") == "agent_history_queue_ack" else value.get("queue_ack")
                    if acknowledgement == expected:
                        return digest(expected)
    fail("source_session_unproven")


def capture_source(script_dir, root, request):
    fields = p.run_find_session(script_dir, root, request)
    path = fields.get("claude_jsonl_path") or fields.get("claude_path")
    if not path:
        fail("freshness_unproven")
    p.reject_active_writer(path)
    data, identity = read_generation(path)
    _id, _dir, _journal_path, journal, current_request, _revision = p.parse_request_for_finalize(request["request_path"], root, request["git_dir"])
    if current_request != request:
        fail("source_session_unproven")
    prove_native_ack(data, request, journal)
    return identity


def prove_export(script_dir, root, request, journal, source_before, flags):
    tool = journal["v2"]["native_tool"]
    if native_tool(tool["path"]) != tool:
        fail("tool_changed")
    if capture_source(script_dir, root, request) != source_before:
        fail("freshness_unproven")
    candidate_path = os.path.join(os.path.dirname(request["request_path"]), "exact-export.md")
    # Print is a read-only export even when the explicitly selected run permits
    # cloud sync. It never repeats the publication-bearing normal sync.
    export_flags = list(flags)
    if "--no-cloud-sync" not in export_flags:
        export_flags.append("--no-cloud-sync")
    candidate = bounded_command([tool["path"], "sync", request["provider"], "-s",
                                 request["session_id"], "--print", "--silent"] + export_flags,
                                root, MAX_SOURCE, candidate_path)
    p.run_find_session(script_dir, root, request)
    alias_data, alias = read_generation(os.path.join(root, request["specstory_path"]))
    if candidate != alias_data or not candidate.startswith(b"<!-- Generated by SpecStory, Markdown "):
        fail("freshness_unproven")
    source_after = capture_source(script_dir, root, request)
    if source_after != source_before or native_tool(tool["path"]) != tool:
        fail("freshness_unproven")
    return {"source": source_before, "alias": alias, "export_sha256": p.sha256_bytes(candidate),
            "queue_ack_sha256": digest(queue_ack(request, journal)), "tool": tool, "complete": True}


def reprove_freshness(script_dir, root, request, journal, prepared=False):
    meta = journal["v2"]
    fresh = meta["freshness"]
    if not fresh or meta["group_quiescent"] is not True or \
            meta["helper_revision"] != helper_revision(script_dir) or \
            fresh["queue_ack_sha256"] != digest(queue_ack(request, journal)):
        fail("freshness_unproven")
    if capture_source(script_dir, root, request) != fresh["source"] or \
            native_tool(fresh["tool"]["path"]) != fresh["tool"]:
        fail("freshness_unproven")
    if not prepared:
        _data, alias = read_generation(os.path.join(root, request["specstory_path"]))
        if alias != fresh["alias"]:
            fail("freshness_unproven")


def immutable_write(path, data):
    """No-replace, fsynced private publication. Existing receipts never change."""
    parent = os.path.dirname(path)
    p.assert_private_dir(parent)
    if os.path.lexists(path):
        fail("evidence_already_exists")
    temporary = os.path.join(parent, ".evidence-" + os.urandom(12).hex())
    try:
        p.atomic_write(temporary, data)
        os.link(temporary, path, follow_symlinks=False)
        p.fsync_directory(parent)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def load_private(path, maximum=MAX_RECEIPT):
    raw, identity = read_generation(path, maximum, private=True)
    # The ordinary protocol remains bounded to 128 KiB. Evidence has its own
    # explicit larger bound because it contains full PRIVATE beforeimages.
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=p._unique_object,
                           parse_constant=p._reject_constant)
    except (ValueError, UnicodeError):
        fail("invalid_evidence", 4)
    if not isinstance(value, dict) or p.canonical_json_bytes(value) != raw:
        fail("invalid_evidence", 4)
    return value, identity["sha256"]


def encode(data):
    return base64.b64encode(data).decode("ascii")


def decode(value):
    try:
        if not isinstance(value, str):
            fail("invalid_evidence")
        return base64.b64decode(value, validate=True)
    except ValueError:
        fail("invalid_evidence")


def occurrence_offsets(data, value):
    if not value:
        fail("incomplete_finding")
    result = []
    start = 0
    while True:
        offset = data.find(value, start)
        if offset < 0:
            return result
        result.append([offset, offset + len(value)])
        if len(result) > MAX_FINDINGS:
            fail("incomplete_finding")
        start = offset + 1


def scanner_proof(config, request_path=None):
    import tomllib
    data, policy = read_generation(str(config), p.MAX_GITLEAKS_CONFIG_BYTES, private=True)
    if request_path is not None:
        root, git_dir = p.discover_repository()
        _id, _dir, _path, journal, request, _revision = p.parse_request_for_finalize(request_path, root, git_dir)
        expected = p.sha256_bytes(p.request_gitleaks_config(os.path.dirname(__file__), root, request))
        if journal["schema_version"] != 2 or policy["path"] != journal["gitleaks_config_path"] or \
                policy["sha256"] != journal["gitleaks_config_sha256"] or policy["sha256"] != expected:
            fail("trusted_policy_changed")
    try:
        parsed = tomllib.loads(data.decode("utf-8"))
    except (ValueError, UnicodeError):
        fail("unsupported_scanner_policy")
    extension = parsed.get("extend", {})
    if not isinstance(extension, dict) or "path" in extension:
        # A private copied config must not silently consult a mutable external
        # policy closure. Native bundled defaults are bound by executable hash.
        fail("unsupported_scanner_policy")
    return {"policy": policy, "scanner": native_tool(shutil.which("gitleaks")),
            "git": native_tool(shutil.which("git")), "python": native_tool(sys.executable),
            "bash": native_tool(p.bash_executable())}


def prepare_receipt(request_path, redactor, audit, selected, traces, coverage, proof):
    root, git_dir = p.discover_repository()
    _id, run_dir, journal_path, journal, request, revision = p.parse_request_for_finalize(request_path, root, git_dir)
    if journal["schema_version"] != 2 or journal["state"] != "synced" or not p.request_lifecycle_is_proven(journal, request):
        fail("invalid_evidence")
    if not os.path.isfile(os.path.join(run_dir, "finalize.lock")):
        fail("lifecycle_unproven")
    reprove_freshness(os.path.dirname(__file__), root, request, journal)
    exact = [request["specstory_path"]] + ([request["plan_path"]] if request["plan_policy"] == "path" else [])
    if list(selected) != exact:
        fail("selector_mismatch")
    records = []
    entries = {entry.path: entry for entry in audit.entries}
    count = 0
    for path in selected:
        before = entries[path]
        after = redactor._load_index_blob(path)
        source, identity = read_generation(os.path.join(root, path))
        clean = redactor._run_git(["hash-object", "--path=" + path, "--stdin"], source).strip().decode("ascii")
        if clean != before.oid or before.mode != after.mode:
            fail("source_changed")
        if source != before.data:
            # V1 retains general clean/smudge compatibility. V2 cannot claim a
            # complete replay of arbitrary external filter transformations.
            fail("unsupported_source_filter")
        findings = []
        for finding in audit.findings_by_path.get(path, []):
            raw = redactor._encode_content(finding.get("Secret", ""))
            offsets = occurrence_offsets(before.data, raw)
            if not offsets or raw in after.data:
                fail("incomplete_finding")
            findings.append({"kind": "scanner", "rule": redactor._safe_rule_id(finding.get("RuleID")),
                             "value": encode(raw), "occurrences": offsets})
        analysis = redactor.analyze_private_key_content(redactor._decode_content(before.data))
        text = redactor._decode_content(before.data)
        if analysis.incomplete_headers:
            fail("incomplete_finding")
        for item in analysis.complete_ranges + analysis.isolated_header_ranges:
            raw = redactor._encode_content(text[item.start:item.end])
            start = len(redactor._encode_content(text[:item.start]))
            findings.append({"kind": "structure", "rule": "private-key-structure", "value": encode(raw),
                             "occurrences": [[start, start + len(raw)]]})
        # IDs are opaque run-bound values, never hashes of a bare credential.
        for finding in findings:
            count += 1
            if count > MAX_FINDINGS:
                fail("incomplete_finding")
            finding["id"] = digest([revision, path, before.oid, count, finding])
        records.append({"path": path, "mode": before.mode, "pre_oid": before.oid,
                        "post_oid": after.oid, "before_blob": encode(before.data),
                        "after_sha256": p.sha256_bytes(after.data), "source_before": encode(source),
                        "source_identity": identity, "steps": traces[path], "findings": findings})
    prepared_tree = redactor._run_git(["write-tree"]).strip().decode("ascii")
    if proof != scanner_proof(journal["gitleaks_config_path"], request_path):
        fail("scanner_identity_changed")
    receipt = {"schema_version": 2, "request_revision": revision, "request": request,
               "journal_revision": digest(journal), "prepared_tree": prepared_tree,
               "helper_revision": helper_revision(os.path.dirname(__file__)),
               "policy": proof["policy"], "scanner": proof["scanner"], "git": proof["git"],
               "python": proof["python"], "bash": proof["bash"],
               "freshness": journal["v2"]["freshness"], "artifacts": records,
               "coverage": coverage, "complete": True}
    validate_receipt(receipt, redactor)
    raw = p.canonical_json_bytes(receipt)
    if len(raw) > MAX_RECEIPT:
        fail("incomplete_evidence")
    immutable_write(os.path.join(run_dir, "sanitation.json"), raw)
    # This durable journal transition is also before source/index publication.
    # If it fails, the receipt remains diagnostic evidence, never retry authority.
    update(journal_path, journal, receipt_revision=p.sha256_bytes(raw),
           review_status="unresolved" if count else "not_required")


def validate_coverage(receipt, redactor):
    p.validate_oid(receipt["prepared_tree"])
    coverage = receipt["coverage"]
    if not isinstance(coverage, list) or not 1 <= len(coverage) <= 4096:
        fail("incomplete_coverage")
    listing = redactor._run_git(["ls-tree", "-r", "-z", receipt["prepared_tree"]])
    records = [item for item in listing.split(b"\0") if item]
    expected = {}
    for item in records:
        metadata, raw_path = item.split(b"\t", 1)
        mode, kind, oid = metadata.decode("ascii").split(" ")
        path = redactor._decode_git_path(raw_path)
        expected[path] = (mode, kind, oid)
    if len(expected) != len(coverage):
        fail("incomplete_coverage")
    seen = set()
    total = 0
    for row in coverage:
        if not isinstance(row, dict) or row.get("path") in seen:
            fail("incomplete_coverage")
        path = row["path"]
        seen.add(path)
        mode, kind, oid = expected.get(path, (None, None, None))
        if row.get("mode") != mode or row.get("oid") != oid:
            fail("incomplete_coverage")
        if mode == "160000" and kind == "commit":
            if set(row) != {"path", "mode", "oid", "status"} or row["status"] != "nonblob_gitlink":
                fail("incomplete_coverage")
            continue
        if set(row) != {"path", "mode", "oid", "size", "sha256", "status"} or \
                row["status"] != "scanned_full_blob" or kind != "blob" or mode not in ("100644", "100755", "120000"):
            fail("incomplete_coverage")
        size = row["size"]
        if type(size) is not int or not 0 <= size <= MAX_SOURCE:
            fail("incomplete_coverage")
        total += size
        if total > 128 * 1024 * 1024 or redactor._run_git(["cat-file", "-s", oid]).strip() != str(size).encode("ascii"):
            fail("incomplete_coverage")
        data = redactor._run_git(["cat-file", "blob", oid])
        if b"\0" in data or p.sha256_bytes(data) != row["sha256"]:
            fail("incomplete_coverage")


def origin_slice(segments, start, end):
    result = []
    cursor = 0
    for length, origin in segments:
        low, high = max(cursor, start), min(cursor + length, end)
        if low < high:
            result.append((high - low, None if origin is None else origin + low - cursor))
        cursor += length
    return result


def merged_ranges(ranges):
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def validate_receipt(receipt, redactor):
    required = {"schema_version", "request_revision", "request", "journal_revision", "prepared_tree",
                "helper_revision", "policy", "scanner", "git", "python", "bash", "freshness", "artifacts", "coverage", "complete"}
    if set(receipt) != required or receipt["schema_version"] != 2 or receipt["complete"] is not True:
        fail("incomplete_evidence")
    p.validate_request(receipt["request"])
    if digest(receipt["request"]) != receipt["request_revision"] or not receipt["coverage"]:
        fail("incomplete_evidence")
    validate_coverage(receipt, redactor)
    coverage_by_path = {row["path"]: row for row in receipt["coverage"]}
    request = receipt["request"]
    exact = [request["specstory_path"]] + ([request["plan_path"]] if request["plan_policy"] == "path" else [])
    if [record.get("path") for record in receipt["artifacts"]] != exact:
        fail("incomplete_evidence")
    if receipt["artifacts"][0]["source_identity"] != receipt["freshness"]["alias"]:
        fail("stale_receipt")
    ids = set()
    for record in receipt["artifacts"]:
        if set(record) != {"path", "mode", "pre_oid", "post_oid", "before_blob", "after_sha256",
                           "source_before", "source_identity", "steps", "findings"}:
            fail("incomplete_evidence")
        covered_blob = coverage_by_path.get(record["path"], {})
        if covered_blob.get("oid") != record["post_oid"] or covered_blob.get("mode") != record["mode"] or \
                record["source_identity"]["path"] != os.path.join(request["worktree_root"], record["path"]):
            fail("incomplete_evidence")
        before = decode(record["before_blob"])
        data = before
        if len(before) > MAX_SOURCE or not isinstance(record["steps"], list) or len(record["steps"]) > MAX_FINDINGS:
            fail("incomplete_evidence")
        origins = [(len(before), 0)]
        removed = []
        for step in record["steps"]:
            if set(step) != {"start", "end", "before", "after"}:
                fail("incomplete_evidence")
            start, end = step["start"], step["end"]
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(data):
                fail("incomplete_evidence")
            if data[start:end] != decode(step["before"]):
                fail("incomplete_evidence")
            replacement = decode(step["after"])
            removed.extend((origin, origin + length) for length, origin in origin_slice(origins, start, end) if origin is not None)
            origins = origin_slice(origins, 0, start) + [(len(replacement), None)] + origin_slice(origins, end, len(data))
            data = data[:start] + replacement + data[end:]
            if len(data) > MAX_SOURCE:
                fail("incomplete_evidence")
        if p.sha256_bytes(data) != record["after_sha256"]:
            fail("incomplete_evidence")
        removed = merged_ranges(removed)
        covered = []
        for finding in record["findings"]:
            if set(finding) != {"id", "kind", "rule", "value", "occurrences"} or finding["id"] in ids:
                fail("incomplete_evidence")
            ids.add(finding["id"])
            p.validate_sha256(finding["id"])
            value = decode(finding["value"])
            if not value or value in data or not finding["occurrences"]:
                fail("incomplete_evidence")
            expected = occurrence_offsets(before, value)
            if finding["kind"] == "scanner" and finding["occurrences"] != expected:
                fail("incomplete_evidence")
            for start, end in finding["occurrences"]:
                if [start, end] not in expected or not any(low <= start < end <= high for low, high in removed):
                    fail("incomplete_evidence")
                covered.append([start, end])
        if removed != merged_ranges(covered):
            fail("incomplete_evidence")
        for key, raw in (("pre_oid", before), ("post_oid", data)):
            actual = redactor._run_git(["hash-object", "--stdin", "--no-filters"], raw).strip().decode("ascii")
            if actual != record[key]:
                fail("incomplete_evidence")
        source = decode(record["source_before"])
        validate_identity(record["source_identity"])
        if p.sha256_bytes(source) != record["source_identity"]["sha256"]:
            fail("incomplete_evidence")
    return ids


def redactor_module(root, config, index_file=None):
    import importlib.util
    path = os.path.realpath(os.path.join(os.path.dirname(__file__), "../assets/redact_secrets.py"))
    spec = importlib.util.spec_from_file_location("_receipt_redactor", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module._prepare_repository(config)
    if index_file:
        module.GIT_ENVIRONMENT["GIT_INDEX_FILE"] = index_file
    return module


def record_publication(root, request, journal_path, journal):
    run_dir = os.path.dirname(request["request_path"])
    receipt, revision = load_private(os.path.join(run_dir, "sanitation.json"))
    if revision != journal["v2"]["receipt_revision"] or p.index_tree(root) != receipt["prepared_tree"]:
        fail("publication_unproven")
    sources = []
    for record in receipt["artifacts"]:
        data, identity = read_generation(os.path.join(root, record["path"]))
        if p.sha256_bytes(data) != record["after_sha256"]:
            fail("publication_unproven")
        sources.append(identity)
    publication = {"schema_version": 2, "receipt_revision": revision,
                   "prepared_tree": receipt["prepared_tree"], "sources": sources}
    raw = p.canonical_json_bytes(publication)
    immutable_write(os.path.join(run_dir, "publication.json"), raw)
    return update(journal_path, journal, publication_revision=p.sha256_bytes(raw))


def reprove_receipt(script_dir, root, request, journal, *, committed=False):
    run_dir = os.path.dirname(request["request_path"])
    receipt, revision = load_private(os.path.join(run_dir, "sanitation.json"))
    publication, publication_revision = load_private(os.path.join(run_dir, "publication.json"))
    if revision != journal["v2"]["receipt_revision"] or \
            publication_revision != journal["v2"]["publication_revision"] or \
            receipt["request"] != request or receipt["helper_revision"] != helper_revision(script_dir) or \
            receipt["prepared_tree"] != journal["expected_commit_tree"] or \
            journal["expected_commit_parent"] != request["head_oid"] or \
            receipt["freshness"] != journal["v2"]["freshness"] or \
            receipt["freshness"]["tool"] != journal["v2"]["native_tool"] or \
            publication.get("receipt_revision") != revision or \
            publication.get("prepared_tree") != receipt["prepared_tree"]:
        fail("stale_receipt")
    proof = scanner_proof(journal["gitleaks_config_path"], request["request_path"])
    if any(proof[key] != receipt[key] for key in proof) or receipt["policy"]["sha256"] != journal["gitleaks_config_sha256"]:
        fail("stale_receipt")
    if set(publication) != {"schema_version", "receipt_revision", "prepared_tree", "sources"} or publication["schema_version"] != 2 or \
            not isinstance(publication["sources"], list) or len(publication["sources"]) != len(receipt["artifacts"]):
        fail("incomplete_publication")
    for record, identity in zip(receipt["artifacts"], publication["sources"]):
        validate_identity(identity)
        if identity["path"] != os.path.join(root, record["path"]) or identity["sha256"] != record["after_sha256"]:
            fail("incomplete_publication")
    redactor = redactor_module(root, journal["gitleaks_config_path"])
    ids = validate_receipt(receipt, redactor)
    saved_revision = journal["v2"]["review_revision"]
    if saved_revision:
        saved_path = os.path.join(run_dir, "review-" + saved_revision + ".json")
        _value, actual_revision, actual_status = review_decisions(saved_path, receipt, revision, journal)
        if actual_revision != saved_revision or actual_status != journal["v2"]["review_status"] or actual_status == "unresolved":
            fail("stale_review")
    elif committed and ids:
        fail("incomplete_review")
    if not committed:
        reprove_freshness(script_dir, root, request, journal, prepared=True)
        for identity in publication["sources"]:
            if read_generation(identity["path"])[1] != identity:
                fail("stale_receipt")
        # Unlike Apply's write-tree, these are read-only comparisons: preview
        # must not refresh an index cache or manufacture tree objects.
        p.reject_git_operation(root)
        if p.current_head_state(root) != (request["head_ref"], request["head_oid"]):
            fail("stale_prepared_state")
        difference = p.git_process(root, ["diff", "--cached", "--quiet", "--no-ext-diff", "--no-textconv", receipt["prepared_tree"], "--"])
        if difference.returncode:
            fail("stale_prepared_state")
    return receipt, revision


def review_decisions(path, receipt, revision, journal):
    if os.path.realpath(path) != path:
        fail("unsafe_review")
    p.assert_private_dir(os.path.dirname(path))
    value, review_revision = load_private(path, p.MAX_JSON_BYTES)
    if set(value) != {"schema_version", "request_revision", "receipt_revision", "prepared_tree", "decisions"} or \
            value["schema_version"] != 2 or value["request_revision"] != receipt["request_revision"] or \
            value["receipt_revision"] != revision or value["prepared_tree"] != receipt["prepared_tree"]:
        fail("stale_review")
    ids = {f["id"] for record in receipt["artifacts"] for f in record["findings"]}
    if not isinstance(value["decisions"], list) or len(value["decisions"]) != len(ids):
        fail("incomplete_review")
    seen = set()
    states = set()
    for decision in value["decisions"]:
        if not isinstance(decision, dict) or set(decision) != {"finding_id", "disposition", "reason"}:
            fail("incomplete_review")
        finding = decision["finding_id"]
        if finding not in ids or finding in seen or decision["disposition"] not in DISPOSITIONS:
            fail("incomplete_review")
        p.safe_text(decision["reason"])
        if not decision["reason"].strip() or len(decision["reason"]) > 2048:
            fail("incomplete_review")
        seen.add(finding)
        states.add(decision["disposition"])
    status = "unresolved" if "unresolved" in states else (
        "credential_rotation_required" if "credential_rotation_required" in states else "reviewed_noncredential")
    return value, review_revision, status


def apply_review(arguments, root, request, journal_path, journal):
    receipt, revision = reprove_receipt(arguments.script_dir, root, request, journal)
    run_dir = os.path.dirname(request["request_path"])
    if arguments.review_file:
        if not arguments.allow_commit or journal["state"] != "rotation_required":
            fail("invalid_authorization", 5)
        value, review_revision, status = review_decisions(arguments.review_file, receipt, revision, journal)
        if status == "unresolved":
            fail("unresolved_review", 10)
        if status == "credential_rotation_required" and not arguments.rotation_confirmed:
            fail("rotation_confirmation_required", 10)
        if status == "reviewed_noncredential" and arguments.rotation_confirmed:
            fail("invalid_authorization", 5)
        # An approved exact receipt is retained immutably; this is NOT a future
        # policy allowlist and contains no claim that sanitation rotated a key.
        retained = os.path.join(run_dir, "review-" + review_revision + ".json")
        if os.path.lexists(retained):
            existing, existing_revision = load_private(retained, p.MAX_JSON_BYTES)
            if existing != value or existing_revision != review_revision:
                fail("stale_review")
        else:
            immutable_write(retained, p.canonical_json_bytes(value))
        journal = update(journal_path, journal, review_revision=review_revision, review_status=status)
    elif journal["v2"]["review_revision"]:
        path = os.path.join(run_dir, "review-" + journal["v2"]["review_revision"] + ".json")
        _value, review_revision, status = review_decisions(path, receipt, revision, journal)
        if review_revision != journal["v2"]["review_revision"] or status != journal["v2"]["review_status"]:
            fail("stale_review")
    return journal


def preview(arguments, root, git_dir, journal, request):
    if journal["schema_version"] != 2:
        fail("legacy_review_unsupported", 4)
    receipt, revision = reprove_receipt(arguments.script_dir, root, request, journal,
                                        committed=journal["state"] in ("done", "committing"))
    findings = []
    for record in receipt["artifacts"]:
        for item in record["findings"]:
            # Do not echo arbitrary scanner rule IDs: a hostile rule can itself
            # contain a full raw value. Kind + opaque IDs are sufficient review metadata.
            findings.append({"finding_id": item["id"], "path": record["path"],
                             "kind": item["kind"], "masked_value": "[REDACTED]",
                             "occurrence_count": len(item["occurrences"]), "complete": True})
    _id, _dir, _path, current_journal, current_request, _digest = p.parse_request_for_finalize(request["request_path"], root, git_dir)
    if current_journal != journal or current_request != request:
        fail("stale_inspection", 6)
    p.emit({"schema_version": 2, "protocol_version": 2, "status": "review_preview",
            "request_revision": receipt["request_revision"], "receipt_revision": revision,
            "prepared_tree": receipt["prepared_tree"], "review_status": journal["v2"]["review_status"],
            "reviewable": journal["state"] == "rotation_required", "findings": findings,
            "private_receipt_path": os.path.join(os.path.dirname(request["request_path"]), "sanitation.json"),
            "private_review_path": os.path.join(os.path.dirname(request["request_path"]), "review.json"),
            "commit_attempted": False})
    return 0


def require_platform():
    if os.name != "posix" or sys.version_info < (3, 11):
        fail("unsupported_v2_platform", 3)


def capabilities(arguments):
    require_platform()
    p.emit({"schema_version": 2, "protocol_version": 2, "status": "supported",
            "helper_revision": helper_revision(arguments.script_dir), "capabilities": CAPABILITIES})
    return 0


def inspect(arguments):
    root, git_dir = p.discover_repository()
    request_path = arguments.request or os.environ.get("AGENT_HISTORY_REQUEST_PATH", "")
    if not request_path:
        fail("requires_wrapper", 5)
    run_dir, journal_path, journal = p.validate_run_layout(request_path, root, git_dir)
    request = None
    request_revision = None
    if os.path.lexists(request_path):
        _id, _dir, _path, journal, request, request_revision = p.parse_request_for_finalize(request_path, root, git_dir)
    authentic = authentic_context(journal)
    if arguments.context and not authentic:
        fail("requires_wrapper", 5)
    protocol = journal["schema_version"]
    fresh, review, proven = "unproven", "not_required", False
    if protocol == 2:
        review = journal["v2"]["review_status"]
        if journal["v2"]["freshness"]:
            try:
                reprove_freshness(arguments.script_dir, root, request, journal,
                                  prepared=journal["staging_ready"])
                fresh = "proven"
            except (p.LifecycleError, OSError):
                fresh = "stale"
        if journal["staging_ready"]:
            try:
                reprove_receipt(arguments.script_dir, root, request, journal, committed=journal["state"] == "done")
                if journal["state"] == "done":
                    proven = p.commit_matches_request(root, journal["commit_oid"], request, journal, require_trailer=True)
            except (p.LifecycleError, OSError):
                review = "stale"
    normalized_message_sha256 = None
    if journal["composed_message_sha256"]:
        message, _identity = read_generation(journal["composed_message_path"], p.MAX_MESSAGE_BYTES, private=True)
        if p.sha256_bytes(message) != journal["composed_message_sha256"]:
            fail("stale_message")
        normalized_message_sha256 = p.sha256_bytes(p.normalize_commit_message(root, message))
    result = {"schema_version": 2, "protocol_version": protocol,
              "helper_revision": helper_revision(arguments.script_dir),
              "capabilities": CAPABILITIES if protocol == 2 else [],
              "status": "inspected" if protocol == 2 else "legacy_v1", "run_id": journal["run_id"],
              "request_path": request_path, "request_id": request["request_id"] if request else None,
              "request_revision": request_revision, "journal_revision": digest(journal),
              "state": journal["state"], "worktree_root": root, "git_dir": git_dir,
              "head_ref": request["head_ref"] if request else None, "head_oid": request["head_oid"] if request else None,
              "input_index_tree": request["index_tree"] if request else None,
              "message_sha256": request["base_message_sha256"] if request else None,
              "composed_message_sha256": journal["composed_message_sha256"],
              "normalized_message_sha256": normalized_message_sha256,
              "prepared_tree": journal["expected_commit_tree"], "commit_oid": journal["commit_oid"],
              "child_exit_code": journal["child_exit_code"], "child_signal": journal["child_signal"],
              "group_quiescent": journal["v2"]["group_quiescent"] if protocol == 2 else None,
              "automatic_commit": journal["v2"]["automatic_commit"] if protocol == 2 else None,
              "cloud_sync": journal["v2"]["cloud_sync"] if protocol == 2 else None,
              "sync_status": "proven" if journal["sync_succeeded"] else (
                  "failed" if journal["state"] == "failed" and journal["sync_attempted"] else (
                  "pending" if journal["sync_attempted"] else "not_started")),
              "freshness_status": fresh, "review_status": review, "authentic_context": authentic,
              "receipt_revision": journal["v2"]["receipt_revision"] if protocol == 2 else None,
              "commit_proven": proven}
    for key in ("provider", "session_id", "specstory_path", "plan_policy", "plan_path"):
        result[key] = request[key] if request else None
    # No cache/index refresh, lock, scanner, draft cleanup, or journal mutation.
    if digest(p.read_canonical_json(journal_path, "journal", p.validate_journal)[0]) != result["journal_revision"]:
        fail("stale_inspection", 6)
    p.emit(result)
    return 0
