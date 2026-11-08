"""Provider registration and lookup.

Providers register themselves at import time via :func:`register` (used as a
class decorator). :mod:`banklib.providers` imports every provider module so that
:func:`list_providers` / :func:`get_provider` see them all.
"""

from __future__ import annotations

from .base import Provider
from .errors import UnknownProviderError

_REGISTRY: dict[str, Provider] = {}


def register(cls: type[Provider]) -> type[Provider]:
    """Class decorator: instantiate ``cls`` and register it under its ``name``."""
    if not cls.name:
        raise ValueError(f"{cls.__name__} must set a non-empty 'name'")
    _REGISTRY[cls.name] = cls()
    return cls


def list_providers() -> list[str]:
    """Registered provider names, sorted."""
    _ensure_loaded()
    return sorted(_REGISTRY)


def get_provider(name: str) -> Provider:
    """Return the registered provider instance for ``name``."""
    _ensure_loaded()
    try:
        return _REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise UnknownProviderError(
            f"Unknown provider {name!r}. Known providers: {known}") from None


def _ensure_loaded() -> None:
    # Importing the subpackage triggers each provider module to self-register.
    if not _REGISTRY:
        from . import providers  # noqa: F401
