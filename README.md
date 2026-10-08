# Render2Go

Serviço de vídeos curtos com IA para pequenos negócios: o cliente envia as fotos (até dezenas de uma vez), escreve em português o que deve acontecer, e recebe um vídeo por foto. Os vídeos são gerados com o Wan 2.2 (imagem para vídeo) em GPUs alugadas sob demanda no RunPod.

**No ar:** https://app.render2go.com (página do cliente), `/suporte` e `/aprovar` (revisão, só o dono).

**Mudanças:** veja o [`CHANGELOG.md`](CHANGELOG.md).

## Como funciona

1. **Pedido:** o cliente entra com um código de acesso, escolhe as fotos, o estilo e a duração, e envia. As fotos vão direto para o armazenamento (Cloudflare R2), por links assinados.
2. **Revisão:** o pedido passa pelo filtro de texto e pela revisão humana no `/aprovar`, que mostra um aviso quando há pessoas nas fotos (ADR 0004).
3. **Geração:** o orquestrador sobe pods de GPU conforme a fila. O pod traduz o pedido, gera os vídeos, confere se cada vídeo começa na foto certa e envia ao R2. Depois que a fila esvazia, o pod se desliga sozinho.
4. **Entrega:** o cliente acompanha a previsão de tempo e baixa os vídeos. As fotos são apagadas quando o pedido fecha.

## Partes do repositório

| Pasta | O que tem |
|---|---|
| [`worker/`](worker/README.md) | O gerador: imagem Docker com ComfyUI 0.38.0, compilador de pedidos (Ollama), presets e receitas, lote, worker da fila e ferramentas de pods |
| [`servico/`](docs/09-implantacao.md) | O serviço: API, banco, orquestrador, páginas (TypeScript), detector de pessoas e suporte; implantação com Docker Compose |
| `tools/` | Geração e conferência de templates e Dockerfiles |
| `infra/runpod/` | Dockerfiles do RunPod (gerados) e endpoint de teste |
| `fixtures/sfw/` | Imagens de teste, só conteúdo SFW, com origem e licença |
| `referencia/` | Pipeline manual original e workflow exportado (não editar) |

## Documentos

| Arquivo | Conteúdo |
|---|---|
| [`docs/01-verificacoes.md`](docs/01-verificacoes.md) | Conferência dos pontos não verificados da proposta, com fontes |
| [`docs/02-arquitetura.md`](docs/02-arquitetura.md) | Arquitetura, fluxo de um job, worker e template |
| [`docs/03-seguranca-e-conformidade.md`](docs/03-seguranca-e-conformidade.md) | Política de conteúdo, moderação em camadas, LGPD |
| [`docs/04-plano-de-implementacao.md`](docs/04-plano-de-implementacao.md) | Etapas, critérios de aceite e decisões pendentes (D1–D7) |
| [`docs/05-analise-de-negocio.md`](docs/05-analise-de-negocio.md) | Análise de negócio e a escolha do caminho A (marketing) |
| [`docs/06-qualidade-velocidade-custo.md`](docs/06-qualidade-velocidade-custo.md) | Qualidade, velocidade e custo dos lotes |
| [`docs/07-guia-tecnico-gpus-e-pods.md`](docs/07-guia-tecnico-gpus-e-pods.md) | GPUs medidas, pods, proteções e custos |
| [`docs/08-varios-clientes-e-demanda-alta.md`](docs/08-varios-clientes-e-demanda-alta.md) | Vários clientes ao mesmo tempo, escala e isolamento |
| [`docs/09-implantacao.md`](docs/09-implantacao.md) | Implantação e operação do serviço (servidor, R2, e-mail, migração, 24 h) |
| [`docs/adr/`](docs/adr) | Decisões de arquitetura (0001 a 0005) |

## Desenvolvimento

- Worker: `cd worker && uv sync --python 3.12 && uv run pytest -q`
- Serviço: `cd servico && uv sync --python 3.12 && uv run pytest -q`
- Páginas: `cd servico/web && npm ci && npm run build`

Comandos completos, regras técnicas e de segurança estão no [`CLAUDE.md`](CLAUDE.md).

## Proposta inicial (05/10/2026)

O projeto começou como uma plataforma geral de geração de vídeo. A análise ([`docs/01`](docs/01-verificacoes.md) a [`docs/04`](docs/04-plano-de-implementacao.md)) apontou bloqueadores para qualquer uso adulto, e esse uso segue bloqueado (decisão D1):
- termos do RunPod;
- fotos de pessoas reais;
- verificação de idade;
- pagamentos.

Em 06/10, o dono escolheu o caminho A: só conteúdo SFW, para marketing ([`docs/05`](docs/05-analise-de-negocio.md)).
