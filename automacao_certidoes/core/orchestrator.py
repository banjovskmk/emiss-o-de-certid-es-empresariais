"""Orquestrador: executa os serviços selecionados, um por vez, em thread própria.

Não conhece a interface: comunica-se apenas por eventos (callback 'emit'), que a GUI
consome de forma segura por uma fila.
"""
from __future__ import annotations

import logging
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from core import validators
from core.download_manager import create_run_dir, validate_file
from core.errors import (ConfigurationRequired, IntegrationError, ManualActionRequired,
                         OperationCancelled, TransientError)
from core.models import (CountersEvent, IntegrationMode, LogEvent, ManualActionEvent, RunFinishedEvent,
                         RunStartedEvent, ServiceConfig, ServiceResult, Status, StatusEvent)
from core.summary import build_summary, compute_counters, render_report
from integrations.base import BaseIntegration, RunContext
from services.history_service import HistoryService

_LEVELS = {"INFO": logging.INFO, "SUCESSO": logging.INFO, "AVISO": logging.WARNING, "ERRO": logging.ERROR}


@dataclass
class _Run:
    run_id: str
    cnpj: str
    run_dir: Path
    started: datetime
    selected: List[ServiceConfig]


class Orchestrator:
    def __init__(self, services: List[ServiceConfig], history: HistoryService, emit: Callable[[object], None],
                 docs_root: Path, factory: Callable[[ServiceConfig], BaseIntegration],
                 max_retries: int = 2, retry_wait_s: float = 3.0):
        self._services: Dict[str, ServiceConfig] = {s.id: s for s in services}
        self._order = [s.id for s in services]
        self._history = history
        self._emit_cb = emit
        self._docs_root = Path(docs_root)
        self._factory = factory
        self._max_retries = max_retries
        self._retry_wait = retry_wait_s
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._logger = logging.getLogger("certidoes")

    # ------------------------------------------------------------------ API pública
    @property
    def is_running(self) -> bool:
        return self._running

    def start(self, cnpj: str, selected_ids: List[str]) -> str:
        err = validators.cnpj_error(cnpj)
        if err:
            raise ValueError(err)
        wanted = set(selected_ids)
        unknown = wanted - set(self._services)
        if unknown:
            raise ValueError(f"Serviços desconhecidos: {', '.join(sorted(unknown))}")
        ids = [i for i in self._order if i in wanted]
        if not ids:
            raise ValueError("Nenhum item selecionado.")
        with self._lock:
            if self._running:
                raise RuntimeError("Já existe uma execução em andamento.")
            self._running = True
        try:
            self._cancel.clear()
            clean = validators.clean_cnpj(cnpj)
            started = datetime.now()
            run_dir = create_run_dir(self._docs_root, clean, started)
            run = _Run(f"{started:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}", clean, run_dir, started,
                       [self._services[i] for i in ids])
            self._history.create_run(run.run_id, clean, started, ids, run_dir)
        except Exception:
            self._running = False
            raise
        self._thread = threading.Thread(target=self._run, args=(run,), name="orquestrador", daemon=True)
        self._thread.start()
        return run.run_id

    def cancel(self) -> None:
        if self._running:
            self._cancel.set()

    def wait(self, timeout: Optional[float] = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    # ------------------------------------------------------------------ internos
    def _emit(self, event: object) -> None:
        try:
            self._emit_cb(event)
        except Exception:  # um ouvinte com defeito não pode derrubar a execução
            self._logger.exception("Falha ao entregar evento")

    def _log(self, level: str, service: Optional[str], step: str, message: str, status: Optional[Status] = None) -> None:
        self._logger.log(_LEVELS.get(level, logging.INFO), "%s | %s | %s", service or "-", step, message)
        self._emit(LogEvent(datetime.now(), level, service, step, message, status))

    def _run(self, run: _Run) -> None:
        results: List[ServiceResult] = []
        total = len(run.selected)
        simulated_all = all(s.modo == IntegrationMode.SIMULADA for s in run.selected)
        try:
            self._emit(RunStartedEvent(run.run_id, run.cnpj, run.run_dir))
            self._emit(CountersEvent(compute_counters(total, results)))
            self._log("INFO", None, "Início", f"Iniciando processamento do CNPJ {validators.format_cnpj(run.cnpj)}.")
            for s in run.selected:
                if s.modo == IntegrationMode.PENDENTE:
                    self._log("AVISO", s.nome_curto, "Configuração", "Integração pendente de configuração.",
                              Status.PENDENTE_CONFIGURACAO)
                elif s.modo == IntegrationMode.SIMULADA:
                    self._log("AVISO", s.nome_curto, "Configuração", "Integração SIMULADA: nenhum portal será acessado.")
            for idx, svc in enumerate(run.selected):
                if self._cancel.is_set():
                    for rest in run.selected[idx:]:
                        self._record(run, results, self._cancelled(rest, "Cancelado antes de iniciar."))
                    break
                if idx > 0:
                    self._log("INFO", None, "Fila", "Iniciando o próximo serviço selecionado.")
                self._record(run, results, self._run_service(run, svc))
        except Exception as exc:  # defesa final: nunca deixar a interface presa em "executando"
            self._log("ERRO", None, "Orquestrador", f"Falha inesperada no processamento: {exc}")
            self._logger.exception("Falha inesperada")
        finally:
            self._finish(run, results, total, simulated_all)

    def _record(self, run: _Run, results: List[ServiceResult], r: ServiceResult) -> None:
        results.append(r)
        self._history.add_item(run.run_id, r)
        self._emit(StatusEvent(r.service_id, r.status, r.message, r.document_path))
        self._emit(CountersEvent(compute_counters(len(run.selected), results)))

    def _finish(self, run: _Run, results: List[ServiceResult], total: int, simulated_all: bool) -> None:
        cancelled = self._cancel.is_set()
        finished = datetime.now()
        counters = compute_counters(total, results)
        summary = build_summary(counters, cancelled, simulated_all)
        try:
            (run.run_dir / "resumo_execucao.txt").write_text(
                render_report(run.run_id, validators.format_cnpj(run.cnpj), run.started, finished, results, summary),
                encoding="utf-8")
        except OSError as exc:
            self._log("AVISO", None, "Resumo", f"Não foi possível salvar resumo_execucao.txt: {exc}")
        try:
            self._history.finish_run(run.run_id, finished, "cancelada" if cancelled else "finalizada", summary)
        except Exception:
            self._logger.exception("Falha ao gravar histórico")
        self._log("SUCESSO" if not counters["erros"] else "AVISO", None, "Fim", summary)
        self._running = False
        self._emit(RunFinishedEvent(run.run_id, summary, counters, cancelled))

    def _cancelled(self, svc: ServiceConfig, msg: str) -> ServiceResult:
        now = datetime.now()
        r = ServiceResult(svc.id, svc.nome, svc.tipo, Status.CANCELADO, msg,
                          simulated=svc.modo == IntegrationMode.SIMULADA, started_at=now, finished_at=now)
        self._log("AVISO", svc.nome_curto, "Cancelamento", msg, Status.CANCELADO)
        return r

    def _request_manual_action(self, service_name: str, message: str, timeout_s: float = 600.0) -> None:
        """Emite um pedido à GUI e espera a confirmação do usuário sem fechar o browser."""
        request = ManualActionEvent(service_name=service_name, message=message)
        self._emit(request)
        deadline = time.monotonic() + timeout_s
        while not request.resume_event.wait(0.25):
            if self._cancel.is_set():
                raise OperationCancelled()
            if time.monotonic() >= deadline:
                raise ManualActionRequired(
                    f"Tempo esgotado aguardando a ação manual em {service_name}. "
                    "Execute novamente para tentar concluir a operação."
                )
        if self._cancel.is_set():
            raise OperationCancelled()

    def _run_service(self, run: _Run, svc: ServiceConfig) -> ServiceResult:
        started = datetime.now()
        name = svc.nome_curto
        sim = svc.modo == IntegrationMode.SIMULADA

        def log(level: str, step: str, msg: str, status: Optional[Status]) -> None:
            self._log(level, name, step, msg, status)

        ctx = RunContext(run.cnpj, run.run_dir, self._cancel, log,
                         manual_action=lambda message: self._request_manual_action(name, message))
        self._emit(StatusEvent(svc.id, Status.EM_ANDAMENTO))
        self._log("INFO", name, "Início", "Iniciando solicitação.", Status.EM_ANDAMENTO)
        attempt = 0
        result: Optional[ServiceResult] = None
        while result is None:
            attempt += 1
            try:
                result = self._factory(svc).execute(ctx)
            except OperationCancelled:
                result = self._cancelled(svc, "Operação cancelada pelo usuário durante a execução.")
            except TransientError as exc:
                if attempt <= self._max_retries and not self._cancel.is_set():
                    self._log("AVISO", name, "Nova tentativa",
                              f"{exc.message} Tentativa {attempt} de {self._max_retries + 1}; tentando novamente.",
                              Status.EM_ANDAMENTO)
                    if self._cancel.wait(self._retry_wait):
                        result = self._cancelled(svc, "Operação cancelada durante a espera para nova tentativa.")
                else:
                    result = self._err(svc, Status.ERRO, f"{exc.message} Esgotadas {attempt} tentativa(s).", exc.details)
            except ManualActionRequired as exc:
                result = self._err(svc, Status.PENDENTE_ACAO_MANUAL, exc.message, exc.details)
            except ConfigurationRequired as exc:
                result = self._err(svc, Status.PENDENTE_CONFIGURACAO, exc.message, exc.details)
            except IntegrationError as exc:
                result = self._err(svc, Status.ERRO, exc.message, exc.details)
            except Exception as exc:
                result = self._err(svc, Status.ERRO, f"Erro inesperado: {exc}", traceback.format_exc())

        # "Documento emitido" só vale com arquivo existente e válido.
        if result.status == Status.DOCUMENTO_EMITIDO:
            ok, why = validate_file(result.document_path)
            if not ok:
                result.status = Status.ERRO
                result.message = f"Documento não obtido ou inválido: {why}."
        result.started_at = result.started_at or started
        result.finished_at = datetime.now()
        result.simulated = result.simulated or sim
        level = {Status.DOCUMENTO_EMITIDO: "SUCESSO", Status.CONSULTA_REALIZADA: "SUCESSO",
                 Status.ERRO: "ERRO"}.get(result.status, "AVISO")
        if result.status != Status.CANCELADO:  # o cancelamento já foi registrado em _cancelled()
            self._log(level, name, "Resultado", result.message, result.status)
        return result

    def _err(self, svc: ServiceConfig, status: Status, message: str, details: Optional[str]) -> ServiceResult:
        return ServiceResult(svc.id, svc.nome, svc.tipo, status, message, error_details=details,
                             simulated=svc.modo == IntegrationMode.SIMULADA)

