| Cenário | Política | Entrega | 1º vídeo p50/p95 (s) | Pedido p50/p95 (s) | GPU ociosa (s) | Render desperdiçado (s) | Custo (US$) | US$/vídeo | Pods |
|---|---|---|---|---|---|---|---|---|---|
| fila pequena (1 pedido de 5 vídeos) | fixa 180 s (padrão) | nova (repete o mesmo vídeo) | 299 / 320 (n=30) | 517 / 544 (n=30) | 10899 | 0 | 6.36 | 0.0424 | 30 |
| fila pequena (1 pedido de 5 vídeos) | fixa 180 s (padrão); capacidade antiga | nova (repete o mesmo vídeo) | 299 / 320 (n=30) | 517 / 544 (n=30) | 10899 | 0 | 6.36 | 0.0424 | 30 |
| fila pequena (1 pedido de 5 vídeos) | fixa 60 s | nova (repete o mesmo vídeo) | 299 / 320 (n=30) | 517 / 544 (n=30) | 7299 | 0 | 5.27 | 0.0351 | 30 |
| fila pequena (1 pedido de 5 vídeos) | histerese 60/180 s | nova (repete o mesmo vídeo) | 299 / 320 (n=30) | 517 / 544 (n=30) | 7299 | 0 | 5.27 | 0.0351 | 30 |
| rajada (10 pedidos de 20 em 2 min) | fixa 180 s (padrão) | nova (repete o mesmo vídeo) | 671 / 1427 (n=300) | 1066 / 1561 (n=300) | 87442 | 0 | 130.72 | 0.0218 | 240 |
| rajada (10 pedidos de 20 em 2 min) | fixa 180 s (padrão); capacidade antiga | nova (repete o mesmo vídeo) | 671 / 1427 (n=300) | 1066 / 1561 (n=300) | 87442 | 0 | 130.72 | 0.0218 | 240 |
| rajada (10 pedidos de 20 em 2 min) | fixa 60 s | nova (repete o mesmo vídeo) | 671 / 1427 (n=300) | 1066 / 1561 (n=300) | 58658 | 0 | 122.01 | 0.0203 | 240 |
| rajada (10 pedidos de 20 em 2 min) | histerese 60/180 s | nova (repete o mesmo vídeo) | 671 / 1427 (n=300) | 1066 / 1561 (n=300) | 58658 | 0 | 122.01 | 0.0203 | 240 |
| chegada intermitente (24 pedidos de 3 a 10, ~15 min) | fixa 180 s (padrão) | nova (repete o mesmo vídeo) | 276 / 480 (n=720) | 585 / 1187 (n=720) | 148360 | 0 | 127.18 | 0.0285 | 387 |
| chegada intermitente (24 pedidos de 3 a 10, ~15 min) | fixa 60 s | nova (repete o mesmo vídeo) | 285 / 492 (n=720) | 641 / 1287 (n=720) | 106761 | 0 | 115.38 | 0.0258 | 438 |
| chegada intermitente (24 pedidos de 3 a 10, ~15 min) | histerese 60/180 s | nova (repete o mesmo vídeo) | 281 / 470 (n=720) | 644 / 1121 (n=720) | 141853 | 0 | 125.49 | 0.0281 | 401 |
| GPUs heterogêneas (L40S ou RTX PRO 6000) | fixa 180 s (padrão) | nova (repete o mesmo vídeo) | 548 / 1019 (n=300) | 846 / 1205 (n=300) | 87468 | 0 | 158.90 | 0.0265 | 240 |
| GPUs heterogêneas (L40S ou RTX PRO 6000) | fixa 180 s (padrão); capacidade antiga | nova (repete o mesmo vídeo) | 548 / 1019 (n=300) | 846 / 1205 (n=300) | 87468 | 0 | 158.90 | 0.0265 | 240 |
| GPUs heterogêneas (L40S ou RTX PRO 6000) | fixa 60 s | nova (repete o mesmo vídeo) | 548 / 1019 (n=300) | 846 / 1205 (n=300) | 58668 | 0 | 145.00 | 0.0242 | 240 |
| GPUs heterogêneas (L40S ou RTX PRO 6000) | histerese 60/180 s | nova (repete o mesmo vídeo) | 548 / 1019 (n=300) | 846 / 1205 (n=300) | 58668 | 0 | 145.00 | 0.0242 | 240 |
| foto bloqueada (metade recusada) | fixa 180 s (padrão) | nova (repete o mesmo vídeo) | 308 / 344 (n=60) | 776 / 843 (n=60) | 10832 | 0 | 9.37 | 0.0284 | 30 |
| foto bloqueada (metade recusada) | fixa 60 s | nova (repete o mesmo vídeo) | 308 / 344 (n=60) | 776 / 843 (n=60) | 7207 | 0 | 8.28 | 0.0251 | 30 |
| foto bloqueada (metade recusada) | histerese 60/180 s | nova (repete o mesmo vídeo) | 308 / 344 (n=60) | 776 / 843 (n=60) | 7207 | 0 | 8.28 | 0.0251 | 30 |
| tradução pendente (720p, 4 pedidos ao mesmo tempo) | fixa 180 s (padrão) | nova (repete o mesmo vídeo) | 414 / 456 (n=120) | 804 / 956 (n=120) | 55817 | 0 | 45.41 | 0.0946 | 150 |
| tradução pendente (720p, 4 pedidos ao mesmo tempo) | fixa 180 s (padrão); capacidade antiga | nova (repete o mesmo vídeo) | 411 / 444 (n=120) | 770 / 812 (n=120) | 68324 | 0 | 49.25 | 0.1026 | 180 |
| tradução pendente (720p, 4 pedidos ao mesmo tempo) | fixa 60 s | nova (repete o mesmo vídeo) | 414 / 456 (n=120) | 804 / 956 (n=120) | 37811 | 0 | 39.96 | 0.0833 | 150 |
| tradução pendente (720p, 4 pedidos ao mesmo tempo) | histerese 60/180 s | nova (repete o mesmo vídeo) | 414 / 456 (n=120) | 804 / 956 (n=120) | 37811 | 0 | 39.96 | 0.0833 | 150 |
| saldo insuficiente (paga 3 de 12) | fixa 180 s (padrão) | nova (repete o mesmo vídeo) | 294 / 320 (n=60) | 542 / 592 (n=60) | 10776 | 0 | 7.35 | 0.035 | 30 |
| saldo insuficiente (paga 3 de 12) | fixa 60 s | nova (repete o mesmo vídeo) | 294 / 320 (n=60) | 542 / 592 (n=60) | 7176 | 0 | 6.26 | 0.0298 | 30 |
| saldo insuficiente (paga 3 de 12) | histerese 60/180 s | nova (repete o mesmo vídeo) | 294 / 320 (n=60) | 542 / 592 (n=60) | 7176 | 0 | 6.26 | 0.0298 | 30 |
| cancelamento durante a subida | fixa 180 s (padrão) | nova (repete o mesmo vídeo) | 162 / 232 (n=30) | 218 / 286 (n=30) | 23958 | 0 | 9.33 | 0.1037 | 60 |
| cancelamento durante a subida | fixa 60 s | nova (repete o mesmo vídeo) | 162 / 232 (n=30) | 218 / 286 (n=30) | 16733 | 0 | 7.14 | 0.0794 | 60 |
| cancelamento durante a subida | histerese 60/180 s | nova (repete o mesmo vídeo) | 162 / 232 (n=30) | 218 / 286 (n=30) | 16733 | 0 | 7.14 | 0.0794 | 60 |
| envio instável (20% de falha) | fixa 180 s (padrão) | nova (repete o mesmo vídeo) | 406 / 544 (n=120) | 661 / 793 (n=120) | 45403 | 0 | 35.27 | 0.0294 | 120 |
| envio instável (20% de falha) | fixa 180 s (padrão) | antiga (refaz na GPU) | 449 / 623 (n=120) | 814 / 990 (n=120) | 46571 | 16753 | 40.69 | 0.0339 | 120 |
| envio instável (20% de falha) | fixa 60 s | nova (repete o mesmo vídeo) | 406 / 544 (n=120) | 661 / 793 (n=120) | 30966 | 0 | 30.90 | 0.0258 | 120 |
| envio instável (20% de falha) | fixa 60 s | antiga (refaz na GPU) | 449 / 623 (n=120) | 814 / 990 (n=120) | 31664 | 16644 | 36.14 | 0.0301 | 120 |
| envio instável (20% de falha) | histerese 60/180 s | nova (repete o mesmo vídeo) | 406 / 544 (n=120) | 661 / 793 (n=120) | 30966 | 0 | 30.90 | 0.0258 | 120 |
| envio instável (20% de falha) | histerese 60/180 s | antiga (refaz na GPU) | 449 / 623 (n=120) | 814 / 990 (n=120) | 31664 | 16644 | 36.14 | 0.0301 | 120 |
| cache lento (o 1º pod grava 15 min) | fixa 180 s (padrão) | nova (repete o mesmo vídeo) | 299 / 320 (n=30) | 681 / 714 (n=30) | 37910 | 0 | 16.04 | 0.0668 | 30 |
| cache lento (o 1º pod grava 15 min) | fixa 60 s | nova (repete o mesmo vídeo) | 299 / 320 (n=30) | 681 / 714 (n=30) | 34310 | 0 | 14.95 | 0.0623 | 30 |
| cache lento (o 1º pod grava 15 min) | histerese 60/180 s | nova (repete o mesmo vídeo) | 299 / 320 (n=30) | 681 / 714 (n=30) | 34310 | 0 | 14.95 | 0.0623 | 30 |
