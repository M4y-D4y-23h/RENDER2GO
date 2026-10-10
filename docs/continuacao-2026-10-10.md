# Continuação: área do cliente e acessos do painel (trabalho em andamento desde 10/10/2026)

Este documento passa o trabalho para a próxima sessão. Ele junta o pedido do dono, o que já foi entregue, as decisões de projeto já tomadas e a ordem dos próximos passos. Quando tudo estiver pronto e documentado nos lugares de sempre (docs/09, docs/10, docs/13, ADRs, CHANGELOG), apague este arquivo ou deixe nele só um apontamento.

## 1. O pedido do dono (texto original)

> Seguem algumas mudanças que eu gostaria que fossem feitas na parte do usuário:
>
> * Cada conta deve ter um menu lateral que deverá conter: perfil, projetos (em breve), extratos, logs, modelos de I.A disponiveis, mix (em breve), configurações
> * Na aba de configurações da conta estará tudo que diz respeito ao acesso desde temas do site, e-mail, telefone, MFA, dispositivos conectados, desativação da conta, exclusão da conta
> * Na aba de perfil deverá estar o campo da foto de perfil da conta de e-mail do usuário, caso o e-mail dele não tenha deverá estar a inicial em maiúscula do nome do e-mail, deverá estar para ser adicionado em breve o campo nome de usuário, campo primeiro e último nome, botão de ganhos por indicação (em breve).
> * na aba de extratos os gastos dele, com qual tipo de modelo, com qual modelo de I.A e gastos totais por rodada
> * nos logs da conta as gerações que a conta fez em 24h e que não foram excluídas, deve conter: o nome do modelo que gerou, o horário que foi gerado, e o acesso para o arquivo que foi gerado.
> * na aba dos modelos de I.A deve conter a listagem de todos os modelos que estão ativos e que estão em fila como em breve), por lá o usuário vai ter a possibilidade de acessar toda essa listagem dos modelos e caso se interesse por algum que achar lá, com um clique já ser levado para aba dos pedidos daquele modelo.
> * Na aba mix o usuário vai ter a possibilidade de usar vários modelos ao mesmo tempo, ex: "gerou uma imagem:": quer criar um modelo ou quer adicionar uma música a essa imagem estática, ou quer que o rosto do individuo nessa imagem seja alterado... Essa aba vai ser voltada pra ter um pedido inicial que poderá seguir uma sequência de gerações que partiram ou não daquele pedido inicial, então uma empresa nessa aba também pode anexar vários tipos de arquivos, gerar fotos desses arquivos e após a geração de fotos querer que cada foto também se torne um vídeo de x segundos...
> * na aba de pedidos, o usuário deve ter uma segunda aba onde ele poderá colocar uma sequência de pedidos, igualmente como vem sendo, mas também vai poder criar especificações únicas a determinados pedidos, ex: pra uma sequência de 100 vídeos ele define o prompt: "crie uma apresentação de todas essas fotos" esse prompt fica como padrão, pra todas as imagens escolhidas terá uma caixa de texto para prompts secundários aonde pra 5 fotos escolhidas entre essas 100 ele poderá colocar: "quero que a pessoa nessa foto apenas sorria", "quero que essa moto esteja na estrada", "esse prato será para um comercial de comidas quentes", "o café nessa foto deve virar um café com leite", "quero que encha de pessoas esse restaurante vázio"... e assim as outros 95 fotos seguiram o prompt padrão, pois o campo de texto para prompts secundários estará vázio para as outras.
>
> Agora na parte de admin, eu gostaria que tais modificações fossem aplicadas:
>
> * Crie seções e subseções, atualmente todas as seções estavam juntas em um scroll somente, quero que cada seção tenha o seu scroll, ou seja, se eu clicar pra ir na seção de "Pods" não deve mostrar as informações da seção de "Histórico" como vinha sendo a navegação até o momento.
> * Quero que todo o visual do painel admin, os textos, os botões, as cores, as caixas de textos, os titulos... tudo deve ser aperfeiçoado.
> * Quero que seja possivel dentro do painel admin adicionar novos acessos com: usuário, senha (após válidados código de autenticação do aplicativo por padrão)
> * Quero que cada código de autênticação que será disponibilizado aos admins para que entrem no painel que seja de uso único e instransferivel
> * Quero que tenha uma seção de auditoria no painel registrando as alterações que foram feitas, quando, ip e por quem.
>
> Preciso que as seguintes mudanças sejam aplicadas, muitos pedidos com foco no front-end, mas alguns pedidos também incluindo mudanças no back-end, na arquiteturas e em outros sistemas e funcionalidades do projeto, preciso que você seja cauteloso, meticuloso, preciso, pondere minhas idéias e minhas sugestões a serem adicionadas no site, aperfeiçoe as minhas idéias que são aplicaveis e otimize os processos de cada uma delas, para que o site não seja lento e venha operar sempre pronto para altissima escala.
> Após tudo isso, documente tudo, atualize as documentações, me explique as mudanças e pode subir na main.

