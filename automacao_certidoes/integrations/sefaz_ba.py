"""Integração real: SEFAZ Bahia — Débitos Tributários Estaduais."""
from __future__ import annotations

from pathlib import Path
from playwright.sync_api import sync_playwright

from core.download_manager import unique_path, validate_file
from core.errors import IntegrationError, TransientError
from core.models import ServiceResult, Status
from core.validators import clean_cnpj, format_cnpj
from integrations.base import BaseIntegration, RunContext
from integrations.browser_utils import create_browser_context, pause_for_captcha, save_page_as_pdf


class SefazBaIntegration(BaseIntegration):
    """Emissão ou consulta de certidão de débitos estaduais na SEFAZ Bahia."""
    simulated = False

    def execute(self, ctx: RunContext) -> ServiceResult:
        ctx.info("Acesso", "Acessando o portal da SEFAZ Bahia.")
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
                    raise TransientError(f"Falha de conexão com a SEFAZ-BA: {exc}")

                ctx.check_cancel()
                pause_for_captcha(ctx, page, "SEFAZ-BA")
                ctx.info("Preenchimento", "Preenchendo CNPJ e dados cadastrais.")

                # Se houver inscrição estadual informada nos dados complementares, preencher
                ie = ctx.extra.get("inscricao_estadual") or ctx.extra.get("ie")
                if ie:
                    page.locator("#PHConteudo_TxtNumInscricaoEstadual").fill(str(ie))

                cnpj_fmt = format_cnpj(ctx.cnpj)
                page.locator("#PHConteudo_TxtNumCNPJ").fill(cnpj_fmt)
                ctx.wait(0.5)

                ctx.info("Envio", "Solicitando certidão à SEFAZ-BA.")
                pause_for_captcha(ctx, page, "SEFAZ-BA")
                page.locator("#PHConteudo_btnImprimir").click()
                page.wait_for_timeout(4000)

                ctx.check_cancel()
                pause_for_captcha(ctx, page, "SEFAZ-BA")
                body_text = page.inner_text("body")

                # Verificar se apareceu modal de erro / pendência
                modal = page.locator("#ASModal_Erro").or_(page.locator(".modal-body")).or_(page.locator("#ASModal_Erro_lblMensagem"))
                if modal.is_visible():
                    modal_text = modal.inner_text().strip()
                    if "pendência" in modal_text.lower() or "débito" in modal_text.lower():
                        return self.result(
                            Status.CONCLUIDO,
                            f"Certidão NÃO emitida pela SEFAZ-BA por pendências da empresa: {modal_text}",
                        )
                    if "inscrição estadual" in modal_text.lower():
                        return self.result(
                            Status.CONCLUIDO,
                            "SEFAZ-BA requer Inscrição Estadual para este CNPJ. Informe nos dados complementares.",
                        )
                    return self.result(
                        Status.CONCLUIDO,
                        f"SEFAZ-BA retornou: {modal_text}",
                    )

                # Verificar se uma nova aba ou tela com a certidão foi aberta
                pages = context.pages
                cert_page = pages[-1] if len(pages) > 1 else page
                cert_page.wait_for_load_state("networkidle", timeout=5000)

                pdf_path = unique_path(ctx.run_dir, f"certidao_sefaz_ba_{clean_cnpj(ctx.cnpj)}.pdf")
                save_page_as_pdf(cert_page, pdf_path)
                ok, _ = validate_file(str(pdf_path))

                if ok:
                    return self.result(
                        Status.DOCUMENTO_EMITIDO,
                        "Certidão de Débitos Tributários da SEFAZ-BA obtida com sucesso em PDF oficial.",
                        document_path=str(pdf_path),
                        official_document=True,
                    )

                return self.result(
                    Status.CONCLUIDO,
                    "Processamento na SEFAZ-BA concluído sem geração de documento oficial.",
                )

            finally:
                context.close()
                browser.close()
