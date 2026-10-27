"""Regression tests for bootstrap-project.sh configuration hygiene and migration."""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Mapping

import pytest


SKILL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SKILL_DIR.parents[2]
BOOTSTRAP = SKILL_DIR / "scripts" / "bootstrap-project.sh"
TEMPLATE = SKILL_DIR / "assets" / "pre-commit-config.yaml.template"
MANIFEST = REPO_ROOT / ".pre-commit-hooks.yaml"
HOOK_REPO = "https://github.com/daviddwlee84/agent-skills"
OLD_REV = "ahh-v1.1.0"
NEW_REV = "ahh-v2.0.1"
OLD_HOOK = "redact-agent-secrets"
NEW_HOOK = "check-agent-artifact-secrets"
ARCHIVE_EXCLUDE = (
    r"^(?:\.agents|\.claude|\.codex|\.cursor|\.opencode|\.specify|\.specstory)"
    r"(?:/|$)"
)


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=check,
        capture_output=True,
        text=True,
    )


def init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "agent-history-test@example.invalid")
    git(repo, "config", "user.name", "Agent History Test")
    # Avoid installing real hooks in the throwaway repo. bootstrap treats an
    # existing custom hooksPath as an intentional global-wrapper setup.
    git(repo, "config", "core.hooksPath", ".test-hooks")
    return repo


def run_bootstrap(
    repo: Path,
    tmp_path: Path,
    *args: str,
    check: bool = True,
    env_overrides: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        ["/bin/bash", str(BOOTSTRAP), *args],
        cwd=repo,
        env=env,
        check=check,
        capture_output=True,
        text=True,
    )


def fake_pre_commit(tmp_path: Path) -> tuple[Path, Path]:
    """Install a deterministic validate-config stand-in and return env paths."""
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir(exist_ok=True)
    log_path = tmp_path / "pre-commit.log"
    executable = bin_dir / "pre-commit"
    executable.write_text(
        """#!/bin/sh
set -eu
if [ "${1:-}" != "validate-config" ]; then
  exit 0
fi
printf '%s\n' "$*" >> "$FAKE_PRE_COMMIT_LOG"
if [ "${FAKE_REQUIRE_SCRIPT:-0}" = "1" ] && [ ! -f scripts/redact_secrets.py ]; then
  exit 91
fi
if [ -n "${FAKE_EXPECT_CONFIG_TEXT:-}" ] && ! grep -Fq "$FAKE_EXPECT_CONFIG_TEXT" .pre-commit-config.yaml; then
  exit 92
fi
if [ -n "${FAKE_EXPECT_CONFIG_MODE:-}" ]; then
  python3 -c 'import os, stat, sys; sys.exit(0 if stat.S_IMODE(os.stat(sys.argv[1]).st_mode) == int(sys.argv[2], 8) else 1)' "$2" "$FAKE_EXPECT_CONFIG_MODE" || exit 93
fi
if [ -n "${FAKE_CHMOD_CONFIG_MODE:-}" ]; then
  chmod "$FAKE_CHMOD_CONFIG_MODE" "$2"
fi
exit "${FAKE_VALIDATE_EXIT:-0}"
"""
    )
    executable.chmod(0o755)
    return bin_dir, log_path


def migration_env(
    tmp_path: Path,
    *,
    validate_exit: int = 0,
    require_script: bool = False,
    expected_config_text: str = "",
) -> dict[str, str]:
    bin_dir, log_path = fake_pre_commit(tmp_path)
    return {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FAKE_PRE_COMMIT_LOG": str(log_path),
        "FAKE_VALIDATE_EXIT": str(validate_exit),
        "FAKE_REQUIRE_SCRIPT": "1" if require_script else "0",
        "FAKE_EXPECT_CONFIG_TEXT": expected_config_text,
    }


def write_legacy_script(repo: Path, content: str = "#!/usr/bin/env python3\nprint('legacy')\n") -> Path:
    script = repo / "scripts" / "redact_secrets.py"
    script.parent.mkdir(exist_ok=True)
    script.write_text(content)
    script.chmod(0o755)
    return script


