"""Componentes visuais reutilizáveis (sem regras de negócio)."""
from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path
from typing import Callable, List, Optional

import customtkinter as ctk

from core.log_filters import FILTER_LABELS, format_line, matches
from core.models import LogEvent, ServiceConfig, Status

STATUS_COLORS = {
    Status.AGUARDANDO: "#8b93a1",
    Status.EM_ANDAMENTO: "#3b82f6",
    Status.CONCLUIDO: "#f59e0b",           # finalizado sem documento (ex.: não emitida)
    Status.DOCUMENTO_EMITIDO: "#22c55e",
    Status.CONSULTA_REALIZADA: "#22c55e",
    Status.ERRO: "#ef4444",
    Status.CANCELADO: "#8b93a1",
    Status.PENDENTE_ACAO_MANUAL: "#f97316",
    Status.PENDENTE_CONFIGURACAO: "#eab308",
}
LEVEL_COLORS = {"INFO": "#cbd5e1", "SUCESSO": "#4ade80", "AVISO": "#fbbf24", "ERRO": "#f87171"}
MUTED = "#94a3b8"
CARD = "#1b2230"
ROW = "#232b3b"


def open_path(path: Path) -> None:
    """Abre arquivo ou pasta com o programa padrão do sistema."""
    path = Path(path)
    system = platform.system()
    if system == "Windows":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif system == "Darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class ServiceRow(ctk.CTkFrame):
    def __init__(self, master, service: ServiceConfig, on_toggle: Callable[[], None]):
        super().__init__(master, fg_color=ROW, corner_radius=8)
        self.service = service
        self.var = ctk.BooleanVar(value=False)
        self.grid_columnconfigure(1, weight=1)
        self.check = ctk.CTkCheckBox(self, text="", variable=self.var, width=24, command=on_toggle)
        self.check.grid(row=0, column=0, rowspan=3, padx=(10, 2), pady=8)
        self.name = ctk.CTkLabel(self, text=service.nome, anchor="w", font=ctk.CTkFont(size=13, weight="bold"))
        self.name.grid(row=0, column=1, sticky="ew", pady=(6, 0))
        self.name.bind("<Button-1>", lambda _e: self._toggle())
        self.meta = ctk.CTkLabel(self, text=f"{service.orgao} • {service.tipo.value}", anchor="w",
                                 text_color=MUTED, font=ctk.CTkFont(size=11))
        self.meta.grid(row=1, column=1, sticky="ew")
        self.detail = ctk.CTkLabel(self, text="", anchor="w", text_color=MUTED, font=ctk.CTkFont(size=11),
                                   wraplength=300, justify="left")
        self.detail.grid(row=2, column=1, sticky="ew", pady=(0, 6))
        self.detail.grid_remove()
        self.status = ctk.CTkLabel(self, text="", anchor="e", width=170, font=ctk.CTkFont(size=12, weight="bold"))
        self.status.grid(row=0, column=2, padx=10, sticky="e")
        self.btn_portal = ctk.CTkButton(self, text="Abrir portal ↗", width=95, height=22,
                                        font=ctk.CTkFont(size=11), fg_color="#1e293b", hover_color="#2563eb",
                                        command=self._open_portal)
        self.btn_portal.grid(row=1, column=2, padx=10, sticky="e", pady=(0, 4))
        self.set_status(Status.AGUARDANDO)

    def _open_portal(self) -> None:
        import webbrowser
        try:
            webbrowser.open(self.service.url)
        except Exception:
            pass

    def _toggle(self) -> None:
        if self.check.cget("state") != "disabled":
            self.check.toggle()

    def is_selected(self) -> bool:
        return bool(self.var.get())

    def set_selected(self, value: bool) -> None:
        self.var.set(value)

    def set_enabled(self, enabled: bool) -> None:
        self.check.configure(state="normal" if enabled else "disabled")

    def set_status(self, status: Status, detail: str = "") -> None:
        self.status.configure(text=f"● {status.value}", text_color=STATUS_COLORS[status])
        if detail and status not in (Status.AGUARDANDO, Status.EM_ANDAMENTO):
            self.detail.configure(text=detail[:160])
            self.detail.grid()
        else:
            self.detail.configure(text="")
            self.detail.grid_remove()


class CounterCard(ctk.CTkFrame):
    def __init__(self, master, title: str, color: str):
        super().__init__(master, fg_color=CARD, corner_radius=8)
        self.value = ctk.CTkLabel(self, text="0", font=ctk.CTkFont(size=24, weight="bold"), text_color=color)
        self.value.pack(padx=10, pady=(8, 0))
        ctk.CTkLabel(self, text=title, text_color=MUTED, font=ctk.CTkFont(size=11)).pack(padx=10, pady=(0, 8))

    def set(self, n: int) -> None:
        self.value.configure(text=str(n))


