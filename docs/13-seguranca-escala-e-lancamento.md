# 13 — Segurança, escala e lançamento

Revisão feita em 09/10/2026, a pedido do dono, antes do lançamento oficial e da divulgação em massa: o que estava exposto, o que mudou no código e no servidor, o teste de invasão, o que o serviço aguenta (medido) e a parte que só o dono pode fazer. As decisões estão no [ADR 0007](adr/0007-contas-sessoes-e-painel.md) (contas, sessão, painel e pods) e no [ADR 0008](adr/0008-recarga-pix-mercado-pago.md) (Pix). Os passos da atualização do servidor estão no `docs/09`, seção 0, "09/10/2026 (4)".

**Atualizado em 11/10/2026** com a área do cliente (verificação em duas etapas e confirmação recente) e os acessos do painel por pessoa ([ADR 0009](adr/0009-acessos-do-painel-por-pessoa.md)): seção 2.1, o teste de invasão com 88 verificações (seção 3), a carga medida de novo (seção 4.4) e os cuidados do dono (seção 6.8). A publicação está no `docs/09`, seção 0, "11/10/2026".

## 1. Resumo

| Pedido | O que foi feito | Onde |
|---|---|---|
| Teste gratuito só com conta, e-mail de provedor confiável, sem contas falsas em massa | Cadastro e entrada por código de 8 dígitos no e-mail; só Gmail, Outlook/Hotmail, Yahoo, iCloud, UOL, BOL e Terra; um teste por caixa (apelidos do Gmail contam como a mesma caixa); "não sou um robô"; limites por rede, e-mail e aparelho | ADR 0007 |
| Conta nova sem token, com direito só ao 1º uso do Wan (até 5 fotos, 480p, 3 s) | A conta nasce no teste gratuito no 1º acesso pelo e-mail; a 1ª recarga a passa para o pré-pago | ADR 0007, `docs/11` |
| "Eu não sou um robô" | Cloudflare Turnstile numa página isolada, conferido no servidor e fechado na dúvida | ADR 0007, item 3 |
| Cada "Tenho interesse" no painel | Cada clique, com a conta, o plano e a hora, além do resumo por opção | Painel, seção "Interesse" |
| Painel num endereço mais sofisticado | Endereço secreto + token + código do autenticador (2 fatores), bloqueio por tentativas, IPs permitidos (opcional), auditoria | ADR 0007, item 5 |
| Desligar um pod pelo painel, em emergência | "Desligar" em cada pod, "Parada de emergência" (encerra todos e não sobe outro até "Retomar") | Painel, seção "Pods" |
| APIs expostas e vulnerabilidades | Mapa da API desligado, cabeçalhos que revelavam o servidor removidos, token por pod, envio ao R2 com tamanho assinado, teste de invasão com 73 verificações (88 desde 11/10) | Seções 2 e 3 |
| Documentos legais | `/termos`, `/privacidade` e `/cookies`, com a identificação do vendedor no rodapé de todas as páginas | Seção 6.7 |
| API de pagamento | Recarga por Pix no Mercado Pago, creditada sozinha e uma vez só | ADR 0008 |
| Tráfego alto | Medido; correção de uma espera de 40 ms por resposta; fases de crescimento | Seção 4 |
| Vários modelos e 30+ pods no RunPod | Limites do RunPod, um modelo por pod, cuidados | Seção 5 |
| Sem `robots.txt` (decisão do dono) | Removido. Páginas privadas com `noindex` no cabeçalho; o endereço do painel não aparece em lugar nenhum | Seção 2 |
| Novos acessos ao painel com usuário e senha, depois do código do aplicativo (11/10) | Uma pessoa, um acesso: convite de uso único (QR Code → código do aplicativo → senha), senha só como resumo scrypt, autenticador próprio de cada pessoa, papéis dono e operador | ADR 0009; seção 2.1 |
| Código de autenticação de uso único e intransferível (11/10) | Cada código do aplicativo vale uma vez, gravado de forma atômica; o convite vale 24 h, uma vez e só para aquele nome de acesso | ADR 0009; seção 2.1 |
| Auditoria das alterações no painel: quando, IP e quem (11/10) | Seção "Auditoria": quem fez, o quê, em quem, quando, IP, navegador e o antes e o depois, com filtros e planilha | ADR 0009, item 7 |
| Verificação em duas etapas na conta do cliente (11/10) | Aplicativo autenticador com 10 códigos de recuperação, e confirmação recente ("é você mesmo?") antes das ações sensíveis | Seção 2.1 |

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

