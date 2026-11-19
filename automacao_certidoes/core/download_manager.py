"""Pastas de execução, nomes sem sobrescrita e validação básica de arquivos."""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from core.validators import clean_cnpj


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower() or "arquivo"


def create_run_dir(root: Path, cnpj: str, now: Optional[datetime] = None) -> Path:
    """documentos/<CNPJ>/<AAAA-MM-DD_HHMMSS>[_n]/ — nunca reutiliza pasta existente."""
    now = now or datetime.now()
    base = Path(root) / clean_cnpj(cnpj) / now.strftime("%Y-%m-%d_%H%M%S")
    candidate, n = base, 2
    while candidate.exists():
        candidate = base.with_name(f"{base.name}_{n}")
        n += 1
    candidate.mkdir(parents=True, exist_ok=False)
    return candidate


def unique_path(directory: Path, filename: str) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / filename
    stem, suffix, n = p.stem, p.suffix, 2
    while p.exists():
        p = directory / f"{stem}_{n}{suffix}"
        n += 1
    return p


def validate_file(path: Optional[str], expected_ext: Optional[str] = None) -> Tuple[bool, str]:
    """Confere existência, conteúdo e formato (PDF). Retorna (ok, motivo)."""
    if not path:
        return False, "nenhum arquivo foi informado"
    p = Path(path)
    if not p.is_file():
        return False, "o arquivo não existe"
    if p.stat().st_size == 0:
        return False, "o arquivo está vazio"
    if (expected_ext or p.suffix).lower() == ".pdf":
        with open(p, "rb") as fh:
            if not fh.read(5).startswith(b"%PDF"):
                return False, "o arquivo não tem formato PDF válido"
    return True, "ok"
