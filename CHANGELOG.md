# Histórico de mudanças

Tudo o que mudou no projeto, do mais recente para o mais antigo. Detalhes técnicos estão nos documentos e ADRs citados, e cada item corresponde a commits no repositório.

## 11/10/2026: área do cliente, partes 2 a 5 (configurações, acessos do painel por pessoa e as páginas novas)

Motivo: o pedido do dono de 10/10 (área do cliente com menu lateral, pedido em sequência, painel em seções com visual novo, acessos por pessoa e auditoria). Publicação: [`docs/09`](docs/09-implantacao.md), seção 0, "11/10/2026" (backup, `docker compose up -d --build api` e o primeiro convite pelo terminal). Front-end: [`docs/10`](docs/10-identidade-visual-e-front-end.md), seções 4.2, 5.2 e 6; segurança e carga: [`docs/13`](docs/13-seguranca-escala-e-lancamento.md), seções 2.1, 3 e 4.4.

- **Configurações da conta (servidor e página):** tema (claro, escuro ou automático), troca de e-mail, verificação em duas etapas por aplicativo autenticador (QR Code e 10 códigos de recuperação), aparelhos conectados, pausar e excluir a conta, com um código de confirmação antes das ações importantes; perfil com nome, sobrenome e a foto do Gravatar, só se a pessoa ligar; telefone, nome de usuário e indicação como "Em breve".
- **Área do cliente:** menu lateral (Pedidos, Modelos de IA, Extratos, Logs, Perfil, Configurações; Mix e Projetos "Em breve"), cada tela no próprio endereço; pedido em sequência, com um texto padrão e textos próprios para as fotos escolhidas; extrato por rodada com planilha; logs das últimas 24 horas (assistir, baixar, excluir).
- **Painel:** 12 seções em 5 grupos, cada uma com endereço, rolagem e dados próprios; visão geral; **acessos por pessoa** ([ADR 0009](docs/adr/0009-acessos-do-painel-por-pessoa.md), aceito): convite de uso único (QR Code → código do aplicativo → senha), papéis dono e operador, chave do servidor como emergência; **auditoria** com quem, quando, IP, navegador, antes e depois, filtros e planilha CSV.
- **Mix e Projetos:** proposta no [ADR 0010](docs/adr/0010-mix-e-projetos.md) (cada passo do Mix como um pedido comum, custo antes de começar, sem troca de rosto).
- **Termos e privacidade v2**, aceitos de novo na próxima entrada.
- **Dependências novas:** `cryptography==50.0.2` e `segno==1.6.6` (reconstruir a imagem da API). `RENDER2GO_SEGREDO_CONTAS` não pode mais mudar.
- **Rotas novas da conta:** `POST /api/conta/confirmar/codigo` e `/api/conta/confirmar` (confirmação recente, 10 min); `POST /api/conta/mfa/iniciar`, `/mfa/ativar`, `/mfa/desligar` e `/mfa/codigos` (verificação em duas etapas) e `POST /api/conta/entrar-mfa` (2º passo da entrada); `POST /api/conta/email/codigo` e `/api/conta/email` (troca de e-mail); `GET /api/conta/sessoes`, `POST /api/conta/sessoes/{ref}/encerrar` e `/api/conta/sessoes/encerrar-outras` (aparelhos); `POST /api/conta/desativar` (pausar); `POST /api/conta/perfil`, `GET /api/conta/foto` e `POST /api/conta/preferencias` (perfil, foto do Gravatar e tema); `POST /api/conta/aceitar` (termos e privacidade v2). As telas `/modelos`, `/extratos`, `/logs`, `/perfil`, `/configuracoes`, `/projetos` e `/mix` entregam a mesma página, com `noindex`.
- **Rotas novas do painel** (no endereço secreto): `GET api/resumo` (visão geral); `GET api/acessos`, `POST api/acessos/convites`, `api/acessos/convites/{id}/cancelar`, `api/acessos/{id}/desativar` e `api/acessos/{id}/papel`; `POST api/convite/iniciar`, `api/convite/confirmar` e `api/convite/senha` (primeiro acesso); `GET api/auditoria` e `api/auditoria.csv`; `POST api/clientes/{id}/mfa/desligar`. `POST api/entrar` aceita nome de acesso, senha e código, além da chave do servidor; `GET api/sessao` diz o papel e as rotas que ele abre. Toda rota com sessão está em `PERMISSOES_PAINEL` (fora dele, recusada para todos).
- **Banco** (criado sozinho na subida da API, só somando ao de antes): tabelas `admins`, `admin_convites`, `codigos_recuperacao` e `emails_antigos`; colunas `clientes.primeiro_nome`, `ultimo_nome`, `gravatar`, `tema`, `mfa_segredo`, `mfa_ativo_em`, `mfa_ultimo`, `mfa_pendente`, `mfa_pendente_em`, `pausada_em` e `privacidade_versao`, `sessoes.confirmada_ate` e `contexto`, e `auditoria_admin.agente`; índices `sessoes_admin`, `codigos_recuperacao_cliente`, `admin_convites_usuario`, `auditoria_admin_pessoa` e `auditoria_admin_alvo`.
- **Testes:** suíte do serviço nos dois bancos (268), navegador com `test_paginas_conta.py` e `test_paginas_painel.py` novos (45 ao todo) e teste de invasão 88/88 (eram 73; 15 verificações novas, `docs/13`, seção 3). O teste do Ctrl+clique no menu (`test_menu_opens_each_screen_by_address_without_reloading`) falhou duas vezes no CI: esperava o "carregado" da aba nova, que nasce em `about:blank` e só depois navega; agora espera o endereço `/logs`.
- **Medições** (contra a versão de antes, `87b12c6`, na mesma máquina; `docs/10`, seção 5.2, e `docs/13`, seção 4.4): o painel parou de deslocar o layout (CLS 0,41–0,44 → 0 no celular e 0,07 → 0 no computador, em todas as seções); a área do cliente segue com CLS 0, e o LCP do pior caso é 0,98 s (Pedidos na 1ª visita com a rede lenta; era 0,77 s), dentro da faixa "bom". A 1ª visita ficou ~27 KB maior, porque as telas novas vêm na mesma página; na visita de volta, só o HTML revalidado (304) e os dados da API vão pela rede. A capacidade da API não mudou (cinco rodadas de carga de cada versão).
- **Visita de volta investigada:** a 1ª rodada de medição de 11/10 mostrava o HTML inteiro (13 a 16 KB) em cada volta. Era erro da medição: com o certificado local aceito por `ignore_https_errors`, o Chromium não guarda nada no cache. Atrás do Caddy, a revalidação volta 304 (o `encode` tira do `If-None-Match` o sufixo que ele mesmo põe na ETag); nenhuma mudança de código. O painel baixa o HTML a cada visita de propósito (`no-store`, ADR 0007).
- **Ferramentas:** `servico/tests/medir_paginas.py`, nova e versionada (a de 11/10 tinha ficado fora do repositório): FCP, LCP, CLS e bytes de cada tela, atrás do `Caddyfile` de produção, com o certificado confiado pela chave pública, o "não sou um robô" e a entrada por e-mail ligados e sem internet. `medir_carga.py` passa a apagar todas as tabelas do banco de teste (a lista fixa não tinha as 4 tabelas novas).