## 2. O que já está pronto (na main, CHANGELOG "10/10/2026 (3)")

Só o servidor, compatível com as páginas atuais. Testes: `servico/tests/test_sequencia.py` e `servico/tests/test_extrato.py`, nos dois bancos.

1. **Pedido em sequência (textos por foto).** `POST /api/lotes` aceita `fotos[].prompt` (opcional, até 600 caracteres; `api.py`: `FotoIn`, `_textos_por_foto`, `MAX_TEXTOS_FOTO = 100`). Banco: `fotos.prompt` e `fotos.prompt_en`. Na aprovação (`db.aprovar`), um job `compilar` a mais por texto distinto, ligado à primeira foto com aquele texto (`foto_id`, `arquivo "<foto>-v0"`). Na fila (`db.reivindicar`), o render de uma foto com texto espera o `prompt_en` dela; a compilação do texto padrão continua passando na frente (`ORDER BY CASE WHEN j.tipo = 'compilar' AND j.foto_id IS NULL ...`) e a do texto próprio entra na vez do cliente. O worker do pod não muda: `api.reivindicar` troca `pedido["prompt"]` pelo texto da foto no job de compilação e manda o `prompt_en` da foto no render. `api.prompt` grava pelo `db.definir_prompt_foto`; texto compilado que cai no filtro cancela e devolve só as fotos com esse texto (`db._fechar_lote`). Painel: cada foto do pendente traz `texto` e `filtro_c1`. O pedido guarda `pedido["textos_por_foto"]` (quantos textos distintos).
2. **Extrato por rodada.** `GET /api/extrato?dias=7|30|90|365|0&antes=<quando>` e `GET /api/extrato.csv?dias=&fuso_min=` (fuso em minutos a leste de UTC: no navegador, `-new Date().getTimezoneOffset()`). Cada pedido é uma rodada: `tipo: "rodada"`, `lote`, `quando`, `modelo`, `modelo_id`, `finalidade`, `tipo_modelo`, `videos`, `duracao`, `resolucao`, `cobrado`, `devolvido`, `liquido`, `status` (`null` depois da limpeza do histórico); recargas e ajustes vêm como `tipo: "recarga" | "ajuste"` com `valor` e `descricao`. Resumo: `gasto`, `devolvido`, `liquido`, `rodadas`, `recargas`, `ajustes`, `saldo`; `por_modelo`; `proximo` (o `antes` da página seguinte, ou `null`). Valores em centavos. O modelo fica em `movimentos.modelo` (`db._movimento`, `_cobrar` e os dois estornos).
3. **Logs de geração.** `GET /api/geracoes?horas=24` (1 a 168): `id`, `arquivo`, `lote`, `gerado_em`, `modelo`, `finalidade`, `tipo_modelo`, `duracao`, `resolucao`, `texto`, `texto_proprio`, `url` (assistir) e `baixar` (com `Content-Disposition: attachment`), assinadas por 24 h. `POST /api/geracoes/{id}/excluir`: apaga o vídeo e `conferencia/<nome>.jpg` do armazenamento, marca `jobs.excluido_em` e grava `video_excluido` em `acessos`. Em `GET /api/lotes/{id}`, cada vídeo traz `id`, `texto` e `excluido` (sem endereços quando excluído).
4. `modelos.info(id)` devolve `id`, `nome`, `finalidade`, `titulo` e `tipo` (usado no extrato e nos logs).

