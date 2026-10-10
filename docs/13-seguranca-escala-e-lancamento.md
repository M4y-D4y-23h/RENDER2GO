# 13 — Segurança, escala e lançamento

Revisão feita em 09/10/2026, a pedido do dono, antes do lançamento oficial e da divulgação em massa: o que estava exposto, o que mudou no código e no servidor, o teste de invasão, o que o serviço aguenta (medido) e a parte que só o dono pode fazer. As decisões estão no [ADR 0007](adr/0007-contas-sessoes-e-painel.md) (contas, sessão, painel e pods) e no [ADR 0008](adr/0008-recarga-pix-mercado-pago.md) (Pix). Os passos da atualização do servidor estão no `docs/09`, seção 0, "09/10/2026 (4)".

## 1. Resumo

| Pedido | O que foi feito | Onde |
|---|---|---|
| Teste gratuito só com conta, e-mail de provedor confiável, sem contas falsas em massa | Cadastro e entrada por código de 8 dígitos no e-mail; só Gmail, Outlook/Hotmail, Yahoo, iCloud, UOL, BOL e Terra; um teste por caixa (apelidos do Gmail contam como a mesma caixa); "não sou um robô"; limites por rede, e-mail e aparelho | ADR 0007 |
| Conta nova sem token, com direito só ao 1º uso do Wan (até 5 fotos, 480p, 3 s) | A conta nasce no teste gratuito no 1º acesso pelo e-mail; a 1ª recarga a passa para o pré-pago | ADR 0007, `docs/11` |
| "Eu não sou um robô" | Cloudflare Turnstile numa página isolada, conferido no servidor e fechado na dúvida | ADR 0007, item 3 |
| Cada "Tenho interesse" no painel | Cada clique, com a conta, o plano e a hora, além do resumo por opção | Painel, seção "Interesse" |
| Painel num endereço mais sofisticado | Endereço secreto + token + código do autenticador (2 fatores), bloqueio por tentativas, IPs permitidos (opcional), auditoria | ADR 0007, item 5 |
| Desligar um pod pelo painel, em emergência | "Desligar" em cada pod, "Parada de emergência" (encerra todos e não sobe outro até "Retomar") | Painel, seção "Pods" |
| APIs expostas e vulnerabilidades | Mapa da API desligado, cabeçalhos que revelavam o servidor removidos, token por pod, envio ao R2 com tamanho assinado, teste de invasão com 73 verificações | Seções 2 e 3 |
| Documentos legais | `/termos`, `/privacidade` e `/cookies`, com a identificação do vendedor no rodapé de todas as páginas | Seção 6.7 |
| API de pagamento | Recarga por Pix no Mercado Pago, creditada sozinha e uma vez só | ADR 0008 |
| Tráfego alto | Medido; correção de uma espera de 40 ms por resposta; fases de crescimento | Seção 4 |
| Vários modelos e 30+ pods no RunPod | Limites do RunPod, um modelo por pod, cuidados | Seção 5 |
| Sem `robots.txt` (decisão do dono) | Removido. Páginas privadas com `noindex` no cabeçalho; o endereço do painel não aparece em lugar nenhum | Seção 2 |

## 2. Auditoria: como estava e como ficou

Conferido de fora em https://app.render2go.com, em 09/10/2026, e no código.

| Ponto | Como estava | Como ficou |
|---|---|---|
| Mapa da API | `/openapi.json`, `/docs` e `/redoc` públicos, listando todas as rotas, inclusive as de administração e dos pods | Desligados (404) |
| Cabeçalhos | `Server: Caddy`, `Via: 1.1 Caddy` e `server: uvicorn` revelavam o servidor e a versão | Removidos; HSTS com `includeSubDomains` |
| Painel | Em `/aprovar` (fácil de adivinhar), com um token fixo guardado no navegador, sem 2º fator e sem bloqueio por tentativas | Endereço secreto, token + autenticador, bloqueio de 15 min depois de 5 erros, IPs permitidos, cookie HttpOnly só no caminho do painel, auditoria |
| Clientes | Código de acesso no `localStorage` (a CSP com Trusted Types já fechava a porta do XSS que o leria) | Sessão em cookie HttpOnly; o código antigo ainda serve para entrar |
| Pods | Um token fixo para todos: um pod comprometido podia pegar trabalho em nome de outro | Um token por pod, preso ao nome do worker, revogado quando o pod é encerrado |
| Envio de fotos ao R2 | A URL assinada aceitava um arquivo de qualquer tamanho | O tamanho declarado entra na assinatura: o R2 recusa (403) arquivo maior ou menor (conferido no R2 de verdade) |
| Limites | Em memória, por processo (com vários processos, cada um contava sozinho) | No banco, por rede (IPv6 por /64), e-mail e aparelho |
| Contêineres | Disco gravável, todas as capacidades do Linux, logs sem limite (podiam encher o disco), todos os segredos em todos os processos | Disco só de leitura (escrita só em `/tmp` e nos volumes), sem capacidades, `no-new-privileges`, logs limitados, cada processo só com os segredos que usa (conferido com a imagem de verdade) |
| Caddy | Sem tempos limite próprios (conexões lentas seguravam o servidor) e sem log de acesso | Tempos limite, log em JSON com rotação de 184 dias (cookies e `Authorization` aparecem como REDACTED) |
| Configuração | Uma variável faltando só aparecia no 1º pedido | A API lê tudo ao subir; o que falta sai no log (`docker compose logs api \| grep AVISO`) e no painel, seção "Segurança" |
| DNS | Sem registros CAA, SPF e DMARC e sem DNSSEC | Tarefa do dono (seção 6.4) |
| `robots.txt` | Existia e listava `/api/` | Removido, a pedido do dono; `noindex` vai no cabeçalho das respostas da API, do painel e da página de verificação |
| Documentos legais | Não existiam (o aviso de cookies dizia "falta a política") | Termos de Uso, Política de Privacidade e Política de Cookies, com a identificação do vendedor (Decreto 7.962/2013) |

