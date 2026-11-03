"""COMP-11 secrets manager (DPAPI MVP; Vault is Post-MVP per ADR-004)."""

from secrets_manager.dpapi import DpapiSecretsManager, SecretsManager

__all__ = ["DpapiSecretsManager", "SecretsManager"]
