"""Integração real: TJBA — Fornecedores sancionados."""
from __future__ import annotations
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from core.download_manager import unique_path, validate_file
from core.errors import IntegrationError, TransientError
from core.models import Status
from core.validators import clean_cnpj
from integrations.base import BaseIntegration, RunContext
from integrations.browser_utils import create_browser_context, pause_for_captcha, save_page_as_pdf

class TJBAFornecedoresIntegration(BaseIntegration):
    simulated = False
    def execute(self, ctx: RunContext):
        ctx.info("Acesso", "Acessando o portal de fornecedores sancionados do TJBA."); ctx.check_cancel()
        with sync_playwright() as p:
            browser, context = create_browser_context(p)
            try:
                page = context.new_page(); page.set_default_timeout(25000)
                try:
                    page.goto("https://www7.tjba.jus.br/pjlcnet/relatorio/fornecedores_sancionados.wsp", wait_until="domcontentloaded")
                    campo = page.locator('[id="tmp.cnpj_cpf"]')
                    campo.wait_for(state="visible", timeout=20000)
                except Exception as exc:
                    raise TransientError(f"Não foi possível abrir a consulta de fornecedores sancionados do TJBA: {exc}")
                ctx.info("Preenchimento", "Preenchendo CNPJ e período de consulta.")
                campo.fill(clean_cnpj(ctx.cnpj))
                if clean_cnpj(campo.input_value()) != clean_cnpj(ctx.cnpj):
                    raise IntegrationError("O campo do TJBA alterou o CNPJ; consulta interrompida.")
                # Usar os campos de data quando editáveis; caso sejam calendários, selecionar datas pelo widget.
                hoje = datetime.now()
                inicio = hoje.replace(year=hoje.year - 5).strftime("%d/%m/%Y")
                fim = hoje.strftime("%d/%m/%Y")
                inicial = page.locator('[name="tmp.dt_inicial"]')
                final = page.locator('[name="tmp.dt_final"]')
                try:
                    if inicial.count() and inicial.first.is_visible(): inicial.first.fill(inicio)
                    if final.count() and final.first.is_visible(): final.first.fill(fim)
                except Exception:
                    # Alguns ambientes controlam essas datas por calendário; não clicar em datas fixas de anos passados.
                    pass
                ctx.check_cancel()
                pause_for_captcha(ctx, page, "TJBA — Fornecedores sancionados")
                # O formulário legado usa a função de pesquisa identificada na página do portal.
                try:
                    page.evaluate("if (typeof pesquisar === 'function') pesquisar(document.forms[0]); else document.forms[0].requestSubmit();")
                except Exception as exc:
                    raise IntegrationError(f"Não foi possível executar a pesquisa do TJBA: {exc}")
                try: page.wait_for_load_state("domcontentloaded", timeout=8000)
                except Exception: pass
                page.wait_for_timeout(1000)
                ctx.check_cancel()
                pause_for_captcha(ctx, page, "TJBA — Fornecedores sancionados")
                body = page.locator("body").inner_text().strip()
                if not body:
                    raise IntegrationError("O TJBA retornou uma página vazia; nenhum relatório foi salvo.")
                path = unique_path(ctx.run_dir, f"consulta_tjba_sancionados_{clean_cnpj(ctx.cnpj)}.pdf")
                # Se o portal abre uma janela de impressão, aproveitar a página mais recente.
                pages = [pg for pg in context.pages if not pg.is_closed()]
                target = pages[-1] if pages else page
                save_page_as_pdf(target, path)
                ok, reason = validate_file(str(path))
                if not ok: raise IntegrationError(f"PDF da consulta TJBA inválido: {reason}")
                low = body.lower()
                if "nenhum registro encontrado" in low or "não constam" in low:
                    msg = "Consulta realizada: nenhuma ocorrência de sanção encontrada. Isso não comprova regularidade em outros órgãos."
                elif "sanção" in low or "penalidade" in low:
                    msg = "Consulta realizada; o resultado menciona sanções/penalidades. Confira o relatório salvo."
                else:
                    msg = "Consulta do TJBA salva em PDF; confira no relatório se a pesquisa foi concluída e o período aplicado."
                return self.result(Status.CONSULTA_REALIZADA, msg, document_path=str(path), official_document=False)
            finally:
                context.close(); browser.close()
