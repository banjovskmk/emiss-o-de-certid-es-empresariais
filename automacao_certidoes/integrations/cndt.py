"""Integração real: Tribunal Superior do Trabalho — CNDT."""
from __future__ import annotations
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from core.download_manager import unique_path, validate_file
from core.errors import IntegrationError, ManualActionRequired, TransientError
from core.models import Status
from core.validators import clean_cnpj
from integrations.base import BaseIntegration, RunContext
from integrations.browser_utils import check_for_captcha, create_browser_context, pause_for_captcha

class CNDTIntegration(BaseIntegration):
    simulated = False
    def execute(self, ctx: RunContext):
        ctx.info("Acesso", "Acessando o portal da CNDT (TST).")
        ctx.check_cancel()
        with sync_playwright() as p:
            browser, context = create_browser_context(p)
            try:
                page = context.new_page(); page.set_default_timeout(25000)
                try:
                    page.goto("https://cndt-certidao.tst.jus.br/", wait_until="domcontentloaded")
                    page.get_by_role("link", name="Emitir Certidão").wait_for(state="visible", timeout=20000)
                    page.get_by_role("link", name="Emitir Certidão").click()
                except Exception as exc:
                    raise TransientError(f"Não foi possível abrir a emissão da CNDT: {exc}")
                campo = page.get_by_role("textbox", name="* Informe o número do CNPJ /")
                campo.wait_for(state="visible", timeout=15000)
                campo.fill(clean_cnpj(ctx.cnpj))
                if clean_cnpj(campo.input_value()) != clean_cnpj(ctx.cnpj):
                    raise IntegrationError("O campo da CNDT alterou o CNPJ preenchido; emissão interrompida.")
                # O campo foi identificado no Inspector como “* Digite os caracteres”.
                # A pessoa preenche o desafio no navegador visível; a automação retoma nesta sessão.
                captcha = page.get_by_role("textbox", name="* Digite os caracteres")
                if captcha.count() and captcha.first.is_visible():
                    ctx.request_manual_action(
                        "O portal do TST solicitou o CAPTCHA exibido ao lado do campo “Digite os caracteres”. "
                        "Preencha-o na janela do navegador e clique em “Concluir e continuar” nesta interface."
                    )
                    if not captcha.first.input_value().strip():
                        raise ManualActionRequired("O campo do CAPTCHA da CNDT continua vazio. A emissão não foi enviada.")
                else:
                    pause_for_captcha(ctx, page, "TST / CNDT")
                btn = page.get_by_role("button", name="Emitir Certidão")
                path = unique_path(ctx.run_dir, f"cndt_tst_{clean_cnpj(ctx.cnpj)}.pdf")
                try:
                    with page.expect_download(timeout=20000) as info:
                        btn.click()
                    info.value.save_as(str(path))
                except PlaywrightTimeoutError:
                    body = page.locator("body").inner_text().lower()
                    if "constam" in body and "débito" in body:
                        return self.result(Status.CONCLUIDO, "CNDT não emitida: o portal informa débitos trabalhistas.")
                    if pause_for_captcha(ctx, page, "TST / CNDT"):
                        try:
                            with page.expect_download(timeout=20000) as info:
                                btn.click()
                            info.value.save_as(str(path))
                        except PlaywrightTimeoutError:
                            raise IntegrationError("O CAPTCHA foi tratado, mas a CNDT não iniciou o download.")
                    else:
                        raise IntegrationError("A emissão não iniciou download; nenhum PDF foi marcado como emitido.")
                ok, reason = validate_file(str(path))
                if not ok: raise IntegrationError(f"PDF da CNDT inválido: {reason}")
                return self.result(Status.DOCUMENTO_EMITIDO, "CNDT baixada do portal oficial do TST.", document_path=str(path), official_document=True)
            finally:
                context.close(); browser.close()
