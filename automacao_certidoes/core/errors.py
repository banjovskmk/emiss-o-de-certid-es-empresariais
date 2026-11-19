"""Exceções usadas pelas integrações e pelo orquestrador."""
from __future__ import annotations

from typing import Optional


class IntegrationError(Exception):
    """Falha definitiva da integração (status ERRO)."""

    def __init__(self, message: str, details: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.details = details


class TransientError(IntegrationError):
    """Falha provavelmente temporária (timeout, portal indisponível): permite nova tentativa."""


class ManualActionRequired(IntegrationError):
    """CAPTCHA, autenticação ou dados complementares: exige ação do usuário."""


class ConfigurationRequired(IntegrationError):
    """Integração ainda não implementada/configurada."""


class DocumentValidationError(IntegrationError):
    """Arquivo ausente, vazio ou em formato inesperado."""


class OperationCancelled(Exception):
    """Execução cancelada pelo usuário."""
