# Continuação: área do cliente e acessos do painel (atualizado em 11/10/2026)

Este documento passa o trabalho para a próxima sessão. **Código, páginas e validação estão prontos e na main desde 11/10/2026; falta só terminar a documentação (seção 3).** Quando a seção 3 estiver feita, apague este arquivo e tire a menção a ele do `CLAUDE.md`.

## 1. O pedido do dono (texto original, 10/10/2026)

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

## 2. O que está pronto (na main)

Commits: `b4f9c7b` (parte 1, servidor: textos por foto, extrato por rodada, logs), `07e317c` (parte 2, servidor: configurações da conta), `72decf0` (parte 3, servidor: acessos do painel por pessoa, ADR 0009), `0278d7c` (parte 4, páginas do cliente), `d221cda` (parte 5, páginas do painel), `421c8f2` (lista de pedidos: texto de cada foto só nos pedidos em sequência, para não pesar na rota quente) e o commit desta atualização (documentação).

- **Servidor:** confirmação recente antes de ações sensíveis; verificação em duas etapas do cliente (TOTP cifrado com AES-GCM, `cryptography==50.0.2`; QR em SVG pelo `segno==1.6.6`; 10 códigos de recuperação); entrada com o passo do autenticador; troca de e-mail; aparelhos conectados; pausar a conta; perfil (nome, sobrenome, Gravatar por escolha, servido pelo próprio servidor); tema; interesses por recurso (`recurso:mix`, `recurso:projetos`…); acessos do painel por pessoa (tabelas `admins` e `admin_convites`, convite em fragmento → QR → código → senha, papéis dono e operador com `PERMISSOES_PAINEL` fail-closed, chave do servidor como emergência), auditoria com antes/depois, agente, filtros, página seguinte e CSV, e `GET <painel>/api/resumo`. CLI `admin-convite`.
- **Páginas do cliente:** moldura com menu lateral (Pedidos, Modelos de IA, Extratos, Logs, Perfil, Configurações; Mix e Projetos "Em breve"), endereços próprios, pedido em sequência (texto por foto), tema manual (`data-tema`), CLS zero em cada tela.
- **Páginas do painel:** 12 seções em 5 grupos, cada uma com endereço `#`, rolagem e dados próprios (carregados só quando a seção abre; a aberta se atualiza a cada 30 s); entrada em três modos (pessoa, emergência, convite com QR); telas de acessos e de auditoria; visão geral; operador sem o que o papel não abre.
- **Validação (etapa 7), feita:** suíte do serviço nos dois bancos (268 testes), navegador (`test_paginas.py`, `test_paginas_conta.py`, `test_paginas_painel.py`: 45 testes, com axe-core nos dois temas, 320 px, teclado e CLS com a API lenta), ruff, build reproduzível; teste de invasão com o Caddy 2.10: **88/88** (eram 73; casos novos de força bruta por pessoa e por nome, convite reaproveitado, código repetido, operador barrado). Carga (nesta máquina, 2 processos, 3 conexões, 30 s), versão de antes (`87b12c6`) × nova: lotes ~380–389 × ~374–379 pedidos/s, misto ~400–422 × ~400–411, página ~1830–1958 × ~1900–1935: equivalentes (os números de 09/10 do `docs/13`, 475/520/1470, são de outra máquina). Em sequência, 1 processo: `/api/lotes` 5,26 × 5,33 ms, `/api/conta` 2,53 × 3,12 ms, `/` 1,07 × 0,98 ms. O `421c8f2` tirou um JOIN de `fotos` da lista de pedidos (só os pedidos em sequência buscam os textos).
- **Documentação já feita:** ADR 0009 (aceito) e ADR 0010 (Mix e Projetos, proposto); `docs/09` (seção 0, "11/10/2026": backup, `--build api`, o primeiro convite pelo terminal, chave de emergência, `RENDER2GO_SEGREDO_CONTAS` que não pode mudar, o que muda para clientes e painel, como voltar; e as linhas de operação, variáveis, segurança e "o que ainda não existe"); termos e privacidade v2 (as opções "Em breve" e a exclusão de vídeo pela tela Logs, também no resumo da janela de novo aceite); `docs/10`: introdução, testes, revisões, tema escolhido (1.3), 33 pares de contraste, 44 ícones, regras de layout 1, 3, 7 e 15 (nova: "uma tela por vez"), tabela de componentes e células da tabela de atributos (scripts, URLs, navegação, teste de invasão).