### 2.1 Área do cliente e acessos do painel (11/10/2026)

**Verificação em duas etapas do cliente** (opcional, em Configurações):
- aplicativo autenticador (TOTP, RFC 6238, o mesmo tipo de código do painel). O QR Code é um SVG feito no servidor (`segno`) e mostrado como imagem `blob:`; a pessoa tem 15 minutos para lê-lo e confirmar o primeiro código;
- o segredo fica **cifrado no banco** (AES-GCM, com uma chave derivada de `RENDER2GO_SEGREDO_CONTAS` só para isso, e o id da conta como dado associado: copiado para a linha de outra conta, não abre);
- cada código vale uma vez (o último passo de 30 s aceito fica gravado);
- **10 códigos de recuperação** de uso único, para entrar sem o celular: 10 letras e números, sem os que se confundem (~49 bits cada), guardados só como HMAC preso à conta;
- na entrada, o código do e-mail abre só um desafio, que não é sessão: vale 5 minutos e uma vez, e a sessão só nasce com o código do aplicativo ou um de recuperação. 5 erros bloqueiam a conta por 15 minutos;
- quem perdeu o celular e os códigos fala com o suporte, e só o dono pode desligar a verificação, pelo painel (Contas e saldo), com auditoria e aviso no e-mail da conta.

**Confirmação recente** ("é você mesmo?"): trocar o e-mail, ligar ou desligar a verificação, gerar códigos de recuperação novos e excluir a conta pedem uma confirmação dos últimos 10 minutos (a sessão aberta há menos de 10 minutos já conta):
- com a verificação ligada, só o código do aplicativo ou um de recuperação: o código do e-mail sozinho não basta, para quem tomou o e-mail não desligar a proteção;
- sem ela, um código de 8 dígitos no e-mail, com chave própria (não derruba um código de entrada pedido em outro aparelho), até 5 por hora e 10 por dia; 5 erros bloqueiam por 15 minutos;
- assim, um cookie de sessão roubado não basta para tomar a conta (trocar o e-mail) nem para apagá-la.

**Avisos e outros cuidados da conta:**
- **avisos de segurança no e-mail da conta**, só texto e sem links (como os códigos): conta excluída ou pausada, verificação ligada ou desligada (também pelo suporte), códigos de recuperação novos ou usados e troca de e-mail (o aviso vai para o endereço antigo, com o novo mascarado);
- troca de e-mail com código no endereço novo; o antigo fica só como resumo (HMAC), para não ganhar outro teste grátis numa conta nova;
- aparelhos conectados: encerrar um ou todos. A referência de cada sessão na página é derivada do id, que nunca sai do servidor, e outra conta não encerra a sessão de ninguém;
- as configurações só aceitam a sessão do navegador: o código de acesso antigo no cabeçalho recebe 403.

**Acessos do painel por pessoa** ([ADR 0009](adr/0009-acessos-do-painel-por-pessoa.md)):
- **senha só como resumo scrypt** (n = 2^15, r = 8, p = 1: ~0,1 s e 32 MiB por conferência, no máximo 4 ao mesmo tempo por processo), com a regra do NIST SP 800-63B: 12 a 128 caracteres, sem o nome de acesso, com pelo menos 5 caracteres diferentes e fora de uma lista de pedaços comuns em vazamentos;
- o segredo do aplicativo de cada pessoa é cifrado como o do cliente, com o id da pessoa como dado associado;
- **convite de uso único**, que vale 24 horas e só cria aquele nome de acesso. O token vai no fragmento do endereço (`#convite=…`), que o navegador nunca manda ao servidor, ao registro do Caddy nem a outros sites; o banco guarda só o SHA-256. A ordem é QR Code → código do aplicativo → senha: quem intercepta o convite sem o celular da pessoa não cria a senha;
- **entrada:** a mesma mensagem e o mesmo tempo para nome inexistente, senha errada e código errado (a senha é conferida sempre, mesmo contra um resumo de mentira); cada código do aplicativo vale uma vez, gravado de forma atômica (nem em duas abas ao mesmo tempo); 5 erros bloqueiam a rede por 15 minutos, e 5 erros em 15 minutos bloqueiam o nome de acesso, mesmo vindo de outras redes;
- **papéis por rota** (`PERMISSOES_PAINEL`, em `api.py`): o dono faz tudo; o operador revisa pedidos e consulta, sem mexer em dinheiro, pods, contas ou acessos. Rota com sessão fora do mapa é recusada para todos (fail-closed; um teste confere que toda rota está no mapa). Sempre fica um dono ativo, ninguém tira o próprio acesso, e tirar o acesso de alguém derruba as sessões dessa pessoa na hora;
- **a chave do servidor** (`RENDER2GO_ADMIN_TOKENS` + `RENDER2GO_ADMIN_TOTP`) continua como acesso de emergência, com papel de dono e registrada como emergência na auditoria;
- **auditoria** de cada ação (quem fez, o quê, em quem, quando, IP, navegador e o antes e o depois), também das entradas recusadas, das de emergência e dos convites. O `apagar-historico` não a apaga, e a planilha é protegida contra fórmulas.

