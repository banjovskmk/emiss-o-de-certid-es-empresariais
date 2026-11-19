"""Filtros do painel de logs (independentes da interface, portanto testáveis)."""
from __future__ import annotations

from core.models import STATUS_PENDENTES, LogEvent, Status

FILTER_LABELS = ["Todas", "Sucessos", "Erros", "Em andamento", "Consultas realizadas", "Pendentes"]


def matches(event: LogEvent, label: str) -> bool:
    if label == "Todas":
        return True
    if label == "Sucessos":
        return event.level == "SUCESSO"
    if label == "Erros":
        return event.level == "ERRO"
    if label == "Em andamento":
        return event.status == Status.EM_ANDAMENTO
    if label == "Consultas realizadas":
        return event.status == Status.CONSULTA_REALIZADA
    if label == "Pendentes":
        return event.status in STATUS_PENDENTES
    raise ValueError(f"Filtro desconhecido: {label}")


def format_line(event: LogEvent) -> str:
    svc = f"[{event.service}] " if event.service else ""
    return f"[{event.timestamp:%H:%M:%S}] [{event.level}] {svc}{event.message}"
