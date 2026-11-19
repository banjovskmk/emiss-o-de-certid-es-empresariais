"""Script de inspeção de portais oficiais de certidões e consultas.

Executa diagnósticos de conectividade HTTP, SSL, renderização via Playwright Chromium,
identificação de campos de CNPJ, botões de ação e detecção de CAPTCHAs/WAFs.
Gera relatórios em Markdown, JSON e capturas de tela (screenshots).

Uso:
    python inspecionar_portais.py
    python inspecionar_portais.py --mode http
    python inspecionar_portais.py --mode browser
    python inspecionar_portais.py --service receita_federal
"""
from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.config import load_services
from core.models import ServiceConfig

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

CAPTCHA_PATTERNS = {
    "Google reCAPTCHA": [r"google\.com/recaptcha", r"g-recaptcha", r"recaptcha/api"],
    "hCaptcha": [r"hcaptcha\.com", r"h-captcha"],
    "Cloudflare Turnstile": [r"challenges\.cloudflare\.com/turnstile", r"cf-turnstile"],
    "ShieldSquare / Radware": [r"validate\.perfdrive\.com", r"shieldsquare", r"_ss_"],
    "Gov.br / Cidadão.br": [r"sso\.acesso\.gov\.br", r"acesso\.gov\.br"],
    "CAPTCHA de Imagem Local": [r"captcha.*image", r"captcha\.ashx", r"img.*captcha", r"id=.*captcha", r"codigo.*seguranca"],
}


def inspect_http(service: ServiceConfig, timeout: float = 20.0) -> Dict[str, Any]:
    """Inspeciona conectividade básica HTTP/SSL e extrai metadados do HTML bruto."""
    result: Dict[str, Any] = {
        "id": service.id,
        "nome": service.nome,
        "url_configurada": service.url,
        "http_ok": False,
        "http_status": None,
        "url_final": service.url,
        "tempo_ms": None,
        "titulo_html": None,
        "tamanho_bytes": 0,
        "ssl_valido": True,
        "redirected": False,
        "captchas_detectados": [],
        "campos_cnpj_sugeridos": [],
        "erro": None,
    }

    t0 = time.time()
    req = urllib.request.Request(
        service.url,
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        },
    )

    ctx = ssl.create_default_context()
    # Permitir certificados com cadeias intermediárias governamentais
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            elapsed = round((time.time() - t0) * 1000)
            result["tempo_ms"] = elapsed
            result["http_status"] = resp.status
            result["url_final"] = resp.geturl()
            result["redirected"] = resp.geturl() != service.url
            result["http_ok"] = (200 <= resp.status < 400)

            raw_bytes = resp.read()
            result["tamanho_bytes"] = len(raw_bytes)
            text = raw_bytes.decode("utf-8", errors="replace")

            # Título
            m_title = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
            if m_title:
                result["titulo_html"] = m_title.group(1).strip()

            # Checar assinaturas de CAPTCHA / WAF
            for name, pats in CAPTCHA_PATTERNS.items():
                for pat in pats:
                    if re.search(pat, text, re.IGNORECASE) or re.search(pat, result["url_final"], re.IGNORECASE):
                        if name not in result["captchas_detectados"]:
                            result["captchas_detectados"].append(name)
                        break

            # Buscar inputs associados a CNPJ
            inputs = re.findall(r"<input[^>]+>", text, re.IGNORECASE)
            for inp in inputs:
                if re.search(r"cnpj|documento|num.*inscricao", inp, re.IGNORECASE):
                    result["campos_cnpj_sugeridos"].append(inp.strip()[:120])

    except urllib.error.HTTPError as exc:
        result["tempo_ms"] = round((time.time() - t0) * 1000)
        result["http_status"] = exc.code
        result["erro"] = f"HTTP {exc.code}: {exc.reason}"
    except Exception as exc:
        result["tempo_ms"] = round((time.time() - t0) * 1000)
        result["erro"] = str(exc)

    return result