## 10/10/2026 (3): área do cliente, parte 1 (só o servidor): textos por foto, extrato por rodada e logs de geração

Motivo: o dono pediu uma área do cliente com menu lateral (perfil, projetos, extratos, logs, modelos de IA, mix e configurações), um pedido em sequência com textos próprios por foto e mudanças no painel (seções, acessos por pessoa, auditoria). **Esta entrega traz só a parte do servidor de três itens**, compatível com as páginas atuais (nada muda para quem usa o site hoje); as telas e o resto vieram nas partes 2 a 5 (entrada de 11/10/2026, acima). Para publicar: `git pull` e `docker compose up -d --build api` (as colunas novas são criadas sozinhas na subida; o worker dos pods não muda).

- **Pedido em sequência (API):** cada foto do pedido pode ter um texto próprio (`fotos[].prompt`, até 600 caracteres); a foto sem texto segue o texto padrão. O texto próprio passa pelas mesmas conferências do texto padrão (3 letras, filtro de conteúdo, termos dos presets), no máximo 100 textos distintos por pedido, e um texto igual ao padrão não conta. O revisor do painel vê o texto e o resultado do filtro de cada foto antes de aprovar. **Uma compilação por texto distinto** (fotos com o mesmo texto dividem), na vez do cliente, logo antes dos vídeos dessas fotos; o worker do pod não muda (recebe o texto da foto no lugar do texto padrão). Se o texto compilado de uma foto cai no filtro, só as fotos com esse texto são canceladas e devolvidas. A cópia dos dados (LGPD) traz os textos.
- **Extrato por rodada:** `GET /api/extrato` (7, 30, 90 ou 365 dias, ou tudo; página seguinte por `antes`) traz cada pedido como uma rodada, com o tipo de modelo, o modelo de IA, vídeos, duração, resolução, cobrado, devolvido e o total da rodada; as recargas e os ajustes; o resumo (gasto, devolvido, líquido, saldo) e o total por modelo. O modelo fica gravado no próprio movimento (coluna `movimentos.modelo`): a rodada continua completa depois da limpeza do histórico. `GET /api/extrato.csv`: a mesma lista numa planilha que abre direto no Excel (BOM, `;`, vírgula decimal, horário no fuso do navegador) e protegida contra fórmulas injetadas.
- **Logs de geração:** `GET /api/geracoes?horas=24` (de 1 a 168 h) lista os vídeos que a conta gerou e não excluiu, com o modelo, o horário e os endereços para ver e baixar. `POST /api/geracoes/{id}/excluir` apaga o vídeo e a imagem da conferência do armazenamento e fica nos registros de acesso; o pedido passa a mostrar o vídeo como excluído (sem endereço).
- **Banco:** colunas `fotos.prompt`, `fotos.prompt_en`, `movimentos.modelo` e `jobs.excluido_em`; índice `fotos_lote`.
- **Testes:** `tests/test_sequencia.py` e `tests/test_extrato.py` (SQLite e Postgres); suíte do serviço inteira passando.

## 10/10/2026 (2): revisão do front-end (estética, navegação e Core Web Vitals)

Motivo: o dono pediu uma nova passada em todas as páginas, só no front-end e na segurança do front-end, com UX, UI e os atributos de desempenho, acessibilidade e indexação. Detalhes em [`docs/10`](docs/10-identidade-visual-e-front-end.md), seções 4.1 e 5.1. Não muda a API nem o banco: no servidor, `git pull` e `docker compose up -d --build api`, que não reinicia o orquestrador ([`docs/09`](docs/09-implantacao.md), seção 0, "10/10/2026 (2)").

- **Topo conforme a sessão:** quem não entrou vê "Entrar" (leva ao cartão de entrada, com o foco nele) e "Suporte"; quem entrou vê "Pedidos", "Suporte" e "Sair" em todas as páginas, também no suporte, nas páginas de texto e na 404. O script do `<head>` (parte nova `sessao`) marca a sessão antes de desenhar: nada pisca nem empurra. A 404 e o "Voltar" do suporte mudam o texto conforme a sessão.
- **Entrada:** brilho anil e urucum no fundo e uma ilustração "fotos → vídeo" em SVG dentro do HTML (0,7 KB comprimido, decorativa, cores do tema; só a partir de 640 px).
- **"O que você quer criar?":** a opção disponível ocupa a linha inteira, com borda anil, seta e título maior; as "Em breve" ficaram compactas, com o selo e o "Tenho interesse" na mesma linha.
- **Rodapé único** (símbolo, frase da marca, links e identificação da empresa) em todas as páginas; a 404 ganhou "Cookies", "Preferências de cookies" e a identificação.
- **Páginas de texto:** ritmo de leitura e, nos termos e na privacidade, índice "Nesta página" fixo ao lado no computador, com a seção atual marcada; impressão só com o texto.
- **Celular:** janelas que sobem de baixo, na largura toda; margens que respeitam a área segura do iPhone.
- **Aviso de cookies:** "Aceitar todos" e "Só os necessários" com o mesmo destaque (guia da ANPD); no computador, fica no canto inferior esquerdo.
- **Core Web Vitals:** desde a tela de finalidades, as opções e o saldo chegam da API depois da 1ª pintura: na visita de volta (CSS e JS em cache), o painel empurrava os pedidos e o rodapé, com **CLS de 0,59 no celular e 0,24 no computador** ("ruim"); na 1ª visita, a linha "Conectado como … · saldo" quebrava depois da pintura (0,076); com o "não sou um robô" ligado, a entrada e o suporte também se mexiam (até 0,061 no suporte de quem entrou, no celular). Agora tudo o que chega depois tem o lugar reservado: linha da conta com altura fixa, conta pedida no `<head>` junto com as receitas (saldo, aviso do plano e opções no mesmo quadro), esqueleto no "Novo pedido", "Sair", o quadro da verificação e o aviso de conta do suporte já no HTML. **CLS zero em todas as telas**, também com a API 1,5 s mais lenta que a página (320 a 1440 px). Custo: o LCP do painel fica uma ida e volta à API mais tarde (o maior texto aparece junto com os dados), ainda na faixa "bom" ([`docs/10`](docs/10-identidade-visual-e-front-end.md), seção 5.1).
- **Acessibilidade:** "mais contraste", cores forçadas e impressão; contraste do texto sobre o brilho conferido no teste; `aria-current="location"` no índice. Tags `application-name` e `apple-mobile-web-app-title`.
- **Testes:** verificações novas para o topo e o "Sair" (com a marca antiga sem sessão), o que já vem reservado no HTML, o índice das páginas de texto, o CLS com a API lenta (e o esqueleto saindo quando a API falha), a janela no celular, os botões do aviso de cookies, topo/rodapé/script de sessão em cada página e o contraste sobre o brilho. Suíte do serviço inteira (SQLite e Postgres), axe-core sem violações nos dois temas e teste de invasão com 73 de 73.

