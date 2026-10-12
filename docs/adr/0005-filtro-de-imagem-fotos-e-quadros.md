# ADR 0005: filtro de imagem nas fotos e nos quadros dos vídeos (2ª porta)

- **Status:** proposto (08/10/2026). Aceitar é decisão do dono do projeto, depois da validação da seção "Antes de aceitar".
- **Atualização (09/10/2026):** o `/aprovar` virou o painel do dono, no endereço secreto (ADR 0007). Com o cadastro aberto, este filtro é o caminho para a revisão humana não virar o gargalo (`docs/13`, seção 4.2).
- **Atualização (11/10/2026): o ponto 2 (quadros do vídeo, no pod) está implementado** (`render2go_worker/filtro_imagem.py`), a pedido do dono, depois que testes do plano interno (aprovado sozinho, sem revisão) geraram vídeos com nudez a partir de textos com duplo sentido que passaram pelo C1. O ponto 1 (fotos no servidor) continua reservado. Como ficou:
  - **classificador:** `Freepik/nsfw_image_detector` (EVA02-base 448 px, licença MIT conferida no cartão e na seção "License"; base `timm/eva02_base_patch14_448.mim_in22k_ft_in22k_in1k`, MIT; biblioteca `timm==1.0.30`, Apache-2.0), fixado por commit e SHA-256 no `models.lock.json` (`nsfw_freepik_modelo`, `nsfw_freepik_config`) e baixado com os outros modelos;
  - **quadros:** 1 a cada 4 (a cada 0,25 s, com o 1º, que é a própria foto, e o último), não os 6 previstos acima: com nota por quadro em milissegundos na GPU, a amostra maior custa pouco e não deixa passar um trecho curto;
  - **limiar:** nota = P(medium) + P(high); barrado com nota ≥ 0,35 em qualquer quadro (o cartão usa 0,5). Nas 4 fotos SFW do repositório, a maior nota foi 0,013; em vídeos de teste feitos com elas, 0,017. O "low" (sugestivo, ex.: roupa de banho) não barra;
  - **barrado:** o vídeo e a folha de conferência não saem do pod; o job vira erro **sem repetição**, o valor volta para o saldo (termos, item 4: vídeo que não sai), e ficam a nota, o horário (`jobs.nsfw`, `jobs.barrado_em`) e o evento `video_barrado` no registro de auditoria do pedido. O histórico do painel mostra "N vídeo(s) barrado(s) pelo filtro de imagem". O cliente vê o vídeo como não entregue, sem o motivo;
  - **fail-closed:** a API recusa "ok" sem a nota e sem quadros conferidos (o job volta para a fila) e barra também quando a nota que chega é alta; classificador que não carrega faz o worker sair (motivo "filtro") e o pod ser encerrado;
  - **privacidade:** a política passou à versão 3 (seção 6, decisões automatizadas): o vídeo barrado é uma decisão automática, e o cliente pode pedir revisão pelo suporte (LGPD, art. 20).
- **Atualização (12/10/2026), a pedido do dono** ("preciso que tenha um jeito de eu como admin acessar esses vídeos bloqueados" e "quero sim que haja uma justificativa de recusa"):
  - **quarentena:** o vídeo barrado e a folha dele não vão mais para o lixo do pod: vão para `quarentena/<cliente>/<pedido>/` no R2 (endereços assinados que a API manda com o job; o pod envia e a API confere que o vídeo está lá antes de marcar `jobs.quarentena_em`). Nunca vão para `saida/`: o cliente não tem link. **Só o dono** abre, no painel (histórico, "Ver os que não saíram", "Ver o vídeo barrado"; rota `GET <painel>/api/jobs/{job}/quarentena`, papel dono), com links de 10 min, o vídeo e a folha desfocados até ele pedir, e cada abertura na auditoria do painel. Prazo: 7 dias (a rota recusa depois disso e a regra `quarentena-7-dias` do bucket apaga, `docs/09`, seção 3). Se o vídeo mostrar criança ou adolescente, o dono não baixa nem compartilha: denuncia (SaferNet, polícia) e procura orientação jurídica; a guarda serve só para isso e para a revisão (ECA, art. 241-B, § 2º);
  - **motivo para o cliente:** o pedido mostra, por vídeo e em resumo, "barrado pelo filtro de imagem: o vídeo gerado mostrava conteúdo impróprio" (sem a nota); a recusa do texto diz a categoria (ex.: "ele pede armas"), nunca a palavra (`render2go_servico/motivos.py`);
  - **privacidade v4:** a política diz a guarda de 7 dias, quem acessa e para quê (seção 6 e prazos).
