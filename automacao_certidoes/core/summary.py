"""Contadores, frase-resumo e relatório interno da execução."""
from __future__ import annotations

from typing import Iterable, List

from core.models import STATUS_PENDENTES, ServiceResult, Status


def compute_counters(total: int, results: Iterable[ServiceResult]) -> dict:
    c = {"total": total, "emitidas": 0, "consultas": 0, "nao_emitidas": 0,
         "erros": 0, "canceladas": 0, "pendentes": 0}
    for r in results:
        if r.status == Status.DOCUMENTO_EMITIDO:
            c["emitidas"] += 1
        elif r.status == Status.CONSULTA_REALIZADA:
            c["consultas"] += 1
        elif r.status == Status.CONCLUIDO:
            c["nao_emitidas"] += 1
        elif r.status == Status.ERRO:
            c["erros"] += 1
        elif r.status == Status.CANCELADO:
            c["canceladas"] += 1
        elif r.status in STATUS_PENDENTES:
            c["pendentes"] += 1
    return c


def _pl(n: int, sing: str, plur: str) -> str:
    return f"{n} {sing if n == 1 else plur}"


def build_summary(c: dict, cancelled: bool = False, simulated: bool = False) -> str:
    parts: List[str] = []
    if c["emitidas"]:
        parts.append(_pl(c["emitidas"], "certidão emitida", "certidões emitidas"))
    if c["consultas"]:
        parts.append(_pl(c["consultas"], "consulta realizada", "consultas realizadas"))
    if c["nao_emitidas"]:
        parts.append(_pl(c["nao_emitidas"], "certidão não emitida pelo órgão", "certidões não emitidas pelo órgão"))
    if c["erros"]:
        parts.append(_pl(c["erros"], "erro", "erros"))
    if c["canceladas"]:
        parts.append(_pl(c["canceladas"], "operação cancelada", "operações canceladas"))
    if c["pendentes"]:
        parts.append(_pl(c["pendentes"], "operação pendente", "operações pendentes"))
    head = "Processamento cancelado" if cancelled else "Processamento finalizado"
    text = f"{head}: {_pl(c['total'], 'item selecionado', 'itens selecionados')}"
    if len(parts) == 1:
        text += ", " + parts[0]
    elif parts:
        text += ", " + ", ".join(parts[:-1]) + " e " + parts[-1]
    text += "."
    return ("[SIMULAÇÃO] " + text) if simulated else text


def render_report(run_id: str, cnpj: str, started, finished, results: List[ServiceResult], summary: str) -> str:
    lines = ["REGISTRO INTERNO DA APLICAÇÃO — não é documento oficial de nenhum órgão.", "",
             f"Execução: {run_id}", f"CNPJ: {cnpj}",
             f"Início: {started:%d/%m/%Y %H:%M:%S}", f"Término: {finished:%d/%m/%Y %H:%M:%S}", "",
             summary, "", "Itens:"]
    for r in results:
        tag = " (SIMULADO)" if r.simulated else ""
        lines.append(f"- {r.service_name}{tag} | {r.operation.value} | {r.status.value}")
        lines.append(f"    {r.message}")
        if r.document_path:
            kind = "documento oficial" if r.official_document else "registro interno/simulado"
            lines.append(f"    Arquivo ({kind}): {r.document_path}")
    return "\n".join(lines) + "\n"
