# Conferência da continuidade: avaliação do teste de 08/10 e calibração (09/10/2026)

Registro de onde vieram os limites da conferência do vídeo inteiro (`render2go_worker/conferencia.py`). Só números: os vídeos e as fotos não ficam no repositório.

## 1. O teste gratuito de 08/10

Pedido feito pelo dono numa conta de teste: 5 fotos de carros, de propósito com resoluções boas e ruins, 480p, 3 s, estilo Livre, texto "crie um vídeo de apresentação desses carros pra cada uma dessas fotos". O pod ligou às 22:22 (horário de Brasília), depois dos 10 min de espera do agrupamento de testes, e desligou às 22:33. Custou US$ 0,11.

| Vídeo | Foto | O que aconteceu | Nota |
|---|---|---|---|
| azul | boa | A câmera gira devagar; carro igual do começo ao fim (placa, rodas, logotipo); brilho de pôr do sol no fim | Bom |
| branco | boa, salão | Carro estável; no fundo, pessoas aparecem e somem e o telão troca de imagem | Aceitável |
| vermelho | borrada (miniatura ampliada) | O carro anda para a frente; salto de nitidez no começo; a faixa vermelha do chão some; mancha no para-brisa | Médio |
| preto | boa | Aos 2 s, corte seco para um Kwid **branco**, com duas pessoas abrindo a porta | Reprovado |
| preto (baixa resolução) | ruim | Na metade, fusão para **outro carro, de outra marca**, branco | Reprovado |

Causas e o que mudou:
- **O texto foi traduzido ao pé da letra, no plural:** o modelo de vídeo recebeu um "vídeo de apresentação destes carros" e fez o que um vídeo de apresentação faz: passou para o próximo carro. Daí o tradutor I2V v6, com os termos de cena única anexados pelo código (`worker/evals/compiler/RESULTADOS.md`).
- **A conferência só olhava o 1º quadro**, e os 5 vídeos começam certos. Daí a conferência da continuidade (seção 2).
- **Foto pequena piora:** o preto de baixa resolução foi o que mais se perdeu. Daí o aviso de foto pequena na página.

## 2. Calibração dos limites

Medida, para cada quadro: cor média (R, G, B) do centro do quadro, metade da largura e da altura, reduzido a 8×8 por média de área. Diferença entre duas cores = média das diferenças absolutas de R, G e B (0 a 255).
- **Deriva:** a maior diferença entre um quadro e o 1º.
- **Salto:** a maior diferença entre dois quadros vizinhos.

Os 39 vídeos que existiam (os 5 do teste e os 34 das rodadas de 06 e 07/10), em [`medidas.csv`](medidas.csv):

| | Deriva | Salto |
|---|---|---|
| 37 vídeos aproveitáveis | até 31,5 (tênis com aproximação forte); quase todos abaixo de 17 | até 5,8 |
| Corte para outro carro (preto) | 54,2 | 16,2 |
| Fusão para outro carro (preto, baixa resolução) | 85,8 | 6,3 |
| **Limite** | **40** | **12** |

- Com esses limites, os 2 vídeos com troca são barrados e nenhum dos 37 aproveitáveis.
- **A folga é pequena na deriva** (31,5 contra 40): uma aproximação forte de um produto de cor muito diferente do fundo pode passar do limite. Nesse caso o vídeo é refeito com outra seed e, depois de 3 tentativas, falha (no pré-pago, volta para o saldo). As medidas de cada vídeo ficam no banco (`jobs.deriva_cor`, `jobs.salto_cor`) para recalibrar com dados de produção (`docs/09`, seção 5.1.9).
- **Não pega** a troca por um objeto da mesma cor, nem mudanças sutis no próprio produto: o perfume que mudou de cor em 06/10 deu deriva de 11 a 15.

### Outras medidas testadas e descartadas

- **Correlação em tons de cinza entre quadros vizinhos** (a mesma da conferência do 1º quadro): o corte para o Kwid branco deu 0,978, igual a vídeos bons (a pose e o fundo continuam iguais; só a cor muda).
- **Correlação entre o 1º e o último quadro:** vídeos bons com câmera em movimento chegam a 0,13; não separa.
- **Nitidez da foto (variância do laplaciano, por regiões)**, para um aviso de "foto borrada": uma xícara de estúdio com fundo desfocado mediu 219 a 261, abaixo da foto borrada do carro vermelho (549). Não separa; o aviso na página ficou só pela resolução.

## 3. Como refazer

```bash
cd worker && uv run python -c "
from pathlib import Path
from render2go_worker import conferencia as c
for v in sorted(Path('PASTA_DOS_VIDEOS').rglob('*.mp4')):
    m = c.continuidade(v)
    print(v.name, m, c.problema_continuidade(**m) or 'ok')
"
```