def commit_paths(repo: Path, *paths: str) -> None:
    git(repo, "add", *paths)
    git(repo, "commit", "-qm", "legacy bootstrap")


def old_local_config(*, sibling: bool = False, safe_options: str = "") -> str:
    sibling_hook = ""
    if sibling:
        sibling_hook = """      # sibling comment must survive
      - id: keep-local-sibling
        name: Keep local sibling
        entry: /usr/bin/true
        language: system
"""
    return f"""# preserve top comment
repos:
  - repo: local
    hooks:
      - id: {OLD_HOOK}
        name: Auto-redact secrets in agent artifacts
        entry: ./scripts/redact_secrets.py --fix
        language: system
        pass_filenames: false
{safe_options}{sibling_hook}"""


def test_fresh_bootstrap_installs_validation_only_template(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)

    run_bootstrap(repo, tmp_path)

    config = (repo / ".pre-commit-config.yaml").read_text()
    assert config == TEMPLATE.read_text()
    assert f"rev: {NEW_REV}" in config
    assert f"- id: {NEW_HOOK}" in config
    assert f"- id: {OLD_HOOK}" not in config
    assert "--fix" not in config
    assert "post-session finalizer" in config
    assert config.count(ARCHIVE_EXCLUDE) == 2
    assert "- repo: https://github.com/gitleaks/gitleaks\n    rev: v8.30.1" in config
    assert "- id: gitleaks-system\n        pass_filenames: false" in config
    assert "- id: detect-private-key" in config


def test_ordinary_bootstrap_does_not_overwrite_unrelated_existing_configs(
    tmp_path: Path,
) -> None:
    repo = init_repo(tmp_path)
    pre_commit = repo / ".pre-commit-config.yaml"
    gitleaks = repo / ".gitleaks.toml"
    pre_commit.write_bytes(b"# user pre-commit config\nrepos: []\n")
    gitleaks.write_bytes(b"# user gitleaks config\n[allowlist]\npaths = []\n")

    run_bootstrap(repo, tmp_path)

    assert pre_commit.read_bytes() == b"# user pre-commit config\nrepos: []\n"
    assert gitleaks.read_bytes() == b"# user gitleaks config\n[allowlist]\npaths = []\n"


def test_published_hooks_are_unconditional_validation_only_checkers() -> None:
    manifest = MANIFEST.read_text()
    blocks = {
        block.splitlines()[0]: block
        for block in re.split(r"(?m)^- id: ", manifest)[1:]
    }

    assert set(blocks) == {NEW_HOOK, OLD_HOOK}
    for block in blocks.values():
        assert "redact_secrets.py --check-index" in block
        assert "redact_secrets.py --fix" not in block
        assert "pass_filenames: false" in block
        assert "always_run: true" in block
        assert "types: []" in block
        assert not re.search(r"(?m)^  (?:files|exclude|types_or|exclude_types):", block)
    assert "Deprecated validation-only alias" in manifest


# Only the generic *mutators* must skip the archival/install roots. Other hooks
# carry deliberately narrower excludes (check-added-large-files exempts just
# `.specstory/history`), so scope the extraction per hook rather than sweeping
# every `exclude:` in the template.
GENERIC_MUTATOR_HOOKS = ("end-of-file-fixer", "trailing-whitespace")


def test_archive_exclusion_is_component_anchored_for_exact_roots() -> None:
    template = TEMPLATE.read_text()
    excludes = [
        match.group("exclude")
        for hook in GENERIC_MUTATOR_HOOKS
        for match in [
            re.search(
                rf"(?ms)^      - id: {re.escape(hook)}$.*?^        exclude: '(?P<exclude>[^']+)'$",
                template,
            )
        ]
        if match is not None
    ]

    assert excludes == [ARCHIVE_EXCLUDE, ARCHIVE_EXCLUDE]
    pattern = re.compile(excludes[0])
    roots = [
        ".agents",
        ".claude",
        ".codex",
        ".cursor",
        ".opencode",
        ".specify",
        ".specstory",
    ]
    for root in roots:
        assert pattern.search(root)
        assert pattern.search(f"{root}/nested/file.md")
    for lookalike in [
        ".agents-extra/file.md",
        ".claudex/file.md",
        ".codexish/file.md",
        ".cursor-rules/file.md",
        ".opencode2/file.md",
        ".specify-old/file.md",
        ".specstoryish/file.md",
        "nested/.specstory/file.md",
    ]:
        assert not pattern.search(lookalike)