## 3. O que falta (documentação), na ordem

1. **`docs/10`:**
   - seção **4.2 "Área do cliente e painel em seções (11/10/2026)"**: o que entrou (os itens da seção 2 acima, vistos pelo front-end), como cada atributo foi mantido (CLS zero com lugar reservado e esqueletos, `data-antecipar` só com os dados da tela, `aria-current`, foco no `h1` ao trocar de tela, título da aba, Voltar do navegador, janela de baixo com foco preso no celular, operador com `.so-dono` escondido e o servidor conferindo de novo) e as ideias do dono ponderadas (foto do perfil pelo Gravatar por escolha, porque o Gmail e o Outlook não dão a foto sem "Entrar com Google/Microsoft"; telefone, nome de usuário e indicação "Em breve"; troca de rosto fora, ADR 0010);
   - seção **5.2 "Área do cliente e painel (11/10/2026)"** com as medições da seção 5 abaixo (a versão de antes é a `87b12c6`); se quiser completar, medir de novo as telas `painel`, `painel-revisao` e `painel-clientes` da versão nova (a rodada foi interrompida antes delas);
   - seção 6 **"Como mexer nas páginas"**: tela nova na área do cliente (`<section class="vista" id="vista-<nome>">` no `index.html`, link no menu com `data-vista`, `TELAS_CONTA` em `api.py`, chave em `data-antecipar` do `inicio.js`, script da tela em `src/ts/<tela>.ts` chamado pela `moldura.ts`); seção nova no painel (`<section class="secao-painel" id="sec-<nome>">` com `h1#t-<nome>`, item no menu com `data-secao` e `data-rota`, `SECOES` em `painel.ts`, controle no `painel-*.ts` do grupo, rota em `PERMISSOES_PAINEL`, chave em `data-antecipar` e, se for só do dono, em `data-so-dono`); os arquivos `painel-base.ts`, `painel-entrada.ts`, `painel-pedidos.ts`, `painel-clientes.ts`, `painel-operacao.ts` e `painel-seguranca.ts`; e o comando dos testes com os três arquivos do navegador.
2. **`docs/13`:** verificação em duas etapas do cliente e confirmação recente; acessos do painel por pessoa (ADR 0009: scrypt, segredo cifrado, convite em fragmento, limites por rede e por nome, papéis fail-closed, emergência, auditoria); teste de invasão 88/88 com os casos novos; carga de 11/10 (seção 2 acima), deixando claro que é outra máquina que a de 09/10.
3. **`CHANGELOG.md`:** a entrada "11/10/2026" já tem um resumo; completar com os detalhes (rotas novas, tabelas e colunas, medições) e tirar a frase "a documentação detalhada ainda está sendo terminada".
4. **`CLAUDE.md`:** o status, a lista de ADRs e o comando do navegador já foram acertados; falta: regras novas (nunca trocar o `RENDER2GO_SEGREDO_CONTAS`; rota nova do painel só com entrada em `PERMISSOES_PAINEL`; tela nova da área do cliente e seção nova do painel como no item 1) e tirar a menção a este arquivo.
5. **Investigar (pode virar item do `docs/10`, seção 7):** na visita de volta, a medição transfere 13–16 KB nas telas com sessão e 13 KB na entrada, nas duas versões, o que parece o HTML inteiro (`index.html` tem 11,5 KB comprimido; antes, 6,5 KB). Conferir no navegador, pelo Caddy, se a revalidação do HTML volta 304 (a API compara o `If-None-Match` com a ETag; o `encode` do Caddy pode mudar a ETag da resposta comprimida). Se não voltar, acertar a comparação (aceitar a ETag com o sufixo do Caddy) e medir de novo.
6. Apagar este arquivo, commit, push na main, conferir o CI (`servico.yml` e `worker.yml`) e explicar as mudanças ao dono em pt-BR.

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
- Páginas: `cd servico/web && npm ci && npm run build`; navegador: `cd servico && uv run --with playwright==1.56.0 pytest -q tests/test_paginas.py tests/test_paginas_conta.py tests/test_paginas_painel.py` (o Chromium já vem instalado no ambiente; não rodar `playwright install`).
- **Medição de FCP, LCP e CLS** (a ferramenta ficou fora do repositório): subir a API (`criar_app` com `limite_por_minuto` alto, `turnstile_desligado`, Postgres próprio) atrás do Caddy 2.10 com o `Caddyfile` de produção (trocando o domínio por `localhost:<porta>` com `tls internal`, `api:8000` pelo endereço local e o log por um arquivo); semear uma conta do pré-pago com 3 pedidos (pronto, na fila e em revisão) e as sessões do cliente e do painel; Chromium 141 pelo Playwright, com a sessão em cookie e as marcas no `localStorage` por `add_init_script`, `PerformanceObserver` de LCP, CLS e FCP, e rede e CPU pelo CDP (`Network.emulateNetworkConditions`, `Emulation.setCPUThrottlingRate` 4×); mediana de 5. Dois cenários: rede lenta para tudo (1,6 Mbps, 150 ms, 390 × 844 px; 1ª visita e de volta) e visita de volta com o cache cheio (ida e volta de 0, 120 e 250 ms; 390 e 1280 px).

