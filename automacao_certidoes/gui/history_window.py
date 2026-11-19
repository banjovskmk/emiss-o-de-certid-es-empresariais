"""Janela de consulta ao histórico (SQLite)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from tkinter import ttk

import customtkinter as ctk

from core.validators import format_cnpj
from gui.components import open_path
from services.history_service import HistoryService


def _fmt(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d/%m/%Y %H:%M:%S")
    except (TypeError, ValueError):
        return iso or "—"


class HistoryWindow(ctk.CTkToplevel):
    def __init__(self, master, history: HistoryService):
        super().__init__(master)
        self.title("Histórico de execuções")
        self.geometry("980x600")
        self.history = history
        self._folders = {}
        style = ttk.Style(self)
        style.configure("Hist.Treeview", background="#1b2230", foreground="#e2e8f0", fieldbackground="#1b2230",
                        rowheight=26, borderwidth=0)
        style.configure("Hist.Treeview.Heading", background="#334155", foreground="#e2e8f0")
        style.map("Hist.Treeview", background=[("selected", "#2563eb")])
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        cols = ("inicio", "cnpj", "situacao", "resumo")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", style="Hist.Treeview")
        for c, t, w in (("inicio", "Início", 150), ("cnpj", "CNPJ", 150), ("situacao", "Situação", 100), ("resumo", "Resumo", 560)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 4))
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.details = ctk.CTkTextbox(self, wrap="word", font=ctk.CTkFont(family="Consolas", size=12))
        self.details.grid(row=1, column=0, sticky="nsew", padx=10, pady=4)
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", padx=10, pady=(4, 10))
        ctk.CTkButton(bar, text="Atualizar", width=100, command=self.refresh).pack(side="left")
        ctk.CTkButton(bar, text="Abrir pasta da execução", width=170, command=self._open_folder).pack(side="left", padx=8)
        self.refresh()
        self.after(150, self.lift)

    def refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self._folders.clear()
        for r in self.history.list_runs():
            self.tree.insert("", "end", iid=r["id"], values=(_fmt(r["iniciado_em"]), format_cnpj(r["cnpj"]),
                                                               r["situacao"], r["resumo"] or "—"))
            self._folders[r["id"]] = r["pasta"]

    def _on_select(self, _e=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        lines = []
        for it in self.history.get_items(sel[0]):
            tag = " (SIMULADO)" if it["simulado"] else ""
            lines.append(f"{it['servico_nome']}{tag} — {it['tipo_operacao']} — {it['status']}")
            lines.append(f"    {it['mensagem']}")
            if it["caminho_documento"]:
                lines.append(f"    Arquivo: {it['caminho_documento']}")
            if it["detalhes_erro"]:
                lines.append(f"    Detalhes técnicos: {it['detalhes_erro'].strip().splitlines()[-1]}")
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("end", "\n".join(lines) or "Sem itens registrados.")
        self.details.configure(state="disabled")

    def _open_folder(self) -> None:
        sel = self.tree.selection()
        folder = self._folders.get(sel[0]) if sel else None
        if folder and Path(folder).exists():
            open_path(Path(folder))
