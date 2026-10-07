# Subida do pod: download em partes e cache no volume (07/10/2026, noite)

- **Como rodou:** `tools/lote_nuvem.py` com `--volume` (volume de rede `gpu2go-cache`, 60 GB, US-NC-2), 2 fotos por pod, qualidade rápida, vídeos de 3 s. RTX PRO 6000 secure, US$ 2,09/h.
- **Pod 1** (`pod1-enche-cache.log`): volume vazio. Baixou tudo da origem com o download novo (aria2, 8 conexões por arquivo) e gravou o cache no volume depois do PRONTO, enquanto renderizava.
- **Pod 2** (`pod2-do-cache.log`): copiou modelos, ambiente e compilador do volume para o disco local, conferindo o SHA-256.

## Tempos da instalação (segundos desde o início do `bootstrap_pod.sh`)

| Etapa | Antes (07/10, manhã, US-NC-1) | Pod 1: download em partes | Pod 2: do volume |
|---|---|---|---|
| apt | — | 14 | 13 |
| Ambiente (venv, ComfyUI, Ollama) | ~3–4 min | 51 (instalado) + 15 (empacotado) | 45 (do volume) |
| Modelos, 37 GB | ~4 min (~140 MB/s) | 63 (~750 MB/s) | 77 (~580 MB/s) |
| Conferência SHA-256 | sequencial | 91 (em paralelo) | junto da cópia |
| **Instalação pronta** | **5,0–5,9 min** | **91 s** | **77 s** |
| Do comando ao pod pronto | 5,7–6,5 min | 3,1 min | 2,5 min |
| Lote inteiro (2 vídeos de 3 s) | — | 4,4 min, ~US$ 0,15 | 3,8 min, ~US$ 0,13 |

## Conclusões

- **O ganho grande foi o download em partes:** a instalação caiu de ~5,5 min para ~1,5 min. A rede do host aguentava ~750 MB/s; antes, os 2 arquivos de 15 GB vinham numa conexão cada.
- **O cache no volume ganhou só ~14 s** sobre isso: o volume leu a ~580 MB/s, mais devagar que a origem nesse host.
- **O cache continua útil como reserva:** a subida não depende do Hugging Face, do GitHub e do PyPI (fora do ar ou lentos), nem da rede do host. Mas ele:
  - prende o lote ao datacenter do volume, o que dá menos vagas de GPU;
  - custa ~US$ 4,20 por mês (60 GB).
- **A subida inteira (bandeirada) caiu de ~7 min para ~3 min:** ~US$ 0,10 por pod na RTX PRO 6000, contra ~US$ 0,22 antes.
- **Proteções testadas antes, sem GPU (volume simulado):**
  - cópia de modelo corrompida no volume: detectada pelo SHA-256, baixada de novo e regravada;
  - ambiente corrompido: detectado, reinstalado e regravado.
- **Custo da rodada:** ~US$ 0,28 em pods, mais o volume.
