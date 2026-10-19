"""Where the owner's Claude API key lives: the macOS Keychain when available, else a private file (mode 0600)
outside the project. The key is never written to the data folder and never sent back to the browser in full."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

SERVICE, ACCOUNT = "timeby", "anthropic-api-key"


def validate(key: str) -> str:
    key = key.strip()
    if not key.startswith("sk-ant-") or len(key) < 24 or any(c.isspace() for c in key):
        raise ValueError("That doesn't look like a Claude API key — it starts with sk-ant-.")
    return key


def mask(key: str) -> str:
    return f"{key[:7]}…{key[-4:]}"


class MacKeychain:
    where = "macOS Keychain"

    def get(self) -> str | None:
        found = subprocess.run(["security", "find-generic-password", "-a", ACCOUNT, "-s", SERVICE, "-w"],
                               capture_output=True, text=True)
        if found.returncode != 0:
            return None
        return found.stdout.strip() or None

    def put(self, key: str) -> None:
        # -U updates an existing item; the key is passed to the system tool directly, never via a shell
        subprocess.run(["security", "add-generic-password", "-a", ACCOUNT, "-s", SERVICE, "-w", key, "-U"],
                       check=True, capture_output=True)

    def delete(self) -> None:
        subprocess.run(["security", "delete-generic-password", "-a", ACCOUNT, "-s", SERVICE], capture_output=True)


class PrivateFile:
    where = "private file in your home folder"

    def __init__(self, path: Path = Path.home() / ".config" / "timeby" / "anthropic_api_key"):
        self.path = path

    def get(self) -> str | None:
        if not self.path.is_file():
            return None
        return self.path.read_text().strip() or None

    def put(self, key: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # created private, never world-readable
        with os.fdopen(fd, "w") as f:
            f.write(key)

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)


class KeyStore:
    """Cached access, so building the UI state doesn't ask the Keychain every second."""

    def __init__(self, backend=None):
        self.backend = backend or (MacKeychain() if sys.platform == "darwin" and shutil.which("security") else PrivateFile())
        self._cache: str | None = None
        self._loaded = False

    def get(self) -> str | None:
        if not self._loaded:
            self._cache, self._loaded = self.backend.get(), True
        return self._cache

    def save(self, key: str) -> None:
        key = validate(key)
        self.backend.put(key)
        self._cache, self._loaded = key, True

    def remove(self) -> None:
        self.backend.delete()
        self._cache, self._loaded = None, True

    def public(self) -> dict:
        key = self.get()
        return {"configured": bool(key), "masked": mask(key) if key else "", "where": self.backend.where}