**`RENDER2GO_SEGREDO_CONTAS` não pode mudar** depois que houver acessos por pessoa ou verificação em duas etapas ligada: os segredos do aplicativo deixam de abrir, e a entrada que depende deles é recusada (fail-closed). Se mudar, cada pessoa do painel precisa de um convite novo, e os clientes com a verificação ligada, de o dono desligá-la pelo painel (seção 6.8).

## 3. Teste de invasão

**Como:** `servico/tests/teste_invasao.py` sobe a API de verdade num Postgres de teste, atrás do `Caddyfile` de produção (com TLS local), e ataca de fora, como um invasor faria. Cada grupo de ataques sai de um IP próprio (127.0.0.x), como redes diferentes. Ele recusa rodar num banco cujo nome não tenha "teste", "invasao" ou "pentest", porque apaga o banco a cada rodada.

**Resultado em 09/10/2026: 73 de 73 verificações OK**, depois das correções abaixo. **Em 11/10/2026: 88 de 88**, com 15 verificações novas (acessos do painel por pessoa e configurações da conta, as duas últimas linhas da tabela), sem achados. **De novo em 12/10/2026**, com o filtro de imagem, a hibernação, o teste gratuito sem teto por IP e as fotos conferidas antes de abrir: 88 de 88, sem achados.

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
| Acessos do painel por pessoa (11/10) | Senha do convite antes do código do aplicativo; o mesmo convite de novo, de outra rede; convite chutado, com injeção SQL ou Unicode; o mesmo código do aplicativo duas vezes; operador nas rotas de pods, GPUs, pagamentos, acessos, auditoria e configuração; nome inexistente, senha errada e código errado; força bruta por rede e por nome de acesso | 409 (código primeiro); 401; 401; 401 na 2ª vez; 403 nas 8 rotas; a mesma resposta nos três casos; 429 na rede e no nome, também vindo de outra rede |
| Configurações da conta (11/10) | Rotas das configurações sem sessão e com o código de acesso antigo no cabeçalho; encerrar a sessão de outra conta pela referência; excluir a conta com uma sessão de 20 minutos sem confirmar; o desafio da verificação em duas etapas usado como sessão e usado duas vezes; código de recuperação repetido; força bruta no código do aplicativo | 401 e 403; 404 (IDOR); 403 "confirme que é você"; 401; 401 na 2ª vez; 401; 429 (conta bloqueada 15 min) |

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

### 3.1 Arquivos enviados pelos clientes (11/10/2026)

O dono perguntou se uma foto maliciosa poderia levar código ao servidor ou ao banco. O caminho de cada foto, conferido no código:

