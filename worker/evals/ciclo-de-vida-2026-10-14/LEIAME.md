# Ciclo de vida dos pods: reproduções de 14/10/2026

Reproduções **locais**, com provedores falsos e sem rede: mostram o comportamento do código, não incidentes medidos em produção. Nenhum pod foi criado.

- `reproduzir.py`: os dois achados da revisão de 14/10/2026 (`docs/15`, seção 1; ADR 0016).
- `antes.json`: saída no commit `0a8149b` (a `main` da revisão), rodada num `git worktree` daquele commit.
- `depois.json`: saída nesta branch.

| Achado | Antes (`0a8149b`) | Depois |
|---|---|---|
| `_encerrar` com o `DELETE` falhando (`OSError`), chamado 2 vezes | 1 chamada ao provedor; `pods.json` vazio; `vivo=False` | 2 chamadas; o pod fica no `pods.json` com `encerramento: falhou`, `estado: encerrando` e o erro |
| `_retomar_algum` 3 vezes no mesmo episódio de demanda, host sem GPU livre | falhas 1, 2 e o pod descartado (encerrado com o disco de volume); 3 chamadas ao `retomar` | falhas 1, 1, 1; 1 chamada ao `retomar` (as outras esperam o intervalo); o pod continua no conjunto |

Os mesmos casos, e os outros da revisão (orçamento com pods drenando, reinício, fechamento concorrente, 404, respostas inconclusivas, retomadas simultâneas, corrupção do volume), estão nos testes `worker/tests/test_ciclo_de_vida.py` e `servico/tests/test_orquestrador_ciclo.py` (SQLite e Postgres), que falhavam no `0a8149b` e passam nesta branch.