## 10/10/2026: a versão de contas por e-mail no servidor

Motivo: o dono configurou o e-mail e atualizou o servidor, com os blocos do [`docs/09`](docs/09-implantacao.md), seção 0, "09/10/2026 (4)".

- **No ar desde 10/10/2026:** entrada pelo e-mail com o "não sou um robô", painel no endereço secreto com token e autenticador, `/termos`, `/privacidade` e `/cookies`; `/aprovar`, `/openapi.json` e `/docs` fechados (404); cabeçalhos de segurança e HTTP/3 conferidos por fora.
- **E-mail:** Amazon SES em Ohio (`us-east-2`), domínio verificado (Easy DKIM de 2048 bits, MAIL FROM `envio.render2go.com`, DMARC `p=none` com relatórios no DMARC Management da Cloudflare), e-mail de teste entregue; saída do sandbox pedida. Até a aprovação, o código só chega a e-mails verificados no SES.
- **Falta:** aprovação do SES, dados da empresa (o rodapé mostra "[preencher]"), Mercado Pago, revisão do advogado e o DMARC em `p=quarantine` depois de 2 semanas.
- **Documentação:** primeiro acesso ao painel, o que conferir quando o código do autenticador não entra (com o comando que mostra os códigos que o servidor aceita), o que fazer se perder o endereço, o token ou o autenticador, e a conta do dono no plano interno (`docs/09`, seção 0, passo 7); situação do lançamento no `docs/13`, seção 6.

## 09/10/2026 (4): contas por e-mail, painel secreto, Pix e segurança para o lançamento

Motivo: antes do lançamento oficial e da divulgação em massa, o dono pediu o cadastro aberto (sem código entregue à mão) sem contas falsas em massa, o painel num endereço mais seguro, desligar pods em emergência, cada "Tenho interesse" no painel, pagamento automático, documentos legais, uma revisão completa de segurança com teste de invasão e uma resposta sobre tráfego alto. Decisões: [ADR 0007](docs/adr/0007-contas-sessoes-e-painel.md) e [ADR 0008](docs/adr/0008-recarga-pix-mercado-pago.md); revisão, medições e a lista do dono: [`docs/13`](docs/13-seguranca-escala-e-lancamento.md). **A atualização do servidor tem passos antes** ([`docs/09`](docs/09-implantacao.md), seção 0).

### Contas pelo e-mail
- **Cadastro e entrada sem senha:** o cliente informa o e-mail e recebe um código de 8 dígitos (uso único, 10 min, 5 tentativas; o banco guarda só um HMAC dele). No 1º acesso, a conta nasce no **teste gratuito** (um pedido, até 5 fotos, 480p, 3 s).
- **Só provedores confiáveis:** Gmail, Outlook/Hotmail, Yahoo, iCloud, UOL, BOL e Terra (pedem verificação para abrir uma caixa). Descartáveis e domínios próprios ficam de fora; o painel cria a conta de um e-mail de empresa.
- **Um teste por caixa:** apelidos do Gmail (pontos e "+"), "+" nos outros e "-palavra" no Yahoo contam como a mesma caixa; o e-mail de uma conta excluída volta sem teste.
- **"Não sou um robô"** (Cloudflare Turnstile) numa página isolada, `/verificacao`, conferido no servidor e fechado na dúvida; vale também no suporte sem conta.
- **Limites no banco**, válidos entre processos: códigos por rede (IPv6 por /64), e-mail e aparelho; credenciais erradas bloqueiam a rede por um tempo.
- **Sessão em cookie** `__Host-` HttpOnly (30 dias); "Sua conta": baixar os dados, sair de todos os aparelhos e excluir a conta (LGPD). Os clientes antigos entram com o código de acesso de sempre.
- **Provedor de e-mail escolhido: Amazon SES** (Ohio, preço à la carte), o mais barato em volume e o que mais aguenta pico (comparação com Resend, Postmark e Brevo e passo a passo em [`docs/13`](docs/13-seguranca-escala-e-lancamento.md), seção 6.1.1). Num pico, o provedor recusa o que passa do limite por segundo (no SES, "454 Throttling"): o envio do código e do suporte tenta de novo em 1 s e em 4 s; erro definitivo (endereço recusado, senha errada) não é repetido.
- **Passo a passo do SES** conferido nas páginas da AWS (`docs/13`, seção 6.1.1): plano Paid da conta (o Free encerra em 6 meses), alerta de gasto, domínio com DKIM, MAIL FROM e DMARC, teste no sandbox, saída do sandbox (com o texto para a AWS), limites pelo Service Quotas e o `.env` pelo terminal. Sem `RENDER2GO_SUPORTE_DE`, o suporte usava como remetente o usuário do SMTP, que no SES não é um e-mail (o envio seria recusado); agora usa o remetente dos códigos.
- **SES em Ohio (`us-east-2`):** a conta da AWS veio na "nova experiência" (projetos), que prende o projeto à região do país do endereço (Brasil → Ohio); outra região só com "Ativar recursos avançados", que não tem volta. Ohio basta: ~1 s por e-mail a partir de Nuremberg (São Paulo seria ~2 s; Frankfurt, uma fração de segundo). O guia (`docs/13`, seção 6.1.1, passo 4) explica o aviso "Região indisponível" e manda conferir a região no MX antes de publicar o DNS.
- **Atualização do servidor em blocos testados** (`docs/09`, seção 0, "09/10/2026 (4)"): backup e imagem nova; painel e segredos (gera o endereço secreto, o token novo, a chave do autenticador e o segredo das contas, mantém o segredo das contas que já existir e não troca nada se rodar de novo); dados da empresa (pergunta e grava, sem apóstrofo e com e-mail conferido); a lista do que o `.env` ainda não tem; e o e-mail que recebe o suporte com 2 processos da API. Testados num terminal simulado, com colagem vazia, apóstrofo e acentos, e os valores conferidos pelo código do serviço e pelo `docker compose config`.
- **Credenciais SMTP do SES na conta de projetos** (`docs/13`, seção 6.1.1, passo 13): "Credenciais SMTP do IAM", não o "Gerenciador de e-mail SMTP" (cobra à parte e troca a senha sozinha); a política da conta impede o grupo que o SES usa, então a permissão de envio vai direto no usuário (política em linha `ses:SendRawEmail`).
- **SES pelo assistente "Configurar"** (conta nova, `docs/13`, seção 6.1.1, passo 5): MAIL FROM `envio` já no assistente, seguir sem plano de preços e com as etapas opcionais desligadas (o Virtual Deliverability Manager custa à parte e pode segurar e-mails, o que atrasaria os códigos). DMARC corrigido: os relatórios vão para o DMARC Management da Cloudflare, porque um endereço do Gmail no `rua` não recebe relatórios de outro domínio; e um `_dmarc` só.
- **Passo a passo do Turnstile** na Cloudflare: `docs/13`, seção 6.1.2. O widget fica cadastrado com `render2go.com`, que cobre `app.render2go.com`. O servidor aceita a verificação servida no endereço do serviço (`DOMINIO`) ou no domínio acima dele, porque a Cloudflare não deixa claro qual dos dois informa.

