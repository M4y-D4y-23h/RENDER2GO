# 16 — Piloto e lançamento (17/10/2026)

Como abrir o Render2Go para os primeiros clientes com entregas confiáveis, pouco trabalho para o cliente e fidelidade ao pedido: o que conferir antes, o ensaio completo, o que medir, o suporte e os limites. As mudanças de código estão no [ADR 0018](adr/0018-versoes-do-pedido-e-revisao-pelo-cliente.md) (proposto) e na entrada 17/10/2026 do `docs/09`. **Na `main` desde 17/10/2026, mas ainda não testado em produção nem em GPU:** os números abaixo são os já medidos em rodadas anteriores (com a fonte) ou ficam em branco para o piloto preencher.

## 1. O que o piloto oferece (e o que não oferece)

- **Disponível:** "Foto vira vídeo" (Wan 2.2 I2V, a produção de sempre, ADR 0001 e 0014): um vídeo curto, sem som, por foto, a partir de um texto em português, com o cartão "Entendemos seu pedido assim", a revisão opcional antes de gerar, o retorno sobre cada vídeo e o pedido de correção.
- **Texto-guia da oferta** (página inicial): "Transforme fotos do seu negócio em vídeos curtos para divulgação, com pedido organizado, adaptação visível e conferência do resultado."
- **Não prometer:** edição geral da foto (trocar cor, fundo, rótulo), resultado perfeito, gerações ilimitadas, texto, preço ou logotipo escritos pelo gerador, som. O cartão mostra os limites do vídeo em cada pedido.
- **Texto, preço e logotipo exatos:** pela Montagem (ADR 0015), que continua só no plano interno (`RENDER2GO_MONTAGEM=interno`) até cumprir o "Antes de ligar para os clientes" do ADR 0015. A pendência do texto diz "quando ela estiver disponível na sua conta".
- **Em breve** (catálogo de finalidades, Mix, Projetos): continua secundário, com "Tenho interesse"; nenhum item do catálogo é ligado sem o pipeline funcionando de ponta a ponta.

## 2. Antes de abrir (lista do dono)

1. **Versão publicada:** no servidor, `git log -1 --oneline` é o commit da entrada 17/10/2026 do `docs/09` (ou o merge dela na `main`), e `curl -s https://app.render2go.com/saude` responde `{"ok": true}`.
2. **Migrações:** a API cria sozinha as tabelas `versoes`, `criterios_avaliados`, `retornos` e `correcoes` e as colunas novas na partida. Conferir: `docker compose exec -T postgres psql -U gpu2go gpu2go -c "\dt"` lista as quatro; `... -c "\d lotes"` tem `versao_atual`, `versao_liberada`, `decisao_em`, `revisao`.
3. **`.env`:** `RENDER2GO_REVISAO_CLIENTE` (padrão 1: o modo "Quero revisar antes de gerar" aparece), `RENDER2GO_CORRECOES` (padrão 1), `RENDER2GO_CORRECAO_PRAZO_H` (padrão 72; de 1 a 720, fora disso a API não sobe). `RENDER2GO_LEGENDA_POR_FOTO=0` (o experimento do ADR 0014 continua desligado).
4. **Privacidade v6:** a página `/privacidade` mostra "Versão privacidade-v6"; quem entra aceita de novo (comportamento normal de versão nova).
5. **Backup e restauração:** o `pg_dump` da entrada 11/10/2026 (2) do `docs/09`, bloco 2, antes da atualização; **restaurar uma vez num banco de teste** antes do piloto (`gunzip -c backup.sql.gz | psql` num Postgres vazio, e a API de teste apontada para ele sobe e lista os pedidos). Backup diário enquanto durar o piloto.
6. **Alertas:** o painel, seção Pods (sinal de vida do orquestrador, saldo do RunPod, teto por hora, ADR 0016) e GPUs; o e-mail do suporte (`RENDER2GO_SUPORTE_PARA`) chegando; `docker compose logs --since 1h api orquestrador | grep -i aviso` uma vez por dia.
7. **Teste de invasão** (`docs/13`, seção 3) com a versão nova, antes do primeiro cliente: as rotas novas do cliente (`/api/videos/{id}/retorno`, `/correcao`, `/api/lotes/{id}/versao/aprovar`, `/texto`, `/api/pedido/entendimento`) e do painel (`entregas`, `jobs/{id}/video`, `jobs/{id}/criterios`, `correcoes/{id}/decidir`).

