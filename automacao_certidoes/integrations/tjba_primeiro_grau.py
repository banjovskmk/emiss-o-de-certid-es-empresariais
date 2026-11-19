"""Integração real: TJBA — Certidão Judicial de 1º Grau."""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from core.download_manager import unique_path, validate_file
from core.errors import IntegrationError, ManualActionRequired, TransientError
from core.models import ServiceResult, Status
from core.validators import clean_cnpj
from integrations.base import BaseIntegration, RunContext
from integrations.browser_utils import check_for_captcha, create_browser_context, pause_for_captcha, save_page_as_pdf


def _obter_dados_empresa(cnpj: str, extra: dict) -> tuple[str, str]:
    """Obtém Razão Social e Endereço a partir de extra ou via consulta automática pública."""
    razao = str(extra.get("razao_social", "")).strip()
    endereco = str(extra.get("endereco", "")).strip()
    if razao and endereco:
        return razao, endereco

    try:
        clean = clean_cnpj(cnpj)
        url = f"https://brasilapi.com.br/api/cnpj/v1/{clean}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                if not razao:
                    razao = (data.get("razao_social") or data.get("nome_fantasia") or "").strip()
                if not endereco:
                    logr = data.get("logradouro", "").strip()
                    num = str(data.get("numero", "")).strip()
                    bairro = data.get("bairro", "").strip()
                    municipio = data.get("municipio", "").strip()
                    uf = data.get("uf", "").strip()
                    end_parts = []
                    if logr:
                        end_parts.append(f"{logr}, {num}" if num else logr)
                    if bairro:
                        end_parts.append(bairro)
                    if municipio or uf:
                        end_parts.append(f"{municipio}/{uf}".strip("/"))
                    endereco = " - ".join(end_parts)
    except Exception:
        pass

    return razao, endereco


class TJBAPrimeiroGrauIntegration(BaseIntegration):
    simulated = False

    def execute(self, ctx: RunContext) -> ServiceResult:
        ctx.info("Acesso", "Acessando o Portal de Certidões do TJBA (1º Grau).")
        ctx.check_cancel()

        # Obter ou buscar dados complementares da empresa
        razao, endereco = _obter_dados_empresa(ctx.cnpj, ctx.extra)

        with sync_playwright() as p:
            browser, context = create_browser_context(p)
            try:
                page = context.new_page()
                page.set_default_timeout(25000)

                try:
                    page.goto("https://portalcertidoes.tjba.jus.br/#/primeirograu", wait_until="domcontentloaded")
                    page.locator("#radioJuridica, input[value='J'], label:has-text('Jurídica')").first.wait_for(
                        state="visible", timeout=20000
                    )
                except Exception as exc:
                    raise TransientError(f"Não foi possível abrir o formulário do TJBA 1º Grau: {exc}")

                ctx.check_cancel()

                # Etapa 1: Selecionar Pessoa Jurídica e Modelo
                ctx.info("Formulário", "Selecionando Pessoa Jurídica e Modelo da certidão.")
                radio_jur = page.locator("#radioJuridica, input[value='J'], label:has-text('Jurídica')").first
                radio_jur.click()
                page.wait_for_timeout(500)

                modelo = page.locator("#selectModelo")
                if modelo.count():
                    try:
                        modelo.select_option("4")
                    except Exception:
                        pass

                page.wait_for_timeout(500)
                btn_avancar1 = page.locator(
                    "input[type='submit'][value*='Avançar'], input[value*='Avançar'], button:has-text('Avançar')"
                ).first
                btn_avancar1.click()
                page.wait_for_timeout(1500)
                ctx.check_cancel()

                # Etapa 2: Preenchimento de dados
                ctx.info("Preenchimento", "Preenchendo formulário cadastral do TJBA.")
                inp_cnpj = page.locator("#cnpj").or_(page.locator("input[name*='cnpj' i]")).first
                inp_cnpj.wait_for(state="visible", timeout=15000)
                inp_cnpj.fill(clean_cnpj(ctx.cnpj))

                inp_razao = page.locator("#razaoSocial").or_(page.locator("input[name*='razao' i]")).first
                inp_end = page.locator("#endereco").or_(page.locator("input[name*='endereco' i]")).first

                if razao and inp_razao.count():
                    inp_razao.fill(razao)
                if endereco and inp_end.count():
                    inp_end.fill(endereco)

                # Se faltar algum dado complementar, manter a janela aberta para o usuário preencher
                if not razao or not endereco:
                    ctx.warn("Dados", "Razão Social ou Endereço precisam de confirmação no portal.")
                    ctx.request_manual_action(
                        "O portal TJBA 1º Grau requer Razão Social e Endereço Completo. "
                        "Confira ou preencha esses campos diretamente na janela aberta do navegador e depois clique "
                        "em 'Concluir e continuar' para prosseguir com a emissão."
                    )

                # Checagem de captcha
                pause_for_captcha(ctx, page, "TJBA — 1º Grau")

                # Clicar em Avançar na etapa 2
                btn_avancar2 = page.locator(
                    "input[type='submit'][value*='Avançar'], button:has-text('Avançar')"
                ).first
                btn_avancar2.click()

                page.wait_for_timeout(3000)
                pause_for_captcha(ctx, page, "TJBA — 1º Grau")
                ctx.check_cancel()

                # Etapa 3: Obter Certidão
                ctx.info("Emissão", "Aguardando geração do documento pelo TJBA.")
                path = unique_path(ctx.run_dir, f"certidao_tjba_1grau_{clean_cnpj(ctx.cnpj)}.pdf")
                downloaded = False

                btn_imprimir = page.locator(
                    "input[value*='Imprimir'], button:has-text('Imprimir'), a:has-text('Imprimir')"
                ).first

                try:
                    btn_imprimir.wait_for(state="visible", timeout=15000)
                    try:
                        with page.expect_download(timeout=8000) as info:
                            btn_imprimir.click()
                        info.value.save_as(str(path))
                        downloaded = True
                    except Exception:
                        pass
                except PlaywrightTimeoutError:
                    pass

                if not downloaded:
                    page.wait_for_timeout(2000)
                    content = page.content().lower()
                    if not any(t in content for t in ("certidão", "certidao", "primeiro grau", "falência", "falencia", "resultado")):
                        raise IntegrationError("O TJBA não apresentou uma certidão válida após avançar; PDF não gerado.")
                    save_page_as_pdf(page, path)

                ok, reason = validate_file(str(path))
                if not ok:
                    raise IntegrationError(f"PDF do TJBA inválido: {reason}")

                return self.result(
                    Status.DOCUMENTO_EMITIDO,
                    "Certidão Judicial de 1º Grau do TJBA emitida e salva com sucesso.",
                    document_path=str(path),
                    official_document=True,
                )

            finally:
                context.close()
                browser.close()
