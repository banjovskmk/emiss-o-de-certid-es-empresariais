"""Integração real: CGU — Controladoria-Geral da União."""
from __future__ import annotations

from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from core.download_manager import unique_path, validate_file
from core.errors import IntegrationError, ManualActionRequired, TransientError
from core.models import ServiceResult, Status
from core.validators import clean_cnpj, format_cnpj
from integrations.base import BaseIntegration, RunContext
from integrations.browser_utils import check_for_captcha, create_browser_context, pause_for_captcha, save_page_as_pdf


class CGUIntegration(BaseIntegration):
    """Emissão e consulta de certidões no portal oficial da CGU."""
    simulated = False

    def execute(self, ctx: RunContext) -> ServiceResult:
        ctx.info("Acesso", "Acessando o portal de certidões da CGU.")
        ctx.check_cancel()

        with sync_playwright() as p:
            browser, context = create_browser_context(p)
            try:
                page = context.new_page()
                page.set_default_timeout(25000)

                try:
                    page.goto(self.config.url, wait_until="domcontentloaded")
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception as exc:
                    raise TransientError(f"Falha de conexão com a CGU: {exc}")

                ctx.check_cancel()
                body_text = page.locator("body").first.inner_text()

                # Checagem de bloqueio CloudFront / WAF
                if "403 error" in body_text.lower() and "cloudfront" in body_text.lower():
                    ctx.warn("WAF", "Bloqueio CloudFront detectado na CGU.")
                    raise ManualActionRequired(
                        "O portal da CGU bloqueou o acesso automatizado via CloudFront. "
                        "Acesse o portal diretamente pelo botão 'Abrir portal ↗'."
                    )

                # 1. Se estiver na home, clicar para ir para a tela de consulta de certidões
                if "/consulta-certidao" not in page.url:
                    btn_privado = page.locator("button:has-text('Entes Privados'), a:has-text('Entes Privados')").first
                    if btn_privado.count() and btn_privado.is_visible():
                        btn_privado.click()
                    else:
                        page.goto("https://certidoes.cgu.gov.br/consulta-certidao", wait_until="domcontentloaded")
                    page.wait_for_load_state("networkidle", timeout=6000)

                ctx.check_cancel()

                # 2. Selecionar obrigatoriamente a opção "Ente Privado"
                ctx.info("Opção", "Selecionando a opção 'Ente Privado' no formulário da CGU.")
                radio_ente = page.locator("label:has-text('Ente Privado')").or_(
                    page.locator("input[type='radio'][value='0']")
                ).first
                try:
                    radio_ente.wait_for(state="visible", timeout=12000)
                    radio_ente.click()
                except PlaywrightTimeoutError:
                    r_inp = page.locator("input[type='radio'][value='0']").first
                    if r_inp.count():
                        r_inp.check(force=True)
                    else:
                        raise IntegrationError("Opção 'Ente Privado' não encontrada no portal da CGU.")

                page.wait_for_timeout(1000)

                # Garantir que a certidão esteja selecionada (checkbox)
                checkboxes = page.locator("input[type='checkbox']")
                if checkboxes.count() > 0:
                    first_cb = checkboxes.first
                    if not first_cb.is_checked():
                        first_cb.check(force=True)

                # 3. Preencher o CNPJ da empresa
                ctx.info("Preenchimento", "Preenchendo o CNPJ da empresa.")
                cnpj_inp = page.locator("#cpfCnpj").or_(
                    page.locator("input[placeholder*='CNPJ']")
                ).or_(page.locator("input#cnpj")).first
                cnpj_inp.wait_for(state="visible", timeout=10000)
                cnpj_inp.fill(clean_cnpj(ctx.cnpj))
                ctx.wait(0.5)

                # 4. Clicar no botão Consultar
                btn_consultar = page.locator("#consultar").or_(
                    page.locator("button:has-text('Consultar')")
                ).first
                btn_consultar.wait_for(state="visible", timeout=10000)
                btn_consultar.click()

                # 5. Desafio WAF / CAPTCHA (AWS WAF Captcha)
                page.wait_for_timeout(2000)
                pause_for_captcha(ctx, page, "CGU")
                ctx.check_cancel()

                # 6. Aguardar resultado da consulta e botão de emissão
                ctx.info("Emissão", "Aguardando geração da certidão da CGU.")
                pdf_path = unique_path(ctx.run_dir, f"certidao_cgu_{clean_cnpj(ctx.cnpj)}.pdf")

                btn_emissao = page.locator("button[id^='btnEmitirCertidao_']").or_(
                    page.locator("button:has-text('Certidão Negativa')")
                ).or_(
                    page.locator("button:has-text('Certidão')")
                ).or_(
                    page.locator("button:has-text('Relatório')")
                ).first

                downloaded = False
                try:
                    btn_emissao.wait_for(state="visible", timeout=20000)
                    try:
                        with page.expect_download(timeout=15000) as download_info:
                            btn_emissao.click()
                        download = download_info.value
                        download.save_as(str(pdf_path))
                        downloaded = True
                    except Exception:
                        pass
                except PlaywrightTimeoutError:
                    if pause_for_captcha(ctx, page, "CGU"):
                        if btn_emissao.is_visible():
                            try:
                                with page.expect_download(timeout=15000) as download_info:
                                    btn_emissao.click()
                                download_info.value.save_as(str(pdf_path))
                                downloaded = True
                            except Exception:
                                pass

                # Fallback: se não fez download direto do arquivo, salvar a página/resultado em PDF
                if not downloaded:
                    page.wait_for_timeout(2000)
                    body_content = page.content().lower()
                    if not any(term in body_content for term in ("certidão", "certidao", "nada consta", "consulta de certid")):
                        raise IntegrationError("A CGU não apresentou o resultado da certidão após a consulta.")
                    save_page_as_pdf(page, pdf_path)

                validate_file(str(pdf_path))

                return self.result(
                    Status.DOCUMENTO_EMITIDO,
                    "Certidão da CGU (Entes Privados) emitida e salva com sucesso.",
                    document_path=str(pdf_path),
                    official_document=True,
                )

            finally:
                context.close()
                browser.close()
