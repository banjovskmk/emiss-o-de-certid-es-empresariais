"""Integração SIMULADA: não acessa nenhum portal. Serve para validar interface, logs e fluxo."""
from __future__ import annotations

import hashlib
import random
from typing import Dict, Optional, Tuple

from core.download_manager import slugify, unique_path, validate_file
from core.errors import IntegrationError, ManualActionRequired, TransientError
from core.models import OperationType, ServiceResult, Status
from integrations.base import BaseIntegration, RunContext

OUTCOMES = ("sucesso", "ocorrencias", "pendencias", "manual", "transitorio", "erro")


def pick_outcome(cnpj: str, service_id: str, operation: OperationType) -> str:
    """Resultado determinístico (mesmo CNPJ + serviço = mesmo resultado)."""
    h = int(hashlib.sha256(f"{cnpj}:{service_id}".encode()).hexdigest(), 16) % 100
    if operation == OperationType.CERTIDAO:
        bands = [(62, "sucesso"), (72, "pendencias"), (82, "manual"), (90, "transitorio")]
    else:
        bands = [(55, "sucesso"), (70, "ocorrencias"), (82, "manual"), (90, "transitorio")]
    for limit, name in bands:
        if h < limit:
            return name
    return "erro"


class SimulatedIntegration(BaseIntegration):
    simulated = True
    TAG = "(SIMULADO)"

    def __init__(self, config, delay: Tuple[float, float] = (0.4, 1.2), outcomes: Optional[Dict[str, str]] = None):
        super().__init__(config)
        self.delay = delay
        self.forced = outcomes or {}
        self._attempts: Dict[str, int] = {}

    def _pause(self, ctx: RunContext) -> None:
        ctx.wait(random.uniform(*self.delay) if self.delay[1] > 0 else 0)

    def execute(self, ctx: RunContext) -> ServiceResult:
        cfg = self.config
        outcome = self.forced.get(cfg.id) or pick_outcome(ctx.cnpj, cfg.id, cfg.tipo)
        if outcome not in OUTCOMES:
            raise ValueError(f"Resultado simulado desconhecido: {outcome}")
        key = str(ctx.run_dir)  # tentativas contadas por execução
        n = self._attempts[key] = self._attempts.get(key, 0) + 1

        ctx.info("Acesso", f"Acessando o portal oficial {self.TAG}.")
        self._pause(ctx)
        ctx.info("Preenchimento", f"Preenchendo o CNPJ {self.TAG}.")
        self._pause(ctx)
        ctx.info("Envio", f"Enviando a solicitação {self.TAG}.")
        self._pause(ctx)
        ctx.info("Resposta", f"Aguardando a resposta do portal {self.TAG}.")
        self._pause(ctx)

        if outcome == "transitorio" and n == 1:
            raise TransientError("Portal indisponível (timeout simulado).", "SimulatedTimeout")
        if outcome == "manual":
            raise ManualActionRequired("CAPTCHA detectado (simulado). Resolva manualmente para concluir esta operação.")
        if outcome == "erro":
            raise IntegrationError("Falha técnica simulada no portal.", "SimulatedError: layout alterado")
        if outcome == "pendencias":
            return self.result(Status.CONCLUIDO, "Certidão NÃO emitida: o órgão informou pendências da empresa (simulado). "
                                                 "Não se trata de falha técnica.")

        ctx.check_cancel()
        is_cert = cfg.tipo == OperationType.CERTIDAO
        name = f"{slugify(cfg.id)}_{'SIMULADO' if is_cert else 'consulta_simulada'}.txt"
        path = unique_path(ctx.run_dir, name)
        path.write_text(
            "REGISTRO SIMULADO — NÃO É UM DOCUMENTO OFICIAL.\n"
            f"Serviço: {cfg.nome}\nCNPJ: {ctx.cnpj}\nOperação: {cfg.tipo.value}\n"
            "Este arquivo foi gerado pelo modo de simulação da aplicação; nenhum portal foi acessado.\n",
            encoding="utf-8")
        ok, why = validate_file(str(path))
        if not ok:
            raise IntegrationError(f"Arquivo simulado inválido: {why}")
        if is_cert:
            return self.result(Status.DOCUMENTO_EMITIDO, "Documento emitido e validado (simulado).", str(path))
        if outcome == "ocorrencias":
            return self.result(Status.CONSULTA_REALIZADA, "Consulta realizada: OCORRÊNCIAS encontradas (simulado). "
                                                          "Registro interno salvo.", str(path))
        return self.result(Status.CONSULTA_REALIZADA, "Consulta realizada: nenhuma ocorrência encontrada (simulado). "
                                                      "Isto NÃO comprova regularidade da empresa.", str(path))