1. **O pedido declara** nome, tipo e tamanho de cada foto. A API aceita só `image/jpeg`, `image/png` e `image/webp`, até 50 MB e até 1.000 fotos (o teste gratuito, 5). O nome nunca vira caminho: a chave no R2 é gerada pela API, na pasta do pedido.
2. **A foto vai direto do navegador para o R2** por uma URL assinada que fixa o tipo e o tamanho: o R2 recusa outro tamanho. **A foto nunca passa pelo servidor nem fica no disco dele**, e nenhum byte dela vai para o banco: o banco guarda só nome, tipo, tamanho, chave e, depois, o SHA-256 e o resultado da análise, sempre por consulta parametrizada.
3. **Na confirmação do envio**, a API confere o tamanho de cada arquivo no R2 (16 consultas em paralelo) e só então cobra.
4. **O único lugar do servidor que abre a foto é o contêiner `analisador`** (aviso de pessoas): sem privilégios, disco só de leitura, sem os segredos da API e, desde 11/10, com **memória limitada a 1 GB** (uma foto feita para estourar a memória derruba só ele, que volta sozinho; a API e o Postgres seguem). Antes de decodificar, desde 11/10 (`pessoas.abrir_foto`):
   - o tipo é conferido pelos primeiros bytes (a assinatura do arquivo), e não pelo nome ou pelo que o navegador declarou;
   - o Pillow só usa os leitores de JPEG, PNG e WebP: TIFF, PSD, EPS (que chamaria o Ghostscript), BMP, GIF e os outros nem são tentados, porque são a maior superfície de ataque;
   - o tamanho em pixels sai do cabeçalho e precisa caber em 50 megapixels (a "bomba de descompressão" é um arquivo de poucos KB que declara bilhões de pixels);
   - o JPEG é decodificado já reduzido (`draft`), com menos memória.
   O arquivo que não passa vira "foto ilegível" ou "não é uma foto" na análise, para o revisor; nada é executado.
5. **No pod** (descartável, apagado ao encerrar), a mesma regra (`media.check_image` e `preparo`): só os três formatos, 50 MP no máximo, e a foto inválida falha o vídeo sem nova tentativa, com o valor devolvido. **Achado e corrigido:** o caminho da fila usava o teto padrão de 16,7 MP (4096×4096), e a foto de uma câmera de 24 MP ou de um celular no modo 48–50 MP falhava no pod; agora é o teto de 50 MP do preparo.
6. **Na volta para o cliente**, o vídeo e a folha são gerados pelos pods (nunca o arquivo enviado), e as fotos são servidas pelo domínio do R2, com o tipo fixado na assinatura: mesmo um arquivo disfarçado não roda como página no domínio do serviço (cookies, painel).

**Testes novos:** `servico/tests/test_pessoas.py` e `worker/tests/test_media_imagens.py`: as três imagens válidas abrem; página HTML, executável, zip, EPS, PHP começando com a assinatura do JPEG, TIFF, BMP, GIF, ICO e PPM disfarçados são recusados sem decodificar; a bomba de 10 bilhões de pixels em ~100 bytes e a de 64 MP são recusadas pelo cabeçalho; a foto de 24 MP passa na fila; o arquivo inválido vira erro na análise, sem parar o analisador.

**Dependências:** Pillow 12.3.0 no serviço e no worker, uma versão recente, que já traz as correções conhecidas dos leitores de imagem (como a do WebP de 2023, corrigida na 10.0.1). Atualizar o Pillow junto com as outras dependências, sempre com versão fixada.

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

### 4.4 Medido em 11/10/2026 (área do cliente e acessos por pessoa)

**Outra máquina que a da seção 4.1**: o contêiner de desenvolvimento (Xeon de 2,1 GHz, 4 CPU), também com a API, o Postgres e o gerador de carga na mesma CPU. Os números não se comparam com os de 09/10; o que vale é a versão de antes desta entrega (`87b12c6`) contra a nova, na mesma máquina e com a mesma ferramenta (`medir_carga.py`: 300 contas, 2 processos da API, 3 conexões, 30 s por cenário; cinco rodadas de cada versão, intercaladas). Pedidos por segundo:

| Cenário | Antes (`87b12c6`) | Nova | 95% abaixo de |
|---|---|---|---|
| Página do cliente acompanhando os pedidos (`GET /api/lotes`) | 385 a 411 | 376 a 400 | 11,5 a 13,1 ms nas duas |
| Misto (85% pedidos, 10% conta, 5% página inicial) | 419 a 453 | 413 a 437 | 11,2 a 12,4 ms nas duas |
| Página inicial | 2.029 a 2.157 | 1.998 a 2.105 | 2,3 a 2,5 ms nas duas |
| Rodadas pela metade, fora das faixas acima | 6 de 15 cenários (lotes 1, misto 3, página 2) | 1 de 15 (página) | 21 a 22 ms (lotes e misto); 3,7 ms (página) |