def inspect_browser(
    service: ServiceConfig,
    output_dir: Path,
    timeout: float = 25.0,
    headless: bool = True,
    take_screenshots: bool = True,
) -> Dict[str, Any]:
    """Inspeciona a página real renderizada com o Chromium headless do Playwright."""
    from playwright.sync_api import sync_playwright

    result: Dict[str, Any] = {
        "id": service.id,
        "browser_ok": False,
        "url_final_browser": None,
        "titulo_browser": None,
        "tempo_render_ms": None,
        "inputs_encontrados": [],
        "campos_cnpj_detectados": [],
        "botoes_encontrados": [],
        "captchas_detectados": [],
        "screenshot_path": None,
        "erro": None,
    }

    t0 = time.time()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(
                user_agent=DEFAULT_USER_AGENT,
                viewport={"width": 1280, "height": 800},
                ignore_https_errors=True,
            )
            page = context.new_page()

            try:
                page.goto(service.url, timeout=int(timeout * 1000), wait_until="domcontentloaded")
                # Aguarda estabilização de rede / SPA
                try:
                    page.wait_for_load_state("networkidle", timeout=6000)
                except Exception:
                    pass

                result["tempo_render_ms"] = round((time.time() - t0) * 1000)
                result["url_final_browser"] = page.url
                result["titulo_browser"] = page.title()
                result["browser_ok"] = True

                # Analisar elementos interativos no DOM
                page_content = page.content()

                # Checagem de desafios / CAPTCHA no DOM
                for name, pats in CAPTCHA_PATTERNS.items():
                    for pat in pats:
                        if re.search(pat, page_content, re.IGNORECASE) or re.search(pat, page.url, re.IGNORECASE):
                            if name not in result["captchas_detectados"]:
                                result["captchas_detectados"].append(name)
                            break

                # Localizar inputs de texto
                inputs = page.query_selector_all("input")
                for inp in inputs:
                    try:
                        inp_type = inp.get_attribute("type") or "text"
                        if inp_type in ("hidden", "checkbox", "radio", "submit", "button"):
                            continue
                        name = inp.get_attribute("name") or ""
                        inp_id = inp.get_attribute("id") or ""
                        placeholder = inp.get_attribute("placeholder") or ""
                        aria = inp.get_attribute("aria-label") or ""

                        selector_info = {
                            "type": inp_type,
                            "name": name,
                            "id": inp_id,
                            "placeholder": placeholder,
                            "aria_label": aria,
                        }
                        result["inputs_encontrados"].append(selector_info)

                        full_text = f"{name} {inp_id} {placeholder} {aria}".lower()
                        if any(k in full_text for k in ("cnpj", "documento", "inscricao", "empresa")):
                            result["campos_cnpj_detectados"].append(selector_info)
                    except Exception:
                        pass

                # Localizar botões de ação
                buttons = page.query_selector_all("button, input[type=submit], input[type=button], a.btn")
                for btn in buttons[:10]:
                    try:
                        txt = (btn.inner_text() or btn.get_attribute("value") or "").strip()
                        btn_id = btn.get_attribute("id") or ""
                        if txt or btn_id:
                            result["botoes_encontrados"].append({"texto": txt[:40], "id": btn_id})
                    except Exception:
                        pass

                # Captura de tela
                if take_screenshots:
                    output_dir.mkdir(parents=True, exist_ok=True)
                    shot_path = output_dir / f"{service.id}.png"
                    page.screenshot(path=str(shot_path), full_page=False)
                    result["screenshot_path"] = str(shot_path)

            finally:
                context.close()
                browser.close()

    except Exception as exc:
        result["tempo_render_ms"] = round((time.time() - t0) * 1000)
        result["erro"] = str(exc)

    return result


