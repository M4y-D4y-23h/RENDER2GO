# Subida do pod até o 1º vídeo (08/10/2026, noite)

O dono do projeto viu ~6 min do pedido até 3 vídeos prontos, com o pod desligado. Três pods reais conferiram a subida nova e mediram cada etapa. Cada pod renderizou 1 vídeo de 3 s (480p) da foto `fixtures/sfw/produto-cafe.jpg`.

- **Como rodou:** `teste_cedo.py`, que é o `lote_nuvem` com a subida do worker da fila:
  - serviços e tradução do pedido com o estado `AMBIENTE`;
  - render só no `PRONTO`.
- **Pods:** nuvem secure, com desligamento automático, encerrados no fim.

## Tempos (segundos desde o pedido do pod)

| Etapa | Teste 1 (L40S) | Teste A (RTX PRO 6000) | Teste B (L40S) |
|---|---|---|---|
| Teste de rede | Hugging Face a 25 MB/s; espelho (R2) a 272 MB/s | Hugging Face a 39 MB/s; espelho a 307 MB/s | espelho a 73 MB/s (testado primeiro) |
| Origem dos modelos | Hugging Face e espelho misturados | só o espelho | só o espelho |
| Agente, CUDA e rede ok | 61 | 62 | 53 |
| Instalação: ambiente (venv, ComfyUI, Ollama) | 189 (PyPI e GitHub lentos) | 60 | 104 |
| Instalação: modelos baixados (37 GB) | 175 (~215 MB/s) | **44 (~925 MB/s)** | 123 (~310 MB/s) |
| Instalação pronta (`PRONTO`) | 221 | 94 (com 15 s de empacotamento, ver abaixo) | 160 |
| Serviços no ar | 314 | 185 | **213, ainda baixando os modelos** |
| Tradução do pedido pronta | 356 | 220 | 253 |
| **1º vídeo pronto** | **407 (6,8 min)** | **249 (4,2 min)** | **307 (5,1 min)** |
| Custo do pod | ~US$ 0,12 | ~US$ 0,80 (envio do ambiente, abaixo) | ~US$ 0,09 |

## O que ficou

- **Modelos só do espelho quando ele passa no teste de rede (`so_espelho`):**
  - no teste 1, com o Hugging Face e o espelho misturados, a origem lenta segurou o download: 175 s;
  - no teste A, só do espelho: 44 s.
  - Agora o espelho é testado primeiro. Aprovado (≥ 40 MB/s), a origem nem é testada. A origem continua como reserva no `curl` de cada arquivo e quando o espelho é reprovado.
- **Serviços e tradução durante o download (`AMBIENTE`):**
  - no teste B, o ComfyUI e o Ollama subiram com os modelos ainda baixando, e o pedido foi traduzido enquanto os modelos eram conferidos;
  - o 1º vídeo começou assim que o estado virou `PRONTO`;
  - ganho de ~25 s nesse host.
- **apt em duas partes:** os pacotes do download ficam prontos em 3 s, e o download começa logo.

## O que não ficou: o ambiente pronto no espelho

- **A ideia:** empacotar o venv, o ComfyUI e o Ollama num `tar.zst` e guardar no R2, para os próximos pods não dependerem do PyPI e do GitHub (189 s no teste 1).
- **O teste A:** o pod empacotou em 15 s, mas o envio ao R2 não terminou em 15 min, e o lote abortou o envio sozinho. O pod ficou ligado esse tempo (RTX PRO 6000, ~US$ 0,80).
- **O código saiu.** Se voltar, o arquivo deve ser montado e enviado de uma máquina com boa saída de rede (ex.: o CI), não de um pod.

## Para estimar

- **Do pedido do pod ao 1º vídeo começar:**
  - 3,7 min (teste A);
  - 4,2 min (teste B);
  - 6 min no host lento antes das mudanças (teste 1).
- **A API usa 4 min até ter 3 subidas medidas** na tabela `geradores`; depois, a mediana das últimas 10.
- **Pedido do dono (3 vídeos de 3 s):** ~5–6 min de ponta a ponta na L40S, contra 6+ min antes. O que domina é a subida.