## 5. Medições de 11/10/2026 (versão `87b12c6` → nova, mediana de 5)

Rede lenta para tudo (celular, 4G lenta, CPU 4×):

| Tela | Visita | FCP | LCP | CLS | Transferido |
|---|---|---|---|---|---|
| Entrada (sem sessão) | 1ª | 0,57 → 0,59 s | 0,57 → 0,59 s | 0 → 0,006 | 55 → 82 KB |
| Entrada (sem sessão) | de volta | 0,28 → 0,28 s | 0,28 → 0,30 s | 0,006 → 0,006 | 8 → 13 KB |
| Pedidos (com sessão) | 1ª | 0,52 → 0,59 s | 0,84 → 1,04 s | 0 → 0 | 58 → 85 KB |
| Pedidos (com sessão) | de volta | 0,28 → 0,30 s | 0,46 → 0,48 s | 0 → 0 | 11 → 16 KB |
| Extratos (nova) | 1ª / de volta | 0,56 / 0,29 s | 0,56 / 0,32 s | 0 / 0 | 82 / 14 KB |
| Configurações (nova) | 1ª / de volta | 0,54 / 0,28 s | 0,59 / 0,34 s | 0 / 0 | 82 / 13 KB |
| Painel do dono (antes: tela única) | 1ª / de volta | 0,59 / 0,29 s (antes) | 0,59 / 0,29 s (antes) | 0 / **0,314** (antes) | 49 / 8 KB (antes) |

Visita de volta com o cache cheio (CPU 4×), LCP e CLS:

| Tela | Ida e volta | 390 px | 1280 px |
|---|---|---|---|
| Pedidos | 0 ms | 0,22 → 0,27 s, CLS 0 → 0 | 0,15 → 0,21 s, CLS 0 → 0 |
| Pedidos | 120 ms | 0,38 → 0,38 s, CLS 0 → 0 | 0,38 → 0,38 s, CLS 0 → 0 |
| Pedidos | 250 ms | 0,65 → 0,65 s, CLS 0 → 0 | 0,62 → 0,60 s, CLS 0 → 0 |
| Extratos (nova) | 0 / 120 / 250 ms | 0,14 / 0,26 / 0,38 s, CLS 0 | 0,13 / 0,26 / 0,36 s, CLS 0 |
| Configurações (nova) | 0 / 120 ms | 0,17 / 0,28 s, CLS 0 | 0,13 / 0,24 s, CLS 0 |
| Painel do dono, versão de antes | 0 / 120 / 250 ms | 0,08 / 0,25 / 0,35 s, **CLS 0,347 / 0,314 / 0,346** | 0,09 / 0,27 / 0,41 s, **CLS 0,096 / 0,075 / 0,075** |

Leitura: o painel de antes deslocava o layout na visita de volta (CLS "ruim" no celular); o novo foi medido sem deslocamento nos testes de navegador (`test_paginas_painel.py`, CLS com a API lenta, 6 seções em 390 e 1280 px, e o convite), e falta só a rodada desta ferramenta para ele. As telas do cliente seguem com CLS zero e LCP bem dentro da faixa "bom" (até 2,5 s); a 1ª visita ficou ~27 KB maior (o script da área do cliente: 18,6 → 33,1 KB comprimido; o HTML: 6,5 → 11,5 KB), tudo em cache por versão, e o painel tem o próprio script (13,7 → 26,9 KB comprimido), que só o dono baixa.
