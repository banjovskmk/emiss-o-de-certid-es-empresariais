"""
Contexto compartilhado de navegador para emissão de certidões.
Porta o módulo contexto.js do projeto emissão_certidões_claude para Python/Playwright sync.

Usa perfil persistente do Chromium (launchPersistentContext) para manter cookies/sessão
entre execuções, assim como o projeto original em Node.js.
"""
from __future__ import annotations

import os
import re
import threading
import time
from datetime import date
from pathlib import Path
from typing import Callable, List, Optional, TYPE_CHECKING

from playwright.sync_api import BrowserContext, Download, Page, Response, sync_playwright

from core.errors import ManualActionRequired, OperationCancelled
from integrations.base import RunContext

if TYPE_CHECKING:
    pass

# ─────────────────────────────────────────────────────────────────────────────
# Constantes do navegador
# ─────────────────────────────────────────────────────────────────────────────
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-popup-blocking",
    "--start-maximized",
    "--no-sandbox",
]

# Pasta de perfil persistente: mantém cookies/sessão entre execuções
_BASE_DIR = Path(__file__).resolve().parent.parent
PROFILE_DIR = _BASE_DIR / ".perfil-navegador"


# ─────────────────────────────────────────────────────────────────────────────
# Fila de downloads pendentes (equivale ao `ouvirDownloads` do JS)
# ─────────────────────────────────────────────────────────────────────────────
class _DownloadQueue:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: List[Download] = []

    def push(self, dl: Download) -> None:
        with self._lock:
            self._items.append(dl)

    def proximo(self, timeout_ms: float = 20_000) -> Download:
        """Aguarda e retorna o próximo download da fila."""
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            with self._lock:
                if self._items:
                    return self._items.pop(0)
            time.sleep(0.3)
        raise TimeoutError("Nenhum download recebido dentro do prazo.")

    def pendentes(self) -> int:
        with self._lock:
            return len(self._items)


# ─────────────────────────────────────────────────────────────────────────────
# Fila de respostas PDF da rede (equivale ao `ouvirRespostasPdf` do JS)
# ─────────────────────────────────────────────────────────────────────────────
class _PdfQueue:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: List[bytes] = []

    def push(self, data: bytes) -> None:
        with self._lock:
            self._items.append(data)

    def proximo(self, timeout_ms: float = 60_000) -> bytes:
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            with self._lock:
                if self._items:
                    return self._items.pop(0)
            time.sleep(0.4)
        raise TimeoutError("Nenhum PDF de rede recebido dentro do prazo.")

    def pendentes(self) -> int:
        with self._lock:
            return len(self._items)


