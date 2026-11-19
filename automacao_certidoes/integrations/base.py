"""Contrato das integrações e contexto de execução."""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from core.errors import OperationCancelled
from core.models import OperationType, ServiceConfig, ServiceResult, Status

LogFn = Callable[[str, str, str, Optional[Status]], None]  # level, step, message, status


class RunContext:
    """Entregue a cada integração: CNPJ, pasta de saída, log e cancelamento."""

    def __init__(self, cnpj: str, run_dir: Path, cancel_event: threading.Event, log: LogFn,
                 extra: Optional[dict] = None, manual_action: Optional[Callable[[str], None]] = None):
        self.cnpj = cnpj
        self.run_dir = Path(run_dir)
        self.cancel_event = cancel_event
        self._log = log
        self.extra = extra or {}  # dados complementares informados pelo usuário
        self._manual_action = manual_action

    def info(self, step: str, message: str) -> None:
        self._log("INFO", step, message, Status.EM_ANDAMENTO)

    def warn(self, step: str, message: str) -> None:
        self._log("AVISO", step, message, Status.EM_ANDAMENTO)

    def check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise OperationCancelled()

    def request_manual_action(self, message: str) -> None:
        """Pausa a automação para o usuário resolver CAPTCHA/login na janela visível.

        A sessão Playwright permanece aberta; após a confirmação na interface,
        a integração retoma exatamente da etapa em que parou.
        """
        self.check_cancel()
        if self._manual_action is None:
            from core.errors import ManualActionRequired
            raise ManualActionRequired(message)
        self._manual_action(message)
        self.check_cancel()

    def wait(self, seconds: float) -> None:
        """Pausa que responde imediatamente ao cancelamento."""
        if self.cancel_event.wait(max(0.0, seconds)):
            raise OperationCancelled()


class BaseIntegration(ABC):
    simulated = False

    def __init__(self, config: ServiceConfig):
        self.config = config

    @abstractmethod
    def execute(self, ctx: RunContext) -> ServiceResult:
        """Executa a operação e devolve resultado estruturado. Pode lançar IntegrationError."""

    def result(self, status: Status, message: str, document_path: Optional[str] = None,
               official_document: bool = False, error_details: Optional[str] = None) -> ServiceResult:
        return ServiceResult(service_id=self.config.id, service_name=self.config.nome,
                             operation=self.config.tipo, status=status, message=message,
                             document_path=document_path, official_document=official_document,
                             simulated=self.simulated, started_at=datetime.now(), error_details=error_details)


class PendingIntegration(BaseIntegration):
    """Serviço cadastrado, mas sem integração real configurada."""

    def execute(self, ctx: RunContext) -> ServiceResult:
        from core.errors import ConfigurationRequired
        raise ConfigurationRequired("Integração real ainda não implementada/configurada para este serviço.")
