# 03 — Segurança e conformidade (requisitos da v1)

> Desenho técnico para atender a seção 11 da especificação desde a primeira versão. **Não é parecer jurídico.** Antes de abrir para qualquer pessoa de fora, contratar advogado(a) de direito digital para validar este documento, os termos de uso e a política de privacidade.

## 0. Princípios

1. **Bloquear na dúvida.** Se uma verificação obrigatória falhar ou o fornecedor estiver fora do ar, o job fica retido e nada é entregue.
2. **Camadas independentes.** Nenhuma camada basta sozinha. O compilador é um modelo abliterado e **nunca** conta como camada de segurança.
3. **Pessoas reais fora do modo adulto.** A plataforma gera personagens fictícios adultos; fotos de terceiros não entram (seção 3).
4. **Nada público na v1.** Sem galeria pública, sem link compartilhável; o cliente vê os próprios vídeos por URL assinada de curta duração.
5. **Mínimo de dados.** Identidade (verificação de idade) separada do conteúdo; biometria fica com o fornecedor de verificação.
6. **Fornecedores que aceitam o caso de uso, por escrito.**

## 1. Bloqueador: fornecedores que aceitem o caso de uso

O item 1 do doc 01 mostra que os Termos do RunPod listam conteúdo adulto como não autorizado. O mesmo cuidado vale para **todos** os fornecedores, porque qualquer um deles pode encerrar a conta:

| Fornecedor | Situação |
|---|---|
| GPU (RunPod) | **[CONFLITO]**: pedir confirmação por escrito ou trocar |
| Registry das imagens Docker | Verificar os termos (as imagens contêm pesos de modelos NSFW) |
| Storage e CDN | Verificar |
| Hospedagem do backend e do frontend | Verificar (várias plataformas PaaS proíbem conteúdo adulto) |
| Verificação de idade | Verificar se atendem o setor adulto |
| Moderação (CSAM, idade, celebridades) | Verificar elegibilidade e preço |
| Pagamentos | Processador especializado (seção 11) |
| E-mail transacional, domínio, DNS | Verificar |

## 2. Política de conteúdo

**Permitido:** conteúdo adulto com personagens fictícios adultos, dentro da lei.

**Proibido** (bloqueio, registro e possível banimento):

- Qualquer menor de idade, **ou aparência ou contexto infantil**, mesmo que o pedido diga que o personagem é adulto (a estimativa de idade se aplica à imagem, não ao que foi declarado).
- Pessoas reais identificáveis: celebridades, pessoas públicas, conhecidos; por nome, descrição ou imagem.
- Falta de consentimento, violência sexual, coerção, pessoa inconsciente ou incapaz.
- Zoofilia, incesto, necrofilia.
- Outras categorias que o adquirente de pagamentos exigir (as regras das bandeiras costumam ser mais amplas que a lei; confirmar a lista).

A regra `Rules: all characters are adults... REFUSED` do compilador continua em todas as versões (seção 6.3 da especificação), como uma camada a mais.

## 3. Imagens no I2V (decisão crítica)

É a função de maior risco: um I2V adulto que aceita qualquer foto vira ferramenta de deepfake sexual. O Código Penal já tipifica a montagem que inclui uma pessoa em cena de nudez ou sexo (art. 216-B, parágrafo único), e as regras das bandeiras exigem consentimento documentado **da pessoa retratada**, não de quem envia (doc 01, itens 16 e 17).

| Política | Risco | Recomendação |
|---|---|---|
| Upload livre com declaração de consentimento | **Alto.** Quem envia não pode consentir pela pessoa retratada | Não adotar |
| Upload livre + detecção de celebridades | **Alto.** Não detecta pessoas comuns (ex-parceiros, colegas) | Não adotar |
| **Só imagens geradas na plataforma** (ou o último quadro de um vídeo gerado nela) | Baixo | **v1** |
| "Somente eu": upload aceito só se **todos** os rostos coincidirem com a selfie verificada do titular (biometria com prova de vida) | Médio. Biometria é dado sensível na LGPD; erro de comparação | v2, depois de parecer jurídico |

**Implementação da v1:** não existe rota de upload de imagem no modo adulto. O I2V recebe o `asset_id` de um ativo do próprio usuário com origem registrada (`gerado` ou `quadro_extraido`). O backend busca o arquivo no storage; o cliente nunca envia bytes de imagem.

