# Verificação de CAPTCHA — automação de certidões

Data da revisão do código: 03/11/2026

## Resultado da conferência

| Portal | Evidência disponível | Como a automação se comporta |
|---|---|---|
| TST / CNDT | CAPTCHA de imagem e campo “Digite os caracteres” visíveis em `documentos/diagnostico/cndt_gerar.png`; seletor também enviado pelo usuário via Playwright Inspector. | Pausa e aguarda a pessoa preencher o CAPTCHA na janela do navegador aberta. |
| TJBA 1º Grau | Relatório da inspeção registra CAPTCHA local e Google reCAPTCHA; a captura da tela inicial mostra o selo reCAPTCHA, mas não um desafio aberto. | Detecta widget/desafio quando visível e oferece pausa manual. |
| Receita Federal | Relatório de inspeção registrou assinaturas de CAPTCHA de imagem, hCaptcha e Google reCAPTCHA; captura da página inicial não mostra desafio ativo. | Verifica em tempo de execução; pausa apenas se o desafio estiver visível. |
| CGU | Relatório mencionou CAPTCHA local, mas a captura de diagnóstico mostra erro HTTP 403 CloudFront. O 403 é bloqueio/WAF, não CAPTCHA. | Verificação genérica de CAPTCHA; bloqueio CloudFront continua tratado como erro/ação externa, não como CAPTCHA. |
| SEFAZ-BA | Nenhum CAPTCHA observado na inspeção disponível. | Verificação genérica antes/depois das etapas principais. |
| ComprasNet-BA | Nenhum CAPTCHA observado na inspeção disponível. | Verificação genérica na página e em seus frames antes da pesquisa e ao recuperar resultados. |
| TJBA — Fornecedores sancionados | Nenhum CAPTCHA observado na inspeção disponível. | Verificação genérica antes/depois da pesquisa. |
| Caixa / CRF-FGTS | Nenhum CAPTCHA observado na inspeção disponível. | Verificação genérica antes da consulta e ao aguardar o certificado. |
| TCU | Nenhum CAPTCHA observado na inspeção disponível. | Verificação genérica antes da emissão e se o botão de download não aparecer. |

## Fluxo de interação humana

1. O Chromium é iniciado com janela visível, permitindo interação com os controles do portal.
2. Quando um CAPTCHA visível é identificado, o serviço emite um evento para a interface e fica pausado sem fechar a página.
3. A janela da interface informa qual portal exige ação manual.
4. O usuário resolve o CAPTCHA no navegador e aciona **Concluir e continuar**.
5. O orquestrador libera a mesma integração para prosseguir. O botão **Cancelar execução** encerra a espera e cancela o processamento.
6. O tempo máximo de espera é de 10 minutos. Nenhuma resolução automática ou reaproveitamento de resposta é implementado.

## Limitações

A detecção é baseada em widgets, iframes e campos visíveis conhecidos. Portais podem trocar o provedor ou apresentar um desafio com seletor diferente; nesse caso, o desafio pode não ser reconhecido até que novos seletores sejam adicionados. O relatório histórico é de 26/10/2026, portanto não representa garantia de que o desafio aparecerá em toda emissão.
