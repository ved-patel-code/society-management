"""Module framework: ``ModuleSpec`` + ``MODULE_REGISTRY`` (docs/02 §3).

Each toggleable module declares a ``ModuleSpec`` and registers it. The registry
drives: the permission catalog seeded at startup, the ``depends_on`` check when a
society enables a module, and (later) router mounting. The Platform Foundation is
NOT a toggleable module, but it registers its own permission-bearing spec so its
permission keys seed like everyone else's.

Foundation scope: no business module is built yet, so the registry ships empty of
feature modules — but the framework exists and is exercised (foundation spec +
``require_module`` gate).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.common.errors import DependencyError

if TYPE_CHECKING:  # avoid importing FastAPI/router types at module-import time
    from fastapi import APIRouter


@dataclass(frozen=True)
class PermissionDef:
    """A single permission the module contributes to the catalog."""

    key: str  # e.g. "houses.update_status"
    description: str


@dataclass
class ModuleSpec:
    """Self-description of a module (docs/02 §3)."""

    key: str
    name: str
    permissions: list[PermissionDef] = field(default_factory=list)
    default_config: dict = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    is_core: bool = False
    router: "APIRouter | None" = None


class ModuleRegistry:
    """In-process registry of all known modules, keyed by ``ModuleSpec.key``."""

    def __init__(self) -> None:
        self._modules: dict[str, ModuleSpec] = {}

    def register(self, spec: ModuleSpec) -> ModuleSpec:
        if spec.key in self._modules:
            raise ValueError(f"Module '{spec.key}' is already registered.")
        self._modules[spec.key] = spec
        return spec

    def get(self, key: str) -> ModuleSpec | None:
        return self._modules.get(key)

    def all(self) -> list[ModuleSpec]:
        return list(self._modules.values())

    def keys(self) -> list[str]:
        return list(self._modules.keys())

    def all_permission_keys(self) -> list[PermissionDef]:
        """Every permission across all registered modules (deduped by key)."""
        seen: dict[str, PermissionDef] = {}
        for spec in self._modules.values():
            for perm in spec.permissions:
                seen[perm.key] = perm
        return list(seen.values())

    def resolve_dependencies(self, key: str, enabled_keys: set[str]) -> None:
        """Raise ``DependencyError`` if enabling ``key`` needs a module that is
        not in ``enabled_keys`` (docs/PF §6). ``enabled_keys`` is what the society
        already has enabled (excluding ``key`` itself).
        """
        spec = self.get(key)
        if spec is None:
            raise DependencyError(
                f"Unknown module '{key}'.", details={"module_key": key}
            )
        missing = [dep for dep in spec.depends_on if dep not in enabled_keys]
        if missing:
            raise DependencyError(
                f"Module '{key}' requires: {', '.join(missing)}.",
                details={"module_key": key, "missing": missing},
            )


MODULE_REGISTRY = ModuleRegistry()
