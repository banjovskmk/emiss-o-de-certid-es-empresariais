"""Integração real: Receita Federal — certidões federais."""
from __future__ import annotations
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from core.download_manager import unique_path, validate_file
from core.errors import IntegrationError, ManualActionRequired, TransientError
from core.models import Status
from core.validators import clean_cnpj, format_cnpj
from integrations.base import BaseIntegration, RunContext
from integrations.browser_utils import check_for_captcha, create_browser_context, pause_for_captcha

class ReceitaFederalIntegration(BaseIntegration):
    simulated = False
    def execute(self, ctx: RunContext):
        ctx.info("Acesso", "Acessando o portal de certidões da Receita Federal.")
        ctx.check_cancel()
        with sync_playwright() as p:
            browser, context = create_browser_context(p)
            try:
                page = context.new_page(); page.set_default_timeout(25000)
                try:
                    page.goto(self.config.url, wait_until="domcontentloaded")
                    page.get_by_role("textbox", name="CNPJ").wait_for(state="visible", timeout=20000)
                except Exception as exc:
                    raise TransientError(f"Não foi possível abrir o formulário da Receita Federal: {exc}")
                ctx.info("Preenchimento", "Preenchendo o CNPJ no formulário da Receita Federal.")
                campo = page.get_by_role("textbox", name="CNPJ").first
                campo.fill(format_cnpj(ctx.cnpj))
                if clean_cnpj(campo.input_value()) != clean_cnpj(ctx.cnpj):
                    raise IntegrationError("O CNPJ preenchido pela página não corresponde ao informado; emissão interrompida para evitar consulta incorreta.")
                ctx.check_cancel()
                # Se houver CAPTCHA, pausa na interface e mantém esta mesma página aberta.
                pause_for_captcha(ctx, page, "Receita Federal")
                btn = page.get_by_role("button", name="Consultar Certidão")
                if awaitable_count(btn) == 0:
                    raise IntegrationError("Botão 'Consultar Certidão' não encontrado.")
                btn.first.click()
                ctx.info("Consulta", "Aguardando o resultado da consulta.")
                try:
                    page.get_by_title("Segunda via").first.wait_for(state="visible", timeout=20000)
                except PlaywrightTimeoutError:
                    if pause_for_captcha(ctx, page, "Receita Federal"):
                        try:
                            page.get_by_title("Segunda via").first.wait_for(state="visible", timeout=20000)
                        except PlaywrightTimeoutError:
                            raise IntegrationError("A verificação manual foi concluída, mas o portal não apresentou a opção 'Segunda via'.")
                    else:
                        raise IntegrationError("A consulta não apresentou a opção 'Segunda via'; nenhum PDF foi declarado como emitido.")
                ctx.check_cancel()
                path = unique_path(ctx.run_dir, f"certidao_receita_federal_{clean_cnpj(ctx.cnpj)}.pdf")
                try:
                    with page.expect_download(timeout=20000) as info:
                        page.get_by_title("Segunda via").first.click()
                    info.value.save_as(str(path))
                except PlaywrightTimeoutError:
                    raise IntegrationError("A opção 'Segunda via' não iniciou download. Verifique o comportamento atual do portal.")
                ok, reason = validate_file(str(path))
                if not ok: raise IntegrationError(f"PDF da Receita Federal inválido: {reason}")
                return self.result(Status.DOCUMENTO_EMITIDO, "Certidão da Receita Federal baixada do portal oficial.", document_path=str(path), official_document=True)
            finally:
                context.close(); browser.close()

def awaitable_count(locator):
    # Mantém a checagem síncrona explícita para a API sync do Playwright.
    return locator.count()