**O que já estava bom e continua:** CSP estrita com Trusted Types, SRI, COOP/CORP e `Permissions-Policy` (ADR 0006); CSRF barrado pelo Fetch Metadata; revisão humana antes de qualquer GPU e filtros que bloqueiam na dúvida (ADR 0004); nenhum binário passa pela API (só URLs assinadas e curtas); ComfyUI e Ollama só em `127.0.0.1` nos pods; pods só na secure cloud, com desligamento automático.

## 3. Teste de invasão

**Como:** `servico/tests/teste_invasao.py` sobe a API de verdade num Postgres de teste, atrás do `Caddyfile` de produção (com TLS local), e ataca de fora, como um invasor faria. Cada grupo de ataques sai de um IP próprio (127.0.0.x), como redes diferentes. Ele recusa rodar num banco cujo nome não tenha "teste", "invasao" ou "pentest", porque apaga o banco a cada rodada.

**Resultado em 09/10/2026: 73 de 73 verificações OK**, depois das correções abaixo.

| Grupo | O que foi tentado | Resultado |
|---|---|---|
| Descoberta | 44 caminhos conhecidos: painéis, `/aprovar`, docs da API, `.env`, `.git`, backups, phpMyAdmin, WordPress, métricas | Só 404; o endereço do painel não aparece em nenhuma página, script, CSS, sitemap ou página 404 |
| Travessia de caminho | `..%2f`, `%2e%2e`, `....//`, `\`, `%00`, nomes enormes | Nada fora das páginas publicadas |
| Redirecionamento | `//outro-site`, painel sem a barra | Sem redirecionamento aberto |
| Cabeçalhos e cookies | Todas as respostas | HSTS, CSP, `nosniff`, `DENY`; sem `Server`/`Via`; cookies `__Host-`/`__Secure-`, HttpOnly, Secure, SameSite |
| CORS e métodos | Origem de outro site, preflight, TRACE/PUT/DELETE/PATCH | Recusados |
| Autenticação | Rotas sem sessão; cookies e tokens forjados (injeção SQL, Unicode, 10 KB); sessão do painel usada como de cliente e vice-versa; token de um pod usado por outro | Sempre 401/403, nunca 500 |
| IDOR | Pedido, envio e recarga de outra conta | 404 |
| CSRF | `cross-site`, `same-site`, `Origin` de outro site, sem o cabeçalho da página, no painel | 403 |
| Injeção | SQL na busca do painel e nos ids; e-mails com CRLF, letras de outros alfabetos e largura cheia; tipos trocados; campos extras (`plano`, `aprovado`, `cliente_id`); nome de foto com `../`; corpo de 2 MB | 422/404/413; campos extras ignorados; a chave no R2 fica na pasta do pedido |
| Aviso do Mercado Pago | Sem assinatura, com outro segredo, com Unicode | 401; o aviso válido só dispara a consulta ao Mercado Pago |
| Sessões | Sair, sair de todos os aparelhos, conta excluída, apelido do Gmail | O cookie copiado deixa de valer; um teste por caixa |
| Limites | Códigos por e-mail; força bruta no código; entrada no painel; tokens de worker chutados | 429 no 6º código; o código morre depois de 5 erros; painel bloqueado 15 min; rede bloqueada depois de 60 erros |

**XSS armazenado:** um teste no navegador (`test_paginas.py`) põe HTML e JavaScript no nome da conta, no texto do pedido e numa mensagem de suporte. O painel mostra tudo como texto, nada executa e a CSP nem precisa bloquear.

**Achados e correções** (todos com teste de regressão):
- **Caractere NUL (`\x00`)** numa busca ou mensagem dava erro 500 (o Postgres recusa). Agora sai de todo texto antes do banco.
- **Credenciais com caracteres fora do ASCII** (token, código do autenticador, nome de worker, assinatura do aviso) davam erro 500 na comparação em tempo constante. Agora a comparação é em bytes.
- **Dígitos de outros alfabetos** passavam como dígitos (`\d` do Python). Agora só 0–9.
- **E-mail de empresa:** a resposta dizia se havia conta criada pelo painel antes da verificação anti-robô e dos limites, e a entrada com código respondia diferente com e sem conta. Agora a lista de provedores é conferida depois deles, e a entrada com código não a consulta.

**Rodar de novo** (o binário do Caddy é o mesmo da imagem do compose, conferido pelo SHA-512 do release):
```bash
V=2.10.2; mkdir -p /tmp/caddy && cd /tmp/caddy
curl -fsSLO https://github.com/caddyserver/caddy/releases/download/v$V/caddy_${V}_linux_amd64.tar.gz
curl -fsSLO https://github.com/caddyserver/caddy/releases/download/v$V/caddy_${V}_checksums.txt
grep linux_amd64.tar.gz caddy_${V}_checksums.txt | sha512sum -c - && tar -xzf caddy_${V}_linux_amd64.tar.gz caddy
cd ~/GPU2GO/servico   # ou a cópia local do repositório
uv run python tests/teste_invasao.py --caddy /tmp/caddy/caddy --db "postgresql://postgres@/render2go_invasao?host=/var/tmp&port=55432"
```

