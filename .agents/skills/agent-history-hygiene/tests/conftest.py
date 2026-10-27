"""Shared pytest fixtures for the agent-history-hygiene test suite.

The redactor under test (`assets/redact_secrets.py`) is a PEP 723 uv
script with a `#!/usr/bin/env -S uv run --script` shebang. Its body is
otherwise a plain Python module — we load it via `importlib` so tests
can call individual functions without shelling out.
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = SKILL_ROOT / "assets"
SCRIPTS_DIR = SKILL_ROOT / "scripts"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


# --- detect-private-key-safe PEM builders ----------------------------------
# pre-commit's `detect-private-key` greps its BLACKLIST as plain substrings and
# honours NO allowlist mechanism -- not `<!-- gitleaks:allow -->`, not
# .github/secret_scanning.yml. `npx skills add` materialises this whole skill
# (tests included) into the consumer's repo at `.agents/skills/<name>/`, which
# is inside their own hook's scan scope. So a literal `BEGIN <TYPE> PRIVATE
# KEY` anywhere in a file we SHIP fails `git commit` in every downstream repo
# that runs that hook -- with no marker they can add to the file to stop it.
#
# Tests therefore assemble the headers at runtime, the same split-literal trick
# `assets/redact_secrets.py` already uses for its OpenVPN token. Enforced by
# tests/test_shipped_file_hygiene.py. Do not inline these back into a literal.
_PK = "PRIVATE" + " KEY"

# The version digits are separate names, not inline literals: CPython folds
# `"PuTTY-User-" + "Key-File-2"` at compile time, which would put the intact
# BLACKLIST entry into tests/__pycache__/*.pyc even though the .py is clean.
# Splitting at the digit works because the truncated prefixes are not
# themselves BLACKLIST entries.
_PUTTY_KEY_FILE_VERSION = 2
_OPENVPN_STATIC_KEY_VERSION = 1

#: PuTTY private-key header (BLACKLIST entry with no "PRIVATE KEY" text).
PUTTY_HEADER = f"PuTTY-User-Key-File-{_PUTTY_KEY_FILE_VERSION}"
#: OpenVPN static-key header (likewise).
OPENVPN_HEADER = (
    f"-----BEGIN OpenVPN Static key V{_OPENVPN_STATIC_KEY_VERSION}-----"
)


def pem_header(kind: str = "RSA") -> str:
    """`-----BEGIN <kind> PRIVATE KEY-----`, assembled at runtime."""
    return f"-----BEGIN {kind} {_PK}-----"


def pem_footer(kind: str = "RSA") -> str:
    """`-----END <kind> PRIVATE KEY-----`, assembled at runtime."""
    return f"-----END {kind} {_PK}-----"


def pem_block(kind: str = "RSA", body: str = "fake material") -> str:
    """A full fake PEM block, header + body + footer, newline-terminated."""
    return f"{pem_header(kind)}\n{body}\n{pem_footer(kind)}\n"


#: Fixture placeholders expanded when a corpus fixture is staged, so the
#: fixture file on disk carries no BLACKLIST substring either. Mirrors the
#: existing `__SYNTHETIC_STRIPE_WEBHOOK_SECRET__` treatment.
#: Length of the Stripe webhook secret body. A named constant on purpose:
#: CPython constant-folds `"a" * 32` at compile time, which would bake a live
#: `stripe-webhook-secret` shape into tests/__pycache__/*.pyc -- and a
#: downstream user who runs pytest inside their repo would find gitleaks
#: firing on a build artifact. A name defeats the fold.
_WHSEC_BODY_LEN = 32

FIXTURE_PLACEHOLDERS = {
    "__SYNTHETIC_PEM_BEGIN__": pem_header(),
    "__SYNTHETIC_PEM_END__": pem_footer(),
    "__SYNTHETIC_PEM_HEADER_OPENSSH__": pem_header("OPENSSH"),
    "__SYNTHETIC_STRIPE_WEBHOOK_SECRET__": "whsec_" + "a" * _WHSEC_BODY_LEN,
}


def _load_redact_secrets_module():
    """Import assets/redact_secrets.py as a module despite the shebang."""
    script_path = ASSETS_DIR / "redact_secrets.py"
    spec = importlib.util.spec_from_file_location("redact_secrets", script_path)
    if spec is None or spec.loader is None:  # pragma: no cover — defensive
        raise RuntimeError(f"Could not load spec for {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["redact_secrets"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def redact_secrets():
    """The imported redact_secrets module."""
    return _load_redact_secrets_module()


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """Directory containing *.md corpus files."""
    return FIXTURES_DIR


@pytest.fixture(scope="session")
def assets_dir() -> Path:
    """Skill-local assets/ (templates + bundled redactor)."""
    return ASSETS_DIR


@pytest.fixture(scope="session")
def scripts_dir() -> Path:
    """Skill-local scripts/."""
    return SCRIPTS_DIR


@pytest.fixture(scope="session")
def gitleaks_available() -> bool:
    """Whether the gitleaks CLI is on PATH. Corpus tests skip when false."""
    return shutil.which("gitleaks") is not None


@pytest.fixture
def tmp_git_repo(tmp_path: Path, assets_dir: Path):
    """Bootstrap an empty git repo in tmp_path with our .gitleaks.toml
    config copied in. Returns the repo path.

    Uses -c user.email/name so the test works on CI boxes without a
    configured git identity.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-q", "-b", "main"],
        cwd=repo,
        check=True,
    )
    # Copy our gitleaks config so rules + allowlists apply.
    shutil.copy(assets_dir / "gitleaks.toml.template", repo / ".gitleaks.toml")
    # Seed an initial commit so --staged has something to diff against.
    (repo / "README.md").write_text("init\n", encoding="utf-8")
    subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=test",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "init",
        ],
        cwd=repo,
        check=True,
    )
    return repo