# ─────────────────────────────────────────────────────────────────────────────
# Contexto de automação (equivale ao objeto `ctx` montado em contexto.js)
# ─────────────────────────────────────────────────────────────────────────────
class AutoCtx:
    """
    Contexto passado a cada integração. Encapsula page, downloads, PDFs e
    helpers para preencher CNPJ, baixar arquivos e aguardar ação humana.
    """

    def __init__(
        self,
        *,
        context: BrowserContext,
        page: Page,
        cnpj: str,
        run_ctx: RunContext,
        downloads: _DownloadQueue,
        pdfs_da_rede: _PdfQueue,
        interativo: bool = True,
        captcha_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.context = context
        self.page = page
        self.cnpj = cnpj                         # só dígitos
        self.cnpj_fmt = _formatar_cnpj(cnpj)
        self.run_ctx = run_ctx
        self.downloads = downloads
        self.pdfs_da_rede = pdfs_da_rede
        self.interativo = interativo
        self._captcha_callback = captcha_callback  # chamado para avisar usuário
        self.salvos: List[str] = []
        self.empresa: dict = {}

    # ── helpers de log ───────────────────────────────────────────────────────
    def passo(self, msg: str) -> None:
        self.run_ctx.info("Passo", msg)

    def aviso(self, msg: str) -> None:
        self.run_ctx.warn("Aviso", msg)

    # ── preenchimento de CNPJ ────────────────────────────────────────────────
    def preencher_cnpj(self, seletor_ou_locator, somente_digitos: bool = True) -> None:
        """Preenche o campo de CNPJ. Aceita seletor CSS (str) ou Locator."""
        valor = self.cnpj if somente_digitos else self.cnpj_fmt
        if isinstance(seletor_ou_locator, str):
            el = self.page.locator(seletor_ou_locator)
        else:
            el = seletor_ou_locator
        el.fill(valor)
        time.sleep(0.1)

    def preencher_cnpj_fmt(self, seletor_ou_locator) -> None:
        self.preencher_cnpj(seletor_ou_locator, somente_digitos=False)

    # ── salvamento de arquivos ───────────────────────────────────────────────
    def _destino(self, rotulo: str, ext: str) -> Path:
        nome = _nome_seguro(rotulo) + ext
        destino = _caminho_livre(self.run_ctx.run_dir / nome)
        return destino

    def _registrar(self, caminho: Path) -> str:
        p = str(caminho)
        self.salvos.append(p)
        self.passo(f"salvo: {caminho.name}")
        return p

    # ── download ─────────────────────────────────────────────────────────────
    def baixar(
        self,
        acao: Callable,
        rotulo: str,
        timeout_ms: float = 60_000,
    ) -> str:
        """Executa ação e aguarda um download, salvando-o com o rótulo dado."""
        with self.page.expect_download(timeout=timeout_ms) as dl_info:
            acao()
        dl = dl_info.value
        destino = self._destino(rotulo, Path(dl.suggested_filename).suffix or ".pdf")
        dl.save_as(str(destino))
        return self._registrar(destino)

    def colher_download(self, rotulo: str, timeout_ms: float = 20_000) -> str:
        """Pega próximo download já enfileirado (sem disparar nova ação)."""
        dl = self.downloads.proximo(timeout_ms)
        destino = self._destino(rotulo, Path(dl.suggested_filename).suffix or ".pdf")
        dl.save_as(str(destino))
        return self._registrar(destino)

    def baixar_ou_capturar(
        self,
        acao: Callable,
        rotulo: str,
        timeout_ms: float = 60_000,
    ) -> str:
        """Tenta download; se falhar, faz PDF da página."""
        try:
            return self.baixar(acao, rotulo, timeout_ms)
        except Exception:
            acao()
            time.sleep(2)
            return self.pdf_da_pagina(self.page, rotulo)

    # ── PDF da página ─────────────────────────────────────────────────────────
    def pdf_da_pagina(
        self,
        alvo: Page,
        rotulo: str,
        ocultar: Optional[List[str]] = None,
    ) -> str:
        """Converte a página visível em PDF A4."""
        destino = self._destino(rotulo, ".pdf")
        if ocultar:
            sels = ",".join(ocultar)
            alvo.evaluate(
                f"document.querySelectorAll('{sels}').forEach(e => e.style.display='none')"
            )
        alvo.emulate_media(media="print")
        alvo.pdf(
            path=str(destino),
            format="A4",
            print_background=True,
            margin={"top": "10mm", "bottom": "10mm", "left": "8mm", "right": "8mm"},
        )
        alvo.emulate_media(media=None)
        return self._registrar(destino)

    # ── captura de PDF da rede ────────────────────────────────────────────────
    def capturar_pdf_da_rede(
        self,
        acao: Callable,
        rotulo: str,
        timeout_ms: float = 60_000,
    ) -> str:
        """Executa ação e aguarda um PDF interceptado na rede (não download)."""
        acao()
        dados = self.pdfs_da_rede.proximo(timeout_ms)
        destino = self._destino(rotulo, ".pdf")
        destino.write_bytes(dados)
        return self._registrar(destino)

    # ── imagem de depuração ───────────────────────────────────────────────────
    def imagem_da_pagina(self, alvo: Page, rotulo: str) -> str:
        destino = self._destino(rotulo, ".png")
        alvo.screenshot(path=str(destino), full_page=True)
        return self._registrar(destino)

    # ── aguardar ação humana ──────────────────────────────────────────────────
    def aguardar_humano(
        self,
        mensagem: str,
        condicao: Optional[Callable[[], bool]] = None,
        timeout_ms: float = 240_000,
    ) -> None:
        """
        Avisa o usuário via callback e aguarda a condição ser satisfeita.
        Lança ManualActionRequired se não for interativo.
        """
        if not self.interativo:
            raise ManualActionRequired(
                f"Ação humana necessária (captcha/login) e execução está em modo automático.\n{mensagem}"
            )
        # notifica a GUI (abre diálogo, janela do browser visível, etc.)
        if self._captcha_callback:
            self._captcha_callback(mensagem)

        if condicao is None:
            # Sem condição: aguarda um tempo fixo para o usuário agir
            time.sleep(timeout_ms / 1000)
            return

        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            self.run_ctx.check_cancel()
            try:
                if condicao():
                    return
            except Exception:
                pass
            time.sleep(1.0)

        raise ManualActionRequired(
            f"Tempo esgotado aguardando ação humana.\n{mensagem}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Gerenciador do contexto de browser persistente
# ─────────────────────────────────────────────────────────────────────────────
class BrowserSession:
    """
    Gerencia um contexto persistente do Chromium (mantém cookies entre runs).
    Equivale ao chromium.launchPersistentContext do projeto JS.
    """

    def __init__(self, headless: bool = False) -> None:
        self.headless = headless
        self._playwright = None
        self._context: Optional[BrowserContext] = None
        self._downloads: Optional[_DownloadQueue] = None
        self._pdfs: Optional[_PdfQueue] = None

    def __enter__(self) -> "BrowserSession":
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=self.headless,
            accept_downloads=True,
            viewport={"width": 1366, "height": 900},
            user_agent=USER_AGENT,
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            ignore_https_errors=True,
            args=BROWSER_ARGS,
        )
        self._downloads = _DownloadQueue()
        self._pdfs = _PdfQueue()

        # Interceptar downloads
        self._context.on("download", lambda dl: self._downloads.push(dl))

        # Interceptar respostas PDF da rede
        def _on_response(resp: Response) -> None:
            ct = resp.headers.get("content-type", "")
            if "application/pdf" in ct:
                try:
                    self._pdfs.push(resp.body())
                except Exception:
                    pass

        self._context.on("response", _on_response)
        return self

    def __exit__(self, *_) -> None:
        try:
            if self._context:
                self._context.close()
        except Exception:
            pass
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass

    def new_auto_ctx(
        self,
        *,
        cnpj: str,
        run_ctx: RunContext,
        interativo: bool = True,
        captcha_callback: Optional[Callable[[str], None]] = None,
    ) -> tuple["AutoCtx", Page]:
        """Abre nova aba e monta um AutoCtx para ela."""
        page = self._context.new_page()
        ctx = AutoCtx(
            context=self._context,
            page=page,
            cnpj=cnpj,
            run_ctx=run_ctx,
            downloads=self._downloads,
            pdfs_da_rede=self._pdfs,
            interativo=interativo,
            captcha_callback=captcha_callback,
        )
        return ctx, page

    def fechar_abas_residuais(self, page_atual: Page) -> None:
        """Fecha abas about:blank residuais abertas pelos portais."""
        for p in self._context.pages:
            if p is not page_atual and p.url.startswith("about:"):
                try:
                    p.close()
                except Exception:
                    pass


# ─────────────────────────────────────────────────────────────────────────────
# Utilitários
# ─────────────────────────────────────────────────────────────────────────────
def _formatar_cnpj(cnpj: str) -> str:
    d = re.sub(r"\D", "", str(cnpj)).zfill(14)
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"


def _nome_seguro(s: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", str(s)).strip()


def _caminho_livre(destino: Path) -> Path:
    if not destino.exists():
        return destino
    stem = destino.stem
    suffix = destino.suffix
    parent = destino.parent
    for i in range(2, 100):
        alt = parent / f"{stem} ({i}){suffix}"
        if not alt.exists():
            return alt
    return parent / f"{stem} ({int(time.time())}){suffix}"


def data_br(d: date) -> str:
    return f"{d.day:02d}/{d.month:02d}/{d.year}"
