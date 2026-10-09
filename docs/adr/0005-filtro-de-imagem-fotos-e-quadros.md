# ADR 0005: filtro de imagem nas fotos e nos quadros dos vídeos (2ª porta)

- **Status:** proposto (08/10/2026). Aceitar é decisão do dono do projeto, depois da validação da seção "Antes de aceitar".
- **Atualização (09/10/2026):** o `/aprovar` virou o painel do dono, no endereço secreto (ADR 0007). Com o cadastro aberto, este filtro é o caminho para a revisão humana não virar o gargalo (`docs/13`, seção 4.2).
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
