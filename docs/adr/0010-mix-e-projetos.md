# ADR 0010: Mix (vários modelos em sequência, a partir de um pedido só) e Projetos

- **Status:** proposto (11/10/2026). Aceitar é decisão do dono do projeto, depois da seção "Antes de aceitar". Desde 10/10/2026, `/mix` e `/projetos` existem na área do cliente como "Em breve": explicam o recurso e contam o "Tenho interesse" (`recurso:mix` e `recurso:projetos`), que aparece no painel, seção Interesses.
- **Contexto:** pedido do dono (10/10/2026): no Mix, "usar vários modelos ao mesmo tempo", a partir de "um pedido inicial que poderá seguir uma sequência de gerações": anexar arquivos do negócio, "gerar fotos desses arquivos e após a geração de fotos querer que cada foto também se torne um vídeo de x segundos", com os exemplos "criar um modelo", "adicionar uma música a essa imagem estática" e "que o rosto do indivíduo nessa imagem seja alterado"; e "Projetos (em breve)" no menu. Hoje só uma finalidade roda (foto vira vídeo, Wan 2.2 I2V); as outras dependem de modelos ainda não ativados (`docs/12`, seções 4 e 5).

## Decisão (proposta)

1. **Cada passo do Mix é um pedido comum** (`lotes`), ligado ao anterior:
   - tabela `mix` (id, cliente, criado, situação e o plano dos passos em JSON, com versão) e, em `lotes`, as colunas `mix_id`, `passo` e `origem` (o pedido de onde vieram as entradas);
   - cada passo reaproveita o que já existe e foi testado: a cobrança e os estornos, a revisão humana (ADR 0004), a fila intercalada por cliente, a conferência automática da finalidade, o extrato por rodada e os logs;
   - nenhum processo novo de longa duração: quando um passo fecha (`db._fechar_lote`), o servidor cria o seguinte com os resultados entregues (o que falhou ou foi excluído não segue).
2. **Passos da v1**, cada um só quando o modelo dele estiver ativo (`docs/12`, seção 5, com o próprio ADR):
   - **imagem:** imagem nova a partir de uma foto e de um texto (Qwen-Image-Edit: o produto num fundo novo, outra luz) ou só de um texto (Qwen-Image);
   - **vídeo:** foto vira vídeo, o de hoje, com o texto padrão e os textos por foto do pedido em sequência;
   - **som:** trilha a partir de um texto (ACE-Step) ou locução (Chatterbox Multilingual, só com as vozes do catálogo), juntada no acabamento (`acabamento.py`, ffmpeg em CPU). "Adicionar uma música a essa imagem estática" vira um vídeo da imagem parada, com zoom lento e a trilha, sem GPU de vídeo;
   - **objeto 3D** a partir de uma foto (TRELLIS), uma leitura possível de "criar um modelo": entra como passo final quando o modelo for ativado;
   - até 4 passos e os mesmos limites do pedido de hoje (fotos por pedido, textos distintos), para o custo e o tempo continuarem previsíveis.
3. **Custo antes de começar:** a página mostra cada passo (modelo, quantidade, preço por unidade e subtotal) e o total máximo. O total é cobrado no início e o que não rodar volta ao saldo sozinho (passo recusado, resultado que falha, passo cancelado). O Mix nunca para no meio por falta de saldo, e vale a regra de hoje: cobrança no envio, estorno automático. Preço por modelo em `precos.py` e `docs/11`.
4. **Parar entre os passos:** o cliente vê os resultados de cada passo (em Logs) e pode cancelar os seguintes, com o estorno do que não começou. Escolher, a cada passo, quais resultados seguem (desmarcar uma imagem antes de ela virar vídeo) fica para depois da v1.
5. **Segurança igual à de hoje ou mais rígida:**
   - o texto de cada passo passa pelo C1 (ADR 0004) e pelo perfil do tradutor de cada modelo (prompt novo = arquivo novo + rodada de fidelidade, regra do projeto);
   - a imagem gerada no meio do caminho é conteúdo novo que ninguém revisou: **o Mix depende do filtro de imagem (ADR 0005) aceito e em produção**. Até lá, o resultado de cada passo de imagem vai para a revisão humana antes do passo seguinte (fail-closed);
   - **sem troca de rosto e sem rosto de pessoa real gerado ou alterado** (o exemplo "que o rosto do indivíduo seja alterado"). É a ferramenta de deepfake que os termos proíbem (item 6: "imitar uma pessoa real"), e mexer nos traços do rosto pode ser tratamento de dado biométrico, sensível na LGPD (art. 5º, II, e art. 11). Os termos de troca de rosto entram num C1 novo (`c1.v3.json`, que mantém os da v2); o `REFUSED` do tradutor continua, sem ser camada de segurança. Edições que mantêm a pessoa como ela é (luz, fundo) só com a autorização das pessoas nas fotos, como hoje (aviso de pessoas, `pessoas.py`);
   - voz de pessoa real só com a autorização por escrito da dona da voz (`docs/12`, seção 2.8); clonar uma voz a partir de um áudio enviado fica fora da v1;
   - **outros tipos de arquivo** no pedido inicial (o PDF do cardápio, o logotipo em vetor) ficam fora da v1: cada tipo novo é um leitor novo exposto a arquivos de terceiros. Quando entrarem, o arquivo vira imagem num processo isolado (sem rede, só leitura, com tempo e memória limitados) e o pedido continua só com fotos e textos.