**Fora do teste:** o "não sou um robô" foi conferido à parte, com as chaves de teste da Cloudflare (a que sempre passa e a que sempre falha). Não foram testados: ataque de volume (DDoS), que só uma CDN segura (seção 6.4); a infraestrutura dos fornecedores (Hetzner, Cloudflare, RunPod, Mercado Pago); engenharia social e o roubo das senhas do dono (seção 6.6).

## 4. Escala: o que o servidor aguenta

### 4.1 Medido em 09/10/2026

Numa máquina de 4 vCPU (Xeon 2,3 GHz, 15 GB), com a API, o Postgres e o gerador de carga dividindo a mesma CPU. Com isso, os números são um piso. Base: 300 contas, 900 pedidos e 3.600 trabalhos (vídeos e traduções). Ferramenta: `servico/tests/medir_carga.py`.

| Processos da API | Cenário | Pedidos por segundo | 95% abaixo de |
|---|---|---|---|
| 2 | Página do cliente acompanhando os pedidos (`GET /api/lotes`) | ~475 | 10 ms |
| 2 | Misto (85% pedidos, 10% conta, 5% página inicial) | ~520 | 10 ms |
| 2 | Página inicial | ~1.470 | 3 ms |
| 3 | Misto | ~810 | 6 ms |
| 3 | Página inicial | ~2.570 | 2 ms |

- **A medição achou um problema, já corrigido:** com mais de um processo, cada resposta numa conexão reaproveitada esperava ~40 ms (o algoritmo de Nagle com o ACK atrasado do outro lado), e o Caddy reaproveita as conexões com a API. Eram 41 pedidos/s e 44 ms por resposta; agora a API cria o próprio socket, com `TCP_NODELAY` (teste em `test_cli.py`).
- **Rodar de novo:** `uv run python tests/medir_carga.py --processos 2 --conexoes 3 --db "postgresql://postgres@/render2go_carga_teste?host=/var/tmp&port=55432"`. O banco é apagado e semeado de novo; o nome precisa ter "teste" ou "carga".

### 4.2 O que isso quer dizer

- **A página só pergunta enquanto há pedido em andamento e a aba está à vista:** uma consulta a cada 10 s, ~0,1 pedido/s por pessoa. Então ~500 pedidos/s equivalem a **~5.000 pessoas acompanhando pedidos ao mesmo tempo** num servidor de 4 vCPU. A página inicial sai da memória, aos milhares por segundo.
- **Cadastros:** limitados pelo e-mail (8 envios em paralelo por processo, `RENDER2GO_EMAIL_PARALELO`) e pela cota do provedor de e-mail.
- **Os gargalos de verdade não estão na API:**
  1. **a revisão humana:** todo pedido espera o dono aprovar (ADR 0004). A 1 minuto por pedido, são ~60 pedidos por hora por revisor;
  2. **a GPU:** uma L40S faz ~65 vídeos de 3 s por hora (55 s cada, `docs/07`), e o RunPod tem limite de gasto e vagas que somem nos horários de pico (seção 5).

### 4.3 Fases de crescimento

| Fase | Uso | O que fazer |
|---|---|---|
| **Lançamento** | Até ~1.000 contas ativas por dia | O servidor atual, com `RENDER2GO_API_WORKERS=2`, Postgres no mesmo servidor e backup diário (seção 6.5). A revisão pelo dono dá conta. |
| **Crescimento** | ~10 mil por dia, com picos de campanha | Servidor de 8 vCPU e 16 GB, com `RENDER2GO_API_WORKERS=4` a `6` (processos × `RENDER2GO_DB_POOL` + 30 precisa caber no Postgres; acima de 100 conexões, subir o `max_connections`). Proxy da Cloudflare na frente (DDoS e estáticos), com o firewall aceitando só a Cloudflare. Postgres num servidor próprio ou gerenciado, com backup contínuo. Cota maior no provedor de e-mail. Mais revisores ou o filtro automático do ADR 0005. |
| **Escala grande** | 100 mil contas ou mais, campanhas nacionais | Vários servidores da API atrás de um balanceador: a API não guarda estado (sessões, limites e fila estão no Postgres). O orquestrador continua um só (trava de líder; um segundo fica de reserva). Postgres com réplica de leitura e PgBouncer. Um grupo de pods por modelo (seção 5) e o limite de gasto do RunPod aumentado. |

## 5. GPUs no RunPod: vários modelos e 30 pods ou mais

