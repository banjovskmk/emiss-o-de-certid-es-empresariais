"""
Classe base para integrações que usam o BrowserSession compartilhado.

Ao invés de cada integração abrir/fechar seu próprio playwright, elas recebem
um AutoCtx já montado pelo orquestrador (via PlaywrightOrchestrator), que mantém
um único contexto de navegador persistente para toda a execução.

Cada integração só precisa implementar `_emitir(ctx)`.
"""
from __future__ import annotations

from abc import abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from core.errors import IntegrationError, ManualActionRequired, TransientError
from core.models import ServiceResult, Status
from integrations.base import BaseIntegration, RunContext

if TYPE_CHECKING:
    from integrations.browser_context import AutoCtx


class BrowserIntegration(BaseIntegration):
    """
    Integração real baseada em Playwright com contexto de navegador compartilhado.
    
    O orquestrador injeta o `auto_ctx` via `set_ctx()` antes de chamar `execute()`.
    """
    simulated = False

    def __init__(self, config) -> None:
        super().__init__(config)
        self._auto_ctx: Optional["AutoCtx"] = None
        self._captcha_cb: Optional[Callable[[str], None]] = None

    def set_ctx(self, auto_ctx: "AutoCtx") -> None:
        """Injeta o contexto de browser antes da execução."""
        self._auto_ctx = auto_ctx

    def set_captcha_callback(self, cb: Callable[[str], None]) -> None:
        self._captcha_cb = cb

    def execute(self, ctx: RunContext) -> ServiceResult:
        if self._auto_ctx is None:
            raise IntegrationError(
                "Contexto de navegador não configurado. "
                "Use PlaywrightOrchestrator para executar integrações reais."
            )
        try:
            return self._emitir(self._auto_ctx)
        except ManualActionRequired:
            raise
        except TransientError:
            raise
        except IntegrationError:
            raise
        except Exception as exc:
            msg = str(exc)
            # Erros de rede transientes
            if any(k in msg for k in [
                "ERR_EMPTY_RESPONSE", "ERR_CONNECTION", "ERR_NETWORK",
                "ERR_TIMED_OUT", "ERR_ABORTED", "ECONNRESET", "net::"
            ]):
                raise TransientError(f"Falha de rede: {msg[:120]}")
            raise IntegrationError(f"Erro inesperado: {msg[:200]}")

    @abstractmethod
    def _emitir(self, ctx: "AutoCtx") -> ServiceResult:
        """Implementa a emissão/consulta usando o ctx do browser."""
