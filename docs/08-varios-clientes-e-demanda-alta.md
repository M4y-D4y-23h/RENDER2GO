# 08 — Vários clientes ao mesmo tempo: fila, escala e isolamento

Análise de 07/10/2026 para a fase 3 (ADR 0003): como atender muitos pedidos simultâneos, por exemplo 10 testes gratuitos de 10 fotos, sem atrasar, sem estourar o custo e **sem nunca entregar a um cliente o arquivo de outro**. Os números vêm de `docs/07` e de `worker/evals/gpu-2026-10-07-volume/`.

## 1. O cenário de referência: 10 clientes × 10 fotos (teste gratuito)

Desde 09/10/2026 o teste gratuito vale até 5 fotos (`docs/09`, seção 5.1); o cenário de 10 fotos continua servindo de referência para a fila e o custo.

- **Teste:** qualidade rápida, vídeos de **3 s**, ~12 s de GPU por vídeo na RTX PRO 6000 (28 s × (3/5)^1,6).
- **Trabalho total:** 100 vídeos ≈ 21 min de GPU ≈ **US$ 0,72**.
- **Subida de cada pod:** ~3 min, ~US$ 0,10 (medido em 07/10, com download em partes).

| Pods | Tudo pronto em | Custo total | Por cliente |
|---|---|---|---|
| 2 | ~14 min | ~US$ 0,93 | US$ 0,09 |
| **3** | **~10 min** | **~US$ 1,03** | **US$ 0,10** |
| **4** | **~8–9 min** | **~US$ 1,14** | **US$ 0,11** |
| 10 (um por cliente) | ~5 min | ~US$ 1,77 | US$ 0,18 |

**Melhor cenário:** uma **fila única** com 3–4 pods, intercalando os clientes, de modo que o vídeo 1 de cada um sai antes do vídeo 2 de qualquer um. O primeiro vídeo de cada cliente chega em ~4–5 min e o lote inteiro em ~10 min.

## 2. Como garantir que cada cliente receba só os próprios vídeos

São camadas independentes: uma falha numa delas não basta para trocar arquivos.

1. **O destino é decidido pelo servidor, nunca pelo pod nem pelo cliente.** Todo arquivo tem um endereço `entrada/<cliente>/<lote>/<foto>` ou `saida/<cliente>/<lote>/<vídeo>` (`servico/armazenamento.py`).
   - O pod recebe uma **URL assinada de envio para um único endereço**, válida por 1 h. Ele não tem como gravar em outro lugar.
   - Já está no rascunho da API: `/api/worker/reivindicar`.
2. **Um job = uma foto de um lote**, numa linha do banco: lote, foto, arquivo de saída e semente.
   - O pod só conclui um job que esteja com ele (`db.concluir` confere o worker).
   - Job abandonado volta para a fila sozinho (prazo de 15 min, até 3 tentativas).
3. **O cliente só enxerga o que é dele.** Toda consulta é `lote(id, cliente_id)`: o lote de outro responde "não encontrado". Os links de download são assinados por arquivo e expiram em 24 h.
4. **No pod, cada vídeo é buscado pelo ID do próprio pedido no ComfyUI** (`/history/<prompt_id>`, `comfy_client.py`), não pelo "último arquivo da pasta". No worker da fila (a fazer), as pastas de entrada e saída serão limpas entre um job e outro.
5. **Conferência automática nova (proposta, já validada nos vídeos de 07/10):**
   - No I2V, o 1º quadro do vídeo é a própria foto de entrada. Antes de marcar o job como "ok", o servidor compara esse quadro com a foto do job.
   - Nos 16 vídeos de 07/10, a similaridade deu **1,00 com a foto certa** e **no máximo 0,28 com qualquer outra**.
   - Com um limite de ~0,9, um vídeo trocado não tem como passar: ele vira erro e é refeito.
6. **Registro de tudo:** cada envio, aprovação, compilação e conclusão vira um evento no banco (`eventos`), com o lote e o worker. Se algo der errado, dá para rastrear.

Os itens 1 a 3 e 6 já estão no código da fase 3 (rascunho, em pausa); o 4 está no worker atual. O item 5 é novo, cerca de 30 linhas, e pode entrar no "concluir".

## 3. Demanda alta: como dar conta sem perder o controle do custo

- **Pods automáticos pelo tamanho da fila:**
  - pods = trabalho na fila ÷ prazo desejado (ex.: 15 min), com mínimo de 1 e um teto (custo por hora que o saldo aguenta);
  - cada pod precisa ter trabalho para pelo menos ~3× a subida (~10 min); senão, o trabalho vai para menos pods. Já feito no `lote_nuvem` (`--pods auto`, padrão, e `--prazo`).
