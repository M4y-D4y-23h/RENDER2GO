# Bootstrap dos pods em contêiner, sem GPU (12/10/2026)

Confere, na imagem dos pods (`python:3.12-slim-bookworm`), as duas partes do `worker/scripts/bootstrap_pod.sh` que mudaram em 12/10/2026, sem RunPod e sem GPU:

- **download dos modelos:** um `aria2c` por arquivo (16 conexões), 4 arquivos ao mesmo tempo, cada um conferido (bytes e SHA-256 pelo `hashlib` do Python) logo depois do próprio download, e a velocidade da rede medida no próprio download (o teste de 15 s de antes saiu);
- **volta da hibernação (`retomada`):** tudo conferido no disco de volume antes de criar os links de `/opt`; o que não confere faz a subida do zero no disco do contêiner, sem tocar no volume (ADR 0011, atualização de 12/10).

## Como rodar

```bash
bash worker/evals/bootstrap-2026-10-12/teste_bootstrap.sh            # as duas partes
PARTES=volta bash worker/evals/bootstrap-2026-10-12/teste_bootstrap.sh  # só a volta
```

- Precisa de Docker. Gera ~2,2 GB de "modelos" aleatórios numa pasta temporária (apagada no fim): abaixo de 2 GB a baixar, o bootstrap não mede a rede (pouco dado para julgar).
- `servidor.py`: servidor HTTP local com `Range` (o `aria2c` só abre várias conexões com `Range`), nos papéis de origem (Hugging Face), espelho (R2), espelho corrompido (1º byte trocado) e arquivo ausente (404).
- `no_conteiner.sh`: carrega só as funções do bootstrap (`BOOTSTRAP_SO_FUNCOES=1`), troca a lista de modelos pela dos arquivos de teste e roda os casos.
- Atrás do proxy do ambiente de nuvem, o contêiner usa a rede do host e a CA do proxy (`/root/.ccr/ca-bundle.crt`), e o apt vai por HTTPS.

## Resultado (12/10/2026, 0 falhas)

### Download (4 arquivos: 1000 + 1000 + 200 MB e um JSON)

| Caso | Resultado | Conferido |
|---|---|---|
| tudo do espelho | ok em 10 s; rede medida: 223 MB/s | os 4 arquivos, com SHA-256 de verdade em disco; cada um conferido logo depois do próprio download (o menor em 0 s, os grandes em ~1 s) |
| espelho corrompido (m2 com 1 byte trocado) | ok em 11 s | o arquivo errado foi baixado de novo **da origem** (antes, do mesmo espelho) |
| espelho sem o arquivo (m3, 404) | ok em 13 s | o `curl` de reserva completou da origem |
| nada confere (origem e espelho errados) | falha (rc 1), como deve | o bootstrap não segue com modelo errado |

As velocidades são do servidor local dentro da mesma máquina: servem para conferir que a medida sai e é usada, não como número de rede de host.

### Volta da hibernação (disco de volume falso)

| Caso | Resultado | Conferido |
|---|---|---|
| volta certa | PRONTO; SHA-256 de tudo conferido em segundo plano | `/opt` aponta para o volume; as pastas de dados do ComfyUI (fotos e vídeos de clientes) ficam no contêiner; o marcador continua |
| volume incompleto (falta um modelo) | `retomada` devolve 1 e o estado continua INSTALANDO | **nenhum link criado**: a subida do zero segue no disco do contêiner, sem escrever no volume; não vira FALHOU |
| arquivo corrompido (bytes certos, SHA-256 errado) | FALHOU ("disco de volume com arquivo corrompido") | o worker para e o pod é encerrado, não hibernado; o marcador `instalado` sai |

## O que este teste não cobre

- Velocidade real de rede e de disco dos hosts do RunPod (fica na memória por datacenter do orquestrador, a cada subida de verdade).
- GPU, CUDA, ComfyUI e Ollama (ficam para o 1º pod depois da atualização: a seção GPUs do painel mostra o tempo até o 1º vídeo).