**Impacto no produto:** o fluxo principal de hoje (I2V com imagens próprias) muda. A porta de entrada passa a ser o **T2V**, seguido de "continuar vídeo" (I2V a partir do último quadro), e depois "animar imagem gerada", quando houver um passo de texto para imagem (a testar: o próprio Wan T2V com 1 quadro). Por isso o T2V vira prioridade na Etapa 2 do plano.

## 4. Verificação de idade dos clientes

- **Obrigação:** o ECA Digital (em vigor desde 17/03/2026) exige verificação confiável e auditável para conteúdo pornográfico e **veda a autodeclaração** (doc 01, item 17).
- **Fluxo:** cadastro → verificação por fornecedor (documento + selfie com prova de vida, ou estimativa facial de idade com margem) → conta marcada como "maior verificado" → só então qualquer tela de geração ou galeria.
- **Guardamos só o resultado:** data, método, fornecedor, ID da transação. Fotos de documento e selfies ficam com o fornecedor, conforme contrato.
- **Reverificação** quando houver sinal de conta compartilhada (troca de dispositivo e de local, denúncia).
- **Mercado da v1 só no Brasil**, com bloqueio de países cujas exigências próprias de verificação não forem atendidas (Reino Unido e vários estados dos EUA têm regras específicas). Reduz o escopo de conformidade.
- Acompanhar a regulamentação da ANPD sobre os mecanismos aceitos e escolher um fornecedor que se comprometa a segui-la.

## 5. Moderação em camadas

```
Pedido (pt) ─► C1 regras ─► C2 classificador de texto ──────────────────────┐
                                                                             ▼
Ativo (I2V) ─► C3 origem e dono ─► C4 CSAM (hash + classificador) ─► C5 idade/celebridade
                                                                             │
Worker: compilador ─► C6 REFUSED? ─► C7 = C1 + C2 no prompt em inglês ─► render ─► MP4
                                                                             │
MP4 ─► C8 quadros: CSAM, idade estimada, celebridade ─► entrega ao cliente   │
          └─ suspeita ─► quarentena ─► seção 6                               ▼
Entregue ─► C9 canal de denúncia ─► remoção em até 48 h + hash bloqueado
```

| Camada | Onde roda | O que faz | Se disparar |
|---|---|---|---|
| **C1** Regras determinísticas | Backend e worker (biblioteca compartilhada) | Normalização (minúsculas, acentos, leetspeak, letras repetidas, espaços); listas por categoria da seção 2 em pt, en e es; expressões para idade abaixo de 18 ("13 anos", "15yo" etc.); lista de nomes de pessoas públicas | Recusa, registro, pontuação de abuso |
| **C2** Classificador de texto | Backend | Modelo de segurança **não abliterado** e multilíngue com categoria específica de exploração sexual infantil. Candidato: Llama Guard 3 (categoria S4; suporta português; há versão 1B que roda em CPU). Política: permitir conteúdo sexual adulto (S12) e bloquear S1, S3, S4 e afins. **Validar** o desempenho em pt-BR e a licença | Recusa |
| **C3** Origem e dono do ativo | Backend | O ativo é do usuário e tem origem `gerado`/`quadro_extraido` | Recusa |
| **C4** CSAM no ativo | Backend | Hash contra bancos conhecidos + classificador de CSAM inédito | Seção 6 |
| **C5** Idade e celebridade no ativo | Backend | Detecção de rostos, idade estimada, reconhecimento de celebridades | Recusa; idade em dúvida → bloquear |
| **C6** `REFUSED` | Worker | Saída do compilador. No teste real da Etapa 1, um pedido com o presidente do Brasil **não** gerou `REFUSED` (doc 01, item 20) | Recusa (sinal fraco, mas conta) |
| **C7** C1 + C2 no prompt em inglês | Worker (C1) e backend (C2, no retorno) | O compilador pode introduzir termos | Recusa, sem entrega |
| **C8** Saída | Backend, sobre quadros de amostra (~1 por segundo + primeiro e último) | CSAM (classificador + hash do vídeo), idade estimada de cada rosto, celebridades | Seção 6 ou recusa |
| **C9** Denúncia | Formulário público | Qualquer pessoa denuncia um conteúdo | Seção 7 |

