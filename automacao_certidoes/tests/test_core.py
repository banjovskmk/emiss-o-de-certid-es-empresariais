import json
import threading
import time
from pathlib import Path

import pytest

from core.config import load_services, load_settings
from core.download_manager import create_run_dir, unique_path, validate_file
from core.errors import IntegrationError, ManualActionRequired, TransientError
from core.log_filters import FILTER_LABELS, matches
from core.models import LogEvent, ManualActionEvent, RunFinishedEvent, ServiceResult, Status, StatusEvent
from core.orchestrator import Orchestrator
from core.summary import build_summary, compute_counters
from core.validators import clean_cnpj, cnpj_error, format_cnpj, is_valid_cnpj
from integrations.base import BaseIntegration
from integrations.registry import make_factory
from services.history_service import HistoryService
from services.logging_service import sanitize

ROOT = Path(__file__).resolve().parents[1]
VALID = "11.222.333/0001-81"

SPEC_URLS = [
    "https://servicos.receitafederal.gov.br/servico/certidoes/#/home/cnpj",
    "https://certidoes.cgu.gov.br/",
    "https://cndt-certidao.tst.jus.br/",
    "https://servicos.sefaz.ba.gov.br/sistemas/DSCRE/Modulos/Publico/EmissaoCertidao.aspx",
    "https://www.comprasnet.ba.gov.br/inter/system/Fornecedor/FornecedorComPenalidade.asp",
    "https://www7.tjba.jus.br/pjlcnet/relatorio/fornecedores_sancionados.wsp",
    "https://consulta-crf.caixa.gov.br/consultacrf/pages/consultaEmpregador.jsf",
    "https://certidoes.apps.tcu.gov.br/emitir-certidao-inidoneos",
    "https://portalcertidoes.tjba.jus.br/#/primeirograu",
]


# ------------------------------------------------------------ CNPJ
def test_cnpj_valid_and_invalid():
    assert is_valid_cnpj(VALID) and is_valid_cnpj("11222333000181")
    assert not is_valid_cnpj("11.222.333/0001-82")
    assert not is_valid_cnpj("00000000000000")
    assert not is_valid_cnpj("123")
    assert is_valid_cnpj("12.ABC.345/01DE-35")  # alfanumérico


def test_cnpj_messages_and_format():
    assert "Informe" in cnpj_error("")
    assert "14" in cnpj_error("123")
    assert "verificadores" in cnpj_error("11222333000182")
    assert cnpj_error(VALID) is None
    assert format_cnpj("11222333000181") == VALID
    assert format_cnpj("1122") == "11.22"
    assert clean_cnpj(VALID) == "11222333000181"


# ------------------------------------------------------------ configuração
def test_nine_services_with_exact_urls():
    services = load_services(ROOT / "config" / "certidoes.json")
    assert [s.url for s in services] == SPEC_URLS


def test_settings_paths():
    st = load_settings(ROOT / "config" / "settings.json", ROOT)
    assert st.docs_dir == ROOT / "documentos"


# ------------------------------------------------------------ arquivos
def test_download_manager(tmp_path):
    d1 = create_run_dir(tmp_path, VALID)
    d2 = create_run_dir(tmp_path, VALID)
    assert d1 != d2 and d1.parent.name == "11222333000181"
    p1 = unique_path(d1, "a.pdf")
    p1.write_bytes(b"%PDF-1.4 x")
    assert unique_path(d1, "a.pdf").name == "a_2.pdf"
    assert validate_file(str(p1))[0]
    assert not validate_file(str(d1 / "nao.pdf"))[0]
    empty = d1 / "v.pdf"
    empty.write_bytes(b"")
    assert "vazio" in validate_file(str(empty))[1]
    bad = d1 / "b.pdf"
    bad.write_bytes(b"<html>")
    assert "PDF" in validate_file(str(bad))[1]


# ------------------------------------------------------------ histórico / logs
def test_history_persistence(tmp_path):
    from datetime import datetime
    h = HistoryService(tmp_path / "h.sqlite3")
    h.create_run("r1", "11222333000181", datetime.now(), ["cgu"], tmp_path)
    h.add_item("r1", ServiceResult("cgu", "CGU", __import__("core.models", fromlist=["x"]).OperationType.CERTIDAO,
                                   Status.ERRO, "falhou", error_details="senha=abc123 boom"))
    h.finish_run("r1", datetime.now(), "finalizada", "resumo")
    runs = h.list_runs()
    assert runs[0]["situacao"] == "finalizada" and runs[0]["resumo"] == "resumo"
    item = h.get_items("r1")[0]
    assert item["status"] == "Erro" and "abc123" not in item["detalhes_erro"]


