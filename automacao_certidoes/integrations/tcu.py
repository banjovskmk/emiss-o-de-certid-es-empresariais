"""Integração real: TCU — Certidão de Inidôneos."""
from __future__ import annotations
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from core.download_manager import unique_path, validate_file
from core.errors import IntegrationError, TransientError
from core.models import Status
from core.validators import clean_cnpj, format_cnpj
from integrations.base import BaseIntegration, RunContext
from integrations.browser_utils import create_browser_context, pause_for_captcha

class TCUIntegration(BaseIntegration):
    simulated = False
    def execute(self, ctx: RunContext):
        ctx.info("Acesso", "Acessando o portal de certidões do TCU.")
        ctx.check_cancel()
        with sync_playwright() as p:
            browser, context = create_browser_context(p)
            try:
                page = context.new_page(); page.set_default_timeout(25000)
                try:
                    page.goto("https://certidoes.apps.tcu.gov.br/emitir-certidao-inidoneos", wait_until="domcontentloaded")
                    campo = page.get_by_role("textbox", name="CNPJ do responsável")
                    campo.wait_for(state="visible", timeout=20000)
                except Exception as exc:
                    raise TransientError(f"Falha ao abrir formulário do TCU: {exc}")
                ctx.info("Preenchimento", "Inserindo o CNPJ do responsável.")
                campo.fill(format_cnpj(ctx.cnpj))
                if clean_cnpj(campo.input_value()) != clean_cnpj(ctx.cnpj):
                    raise IntegrationError("O campo do TCU alterou o CNPJ preenchido; emissão interrompida.")
                ctx.check_cancel()
                pause_for_captcha(ctx, page, "TCU")
                page.get_by_role("button", name="Emitir certidão", exact=True).click()
                baixar = page.get_by_role("button", name="Baixar certidão em PDF", exact=True)
                try:
                    baixar.wait_for(state="visible", timeout=20000)
                except PlaywrightTimeoutError:
                    if pause_for_captcha(ctx, page, "TCU"):
                        try:
                            baixar.wait_for(state="visible", timeout=20000)
                        except PlaywrightTimeoutError:
                            raise IntegrationError("A verificação manual foi concluída, mas o TCU não exibiu o botão de download.")
                    else:
                        texto = page.locator("body").inner_text().lower()
                        if "inidôneo" in texto and ("consta" in texto or "registro" in texto):
                            return self.result(Status.CONCLUIDO, "O TCU retornou informação de registro de inidoneidade; não foi baixada certidão negativa.")
                        raise IntegrationError("O TCU não exibiu 'Baixar certidão em PDF' após a emissão.")
                ctx.info("Download", "Baixando o PDF oficial emitido pelo TCU.")
                path = unique_path(ctx.run_dir, f"certidao_tcu_{clean_cnpj(ctx.cnpj)}.pdf")
                with page.expect_download(timeout=20000) as info:
                    baixar.click()
                info.value.save_as(str(path))
                ok, reason = validate_file(str(path))
                if not ok: raise IntegrationError(f"PDF do TCU inválido: {reason}")
                return self.result(Status.DOCUMENTO_EMITIDO, "Certidão do TCU baixada do portal oficial.", document_path=str(path), official_document=True)
            finally:
                context.close(); browser.close()