### Painel do dono
- **Sai do `/aprovar`** (agora 404) para um endereço secreto (`RENDER2GO_ADMIN_CAMINHO`), com token e código do aplicativo autenticador (TOTP), bloqueio de 15 min depois de 5 erros, lista opcional de IPs e auditoria de cada ação.
- **Pods:** "Desligar" em cada pod e "Parada de emergência" (encerra todos e não sobe outro até "Retomar"); o orquestrador executa em até ~10 s e dá sinal de vida.
- **Interesse:** além da contagem, cada clique em "Tenho interesse", com a conta, o e-mail, o plano e a hora.
- **Contas e saldo** (busca, plano, e-mail, desativar), **Recargas por Pix** e **Segurança** (o que falta configurar e as ações do painel).

### Recarga por Pix (Mercado Pago)
- O cliente escolhe o valor e paga o QR na própria página; o saldo entra quando a API do Mercado Pago confirma, uma vez só. O aviso (webhook) precisa da assinatura e só serve de gatilho; valor divergente não credita; estorno retira o saldo. A 1ª recarga tira a conta do teste gratuito.

### Documentos legais
- **`/termos`, `/privacidade` e `/cookies`**, com as bases legais, os prazos de guarda, os direitos (LGPD, art. 18), o Marco Civil, o CDC e a identificação do vendedor (Decreto 7.962/2013) no rodapé de todas as páginas. Falta a revisão de um advogado.

### Segurança do servidor e teste de invasão
- **Fechado:** o mapa da API (`/openapi.json`, `/docs`, `/redoc`), os cabeçalhos que revelavam o servidor (`Server`, `Via`), o `robots.txt` (decisão do dono), o envio ao R2 sem limite de tamanho (agora o tamanho vai na assinatura) e o token de worker igual para todos os pods (agora um por pod, revogado no fim).
- **Contêineres:** disco só de leitura, sem as capacidades do Linux, `no-new-privileges`, logs com tamanho limitado e cada processo só com os segredos que usa; análise das fotos no contêiner próprio `analisador`. Caddy com tempos limite, HTTP/3 e log de acesso com rotação.
- **Teste de invasão** (`servico/tests/teste_invasao.py`): 73 verificações (descoberta, travessia, cabeçalhos, autenticação, IDOR, CSRF, injeção, webhook, sessões e limites), todas OK depois de 4 correções: NUL num texto dava erro 500; credenciais com caracteres fora do ASCII davam erro 500; dígitos de outros alfabetos passavam; e a resposta para um e-mail de empresa dizia se havia conta antes da verificação anti-robô. Mais um teste no navegador: HTML e JavaScript de clientes aparecem como texto no painel.
- **A API avisa ao subir** o que falta configurar (`docker compose logs api | grep AVISO`) e não sobe com variável obrigatória faltando.

### Escala
- **Pool de conexões** do Postgres, **vários processos** da API (`RENDER2GO_API_WORKERS`), cache curto da previsão, consultas da fila em lote, índices novos e trava de líder no orquestrador.
- **Achado na medição:** com vários processos, cada resposta numa conexão reaproveitada esperava ~40 ms (TCP sem `TCP_NODELAY`); corrigido. Medido em 4 vCPU: ~475–810 pedidos/s com 2–3 processos, 95% abaixo de 10 ms (`servico/tests/medir_carga.py`; `docs/13`, seção 4).
- **GPUs:** limites do RunPod (US$ 80/h por padrão), 30+ pods e um modelo por pod, em `docs/13`, seção 5.

## 09/10/2026 (3): tela de finalidades e licenças dos modelos

### "O que você quer criar?" antes do pedido
- **Blocos com as 6 finalidades mais usadas** no mercado: foto vira vídeo, vídeo a partir de um texto, editar ou melhorar uma foto, imagem a partir de um texto, pessoa falando o seu áudio e mudar o estilo de um vídeo. "Ver todas as opções" abre uma janela com as 12, agrupadas pelo que o cliente envia (foto, texto, vídeo, áudio): também ampliação de resolução, 3D, locução, trilha sonora, som para vídeo e imagem a partir de um áudio.
- **Só "Foto vira vídeo" (Wan 2.2) está disponível.** As outras aparecem como "Em breve", sem deixar escolher, com o botão **"Tenho interesse"**: cada cliente conta uma vez por opção (tabela `interesses`), e o `/aprovar` mostra a contagem, seção "Interesse", para decidir o próximo modelo pela demanda.
- **Depois de escolher:** o cliente vê o modelo e para que ele é melhor ("Wan 2.2 é melhor para: produto parado com câmera suave…") e então o formulário de sempre; "Trocar" volta aos blocos. Na janela, cada opção em breve mostra os modelos previstos.
- **Catálogo único** em `servico/src/render2go_servico/modelos.py` (finalidades, modelos, para que cada um serve, licença). A API manda o catálogo em `/api/receitas`; `POST /api/interesses` e `GET /api/admin/interesses` são novos. Ícones novos de linha: texto, editar, camadas, ampliar, cubo, áudio, música e grade.

