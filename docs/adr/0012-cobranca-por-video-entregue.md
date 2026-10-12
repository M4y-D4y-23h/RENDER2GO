# ADR 0012: cobrança por vídeo entregue, pedidos da mesma conta em ordem e pedidos sem limite de fotos

- **Status:** aceito (12/10/2026), a pedido do dono do projeto: "sem limite de fotos para contas pagas e internas; cada pedido processado só depois da conclusão do anterior; o valor descontado do saldo logo depois de cada arquivo ser processado e entregue corretamente", para que pedidos muito grandes sejam pagos aos poucos e o dinheiro das recargas vá pagando os pods.
- **Contexto:** desde 09/10/2026 (`docs/11`, seção 4), o pedido do pré-pago era cobrado inteiro quando as fotos terminavam de chegar, com estorno automático da recusa e de cada vídeo que não saía. Isso exigia o saldo do pedido inteiro antes de começar. Num pedido de milhares de fotos, o cliente teria de recarregar tudo de uma vez, e o dono só via o dinheiro voltar em estornos. Havia ainda dois limites: 1.000 fotos por pedido e uma cota de 500 fotos por dia por conta.

## Decisão

1. **Cada vídeo é cobrado quando sai certo.** Nos pedidos novos do plano pago (`lotes.cobranca = 'video'`), nada sai do saldo no envio. Quando o worker entrega um vídeo que passou na conferência da continuidade e no filtro de imagem, a API grava o movimento `video` (o preço de um vídeo, referência única `video:<job>`, na mesma transação da conclusão). O vídeo que falha, é barrado ou cancelado nunca é cobrado; o pedido recusado na revisão não custa nada. Não há mais estorno nesses pedidos.
2. **Saldo livre e reserva.** O saldo livre é o saldo menos o preço dos vídeos desses pedidos que estão num pod agora. A fila (`Banco.reivindicar`) só entrega um vídeo a um pod se o saldo livre da conta paga por ele, com a linha do cliente travada (`FOR UPDATE`) na mesma transação; senão, pula a conta e tenta a próxima. Assim, vários pods ao mesmo tempo nunca pegam mais vídeos do que o saldo paga (teste com 10 pods e saldo para 3).
3. **Sem saldo, o pedido espera.** O cliente vê "Aguardando saldo" no pedido; ao recarregar, ele continua sozinho de onde parou. O orquestrador só conta como trabalho os vídeos que o saldo livre paga (`Banco.trabalho`): pedido parado por saldo não liga nem segura pod.
4. **Pedidos da mesma conta em ordem.** Os vídeos de um pedido só começam quando o pedido anterior da mesma conta termina (`PEDIDO_ANTERIOR_ABERTO`); a tradução do texto do pedido seguinte pode adiantar, porque é rápida e não gera vídeo. Outras contas não esperam: a vez continua intercalada entre clientes.
5. **Envio.** Para criar o pedido, o saldo livre precisa pagar ao menos o 1º vídeo; a página mostra a estimativa do pedido inteiro e quantos vídeos o saldo atual paga.
6. **Sem limite de fotos** nos planos pago e interno: a cota diária fica só no teste gratuito (que tem o teto de 5 fotos), e o pedido grande vai em partes ("Pedido muito grande", em Consequências).
7. **Pedidos antigos.** Os pedidos criados antes desta mudança (`lotes.cobranca` vazio) seguem a regra anterior até o fim: cobrados no envio, com estorno.

## Consequências

- **Para o cliente:** paga só o que recebe e pode começar um pedido grande com pouco saldo. Os Termos de Uso mudam (item 5; `termos-v3`, aceite de novo na próxima entrada).
- **Para o dono:** o gasto aparece no extrato e no painel a cada vídeo entregue ("Cobrado hoje" sobe aos poucos). Uma rodada do extrato soma os vídeos entregues daquele pedido.
- **Pods:** o saldo do cliente decide quantos vídeos entram na conta do orquestrador. Com milhares de vídeos e saldo para todos, os pods sobem até `RENDER2GO_MAX_PODS`.
- **Risco aceito:** um ajuste manual negativo no painel, com vídeos em andamento, pode deixar o saldo abaixo de zero na entrega desses vídeos (a reserva já os contava). O vídeo é entregue e cobrado; o pedido espera a recarga.
- **Prazo das fotos:** as fotos ficam 7 dias no armazenamento. O pedido aberto há mais de 6,5 dias (esperando saldo, a revisão ou o pedido anterior da conta) fecha: o que não está num pod é cancelado sem custo e o cliente vê o motivo (`Banco.vencer_pedidos`, a cada ciclo do orquestrador e em cada `reivindicar`). Os Termos v3 dizem isso no item 5.
- **Pedido muito grande** (milhares de fotos): criado em partes de até 1.000 fotos (`POST /api/lotes` e `POST /api/lotes/{lote}/fotos`), sem limite total; a página cria cada parte só quando a anterior subiu (os links de envio valem 1 h). A lista de pedidos traz os 100 primeiros vídeos e as contagens, e o resto vem de 100 em 100; o revisor vê as 60 primeiras fotos e as marcadas pela análise, com os totais do pedido inteiro (CHANGELOG, 12/10/2026 (2)).

## Testes

`servico/tests/test_db.py`: cobrança só do entregue e uma vez só; pedido parado por saldo sem segurar pod e retomado com a recarga; pedidos da mesma conta em ordem com pods livres; 10 pods com saldo para 3 (falha no Postgres sem a trava do cliente). `servico/tests/test_api.py`: o fluxo inteiro pela API (preço por vídeo, nada no envio, recusa sem custo, vídeo entregue cobrado, pausa e volta com a recarga). Os testes do modo antigo continuam, com `cobranca="pedido"`. `servico/tests/test_pedido_grande.py` e, no navegador, `test_a_big_order_goes_up_in_parts_one_after_another`: o pedido sem limite, em partes.
