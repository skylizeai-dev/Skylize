"""Tenant onboarding + RBAC service (Subsystem 1)."""

from __future__ import annotations

from .service import TenantError, TenantService

__all__ = ["TenantError", "TenantService"]
