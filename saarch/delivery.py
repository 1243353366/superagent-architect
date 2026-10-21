"""Stage F: delivery.

Two delivery paths, both real:
  * zip export — always available, produces a repository-ready archive.
  * GitHub delivery — pushes the generated project to a new repository via the
    REST API, using only the Python standard library (no PyGithub required,
    though the interface matches so PyGithub can be substituted). Uses the
    GITHUB_TOKEN environment variable; the token is never persisted to the
    project record, the audit trail, or any generated file.
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional


def export_zip(generated_dir: Path, out_dir: Path) -> Path:
    """Bundle the generated project into a repository-ready zip archive."""
    generated_dir = Path(generated_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / f"{generated_dir.name}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(generated_dir.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts:
                zf.write(f, f.relative_to(generated_dir))
    return archive


class GitHubDelivery:
    """Minimal REST client for repository delivery (stdlib only)."""

    API = "https://api.github.com"

    def __init__(self, token: str, api: str = API):
        if not token:
            raise ValueError("a GitHub token is required for delivery")
        self._token = token
        self._api = api.rstrip("/")

    def _request(self, method: str, path: str, payload: Optional[dict] = None,
                 expect: tuple = (200, 201)):
        req = urllib.request.Request(
            self._api + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "superagent-architect",
            })
        try:
            with urllib.request.urlopen(req) as resp:
                body = resp.read()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GitHub API {method} {path} -> {exc.code}: {detail}") from exc

    def create_repo(self, name: str, description: str = "",
                    private: bool = True) -> dict:
        """Create the repository. PRIVATE by default: generated applications
        ship without authentication, so publishing to a public target is an
        explicit, approved decision — never a default."""
        # auto_init seeds the repo with an initial commit: the git-trees API
        # cannot build on a completely empty repository (409).
        try:
            return self._request("POST", "/user/repos", {
                "name": name,
                "description": description,
                "private": private,
                "auto_init": True,
            })
        except RuntimeError as exc:
            # reuse an existing repository rather than failing the delivery
            if "name already exists" not in str(exc):
                raise
            login = self._request("GET", "/user")["login"]
            return self._request("GET", f"/repos/{login}/{name}")

    def push_tree(self, full_name: str, files: dict, message: str,
                  branch: str = "main") -> str:
        """Create a single commit containing every file, on top of HEAD.

        ``files`` maps path -> text content. Binary files are not supported by
        this method (the generated app is text-only).
        """
        try:
            ref = self._request("GET", f"/repos/{full_name}/git/ref/heads/{branch}")
        except RuntimeError as exc:
            # repository exists but has no commits yet: bootstrap via the
            # contents API (the only write path that works on an empty repo)
            if "404" not in str(exc) and "Git Repository is empty" not in str(exc):
                raise
            bootstrap = sorted(files)[0]
            self._request("PUT", f"/repos/{full_name}/contents/{bootstrap}", {
                "message": message,
                "content": base64.b64encode(
                    files[bootstrap].encode("utf-8")).decode("ascii"),
            })
            del files[bootstrap]
            ref = self._request("GET",
                                f"/repos/{full_name}/git/ref/heads/{branch}")
        base_commit = ref["object"]["sha"]
        commit_data = self._request("GET", f"/repos/{full_name}/git/commits/{base_commit}")
        tree = [{"path": path, "mode": "100644", "type": "blob", "content": content}
                for path, content in sorted(files.items())]
        tree_data = self._request("POST", f"/repos/{full_name}/git/trees", {
            "base_tree": commit_data["tree"]["sha"],
            "tree": tree,
        })
        commit = self._request("POST", f"/repos/{full_name}/git/commits", {
            "message": message,
            "tree": tree_data["sha"],
            "parents": [base_commit],
        })
        self._request("PATCH", f"/repos/{full_name}/git/refs/heads/{branch}", {
            "sha": commit["sha"],
        })
        return f"https://github.com/{full_name}"

    def deliver(self, generated_dir: Path, repo_name: str,
                description: str = "", private: bool = True) -> dict:
        """Full delivery: create repo (private by default), push every file.

        ``private`` must be an explicit decision made and approved at the CLI
        layer; this method records nothing and assumes nothing about it.
        """
        generated_dir = Path(generated_dir)
        files = {}
        for f in sorted(generated_dir.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".db":
                files[str(f.relative_to(generated_dir))] = f.read_text(encoding="utf-8")
        repo = self.create_repo(repo_name, description=description,
                                private=private)
        url = self.push_tree(repo["full_name"], files,
                             "Initial delivery by superagent-architect")
        return {"repo_url": url, "repo_name": repo["full_name"],
                "files": len(files), "private": private,
                "delivered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ")}


def get_github_token() -> str:
    """Read the delivery token from the environment. Never persisted."""
    return os.environ.get("GITHUB_TOKEN", "")