- **Sem volume quando a demanda é alta:** o volume prende o lote a um datacenter e ganha só ~14 s. Sem ele, os pods sobem onde houver vaga, em qualquer datacenter bom, seguindo a ordem L40S → RTX 6000 Ada → RTX PRO 6000 → RTX 4090 (desde 08/10/2026; `docs/07`, seção 1).
- **Pod quente:** quando a fila esvazia, o pod espera até ~3 min (o tempo de uma subida) antes de desligar. Pedidos em sequência saem sem subida nenhuma.
- **Prioridade:** pacotes pagos na frente; testes gratuitos intercalados entre si, numa faixa separada. No mesmo cliente, os pedidos saem na ordem de chegada.
- **Limites por cliente:** cota diária de fotos (já existe: `cota_fotos_dia`) e um teste gratuito por cliente.
- **Prazo visível:** o cliente vê a posição na fila e o tempo estimado.
- **Limite da conta no RunPod:** o número de GPUs simultâneas e o saldo limitam a escala; a regra de saldo já trava antes de gastar o que não existe. Para picos maiores, recarregar o saldo e pedir aumento de limite.
- **Sempre valendo:** desligamento automático de cada pod (no máximo 2 h), prazo por job e fila no Postgres com `FOR UPDATE SKIP LOCKED`, para dois pods nunca pegarem o mesmo job.

## 4. Implementado na fase 3 (07/10/2026)

1. **Clientes intercalados na fila:** `db.reivindicar` dá a vez ao cliente com menos vídeos já iniciados nos pedidos em andamento, e lotes pagos passam na frente de testes gratuitos. Desde 08/10/2026 a vez é por **cliente**, não por pedido: o pedido novo de uma conta espera o anterior da mesma conta (antes ele passava na frente).
2. **Conferência do 1º quadro e da continuidade:**
   - o worker só envia o vídeo que bate com a foto (`render2go_worker/conferencia.py`);
   - a API recusa "ok" sem a similaridade ou sem o vídeo no armazenamento, apaga o vídeo e repete o job.
   - **Desde 09/10/2026, o vídeo inteiro:** no teste de 08/10, 2 de 5 vídeos começaram na foto e trocaram de carro no meio (corte e fusão). O worker mede a cor média do centro de cada quadro: deriva para o 1º quadro até 40 e salto entre quadros vizinhos até 12 (calibrado em 39 vídeos: os bons ficaram até 31,5 e 5,8; as trocas deram 54 e 86 de deriva e 16,2 de salto no corte). O vídeo reprovado é refeito com outra seed; a API recusa "ok" sem as medidas. A troca por um produto da mesma cor ainda passa (`docs/09`, seção 5.1.9; calibração em `worker/evals/conferencia-2026-10-09/`).
3. **Pods automáticos pelo tamanho da fila e pod quente:**
   - o orquestrador (`servico/.../orquestrador.py`) usa a mesma regra do `lote_nuvem --pods auto`;
   - o worker sai após 3 min sem jobs, e o pod é encerrado.
4. **Worker da fila, orquestrador, páginas e implantação:**
   - worker da fila: `render2go_worker/fila.py`;
   - páginas: `/` e `/aprovar`;
   - Dockerfile, CI e `docs/09-implantacao.md`.

5. **Teste gratuito barato e sem abuso** (08/10/2026):
   - um pedido só, 480p e 3 s, com até 5 fotos (eram 10 até 09/10/2026);
   - até 5 fotos de teste por dispositivo e 50 por IP;
   - testes esperam juntar 20 vídeos ou 10 min antes de subir um pod (`docs/09`, seção 5.1).
6. **Fotos de entrada apagadas quando o lote fecha** (`docs/09`, seção 5.2).

**Medido em 08/10/2026, no servidor de produção:** 1 lote de teste com 9 fotos.
- Pod pronto em 2 min 40 s (instalação de 78 s).
- 9 vídeos de 3 s conferidos.
- Pod encerrado 3,5 min depois do último vídeo.
- US$ 0,24 no total.

7. **Previsão com o estado real dos pods** (08/10/2026, noite): o orquestrador registra cada pod na tabela `geradores`, e a previsão do cliente usa a subida medida e os vídeos à frente na mesma ordem da fila (`docs/09`, seção 5.1.3).
8. **Subida mais rápida** (08/10/2026, noite): modelos só do espelho no R2 quando ele passa no teste de rede, serviços e tradução do pedido durante o download, ciclo de 10 s e consultas de 3 s. Medido em pods reais: 4,2–5,1 min do pedido do pod ao 1º vídeo, contra 6,8 min (`docs/07`, seção 9).

**Ainda por fazer:** medir em pods reais a fila com vários clientes ao mesmo tempo.
