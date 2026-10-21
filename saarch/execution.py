"""Isolated execution: the pipeline never executes against the host workspace.

Every subprocess the pipeline runs as a *check* (compile, the generated
project's own test suite) executes inside a throwaway snapshot of the
generated project, created in a temporary directory, with a hard timeout and
a scrubbed environment. Host secrets (GITHUB_TOKEN, model keys, ...) are not
inherited by the sandbox, side effects (test databases, caches) are deleted
with the sandbox, and the real project tree is only ever written by explicit
generation, never by execution.

Honesty contract: a sandbox that cannot be created or a command that cannot
run is reported as such — never as passed.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .audit import AuditLog

CHECK_TIMEOUT_SECONDS = 240

# Only these environment variables are inherited by sandboxed processes.
# Anything else (GITHUB_TOKEN, SAARCH_LLM_KEY, ...) stays out of the sandbox.
_SANDBOX_ENV_ALLOWLIST = (
    "PATH", "HOME", "LANG", "LC_ALL", "PYTHONIOENCODING", "PYTHONPATH",
    "SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "TMPDIR", "TMP", "TEMP",
)


class SandboxError(Exception):
    """The isolated environment could not be prepared."""


def _scrubbed_env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = {k: os.environ[k] for k in _SANDBOX_ENV_ALLOWLIST if k in os.environ}
    if extra:
        env.update({k: str(v) for k, v in extra.items()})
    return env


@contextmanager
def isolated_sandbox(source_dir: Path, keep_out=("__pycache__", "*.db", "*.pyc")):
    """Yield a private copy of ``source_dir`` in a temp dir, deleted after.

    The copy excludes caches and databases so execution always starts from a
    clean state; the source tree itself is never modified by anything that
    runs inside the sandbox.
    """
    source_dir = Path(source_dir)
    if not source_dir.is_dir():
        raise SandboxError(f"cannot isolate: {source_dir} does not exist")
    tmp = Path(tempfile.mkdtemp(prefix="saarch-sandbox-"))
    sandbox = tmp / "app"
    try:
        shutil.copytree(source_dir, sandbox,
                        ignore=shutil.ignore_patterns(*keep_out))
        yield sandbox
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_isolated(cmd: List[str], cwd: Path,
                 timeout: int = CHECK_TIMEOUT_SECONDS,
                 env_extra: Optional[Dict[str, str]] = None
                 ) -> Tuple[int, str, str, bool]:
    """Run a command inside the sandbox with a scrubbed environment.

    Returns (returncode, stdout, stderr, timed_out).
    """
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True,
            timeout=timeout, env=_scrubbed_env(env_extra))
        return proc.returncode, proc.stdout, proc.stderr, False
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return 124, out, err + f"\n[timeout after {timeout}s]", True
    except OSError as exc:
        return 127, "", str(exc), False


def run_check_isolated(cmd: List[str], source_dir: Path,
                       audit: Optional[AuditLog] = None,
                       name: str = "check",
                       timeout: int = CHECK_TIMEOUT_SECONDS
                       ) -> Tuple[int, str, str, bool]:
    """Prepare a sandbox, run ``cmd`` in it, record the isolation in the audit.

    This is the single entry point the verifier uses for executable checks.
    """
    try:
        with isolated_sandbox(source_dir) as sandbox:
            if audit:
                audit.append("isolated_check",
                             check=name, sandbox=str(sandbox),
                             timeout=timeout, cmd=cmd)
            return run_isolated(cmd, sandbox, timeout=timeout)
    except SandboxError as exc:
        return 127, "", str(exc), False
