# 11 — Custos mensais e proposta de planos

Levantamento de 08/10/2026. Os custos vêm das medições do projeto (`docs/07`), dos preços do RunPod consultados pela API nesse dia e de preços públicos. A seção 4 é uma **proposta para aprovar**: nada dela está no código. Os planos que existem hoje continuam os mesmos: `teste`, `pago` e `interno`.

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

## 4. Proposta de planos (para aprovar; não aplicada)

### 4.1 Base de custo

- **1 crédito = 1 vídeo de 5 s em 480p.** O custo de referência é o que o dono mediu, **US$ 0,03 ≈ R$ 0,17 por crédito**, com folga sobre os ~US$ 0,02 esperados na L40S.
- **720p custa 3 créditos.** O tempo de GPU é ~3×.
- **7 s custa o dobro de créditos.** O tempo cresce ~1,7×.
- **Do preço de venda saem também:**
  - a taxa do meio de pagamento: ~1% no Pix, ~4–5% no cartão;
  - o imposto: Simples Nacional, de ~6% a 15,5% conforme o enquadramento;
  - os custos fixos (~R$ 43/mês);
  - o seu tempo de revisão.

### 4.2 Planos

| Plano | Preço | Créditos | Equivale a (5 s) | Preço por crédito | GPU (pior caso) | Sobra depois de GPU, taxa de 5% e imposto de 6–15,5% |
|---|---|---|---|---|---|---|
| **Teste grátis** | R$ 0 | — | 10 vídeos de 3 s em 480p, 1 pedido (como hoje) | — | ~R$ 0,77 por pessoa | — |
| **Avulso** (validade de 60 dias) | R$ 19,90 | 12 | 12 em 480p ou 4 em 720p | R$ 1,66 | R$ 2,05 | R$ 13,77–15,66 (69–79%) |
| **Essencial** (mensal) | R$ 49,90 | 40 | 40 em 480p ou 13 em 720p | R$ 1,25 | R$ 6,84 | R$ 32,83–37,57 (66–75%) |
| **Profissional** (mensal) | R$ 129,90 | 120 | 120 em 480p ou 40 em 720p | R$ 1,08 | R$ 20,52 | R$ 82,75–95,09 (64–73%) |
| **Agência** (mensal) | R$ 349,90 | 400 | 400 em 480p ou 133 em 720p | R$ 0,87 | R$ 68,40 | R$ 209,77–243,01 (60–69%) |
| **Crédito extra** | R$ 0,99 | 1 | 1 em 480p | R$ 0,99 | R$ 0,17 | ~70% |

- **Pior caso:** o cliente usa todos os créditos. Crédito que sobra no mês e não acumula aumenta a margem.
- **Ponto de equilíbrio da infraestrutura:** dois clientes do Essencial pagam os custos fixos (~R$ 43/mês).
- **Plano anual:** 2 meses grátis (pague 10, leve 12), para segurar o cliente.

### 4.3 O que muda entre os planos

| | Avulso | Essencial | Profissional | Agência |
|---|---|---|---|---|
| Fotos por pedido | até 20 | até 50 | até 100 | até 500 |
| Qualidade | 480p e 720p | 480p e 720p | 480p e 720p | 480p e 720p |
| Prioridade na fila | normal | normal | à frente | à frente |
| Revisão | em até 1 h útil | em até 1 h útil | em até 30 min úteis | em até 30 min úteis |
| Suporte | e-mail | e-mail | e-mail | e-mail e WhatsApp |

### 4.4 Por que esses valores

- **Em reais por vídeo**, os planos (R$ 0,87 a R$ 1,99) ficam abaixo dos créditos das ferramentas de vídeo com IA mais conhecidas, que cobram em dólar. Também ficam muito abaixo de um vídeo feito por freelancer (R$ 25 a 50, `docs/05`).
- **A vantagem do Render2Go** é o lote: 40 fotos, um pedido, 40 vídeos. O preço por crédito cai nos planos maiores para premiar quem manda volume, que é o público da seção 5.1 do `docs/05`.
- **A GPU fica entre 10% e 20% do preço** mesmo no pior caso. Sobra espaço para uma alta de preço do RunPod como a da RTX PRO 6000 (+19%) sem mexer nos planos.

### 4.5 O que fazer para aplicar (depois da aprovação)

1. Créditos por cliente no banco: saldo, validade e extrato.
2. Cobrança:
   - Pix e cartão por um intermediador brasileiro (ex.: Mercado Pago, Asaas ou Pagar.me);
   - webhook que credita o saldo;
   - página de planos.
3. Limites por plano na API: fotos por pedido, qualidade e prioridade na fila. A fila já separa pagos de testes.
4. Termos de uso e política de privacidade (LGPD), que já faltam hoje.