## 3. O que falta, na ordem

Cada etapa termina com testes passando nos dois bancos, lint e formatação. As etapas 3 e 4 mudam a entrada (passo do autenticador) e a exclusão (confirmação recente), que as páginas de hoje não conhecem: só vão para a main junto com as páginas das etapas 5 e 6, depois da validação (etapa 7).

### Etapa 3: configurações da conta (servidor)

Decisões já tomadas (ponderadas com o pedido do dono):

- **Confirmação recente ("modo confirmado")** antes de ações sensíveis: trocar e-mail, ligar ou desligar o MFA, gerar códigos de recuperação novos e excluir a conta. Coluna `sessoes.confirmada_ate`; uma sessão aberta há menos de 10 min já conta como confirmada (a pessoa acabou de provar o e-mail e o MFA). `POST /api/conta/confirmar/codigo` manda um código ao e-mail atual, com chave própria em `codigos` (ex.: `confirmar:<email_norm>`, para não invalidar um código de entrada); `POST /api/conta/confirmar` aceita esse código, o do autenticador ou um de recuperação e vale 10 min. Essas rotas só aceitam a sessão do cookie: o código de acesso antigo (cabeçalho `Authorization`) recebe 403 ("use a página").
- **MFA do cliente (aplicativo autenticador, TOTP):** colunas `clientes.mfa_segredo` (cifrado no banco; não há `cryptography` no serviço hoje: acrescentar com versão fixada e usar AES-GCM com chave derivada de `RENDER2GO_SEGREDO_CONTAS`, como `contas.chave_contas`), `mfa_ativo_em` e `mfa_ultimo` (reuso do código barrado de forma atômica: `UPDATE clientes SET mfa_ultimo = ? WHERE id = ? AND (mfa_ultimo IS NULL OR mfa_ultimo < ?)`). Ativação em dois passos (`/api/conta/mfa/iniciar` devolve o segredo, a URI `otpauth://` e o QR em SVG; `/api/conta/mfa/ativar` confere um código e devolve 10 códigos de recuperação, mostrados uma vez, guardados só como HMAC numa tabela `codigos_recuperacao`). QR: `segno` com versão fixada (gera SVG no servidor; a página mostra por `blob:` URL, sem `innerHTML`). Dependência nova = reconstruir a imagem da API (docs/09).
- **Entrada com MFA:** `entrar` e `entrar-codigo` não abrem a sessão quando a conta tem MFA; devolvem `{"mfa": true, "desafio": <token>}`, uma linha em `sessoes` com `tipo = "mfa"` (5 min; acrescentar "mfa" em `SESSAO_TIPOS`). `POST /api/conta/entrar-mfa {desafio, codigo}` aceita o código do autenticador ou um de recuperação e abre a sessão. Limite por conta: 5 erros em 15 min e 20 por dia.
- **Troca de e-mail:** com confirmação recente; `POST /api/conta/email/codigo {email}` confere formato, provedor aceito (`_dominio_aceito`) e se já é de outra conta (pode responder que está em uso: a pessoa está conectada e há limite de 5 por hora e 10 por dia por conta) e manda o código ao endereço novo; `POST /api/conta/email {email, codigo}` troca `email`/`email_norm` (único), grava em `acessos`, avisa o endereço antigo e acerta `clientes.nome` quando ele era o e-mail antigo.
- **Aparelhos conectados:** `GET /api/conta/sessoes` (até 50 sessões ativas do tipo cliente) com `ref` (derivada: `sha256("ref:" + id)[:20]`; nunca o `id`), aparelho legível tirado do User-Agent ("Chrome no Android"), IP, criada, último uso e `atual`; `POST /api/conta/sessoes/{ref}/encerrar` e `POST /api/conta/sessoes/encerrar-outras`.
- **Desativação pelo próprio cliente ("pausar a conta"):** `POST /api/conta/desativar {confirmacao: "DESATIVAR"}`; recusada com pedido em andamento; grava `clientes.pausada_em` e encerra todas as sessões; o saldo fica. Entrar de novo pelo código do e-mail reativa (limpa `pausada_em`, evento `reativada`, aviso na página). A desativação feita pelo painel (`ativo = 0`) continua separada e só o suporte desfaz; o painel mostra "pausada pelo cliente".
- **Exclusão:** a rota atual, agora com confirmação recente.
- **Perfil:** `clientes.primeiro_nome` e `clientes.ultimo_nome` (1 a 60 caracteres, letras, espaço, apóstrofo, hífen e ponto) em `PUT /api/conta/perfil`. Foto: não há como pegar a foto do Gmail ou do Outlook sem "Entrar com Google/Microsoft" (OAuth); o caminho viável é o **Gravatar, por escolha da pessoa** (`clientes.gravatar`), servido pelo próprio servidor em `GET /api/conta/foto` (host fixo `gravatar.com`, SHA-256 do e-mail em minúsculas, `d=404&s=160`, 5 s, até 200 KB, só PNG/JPEG, `Cache-Control: private, max-age=86400`, cache em memória): o navegador nunca fala com terceiros e a CSP não muda. Sem foto, a inicial maiúscula do e-mail. "Nome de usuário" e "Ganhos por indicação" aparecem como "Em breve", com "Tenho interesse".
- **Tema:** claro, escuro ou automático; `clientes.tema` em `PUT /api/conta/preferencias`; a página aplica antes de desenhar pelo `localStorage` (script do `<head>`) e acerta pelo `/api/conta`.
- **Telefone:** "Em breve" (SMS depende de escolher e pagar um provedor: decisão do dono), com "Tenho interesse".
- **Interesse em recursos em breve:** ampliar `POST /api/interesses` para ids de recurso (`recurso:indicacao`, `recurso:mix`, `recurso:projetos`, `recurso:telefone`, `recurso:nome-usuario`), com título no `GET {painel}/api/interesses`.
- **Painel:** `POST {painel}/api/clientes/{id}/mfa/desligar` (com auditoria) para quem perdeu o celular e os códigos.
- `GET /api/conta` passa a trazer nome, sobrenome, tema, gravatar, `mfa` e o que a página de configurações precisa; `dados_da_conta` traz os campos novos (nunca o segredo do MFA).
- **Documentos:** privacidade v2 (Gravatar por escolha, MFA, troca de e-mail, aparelhos) e termos v2 se preciso; subir as versões em `contas.DOCUMENTOS` e conferir como uma sessão aberta antes da versão nova aceita a versão nova.
- Testes novos: `tests/test_conta_config.py`.