## 3. Ensaio completo (antes do 1º cliente)

Uma conta de teste do plano pago, com saldo, e o painel aberto em outra janela. Anotar o tempo e qualquer erro de cada passo.

| # | Passo | O que conferir |
|---|-------|----------------|
| 1 | Entrar com um e-mail aceito (código de 8 dígitos) | aceite da privacidade v6; o topo mostra a conta |
| 2 | Recarga por Pix (menor valor) | o saldo entra só com a confirmação do Mercado Pago; o extrato mostra a recarga |
| 3 | Pedido "seguir": 3 fotos, "Gire lentamente o produto.", detalhamento com "Sem pessoas na cena" e "O que precisa continuar igual" | "Ver como vamos entender" mostra a ação e a restrição; o pedido sai sem pendência |
| 4 | Revisão de conteúdo no painel (Revisão → aprovar) | os vídeos começam sem clique do cliente; o cartão mostra "Seguindo a adaptação" e o texto usado na geração (versão e hash) |
| 5 | Pedido "revisar": 2 fotos, texto ambíguo ("que a marca resplenda") | a pendência aparece; "Seguir com o texto como está" cria o pedido; depois da revisão de conteúdo e da tradução, "Esperando a sua aprovação"; **nenhum pod sobe por ele** (seção Pods) |
| 6 | Abrir o pedido 5 em duas abas; mudar o texto numa; aprovar na outra | a outra aba recebe "O pedido mudou depois que você abriu esta versão"; nada é gerado até aprovar a versão nova |
| 7 | Aprovar a versão nova | os vídeos entram na fila; o cartão mostra "Aprovado por você" |
| 8 | Baixar um vídeo; "Pode usar como está?" → "Não, precisa mudar"; "Pedir correção" | o painel, seção Entregas, mostra a correção aberta e o retorno |
| 9 | No painel, "Revisar" o vídeo: cada critério com o estado e a evidência; decidir a correção (dono) com crédito | a situação muda; o extrato do cliente mostra "Crédito"; uma 2ª decisão não credita de novo |
| 10 | Parar um pedido em andamento | o que falta é cancelado sem custo; o vídeo que já está num pod termina |
| 11 | Deixar um pedido "revisar" sem resposta | ele não segura pod; vence com as fotos (6,5 dias), sem custo (no ensaio, conferir só que nenhum pod fica ligado por ele) |

Se um passo falhar, o piloto não abre: corrigir, publicar e repetir o ensaio inteiro.

## 4. O que medir (painel, seção Entregas, "Piloto, últimos 14 dias")

O painel calcula só o que o banco sabe (`Banco.metricas_piloto`); o resto vai à mão numa planilha do piloto, semana a semana.

| Métrica | De onde vem | Observação |
|---------|-------------|------------|
| 1ª entrega utilizável | pedidos em que o cliente respondeu sobre todos os vídeos e todos "Pode usar como está" | os pedidos sem resposta não entram (e aparecem em "sem retorno") |
| Requisitos obrigatórios atendidos | a revisão da equipe (critérios com evidência): atende, em parte, não atende, não verificado | **revisar o vídeo inteiro de todos os pedidos do piloto**; "não verificado" nunca conta como aprovado |
| Resultados sem avaliação | vídeos entregues sem retorno do cliente e vídeos sem decisão da equipe | meta do piloto: zero vídeos sem decisão da equipe |
| Correções e motivos | correções por classificação; retornos por motivo | a classificação é da equipe; o motivo, do cliente |
| Tempo ativo do cliente | **não medido** | anotar no ensaio e em 3 pedidos acompanhados (do 1º clique ao "Enviar pedido", e o tempo de aprovação) |
| Tempo total até uma peça utilizável | da criação do pedido à entrega do vídeo marcado "pode usar" (mediana) | é tempo de relógio, com a revisão de conteúdo e a espera do cliente |
| Custo por entrega aprovada | GPU do período ÷ vídeos marcados "pode usar" (o painel dá a parte da GPU) | somar à mão: tentativas a mais (já estão no custo de GPU), subida e ociosidade (idem), armazenamento (R2), taxas do Pix e o tempo da equipe (revisão de conteúdo, revisão do piloto, correções) × custo da hora |
| Divergências pedido × adaptação × execução | decisões pedidas por mudança na adaptação, por motivo (`substituiu`, `restricoes`, `pessoa_inventada`, `prompt_diferente`) e os critérios "não atendido" | cada uma é um caso para olhar: o texto, o prompt usado e o vídeo |

