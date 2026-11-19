"""Janela principal. Contém só apresentação; toda a lógica fica em core/ e integrations/."""
from __future__ import annotations

import queue
from pathlib import Path
from tkinter import messagebox
from typing import Callable, Dict, List, Optional

import customtkinter as ctk

from core.config import Settings
from core.models import (CountersEvent, IntegrationMode, LogEvent, ManualActionEvent, RunFinishedEvent,
                         RunStartedEvent, ServiceConfig, Status, StatusEvent)
from core.orchestrator import Orchestrator
from core.validators import clean_cnpj, cnpj_error, format_cnpj
from gui.components import (CARD, MUTED, CounterCard, DocumentsPanel, LogPanel, ServiceRow, open_path)
from gui.history_window import HistoryWindow
from integrations.registry import make_factory
from services.history_service import HistoryService


class MainWindow(ctk.CTk):
    POLL_MS = 100

    def __init__(self, settings: Settings, services: List[ServiceConfig],
                 history: Optional[HistoryService] = None, factory: Optional[Callable] = None):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.title("Automação de Certidões Empresariais")
        self.geometry("1280x780")
        self.minsize(1100, 680)
        self.settings = settings
        self.services = services
        self.history = history or HistoryService(settings.db_path)
        self.events: "queue.Queue[object]" = queue.Queue()
        self.orch = Orchestrator(services, self.history, self.events.put, settings.docs_dir,
                                 factory or make_factory(settings),
                                 max_retries=settings.max_retries, retry_wait_s=settings.retry_wait_s)
        self.last_run_dir: Optional[Path] = None
        self._manual_dialog: Optional[ctk.CTkToplevel] = None
        self.rows: Dict[str, ServiceRow] = {}
        self._build()
        self._update_selected_count()
        self.after(self.POLL_MS, self._poll)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ----------------------------------------------------------------- layout
    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, columnspan=2, sticky="ew", padx=16, pady=(12, 4))
        ctk.CTkLabel(head, text="Automação de Certidões Empresariais", font=ctk.CTkFont(size=22, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(head, text="Consulta e emissão de documentos para pagamentos", text_color=MUTED).pack(anchor="w")
        if any(s.modo == IntegrationMode.SIMULADA for s in self.services):
            self.banner = ctk.CTkLabel(head, text="⚠ MODO DE TESTE: serviços marcados como SIMULADO não acessam nenhum portal "
                                                  "e geram apenas registros fictícios.", text_color="#fbbf24")
            self.banner.pack(anchor="w", pady=(4, 0))

        left = ctk.CTkFrame(self, fg_color="transparent", width=600)
        left.grid(row=1, column=0, sticky="nsw", padx=(16, 8), pady=8)
        left.grid_rowconfigure(1, weight=1)
        right = ctk.CTkFrame(self, fg_color="transparent")
        right.grid(row=1, column=1, sticky="nsew", padx=(8, 16), pady=8)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)

        # --- CNPJ
        box = ctk.CTkFrame(left, fg_color=CARD, corner_radius=10)
        box.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        box.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(box, text="CNPJ da empresa", font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 2))
        self.cnpj_entry = ctk.CTkEntry(box, placeholder_text="00.000.000/0000-00", height=36, font=ctk.CTkFont(size=15))
        self.cnpj_entry.grid(row=1, column=0, sticky="ew", padx=(12, 6), pady=(0, 4))
        self.cnpj_entry.bind("<KeyRelease>", self._on_cnpj_key)
        self.btn_clear_cnpj = ctk.CTkButton(box, text="Limpar", width=80, height=36, fg_color="#334155",
                                            hover_color="#475569", command=self._clear_cnpj)
        self.btn_clear_cnpj.grid(row=1, column=1, padx=(0, 12), pady=(0, 4))
        self.cnpj_msg = ctk.CTkLabel(box, text="", anchor="w", font=ctk.CTkFont(size=12))
        self.cnpj_msg.grid(row=2, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 8))

        # --- serviços
        sbox = ctk.CTkFrame(left, fg_color=CARD, corner_radius=10)
        sbox.grid(row=1, column=0, sticky="nsew")
        sbox.grid_columnconfigure(0, weight=1)
        sbox.grid_rowconfigure(2, weight=1)
        top = ctk.CTkFrame(sbox, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(top, text="Selecione as certidões e consultas", font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=0, sticky="w")
        self.selected_label = ctk.CTkLabel(top, text="", text_color=MUTED)
        self.selected_label.grid(row=0, column=1, sticky="e")
        bar = ctk.CTkFrame(sbox, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=12)
        self.btn_all = ctk.CTkButton(bar, text="Selecionar todas", width=130, command=lambda: self._select_all(True))
        self.btn_all.pack(side="left")
        self.btn_none = ctk.CTkButton(bar, text="Desmarcar todas", width=130, fg_color="#334155", hover_color="#475569",
                                      command=lambda: self._select_all(False))
        self.btn_none.pack(side="left", padx=6)
        scroll = ctk.CTkScrollableFrame(sbox, fg_color="transparent", width=560)
        scroll.grid(row=2, column=0, sticky="nsew", padx=6, pady=6)
        scroll.grid_columnconfigure(0, weight=1)
        for i, s in enumerate(self.services):
            row = ServiceRow(scroll, s, self._update_selected_count)
            row.grid(row=i, column=0, sticky="ew", pady=3, padx=4)
            self.rows[s.id] = row
        act = ctk.CTkFrame(sbox, fg_color="transparent")
        act.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 12))
        act.grid_columnconfigure(0, weight=1)
        self.btn_start = ctk.CTkButton(act, text="▶ Processar itens selecionados", height=40,
                                       font=ctk.CTkFont(size=14, weight="bold"), command=self._on_start)
        self.btn_start.grid(row=0, column=0, sticky="ew")
        self.btn_cancel = ctk.CTkButton(act, text="Cancelar execução", height=40, fg_color="#b91c1c",
                                        hover_color="#991b1b", state="disabled", command=self._on_cancel)
        self.btn_cancel.grid(row=0, column=1, padx=(8, 0))

        # --- contadores
        cards = ctk.CTkFrame(right, fg_color="transparent")
        cards.grid(row=0, column=0, sticky="ew")
        self.counter_cards: Dict[str, CounterCard] = {}
        spec = [("total", "Selecionados", "#e2e8f0"), ("emitidas", "Emitidas", "#22c55e"),
                ("consultas", "Consultas", "#22c55e"), ("nao_emitidas", "Não emitidas", "#f59e0b"),
                ("erros", "Erros", "#ef4444"), ("canceladas", "Canceladas", "#8b93a1"),
                ("pendentes", "Pendentes", "#f97316")]
        for i, (key, title, color) in enumerate(spec):
            cards.grid_columnconfigure(i, weight=1, uniform="c")
            card = CounterCard(cards, title, color)
            card.grid(row=0, column=i, padx=3, sticky="ew")
            self.counter_cards[key] = card

        self.log_panel = LogPanel(right)
        self.log_panel.grid(row=1, column=0, sticky="nsew", pady=8)
        self.docs_panel = DocumentsPanel(right)
        self.docs_panel.grid(row=2, column=0, sticky="ew")

        foot = ctk.CTkFrame(right, fg_color="transparent")
        foot.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        foot.grid_columnconfigure(2, weight=1)
        ctk.CTkButton(foot, text="Abrir pasta de documentos", width=190, command=self._open_docs_folder).grid(row=0, column=0)
        ctk.CTkButton(foot, text="Consultar histórico", width=160, command=self._open_history).grid(row=0, column=1, padx=8)
        self.summary_label = ctk.CTkLabel(right, text="", anchor="w", wraplength=640, justify="left")
        self.summary_label.grid(row=4, column=0, sticky="ew", pady=(6, 0))

    # ----------------------------------------------------------------- CNPJ
    def _on_cnpj_key(self, _e=None) -> None:
        raw = self.cnpj_entry.get()
        formatted = format_cnpj(raw)
        if formatted != raw:
            self.cnpj_entry.delete(0, "end")
            self.cnpj_entry.insert(0, formatted)
        self._refresh_cnpj_msg()

    def _refresh_cnpj_msg(self) -> None:
        c = clean_cnpj(self.cnpj_entry.get())
        if not c:
            self.cnpj_msg.configure(text="")
        elif len(c) < 14:
            self.cnpj_msg.configure(text=f"{len(c)}/14 caracteres", text_color=MUTED)
        else:
            err = cnpj_error(c)
            self.cnpj_msg.configure(text=err or "CNPJ válido ✔", text_color="#ef4444" if err else "#22c55e")

    def _clear_cnpj(self) -> None:
        if self.orch.is_running:
            return
        self.cnpj_entry.delete(0, "end")
        self._refresh_cnpj_msg()
        self._reset_rows()
        self.summary_label.configure(text="")

    # ----------------------------------------------------------------- seleção
    def selected_ids(self) -> List[str]:
        return [sid for sid, r in self.rows.items() if r.is_selected()]

    def _select_all(self, value: bool) -> None:
        for r in self.rows.values():
            r.set_selected(value)
        self._update_selected_count()

    def _update_selected_count(self) -> None:
        n = len(self.selected_ids())
        self.selected_label.configure(text=f"{n} de {len(self.rows)} selecionados")
        self.counter_cards["total"].set(n) if not self.orch.is_running else None

    def _reset_rows(self) -> None:
        for r in self.rows.values():
            r.set_status(Status.AGUARDANDO)

    # ----------------------------------------------------------------- execução
    def _on_start(self) -> None:
        err = cnpj_error(self.cnpj_entry.get())
        if err:
            self._refresh_cnpj_msg()
            messagebox.showwarning("CNPJ inválido", err, parent=self)
            return
        ids = self.selected_ids()
        if not ids:
            messagebox.showwarning("Nada selecionado", "Marque ao menos uma certidão ou consulta.", parent=self)
            return
        self._reset_rows()
        self.docs_panel.clear()
        self.summary_label.configure(text="")
        try:
            self.orch.start(self.cnpj_entry.get(), ids)
        except (ValueError, RuntimeError) as exc:
            messagebox.showwarning("Não foi possível iniciar", str(exc), parent=self)
            return
        self._set_running(True)  # já bloqueia a interface; RunStartedEvent confirma

    def _on_cancel(self) -> None:
        self.orch.cancel()
        self.btn_cancel.configure(state="disabled", text="Cancelando…")
        self.log_panel.add(LogEvent(__import__("datetime").datetime.now(), "AVISO", None, "Cancelamento",
                                    "Cancelamento solicitado: nenhuma nova operação será iniciada.", None))

    def _set_running(self, running: bool) -> None:
        for w in (self.btn_start, self.btn_all, self.btn_none, self.btn_clear_cnpj):
            w.configure(state="disabled" if running else "normal")
        self.cnpj_entry.configure(state="disabled" if running else "normal")
        for r in self.rows.values():
            r.set_enabled(not running)
        self.btn_cancel.configure(state="normal" if running else "disabled", text="Cancelar execução")

    # ----------------------------------------------------------------- eventos (fila -> UI)
    def _poll(self) -> None:
        try:
            for _ in range(300):
                self._handle(self.events.get_nowait())
        except queue.Empty:
            pass
        self.after(self.POLL_MS, self._poll)

    def _show_manual_action(self, ev: ManualActionEvent) -> None:
        """Mostra instruções de CAPTCHA; o usuário pode alternar para a janela do portal."""
        if self._manual_dialog is not None and self._manual_dialog.winfo_exists():
            self._manual_dialog.destroy()

        win = ctk.CTkToplevel(self)
        self._manual_dialog = win
        win.title(f"Ação manual necessária — {ev.service_name}")
        win.geometry("560x270")
        win.resizable(False, False)
        win.transient(self)

        ctk.CTkLabel(win, text="Complete a verificação no portal",
                     font=ctk.CTkFont(size=18, weight="bold")).pack(padx=22, pady=(22, 8))
        instruction = (ev.message or f"O portal {ev.service_name} solicitou uma confirmação humana.").strip()
        instruction += (
            "\n\nA janela do navegador está aberta. Resolva o desafio nela, não feche a página "
            "e depois retorne a esta janela para clicar em ‘Concluir e continuar’."
        )
        ctk.CTkLabel(win, text=instruction, justify="left", wraplength=510).pack(padx=22, pady=8, anchor="w")

        buttons = ctk.CTkFrame(win, fg_color="transparent")
        buttons.pack(fill="x", padx=22, pady=(12, 20))

        def resume() -> None:
            ev.resume_event.set()
            if win.winfo_exists():
                win.destroy()
            self._manual_dialog = None

        def cancel() -> None:
            self.orch.cancel()
            ev.resume_event.set()
            if win.winfo_exists():
                win.destroy()
            self._manual_dialog = None

        ctk.CTkButton(buttons, text="Concluir e continuar", command=resume, height=38).pack(side="left", expand=True, fill="x", padx=(0, 6))
        ctk.CTkButton(buttons, text="Cancelar execução", command=cancel, height=38,
                      fg_color="#7f1d1d", hover_color="#991b1b").pack(side="left", expand=True, fill="x", padx=(6, 0))
        win.protocol("WM_DELETE_WINDOW", cancel)

    def _handle(self, ev: object) -> None:
        if isinstance(ev, ManualActionEvent):
            self._show_manual_action(ev)
        elif isinstance(ev, LogEvent):
            self.log_panel.add(ev)
        elif isinstance(ev, StatusEvent):
            row = self.rows.get(ev.service_id)
            if row:
                row.set_status(ev.status, ev.message)
            if ev.document_path:
                self.docs_panel.add(row.service.nome_curto if row else ev.service_id, ev.status, ev.document_path,
                                    lambda m: messagebox.showwarning("Abrir arquivo", m, parent=self))
        elif isinstance(ev, CountersEvent):
            for k, v in ev.counters.items():
                if k in self.counter_cards:
                    self.counter_cards[k].set(v)
        elif isinstance(ev, RunStartedEvent):
            self.last_run_dir = ev.run_dir
            self._set_running(True)
        elif isinstance(ev, RunFinishedEvent):
            if self._manual_dialog is not None and self._manual_dialog.winfo_exists():
                self._manual_dialog.destroy()
            self._manual_dialog = None
            self._set_running(False)
            self.summary_label.configure(text=ev.summary)

    # ----------------------------------------------------------------- rodapé
    def _open_docs_folder(self) -> None:
        target = self.last_run_dir if self.last_run_dir and self.last_run_dir.exists() else self.settings.docs_dir
        target.mkdir(parents=True, exist_ok=True)
        try:
            open_path(target)
        except Exception as exc:
            messagebox.showwarning("Abrir pasta", f"Não foi possível abrir a pasta:\n{target}\n\n{exc}", parent=self)

    def _open_history(self) -> None:
        HistoryWindow(self, self.history)

    def _on_close(self) -> None:
        if self.orch.is_running:
            if not messagebox.askyesno("Execução em andamento", "Há uma execução em andamento. Cancelar e sair?", parent=self):
                return
            self.orch.cancel()
            self.orch.wait(5)
        self.destroy()
