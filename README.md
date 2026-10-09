# Render2Go

Serviço de vídeos curtos com IA para pequenos negócios: o cliente envia as fotos (até dezenas de uma vez), escreve em português o que deve acontecer, e recebe um vídeo por foto. Os vídeos são gerados com o Wan 2.2 (imagem para vídeo) em GPUs alugadas sob demanda no RunPod.

**No ar:** https://app.render2go.com (entrada pelo e-mail e página do cliente), `/suporte`, `/termos`, `/privacidade` e `/cookies`. O painel do dono (revisão, pods, contas, recargas) fica num endereço secreto, com dois fatores (ADR 0007).

**Mudanças:** veja o [`CHANGELOG.md`](CHANGELOG.md).

## Como funciona

1. **Pedido:** o cliente entra com o e-mail e um código de 8 dígitos que chega nele (a conta nova ganha um teste gratuito; depois, pré-pago com recarga por Pix), escolhe o que quer criar (hoje, "Foto vira vídeo"; as outras opções aparecem como "Em breve"), as fotos, o estilo e a duração, e envia. As fotos vão direto para o armazenamento (Cloudflare R2), por links assinados.
2. **Revisão:** o pedido passa pelo filtro de texto e pela revisão humana no painel do dono, que mostra um aviso quando há pessoas nas fotos (ADR 0004).
3. **Geração:** o orquestrador sobe pods de GPU conforme a fila. O pod traduz o pedido, gera os vídeos, confere se cada vídeo começa na foto certa e continua nela até o fim (o vídeo que troca de produto é refeito) e envia ao R2. Depois que a fila esvazia, o pod se desliga sozinho.
4. **Entrega:** o cliente acompanha a previsão de tempo e baixa os vídeos. As fotos são apagadas quando o pedido fecha.

## Partes do repositório

| Pasta | O que tem |
|---|---|
| [`worker/`](worker/README.md) | O gerador: imagem Docker com ComfyUI 0.38.0, compilador de pedidos (Ollama), presets e receitas, lote, worker da fila e ferramentas de pods |
| [`servico/`](docs/09-implantacao.md) | O serviço: API, contas, banco, orquestrador, recarga por Pix, páginas (TypeScript, [`docs/10`](docs/10-identidade-visual-e-front-end.md)), detector de pessoas e suporte; implantação com Docker Compose; teste de invasão e medição de carga em `servico/tests/` |
| `tools/` | Geração e conferência de templates e Dockerfiles; arquivos da marca (`marca.py`) |
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
| [`docs/10-identidade-visual-e-front-end.md`](docs/10-identidade-visual-e-front-end.md) | Identidade visual, regras de layout, componentes, desempenho, acessibilidade e segurança das páginas |
| [`docs/11-custos-e-planos.md`](docs/11-custos-e-planos.md) | Custos mensais (parado, escala pequena e demanda alta) e preços do pré-pago em vigor (por segundo de vídeo, com custo e margem) |
| [`docs/12-modelos-e-licencas.md`](docs/12-modelos-e-licencas.md) | Modelos de IA por finalidade: quais podem ser usados comercialmente, com que condições e fontes; riscos dos modelos em uso |
| [`docs/13-seguranca-escala-e-lancamento.md`](docs/13-seguranca-escala-e-lancamento.md) | Revisão de segurança antes do lançamento, teste de invasão, o que o servidor aguenta (medido), GPUs com 30+ pods e a lista de tarefas do dono |
| [`docs/adr/`](docs/adr) | Decisões de arquitetura (0001 a 0008) |

## Desenvolvimento

- Worker: `cd worker && uv sync --python 3.12 && uv run pytest -q`
- Serviço: `cd servico && uv sync --python 3.12 && uv run pytest -q` (teste de invasão e medição de carga: `docs/13`, seções 3 e 4)
- Páginas: `cd servico/web && npm ci && npm run build` (edite `src/`; o build gera `publico/`, que também é versionado)

Comandos completos, regras técnicas e de segurança estão no [`CLAUDE.md`](CLAUDE.md).

## Proposta inicial (05/10/2026)

O projeto começou como uma plataforma geral de geração de vídeo. A análise ([`docs/01`](docs/01-verificacoes.md) a [`docs/04`](docs/04-plano-de-implementacao.md)) apontou bloqueadores para qualquer uso adulto, e esse uso segue bloqueado (decisão D1):
- termos do RunPod;
- fotos de pessoas reais;
- verificação de idade;
- pagamentos.

Em 06/10, o dono escolheu o caminho A: só conteúdo SFW, para marketing ([`docs/05`](docs/05-analise-de-negocio.md)).