- **Leitura:** a capacidade da API ficou a mesma. As faixas se sobrepõem nos três cenários, nenhum pedido deu erro, e o tempo de cada `GET /api/lotes` medido em processo, sem rede nem concorrência, é igual nas duas versões (7,2 ms, com as mesmas consultas no perfil). Nesta máquina, algumas rodadas inteiras saem pela metade: todo pedido demora ~2×, nas duas versões, inclusive na página inicial, que só lê da memória e não mudou. Por isso a tabela mostra as rodadas normais à parte. Uma rodada anterior do mesmo dia, noutro contêiner, deu o mesmo quadro (lotes ~380–389 × ~374–379, misto ~400–422 × ~400–411, página ~1.830–1.958 × ~1.900–1.935) e, em sequência com 1 processo, o tempo de cada resposta: `/api/lotes` 5,26 → 5,33 ms, `/api/conta` 2,53 → 3,12 ms (a conta passou a trazer o perfil, o tema e os "Tenho interesse", numa transação só) e `/` 1,07 → 0,98 ms.
- **A lista de pedidos não ficou mais cara com o pedido em sequência:** `GET /api/lotes` é a rota mais pedida, e os vídeos voltaram a vir de uma consulta simples; os textos próprios das fotos vêm numa 2ª consulta, só para os pedidos que os têm (commit `421c8f2`, que tirou ~0,3 ms do Postgres em cada chamada).
- **As telas novas não ficam consultando a API:** Extratos, Logs, Perfil e Configurações pedem os dados uma vez, quando abrem. Só a tela de Pedidos se atualiza sozinha (a cada 10 s, com pedido em andamento e a aba à vista), como antes; e o painel, a cada 30 s na seção aberta, só para quem está nele. A conta de ~0,1 pedido/s por pessoa da seção 4.2 continua valendo.

### 4.5 Pedidos ao mesmo tempo (11/10/2026)

O dono perguntou sobre erros com muitos pedidos simultâneos. O que protege, com teste:
- **Saldo:** a cobrança trava a linha do cliente no Postgres (`SELECT ... FOR UPDATE`) e cada cobrança tem uma referência única. Teste novo: 10 pedidos da mesma conta terminam de subir no mesmo instante, com saldo para 3: exatamente 3 são cobrados, 7 recebem "saldo insuficiente" e o saldo termina em zero, nunca negativo. Sem a trava, o teste falha no Postgres (conferido tirando a trava).
- **Fila dos pods:** cada vídeo é reivindicado com `FOR UPDATE SKIP LOCKED` e prazo; 6 workers disputando 20 trabalhos pegam cada um uma vez só (teste existente). Vídeo de pod que cai volta para a fila quando o prazo vence, até 3 tentativas.
- **Rede:** o download da foto no pod tenta de novo nas falhas de rede; a falha da foto em si (tipo, tamanho) não se repete. O envio do navegador para o R2 é direto, sem passar pelo servidor.
- **Capacidade:** seções 4.1 e 4.4 (~400–800 pedidos/s na API com 2–3 processos, ~5.000 pessoas acompanhando pedidos ao mesmo tempo). Os limites de verdade são a GPU (`RENDER2GO_MAX_PODS`, o saldo e o estoque do RunPod) e a revisão humana; com mais pedidos do que pods, eles esperam na fila, intercalados por cliente, e a página mostra a previsão.
- **Medido de novo em 12/10/2026**, com o código desta entrega, na mesma máquina e com a mesma ferramenta da seção 4.4 (2 processos, 3 conexões): `GET /api/lotes` 381 e 387 pedidos/s (95% abaixo de 12,7 ms), misto 431 e 439, página inicial ~2.056, **nenhum erro**. A 1ª rodada de `GET /api/lotes` saiu pela metade (180/s, 95% abaixo de 22,5 ms), o mesmo fenômeno da seção 4.4; as outras duas voltaram à faixa. A capacidade não mudou.
- **Não testado ainda:** um teste de ponta a ponta com centenas de clientes enviando fotos reais ao R2 e vários pods ao mesmo tempo (custa GPU; fica para antes da divulgação grande, seção 7).

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