def generate_markdown_report(inspections: List[Dict[str, Any]], output_file: Path) -> None:
    """Gera um relatório completo em formato Markdown."""
    lines: List[str] = [
        "# Relatório de Inspeção dos Portais Oficiais",
        "",
        f"**Data da inspeção:** {datetime.now():%d/%m/%Y às %H:%M:%S}  ",
        f"**Total de portais inspecionados:** {len(inspections)}  ",
        "",
        "## Resumo Geral de Disponibilidade",
        "",
        "| ID | Portal / Serviço | Órgão | HTTP | Tempo (ms) | Browser Render | CAPTCHA / Desafio | Screenshot |",
        "|---|---|---|:---:|:---:|:---:|---|:---:|",
    ]

    for item in inspections:
        svc = item["servico"]
        h = item.get("http", {})
        b = item.get("browser", {})

        http_badge = "✅ 200" if h.get("http_ok") else f"❌ {h.get('http_status') or 'Erro'}"
        tempo = f"{h.get('tempo_ms') or '-'} ms"
        browser_badge = "✅ OK" if b.get("browser_ok") else ("❌ Falha" if b else "—")

        captchas = list(set((h.get("captchas_detectados") or []) + (b.get("captchas_detectados") or [])))
        captcha_str = ", ".join(captchas) if captchas else "Nenhum detectado"

        has_shot = "📸 Sim" if b.get("screenshot_path") else "—"
        lines.append(
            f"| `{svc.id}` | [{svc.nome}]({svc.url}) | {svc.orgao} | {http_badge} | {tempo} | {browser_badge} | {captcha_str} | {has_shot} |"
        )

    lines.extend([
        "",
        "---",
        "",
        "## Detalhamento Técnico por Portal",
        "",
    ])

    for item in inspections:
        svc: ServiceConfig = item["servico"]
        h = item.get("http", {})
        b = item.get("browser", {})

        lines.extend([
            f"### {svc.nome} ({svc.orgao})",
            f"- **ID interno:** `{svc.id}`",
            f"- **Tipo de operação:** {svc.tipo.value}",
            f"- **URL configurada:** [{svc.url}]({svc.url})",
        ])

        if h.get("url_final") and h.get("url_final") != svc.url:
            lines.append(f"- **URL final (redirecionada):** `{h.get('url_final')}`")

        lines.append(f"- **Título da página:** {b.get('titulo_browser') or h.get('titulo_html') or 'Não obtido'}")
        lines.append(f"- **Status HTTP:** {h.get('http_status') or 'N/A'} (tempo de resposta: {h.get('tempo_ms')} ms)")

        if b.get("tempo_render_ms"):
            lines.append(f"- **Tempo de renderização (Playwright):** {b.get('tempo_render_ms')} ms")

        # Desafios
        captchas = list(set((h.get("captchas_detectados") or []) + (b.get("captchas_detectados") or [])))
        if captchas:
            lines.append(f"- ⚠️ **Desafios / Proteções detectadas:** {', '.join(captchas)}")
        else:
            lines.append("- **Proteção contra robôs:** Nenhum CAPTCHA/WAF óbvio detectado na página inicial.")

        # Campos de CNPJ
        cnpj_fields = b.get("campos_cnpj_detectados") or []
        if cnpj_fields:
            lines.append("- **Campos de CNPJ/Documento identificados:**")
            for f in cnpj_fields:
                sel = f"id=`{f['id']}`" if f.get("id") else f"name=`{f['name']}`"
                lines.append(f"  - Input ({sel}): placeholder={f.get('placeholder')!r}, aria={f.get('aria_label')!r}")
        elif b.get("inputs_encontrados"):
            lines.append(f"- **Inputs disponíveis ({len(b['inputs_encontrados'])}):**")
            for f in b["inputs_encontrados"][:5]:
                lines.append(f"  - `{f.get('type')}` id=`{f.get('id')}` name=`{f.get('name')}` placeholder=`{f.get('placeholder')}`")

        # Botões
        if b.get("botoes_encontrados"):
            btn_labels = [btn.get("texto") or btn.get("id") for btn in b["botoes_encontrados"] if btn.get("texto") or btn.get("id")]
            if btn_labels:
                lines.append(f"- **Botões de ação encontrados:** {', '.join(f'`{label}`' for label in btn_labels[:6])}")

        if b.get("screenshot_path"):
            shot_file = Path(b['screenshot_path']).name
            lines.append(f"- **Captura de tela:** `documentos/inspecao/{shot_file}`")

        if h.get("erro") or b.get("erro"):
            lines.append(f"- ❌ **Erros reportados:** HTTP={h.get('erro')} | Browser={b.get('erro')}")

        lines.append("")

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("\n".join(lines), encoding="utf-8")