def test_migrates_old_remote_pin_without_consuming_sibling_hook(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    config = repo / ".pre-commit-config.yaml"
    config.write_text(
        f"""# keep top
repos:
  # old agent hooks
  - repo: {HOOK_REPO}
    rev: {OLD_REV}  # sibling stays pinned here
    hooks:
      - id: {OLD_HOOK}
        files: '^custom-agent/.*\\.md$'  # carry scope
      # preserve sibling hook comment
      - id: sibling-hook
        stages: [pre-commit]

  # scanner block stays untouched
  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.22.1
    hooks:
      - id: gitleaks-system
        pass_filenames: false

  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: detect-private-key
      - id: end-of-file-fixer
        exclude: ^\\.specstory/
      - id: trailing-whitespace
        exclude: ^vendor/
"""
    )
    env = migration_env(tmp_path)

    run_bootstrap(repo, tmp_path, "--migrate", env_overrides=env)

    migrated = config.read_text()
    assert OLD_HOOK not in migrated
    assert migrated.count(HOOK_REPO) == 2
    assert f"rev: {OLD_REV}  # sibling stays pinned here" in migrated
    assert "- id: sibling-hook\n        stages: [pre-commit]" in migrated
    assert "# preserve sibling hook comment" in migrated
    assert f"rev: {NEW_REV}\n    hooks:\n      - id: {NEW_HOOK}" in migrated
    assert "files: '^custom-agent/.*\\.md$'  # carry scope" in migrated
    assert "- id: gitleaks-system\n        pass_filenames: false" in migrated
    assert "- id: detect-private-key\n      - id: end-of-file-fixer" in migrated
    assert f"exclude: '{ARCHIVE_EXCLUDE}'" in migrated
    assert f"exclude: '(?:^vendor/)|(?:{ARCHIVE_EXCLUDE})'" in migrated
    validation_log = (tmp_path / "pre-commit.log").read_text().splitlines()
    assert len(validation_log) == 1
    assert validation_log[0].startswith("validate-config ")


def test_migrates_local_redactor_with_sibling_and_preserves_modified_script(
    tmp_path: Path,
) -> None:
    repo = init_repo(tmp_path)
    config = repo / ".pre-commit-config.yaml"
    config.write_text(
        old_local_config(
            sibling=True,
            safe_options="""        # local safe overrides move with the hook
        files: '^local-agent/.*\\.md$'
        exclude: '^local-agent/generated/'
        stages: [pre-commit]
""",
        )
    )
    script = write_legacy_script(repo)
    commit_paths(repo, ".pre-commit-config.yaml", "scripts/redact_secrets.py")
    script.write_text(script.read_text() + "# local customization\n")
    before_script = script.read_bytes()
    env = migration_env(
        tmp_path,
        require_script=True,
        expected_config_text=OLD_HOOK,
    )

    result = run_bootstrap(repo, tmp_path, "--migrate", env_overrides=env)

    migrated = config.read_text()
    assert "- repo: local" in migrated
    assert "# sibling comment must survive" in migrated
    assert "- id: keep-local-sibling" in migrated
    assert OLD_HOOK not in migrated
    assert f"- id: {NEW_HOOK}" in migrated
    assert "# local safe overrides move with the hook" in migrated
    assert "files: '^local-agent/.*\\.md$'" in migrated
    assert "exclude: '^local-agent/generated/'" in migrated
    assert "stages: [pre-commit]" in migrated
    assert script.read_bytes() == before_script
    assert "preserving scripts/redact_secrets.py" in result.stderr
    assert git(repo, "diff", "--cached", "--name-only").stdout == ""


@pytest.mark.parametrize("sibling_position", ["before", "after"])
def test_flow_style_sibling_in_target_local_repo_refuses_without_writes(
    tmp_path: Path,
    sibling_position: str,
) -> None:
    repo = init_repo(tmp_path)
    config = repo / ".pre-commit-config.yaml"
    flow_sibling = (
        "      - {id: keep-local-sibling, name: Keep local sibling, "
        "entry: /usr/bin/true, language: system}\n"
    )
    legacy_hook = f"""      - id: {OLD_HOOK}
        name: Auto-redact secrets in agent artifacts
        entry: ./scripts/redact_secrets.py --fix
        language: system
        pass_filenames: false
"""
    hook_lines = (
        flow_sibling + legacy_hook
        if sibling_position == "before"
        else legacy_hook + flow_sibling
    )
    config.write_text(f"repos:\n  - repo: local\n    hooks:\n{hook_lines}")
    script = write_legacy_script(repo)
    before_config = config.read_bytes()
    before_script = script.read_bytes()
    env = migration_env(tmp_path)

    result = run_bootstrap(
        repo,
        tmp_path,
        "--migrate",
        check=False,
        env_overrides=env,
    )

    assert result.returncode == 5
    assert "unsupported hook item or flow-style hooks structure" in result.stderr
    assert config.read_bytes() == before_config
    assert script.read_bytes() == before_script
    assert not (tmp_path / "pre-commit.log").exists()
    assert not (repo / ".specstory").exists()


def test_flow_style_hooks_collection_refuses_without_writes(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    config = repo / ".pre-commit-config.yaml"
    config.write_text(
        f"""repos:
  - repo: local
    hooks: [{{id: {OLD_HOOK}, name: 'Auto-redact secrets in agent artifacts', entry: './scripts/redact_secrets.py --fix', language: system, pass_filenames: false}}]
"""
    )
    script = write_legacy_script(repo)
    before_config = config.read_bytes()
    before_script = script.read_bytes()
    env = migration_env(tmp_path)

    result = run_bootstrap(
        repo,
        tmp_path,
        "--migrate",
        check=False,
        env_overrides=env,
    )

    assert result.returncode == 5
    assert "unsupported indentation or flow syntax" in result.stderr
    assert config.read_bytes() == before_config
    assert script.read_bytes() == before_script
    assert not (tmp_path / "pre-commit.log").exists()
    assert not (repo / ".specstory").exists()


def test_redactor_only_local_block_is_replaced_and_script_removed_after_validation(
    tmp_path: Path,
) -> None:
    repo = init_repo(tmp_path)
    config = repo / ".pre-commit-config.yaml"
    config.write_text(old_local_config())
    script = write_legacy_script(repo)
    script_bytes = script.read_bytes()
    commit_paths(repo, ".pre-commit-config.yaml", "scripts/redact_secrets.py")
    env = migration_env(
        tmp_path,
        require_script=True,
        expected_config_text=OLD_HOOK,
    )

    result = run_bootstrap(repo, tmp_path, "--migrate", env_overrides=env)

    migrated = config.read_text()
    assert "repo: local" not in migrated
    assert f"repo: {HOOK_REPO}" in migrated
    assert f"rev: {NEW_REV}" in migrated
    assert f"- id: {NEW_HOOK}" in migrated
    assert not script.exists()
    # The removal is deliberately not staged; the old index blob is untouched.
    assert git(repo, "show", ":scripts/redact_secrets.py").stdout.encode() == script_bytes
    assert git(repo, "diff", "--cached", "--name-only").stdout == ""
    assert result.stderr.index("migrated atomically") < result.stderr.index("removed: scripts/redact_secrets.py")
    assert len((tmp_path / "pre-commit.log").read_text().splitlines()) == 1


def test_preserves_compatible_redactor_overrides_verbatim(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    config = repo / ".pre-commit-config.yaml"
    config.write_text(
        f"""repos:
  - repo: {HOOK_REPO}
    rev: {OLD_REV}
    hooks:
      - id: {OLD_HOOK}
        # custom safe trigger controls
        files: '^team-agent/(plans|history)/.*\\.md$'
        exclude: '^team-agent/generated/'  # retain exclusion
        stages: [pre-commit, pre-push]
"""
    )
    env = migration_env(tmp_path)

    run_bootstrap(repo, tmp_path, "--migrate", env_overrides=env)

    migrated = config.read_text()
    assert f"rev: {NEW_REV}" in migrated
    assert f"- id: {NEW_HOOK}" in migrated
    assert "# custom safe trigger controls" in migrated
    assert "files: '^team-agent/(plans|history)/.*\\.md$'" in migrated
    assert "exclude: '^team-agent/generated/'  # retain exclusion" in migrated
    assert "stages: [pre-commit, pre-push]" in migrated


@pytest.mark.parametrize(
    ("config_text", "error_fragment"),
    [
        (
            f"""repos:
  - repo: {HOOK_REPO}
    rev: {OLD_REV}
    hooks:
      - id: {OLD_HOOK}
        args: [--legacy]
""",
            "unsupported options: args",
        ),
        (
            old_local_config().replace(
                "entry: ./scripts/redact_secrets.py --fix",
                "entry: ./scripts/redact_secrets.py --fix --legacy",
            ),
            "entry is customized",
        ),
    ],
)
def test_unknown_redactor_args_or_entry_refuses_without_writes(
    tmp_path: Path,
    config_text: str,
    error_fragment: str,
) -> None:
    repo = init_repo(tmp_path)
    config = repo / ".pre-commit-config.yaml"
    config.write_text(config_text)
    script = write_legacy_script(repo)
    before_config = config.read_bytes()
    before_script = script.read_bytes()
    env = migration_env(tmp_path)

    result = run_bootstrap(
        repo,
        tmp_path,
        "--migrate",
        check=False,
        env_overrides=env,
    )

    assert result.returncode == 5
    assert error_fragment in result.stderr
    assert config.read_bytes() == before_config
    assert script.read_bytes() == before_script
    assert not (tmp_path / "pre-commit.log").exists()
    assert not (repo / ".specstory").exists()


def test_ambiguous_multiple_redactors_refuse_without_writes(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    config = repo / ".pre-commit-config.yaml"
    config.write_text(
        f"""repos:
  - repo: {HOOK_REPO}
    rev: {OLD_REV}
    hooks:
      - id: {OLD_HOOK}
  - repo: {HOOK_REPO}
    rev: {OLD_REV}
    hooks:
      - id: {OLD_HOOK}
"""
    )
    script = write_legacy_script(repo)
    before_config = config.read_bytes()
    before_script = script.read_bytes()
    env = migration_env(tmp_path)

    result = run_bootstrap(
        repo,
        tmp_path,
        "--migrate",
        check=False,
        env_overrides=env,
    )

    assert result.returncode == 5
    assert "multiple old/new" in result.stderr
    assert config.read_bytes() == before_config
    assert script.read_bytes() == before_script
    assert not (tmp_path / "pre-commit.log").exists()


def test_validator_failure_leaves_config_script_and_index_untouched(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    config = repo / ".pre-commit-config.yaml"
    config.write_text(old_local_config())
    script = write_legacy_script(repo)
    commit_paths(repo, ".pre-commit-config.yaml", "scripts/redact_secrets.py")
    before_config = config.read_bytes()
    before_script = script.read_bytes()
    before_tree = git(repo, "write-tree").stdout
    env = migration_env(
        tmp_path,
        validate_exit=42,
        require_script=True,
        expected_config_text=OLD_HOOK,
    )

    result = run_bootstrap(
        repo,
        tmp_path,
        "--migrate",
        check=False,
        env_overrides=env,
    )

    assert result.returncode == 5
    assert "rejected the complete migration candidate" in result.stderr
    assert config.read_bytes() == before_config
    assert script.read_bytes() == before_script
    assert git(repo, "write-tree").stdout == before_tree
    assert not (repo / ".specstory").exists()
    assert not list(repo.glob(".pre-commit-config.yaml.agent-history-hygiene.*"))
    assert not list(repo.glob(".pre-commit-config.yaml.agent-history-hygiene-meta.*"))


def test_untracked_legacy_script_is_preserved_when_provenance_is_unknown(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    config = repo / ".pre-commit-config.yaml"
    config.write_text(old_local_config())
    script = write_legacy_script(repo)
    before_script = script.read_bytes()
    env = migration_env(tmp_path, require_script=True)

    result = run_bootstrap(repo, tmp_path, "--migrate", env_overrides=env)

    assert f"- id: {NEW_HOOK}" in config.read_text()
    assert script.read_bytes() == before_script
    assert "not one exact stage-0 tracked regular file" in result.stderr


def test_second_migration_is_byte_stable_and_skips_validator(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    config = repo / ".pre-commit-config.yaml"
    config.write_text(
        f"""repos:
  - repo: {HOOK_REPO}
    rev: {OLD_REV}
    hooks:
      - id: {OLD_HOOK}
"""
    )
    env = migration_env(tmp_path)

    run_bootstrap(repo, tmp_path, "--migrate", env_overrides=env)
    first = config.read_bytes()
    run_bootstrap(repo, tmp_path, "--migrate", env_overrides=env)

    assert config.read_bytes() == first
    assert len((tmp_path / "pre-commit.log").read_text().splitlines()) == 1


def test_exact_new_hook_merges_mutator_exclusions_then_is_byte_stable(
    tmp_path: Path,
) -> None:
    repo = init_repo(tmp_path)
    config = repo / ".pre-commit-config.yaml"
    config.write_text(
        f"""repos:
  - repo: {HOOK_REPO}
    rev: {NEW_REV}
    hooks:
      - id: {NEW_HOOK}
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: end-of-file-fixer
        exclude: ^\\.specstory/
      - id: trailing-whitespace
"""
    )
    env = migration_env(tmp_path)

    run_bootstrap(repo, tmp_path, "--migrate", env_overrides=env)

    first = config.read_bytes()
    migrated = first.decode()
    assert migrated.count(f"- id: {NEW_HOOK}") == 1
    assert f"exclude: '{ARCHIVE_EXCLUDE}'" in migrated
    assert migrated.count(ARCHIVE_EXCLUDE) == 2
    assert len((tmp_path / "pre-commit.log").read_text().splitlines()) == 1

    run_bootstrap(repo, tmp_path, "--migrate", env_overrides=env)

    assert config.read_bytes() == first
    assert len((tmp_path / "pre-commit.log").read_text().splitlines()) == 1


def test_exact_new_hook_config_is_left_byte_for_byte_unchanged(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    config = repo / ".pre-commit-config.yaml"
    config.write_text(
        f"""# user formatting is intentional
repos:
  - repo: {HOOK_REPO}
    rev: {NEW_REV}  # immutable
    hooks:
      - id: {NEW_HOOK}
        files: '^only-this/.*\\.md$'
"""
    )
    script = write_legacy_script(repo, "# unrelated retained file\n")
    before_config = config.read_bytes()
    before_script = script.read_bytes()
    env = migration_env(tmp_path)

    run_bootstrap(repo, tmp_path, "--migrate", env_overrides=env)

    assert config.read_bytes() == before_config
    assert script.read_bytes() == before_script
    assert not (repo / ".specstory").exists()
    assert not (repo / ".gitleaks.toml").exists()
    assert not (tmp_path / "pre-commit.log").exists()


def test_migration_dry_run_validates_but_publishes_nothing(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    config = repo / ".pre-commit-config.yaml"
    config.write_text(
        f"""repos:
  - repo: {HOOK_REPO}
    rev: {OLD_REV}
    hooks:
      - id: {OLD_HOOK}
"""
    )
    before = config.read_bytes()
    env = migration_env(tmp_path)

    result = run_bootstrap(
        repo,
        tmp_path,
        "--migrate",
        "--dry-run",
        env_overrides=env,
    )

    assert config.read_bytes() == before
    assert "[dry-run] validated migration candidate" in result.stderr
    assert len((tmp_path / "pre-commit.log").read_text().splitlines()) == 1
    assert not (repo / ".specstory").exists()


@pytest.mark.parametrize(
    "original_mode",
    [0o644, 0o640, 0o755],
    ids=["ordinary", "custom-readable", "executable"],
)
def test_migration_preserves_config_mode_across_validation_and_atomic_publish(
    tmp_path: Path,
    original_mode: int,
) -> None:
    repo = init_repo(tmp_path)
    config = repo / ".pre-commit-config.yaml"
    config.write_text(
        f"""repos:
  - repo: {HOOK_REPO}
    rev: {OLD_REV}
    hooks:
      - id: {OLD_HOOK}
"""
    )
    config.chmod(original_mode)
    env = migration_env(tmp_path)
    env["FAKE_EXPECT_CONFIG_MODE"] = format(original_mode, "o")
    # A validator should not mutate its input, but publication must still restore
    # the source mode if one changes candidate metadata without changing bytes.
    env["FAKE_CHMOD_CONFIG_MODE"] = "600"

    run_bootstrap(repo, tmp_path, "--migrate", env_overrides=env)

    assert stat.S_IMODE(config.stat().st_mode) == original_mode
    assert len((tmp_path / "pre-commit.log").read_text().splitlines()) == 1


def test_adds_precise_rules_idempotently_and_keeps_history_visible(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)

    run_bootstrap(repo, tmp_path)
    ignore_file = repo / ".specstory" / ".gitignore"
    first = ignore_file.read_text()
    run_bootstrap(repo, tmp_path)

    assert ignore_file.read_text() == first
    assert first.count("/.project.json") == 1
    assert first.count("/statistics.json") == 1

    history = repo / ".specstory" / "history" / "session.md"
    history.parent.mkdir()
    history.write_text("# Session\n")
    ignored = git(repo, "check-ignore", "-q", str(history.relative_to(repo)), check=False)
    assert ignored.returncode == 1


def test_preserves_existing_nested_ignore_content(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    ignore_file = repo / ".specstory" / ".gitignore"
    ignore_file.parent.mkdir()
    ignore_file.write_text("# keep me\n/custom-local.json")

    run_bootstrap(repo, tmp_path)
    content = ignore_file.read_text()

    assert content.startswith("# keep me\n/custom-local.json\n")
    assert content.count("/.project.json") == 1
    assert content.count("/statistics.json") == 1


def test_dry_run_does_not_create_specstory_directory(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)

    result = run_bootstrap(repo, tmp_path, "--dry-run")

    assert not (repo / ".specstory").exists()
    assert "[dry-run] add '/.project.json'" in result.stderr
    assert "[dry-run] add '/statistics.json'" in result.stderr


def test_tracked_state_warns_until_explicitly_untracked(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    specstory = repo / ".specstory"
    specstory.mkdir()
    project = specstory / ".project.json"
    statistics = specstory / "statistics.json"
    project.write_text('{"workspace_id":"machine-a"}\n')
    statistics.write_text('{"sessions":{}}\n')
    git(repo, "add", "-f", ".specstory/.project.json", ".specstory/statistics.json")
    git(repo, "commit", "-qm", "track old SpecStory state")
    statistics.write_text('{"sessions":{"new-machine":{}}}\n')

    warned = run_bootstrap(repo, tmp_path)
    assert "SpecStory machine state is already tracked" in warned.stderr
    assert git(repo, "ls-files", ".specstory/.project.json").stdout.strip()
    assert git(repo, "ls-files", ".specstory/statistics.json").stdout.strip()

    migrated = run_bootstrap(repo, tmp_path, "--untrack-specstory-state")
    assert "files remain on disk" in migrated.stderr
    assert project.exists()
    assert statistics.exists()
    assert git(repo, "ls-files", ".specstory/.project.json").stdout == ""
    assert git(repo, "ls-files", ".specstory/statistics.json").stdout == ""
    assert "D  .specstory/.project.json" in git(repo, "status", "--short").stdout
    assert "D  .specstory/statistics.json" in git(repo, "status", "--short").stdout
