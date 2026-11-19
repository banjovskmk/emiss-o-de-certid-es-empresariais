"""Teste da interface com serviços simulados. Requer tkinter e um display (use xvfb-run no Linux)."""
import os
import sys
import time
from pathlib import Path

import pytest

ctk = pytest.importorskip("customtkinter")
if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
    pytest.skip("sem DISPLAY (rode com: xvfb-run -a pytest)", allow_module_level=True)

from core.config import load_services, load_settings
from core.models import Status
from integrations.registry import make_factory
from gui.main_window import MainWindow

ROOT = Path(__file__).resolve().parents[1]
VALID = "11222333000181"


def make_app(tmp_path, delay=0.0, forced=None):
    st = load_settings(ROOT / "config" / "settings.json", tmp_path)
    st.sim_delay_min = st.sim_delay_max = delay
    st.retry_wait_s = 0
    services = load_services(ROOT / "config" / "certidoes.json")
    if forced is not None or delay > 0:
        from dataclasses import replace
        from core.models import IntegrationMode
        services = [replace(s, modo=IntegrationMode.SIMULADA) for s in services]
    try:
        app = MainWindow(st, services, factory=make_factory(st, forced))
    except Exception as exc:  # TclError etc.
        pytest.skip(f"interface indisponível neste ambiente: {exc}")
    return app


def pump(app, cond, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        app.update()
        if cond():
            return True
        time.sleep(0.01)
    return False


def type_cnpj(app, text):
    app.cnpj_entry.delete(0, "end")
    app.cnpj_entry.insert(0, text)
    app._on_cnpj_key()


def test_cnpj_field_formats_and_validates(tmp_path):
    app = make_app(tmp_path)
    try:
        type_cnpj(app, VALID)
        assert app.cnpj_entry.get() == "11.222.333/0001-81"
        assert "válido" in app.cnpj_msg.cget("text")
        type_cnpj(app, "11222333000182")
        assert "verificadores" in app.cnpj_msg.cget("text")
        app._clear_cnpj()
        assert app.cnpj_entry.get() == ""
    finally:
        app.destroy()


def test_selection_buttons_and_count(tmp_path):
    app = make_app(tmp_path)
    try:
        assert len(app.rows) == 9 and app.selected_ids() == []
        app._select_all(True)
        assert len(app.selected_ids()) == 9 and "9 de 9" in app.selected_label.cget("text")
        app.rows["cgu"].set_selected(False)
        app._update_selected_count()
        assert "8 de 9" in app.selected_label.cget("text")
        app._select_all(False)
        assert app.selected_ids() == []
    finally:
        app.destroy()


def test_full_run_with_simulated_failure(tmp_path):
    forced = {"cgu": "erro", "cndt": "pendencias", "tcu_inidoneos": "manual"}
    app = make_app(tmp_path, forced=forced)
    try:
        type_cnpj(app, VALID)
        app._select_all(True)
        app._on_start()
        assert pump(app, lambda: app.summary_label.cget("text") != "")
        assert app.rows["cgu"].status.cget("text") == "● Erro"
        assert app.rows["tcu_inidoneos"].status.cget("text") == "● Pendente de ação manual"
        assert app.rows["receita_federal"].status.cget("text") in ("● Documento emitido", "● Erro",
                                                                    "● Pendente de ação manual", "● Concluído")
        total = sum(int(app.counter_cards[k].value.cget("text")) for k in
                    ("emitidas", "consultas", "nao_emitidas", "erros", "canceladas", "pendentes"))
        assert total == 9  # a falha de uma certidão não impediu as demais
        assert app.log_panel.event_count > 20
        assert app.docs_panel.count >= 1
        assert str(app.btn_start.cget("state")) == "normal"
        assert "SIMULAÇÃO" in app.summary_label.cget("text")
    finally:
        app.destroy()


def test_cancel_keeps_ui_responsive(tmp_path):
    app = make_app(tmp_path, delay=0.25)
    try:
        type_cnpj(app, VALID)
        app._select_all(True)
        app._on_start()
        assert pump(app, lambda: app.log_panel.event_count > 5)
        ticks = {"n": 0}

        def tick():
            ticks["n"] += 1
            app.after(20, tick)
        app.after(20, tick)
        app._on_cancel()
        assert pump(app, lambda: app.summary_label.cget("text") != "", timeout=20)
        assert "cancelado" in app.summary_label.cget("text")
        assert int(app.counter_cards["canceladas"].value.cget("text")) >= 1
        assert ticks["n"] > 0  # o laço de eventos continuou rodando durante o cancelamento
    finally:
        app.destroy()


def test_log_filter_and_clear_view_keeps_history(tmp_path):
    app = make_app(tmp_path, forced={"cgu": "erro"})
    try:
        type_cnpj(app, VALID)
        app.rows["cgu"].set_selected(True)
        app._on_start()
        assert pump(app, lambda: app.summary_label.cget("text") != "")
        app.log_panel._on_filter("Erros")
        shown = app.log_panel.text.get("1.0", "end")
        assert "[ERRO]" in shown and "[INFO]" not in shown
        app.log_panel.clear_view()
        assert app.log_panel.event_count == 0
        assert len(app.history.list_runs()) == 1  # histórico permanente intacto
    finally:
        app.destroy()