### Modelos e licenças comerciais ([`docs/12`](docs/12-modelos-e-licencas.md))
- **Conferidos nos cartões e nos textos das licenças**, por finalidade: quais são liberados (Apache 2.0, MIT, BSD-3), quais têm condições (LTX: abaixo de US$ 10 mi de faturamento; Tencent Hunyuan: fora da UE, Reino Unido e Coreia do Sul; CogVideoX: registro e até 1 mi de visitas/mês; Stability: abaixo de US$ 1 mi; SDXL: restrições de uso nos termos) e quais não são comerciais (FLUX dev, XTTS-v2, F5-TTS, MusicGen, MMAudio, OmniGen2).
- **OmniGen2 caiu na conferência:** o cartão diz Apache 2.0, mas ele embute o Qwen2.5-VL-3B, de licença só para fins não comerciais.
- **Risco nos modelos em uso:** tudo o que roda hoje permite uso comercial, mas o Wan em produção é um ajuste da comunidade marcado "Uncensored" e o codificador de texto tem vocabulário sexual explícito (herança do projeto original). Recomendado trocar pelos arquivos oficiais Apache 2.0, com ADR e teste em GPU, e o tradutor por um Qwen 2.5 comum.

## 09/10/2026 (2): qualidade dos vídeos, pedidos leigos e relatório de GPUs

Motivo: no teste gratuito de 08/10 (5 fotos de carros, 480p, 3 s), 2 dos 5 vídeos começaram na foto e depois trocaram de carro: um cortou para um Kwid branco aos 2 s, o outro se fundiu num carro de outra marca. A conferência só olhava o 1º quadro, e o pedido ("crie um vídeo de apresentação desses carros pra cada uma dessas fotos") foi traduzido ao pé da letra, no plural.

### Conferência do vídeo inteiro ([`docs/08`](docs/08-varios-clientes-e-demanda-alta.md), seção 2)
- **Além do 1º quadro, a continuidade:** o worker mede a cor média do centro de cada quadro e compara com o 1º quadro (deriva) e com o anterior (salto). Corte brusco ou produto que muda de cor reprova o vídeo.
- **Limites calibrados em 39 vídeos:** os 37 aproveitáveis de 06 a 08/10 ficaram até 31,5 de deriva e 5,8 de salto; os 2 com troca deram 54 e 86 de deriva e 16,2 de salto no corte. Limites: deriva 40, salto 12. Os 2 seriam barrados; nenhum dos 37 bons seria.
- **Refeito com outra seed:** o vídeo reprovado não é enviado e volta para a fila com outra variação aleatória (até 3 tentativas no total). Se não sair, não chega ao cliente e, no pré-pago, volta para o saldo.
- **Fail-closed:** a API recusa "ok" sem as medidas. **Limitação:** a troca por um objeto da mesma cor passa.
- **Registro:** a avaliação dos 5 vídeos do teste, as medidas dos 39 vídeos e as métricas descartadas estão em [`worker/evals/conferencia-2026-10-09/`](worker/evals/conferencia-2026-10-09/LEIAME.md).

### Pedido em cena única, para pedidos leigos e detalhados
- **Tradutor v6** (`worker/prompts/compiler-i2v.v6.txt`): o texto vale para cada foto, uma de cada vez. Ordens ao serviço ("crie um vídeo", "pra cada foto", "para o Instagram") saem; plural que só quer dizer "as fotos" ("desses carros") vira um carro só; pedido leigo sem ação ("deixe bonito", "apresentação do carro") vira movimento suave de luz sobre o produto, com a câmera parada; pedido detalhado continua traduzido inteiro.
- **Termos de continuidade anexados pelo código** (presets v3 e receitas v3, em todos os estilos): "a single continuous uninterrupted shot, the same subject from the image stays in view from the first frame to the last, its shape, colors and details stay exactly the same". Termos afirmativos: com CFG 1 o prompt negativo não age.
- **Fidelidade (casos v2: os 39 da v1, iguais, mais 6 do serviço; CPU, 7B):** v6 com 66% dos casos ok contra 42% da v5; 65% contra 49% nos casos antigos e 67% contra 17% nos do serviço; 94% sem invenção contra 83%. Ainda falham: o plural com verbo ("os produtos giram"), "faróis" (sai "Faros") e 1 de 18 saídas que escreveu "She" num pedido leigo. Detalhes em [`worker/evals/compiler/RESULTADOS.md`](worker/evals/compiler/RESULTADOS.md).
- **Câmera contraditória:** quando o próprio tradutor descreve a câmera se movendo, o código remove o "Static camera." que ele escreveu no fim.

### Página do cliente
- **Aviso de foto pequena:** a página mede cada foto escolhida e avisa quando ela vai ser ampliada 1,5× ou mais para o tamanho do vídeo (ex.: 300×200 para 832×480). Não bloqueia o envio; a miniatura ganha o selo "baixa resolução".
  - **Por que não "foto borrada":** a nitidez medida pela imagem não separou as fotos ruins das boas (uma xícara de estúdio, com fundo desfocado, mediu igual à foto borrada do carro vermelho). Fica a dica de usar a foto original.
- **Ajuda do texto:** o campo explica que o texto vale para cada foto e mostra exemplos simples e detalhados.

### `/aprovar`, seção "GPUs"
- **Cada GPU que subiu:** pod no RunPod, GPU, datacenter, preço por hora, tempo até o 1º vídeo, tempo ligado, vídeos gerados e com falha, pedidos atendidos, uso da GPU, custo estimado e situação (inclusive "falhou na subida").
- **Totais do período** (24 h, 7, 30 ou 90 dias): GPUs, tempo ligado, custo, vídeos e custo por vídeo; botão para baixar o relatório (JSON).
- **Custo = preço por hora × tempo ligado**, da criação do pod ao desligamento. O id e o preço são gravados assim que o RunPod cria o pod, mesmo que a instalação falhe depois.