**Limiares:** idade estimada abaixo de 18, com margem de segurança (ex.: limite inferior da faixa abaixo de 21), bloqueia. Se só o estimador de idade disparar, o cliente pode pedir revisão humana; se o classificador de CSAM disparar, **não há recurso**: vale a seção 6.

**Indisponibilidade:** se C2, C4, C5 ou C8 não responderem, o job fica retido, é tentado de novo e, passado o prazo, é cancelado com reembolso. Nunca é entregue sem moderação.

## 6. Suspeita de CSAM: procedimento

1. **Automático:** entrega bloqueada; arquivo movido para o bucket de quarentena (criptografado, acesso de no máximo duas pessoas designadas, sem download, prévia borrada); conta suspensa; registros e metadados preservados.
2. **Comunicação:** às autoridades, como exige o ECA Digital. No Brasil, Polícia Federal (Centro Nacional de Triagem de Notificações) e o canal da SaferNet; NCMEC (CyberTipline) se houver ligação com os EUA. O advogado define o roteiro e os prazos.
3. **Retenção:** pelo prazo legal, só na quarentena; nunca copiar, nunca usar em testes ou treino.
4. **Pessoas:** limitar a exposição de quem revisa (prévias borradas, rodízio, apoio).
5. **Antes do beta:** roteiro escrito, responsáveis nomeados e um ensaio do procedimento (sem material real).

## 7. Denúncias e remoção (imagens íntimas não consentidas)

- Formulário público, sem login, para qualquer pessoa denunciar um conteúdo (ex.: alguém que se reconheceu num vídeo).
- Prazo de remoção de até **48 h**, o padrão do TAKE IT DOWN Act e uma boa prática mesmo sem usuários nos EUA; remoção do arquivo, bloqueio do hash para regerações e cópias, revisão da conta.
- Aviso claro, nos termos de uso e no rodapé, de como denunciar.

## 8. Dados pessoais, registros e retenção

- **Dados sensíveis (LGPD):** biometria (verificação de idade) e dados sobre vida sexual (pedidos e vídeos). Exigem base legal específica (consentimento destacado), relatório de impacto (RIPD), encarregado (DPO) nomeado, criptografia em repouso, controle e registro de acesso.
- **Registros de acesso:** guardar por 6 meses (Marco Civil, art. 15). No serviço de marketing, desde 09/10/2026: tabela `acessos` (conta, IP, porta, data e hora), apagada sozinha depois de 6 meses (ADR 0007).
- **Retenção proposta** (validar com o advogado):

| Dado | Retenção |
|---|---|
| Registros de acesso (IP, data e hora) | 6 meses |
| Resultado da verificação de idade (sem biometria) | Enquanto a conta existir, mais o prazo legal |
| Pedido em pt, prompt compilado, parâmetros, decisões de moderação, hashes | Enquanto a conta existir, mais um período definido para auditoria e denúncias |
| Vídeos e imagens gerados | Até o cliente apagar; expiração automática opcional (ex.: 30 ou 90 dias) |
| Quarentena | Conforme orientação jurídica e das autoridades |

- Direitos do titular: exportar e apagar, respeitando as retenções legais.

## 9. Segurança técnica