**Situação em 10/10/2026:** servidor atualizado (`docs/09`, seção 0); Amazon SES em Ohio com o domínio verificado (DKIM, MAIL FROM `envio`, DMARC com relatórios na Cloudflare) e a saída do sandbox pedida; Turnstile e painel novo funcionando. Faltam: aprovação do SES, dados da empresa (CNPJ e endereço fiscal), Mercado Pago, revisão do advogado e, depois de 2 semanas de relatórios limpos, o DMARC em `p=quarantine`.

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
3. **Segredos novos** (`docs/09`, seção 0, "09/10/2026 (4)", bloco 2, que gera e grava todos):
   - `RENDER2GO_SEGREDO_CONTAS`;
   - `RENDER2GO_ADMIN_CAMINHO`, o endereço do painel. Guarde num gerenciador de senhas, não nos favoritos sincronizados;
   - um `RENDER2GO_ADMIN_TOKENS` novo, porque o antigo ficou guardado em navegadores;
   - `RENDER2GO_ADMIN_TOTP`, cadastrado no aplicativo autenticador (Google Authenticator, Authy ou 1Password). Guarde o segredo também no gerenciador de senhas;
   - `RENDER2GO_WORKER_TOKENS` vazio.
4. **Identificação do vendedor** (rodapé e páginas legais): razão social, CNPJ, endereço, e-mail de contato e e-mail de privacidade (`RENDER2GO_EMPRESA_*` e `RENDER2GO_EMAIL_*`; `docs/09`, seção 0, bloco 3). Sem eles, as páginas mostram "[preencher]". Pessoa física pode usar o nome completo e o CPF (Decreto 7.962/2013, art. 2º, I), mas o endereço e o documento ficam públicos no site. Um e-mail do próprio domínio (ex.: `contato@render2go.com`) precisa de quem o receba: o Email Routing da Cloudflare (gratuito) encaminha para o seu Gmail.
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
- **A sua conta no plano interno** e o acesso ao painel: `docs/09`, seção 0, "09/10/2026 (4)", passo 7.
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

### 6.8 Acessos do painel (11/10/2026)

O passo a passo está no `docs/09`, seção 0, "11/10/2026" (bloco 5). Os cuidados de segurança:
- **O seu acesso por pessoa primeiro:** pelo terminal do servidor (`render2go-servico admin-convite --usuario <nome> --papel dono`). Daí em diante, entre com nome, senha e código; a chave do servidor fica guardada no gerenciador de senhas, só para emergência (sem ela, a volta é o mesmo comando, pelo terminal).
- **Um acesso por pessoa, com o menor papel que basta:** operador para quem só revisa e consulta. Ninguém divide nome, senha ou o QR Code.
- **O endereço do convite é tão sensível quanto uma senha** enquanto vale (24 horas): mande em mãos, pelo gerenciador de senhas ou numa mensagem que se apaga, nunca por e-mail aberto nem print.
- **Quem sai da equipe:** "Tirar acesso", na seção Acessos (as sessões da pessoa caem na hora). Celular perdido ou senha esquecida: um convite novo para o mesmo nome.
- **Nunca troque o `RENDER2GO_SEGREDO_CONTAS`** (seção 2.1). Guarde-o no gerenciador de senhas, junto com o resto do `.env`.
- **De vez em quando, olhe a Auditoria:** entradas recusadas em sequência, entradas de emergência que você não fez e convites que você não criou.
- **Cliente que perdeu o celular e os códigos de recuperação:** antes de desligar a verificação dele pelo painel, confirme que é a pessoa (a mensagem vem do e-mail da conta, e ela confirma um dado de um pedido ou de uma recarga).

## 7. O que continua em aberto

- **Revisão humana** é a principal proteção e também o gargalo (ADR 0004). Para escalar: mais revisores ou o filtro automático de imagem (ADR 0005, proposto).
- **Modelo Wan "Uncensored" em produção:** o `docs/12` recomenda trocar pelos arquivos oficiais.
- **D1 (termos do RunPod)** segue bloqueando qualquer uso adulto.
- **Cartão de crédito** (ADR 0008): depois, com 3-D Secure e limites.
- **Proxy da Cloudflare** contra DDoS (seção 6.4).
- **Grupo de pods por modelo**, quando entrar o 2º modelo (seção 5).
- **PgBouncer e réplica do Postgres:** só na fase de escala grande.
- **Chaves de acesso (passkeys, WebAuthn)** no painel e na conta: o melhor caminho a seguir depois do aplicativo autenticador; ficaram para depois (ADR 0009, alternativas). O acesso por pessoa já é a base para elas.
- **Mix e Projetos** (ADR 0010, proposto): o Mix depende do filtro de imagem (ADR 0005) ou da revisão humana entre os passos de imagem, e fica sem troca de rosto.
