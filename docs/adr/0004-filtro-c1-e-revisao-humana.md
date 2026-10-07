# ADR 0004: filtro C1 e revisão humana no serviço SFW (fase 3)

- **Status:** proposto (07/10/2026). Aceitar é decisão do dono do projeto.
- **Contexto:** ADR 0003, fase 3 (API, fila, orquestrador e páginas: `servico/`). O doc 03 prevê o filtro C2, um classificador de texto não abliterado, que ainda não existe. A regra do projeto é fail-closed: worker sem filtros não processa jobs.

## Decisão

No serviço de marketing SFW, a segurança fica assim até o C2 existir:

1. **C1 em três pontos** (`gpu2go_worker/filtros.py`, listas em `filtros/c1.v1.json`):
   - na entrada do pedido, pela API;
   - no prompt compilado, pelo worker;
   - no prompt compilado de novo, pela API, antes de liberar os renders.

   Prompt recusado recusa o lote inteiro.
2. **Revisão humana de todo lote antes da GPU, no lugar do C2.**
   - Nenhum job nasce antes da aprovação (`db.aprovar`).
   - O aprovador vê, em `/aprovar`, as miniaturas, o pedido e o resultado do C1, e aprova ou recusa com motivo. O cliente vê o motivo.
   - A aprovação gera um carimbo HMAC que o worker pode conferir.
3. **Worker da fila só com `GPU2GO_SAFETY=c1-revisao-humana`.** O modo de testes SFW (`disabled-sfw-tests`) não sobe a fila.
4. **O compilador continua sem ser camada de segurança:** a regra `REFUSED` segue no system prompt, e a recusa dele vira erro do lote.
5. **Pessoas nas fotos:** receitas com pessoas exigem a caixa de autorização por escrito (`exige_consentimento`). O aprovador recusa quando a foto mostra pessoas e a caixa não foi marcada.
6. **Conferência do vídeo** (docs/08): só é entregue o vídeo cujo 1º quadro bate com a foto do job (similaridade ≥ 0,9). A API apaga e repete o que não bate.
7. **Retenção no R2, por regra de ciclo de vida do bucket:** fotos de entrada apagadas em 7 dias; vídeos e folhas de conferência, em 30 dias.
8. **Registro:** criação, envio, aprovação ou recusa (com quem), prompt, conclusão e falhas ficam na tabela `eventos`.

## Consequências

- **Atraso na entrega:** o tempo de revisão entra no prazo. Fora do horário de revisão, um teste gratuito espera até alguém aprovar. Opções futuras, cada uma com nova ADR:
  - aprovação automática de clientes pagos com histórico limpo;
  - aprovação automática quando o C2 existir.
- **Custo:** o revisor gasta ~10–30 s por lote. Um lote de 50 fotos vira uma tela com 50 miniaturas.
- **Fora do escopo da v1, e por isso fica proibido:**
  - qualquer uso adulto (D1 bloqueia);
  - foto de pessoa sem autorização;
  - fotos de menores (o C1 bloqueia termos; o aprovador recusa imagens).
- **Quando o C2 existir:** ele entra nos mesmos pontos do C1 (entrada e prompt compilado), e a revisão humana passa a amostral, com nova ADR.

## Alternativas consideradas

- **Rodar sem revisão, só com C1:** listas de termos não enxergam a imagem. Rejeitada.
- **Classificador de imagem no lugar da revisão humana:** ainda não escolhido nem validado em pt-BR. Fica para a Etapa 4.
