"""Provider-agnostic credential storage.

A :class:`CredentialStore` persists a provider's credentials in the system
keyring (macOS Keychain / Windows Credential Locker / Secret Service), falling
back to a mode-0600 JSON file when no keyring backend is available. The set of
fields is supplied by the provider, so the same code serves every bank.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import keyring
from keyring.errors import NoKeyringError, PasswordDeleteError


@dataclass(frozen=True)
class CredentialField:
    """One credential a provider needs.

    ``pattern`` is an optional regex the value must fully match (used by the CLI
    to validate input and by a web form for client-side checks). ``secret``
    marks fields that should be masked when entered or displayed.
    """

    name: str
    label: str
    secret: bool = False
    pattern: str | None = None
    help: str | None = None

    def validate(self, value: str) -> bool:
        if self.pattern is None:
            return bool(value)
        return re.fullmatch(self.pattern, value or "") is not None


class CredentialStore:
    """Load, save and forget one provider's credentials."""

    def __init__(self, service: str, fields: list[CredentialField],
                 fallback_file: Path | None = None):
        self.service = service
        self.fields = list(fields)
        self.field_names = [f.name for f in self.fields]
        self.fallback_file = fallback_file or (
            Path.home() / ".config" / service / "credentials.json")

    # -- fallback file -----------------------------------------------------
    def _fallback_load(self) -> dict:
        if self.fallback_file.exists():
            return json.loads(self.fallback_file.read_text())
        return {}

    def _fallback_save(self, creds: dict) -> None:
        self.fallback_file.parent.mkdir(parents=True, exist_ok=True)
        self.fallback_file.touch(mode=0o600)
        self.fallback_file.write_text(json.dumps(creds))

    # -- public API --------------------------------------------------------
    def load(self) -> dict | None:
        """Return the stored credentials, or ``None`` if any field is missing."""
        try:
            creds = {f: keyring.get_password(self.service, f)
                     for f in self.field_names}
        except NoKeyringError:
            creds = self._fallback_load()
        if all(creds.get(f) for f in self.field_names):
            return {f: creds[f] for f in self.field_names}
        return None

    def save(self, creds: dict) -> str:
        """Persist ``creds``; return a human-readable description of where to."""
        try:
            for f in self.field_names:
                keyring.set_password(self.service, f, creds[f])
            return "system keyring"
        except NoKeyringError:
            self._fallback_save({f: creds[f] for f in self.field_names})
            return str(self.fallback_file)

    def forget(self) -> None:
        """Remove any stored credentials from both keyring and fallback file."""
        try:
            for f in self.field_names:
                try:
                    keyring.delete_password(self.service, f)
                except PasswordDeleteError:
                    pass
        except NoKeyringError:
            pass
        self.fallback_file.unlink(missing_ok=True)