### Servidor
- Atualize com o `docker compose up -d --build` de sempre ([`docs/09`](docs/09-implantacao.md), seção 0). Os pods usam o código do servidor, então API e pods mudam juntos.

## 09/10/2026: pré-pago, preços por segundo e teste com 5 fotos

### Pré-pago no plano pago ([`docs/11`](docs/11-custos-e-planos.md), seção 4)
- **Sem planos nem assinaturas:** o cliente tem saldo em reais, recarrega quando quiser e paga por segundo de vídeo.
  - **480p: R$ 0,25/s; 720p: R$ 0,50/s** (US$ 0,05 e US$ 0,10 no câmbio de R$ 5,00). Um vídeo de 5 s em 480p custa R$ 1,25.
  - Os preços mudam no `.env`: `RENDER2GO_PRECO_480P_S` e `RENDER2GO_PRECO_720P_S`.
- **Cobrança:** o pedido inteiro, quando as fotos terminam de chegar. Sem saldo, a página avisa antes do envio, com o preço.
- **Devolução automática:** pedido recusado volta inteiro; cada vídeo que não sai volta sozinho para o saldo. Nada é cobrado ou devolvido duas vezes, e o mesmo Pix não entra duas vezes.
- **Página do cliente:** saldo, preços, extrato e o custo do pedido antes de enviar; cada pedido mostra quanto custou e quanto voltou.
- **`/aprovar`, seção "Clientes e saldo":** saldo de cada cliente, recarga (ou ajuste) com o código do Pix e extrato para baixar. Pelo terminal: `render2go-servico clientes` e `render2go-servico saldo`.
- **O extrato é registro financeiro:** a limpeza do histórico não o apaga.
- **Ainda manual:** o cliente paga por fora (Pix) e o dono lança a recarga. Pagamento automático é o próximo passo.
- **Margem:** a GPU fica entre 9% e 16% do preço; custos e receita nos cenários estão no `docs/11`, seções 4.2 e 4.3.

### Duração e teste gratuito
- **Vídeos de 4 a 7 s** nos planos pago e interno. Vídeos de 3 s ficam só no teste gratuito.
- **Teste gratuito:** um pedido, vídeos de 3 s em 480p, **até 5 fotos** (eram 10) e 5 por dispositivo; 50 por IP, como antes.

### Servidor
- **Depois de atualizar, lance o saldo dos clientes do plano pago:** sem saldo, eles não conseguem pedir. Os passos estão no [`docs/09`](docs/09-implantacao.md), seção 0.

### Correções
- **Formulários lado a lado:** o campo sem dica não estica mais o próprio controle.

## 08/10/2026 (noite): fila, previsão, subida mais rápida, GPU, custos e limpeza

### Fila e previsão
- **Pedidos da mesma conta em ordem de chegada:**
  - a vez na fila passou a ser por cliente: vai primeiro quem tem menos vídeos começados;
  - no mesmo cliente, o pedido mais antigo sai antes;
  - no mesmo pedido, os vídeos saem na ordem da lista.
  - Antes a vez era por pedido, e o pedido novo de uma conta passava na frente do antigo da mesma conta ([`docs/09`](docs/09-implantacao.md), seção 5.1.5).
- **Previsão fiel com o pod desligado:**
  - o orquestrador registra cada pod (tabela `geradores`: subindo, pronto, encerrado);
  - a previsão usa esse estado e a subida medida (mediana das últimas 10, da decisão de ligar até o 1º vídeo);
  - os vídeos à frente são contados na mesma ordem da fila.
  - Antes, um pod desligado havia menos de 10 min contava como ligado ("começa em instantes"), e um pod no meio da subida não descontava o tempo já passado.

### Subida do pod mais rápida ([`docs/07`](docs/07-guia-tecnico-gpus-e-pods.md), seção 9)
- **Modelos só do espelho (R2) quando ele passa no teste de rede:**
  - o espelho é testado primeiro;
  - os 37 GB baixaram em 44 s num host em que o Hugging Face dava 39 MB/s;
  - antes, as duas origens misturadas levaram 175 s.
- **Durante o download dos modelos:** ComfyUI e Ollama sobem, e o worker já traduz o pedido. O render só começa com os modelos conferidos (estado `PRONTO`).
- **Orquestrador:** olha a fila a cada 10 s (eram 30 s).
- **Consultas ao pod:** a cada 3 s (eram 10 s).
- **apt:** instala primeiro só o que o download usa.
- **Medido em 3 pods reais** (`worker/evals/gpu-2026-10-08-subida/`): do pedido do pod ao 1º vídeo, de **6,8 min** para **4,2 min** (RTX PRO 6000) e **5,1 min** (L40S).
- **Tentado e retirado:** o ambiente pronto no R2, porque o envio pelo pod não terminou em 15 min.
- **Os testes custaram ~US$ 1,00 do saldo do RunPod:** o envio que não terminou deixou um pod RTX PRO 6000 ligado 19 min.

### GPU
- **A RTX PRO 6000 subiu de US$ 2,09 para US$ 2,49/h.** A nova ordem é L40S (US$ 1,09/h, medida) → RTX 6000 Ada (US$ 0,99/h) → RTX PRO 6000 → RTX 4090.
  - Subida e pod quente custam menos da metade por pedido (~US$ 0,11 contra ~US$ 0,26), e o vídeo ~14% menos.
  - Em troca, cada vídeo leva ~2× mais; nos lotes grandes, o orquestrador sobe mais pods.
- **Configurável no `.env`, sem mudar o código:**
  - `RENDER2GO_GPUS`: ordem das GPUs;
  - `RENDER2GO_CICLO_S`: intervalo do ciclo;
  - `RENDER2GO_POD_OCIOSO_MIN`: tempo de pod quente.
- **Orçamento e desligamento automático** passam a contar o preço da opção mais cara com vaga.

### Custos e planos ([`docs/11`](docs/11-custos-e-planos.md))
- **Custos mensais:**
  - parado: ~R$ 43;
  - escala pequena (300 vídeos/mês): ~R$ 136;
  - demanda alta (30 mil vídeos/mês): ~R$ 7.400, ~96% de GPU.
- **Proposta de planos por créditos (não aplicada; trocada pelo pré-pago em 09/10):** Avulso R$ 19,90, Essencial R$ 49,90, Profissional R$ 129,90 e Agência R$ 349,90. O custo de referência é o medido pelo dono, ~US$ 0,03 por vídeo.