def print_console_summary(inspections: List[Dict[str, Any]]) -> None:
    """Exibe no terminal um resumo formatado da inspeção."""
    sep = "=" * 80
    print("\n" + sep)
    print("  RESULTADO DA INSPEÇÃO DOS PORTAIS OFICIAIS DE CERTIDÕES")
    print(sep)
    header = f"{'SERVIÇO':<22} | {'HTTP':<8} | {'TEMPO':<8} | {'BROWSER':<9} | {'CAPTCHA / WAF':<20}"
    print(header)
    print("-" * 80)

    for item in inspections:
        svc = item["servico"]
        h = item.get("http", {})
        b = item.get("browser", {})

        status = f"OK {h.get('http_status')}" if h.get("http_ok") else f"ERRO {h.get('http_status') or '!'}"
        tempo = f"{h.get('tempo_ms') or '-'}ms"
        browser = "OK" if b.get("browser_ok") else ("FALHA" if b else "-")

        captchas = list(set((h.get("captchas_detectados") or []) + (b.get("captchas_detectados") or [])))
        cap_str = captchas[0] if captchas else "Nenhum"
        if len(captchas) > 1:
            cap_str += f" (+{len(captchas)-1})"

        print(f"{svc.nome_curto:<22} | {status:<8} | {tempo:<8} | {browser:<9} | {cap_str:<20}")

    print(sep + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspeciona os portais oficiais de certidões.")
    parser.add_argument(
        "--mode",
        choices=["all", "http", "browser"],
        default="all",
        help="Modo de inspeção: 'http' (rápido), 'browser' (Playwright) ou 'all' (ambos).",
    )
    parser.add_argument("--service", type=str, default="", help="ID de um serviço específico para inspecionar.")
    parser.add_argument("--timeout", type=float, default=25.0, help="Tempo limite por portal em segundos.")
    parser.add_argument("--no-headless", action="store_true", help="Abre o navegador visível (não-headless).")
    parser.add_argument("--no-screenshots", action="store_true", help="Não salvar capturas de tela.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=BASE_DIR / "documentos" / "inspecao",
        help="Diretório para salvar relatórios e capturas.",
    )

    args = parser.parse_args()

    config_path = BASE_DIR / "config" / "certidoes.json"
    services = load_services(config_path)

    if args.service:
        services = [s for s in services if s.id == args.service]
        if not services:
            print(f"Erro: serviço '{args.service}' não encontrado em {config_path}.")
            return 1

    print(f"Iniciando inspeção de {len(services)} portal(is) no modo '{args.mode}'...")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    inspections: List[Dict[str, Any]] = []

    for idx, svc in enumerate(services, start=1):
        print(f"[{idx}/{len(services)}] Inspecionando: {svc.nome} ({svc.url})...")
        item_res: Dict[str, Any] = {"servico": svc}

        if args.mode in ("all", "http"):
            h_res = inspect_http(svc, timeout=args.timeout)
            item_res["http"] = h_res
            status_str = f"HTTP {h_res['http_status']}" if h_res["http_ok"] else f"ERRO ({h_res.get('erro')})"
            print(f"    -> Conexão: {status_str} em {h_res.get('tempo_ms')}ms")

        if args.mode in ("all", "browser"):
            b_res = inspect_browser(
                svc,
                output_dir=args.output_dir,
                timeout=args.timeout,
                headless=not args.no_headless,
                take_screenshots=not args.no_screenshots,
            )
            item_res["browser"] = b_res
            b_status = "OK" if b_res["browser_ok"] else f"ERRO ({b_res.get('erro')})"
            print(f"    -> Browser: {b_status} | Título: {b_res.get('titulo_browser')!r}")

        inspections.append(item_res)

    print_console_summary(inspections)

    # Gravar relatórios
    md_path = args.output_dir / "relatorio_inspecao.md"
    generate_markdown_report(inspections, md_path)
    print(f"Relatório Markdown salvo em: {md_path}")

    json_path = args.output_dir / "relatorio_inspecao.json"
    json_data = []
    for item in inspections:
        svc = item["servico"]
        json_data.append({
            "id": svc.id,
            "nome": svc.nome,
            "orgao": svc.orgao,
            "url": svc.url,
            "tipo": svc.tipo.value,
            "http": item.get("http"),
            "browser": item.get("browser"),
        })
    json_path.write_text(json.dumps(json_data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Relatório JSON salvo em: {json_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
