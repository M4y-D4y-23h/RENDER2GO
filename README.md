# GPU2GO

Plataforma web para gerar vídeos curtos com IA (Wan 2.2 + compilador de prompts em Ollama) a partir de um pedido em português, de qualquer dispositivo, sem terminal nem ComfyUI visível.

**Status:** proposta de arquitetura aguardando aprovação. Ainda não há código de produção.

## Resumo

1. **Bloqueador: Termos do RunPod.** Os Termos de Serviço do RunPod listam *"pornography or graphic adult content"* entre os conteúdos não autorizados, com risco de encerramento da conta e banimento. Isso afeta a plataforma e também o uso manual atual. Antes de investir, é preciso pedir uma resposta por escrito (há um rascunho no doc 04). A arquitetura foi desenhada para trocar de provedor sem reescrever o worker.
2. **Bloqueador: fotos de pessoas reais no I2V.** Um I2V adulto com upload livre vira ferramenta de deepfake sexual, que é crime (CP art. 216-B, parágrafo único). Recomendação para a v1: I2V só a partir de imagens geradas na própria plataforma. O T2V passa a ser a porta de entrada.
3. **Bloqueador: idade, pagamentos e licença.** O ECA Digital, em vigor desde 17/03/2026, proíbe a autodeclaração de idade para conteúdo pornográfico. Stripe e PayPal não aceitam o setor, então será preciso um processador especializado. A licença do encoder NSFW-UMT5 não foi confirmada.
4. **Arquitetura recomendada: Opção B (serverless).** Custa cerca de 5 vezes menos que pods para vídeos isolados (~US$ 0,05 contra ~US$ 0,25 por vídeo, estimados) e não expõe portas. O worker é uma imagem Docker única, com um modo "agente" que roda em qualquer GPU.
5. **Primeiro código:** imagem Docker que reproduz o `start.sh` com o ComfyUI fixado na 0.38.0 (o workflow exportado usa entradas que não existem na 0.34.0 do worker oficial do RunPod) e um script que envia o workflow e devolve o MP4.

As decisões pendentes (D1 a D7) estão na Etapa 0 do [plano](docs/04-plano-de-implementacao.md).

## Documentos

| Arquivo | Conteúdo |
|---|---|
| [`docs/01-verificacoes.md`](docs/01-verificacoes.md) | Conferência de cada item [NÃO VERIFICADO], com fontes e grau de confiança |
| [`docs/02-arquitetura.md`](docs/02-arquitetura.md) | Opção A × B (custo, latência, complexidade), arquitetura, fluxo de um job, worker, template, tiers |
| [`docs/03-seguranca-e-conformidade.md`](docs/03-seguranca-e-conformidade.md) | Requisitos da seção 11 desde a v1: política de conteúdo, idade, moderação em camadas, CSAM, denúncias, LGPD |
| [`docs/04-plano-de-implementacao.md`](docs/04-plano-de-implementacao.md) | Etapas 0 a 8 com critérios de aceite, estrutura do repositório, rascunho da consulta ao RunPod |
| [`referencia/especificacao-pipeline-manual.md`](referencia/especificacao-pipeline-manual.md) | Documento de handoff original (pipeline manual testado) |
| [`referencia/wan-i2v-com-ollama.api.json`](referencia/wan-i2v-com-ollama.api.json) | Workflow exportado do ComfyUI em formato API |