def test_sanitize():
    assert "xyz" not in sanitize("token: xyz e password=xyz")


def test_log_filters():
    from datetime import datetime
    mk = lambda lv, st: LogEvent(datetime.now(), lv, "X", "s", "m", st)
    assert matches(mk("SUCESSO", None), "Sucessos") and not matches(mk("INFO", None), "Sucessos")
    assert matches(mk("ERRO", Status.ERRO), "Erros")
    assert matches(mk("INFO", Status.EM_ANDAMENTO), "Em andamento")
    assert matches(mk("SUCESSO", Status.CONSULTA_REALIZADA), "Consultas realizadas")
    assert matches(mk("AVISO", Status.PENDENTE_ACAO_MANUAL), "Pendentes")
    assert all(matches(mk("INFO", None), "Todas") for _ in FILTER_LABELS[:1])


def test_summary_text():
    c = {"total": 9, "emitidas": 6, "consultas": 1, "nao_emitidas": 0, "erros": 1, "canceladas": 0, "pendentes": 1}
    assert build_summary(c) == ("Processamento finalizado: 9 itens selecionados, 6 certidões emitidas, "
                                "1 consulta realizada, 1 erro e 1 operação pendente.")


# ------------------------------------------------------------ orquestrador
class Scripted(BaseIntegration):
    """Integração de teste com comportamento roteirizado."""
    behaviors = {}
    started = threading.Event()

    def execute(self, ctx):
        b = self.behaviors.get(self.config.id, "ok")
        ctx.info("Passo", "fazendo algo")
        if b == "ok":
            p = unique_path(ctx.run_dir, f"{self.config.id}.pdf")
            p.write_bytes(b"%PDF-1.4 conteudo")
            return self.result(Status.DOCUMENTO_EMITIDO, "ok", str(p), official_document=True)
        if b == "ghost":  # diz que emitiu, mas sem arquivo
            return self.result(Status.DOCUMENTO_EMITIDO, "ok", str(ctx.run_dir / "fantasma.pdf"))
        if b == "boom":
            raise RuntimeError("explodiu")
        if b == "tech":
            raise IntegrationError("falha técnica", "detalhe")
        if b == "captcha":
            raise ManualActionRequired("captcha")
        if b == "manual_pause":
            ctx.request_manual_action("Resolva o CAPTCHA na janela do navegador.")
        if b == "flaky":
            self.__class__.calls = getattr(self.__class__, "calls", 0) + 1
            if self.__class__.calls == 1:
                raise TransientError("timeout")
            return self.result(Status.CONSULTA_REALIZADA, "ok depois de retry")
        if b == "slow":
            Scripted.started.set()
            ctx.wait(10)
        return self.result(Status.CONSULTA_REALIZADA, "ok")


def build(tmp_path, behaviors, retries=2):
    Scripted.behaviors = behaviors
    Scripted.started = threading.Event()
    Scripted.calls = 0
    services = load_services(ROOT / "config" / "certidoes.json")
    events = []
    h = HistoryService(tmp_path / "h.sqlite3")
    orch = Orchestrator(services, h, events.append, tmp_path / "docs", lambda cfg: Scripted(cfg),
                        max_retries=retries, retry_wait_s=0)
    return orch, events, h, services


def finished(events):
    return [e for e in events if isinstance(e, RunFinishedEvent)][-1]


def test_failure_does_not_stop_others(tmp_path):
    orch, events, h, services = build(tmp_path, {"cgu": "boom", "cndt": "tech", "sefaz_ba": "captcha",
                                                "caixa_fgts": "ghost"})
    orch.start(VALID, [s.id for s in services])
    orch.wait(20)
    fin = finished(events)
    c = fin.counters
    assert c["total"] == 9 and c["erros"] == 3 and c["pendentes"] == 1
    assert c["emitidas"] + c["consultas"] == 5  # os demais continuaram
    status = {e.service_id: e.status for e in events if isinstance(e, StatusEvent)
              and e.status != Status.EM_ANDAMENTO}
    assert status["caixa_fgts"] == Status.ERRO  # sem arquivo => não conta como emitida
    assert status["sefaz_ba"] == Status.PENDENTE_ACAO_MANUAL
    run = h.list_runs()[0]
    assert len(h.get_items(run["id"])) == 9 and run["situacao"] == "finalizada"
    assert (Path(run["pasta"]) / "resumo_execucao.txt").exists()
    assert not orch.is_running