### Etapa 4: acessos do painel (servidor), ADR 0009 (aceito, pedido do dono)

- Tabelas `admins` (id, `usuario` único, `senha_hash` com `hashlib.scrypt` (n = 2^15, r = 8, p = 1, sal de 16 bytes), `totp_segredo` cifrado como o do cliente, `totp_ultimo`, `papel` dono ou operador, `ativo`, criado, criado_por, desativado_em, ultimo_acesso) e `admin_convites` (id = SHA-256 do token, `usuario`, `papel`, criado, criado_por, `expira` em 24 h, `usado_em`).
- Entrada: usuário + senha + código do autenticador da própria pessoa; reuso do código barrado de forma atômica por pessoa; a mesma mensagem para qualquer erro; os limites por rede de hoje e mais 5 erros em 15 min por usuário.
- "Senha depois do código do aplicativo validado" e "código de uso único e intransferível": o dono cria um **convite** (usuário + papel), de uso único, que vale 24 h e só cria aquele usuário. No primeiro acesso, a pessoa lê o QR (segredo novo, só dela), confirma um código do aplicativo e **só então** cria a senha (12+ caracteres, sem o nome de usuário, fora de uma lista curta de senhas comuns). Cada código do aplicativo vale uma vez; o convite é consumido de forma atômica.
- A chave do servidor de hoje (`RENDER2GO_ADMIN_TOKENS` + `RENDER2GO_ADMIN_TOTP`) fica como acesso de emergência, auditado como tal.
- Papéis: dono (tudo, inclusive acessos, saldo e parada de emergência) e operador (revisão de pedidos e consultas); mapa de permissões por rota.
- Gestão: listar, desativar (encerra as sessões da pessoa) e emitir convite novo (troca senha e autenticador). CLI `render2go-servico admin-convite --usuario X --papel dono` para o primeiro acesso, pelo terminal do servidor.
- Auditoria: coluna `agente` (User-Agent), índice `(admin, quando)`, filtros (pessoa, ação, alvo, período), página seguinte por `antes`, CSV, e "antes → depois" nas mudanças (plano, saldo, e-mail, desativar/reativar, acessos). `GET {painel}/api/auditoria`.
- `GET {painel}/api/resumo` para a seção "Visão geral" (revisões pendentes, pedidos em andamento, pods, fila, cobrado e devolvido hoje, contas novas, interesses).
- Testes novos (`tests/test_painel_acessos.py`) e casos novos no `tests/teste_invasao.py` (força bruta na entrada, convite reaproveitado, código repetido).

