# Sessão em GPU de 05/10/2026

RTX 4090 secure no RunPod (Irlanda): driver 570.195.03, 95 GB de RAM no cgroup, disco local de 170 GB. O ambiente repetiu os passos do `worker/Dockerfile` (alvos `slim` e `bakeoff`) sobre `python:3.12-slim`: mesmas versões, hashes e commits. Os modelos foram baixados do Hugging Face nas revisões do `models.lock.json`, a ~500 MB/s, e conferidos por bytes e SHA-256. Custo total, incluindo uma primeira tentativa num pod community com defeito: ~US$ 2,08. Só conteúdo SFW.

## Aceite de tempo da Etapa 1 (I2V R1 v2, 640×640, 5 s)

| Rodada | Compilação | Render | Total |
|---|---|---|---|
| Fria | 3,3 s | 62,4 s | 65,7 s |
| Quente | 3,3 s | 56,2 s | 59,5 s |

**O critério de ≤ ~40 s não foi atingido.** O pipeline manual tinha medido 37,7 s.

- Cada uma das duas fases gasta ~11 s em "Model Initialization", porque os dois modelos de 14,8 GB não cabem juntos na VRAM e são trocados a cada vídeo. Os passos em si levam ~4–7 s.
- A/B das opções de carga do ComfyUI, 3 vídeos cada (`i2v/padrao-*`, `sem-*`):

| Opção | Tempo quente |
|---|---|
| Padrão | 55 s |
| `--disable-dynamic-vram` | 74–92 s (pior) |
| `--disable-fast-disk` | 55 s (igual) |

- Fica para a Etapa 3 (`tools/bench.py`): medir numa GPU de 32 GB, onde os dois modelos podem caber, e investigar a diferença para os 37,7 s do pipeline manual.

## Continuar vídeo

O clipe 3 foi gerado a partir do último quadro do clipe 1 (`--continue-from`), com render de 56,4 s. A concatenação sem recodificar deu 162 quadros (10,13 s), como esperado.

## Bake-off do T2V

Ver [ADR 0001](../../../docs/adr/0001-modelo-t2v.md). Os 36 vídeos não deram erro, e as notas humanas cegas estão pendentes.

## Turbo do I2V

1 seed (reduzido de 2 para caber no tempo do pod), 4 prompts, imagem de entrada = último quadro de um vídeo do bake-off (mulher de capa amarela, de costas).

| Variante | Tempo por vídeo | Movimento (YDIF médio) | Saturação (SATAVG) |
|---|---|---|---|
| 4 passos, CFG 1 (produção) | 57 s | 6,30 | 16,8 |
| 8 passos, CFG 1 | 100 s | 6,59 | 16,9 |
| 4 passos, CFG 1,5 | 101 s | 6,34 | 16,4 |
| 4 passos, CFG 2 | 101 s | 6,56 | 16,2 |
| 8 passos, CFG 1,5 | 188 s | 6,41 | 16,5 |

**Conclusão: o turbo fica em 4 passos e CFG 1.**

- As variantes custam de 1,75 a 3,3 vezes o tempo sem ganho mensurável de movimento (±5%), sem cores estouradas e sem melhor aderência.
- Em nenhuma das cinco a pessoa se virou, levantou os braços ou a câmera orbitou, porque a imagem de costas domina o vídeo. Isso confirma a seção 9.3 da especificação: no I2V, a pose vem da imagem.
- YDIF e SATAVG vêm do `signalstats` do ffmpeg. São medidas objetivas, mas grosseiras; as notas humanas estão na página de projeção cega.

## Achados de operação

- **Pod community com GPU quebrada:** o `nvidia-smi` funcionava, mas o `cuInit` falhava com `CUDA_ERROR_UNKNOWN` antes de qualquer código nosso. O `gpu_check.py` do worker detectou o defeito. Na Etapa 3, o handler deve testar o `cuInit` logo na subida e recusar a máquina.
- **O RunPod injeta `PUBLIC_KEY` em todo pod.** No modo `pod`, o entrypoint liga o `sshd` sozinho; na imagem do worker isso funciona porque ela tem `openssh-server`.
- **O RunPod injeta `RUNPOD_API_KEY` no pod**, e essa chave só funciona na API GraphQL. Ela foi usada para o desligamento automático do pod (`podTerminate`), útil também na Etapa 3.
- **Imagem base sem `NVIDIA_DRIVER_CAPABILITIES=compute,utility`:** o CUDA pode não ser montado no container. O Dockerfile do worker já declara a variável.
