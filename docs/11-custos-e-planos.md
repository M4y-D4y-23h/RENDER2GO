# 11 — Custos mensais e preços

Levantamento de 08/10/2026. Os custos vêm das medições do projeto (`docs/07`), dos preços do RunPod consultados pela API nesse dia e de preços públicos. A seção 4 traz os **preços em vigor desde 09/10/2026** (pré-pago, aprovados pelo dono do serviço), que substituíram a proposta de planos por créditos.

Não é parecer contábil nem fiscal: impostos e taxas de pagamento devem ser confirmados com um contador.

## 1. O que cada parte custa

| Parte | O que é | Preço | Observação |
|---|---|---|---|
| Servidor | Hetzner CX23 (2 vCPU, 4 GB), Nuremberg | ~€ 5,49 + € 0,50 do IPv4 ≈ **€ 6/mês (≈ R$ 36)** | Preço de junho/2026 para pedidos novos; confira o valor exato na fatura do console. Backup automático da Hetzner: +20% (opcional). |
| Domínio | render2go.com na Cloudflare | ~US$ 10,46/ano (**≈ R$ 5/mês**) | A Cloudflare cobra o preço de custo do .com. |
| DNS e HTTPS | Cloudflare (DNS) e Caddy (certificado) | grátis | |
| Armazenamento | Cloudflare R2, bucket `gpu2go` | 10 GB, 1 milhão de operações classe A e 10 milhões de classe B grátis por mês; acima disso, US$ 0,015 por GB-mês, US$ 4,50 e US$ 0,36 por milhão; **saída grátis** | O espelho dos modelos (36,6 GB) custa ~US$ 0,40/mês. As fotos são apagadas quando o pedido fecha; os vídeos, em 30 dias. |
| GPUs | RunPod, nuvem secure, por segundo de uso | L40S **US$ 1,09/h**; RTX 6000 Ada US$ 0,99/h; RTX PRO 6000 US$ 2,49/h; RTX 4090 US$ 0,89/h | Sem mensalidade: pod desligado não custa nada. |
| E-mail do suporte | SMTP (ainda não configurado) | grátis num plano de entrada de um serviço de envio (ex.: Brevo, Resend) | Caixa de e-mail própria (ex.: suporte@render2go.com) é à parte. |
| Código e CI | GitHub (repositório privado) | grátis | Até 2.000 min/mês de CI. |

- **Câmbio:** o dólar ficou perto de R$ 5,15 em setembro/2026. Para o que é pago em dólar no cartão (RunPod, domínio), conte **~R$ 5,70 por US$**, com IOF (3,5%) e o spread do banco.
- **Orquestrador ligado 24 h:** não custa nada além do servidor. Ele só olha a fila; GPU só é cobrada com pod ligado.

## 2. Custo de GPU por vídeo (L40S, a GPU padrão desde 08/10)

| Vídeo | Tempo no pod | Custo |
|---|---|---|
| 3 s, 480p (teste grátis) | ~37 s | ~US$ 0,011 |
| 5 s, 480p (rápida) | ~68 s | ~US$ 0,021 |
| 5 s, 720p (alta) | ~3 min 15 s | ~US$ 0,059 |
| 7 s, 720p | ~5 min 20 s | ~US$ 0,098 |

- **Cada vez que um pod liga**, paga-se a subida (~3 min) e o pod quente (3 min esperando o próximo pedido): **~US$ 0,11** na L40S (~US$ 0,26 na RTX PRO 6000).
- **Medido pelo dono em 08/10:** 41 vídeos, em dois códigos, saíram a **~US$ 0,03 por vídeo**, já com as subidas, na RTX PRO 6000 a US$ 2,49/h. Na L40S, a mesma mistura deve sair por ~US$ 0,02.
- Os tempos de "rápida" e "alta" na L40S foram medidos em 06/10 (`docs/07`); o acréscimo de ~13–15 s por vídeo (baixar a foto, conferir e enviar) foi medido em produção em 08/10.

## 3. Quanto custa por mês

### 3.1 Parado (nenhum cliente)

Servidor, domínio e espelho dos modelos: **~R$ 43/mês**. Sem pod ligado, o RunPod não cobra nada.

### 3.2 Escala pequena

Hipótese: ~10 clientes ativos, **300 vídeos/mês** (5 s, 480p), 30 testes grátis e ~60 subidas de pod no mês.

| Item | Por mês |
|---|---|
| Servidor (Hetzner) | ~R$ 36 |
| Domínio | ~R$ 5 |
| R2 (espelho dos modelos; vídeos cabem na cota grátis) | ~R$ 2 |
| GPU: 300 vídeos + 30 testes + 60 subidas = ~US$ 16 | ~R$ 93 |
| E-mail (plano grátis) | R$ 0 |
| **Total** | **~R$ 136 (≈ US$ 24)** |

