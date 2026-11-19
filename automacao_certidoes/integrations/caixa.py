"""Integração real: Caixa — Certificado de Regularidade do FGTS (CRF) e Histórico do Empregador."""
from __future__ import annotations

from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from core.download_manager import unique_path, validate_file
from core.errors import IntegrationError, ManualActionRequired, TransientError
from core.models import ServiceResult, Status
from core.validators import clean_cnpj
from integrations.base import BaseIntegration, RunContext
from integrations.browser_utils import check_for_captcha, create_browser_context, pause_for_captcha, save_page_as_pdf


class CaixaFGTSIntegration(BaseIntegration):
    simulated = False

    def execute(self, ctx: RunContext) -> ServiceResult:
        ctx.info("Acesso", "Acessando o portal do FGTS da Caixa.")
        ctx.check_cancel()

        with sync_playwright() as p:
            browser, context = create_browser_context(p)
            try:
                page = context.new_page()
                page.set_default_timeout(25000)

                try:
                    page.goto(
                        "https://consulta-crf.caixa.gov.br/consultacrf/pages/consultaEmpregador.jsf",
                        wait_until="domcontentloaded",
                    )
                    campo = page.locator('[id="mainForm:txtInscricao1"]')
                    campo.wait_for(state="visible", timeout=20000)
                except Exception as exc:
                    raise TransientError(f"Não foi possível abrir a consulta do FGTS: {exc}")

                # Só pausa se houver captcha real na página
                if check_for_captcha(page):
                    pause_for_captcha(ctx, page, "Caixa / CRF-FGTS")

                ctx.info("Preenchimento", "Preenchendo CNPJ do empregador.")
                cnpj_limpo = clean_cnpj(ctx.cnpj)
                campo.fill(cnpj_limpo)
                if clean_cnpj(campo.input_value()) != cnpj_limpo:
                    raise IntegrationError("O campo da Caixa alterou o CNPJ informado; consulta interrompida.")

                ctx.info("Consulta", "Enviando consulta do FGTS.")
                btn = page.locator('[id="mainForm:btnConsultar"]').or_(
                    page.locator("input[type='submit'][value*='Consultar']")
                ).or_(page.get_by_role("button", name="Consultar")).first

                if btn.count() and btn.is_visible():
                    btn.click()
                else:
                    campo.press("Enter")

                page.wait_for_load_state("networkidle", timeout=10000)
                ctx.check_cancel()

                # Checagem de captcha após submissão
                if check_for_captcha(page):
                    pause_for_captcha(ctx, page, "Caixa / CRF-FGTS")

                # Localizar links na tela de resultado
                link_crf = page.locator("a:has-text('Certificado de Regularidade')").first
                try:
                    link_crf.wait_for(state="visible", timeout=15000)
                except PlaywrightTimeoutError:
                    if check_for_captcha(page) and pause_for_captcha(ctx, page, "Caixa / CRF-FGTS"):
                        link_crf.wait_for(state="visible", timeout=15000)
                    else:
                        body = page.locator("body").first.inner_text().lower()
                        if "não cadastrado" in body or "não localizado" in body:
                            return self.result(Status.CONCLUIDO, "CRF não emitido: empregador não cadastrado ou não localizado.")
                        if "não se encontra em situação regular" in body or "irregular" in body:
                            return self.result(Status.CONCLUIDO, "CRF não emitido: o portal indica situação irregular perante o FGTS.")
                        raise IntegrationError("A Caixa não apresentou os links de certidão/resultado do FGTS.")

                # ==============================================================
                # 1ª Certidão: Certificado de Regularidade do FGTS (CRF)
                # ==============================================================
                ctx.info("CRF", "Emitindo a 1ª certidão: Certificado de Regularidade do FGTS (CRF).")
                link_crf.click()
                page.wait_for_load_state("networkidle", timeout=8000)

                visualizar = page.locator("input[value*='Visualizar'], button:has-text('Visualizar')").first
                if visualizar.count() and visualizar.is_visible():
                    visualizar.click()
                    page.wait_for_load_state("networkidle", timeout=8000)

                try:
                    page.get_by_text("Imprimir", exact=True).wait_for(state="visible", timeout=8000)
                except PlaywrightTimeoutError:
                    pass

                body_crf = page.locator("body").first.inner_text().lower()
                if "certificado de regularidade" not in body_crf and "regularidade do empregador" not in body_crf:
                    raise IntegrationError("A visualização da Caixa não contém o texto esperado do CRF; PDF não gerado.")

                crf_path = unique_path(ctx.run_dir, f"crf_fgts_caixa_{cnpj_limpo}.pdf")
                save_page_as_pdf(page, crf_path)
                ok, reason = validate_file(str(crf_path))
                if not ok:
                    raise IntegrationError(f"PDF do CRF inválido: {reason}")
                ctx.info("CRF", f"1ª certidão salva com sucesso: {crf_path.name}")

                # ==============================================================
                # 2ª Certidão: Histórico do Empregador
                # ==============================================================
                ctx.info("Histórico", "Emitindo a 2ª certidão: Histórico do Empregador FGTS.")
                voltou = False
                for _ in range(3):
                    btn_voltar = page.locator("input[value*='Voltar'], button:has-text('Voltar'), a:has-text('Voltar')").first
                    if btn_voltar.count() and btn_voltar.is_visible():
                        btn_voltar.click()
                        page.wait_for_load_state("networkidle", timeout=6000)
                    link_hist = page.locator("a:has-text('Histórico do Empregador')").first
                    if link_hist.count() and link_hist.is_visible():
                        voltou = True
                        break

                if not voltou:
                    page.goto("https://consulta-crf.caixa.gov.br/consultacrf/pages/consultaEmpregador.jsf", wait_until="domcontentloaded")
                    c = page.locator('[id="mainForm:txtInscricao1"]')
                    c.wait_for(state="visible", timeout=10000)
                    c.fill(cnpj_limpo)
                    btn = page.locator('[id="mainForm:btnConsultar"]').or_(page.locator("input[value*='Consultar']")).first
                    btn.click()
                    page.wait_for_load_state("networkidle", timeout=8000)

                link_hist = page.locator("a:has-text('Histórico do Empregador')").first
                link_hist.wait_for(state="visible", timeout=12000)
                link_hist.click()
                page.wait_for_load_state("networkidle", timeout=8000)
                page.wait_for_timeout(1000)

                hist_path = unique_path(ctx.run_dir, f"historico_fgts_caixa_{cnpj_limpo}.pdf")
                save_page_as_pdf(page, hist_path)
                ok_h, reason_h = validate_file(str(hist_path))
                if not ok_h:
                    ctx.warn("Histórico", f"Não foi possível validar o PDF do Histórico: {reason_h}")
                else:
                    ctx.info("Histórico", f"2ª certidão salva com sucesso: {hist_path.name}")

                msg = f"As 2 certidões do FGTS foram emitidas com sucesso: CRF ({crf_path.name}) e Histórico ({hist_path.name})."
                return self.result(
                    Status.DOCUMENTO_EMITIDO,
                    msg,
                    document_path=str(crf_path),
                    official_document=True,
                )

            finally:
                context.close()
                browser.close()
