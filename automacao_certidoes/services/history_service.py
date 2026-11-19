"""Histórico das execuções em SQLite."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from core.models import ServiceResult
from services.logging_service import sanitize

_SCHEMA = """
CREATE TABLE IF NOT EXISTS execucoes (
    id TEXT PRIMARY KEY, cnpj TEXT NOT NULL, iniciado_em TEXT NOT NULL, finalizado_em TEXT,
    situacao TEXT NOT NULL, itens_selecionados TEXT NOT NULL, resumo TEXT, pasta TEXT
);
CREATE TABLE IF NOT EXISTS itens (
    id INTEGER PRIMARY KEY AUTOINCREMENT, execucao_id TEXT NOT NULL REFERENCES execucoes(id),
    servico_id TEXT NOT NULL, servico_nome TEXT NOT NULL, tipo_operacao TEXT NOT NULL,
    status TEXT NOT NULL, mensagem TEXT, caminho_documento TEXT, documento_oficial INTEGER DEFAULT 0,
    simulado INTEGER DEFAULT 0, iniciado_em TEXT, finalizado_em TEXT, detalhes_erro TEXT
);
CREATE INDEX IF NOT EXISTS idx_itens_execucao ON itens(execucao_id);
CREATE INDEX IF NOT EXISTS idx_exec_cnpj ON execucoes(cnpj);
"""


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat(timespec="seconds") if dt else None


class HistoryService:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def create_run(self, run_id: str, cnpj: str, started: datetime, selected_ids: List[str], folder: Path) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute("INSERT INTO execucoes (id,cnpj,iniciado_em,situacao,itens_selecionados,pasta) VALUES (?,?,?,?,?,?)",
                         (run_id, cnpj, _iso(started), "em andamento", json.dumps(selected_ids), str(folder)))

    def add_item(self, run_id: str, r: ServiceResult) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO itens (execucao_id,servico_id,servico_nome,tipo_operacao,status,mensagem,caminho_documento,"
                "documento_oficial,simulado,iniciado_em,finalizado_em,detalhes_erro) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, r.service_id, r.service_name, r.operation.value, r.status.value, r.message, r.document_path,
                 int(r.official_document), int(r.simulated), _iso(r.started_at), _iso(r.finished_at),
                 sanitize(r.error_details) if r.error_details else None))

    def finish_run(self, run_id: str, finished: datetime, situacao: str, summary: str) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute("UPDATE execucoes SET finalizado_em=?, situacao=?, resumo=? WHERE id=?",
                         (_iso(finished), situacao, summary, run_id))

    def list_runs(self, limit: int = 200, cnpj: Optional[str] = None) -> List[sqlite3.Row]:
        sql, args = "SELECT * FROM execucoes", []
        if cnpj:
            sql += " WHERE cnpj = ?"
            args.append(cnpj)
        sql += " ORDER BY iniciado_em DESC LIMIT ?"
        args.append(limit)
        with closing(self._connect()) as conn:
            return conn.execute(sql, args).fetchall()

    def get_items(self, run_id: str) -> List[sqlite3.Row]:
        with closing(self._connect()) as conn:
            return conn.execute("SELECT * FROM itens WHERE execucao_id=? ORDER BY id", (run_id,)).fetchall()