Os avaliadores automáticos novos (nenhum ainda, além da medida de cor que já existe) entram **só em observação**: aparecem na janela do vídeo, sem decidir e sem nota de confiança, até uma rodada medida com critérios escritos antes decidir o contrário.

## 5. Suporte e correções

- O cliente fala pelo `/suporte` (e-mail) e, sobre um vídeo, pelo "O que precisa mudar?" e "Pedir correção" (uma por vídeo, até 72 h depois da entrega; a proposta, ainda a decidir).
- **Classificação** (seção Entregas): falha técnica (defeito do vídeo), fora do combinado (um critério obrigatório da versão aprovada não foi atendido) ou pedido novo (o cliente quer algo que não estava no pedido). Na dúvida entre "fora do combinado" e "pedido novo", olhar a versão aprovada ("Ver texto usado na geração" e os critérios dela).
- **Crédito:** só o dono, nas duas primeiras classificações, do valor cobrado do vídeo, uma vez só. A cobrança do vídeo não muda (ADR 0012).
- **Refazer:** o cliente envia a foto de novo num pedido novo (as fotos do pedido são apagadas quando ele termina; não há retenção nova). O crédito paga o vídeo novo.
- **Responder** o cliente em até 1 dia útil no piloto (a resposta aparece no próprio vídeo; o suporte por e-mail para o resto).

## 6. Empresas com e-mail de domínio próprio

A entrada pela página aceita só os provedores confiáveis (Gmail, Outlook/Hotmail, Yahoo, iCloud, UOL, BOL, Terra; ADR 0007). Para uma empresa do piloto com e-mail do próprio domínio: o dono cria a conta no painel (Contas e saldo → nova conta por e-mail; o painel aceita e-mail de empresa) e a pessoa entra pelo código que chega nesse e-mail. Conferir antes que o domínio recebe os e-mails do Amazon SES (fora do sandbox, `docs/13`). A outra saída, `RENDER2GO_EMAIL_DOMINIOS_EXTRA` (acrescenta o domínio à entrada pública, com o teste gratuito para cada caixa dele), fica para depois do piloto: a conta criada pelo painel não abre o teste gratuito para o domínio inteiro.

## 7. Limites do piloto

- **GPU:** até `RENDER2GO_MAX_PODS` pods e `RENDER2GO_MAX_USD_H` por hora (ADR 0016); a subida medida do pod até o 1º vídeo foi de 4,2–5,1 min (`worker/evals/gpu-2026-10-08-subida/`) e o vídeo, ~52 s em 480p e ~173 s em 720p na L40S (mediana de 13/10, ADR 0017). Um pedido "revisar" sem resposta não segura pod.
- **Revisão humana:** cada pedido passa pela revisão de conteúdo (ADR 0004) e, no piloto, cada vídeo entregue pela revisão do piloto (o vídeo inteiro, os critérios com a evidência). O limite do piloto é o tempo de quem revisa: **definir antes de abrir** quantos pedidos por dia uma pessoa revisa (medir no ensaio o tempo por vídeo) e não aceitar mais clientes do que isso cobre. Até lá, convidar poucas empresas, uma de cada vez.
- **Fila:** os pedidos de uma conta saem em ordem; entre contas, intercalados (ADR 0012); o pedido que espera o cliente não segura o próximo da mesma conta.

## 8. O que ainda fica para decidir

- A política de revisões (uma correção por vídeo e 72 h são a proposta) e se o crédito pode ser dado sem o pedido do cliente.
- Quem revisa no piloto, quantos vídeos por dia e por quanto tempo a revisão do vídeo inteiro continua obrigatória.
- O custo da hora da equipe para o "custo por entrega aprovada".
- Quando um avaliador automático sai da observação (rodada medida, critérios escritos antes).
- A abertura para clientes depois da atualização do servidor (o `docs/09`, entrada 17/10/2026) e do ensaio da seção 3.
