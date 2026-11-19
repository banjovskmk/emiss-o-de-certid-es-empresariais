"""Modelos compartilhados entre interface, orquestrador e integrações."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from threading import Event
from typing import Optional


class Status(str, Enum):
    AGUARDANDO = "Aguardando"
    EM_ANDAMENTO = "Em andamento"
    CONCLUIDO = "Concluído"  # finalizado pelo órgão sem documento (ex.: certidão não emitida por pendências)
    DOCUMENTO_EMITIDO = "Documento emitido"
    CONSULTA_REALIZADA = "Consulta realizada"
    ERRO = "Erro"
    CANCELADO = "Cancelado"
    PENDENTE_ACAO_MANUAL = "Pendente de ação manual"
    PENDENTE_CONFIGURACAO = "Pendente de configuração"


STATUS_PENDENTES = {Status.PENDENTE_ACAO_MANUAL, Status.PENDENTE_CONFIGURACAO}


class OperationType(str, Enum):
    CERTIDAO = "Emissão de certidão"
    CONSULTA = "Consulta"


class IntegrationMode(str, Enum):
    IMPLEMENTADA = "implementada"
    SIMULADA = "simulada"
    PENDENTE = "pendente"


@dataclass(frozen=True)
class ServiceConfig:
    id: str
    nome: str
    nome_curto: str
    orgao: str
    tipo: OperationType
    url: str
    modo: IntegrationMode
    escopo: str = ""
    observacoes: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "ServiceConfig":
        tipo = {"certidao": OperationType.CERTIDAO, "consulta": OperationType.CONSULTA}.get(data.get("tipo", ""))
        if tipo is None:
            raise ValueError(f"Serviço '{data.get('id')}': tipo inválido ({data.get('tipo')!r}).")
        try:
            modo = IntegrationMode(data.get("modo", "pendente"))
        except ValueError:
            raise ValueError(f"Serviço '{data.get('id')}': modo inválido ({data.get('modo')!r}).") from None
        return cls(id=data["id"], nome=data["nome"], nome_curto=data.get("nome_curto", data["nome"]),
                   orgao=data.get("orgao", ""), tipo=tipo, url=data["url"], modo=modo,
                   escopo=data.get("escopo", ""), observacoes=data.get("observacoes", ""))


@dataclass
class ServiceResult:
    service_id: str
    service_name: str
    operation: OperationType
    status: Status
    message: str
    document_path: Optional[str] = None
    official_document: bool = False  # False = registro interno da aplicação / simulação
    simulated: bool = False
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error_details: Optional[str] = None


# ------------------------------------------------ eventos (thread de trabalho -> interface)

@dataclass
class LogEvent:
    timestamp: datetime
    level: str  # INFO | SUCESSO | AVISO | ERRO
    service: Optional[str]
    step: str
    message: str
    status: Optional[Status] = None


@dataclass
class StatusEvent:
    service_id: str
    status: Status
    message: str = ""
    document_path: Optional[str] = None


@dataclass
class CountersEvent:
    counters: dict = field(default_factory=dict)


@dataclass
class RunStartedEvent:
    run_id: str
    cnpj: str
    run_dir: Path


@dataclass
class RunFinishedEvent:
    run_id: str
    summary: str
    counters: dict
    cancelled: bool


@dataclass
class ManualActionEvent:
    """Solicita uma ação humana na interface sem encerrar a sessão do portal."""
    service_name: str
    message: str
    resume_event: Event = field(default_factory=Event)
