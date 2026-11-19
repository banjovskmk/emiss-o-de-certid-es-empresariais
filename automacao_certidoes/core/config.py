"""Carregamento de config/certidoes.json e config/settings.json."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List

from core.models import ServiceConfig


@dataclass
class Settings:
    base_dir: Path
    docs_dir: Path
    logs_dir: Path
    data_dir: Path
    max_retries: int = 2
    retry_wait_s: float = 3.0
    nav_timeout_s: float = 60.0
    headless: bool = False
    sim_delay_min: float = 0.4
    sim_delay_max: float = 1.2

    @property
    def db_path(self) -> Path:
        return self.data_dir / "historico.sqlite3"


def load_services(path: Path) -> List[ServiceConfig]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = data["servicos"] if isinstance(data, dict) else data
    services = [ServiceConfig.from_dict(i) for i in items]
    ids = [s.id for s in services]
    if len(ids) != len(set(ids)):
        raise ValueError("config/certidoes.json contém ids duplicados.")
    for s in services:
        if not s.url.startswith("https://"):
            raise ValueError(f"Serviço '{s.id}': a URL deve começar com https://")
    return services


def load_settings(path: Path, base_dir: Path) -> Settings:
    raw = json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {}
    dirs, ex, sim = raw.get("diretorios", {}), raw.get("execucao", {}), raw.get("simulacao", {})
    base_dir = Path(base_dir)

    def resolve(name: str, default: str) -> Path:
        p = Path(dirs.get(name, default))
        return p if p.is_absolute() else base_dir / p

    return Settings(
        base_dir=base_dir, docs_dir=resolve("documentos", "documentos"),
        logs_dir=resolve("logs", "logs"), data_dir=resolve("dados", "data"),
        max_retries=int(ex.get("max_tentativas_extras", 2)),
        retry_wait_s=float(ex.get("espera_entre_tentativas_s", 3)),
        nav_timeout_s=float(ex.get("timeout_navegacao_s", 60)),
        headless=bool(raw.get("navegador", {}).get("headless", False)),
        sim_delay_min=float(sim.get("atraso_min_s", 0.4)),
        sim_delay_max=float(sim.get("atraso_max_s", 1.2)),
    )
