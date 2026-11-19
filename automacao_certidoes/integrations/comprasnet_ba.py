"""Integração real: ComprasNet Bahia — consulta de fornecedores com penalidades."""
from __future__ import annotations
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from core.download_manager import unique_path, validate_file
from core.errors import IntegrationError, ManualActionRequired, TransientError
from core.models import Status
from core.validators import clean_cnpj, format_cnpj
from integrations.base import BaseIntegration, RunContext
from integrations.browser_utils import create_browser_context, pause_for_captcha, save_page_as_pdf

class ComprasNetBaIntegration(BaseIntegration):
    simulated = False
    def execute(self, ctx: RunContext):
        ctx.info("Acesso", "Acessando o portal ComprasNet Bahia."); ctx.check_cancel()
        with sync_playwright() as p:
            browser, context = create_browser_context(p)
            try:
                page = context.new_page(); page.set_default_timeout(25000)
                try:
                    page.goto(self.config.url, wait_until="domcontentloaded")
                    frame = page.frame_locator('iframe[name="iframe-system"]')
                    frame.get_by_role("radio", name="Jurídica").wait_for(state="visible", timeout=20000)
                except Exception as exc:
                    raise TransientError(f"Não foi possível localizar o formulário do ComprasNet BA: {exc}")
                frame.get_by_role("radio", name="Jurídica").check()
                campo = frame.locator("#txtCnpj")
                campo.fill(clean_cnpj(ctx.cnpj))
                if clean_cnpj(campo.input_value()) != clean_cnpj(ctx.cnpj):
                    raise IntegrationError("O ComprasNet alterou o CNPJ preenchido; consulta interrompida.")
                ctx.info("Consulta", "Pesquisando o CNPJ no ComprasNet Bahia.")
                pause_for_captcha(ctx, page, "ComprasNet Bahia")
                frame.get_by_role("link", name="Pesquisar").click()
                try:
                    frame.get_by_role("link", name="Imprimir").wait_for(state="visible", timeout=15000)
                except PlaywrightTimeoutError:
                    if pause_for_captcha(ctx, page, "ComprasNet Bahia"):
                        frame.get_by_role("link", name="Pesquisar").click()
                        frame.get_by_role("link", name="Imprimir").wait_for(state="visible", timeout=15000)
                    else:
                        raise IntegrationError("O ComprasNet não exibiu a opção 'Imprimir' após a pesquisa.")
                ctx.check_cancel()
                popup = None
                try:
                    with page.expect_popup(timeout=10000) as pop:
                        frame.get_by_role("link", name="Imprimir").click()
                    popup = pop.value
                    popup.wait_for_load_state("domcontentloaded", timeout=10000)
                except PlaywrightTimeoutError:
                    # Alguns estados do portal imprimem na mesma aba; não declarar documento oficial sem resultado.
                    popup = page
                target = unique_path(ctx.run_dir, f"consulta_comprasnet_ba_{clean_cnpj(ctx.cnpj)}.pdf")
                if not popup.locator("body").inner_text().strip():
                    raise IntegrationError("A página de impressão do ComprasNet ficou vazia.")
                save_page_as_pdf(popup, target)
                ok, reason = validate_file(str(target))
                if not ok: raise IntegrationError(f"PDF de consulta do ComprasNet inválido: {reason}")
                return self.result(Status.CONSULTA_REALIZADA, "Consulta do ComprasNet concluída; relatório salvo em PDF. A consulta não equivale a certidão de regularidade.", document_path=str(target), official_document=False)
            finally:
                context.close(); browser.close()