6. **Projetos:** tabela `projetos` (id, cliente, nome de 1 a 60 caracteres, criado, arquivado) e `lotes.projeto_id`, opcional, também nos passos do Mix:
   - as páginas de Pedidos e do Mix deixam escolher o projeto; o extrato filtra e soma por projeto. O nome do projeto fica gravado no movimento, que sobrevive à limpeza do histórico, como o modelo hoje;
   - "guardar os textos e os estilos que deram certo": receitas salvas por conta (texto padrão, estilo, preset e duração), até 50 por conta, aplicadas com um clique. Os termos dos presets continuam anexados pelo código (regra do projeto);
   - só da própria conta (isolamento entre clientes, `docs/08`). Compartilhar entre contas fica para depois, junto com o "nome de usuário" (em breve);
   - arquivar tira o projeto da lista e não apaga nada; os arquivos seguem a guarda de sempre (termos, item 8).
7. **Escala e GPUs:**
   - cada modelo tem a própria imagem e os próprios pods (modelos embutidos ou copiados para o disco local e conferidos por SHA-256, regra do projeto);
   - o orquestrador sobe pods por modelo, pela fila de cada um, e a previsão mostrada soma a de cada passo (tabela `geradores`);
   - um pod não troca de modelo na v1: trocar custa o download e a VRAM de outro modelo;
   - os passos sem GPU (acabamento, trilha juntada) rodam em CPU.

## Antes de aceitar

- Ao menos um modelo além do de hoje ativado com o próprio ADR (o primeiro candidato é o Qwen-Image-Edit, Apache 2.0: troca de fundo e de luz) e a trilha (ACE-Step), com tempo e preço medidos em GPU (`docs/11`).
- ADR 0005 aceito e em produção, ou a revisão humana entre os passos de imagem.
- Os números do "Tenho interesse" do Mix e dos Projetos no painel, para decidir a ordem. Os Projetos não dependem de modelo novo e podem vir antes, num ADR só deles.
- Termos e privacidade v3: o que é cada passo, as autorizações de pessoas e vozes, o que é cobrado no início e o que volta.

## Alternativas descartadas

- **Um job só que roda todos os modelos do Mix:** um passo que falha derrubaria tudo, e a revisão, a cobrança, o estorno e a conferência teriam de ser refeitos para ele. Com um pedido por passo, cada peça já testada continua valendo.
- **Cobrar cada passo só quando ele começa:** o Mix pararia no meio por falta de saldo, com um estado novo ("aguardando saldo") e resultados pela metade. Cobrar o total e devolver o que não roda é mais simples e já é como o pré-pago funciona.
- **Troca de rosto ou "rosto alterado" com autorização:** fora. Mesmo autorizada, a imagem com o rosto mudado é, na prática, um deepfake da pessoa, e a autorização não acompanha o vídeo quando ele circula.
- **Aceitar qualquer arquivo no pedido inicial:** fora da v1 (item 5).
- **Escolher a cada passo quais resultados seguem:** melhor experiência, mas pede mais telas e estados; fica para depois.

## Consequências

- O extrato agrupa as rodadas do mesmo Mix e ganha o filtro por projeto; os logs mostram de qual passo veio cada arquivo.
- O tempo de um Mix é a soma dos passos (cada um espera a fila e, se preciso, a subida dos pods daquele modelo): a página mostra a previsão de cada passo, como já faz no pedido de hoje.
- Até aceitar, `/mix` e `/projetos` só contam o interesse. Os termos v2 (item 2) já dizem que as opções "Em breve" não têm data nem garantia de lançamento.