### Limpeza do histórico
- **`render2go-servico apagar-historico`:**
  - apaga pedidos, prompts, trabalhos, autorizações, eventos, suporte e registro dos pods;
  - apaga os arquivos dos clientes no R2 (`entrada/` e `saida/`; nunca o espelho dos modelos);
  - reescreve as tabelas (`VACUUM FULL`).
  - Os clientes e os códigos de acesso ficam. Sem `--confirmar`, só mostra o que seria apagado.
- **Servidor:** os passos estão no [`docs/09`](docs/09-implantacao.md), seção 0.

### Correções
- **Envio ao espelho no modo fila:** a espera pelo envio terminava na hora e o abortava, porque o prazo de desligamento do pod não chegava até ela. Agora cada pod guarda o seu. O pod que envia é encerrado fora do ciclo do orquestrador e sai da previsão assim que o worker termina.
- **Testes sem o R2 de verdade:** as credenciais do ambiente ficam de fora dos testes.
- **Tabela nova criada sem disputa:** a API e o orquestrador sobem juntos e criam as tabelas novas ao mesmo tempo. No Postgres, isso podia derrubar um dos dois na 1ª subida depois de uma atualização (ele voltava sozinho, reiniciado pelo Docker). Agora um espera o outro.

## 08/10/2026: identidade visual e front-end das páginas

### Identidade visual ([`docs/10`](docs/10-identidade-visual-e-front-end.md))
- **Conceito:** anil (ações e confiança) e urucum (acento de movimento), dois pigmentos brasileiros, com tinta e papel como neutros. Modo claro e escuro, com contraste AA conferido em teste (28 pares).
- **Logo:** símbolo (play com traços de movimento) e a palavra "Render2Go" em contornos. Favicon, ícones de tela inicial, manifesto e imagem de compartilhamento (1200 × 630) saem do `tools/marca.py`.
- **Tipografia:** títulos em Bricolage Grotesque (OFL), só com os caracteres do português (13 KB); texto na fonte do aparelho.
- **Regras de layout e componentes documentados:** celular primeiro, alvos de toque de 44 px, pedido em passos numerados, 25 ícones próprios.

### Páginas
- **Cliente (`/`):**
  - quem chega vê a apresentação e "Como funciona" ao lado do código de acesso;
  - pedido em passos, com prévia das fotos (dá para tirar uma por uma), arrastar e soltar (inclusive pastas), exemplo de texto para o estilo escolhido e contador de caracteres;
  - autorização destacada quando o estilo exige; erros no próprio campo, com o foco nele;
  - envio com progresso em bytes e porcentagem, ✓ em cada foto enviada e confirmação antes de fechar a página no meio;
  - vídeos assistidos na própria página, com "Abrir em outra aba" e "Baixar";
  - código inválido ou desativado volta ao formulário com a explicação (antes, a mensagem ficava escondida);
  - quem já tem o código vê o painel desde o primeiro quadro.
- **Revisão (`/aprovar`):** menu de seções com o número de pendentes, também no título da aba; aprovar em verde e recusar em vermelho; as fotos não são baixadas de novo a cada atualização; confirmação própria para desativar um código; código novo com botão "Copiar".
- **Suporte e 404:** validação no campo e contador; endereço inexistente abre uma página 404 com caminhos de volta.

### Desempenho, acessibilidade e segurança ([ADR 0006](docs/adr/0006-front-end-build-entrega-e-seguranca.md))
- **Build com esbuild** (versão fixada): JS e CSS minificados, um script por página (o código da revisão não vai mais para o cliente), HTML compacto, versão por hash e SRI. Arquivos com cache de 1 ano; páginas revalidadas a cada visita (ETag). Caddy com zstd.
- **Medido em 4G lenta, celular:** no painel, LCP de 0,71 s para 0,54 s na 1ª visita e de 0,41 s para 0,30 s na volta; CLS de 0,028 para 0. A 1ª visita sem código ficou 0,08 s mais lenta (CSS, HTML e fonte maiores).
- **Acessibilidade:** WCAG 2.2 AA conferido pelo axe-core no CI, em claro e escuro; "Pular para o conteúdo", foco visível, teclado completo e anúncios curtos para leitores de tela (a atualização automática não repete nada).
- **Segurança:** CSP com Trusted Types, COOP, CORP e Permissions-Policy; `no-store` nas respostas da API; pedido que muda algo na API vindo de outro site é recusado (CSRF por Fetch Metadata).
- **Indexação:** descrição, canonical, Open Graph, `robots.txt`, `sitemap.xml`, manifesto e JSON-LD.
- **Testes:** `test_web.py` (cabeçalhos, cache, SRI, 404, CSRF, indexação, contraste) e, no navegador, acessibilidade, celular (320 e 390 px, também com fonte larga), teclado, sessão e antecipação das chamadas da API.
- **Atualizar o servidor:** `git pull && docker compose up -d --build && docker compose restart caddy` (docs/09).

## 08/10/2026: Render2Go no ar, segurança e experiência do cliente

### Implantação
- **Serviço em produção** em https://app.render2go.com:
  - servidor Hetzner CX23 em Nuremberg;
  - Docker Compose com API, orquestrador, Postgres e Caddy (HTTPS automático);
  - guia passo a passo no [`docs/09`](docs/09-implantacao.md).
- **1º lote real** (9 fotos de teste): pod pronto em 2 min 40 s, 9 vídeos de 3 s conferidos, pod encerrado 3,5 min depois do último vídeo, US$ 0,24 no total.
- **Correções achadas na implantação:**
  - a consulta de preço das GPUs leva a chave do RunPod (no servidor não há o proxy que a incluía);
  - a imagem não copia mais o `.env`;
  - o orquestrador funciona com código baixado sob `umask 077`;
  - o orquestrador tem um healthcheck próprio;
  - com a fila vazia, não há consulta de preço a cada 30 s.

### Nome do projeto: GPU2GO → Render2Go
- **Trocou:**
  - pacotes `render2go_worker` e `render2go_servico`;
  - comandos `render2go-*` e imagens;
  - variáveis `RENDER2GO_*`;
  - caminhos `/opt/render2go` e `~/.cache/render2go`;
  - páginas e documentação.
