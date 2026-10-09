# ADR 0008: recarga do pré-pago por Pix, no Mercado Pago

- **Status:** aceito (09/10/2026), a pedido do dono do projeto: escolher e implementar a API de pagamento antes do lançamento.
- **Contexto:** o pré-pago entrou em vigor em 09/10/2026 (`docs/11`, seção 4) com recarga manual: o cliente pagava por fora e o dono lançava o saldo no painel. Com o cadastro aberto (ADR 0007) e a divulgação em massa, a recarga precisa entrar sozinha e na hora, sem o dono no meio, e sem nenhum caminho para crédito sem pagamento.

## Decisão

1. **Pix no Mercado Pago**, pela API de pagamentos (`POST /v1/payments`, `payment_method_id: pix`):
   - o cliente escolhe o valor (de R$ 10 a R$ 2.000; `RENDER2GO_RECARGA_MIN` e `RENDER2GO_RECARGA_MAX`) e informa o CPF ou o CNPJ de quem paga, que o Mercado Pago exige. Os dígitos verificadores são conferidos aqui, e o banco guarda só os 4 últimos;
   - a página mostra o QR e o "copia e cola", que valem por 1 hora;
   - cada pedido vai com `X-Idempotency-Key` e `external_reference` iguais ao id da recarga: a mesma recarga nunca vira dois Pix;
   - até 3 recargas em aberto por conta e 10 pedidos por hora.
2. **O crédito só entra com a confirmação da API do Mercado Pago:**
   - o aviso (webhook) em `/api/pagamentos/mercadopago` só é aceito com a assinatura `x-signature` (HMAC-SHA256 com o segredo do aviso, sobre `id`, `x-request-id` e `ts`). Ele serve só de gatilho: a API consulta o pagamento no Mercado Pago e confere a situação, o valor, a moeda e a referência;
   - sem o aviso (atraso, falha de rede), a página pergunta a situação, e o servidor consulta o Mercado Pago do mesmo jeito, no máximo a cada 10 s por recarga;
   - o crédito entra uma vez só: o movimento leva a referência `pagamento:mercadopago:<id>`, única no banco, na mesma transação que marca a recarga como creditada;
   - valor diferente do pedido vira "divergente" e não credita; estorno ou contestação depois do crédito retira o valor (ajuste no extrato). O saldo nunca fica negativo: se o cliente já gastou, sai o que restar, e a recarga fica destacada no painel para o dono resolver;
   - a 1ª recarga tira a conta do teste gratuito (plano pago).
3. **Painel:** a seção "Recargas por Pix" mostra conta, valor, situação e o documento mascarado; "Conferir" pergunta de novo ao Mercado Pago. O lançamento manual (ajustes, pagamentos por fora) continua.

## Alternativas descartadas

- **Asaas, Efí (Gerencianet), PagBank ou Pagar.me:** também têm Pix com aviso. O Mercado Pago pesou pela marca que o pequeno comerciante já conhece (confiança na hora de pagar), pela conta PJ simples e pelo aviso assinado bem documentado. Trocar depois é uma mudança localizada: `pagamentos.py` (criar e consultar) e a rota do aviso.
- **Stripe:** forte em cartão internacional, que não é o caso; o Pix brasileiro é o centro aqui.
- **Cartão de crédito agora:** a contestação (chargeback) chega semanas depois, com o saldo já gasto em GPU, e cartão roubado é o caminho comum de fraude em créditos pré-pagos. Fica para depois, com 3-D Secure e limites por conta.
- **Checkout Pro (levar o cliente ao site do Mercado Pago):** mais simples, mas tira o cliente da página; o QR na própria página é mais rápido no celular.
- **Confiar só no aviso:** o aviso pode chegar repetido, atrasado ou forjado. Quem decide é a consulta à API.

## Consequências

- **Taxa:** cada Pix recebido paga a taxa do Mercado Pago, que depende do plano e da negociação da conta; as referências públicas vão de ~0,5% a ~1,2%. Confira a sua em "Seu negócio → Custos" e use-a no cálculo da margem (`docs/11`).
- **Chave Pix:** a conta do Mercado Pago precisa de uma chave Pix cadastrada, senão o Pix não é oferecido.
- **API em evolução:** o Mercado Pago está levando integrações novas para a "Orders API"; a API de pagamentos segue documentada. Se for preciso migrar, a mudança fica em `pagamentos.py`.
- **O que o dono precisa fazer** (conta, aplicação, token de produção, aviso com o evento "Pagamentos" e o segredo da assinatura): `docs/13`, seção 6.
- **Testes:** `servico/tests/test_pagamentos.py`, com um Mercado Pago falso (assinatura errada, aviso repetido, valor divergente, estorno, conferência sem aviso), e o teste de invasão (aviso sem assinatura e com assinatura de outro segredo).