**Limites do RunPod** ([documentação de cobrança](https://docs.runpod.io/accounts-billing/billing), consultada em 09/10/2026):
- **limite de gasto padrão de US$ 80/h**, somando tudo na conta. Ele sobe sozinho com o histórico da conta, ou pelo suporte, explicando o uso;
- **saldo:** um pod só sobe com saldo para pelo menos 1 h dele; a cobrança é por segundo;
- **quantidade de pods:** não há teto documentado, mas a vaga de cada GPU varia por datacenter e horário. O orquestrador tenta a lista de GPUs (`RENDER2GO_GPUS`) e os datacenters onde já conseguiu;
- **endpoints serverless** (se um dia forem usados) têm outro teto: sobe por conta própria até 30 workers, conforme o saldo, e acima de 60 só pelo suporte.

**30 pods L40S** (US$ 1,09/h, `docs/07`) custam ~US$ 33/h: cabem nos US$ 80/h, mas o saldo precisa cobrir o pior caso de 2 h de cada pod (~US$ 66), além da reserva. No `.env`: `RENDER2GO_MAX_PODS=30` e `RENDER2GO_MAX_USD_H` com folga (ex.: 40). Juntos, fazem ~1.950 vídeos de 3 s por hora.

**Um modelo por pod** (ou por grupo de pods):
- cada modelo pesa dezenas de GB e ocupa a VRAM inteira. Misturar modelos num pod obriga a carregar e descarregar a cada troca, minutos por vez;
- o worker já informa à fila os modelos que tem (`modelos`), e a fila só entrega a ele trabalho desses modelos;
- **próximo passo, quando entrar o 2º modelo:** o orquestrador passa a subir pods de cada tipo pela fila de cada modelo, com teto de pods e de gasto por modelo, e cada tipo usa a GPU mais barata que comporte o modelo (`docs/12`).

**Cuidados:**
1. **Gasto:** alerta de saldo baixo no RunPod; `RENDER2GO_MAX_USD_H`; o orquestrador não sobe pod abaixo da reserva de saldo; e conferir o relatório de GPUs do painel com a fatura.
2. **Parada de emergência** no painel, e o desligamento automático de cada pod (no máximo 2 h), que vale mesmo se o servidor cair.
3. **Credenciais:** um token por pod (ADR 0007, item 7); a chave do RunPod só no orquestrador (o compose a esvazia na API e na análise); 2FA na conta do RunPod.
4. **Só secure cloud** (já é o padrão).

## 6. A sua parte (dono), em ordem

### 6.1 Antes de atualizar o servidor

1. **E-mail transacional** (obrigatório: sem ele, ninguém entra). **Escolhido: Amazon SES**, na região de Ohio (`us-east-2`), com o preço à la carte (seção 6.1.1).
   - **Por que Ohio:** a conta da AWS foi criada na "nova experiência" (projetos), que prende o projeto à região do país do endereço: Brasil → Ohio. Outra região só com "Ativar recursos avançados", que não tem volta e tira o limite de gasto (seção 6.1.1, passo 4).
   - **Distância:** o servidor fica em Nuremberg. Cada e-mail é uma conversa SMTP de ~10 idas e voltas: até Ohio (~100 ms cada), cerca de 1 s por e-mail; com 8 envios simultâneos por processo da API, ~7 e-mails por segundo por processo, perto do limite por segundo que o SES dá no começo. Frankfurt seria uma fração de segundo, mas não compensa a ativação. Para mais vazão, aumente o limite do SES e `RENDER2GO_EMAIL_PARALELO` juntos.
   - **Por quê:** é o mais barato em volume e o que mais aguenta pico. Preços conferidos nas páginas oficiais em 09/10/2026, por mês:

     | E-mails por mês | Amazon SES (à la carte) | Resend | Postmark (Platform) |
     |---|---|---|---|
     | 10 mil | US$ 1 | US$ 20 | US$ 18 |
     | 100 mil | US$ 10 | US$ 35 | US$ 126 |
     | 1 milhão | US$ 100 | US$ 650 | ~US$ 1.200 (ou preço negociado) |

   - **As outras opções:**
     - **Postmark** tem a melhor fama de entrega para e-mail transacional, mas custa ~12× o SES em volume;
     - **Resend** é o mais simples de configurar, mas custa ~6,5× o SES em 1 milhão por mês, e a API aceita 10 pedidos por segundo por conta (sobe a pedido);
     - **Brevo** mistura transacional com marketing na mesma estrutura, o que deixa a entrega dos códigos menos previsível (a página de preços oficial não abriu na conferência).
   - **O preço do SES mudou em 21/07/2026:** conta nova começa no plano Essentials (US$ 0,16 por mil). A AWS deixa voltar ao à la carte (US$ 0,10 por mil) a qualquer momento: console do SES → **Pricing plan** → **Cancel plan**.
   - **Picos:** o SES começa no "sandbox" (200 e-mails por dia, 1 por segundo); na saída do sandbox você pede os limites (ex.: 50 mil por dia e 20 por segundo) e pede mais antes de cada campanha grande. Acima do limite por segundo, o SES responde "454 Throttling" e o serviço tenta de novo em 1 s e em 4 s.
   - **Plano B:** se a AWS demorar a liberar a produção, use o Resend no começo. Trocar depois é só mudar o SMTP no `.env` e os registros de DNS.
2. **Cloudflare Turnstile** ("não sou um robô", gratuito): seção 6.1.2.
3. **Segredos novos** (`docs/09`, seção 0, "09/10/2026 (4)"):
   - `RENDER2GO_SEGREDO_CONTAS`;
   - `RENDER2GO_ADMIN_CAMINHO`, o endereço do painel. Guarde num gerenciador de senhas, não nos favoritos sincronizados;
   - um `RENDER2GO_ADMIN_TOKENS` novo, porque o antigo ficou guardado em navegadores;
   - `RENDER2GO_ADMIN_TOTP`, cadastrado no aplicativo autenticador (Google Authenticator, Authy ou 1Password). Guarde o segredo também no gerenciador de senhas;
   - `RENDER2GO_WORKER_TOKENS` vazio.
4. **Identificação do vendedor** (rodapé e páginas legais): razão social, CNPJ, endereço, e-mail de contato e e-mail de privacidade (`RENDER2GO_EMPRESA_*` e `RENDER2GO_EMAIL_*`). Sem eles, as páginas mostram "[preencher]".
5. **Mercado Pago** (pode ficar para depois do 1º dia; sem ele, a recarga continua manual):
   - conta verificada, de preferência PJ, com uma **chave Pix cadastrada** (sem ela, o Pix não é oferecido);
   - "Suas integrações" → criar uma aplicação → credenciais de **produção** → Access Token em `RENDER2GO_MP_ACCESS_TOKEN`;
   - Webhooks → URL `https://app.render2go.com/api/pagamentos/mercadopago`, evento "Pagamentos" → copie a assinatura secreta para `RENDER2GO_MP_WEBHOOK_SEGREDO`;
   - confira a taxa do Pix em "Seu negócio → Custos" e teste com uma recarga de R$ 10 paga de outra conta.
6. **Backup do banco** antes de atualizar (comando no `docs/09`).

#### 6.1.1 Passo a passo: e-mail pelo Amazon SES

Conferido nas páginas da AWS em 09/10/2026. Os nomes dos menus estão em inglês; com o console em português, alguns vêm traduzidos ao lado.

**A. Conta da AWS** (uma vez)
1. Em aws.amazon.com → **Create an AWS Account**: e-mail, nome da conta, cartão de crédito internacional e telefone. Escolha o plano **Paid**. O plano **Free** encerra a conta depois de 6 meses (ou quando os créditos acabam), e o e-mail pararia. Os créditos de conta nova (até US$ 200) valem nos dois planos.
2. **Verificação em duas etapas no usuário raiz:** menu da conta (canto superior direito) → **Security credentials** → **Assign MFA device**.
3. **Alerta de gasto:** **Billing and Cost Management** → **Budgets** → **Create budget** → modelo "Monthly cost budget" → US$ 10 e o seu e-mail. Se a senha do SMTP vazar, você fica sabendo antes da fatura.

**B. Domínio no SES** (região Ohio)
4. Abra o **Amazon SES** na região do projeto: **US East (Ohio)**, `us-east-2`. Na "nova experiência" da AWS (conta organizada em projetos, sem seletor de região no alto da página), o projeto fica preso à região do país do endereço, e Ohio é a do Brasil; outra região mostra "Região indisponível" e um erro de "service control policy". O botão **Ativar recursos avançados** libera as regiões, mas **não tem volta**: você passa a administrar a organização da AWS, e o limite de gasto deixa de existir. Para o e-mail, Ohio basta (seção 6.1, item 1). Identidades, sandbox, plano e credenciais valem só na região em que foram criados. **Confira antes de publicar o DNS:** o MX do passo 7 tem que terminar em `us-east-2.amazonses.com`.
5. **Conta nova abre o assistente "Get started"** (no console em português, "Configurar", com 7 etapas), que cria de uma vez o domínio, o MAIL FROM (passo 7) e o e-mail do teste (passo 9):
   - **e-mail:** o seu Gmail (recebe o link de confirmação e o teste do passo 9);
   - **domínio de envio:** `render2go.com`; **MAIL FROM domain** (em português, "Domínio MAIL FROM"): `envio`; "Behavior on MX failure": **Use default MAIL FROM domain** ("Usar o domínio MAIL FROM padrão"). Com "Reject message", um erro no registro MX pararia os códigos de entrada;
   - **plano de preços** (etapa 3, "Select pricing plan"): o Essentials aparece como "Plano atual" porque veio por padrão. No quadro **Downgrade to à la carte pricing**, clique em **Cancel plan** e confirme. Como você não escolheu o Essentials, o cancelamento vale na hora. Não clique em "Escolher plano" em nenhum cartão: plano escolhido só se cancela no ciclo de cobrança seguinte;
   - **etapas opcionais** (4: "Aprimoramentos na capacidade de entrega", que é o Virtual Deliverability Manager; 5: pool de IPs dedicados; 6: gerenciamento de locatários): deixe tudo desligado e avance. Sem plano, o Virtual Deliverability Manager custa US$ 0,07 por mil a mais, e a "entrega compartilhada otimizada" dele pode segurar e-mails de propósito, o que atrasaria os códigos de 10 minutos. IP dedicado começa em US$ 15 por mês e só compensa com centenas de milhares de e-mails por dia. Locatários servem para quem manda e-mail em nome dos domínios dos clientes;
   - na revisão, **Get started**. Depois, a página **Get set up** mostra o que falta: clicar no link que chega no Gmail e publicar o DNS (**Get DNS records**, passos 6 a 8).

   Sem o assistente (conta que já tem identidades): **Identities** → **Create identity** → **Domain** → `render2go.com` → em "Advanced DKIM settings", **Easy DKIM** com **RSA_2048_BIT** → **Create identity**; o MAIL FROM fica no passo 7.
6. **DKIM na Cloudflare** (DNS do `render2go.com`): cadastre os 3 registros **CNAME** que o SES mostrar (nome `xxxx._domainkey`, destino `xxxx.dkim.amazonses.com`), com o **Proxy status em "DNS only"** (nuvem cinza). Com a nuvem laranja o DKIM não confere. No campo **Name** da Cloudflare, tanto faz colar o nome inteiro (`xxxx._domainkey.render2go.com`) ou só o começo; ela não duplica o domínio. Em minutos a identidade fica **Verified**.
7. **MAIL FROM próprio** (alinha o SPF ao domínio, o que o DMARC confere). Se não foi feito no assistente: na identidade → **Custom MAIL FROM domain** → **Edit** → marque **Use a custom MAIL FROM domain** → `envio` → "Behavior on MX failure": **Use default MAIL FROM domain** → **Save changes**. Na Cloudflare:
   - **MX** `envio` → `feedback-smtp.us-east-2.amazonses.com`, prioridade 10 (o SES mostra os dois juntos, `10 feedback-smtp...`; na Cloudflare a prioridade tem campo próprio);
   - **TXT** `envio` → `v=spf1 include:amazonses.com ~all`.
8. **DMARC** na Cloudflare:
   - **TXT** `_dmarc` → `v=DMARC1; p=none`. Se já existir um `_dmarc`, edite esse: com dois, os provedores ignoram os dois;
   - relatórios: **Email** → **DMARC Management** → **Enable DMARC Management**. A Cloudflare acrescenta ao registro um `rua` com um endereço dela e mostra os relatórios no painel. Não use o seu Gmail no `rua`: endereço de outro domínio só recebe relatórios se esse domínio autorizar no DNS dele, e o Gmail não autoriza;
   - depois de 2 semanas com tudo aprovado nos relatórios, troque `p=none` por `p=quarantine`.
9. **Teste ainda no sandbox**, que só envia para endereços verificados:
   - se o Gmail não entrou pelo assistente: **Create identity** → **Email address** → o seu Gmail; clique no link que chega;
   - na identidade `render2go.com` → **Send test email**, de `teste@render2go.com` para o seu Gmail;
   - no Gmail → ⋮ → **Mostrar original**: SPF, DKIM e DMARC devem estar **PASS**.

**C. Produção, preço e limites**
10. **Preço:** **Pricing plan** → **Cancel plan**. A conta nova começa no plano Essentials (US$ 0,16 por mil); assim volta ao à la carte (US$ 0,10 por mil). Se a página já mostrar à la carte (sem plano), não há nada a fazer. Nenhum recurso pago liga sozinho: o Virtual Deliverability Manager só cobra se for ativado (passo 5).
11. **Sair do sandbox:** **Account dashboard** → no aviso "Your Amazon SES account is in the sandbox", **View Get set up page** → **Request production access**:
    - **Mail type:** Transactional;
    - **Website URL:** `https://app.render2go.com`;
    - **Additional contacts:** o seu e-mail;
    - **Preferred contact language:** English;
    - marque o **Acknowledgement** → **Submit request**.

    A AWS responde em até 24 h. Se pedir mais detalhes (em inglês), responda pelo caso de suporte que ela abrir, com este texto (ajuste o volume):

    > Render2Go (https://app.render2go.com) is a Brazilian service that turns product photos into short videos for small businesses. We use SES only for transactional email: one-time sign-in codes (8 digits, valid for 10 minutes), sent only to a person who requested one on our website, and replies to support requests. No marketing or bulk email. Abuse controls: Cloudflare Turnstile bot check, rate limits per email address, network and device, and only well-known mailbox providers are accepted. Bounces and complaints go to the SES account-level suppression list, and we watch the reputation dashboard. Expected volume at launch: up to 5,000 emails per day.
12. **Limites:** depois da aprovação, o **Account dashboard** mostra quantos e-mails por dia e por segundo a conta pode mandar. Para aumentar, antes de uma campanha grande: console **Service Quotas** → região Ohio → **AWS services** → **Amazon Simple Email Service (SES)** → **Sending quota** (por dia) ou **Sending rate** (por segundo) → pedir o aumento (resposta em até 24 h).

**D. Credenciais e servidor**
13. **SMTP settings** → quadro **IAM SMTP credentials** → **Create IAM credentials** → aceite o nome de usuário sugerido → **Create user** → **Download .csv file**. O usuário (`AKIA...`, 20 caracteres) e a senha (44 caracteres) aparecem uma vez só: guarde no gerenciador de senhas. Não são as chaves de acesso da AWS. Se perder a senha, apague esse usuário no IAM e crie outro.
    - **Não use o "SMTP email manager"**, mesmo marcado como recomendado: cobra o processamento do Mail Manager (US$ 0,15 por mil, além do envio) e troca a senha sozinha, o que derrubaria a senha gravada no `.env`.
    - **Na conta de projetos** (passo 4), o console avisa **"User created with some errors"**: o SES tenta pôr o usuário no grupo `AWSSESSendingGroupDoNotRename`, e a política da conta proíbe criar grupos e adicionar usuários a eles. Baixe o `.csv` mesmo assim e dê a permissão direto no usuário: **View user** → **Permissions** → **Add permissions** → **Create inline policy** → **JSON**, com `{"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "ses:SendRawEmail", "Resource": "*"}]}` → nome `EnvioSES` → **Create policy**. Sem isso, o SMTP aceita a senha e recusa cada envio ("Access denied").
14. **`.env` pelo terminal** (PowerShell → `ssh` no servidor): cole o bloco inteiro de uma vez e, quando ele pedir, cole o usuário e a senha do SMTP (a senha não aparece na tela nem fica no histórico; se a colagem vier vazia, ele pergunta de novo). Ele grava o servidor de Ohio na porta 587 (a Hetzner bloqueia a saída nas portas 25 e 465) e os remetentes `nao-responda@` e `suporte@render2go.com`; o `.env` anterior fica em `.env.antes-ses`, e o `RENDER2GO_SUPORTE_PARA` (onde você recebe o suporte) não muda.
    ```bash
    {
      cd ~/GPU2GO/servico/implantacao && cp -p .env .env.antes-ses
      USUARIO=; while [ -z "$USUARIO" ]; do read -rp "Usuário SMTP do SES: " USUARIO; USUARIO=${USUARIO//[$'\t\r ']}; done
      SENHA=; while [ -z "$SENHA" ]; do read -rsp "Senha SMTP do SES (não aparece na tela): " SENHA; echo; SENHA=${SENHA//[$'\t\r ']}; done
      sed -i -E '/^RENDER2GO_(SMTP_HOST|SMTP_PORTA|SMTP_TLS|SMTP_USUARIO|SMTP_SENHA|EMAIL_DE|SUPORTE_DE)=/d' .env
      [ -n "$(tail -c1 .env)" ] && echo >> .env
      printf '%s\n' \
        'RENDER2GO_SMTP_HOST=email-smtp.us-east-2.amazonaws.com' \
        'RENDER2GO_SMTP_PORTA=587' \
        'RENDER2GO_SMTP_TLS=starttls' \
        "RENDER2GO_SMTP_USUARIO=$USUARIO" \
        "RENDER2GO_SMTP_SENHA=$SENHA" \
        'RENDER2GO_EMAIL_DE=Render2Go <nao-responda@render2go.com>' \
        'RENDER2GO_SUPORTE_DE=Render2Go Suporte <suporte@render2go.com>' >> .env
      unset USUARIO SENHA
      grep -E '^RENDER2GO_(SMTP_HOST|SMTP_PORTA|SMTP_TLS|SMTP_USUARIO|EMAIL_DE|SUPORTE_DE|SUPORTE_PARA)=' .env
      awk '/^RENDER2GO_SMTP_SENHA=/{print "Senha SMTP gravada: " length($0) - 21 " caracteres"}' .env
    }
    ```
15. **Conferir, depois da atualização do servidor** (`docs/09`, seção 0): `docker compose logs api | grep AVISO` não fala mais do SMTP. Peça um código em https://app.render2go.com com um Gmail: ele chega na caixa de entrada, e "Mostrar original" diz **PASS** em SPF, DKIM e DMARC. Uma mensagem de teste em `/suporte` chega no `RENDER2GO_SUPORTE_PARA`.
16. **Acompanhar:** SES → **Reputation metrics**. Mantenha os devolvidos (bounces) abaixo de 5% e as reclamações abaixo de 0,1%; acima disso, a AWS revisa a conta e pode pausar o envio.

#### 6.1.2 Passo a passo: "não sou um robô" (Cloudflare Turnstile)

1. Entre no painel da Cloudflare (dash.cloudflare.com) com a conta que tem o domínio e abra a página **Turnstile**, no menu da conta (atalho: `https://dash.cloudflare.com/?to=/:account/turnstile`).
2. **Add widget** e preencha:
   - **Widget name:** `Render2Go - entrada`;
   - **Hostname management:** `render2go.com`. O domínio cobre também todos os subdomínios, inclusive `app.render2go.com` (a Cloudflare não aceita `*`). O servidor só aceita a verificação servida em `app.render2go.com`, o `DOMINIO` do `.env`, ou no domínio acima dele;
   - **Widget mode:** **Managed**. A Cloudflare decide se mostra a caixinha; a maioria das pessoas passa sem clicar;
   - **Pre-clearance:** **No**. Serve para o proxy da Cloudflare, que não está ligado.
3. **Create**. Aparecem a **Site Key** (pública, vai para a página) e a **Secret Key** (secreta, só no servidor). Guarde a secreta no gerenciador de senhas.
4. No `.env` do servidor:
   ```
   RENDER2GO_TURNSTILE_SITE=<Site Key>
   RENDER2GO_TURNSTILE_SEGREDO=<Secret Key>
   ```
   O `DOMINIO` continua `app.render2go.com`: não troque para `render2go.com`, porque o Caddy usa o `DOMINIO` para o certificado e o endereço do site.

   **Pelo terminal** (PowerShell → `ssh` no servidor): cole o bloco inteiro de uma vez e, quando ele pedir, cole cada chave e tecle Enter. A Secret Key não aparece na tela nem fica no histórico; colagem vazia é perguntada de novo; a linha antiga de cada chave é trocada, sem duplicar; e o `.env` anterior fica em `.env.antes-turnstile`.
   ```bash
   {
     cd ~/GPU2GO/servico/implantacao && cp -p .env .env.antes-turnstile
     SITE=; while [ -z "$SITE" ]; do read -rp "Site Key: " SITE; SITE=${SITE//[$'\t\r ']}; done
     SEGREDO=; while [ -z "$SEGREDO" ]; do read -rsp "Secret Key (não aparece na tela): " SEGREDO; echo; SEGREDO=${SEGREDO//[$'\t\r ']}; done
     sed -i '/^RENDER2GO_TURNSTILE_SITE=/d; /^RENDER2GO_TURNSTILE_SEGREDO=/d' .env
     [ -n "$(tail -c1 .env)" ] && echo >> .env
     printf 'RENDER2GO_TURNSTILE_SITE=%s\nRENDER2GO_TURNSTILE_SEGREDO=%s\n' "$SITE" "$SEGREDO" >> .env
     unset SITE SEGREDO
     grep '^RENDER2GO_TURNSTILE_SITE=' .env
     awk -F= '/^RENDER2GO_TURNSTILE_SEGREDO=/{print "Secret Key gravada: " length($2) " caracteres"}' .env
   }
   ```
5. Aplique com `docker compose up -d api`; na atualização desta versão, isso já entra no `docker compose up -d` do `docs/09`.
6. **Conferir:**
   - `docker compose logs api | grep AVISO` não fala mais do Turnstile;
   - no painel, seção "Segurança": "Verificação 'não sou um robô' (Turnstile) ligada";
   - numa janela anônima, em https://app.render2go.com, a caixa da verificação aparece no cartão de entrada, e o código chega.
7. **Se der erro:**
   - a caixa mostra erro de domínio: o widget não cobre `app.render2go.com` (cadastre `render2go.com`);
   - a página diz "não foi possível confirmar que você não é um robô (... invalid-input-secret)": a Secret Key do `.env` está errada;
   - "... de outro site": o `DOMINIO` do `.env` não é `app.render2go.com`.
8. **Depois:** na página do widget ficam os gráficos (quantos passaram e quantos foram barrados). Em **Settings** dá para mudar o widget. Se um dia a Secret Key vazar, gere outra ali e troque no `.env`.

### 6.2 Atualizar

Siga o `docs/09`, seção 0, "09/10/2026 (4)". A atualização leva poucos minutos; os pedidos em andamento continuam depois.

### 6.3 Logo depois de atualizar

- Painel → seção "Segurança": tudo em verde, salvo o que você decidiu deixar para depois (ex.: Pix).
- Crie uma conta de teste com um Gmail seu, peça um teste gratuito e aprove-o.
- **Clientes antigos:** no painel → "Contas e saldo", ligue o e-mail de cada um à conta (ou `render2go-servico email cli_x email`) e avise-os de que agora se entra pelo e-mail. O código antigo continua valendo.

### 6.4 DNS e Cloudflare

- **CAA** (só essas autoridades emitem certificado para o domínio): `render2go.com` CAA `0 issue "letsencrypt.org"` e `0 issue "sectigo.com"` (a reserva do Caddy, ZeroSSL, emite pela Sectigo), mais `0 iodef "mailto:<seu e-mail>"`.
- **DNSSEC:** Cloudflare → DNS → Settings → Enable DNSSEC; depois, cadastre o registro DS no registrador do domínio.
- **SPF, DKIM e DMARC:** item 6.1.1. Subdomínios que não mandam e-mail podem ter `v=spf1 -all`.
- **Proxy da Cloudflare (nuvem laranja):** ligue quando houver ataque de volume ou antes de uma campanha muito grande. Siga o `docs/10`, seção 7, e o comentário pronto no `Caddyfile`, que faz o Caddy confiar só nas faixas da Cloudflare. Feche o firewall da Hetzner para aceitar 80/443 só da Cloudflare. Com o proxy, a porta de origem do visitante (registro do Marco Civil) deixa de chegar.

### 6.5 Servidor (Hetzner)

- **Firewall da Hetzner** (Cloud Firewall): entrada só em 22/tcp (de preferência só do seu IP), 80/tcp, 443/tcp e 443/udp (HTTP/3).
- **SSH:** só com chave (`PasswordAuthentication no`), sem senha para o root; atualizações de segurança automáticas (`unattended-upgrades`).
- **Backups:** snapshots da Hetzner e um `pg_dump` diário cifrado fora do servidor, num bucket separado do dos clientes, com regra de ciclo de vida (ex.: 35 dias). O backup tem dados pessoais: o prazo dele precisa caber na Política de Privacidade.
- **Monitoramento:** um verificador externo (UptimeRobot, Better Stack) em `https://app.render2go.com/saude`, com alerta no celular. Ao subir, confira `docker compose logs api | grep AVISO`.
- **`.env`** com `chmod 600`, fora de qualquer backup público e fora do Git.

### 6.6 Contas e chaves

- **Verificação em duas etapas** em todas as contas que mandam no serviço: Cloudflare, Hetzner, RunPod, GitHub, Mercado Pago, provedor de e-mail e registrador do domínio.
- **RunPod:** alerta de saldo baixo e uma chave de API só para o orquestrador.
- **GitHub:** proteção da branch `main`, Dependabot e verificação de segredos ligados.
- **R2:** chave com permissão só no bucket `gpu2go`.

### 6.7 Jurídico

- **Revisão por advogado** dos Termos de Uso, da Política de Privacidade e da Política de Cookies, antes da divulgação em massa. Os textos têm as bases legais, os prazos e os direitos (LGPD, Marco Civil, CDC e Decreto 7.962/2013), mas não substituem essa revisão.
- **Encarregado de dados:** a Resolução CD/ANPD nº 2/2022 dispensa o agente de pequeno porte de indicar o encarregado, mas exige um canal com os titulares. O e-mail de privacidade cumpre esse papel.
- **Nota fiscal das recargas** e o tratamento fiscal do saldo pré-pago: com o contador.

## 7. O que continua em aberto

- **Revisão humana** é a principal proteção e também o gargalo (ADR 0004). Para escalar: mais revisores ou o filtro automático de imagem (ADR 0005, proposto).
- **Modelo Wan "Uncensored" em produção:** o `docs/12` recomenda trocar pelos arquivos oficiais.
- **D1 (termos do RunPod)** segue bloqueando qualquer uso adulto.
- **Cartão de crédito** (ADR 0008): depois, com 3-D Secure e limites.
- **Proxy da Cloudflare** contra DDoS (seção 6.4).
- **Grupo de pods por modelo**, quando entrar o 2º modelo (seção 5).
- **PgBouncer e réplica do Postgres:** só na fase de escala grande.