- **Ainda pendente para aceitar:** medir os falsos positivos em vídeos SFW reais de clientes (com autorização, fora do repositório), como diz a seção "Antes de aceitar", e decidir o ponto 1.
- **Contexto:** o C1 (ADR 0004) olha só o texto. Um pedido com texto inocente ("a câmera se aproxima") pode vir com uma foto expositiva, ou o modelo pode gerar um quadro expositivo a partir de uma foto que passou. Hoje, quem enxerga a imagem é só a revisão humana, e só a foto de entrada (o vídeo pronto não é revisto). O aviso de pessoas (`render2go_servico/pessoas.py`) já criou o caminho: o servidor baixa cada foto depois do envio, analisa em CPU e grava o resultado em `fotos.analise`.

## Decisão (proposta)

Reservar a 2ª porta em dois pontos, com o mesmo classificador de imagem:

1. **Fotos de entrada, no servidor:**
   - no mesmo passo da análise de pessoas, depois do envio, acrescentando `nsfw` (nota de 0 a 1) em `fotos.analise`;
   - o `/aprovar` mostra a nota por foto;
   - acima do limiar, o lote não pode ser aprovado sem uma confirmação explícita do revisor, registrada em `eventos`.
2. **Quadros do vídeo, no pod, antes do envio:**
   - depois da conferência do 1º quadro (`conferencia.py`), o worker amostra 6 quadros (início, fim e 4 no meio) e passa pelo classificador na GPU (milissegundos por quadro);
   - acima do limiar, o vídeo não é enviado; o job vira erro sem repetição, e o lote fica marcado para o revisor;
   - classificador ausente ou com falha: o worker não envia (fail-closed), como já acontece com a conferência.
3. **Limiar conservador** (bloqueia na dúvida), ajustado só para baixo depois de medir falsos positivos em fotos SFW reais de clientes (produto, comida, fachada, pessoas vestidas, roupa de banho).
4. **Não substitui a revisão humana.** A revisão passa a amostral só com outra ADR, depois de semanas de números.

## Candidatos (a validar antes de aceitar)

| Modelo | Arquitetura | Licença | Observação |
|---|---|---|---|
| `Falconsai/nsfw_image_detection` | ViT-base, 224 px, 2 classes | Apache-2.0, segundo listagens de terceiros | Muito usado; base de treino fechada (não auditável) |
| `Freepik/nsfw_image_detector` | EVA02-base, 448 px | MIT, segundo listagens de terceiros | Conferir também a licença do modelo base |

A licença precisa ser conferida no cartão oficial e no arquivo LICENSE de cada modelo. Versão fixada por commit e SHA-256, como os demais modelos.

## Política de teste

- **Repositório, testes e CI só com conteúdo SFW** (regra do projeto). Não se busca, gera nem guarda material explícito para testar o detector.
- O que dá para medir dentro dessa regra: **falsos positivos** em fotos SFW (é o que trava clientes legítimos) e **tempo** por foto e por quadro.
- **A taxa de acerto em conteúdo explícito vem das métricas publicadas** pelos autores. É uma limitação conhecida, aceita por esta ADR.

## Consequências

- **Custo:** quase zero na GPU (6 quadros por vídeo). No servidor, um modelo de ~350 MB a mais na imagem e ~0,2–0,5 s por foto em CPU.
- **Falso positivo** vira trabalho do revisor (confirmar ou recusar), nunca entrega automática.
- **D1 continua bloqueando qualquer uso adulto:** este filtro existe para manter o serviço SFW, não para liberar outro modo.

## Antes de aceitar

1. Conferir as licenças nos cartões oficiais.
2. Medir os falsos positivos com fotos SFW de clientes, com autorização, fora do repositório.
3. Escolher o limiar.
4. Implementar os dois pontos com testes só SFW: o caminho de bloqueio é testado com um classificador falso que devolve nota alta.