### 3.3 Demanda alta

Hipótese: ~300 clientes ativos, **30.000 vídeos/mês** (18 mil em 480p e 12 mil em 720p, de 5 s), 1.000 testes grátis e ~600 subidas de pod, com até 8 pods ao mesmo tempo nos picos.

| Item | Por mês |
|---|---|
| GPU: ~US$ 1.260 | ~R$ 7.180 |
| Servidor maior (4 vCPU, 8 GB) e backup | ~R$ 100 |
| R2: ~122 GB guardados (vídeos de 30 dias e o espelho), operações dentro da cota grátis | ~R$ 10 |
| E-mail transacional pago | ~R$ 115 |
| Domínio | ~R$ 5 |
| **Total** | **~R$ 7.400 (≈ US$ 1.300)** |

- **A GPU é ~96% do custo** e cresce junto com os vídeos vendidos. O resto é quase fixo.
- **Nessa escala, mudar no `.env`:**
  - `RENDER2GO_MAX_USD_H` (padrão 20): o orquestrador conta cada pod novo pelo preço da GPU mais cara da lista com vaga (US$ 2,49/h se a RTX PRO 6000 tiver estoque), então 20 US$/h dão ~8 pods;
  - `RENDER2GO_MAX_PODS`: hoje são 8;
  - pedir ao RunPod aumento do limite de GPUs simultâneas e manter saldo para alguns dias.
- **Gargalo que não é custo:** a revisão humana de cada pedido (ADR 0004). Com milhares de pedidos por mês, ela passa a precisar do filtro de imagem (ADR 0005) ou de mais gente revisando.

### 3.4 Opcional: um pod sempre ligado no horário comercial

Uma L40S ligada das 9h às 21h, nos dias úteis (~264 h/mês), custa **~US$ 288 (≈ R$ 1.640) por mês**. Em troca, os pedidos desse horário começam em ~1 min, sem os ~3 min de subida. Só compensa com fila constante de clientes; hoje não compensa.

Uma alternativa mais barata é deixar o pod quente esperar mais entre pedidos: `RENDER2GO_POD_OCIOSO_MIN` (padrão 3). Cada minuto a mais custa ~US$ 0,018 na L40S, toda vez que a fila esvazia.

## 4. Preços em vigor (desde 09/10/2026): pré-pago, por segundo

### 4.1 Como funciona

- **Sem planos nem assinaturas.** O cliente do plano `pago` tem um saldo em reais, recarrega quando quiser e paga por segundo de vídeo:

  | Qualidade | Preço por segundo | 4 s | 5 s | 7 s |
  |---|---|---|---|---|
  | 480p (rápida) | **R$ 0,25** (US$ 0,05) | R$ 1,00 | R$ 1,25 | R$ 1,75 |
  | 720p (alta) | **R$ 0,50** (US$ 0,10) | R$ 2,00 | R$ 2,50 | R$ 3,50 |

  - Os valores em dólar usam o câmbio de R$ 5,00 que o dono escolheu. Com o câmbio real do cartão (~R$ 5,70, seção 1), R$ 0,25 equivale a ~US$ 0,044.
  - Mudam no `.env`, sem mexer no código: `RENDER2GO_PRECO_480P_S` e `RENDER2GO_PRECO_720P_S` (`docs/09`, seção 4).
- **Duração:** de 4 a 7 s. Vídeos de 3 s existem só no teste gratuito.
- **Cobrança (desde 12/10/2026, [ADR 0012](adr/0012-cobranca-por-video-entregue.md)):** cada vídeo é cobrado quando fica pronto (conferência e filtro aprovados); o vídeo que não sai e o pedido recusado não custam nada, então não há estorno. Para criar o pedido, o saldo precisa pagar o 1º vídeo; a página mostra a estimativa do pedido inteiro e quantos vídeos o saldo atual paga. Sem saldo para o próximo vídeo, o pedido espera ("Aguardando saldo") e continua sozinho depois da recarga, sem segurar pod. Sem limite de fotos por pedido nem por dia nos planos pago e interno.
- **Pedidos de antes de 12/10/2026:** seguem a regra antiga até o fim: cobrados inteiros quando as fotos chegaram; o recusado volta inteiro e cada vídeo que não sai volta sozinho para o saldo.
- **Recarga:** pelo próprio cliente, por Pix, creditada sozinha quando o Mercado Pago confirma (ADR 0008); ou à mão, pelo dono, no painel ou pelo terminal (`docs/09`, seção 5.1.7). O extrato fica guardado e o cliente vê os últimos movimentos na página.
- **Teste gratuito:** um por caixa de e-mail (conta criada pelo e-mail, ADR 0007): um pedido, vídeos de 3 s em 480p, até 5 fotos (eram 10). Custa ~US$ 0,055 de GPU por pessoa (~R$ 0,31), mais a parte da subida do pod, que é dividida entre os testes agrupados.
- **Plano interno** (o dono): não paga.