- **Compatibilidade:** variáveis com o prefixo antigo `GPU2GO_*` continuam valendo, sem sobrescrever as novas. O navegador migra o código de acesso e o identificador do dispositivo.
- **Mantidos de propósito:**
  - o bucket R2 `gpu2go`, porque renomear exigiria copiar o espelho de 36,6 GB;
  - o banco e o usuário Postgres `gpu2go` e os volumes da 1ª implantação, onde estão os dados;
  - o endereço do repositório no GitHub;
  - os registros históricos dos testes em GPU (`worker/evals/gpu-*`).
- Os passos para migrar o servidor estão no [`docs/09`](docs/09-implantacao.md), seção 0.

### Segurança e auditoria
- **Filtro de texto C1 v2** (`worker/filtros/c1.v2.json`): bloqueia também despir, roupa íntima e movimentos sexualizados ("tire a roupa", "sem roupa", "remove her bikini", "rebolando"…), inclusive com as letras separadas. Palavras comuns em marketing, como gostoso, sedução e decote, ficaram de fora.
- **Aviso "contém pessoas" no `/aprovar`:**
  - o detector roda no próprio servidor: YOLOX-tiny para pessoas, inclusive de costas, e YuNet para rostos;
  - a página mostra um selo por foto e um resumo do lote, em vermelho quando falta a autorização.
- **Registro de auditoria da autorização das pessoas das fotos:**
  - texto exibido e sua versão, com o SHA-256;
  - conta do cliente, IP, dispositivo, navegador, fuso e hora do clique;
  - SHA-256 de cada foto, que fica no banco mesmo depois que a foto é apagada;
  - exportação em JSON por pedido;
  - a caixa não pede nome.
- **ADR 0005 (proposto):** reserva um filtro de imagem nas fotos e nos quadros dos vídeos.
- **Fotos de entrada apagadas** do R2 assim que o pedido fecha.

### Teste gratuito e custo
- **Regras:**
  - um pedido só, com até 10 fotos, vídeos de 3 s em 480p;
  - até 10 fotos por dispositivo e 50 por rede (IP), para que várias pessoas de uma empresa consigam testar.
- **Agrupamento:** os testes esperam juntar 20 vídeos ou 10 minutos antes de subir um pod. Dez testes de 1 foto pagam uma subida em vez de dez.
- **Sinais no `/aprovar`:** tabela de dispositivos (troca de IP, vários códigos, mesmo navegador com outro identificador) e uso por IP.
- **Plano interno** (só o dono): sem limites e com aprovação automática; os filtros continuam valendo.

### Página do cliente
- **Previsão de tempo** sem expor as etapas internas ("começa em cerca de N min", "termina em cerca de N min"), com um indicador de atividade.
- **Vídeos:** o nome abre numa aba nova; "baixar" salva o arquivo.
- **Modelo de geração visível** (hoje o Wan 2.2), pronto para mais modelos; o gerador recusa modelo que não tem.
- **Estilo "Livre (descreva o que quiser)"** (`receitas.v2.json`), para pedidos que não se encaixam nos outros estilos.
- **Suporte** em https://app.render2go.com/suporte:
  - funciona com ou sem código de acesso;
  - tem limite contra spam;
  - envia por e-mail quando configurado (SMTP) e guarda a mensagem no `/aprovar`.
- **Aviso de cookies** em navegador novo: aceitar todos, só os necessários ou gerenciar. A escolha fica registrada no servidor.

### `/aprovar` (revisão)
- **Histórico** de pedidos feitos, aprovados e recusados, com cliente, IP, dispositivo, modelo, autorização e andamento.
- **Desativar o código** de um cliente.
- **Mensagens de suporte.**

## 07/10/2026: escala, subida rápida e fase 3

- **GPUs medidas:**
  - RTX PRO 6000: ~28 s por vídeo de 5 s em 480p, 1,9× a L40S pelo mesmo custo; virou a 1ª opção;
  - 720p: ~93 s;
  - 7 s de vídeo: ~160 s.
- **Pods:**
  - só nuvem secure, limite de 2 h e desligamento automático pelo menor entre tempo, orçamento e saldo;
  - teste de rede na subida, que descarta servidor lento;
  - troca automática de pod que falha ou fica lento.
- **Subida mais rápida:** download em partes (aria2) e SHA-256 em paralelo. A instalação caiu de ~5,5 min para 91 s.
- **Espelho dos modelos no R2** (36,6 GB) como reserva do Hugging Face; o Network Volume de teste foi apagado.
- **Pods automáticos pelo tamanho do lote** (`--pods auto`) e prazo de entrega (`--prazo`).
- **Fase 2 validada em pods reais:** 8 vídeos, 16,9 min, ~US$ 0,40.
- **Fase 3:**
  - API, banco e fila (pagos antes de testes, clientes intercalados);
  - conferência do 1º quadro, para que um vídeo trocado nunca chegue ao cliente;
  - worker da fila, orquestrador de pods e páginas do cliente e do aprovador;
  - ADR 0004 (filtro C1 e revisão humana), CI e guia de implantação.
- **Documentos:** [`docs/07`](docs/07-guia-tecnico-gpus-e-pods.md) (guia técnico) e [`docs/08`](docs/08-varios-clientes-e-demanda-alta.md) (vários clientes e isolamento).

## 06/10/2026: caminho A (marketing para pequenos negócios)

- **Etapa 2 concluída:**
  - medições numa RTX 4090;
  - compilador I2V v5 e T2V v3;
  - ADR 0001 aceito (T2V A14B fp8 + Lightning).
- **Etapa 3:** handler serverless, endpoints como código, bench e custos; imagem montada pelo RunPod a partir do GitHub (ADR 0002 aceito).
- **Caminho A escolhido** ([`docs/05`](docs/05-analise-de-negocio.md)):
  - presets v2, receitas e lote (pasta de fotos + um pedido);
  - preparo da foto, folha de conferência e acabamento (interpolação, altura, zoom);
  - várias GPUs por pod.
- **Rodada L40S:** ~55 s por vídeo. Análise de qualidade, velocidade e custo em [`docs/06`](docs/06-qualidade-velocidade-custo.md); ADR 0003 (arquitetura em escala).

## 05/10/2026: proposta e primeiras etapas

- **Proposta:** verificações, arquitetura, segurança e conformidade, e plano ([`docs/01`](docs/01-verificacoes.md) a [`docs/04`](docs/04-plano-de-implementacao.md)).
- **Etapa 1:** imagem Docker do worker (ComfyUI 0.38.0) e script de envio do workflow.
- **Etapa 2 (início):** presets, compilador versionado, candidatos T2V e "continuar vídeo".