class LogPanel(ctk.CTkFrame):
    MAX_EVENTS = 5000

    def __init__(self, master):
        super().__init__(master, fg_color=CARD, corner_radius=10)
        self._events: List[LogEvent] = []
        self._filter = FILTER_LABELS[0]
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(top, text="Log de execução", font=ctk.CTkFont(size=15, weight="bold")).grid(row=0, column=0, sticky="w")
        self.filter_menu = ctk.CTkOptionMenu(top, values=FILTER_LABELS, width=190, command=self._on_filter)
        self.filter_menu.grid(row=0, column=1, padx=6)
        self.btn_clear = ctk.CTkButton(top, text="Limpar logs da tela", width=140, fg_color="#334155",
                                       hover_color="#475569", command=self.clear_view)
        self.btn_clear.grid(row=0, column=2)
        self.text = ctk.CTkTextbox(self, wrap="word", font=ctk.CTkFont(family="Consolas", size=12),
                                   fg_color="#111723", state="disabled")
        self.text.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        for level, color in LEVEL_COLORS.items():
            self.text.tag_config(level, foreground=color)
        self._placeholder()

    def _placeholder(self) -> None:
        self._write("Os eventos de navegação, emissão, download e erros aparecerão aqui durante o processamento.\n", "INFO")

    def _write(self, line: str, tag: str) -> None:
        self.text.configure(state="normal")
        self.text.insert("end", line, tag)
        self.text.configure(state="disabled")
        self.text.see("end")

    def add(self, event: LogEvent) -> None:
        if len(self._events) >= self.MAX_EVENTS:
            del self._events[: self.MAX_EVENTS // 10]
        self._events.append(event)
        if matches(event, self._filter):
            if len(self._events) == 1:
                self._rerender()
            else:
                self._write(format_line(event) + "\n", event.level)

    def _rerender(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
        shown = [e for e in self._events if matches(e, self._filter)]
        if not shown and not self._events:
            self._placeholder()
        for e in shown:
            self._write(format_line(e) + "\n", e.level)

    def _on_filter(self, label: str) -> None:
        self._filter = label
        self._rerender()

    def clear_view(self) -> None:
        """Limpa só a tela; o histórico permanente (SQLite e arquivo de log) não é afetado."""
        self._events.clear()
        self._rerender()

    @property
    def event_count(self) -> int:
        return len(self._events)


class DocumentsPanel(ctk.CTkFrame):
    """Lista os arquivos gerados na execução atual; botão abre o arquivo."""

    def __init__(self, master):
        super().__init__(master, fg_color=CARD, corner_radius=10)
        ctk.CTkLabel(self, text="Documentos gerados", font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="w", padx=12, pady=(10, 0))
        self.list = ctk.CTkScrollableFrame(self, fg_color="#111723", height=110)
        self.list.pack(fill="both", expand=True, padx=10, pady=10)
        self.empty = ctk.CTkLabel(self.list, text="Nenhum arquivo gerado ainda.", text_color=MUTED)
        self.empty.pack(anchor="w", padx=6, pady=4)
        self.count = 0

    def clear(self) -> None:
        for w in self.list.winfo_children():
            w.destroy()
        self.count = 0
        self.empty = ctk.CTkLabel(self.list, text="Nenhum arquivo gerado ainda.", text_color=MUTED)
        self.empty.pack(anchor="w", padx=6, pady=4)

    def add(self, service_name: str, status: Status, path: str, error_cb: Callable[[str], None]) -> None:
        if self.count == 0:
            self.empty.destroy()
        self.count += 1
        p = Path(path)
        row = ctk.CTkFrame(self.list, fg_color="transparent")
        row.pack(fill="x", pady=2)
        row.grid_columnconfigure(0, weight=1)
        kind = "simulado" if "SIMULADO" in p.name.upper() or "simulada" in p.name else "registro"
        ctk.CTkLabel(row, text=f"{service_name} — {p.name}  [{kind}]", anchor="w").grid(row=0, column=0, sticky="ew")

        def _open() -> None:
            try:
                open_path(p)
            except Exception as exc:  # sem programa associado, arquivo removido etc.
                error_cb(f"Não foi possível abrir {p.name}: {exc}")

        ctk.CTkButton(row, text="Abrir", width=60, height=24, command=_open).grid(row=0, column=1, padx=4)
