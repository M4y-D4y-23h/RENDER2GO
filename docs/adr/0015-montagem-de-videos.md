# ADR 0015: montagem de vídeos (vídeos prontos num vídeo só, com textos, logo e trilha)

- **Status:** aceito no plano interno (14/10/2026), a pedido do dono (13/10/2026: "uma primeira forma de edição"; a montagem "desligada para os clientes até o preço ser definido; ligada no plano interno"). Ligar para os clientes (`RENDER2GO_MONTAGEM=todos`) depende da seção "Antes de ligar para os clientes".
- **Contexto:** o cliente recebe um vídeo curto por foto. Para o anúncio que vai para as redes, ele ainda precisa juntar os vídeos, pôr o preço e a chamada e o logo, fora do Render2Go. O ADR 0010 (Mix, proposto) prevê passos sem GPU que rodam em CPU (acabamento, trilha juntada); a montagem é o primeiro deles e não depende de modelo novo.

## Decisão

1. **Uma montagem é um pedido sem GPU** (tabela `montagens`), feito pelo contêiner `montador` (`render2go-servico montar`): ffmpeg em CPU, fora da API, uma montagem por vez, com 768 MB e 1 CPU (o servidor tem 2 vCPU e 4 GB), sistema de arquivos só de leitura, `/tmp` em memória e nenhum segredo da API (como o `analisador`).
2. **O que entra:** de 2 a 10 vídeos prontos da própria conta, dos últimos 7 dias, na ordem escolhida; corte seco ou esmaecimento (cada vídeo escurece até o preto no fim e volta do preto no começo, 0,25 s de cada lado, menos nas pontas da montagem, porque o 1º quadro vira a capa nas redes); 9:16, 1:1 ou 16:9 em 720p. Cada vídeo aparece inteiro, sobre uma cópia dele mesmo desfocada e escurecida: o produto nunca é cortado quando o formato do vídeo e o da montagem são diferentes.
3. **Textos por cima** (título, preço e chamada, cada um opcional, numa posição fixa):
   - na API: até 40, 20 e 40 caracteres, numa linha; só letras (com os acentos do português e do espanhol), números, espaço e a pontuação de um anúncio (o resto, como emoji, outros alfabetos, `[`, `=`, `\`, `{`, `<` e caracteres invisíveis, é recusado com a explicação); cada texto pelo C1 (ADR 0004), como o texto do pedido (o cliente vê a regra, nunca a palavra; a categoria fica no registro de acesso);
   - **o texto do cliente nunca chega ao ffmpeg**, nem pela linha de filtros nem por um arquivo de texto do `drawtext`: o montador desenha os textos e o logo numa imagem PNG transparente, com o Pillow e a fonte dos títulos das páginas (Bricolage Grotesque, SIL OFL, no commit fixo do google/fonts, conferida por bytes e SHA-256 no build da imagem), e o ffmpeg só põe essa imagem por cima. O tamanho da letra diminui até o texto caber (o título e a chamada podem quebrar em duas linhas).
4. **Logo opcional:** JPEG, PNG ou WebP de até 5 MB, enviado direto ao armazenamento por uma URL assinada que só aceita os bytes declarados. O `analisador` (o único contêiner que decodifica arquivos dos clientes) o abre por `pessoas.abrir_foto` (tipo pelos primeiros bytes, teto de 50 MP lido do cabeçalho), passa pelo filtro das fotos (ADR 0005, a mesma regra; sem o classificador, nada é liberado) e o regrava em PNG (até 512 px, sem metadados). O montador só abre esse PNG. Logo recusado ou inválido: a montagem fecha sem custo, e o cliente vê só "o logo não pôde ser usado".
5. **O ffmpeg só recebe arquivos do serviço** (os vídeos que os pods geraram, a imagem desenhada e a trilha da biblioteca), em duas etapas, com a memória constante (medido: 10 vídeos de uma vez passavam de 740 MB; assim, 204 MB):
   1. um ffmpeg por vídeo: 16 quadros por segundo, a duração do pedido, o fundo desfocado e o tamanho da montagem;
   2. o demuxer concat (modo seguro, só nomes que o montador escreveu) lê os clipes um depois do outro, põe a imagem por cima, junta a trilha ou o silêncio e grava o MP4 final (H.264 + AAC, `faststart`).
   Cada entrada com o formato explícito (nada é adivinhado pelo conteúdo), só o protocolo `file`, um thread de decodificação, sem stdin; o processo com o ambiente vazio, tempo de CPU, tamanho de arquivo e tempo de relógio limitados; a saída conferida pelo ffprobe (H.264, AAC, o tamanho e a duração esperados).
6. **Trilha opcional:** só de uma biblioteca com a licença de cada faixa conferida e documentada (CC0, domínio público ou comprada), baixada no build e conferida por bytes e SHA-256 (`montagem.TRILHAS`). Sem envio de áudio pelo cliente. **A v1 sai com a biblioteca vazia**: a página não mostra a opção e toda montagem tem o áudio em silêncio; cada faixa nova entra com a licença em `servico/trilhas/FONTES.md` e o SHA-256 no código.
7. **Saída** em `saida/<cliente>/<montagem>/` (apagada em 30 dias, como os vídeos), com os mesmos links assinados por 24 h; o cliente exclui quando quiser.
8. **Preço e chave:** `RENDER2GO_MONTAGEM` = `desligada`, `interno` (padrão: só o plano interno, sem custo) ou `todos` (também o plano pago, que paga `RENDER2GO_PRECO_MONTAGEM` por montagem pronta; sem o preço, o pago continua sem). O teste gratuito nunca monta. Quem não pode usar vê o que a montagem faz e o "Tenho interesse" (`recurso:montagem`, no painel).
9. **Cobrança como a do vídeo (ADR 0012):** o preço fica reservado no saldo livre desde o pedido, com a linha do cliente travada (a reserva entra em `_reservado`: os vídeos não gastam o mesmo dinheiro), e é cobrado só quando a montagem fica pronta (movimento `montagem`, referência única `montagem:<id>`, uma rodada própria no extrato e na planilha). A montagem que falha, é cancelada ou perde um vídeo excluído antes de começar não custa nada. O montador que cai no meio devolve a montagem à fila (até 3 vezes); o logo que não chega em 1 h cancela a montagem; o que o analisador não avalia em 1 dia a fecha.
10. **Telas:** a tela "Montagem" na área do cliente (escolher, ordenar, textos, formato, transição, logo e a lista que se atualiza sozinha) e a seção "Montagens" no painel (todas as contas, com o erro técnico e as notas do filtro no logo; o operador também vê; nenhum vídeo). Registro de acesso (`montagem`, `montagem_recusada_filtro`, `montagem_excluida`) e cópia dos dados da conta com as montagens.

## Medições (14/10/2026, a imagem do serviço: ffmpeg 7.1 do Debian, 768 MB e 1 CPU, como no compose)

| Montagem | Vídeo | ffmpeg | Memória do ffmpeg |
|---|---|---|---|
| 2 vídeos de 3 s, 9:16, esmaecimento, textos e logo (o `conferir` do build) | 6 s | 3,4 s | — |
| 10 vídeos de 5 s, 9:16, esmaecimento, textos e logo | 50 s, 4,4 MB | 24 s | 204 MB |
| 10 vídeos de 5 s, 16:9, corte seco, textos e logo | 50 s, 3,6 MB | 23 s | — |

O CPU é o do servidor, que já está pago: o custo de uma montagem é o tempo de 1 vCPU (~25 s no pior caso) e ~5 MB por 30 dias no armazenamento (`docs/11`).

## Antes de ligar para os clientes (`RENDER2GO_MONTAGEM=todos`)

- O preço por montagem (`RENDER2GO_PRECO_MONTAGEM`; proposta no `docs/11`, seção 4).
- Termos de uso e política de privacidade novos (versões novas em `contas.DOCUMENTOS`, aceite de novo na entrada): o que é a montagem, que o preço é cobrado só na montagem pronta, que o logo e os textos ficam guardados (o logo, até 7 dias; os textos, com o pedido) e que o cliente responde pelo que escreve e pelo logo que envia.
- Uma semana de uso no plano interno, com o painel (seção Montagens) sem erro que não seja do cliente.

## Alternativas descartadas

- **`drawtext` com `textfile=`:** o texto não passaria pela linha de filtros, mas o ffmpeg ainda leria o arquivo com o texto do cliente e precisaria do fontconfig e da expansão desligada (`expansion=none`) para não interpretar `%{...}`. Desenhar no Pillow tira o texto do ffmpeg inteiro, mede a largura de verdade (o tamanho da letra se ajusta) e reaproveita a fonte das páginas.
- **Um ffmpeg só com os 10 vídeos e esmaecimento cruzado (`xfade`):** foi a 1ª versão. Os decodificadores dos vídeos ainda não usados correm à frente, e a memória cresce com o número de vídeos (746 MB com 10, e o contêiner de 768 MB matava o ffmpeg). O esmaecimento pelo preto dentro de cada vídeo deixa a junção sequencial.
- **Montar na API ou no analisador:** a API não pode disputar a CPU com as requisições, e o analisador já ocupa até 2 GB com o classificador. Um contêiner próprio cai sozinho, sem levar os outros.
- **Aceitar o áudio do cliente:** mais um leitor exposto a arquivos de terceiros e um risco de direitos autorais que o serviço não confere. Fica fora da v1.
- **Cobrar no pedido e estornar a falha:** a regra antiga dos pedidos. A reserva com a cobrança na entrega é a do ADR 0012, já testada com 10 pods ao mesmo tempo.

## Consequências

- Um contêiner a mais no servidor (`montador`, até 768 MB e 1 CPU) e o ffmpeg do Debian na imagem do serviço (~300 MB a mais); o build monta dois vídeos de teste e falha se o ffmpeg, a fonte ou o Pillow não funcionarem.
- O extrato ganha a rodada "Montagem" (sem IA); a planilha, o tipo "Montagem".
- O ADR 0010 (Mix) pode usar a montagem como passo final, depois dos passos com GPU.

## Testes

- `servico/tests/test_montagem.py`: os textos (tamanho, caracteres, textos que quebrariam uma linha de filtros), a linha do ffmpeg das duas etapas (formatos explícitos, um thread por entrada, nada do cliente), o ffmpeg sem os segredos do contêiner e morto pelo relógio, o logo (PNG regravado; SVG, HTML, GIF, cortado e acima de 50 MP recusados), a imagem por cima, montagens de verdade nos três formatos (vídeos sintéticos SFW), a trilha conferida pelo SHA-256, a fonte ausente (fail-closed), o vídeo corrompido, a reserva e a cobrança uma vez só, os 10 pods com saldo para 3 e uma montagem reservada (Postgres), dois montadores ao mesmo tempo, o prazo do montador e do logo, o logo pelo analisador (liberado, recusado, inválido e o que derruba o analisador), o montador de ponta a ponta e as rotas da API (planos, C1, logo, limites, extrato, painel e exclusão).
- `servico/tests/test_paginas_conta.py` e `test_paginas_painel.py`: a tela no navegador (escolher, ordenar, logo, fila, pronta e excluir, com axe-core), a tela sem acesso com o "Tenho interesse", o menu, o celular e o CLS com a API lenta; a seção no painel (também para o operador).