### 4.2 Custo e margem por vídeo (L40S)

GPU em reais a ~R$ 5,70 por dólar. "Sobra" é o preço menos imposto e taxa de pagamento (de 10% a 20%: Simples de 6% a 15,5%, mais Pix ou cartão) e menos a GPU.

| Vídeo | Preço | GPU | Sobra |
|---|---|---|---|
| 4 s, 480p | R$ 1,00 | ~R$ 0,09 | R$ 0,71–0,81 (71–81%) |
| 5 s, 480p | R$ 1,25 | ~R$ 0,12 | R$ 0,88–1,01 (70–81%) |
| 7 s, 480p | R$ 1,75 | ~R$ 0,17 | R$ 1,23–1,41 (70–80%) |
| 4 s, 720p | R$ 2,00 | ~R$ 0,23 | R$ 1,37–1,57 (69–79%) |
| 5 s, 720p | R$ 2,50 | ~R$ 0,34 | R$ 1,66–1,91 (66–76%) |
| 7 s, 720p | R$ 3,50 | ~R$ 0,56 | R$ 2,24–2,59 (64–74%) |

- **Medidos:** 5 s em 480p, 5 s e 7 s em 720p (seção 2). Os de 4 s e 7 s em 480p e 4 s em 720p são proporcionais ao tempo medido.
- **Subida do pod:** ~R$ 0,63 por vez na L40S (seção 2). Num pedido de um vídeo só de 4 s, ela come quase toda a sobra; a partir de 3 ou 4 vídeos por subida, pesa pouco. Pedidos seguidos aproveitam o pod quente.
- **Ainda não descontados:** o seu tempo de revisão, os custos fixos (~R$ 43/mês), os vídeos refeitos por qualidade (no teste das receitas, 2 de 8 não ficaram bons), a taxa do Mercado Pago em cada recarga por Pix (depende da conta; confira em "Seu negócio → Custos", ADR 0008) e o provedor de e-mail dos códigos de entrada.
- **O custo medido pelo dono** (~US$ 0,03 por vídeo de 3 s, já com as subidas, na RTX PRO 6000) dá ~R$ 0,17: ainda bem abaixo do menor preço (R$ 1,00).
- **Vídeo refeito pela conferência** (desde 09/10, o vídeo que troca de produto no meio volta com outra seed, até 3 tentativas) custa GPU de novo e não é cobrado outra vez; se não sair, volta para o saldo do cliente.
- **Para conferir com os números reais:** o painel, seção "GPUs" (`docs/09`, seção 5.1.8), mostra o custo estimado de cada pod e o custo por vídeo do período, já com subidas, pod quente, refações e falhas na instalação.

### 4.3 Receita nos cenários da seção 3

| Cenário | Receita | Custos (seção 3) | Sobra depois de imposto e taxa (10–20%) |
|---|---|---|---|
| Escala pequena: 300 vídeos de 5 s em 480p | R$ 375 | ~R$ 136 | ~R$ 160–200 |
| Demanda alta: 18 mil de 5 s em 480p e 12 mil de 5 s em 720p | R$ 52.500 | ~R$ 7.400 | ~R$ 34.600–39.850 |

### 4.4 Por que esses valores

- **No nível do atacado, entregando o serviço completo:** quem vende só o modelo pronto, para programadores e em dólar, cobra de US$ 0,015 a 0,05 por segundo em 480p (Wan 2.2 e 3.0) e ~US$ 0,10/s em 720p (pesquisa de 09/10/2026; os preços mudam com frequência). O Render2Go cobra o mesmo e entrega mais: português, estilos prontos, revisão, lote e suporte.
- **Para o cliente, continua barato:** 50 fotos viram 50 vídeos de 5 s por R$ 62,50 em 480p ou R$ 125 em 720p, contra R$ 25 a 50 por vídeo com freelancer (`docs/05`).
- **A GPU fica entre 9% e 16% do preço.** Sobra espaço para uma alta do RunPod como a da RTX PRO 6000 (+19%) sem mudar os preços.
- **Burlar o teste não compensa:** o teste vale R$ 3,75 (5 vídeos de 3 s em 480p) e tem limites por dispositivo, por IP e um pedido só.

### 4.5 O que ainda falta

1. **Pagamento automático:** Pix e cartão por um intermediador brasileiro (ex.: Mercado Pago, Asaas ou Pagar.me), com um aviso (webhook) que lança a recarga. O identificador do pagamento já impede que o mesmo pagamento entre duas vezes.
2. **Termos de uso e política de privacidade (LGPD):** incluir as regras do saldo (validade, devolução de saldo não usado) e confirmar com um advogado e com o contador.
3. **Marca d'água nos vídeos do teste gratuito** (logo e nome no fim): sugerida em 09/10/2026, ainda não feita.