def test_manual_action_event_resumes_same_run(tmp_path):
    orch, events, h, _ = build(tmp_path, {"cndt": "manual_pause"})
    orch.start(VALID, ["cndt"])
    deadline = time.monotonic() + 5
    request = None
    while time.monotonic() < deadline:
        request = next((e for e in events if isinstance(e, ManualActionEvent)), None)
        if request is not None:
            break
        time.sleep(0.02)
    assert request is not None
    request.resume_event.set()
    orch.wait(10)
    assert finished(events).counters["consultas"] == 1
    status = [e.status for e in events if isinstance(e, StatusEvent) and e.service_id == "cndt"]
    assert Status.CONSULTA_REALIZADA in status


def test_retry_transient(tmp_path):
    orch, events, h, _ = build(tmp_path, {"cndt": "flaky"})
    orch.start(VALID, ["cndt"])
    orch.wait(20)
    assert finished(events).counters["consultas"] == 1
    assert any(isinstance(e, LogEvent) and "Tentativa 1" in e.message for e in events)


def test_retry_exhausted(tmp_path):
    class Always(Scripted):
        def execute(self, ctx):
            raise TransientError("timeout")
    services = load_services(ROOT / "config" / "certidoes.json")
    events = []
    orch = Orchestrator(services, HistoryService(tmp_path / "h.db"), events.append, tmp_path, lambda c: Always(c),
                        max_retries=1, retry_wait_s=0)
    orch.start(VALID, ["cndt"])
    orch.wait(20)
    assert finished(events).counters["erros"] == 1


def test_cancel_is_controlled(tmp_path):
    orch, events, h, services = build(tmp_path, {"cgu": "slow"})
    orch.start(VALID, ["receita_federal", "cgu", "cndt", "sefaz_ba"])
    assert Scripted.started.wait(10)
    orch.cancel()
    orch.wait(10)
    fin = finished(events)
    assert fin.cancelled and fin.counters["emitidas"] == 1 and fin.counters["canceladas"] == 3
    assert "Processamento cancelado" in fin.summary


def test_validation_and_single_run(tmp_path):
    orch, events, h, services = build(tmp_path, {"cgu": "slow"})
    with pytest.raises(ValueError):
        orch.start("11.222.333/0001-82", ["cgu"])
    with pytest.raises(ValueError):
        orch.start(VALID, [])
    orch.start(VALID, ["cgu"])
    assert Scripted.started.wait(10)
    with pytest.raises(RuntimeError):
        orch.start(VALID, ["cndt"])
    orch.cancel()
    orch.wait(10)
    orch.start(VALID, ["cndt"])  # liberado após o fim
    orch.wait(10)


def test_counters_function():
    from core.models import OperationType
    mk = lambda st: ServiceResult("a", "A", OperationType.CERTIDAO, st, "")
    c = compute_counters(5, [mk(Status.DOCUMENTO_EMITIDO), mk(Status.ERRO), mk(Status.CANCELADO),
                             mk(Status.PENDENTE_CONFIGURACAO), mk(Status.CONCLUIDO)])
    assert (c["emitidas"], c["erros"], c["canceladas"], c["pendentes"], c["nao_emitidas"]) == (1, 1, 1, 1, 1)


# ------------------------------------------------------------ integrações simuladas
def test_simulated_full_run_labels_everything(tmp_path):
    st = load_settings(ROOT / "config" / "settings.json", tmp_path)
    st.sim_delay_min = st.sim_delay_max = 0
    from dataclasses import replace
    from core.models import IntegrationMode
    services = [replace(s, modo=IntegrationMode.SIMULADA) for s in load_services(ROOT / "config" / "certidoes.json")]
    forced = {"cgu": "erro", "cndt": "pendencias", "tcu_inidoneos": "manual", "comprasnet_ba": "ocorrencias"}
    events = []
    orch = Orchestrator(services, HistoryService(tmp_path / "h.db"), events.append, tmp_path / "docs",
                        make_factory(st, forced), max_retries=1, retry_wait_s=0)
    orch.start(VALID, [s.id for s in services])
    orch.wait(30)
    fin = finished(events)
    c = fin.counters
    assert sum(c[k] for k in ("emitidas", "consultas", "nao_emitidas", "erros", "canceladas", "pendentes")) == 9
    assert c["erros"] >= 1 and c["nao_emitidas"] >= 1 and c["pendentes"] >= 1
    assert fin.summary.startswith("[SIMULAÇÃO]")
    files = list((tmp_path / "docs").rglob("*SIMULADO*.txt")) + list((tmp_path / "docs").rglob("*simulada*.txt"))
    assert files and all("NÃO É UM DOCUMENTO OFICIAL" in f.read_text(encoding="utf-8") for f in files)
