"""Utilitários compartilhados para automação com Playwright nos portais reais."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Tuple
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from core.errors import ManualActionRequired, OperationCancelled
from integrations.base import RunContext

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)



def create_browser_context(playwright_instance, headless: bool = False) -> Tuple[Browser, BrowserContext]:
    """Cria navegador e contexto configurados com cabeçalhos e configurações em português."""
    browser = playwright_instance.chromium.launch(
        headless=headless,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ],
    )
    context = browser.new_context(
        user_agent=USER_AGENT,
        viewport={"width": 1280, "height": 850},
        locale="pt-BR",
        timezone_id="America/Sao_Paulo",
        ignore_https_errors=True,
        accept_downloads=True,
    )
    return browser, context


def check_for_captcha(page: Page) -> Optional[str]:
    """Detecta desafios visíveis, evitando falso positivo por scripts carregados.

    A mera presença de uma biblioteca JS de CAPTCHA no HTML não significa que
    o usuário precise resolver um desafio; por isso priorizamos widgets visíveis.
    """
    selectors = [
        ('iframe[src*="recaptcha/api2/bframe" i], iframe[title*="challenge" i], .g-recaptcha, [data-sitekey]', "Google reCAPTCHA"),
        ('iframe[src*="hcaptcha" i], .h-captcha', "hCaptcha"),
        ('iframe[src*="challenges.cloudflare.com" i], input[name="cf-turnstile-response"]', "Cloudflare Turnstile"),
        ('iframe[src*="awswaf" i], [id*="amzn-btn" i], [id*="amzn-captcha" i], #amzn-captcha-modal, [class*="amzn-captcha" i]', "AWS WAF Captcha"),
        ('img[src*="captcha" i], img[alt*="captcha" i], input[id*="captcha" i], input[name*="captcha" i], input[placeholder*="captcha" i]', "CAPTCHA de imagem"),
        ('input[aria-label*="caracteres" i], input[placeholder*="caracteres" i]', "CAPTCHA de caracteres"),
    ]
    for selector, label in selectors:
        try:
            locator = page.locator(selector)
            for index in range(min(locator.count(), 4)):
                item = locator.nth(index)
                if item.is_visible():
                    return label
        except Exception:
            continue

    # Alguns portais carregam o widget dentro de iframes (inclusive iframes aninhados).
    try:
        for frame in page.frames:
            frame_url = (frame.url or "").lower()
            frame_visible = False
            try:
                frame_visible = frame.frame_element().is_visible()
            except Exception:
                pass
            if frame_visible and "recaptcha" in frame_url and ("bframe" in frame_url or "challenge" in frame_url):
                return "Google reCAPTCHA"
            if frame_visible and "hcaptcha" in frame_url:
                return "hCaptcha"
            if frame_visible and "challenges.cloudflare.com" in frame_url:
                return "Cloudflare Turnstile"
            if frame_visible and ("awswaf" in frame_url or "captcha-sdk.awswaf.com" in frame_url):
                return "AWS WAF Captcha"
            for selector, label in selectors:
                try:
                    nested = frame.locator(selector)
                    if nested.count() and nested.first.is_visible():
                        return label
                except Exception:
                    continue
            try:
                nested_field = frame.get_by_role(
                    "textbox", name=re.compile(r"digite os caracteres|código de verificação|texto da imagem", re.I)
                )
                if nested_field.count() and nested_field.first.is_visible():
                    return "CAPTCHA de caracteres"
            except Exception:
                pass
    except Exception:
        pass

    # Alguns portais rotulam o campo da imagem como “Digite os caracteres”.
    try:
        field = page.get_by_role("textbox", name=re.compile(r"digite os caracteres|código de verificação|texto da imagem", re.I))
        if field.count() and field.first.is_visible():
            return "CAPTCHA de caracteres"
    except Exception:
        pass

    # Proteções que redirecionam a página inteira para um desafio.
    try:
        url = page.url.lower()
        title = page.title().lower()
        body = page.locator("body").inner_text(timeout=1500).lower()
        if "challenges.cloudflare.com" in url or ("just a moment" in title and "checking your browser" in body):
            return "Desafio Cloudflare"
        if "captcha" in title and any(word in body for word in ("não sou um robô", "nao sou um robo", "verifique que você é humano", "verifique que voce e humano")):
            return "Desafio antirobô"
    except Exception:
        pass
    return None


def pause_for_captcha(ctx: RunContext, page: Page, portal: str, *, detected: Optional[str] = None) -> bool:
    """Pausa na interface para resolução humana e mantém o navegador aberto."""
    challenge = detected or check_for_captcha(page)
    if not challenge:
        return False
    ctx.warn("CAPTCHA", f"{challenge} detectado em {portal}; aguardando resolução manual.")
    ctx.request_manual_action(
        f"O portal {portal} apresentou {challenge}. Resolva o desafio na janela do navegador "
        "que foi aberta pela automação. Não feche a página. Depois clique em “Concluir e continuar” "
        "nesta interface para prosseguir com a emissão."
    )
    return True


def save_page_as_pdf(page: Page, target_path: Path) -> Path:
    """Exporta a visualização atual da página como um PDF formatado (A4)."""
    target_path = Path(target_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_bytes = page.pdf(format="A4", print_background=True)
    target_path.write_bytes(pdf_bytes)
    return target_path
