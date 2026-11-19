"""Fábrica de integrações: escolhe a classe conforme o 'modo' do serviço."""
from __future__ import annotations

from typing import Callable, Dict, Optional, Type

from core.config import Settings
from core.models import IntegrationMode, ServiceConfig
from integrations.base import BaseIntegration, PendingIntegration
from integrations.simulated import SimulatedIntegration

from integrations.caixa import CaixaFGTSIntegration
from integrations.cgu import CGUIntegration
from integrations.cndt import CNDTIntegration
from integrations.comprasnet_ba import ComprasNetBaIntegration
from integrations.receita_federal import ReceitaFederalIntegration
from integrations.sefaz_ba import SefazBaIntegration
from integrations.tcu import TCUIntegration
from integrations.tjba_fornecedores import TJBAFornecedoresIntegration
from integrations.tjba_primeiro_grau import TJBAPrimeiroGrauIntegration

# id do serviço -> classe da integração REAL
IMPLEMENTED: Dict[str, Type[BaseIntegration]] = {
    "receita_federal": ReceitaFederalIntegration,
    "cgu": CGUIntegration,
    "cndt": CNDTIntegration,
    "sefaz_ba": SefazBaIntegration,
    "comprasnet_ba": ComprasNetBaIntegration,
    "tjba_fornecedores": TJBAFornecedoresIntegration,
    "caixa_fgts": CaixaFGTSIntegration,
    "tcu_inidoneos": TCUIntegration,
    "tjba_primeiro_grau": TJBAPrimeiroGrauIntegration,
}


def make_factory(settings: Settings, outcomes: Optional[Dict[str, str]] = None) -> Callable[[ServiceConfig], BaseIntegration]:
    cache: Dict[str, BaseIntegration] = {}

    def factory(cfg: ServiceConfig) -> BaseIntegration:
        if cfg.id in cache:  # mantém estado entre tentativas da mesma execução
            return cache[cfg.id]
        if outcomes is not None:
            inst = SimulatedIntegration(cfg, (settings.sim_delay_min, settings.sim_delay_max), outcomes)
        elif cfg.modo == IntegrationMode.IMPLEMENTADA and cfg.id in IMPLEMENTED:
            inst = IMPLEMENTED[cfg.id](cfg)
        elif cfg.modo == IntegrationMode.SIMULADA:
            inst = SimulatedIntegration(cfg, (settings.sim_delay_min, settings.sim_delay_max), outcomes)
        else:
            inst = PendingIntegration(cfg)
        cache[cfg.id] = inst
        return inst

    return factory
