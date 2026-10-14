# Simulação da fila e dos pods (14/10/2026)

**Não é medição.** `render2go_servico.simulacao` (eventos discretos) roda as decisões do código (`capacidade.trabalho`, `pods_desejados`, `equivalentes`, `PoliticaOciosidade`, o teto por hora do ADR 0016 e a fila intercalada por cliente) sobre tempos tirados das medições registradas, com variação aleatória por semente:

| Parâmetro | Valor | De onde |
|---|---|---|
| subida fria até o PRONTO | 195–260 s (uniforme) | 13/10: 195–221 s; 08/10: 4,2–5,1 min até o 1º vídeo |
| serviços no ar antes do PRONTO (tradução durante o download) | 120 s | `subir_pod(cedo=True)` |
| vídeo de 5 s | tabela `lote_nuvem.MEDIDO_S` (L40S 55 s em 480p, 180 s em 720p), ±8% | gpu-2026-10-06*, gpu-2026-10-13-qualidade (52,0 e 172,7 s) |
| 1ª tradução no pod / seguintes | 60 s / 5 s | 13/10: 57,5 s e ~4,5 s |
| conferência + filtro; envio | 4 s; 1,5 s | ordem de grandeza dos logs do worker |
| do DELETE ao fim observado | 5–20 s | sem medida: suposição |
| preços | L40S US$ 1,09/h; RTX PRO 6000 US$ 2,49/h | docs/11 |

```
cd servico && uv run python -m render2go_servico.simulacao --sementes 30 --json ../worker/evals/simulacao-2026-10-14/resultado.json
```

- `resultado.json`: as linhas (30 sementes por cenário, somadas); `tabela.md`: a mesma coisa em tabela.
- O p95 só aparece com n ≥ 20 (n = pedidos somados nas sementes); com menos, "(n<20)".
- Os números servem para **comparar** políticas sob as mesmas premissas; não prometem tempos nem custos de produção. A conclusão está no `docs/15`, seção 3.