### Etapa 5: páginas do cliente

- Moldura com menu lateral: Pedidos, Modelos de IA, Extratos, Logs, Perfil, Configurações, e Projetos e Mix "Em breve". Endereços próprios (`/modelos`, `/extratos`, `/logs`, `/perfil`, `/configuracoes`, `/projetos`, `/mix`) servidos pela API com a mesma página, `noindex` e fora do `sitemap.xml`; o script do `<head>` (`ts/inicio.ts`) marca `data-vista` pelo endereço e pede de antemão só o que a tela usa. Computador: menu fixo com rolagem própria; celular: menu numa janela de baixo, com o foco preso nela. CLS zero em cada tela (lugar reservado, esqueletos), como manda o CLAUDE.md.
- Modelos de IA: o catálogo de `/api/receitas` (`finalidades`); o disponível leva a Pedidos com aquela finalidade escolhida; os em breve com "Tenho interesse".
- Extratos: período, resumo, total por modelo, lista de rodadas e o botão da planilha. Logs: assistir, baixar e excluir (com confirmação). Perfil e Configurações: como na etapa 3. Mix e Projetos: páginas "Em breve" com a explicação e "Tenho interesse" (o Mix vira o ADR 0010, proposto: encadear modelos a partir de um pedido inicial, como no exemplo do dono).
- Pedidos: duas abas, "Mesmo texto para todas" e "Sequência (texto por foto)": texto padrão e a grade de fotos, com seleção de várias fotos para dar a elas um texto próprio; contador ("5 fotos com texto próprio; as outras 95 seguem o texto padrão"); limites (600 caracteres, 100 textos distintos); os textos aparecem na revisão antes de enviar.
- Entrada com o passo do autenticador; aviso de conta reativada.
- Tema manual: `data-tema` no `<html>`; `tokens.css` com o tema claro em `:root`, o escuro em `@media (prefers-color-scheme: dark) { :root:not([data-tema="claro"]) }` e em `:root[data-tema="escuro"]`; ajustar o leitor de pares de contraste do `tests/test_web.py`.
- Regras de sempre: editar só `servico/web/src/`, `npm run build`, sem `innerHTML`/`style=`/script embutido, cor só por token nos dois temas, partes `<!--@sessao-->`, `<!--@topo-->`, `<!--@rodape-->` e `iniciarTopo()` em página nova.

