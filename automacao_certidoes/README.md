2# Automação de Certidões Empresariais

Aplicativo desktop (Python + CustomTkinter) para emitir certidões e consultar fornecedores em nove portais oficiais,
com log em tempo real, contadores, documentos por CNPJ/execução e histórico em SQLite.

> **Status:** As emissões simuladas foram descontinuadas. Cada serviço em `config/certidoes.json` opera em modo real ou pendente de automação (`"modo": "pendente"` / `"implementada"`).
> Todos os portais possuem links diretos acessíveis na interface e no código. Foi disponibilizado um **script de inspeção** automatizado com diagnóstico HTTP e Playwright Chromium para mapear seletores e proteções de cada portal.

---

## Portais Oficiais Integrados

| ID | Órgão | Serviço / Certidão | Tipo | Link Oficial |
|---|---|---|:---:|---|
| `receita_federal` | Receita Federal do Brasil | Débitos e Dívida Ativa da União | Certidão | [Acessar Portal](https://servicos.receitafederal.gov.br/servico/certidoes/#/home/cnpj) |
| `cgu` | Controladoria-Geral da União | Certidões e Consultas (PJ) | Certidão | [Acessar Portal](https://certidoes.cgu.gov.br/) |
| `cndt` | Tribunal Superior do Trabalho | Certidão Negativa de Débitos Trabalhistas | Certidão | [Acessar Portal](https://cndt-certidao.tst.jus.br/) |
| `sefaz_ba` | SEFAZ Bahia | Certidão de Débitos Tributários Estaduais | Certidão | [Acessar Portal](https://servicos.sefaz.ba.gov.br/sistemas/DSCRE/Modulos/Publico/EmissaoCertidao.aspx) |
| `comprasnet_ba` | Governo do Estado da Bahia | Consulta de Fornecedores com Penalidades | Consulta | [Acessar Portal](https://www.comprasnet.ba.gov.br/inter/system/Fornecedor/FornecedorComPenalidade.asp) |
| `tjba_fornecedores` | Tribunal de Justiça da Bahia | Consulta de Fornecedores Sancionados | Consulta | [Acessar Portal](https://www7.tjba.jus.br/pjlcnet/relatorio/fornecedores_sancionados.wsp) |
| `caixa_fgts` | Caixa Econômica Federal | Certificado de Regularidade do FGTS (CRF) | Certidão | [Acessar Portal](https://consulta-crf.caixa.gov.br/consultacrf/pages/consultaEmpregador.jsf) |
| `tcu_inidoneos` | Tribunal de Contas da União | Certidão de Inidôneos | Certidão | [Acessar Portal](https://certidoes.apps.tcu.gov.br/emitir-certidao-inidoneos) |
| `tjba_primeiro_grau` | Tribunal de Justiça da Bahia | Certidões Judiciais de 1º Grau | Certidão | [Acessar Portal](https://portalcertidoes.tjba.jus.br/#/primeirograu) |

---

## Instalação
```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt
playwright install chromium
```

## Inspeção dos Portais Oficiais
Para verificar a conectividade, latência, tempo de resposta e mapear seletores de CNPJ e CAPTCHAs em todos os portais:
```bash
# Inspecionar todos os portais (HTTP + Playwright Browser + Screenshots):
python inspecionar_portais.py

# Apenas checagem rápida HTTP/SSL:
python inspecionar_portais.py --mode http

# Inspecionar um portal específico:
python inspecionar_portais.py --service receita_federal
```
Os relatórios e capturas de tela são salvos automaticamente em `documentos/inspecao/` (`relatorio_inspecao.md`, `relatorio_inspecao.json` e capturas `.png`).

## Executar a Aplicação
```bash
python main.py
```
Na tela principal, cada item possui o botão **"Abrir portal ↗"** para abrir diretamente o portal no navegador.

## Testes
```bash
pytest
```

## Estrutura
| Pasta | Função |
|---|---|
| `gui/` | Interface com botões diretos de acesso aos portais, logs e status. |
| `core/` | Regras: validação de CNPJ, orquestrador, modelos, resumo, filtros, arquivos. |
| `scripts/` | Script de inspeção técnica (`inspecionar_portais.py`). |
| `integrations/` | Integrações oficiais dos portais (`BaseIntegration`). |
| `services/` | Log em arquivo e histórico SQLite. |
| `config/` | `certidoes.json` (serviços e URLs oficiais) e `settings.json`. |
| `documentos/` | Pastas de execuções reais e pasta `inspecao/` com diagnósticos e capturas. |

## Resolução manual de CAPTCHA

O navegador Chromium é aberto em janela visível para permitir que o usuário resolva desafios legítimos dos portais. Quando a automação identifica um CAPTCHA visível, ela pausa na mesma página e exibe uma janela na interface com as opções **Concluir e continuar** e **Cancelar execução**. Depois de resolver o desafio no navegador, volte à interface e clique em **Concluir e continuar**; a automação retoma a etapa interrompida sem reiniciar a consulta. O tempo máximo de espera é de 10 minutos.

Não há tentativa de contornar CAPTCHA, gerar respostas automaticamente ou reutilizar códigos fixos. O campo do TST identificado como “Digite os caracteres” deve ser preenchido pela pessoa.

### Situação identificada nos arquivos de inspeção do projeto

- **TST / CNDT — confirmado:** a captura `documentos/diagnostico/cndt_gerar.png` mostra CAPTCHA de imagem e campo para digitar os caracteres. O código do Inspector enviado pelo usuário também localiza esse campo.
- **TJBA 1º Grau — proteção identificada:** o relatório de inspeção registra Google reCAPTCHA e CAPTCHA local; a captura da página mostra o selo reCAPTCHA, mas não registra um desafio aberto. A interface só pausa quando o desafio estiver visível.
- **Receita Federal — proteção possível:** o relatório de inspeção registrou assinaturas de CAPTCHA local, hCaptcha e reCAPTCHA, mas a captura do formulário (`documentos/inspecao/receita_federal.png`) não mostra um desafio ativo.
- **CGU — não classificar como CAPTCHA com base na evidência atual:** a captura de diagnóstico `documentos/diagnostico/cgu_form.png` mostra resposta HTTP 403 do CloudFront. Isso é bloqueio de acesso/WAF, não um CAPTCHA que o usuário possa simplesmente preencher.
- **SEFAZ-BA, ComprasNet-BA, TJBA Fornecedores sancionados, Caixa/CRF e TCU — nenhum CAPTCHA foi observado nas capturas de inspeção disponíveis.** Mesmo assim, a verificação genérica foi incluída nesses fluxos para pausar se um desafio aparecer durante uma consulta futura.

Essas conclusões se baseiam nas capturas e no relatório de inspeção datados de **26/10/2026**, além do código Playwright do TST enviado nesta conversa. Os portais podem mudar ou apresentar desafios dinamicamente; a detecção em tempo de execução é a referência para abrir a pausa manual.