- **Nenhuma porta exposta nos workers**: ComfyUI e Ollama só em `127.0.0.1`.
- **Atenção ao setup manual de hoje:** a URL `https://<POD_ID>-8188.proxy.runpod.net` é pública e o ComfyUI não tem login. Quem tiver a URL usa a GPU, vê as saídas (`/view`, `/history`) e envia arquivos. Até a migração: não compartilhar a URL e terminar o pod ao fim da sessão (já é a rotina). No modo `pod` da nova imagem, acesso só por túnel SSH ou com autenticação.
- **Segredos** (chaves do provedor de GPU, storage, moderação) num cofre de segredos. O worker não recebe credenciais de storage, só URLs assinadas por job.
- **Webhooks** assinados (HMAC), com conferência do `job_id` e processamento idempotente.
- **Cadeia de suprimentos:** versões fixadas (tag do ComfyUI, commits dos custom nodes, lock das dependências Python com hashes, SHA-256 dos modelos); build no CI; ComfyUI-Manager fora da produção.
- **Contas:** senha + segundo fator ou passkeys; cookies `HttpOnly`; limite de requisições; pontuação de abuso (recusas repetidas bloqueiam a conta).
- **Páginas do serviço de marketing (fase 3, 08/10/2026):** CSP estrita com Trusted Types, SRI, COOP, CORP, `Permissions-Policy` e proteção contra CSRF por Fetch Metadata ([ADR 0006](adr/0006-front-end-build-entrega-e-seguranca.md)).
- **Contas do serviço de marketing (09/10/2026, [ADR 0007](adr/0007-contas-sessoes-e-painel.md)):** sem senha, por código de uso único no e-mail (provedores confiáveis, "não sou um robô", limites por rede, e-mail e aparelho); sessão em cookie HttpOnly; painel do dono num endereço secreto, com token e aplicativo autenticador; token por pod. Revisão completa e teste de invasão: `docs/13`.
- **Isolamento entre clientes:** prefixos por usuário no storage; testes contra acesso a recurso de outro usuário (IDOR).
- Criptografia em repouso (banco e storage) e TLS em tudo.

## 10. Transparência e marcação de IA

- Marcar as saídas como geradas por IA: metadados de proveniência (C2PA) e marca d'água invisível. Na UE é obrigatório (doc 01, item 17); em qualquer lugar ajuda outras plataformas a identificar o conteúdo.
- Remover os metadados que o ComfyUI embute no MP4 (workflow e prompt) com `--disable-metadata`, e incluir só a proveniência controlada.

## 11. Pagamentos

- Processador ou adquirente especializado no setor adulto (Stripe e PayPal não aceitam; doc 01, item 16).
- Cumprir as regras das bandeiras para conteúdo adulto: verificação de idade e identidade, controle de conteúdo, atendimento a reclamações, remoção.
- Modelo de **créditos pré-pagos**: reduz chargeback e deixa o custo previsível para o cliente.
- **Serviço de marketing (SFW), desde 09/10/2026:** recarga do pré-pago por Pix no Mercado Pago, creditada só com a confirmação da API ([ADR 0008](adr/0008-recarga-pix-mercado-pago.md)). Não vale para o uso adulto, que continua exigindo processador especializado (D1).

## 12. Licenças

Resumo do item 4 do doc 01. Ação obrigatória antes de qualquer uso comercial: **confirmar a licença do NSFW-UMT5 e do patch `ComfyUI-UMT5-Extended`**; se não houver permissão, usar o encoder oficial. Auditar a licença de cada custom node que entrar na imagem; não copiar código do `worker-comfyui` (AGPL-3.0).

## 13. Checklist "pronto para beta privado"

- [ ] Fornecedores confirmados por escrito (GPU, registry, storage, hospedagem, verificação de idade, moderação, pagamentos)
- [ ] Parecer jurídico sobre ECA Digital, LGPD, CP 216-B, termos de uso e política de privacidade
- [ ] Verificação de idade em produção, sem autodeclaração
- [ ] Política de imagens do I2V implementada (só ativos com origem na plataforma)
- [ ] C1–C9 ativas, testadas e bloqueando na dúvida
- [ ] Roteiros de CSAM e de denúncia de imagem íntima, com responsáveis e ensaio
- [ ] Formulário público de denúncia com prazo de 48 h
- [ ] Retenção e exclusão implementadas; RIPD; encarregado nomeado
- [ ] Marcação de IA nas saídas
- [ ] Tetos de gasto e alertas
- [ ] Teste de invasão básico (autenticação, IDOR, webhooks, URLs assinadas)

## 14. Como testar as camadas sem material ilícito

- **C1, C2, C7:** casos de teste em texto (frases sintéticas que devem ser bloqueadas e frases adultas permitidas que não podem ser bloqueadas), versionados no repositório.
- **C4, C8:** usar os mecanismos de teste que os próprios fornecedores oferecem. Nunca obter, gerar ou guardar material real para teste.
- **C5, C8 (idade e celebridade):** imagens SFW de adultos e de figuras públicas em domínio público ou licenciadas.
- **Fixtures do repositório:** só conteúdo SFW.