### Etapa 6: páginas do painel

- Seções separadas, cada uma com a própria rolagem e endereço por `#` (visão geral, revisão, clientes, histórico, pods, GPUs, interesses, suporte, pagamentos, acessos, auditoria, segurança), com subseções; dados carregados só quando a seção abre. Visual novo seguindo o docs/10 (textos, botões, cores, campos, títulos).
- Entrada em três modos: usuário + senha + código; chave do servidor (emergência); primeiro acesso por convite, com o QR. Telas de acessos e de auditoria (filtros, página seguinte, CSV) e a visão geral.
- Arquivos: `servico/web/src/paginas/painel.html` e `servico/web/src/ts/painel.ts` (sai em `publico/privado/painel.js`; nenhuma página pública cita o endereço nem o arquivo).

### Etapa 7: validação

- Suíte inteira do serviço nos dois bancos; `tests/test_paginas.py` com telas novas (axe-core nos dois temas, 320 px, teclado, CLS com a API lenta); teste de invasão com os casos novos (Caddy 2.10); medição de carga se a fila ou as rotas quentes mudarem.
- Medição de CLS e LCP: a ferramenta de 10/10 ficou fora do repositório. Para refazer: Playwright com o Chromium do ambiente, latência pelo CDP (`Network.emulateNetworkConditions`; atraso com `time.sleep` dentro de uma rota do Python enfileira os pedidos), hosts bloqueados por `--host-resolver-rules` (uma rota do Playwright desliga o cache HTTP) e `limite_por_minuto` alto na configuração de teste (o limite de 120 por minuto por conta dá 429).

### Etapa 8: documentação e publicação

- ADR 0009 (acessos do painel, aceito) e ADR 0010 (Mix e Projetos, proposto); docs/10 (área do cliente, menu, endereços, temas), docs/09 (passos de publicação: dependências novas exigem reconstruir a imagem da API; primeiro convite pelo terminal), docs/13 (MFA, confirmação recente, acessos do painel, resultado do teste de invasão), docs/11 se o extrato mudar algo de preços; CHANGELOG; CLAUDE.md (status e regras novas); apagar este arquivo.
- Commit, push no ramo de trabalho e na main, conferir o CI (`servico.yml` roda a suíte, o build conferido e o `test_paginas.py`; `worker.yml`) e explicar as mudanças ao dono em pt-BR.

## 4. Ambiente de testes num contêiner novo

- Serviço: `cd servico && uv sync --python 3.12 && uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests`.
- Postgres de teste (o `conftest.py` usa `postgresql://postgres@/render2go_teste?host=/var/tmp&port=55432` se `RENDER2GO_TESTE_PG` não estiver definida; sem o banco, os testes de Postgres são pulados). Com o Postgres 16 do Ubuntu:
  ```bash
  mkdir -p /var/tmp/pgteste && chown postgres:postgres /var/tmp/pgteste
  su postgres -c "/usr/lib/postgresql/16/bin/initdb -D /var/tmp/pgteste/dados -U postgres --auth=trust"
  su postgres -c "/usr/lib/postgresql/16/bin/pg_ctl -D /var/tmp/pgteste/dados -o '-p 55432 -k /var/tmp -c listen_addresses=' -l /var/tmp/pgteste/pg.log start"
  su postgres -c "/usr/lib/postgresql/16/bin/createdb -h /var/tmp -p 55432 render2go_teste"
  su postgres -c "/usr/lib/postgresql/16/bin/createdb -h /var/tmp -p 55432 render2go_invasao"  # teste de invasão
  ```
- Páginas: `cd servico/web && npm ci && npm run build`; navegador: `cd servico && uv run --with playwright==1.56.0 pytest -q tests/test_paginas.py` (o Chromium já vem instalado no ambiente; não rodar `playwright install`).
