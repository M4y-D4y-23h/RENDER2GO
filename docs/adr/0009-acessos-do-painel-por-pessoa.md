# ADR 0009: acessos do painel por pessoa (convite, senha e autenticador próprios), papéis e auditoria

- **Status:** aceito (10/10/2026), a pedido do dono do projeto: "adicionar novos acessos com usuário e senha (depois de validado o código do aplicativo autenticador)", "cada código de autenticação de uso único e intransferível" e "uma seção de auditoria registrando as alterações, quando, IP e por quem". Revê o item 5 do [ADR 0007](0007-contas-sessoes-e-painel.md): a chave do servidor continua, como acesso de emergência.
- **Contexto:** desde 09/10/2026 o painel entra com uma chave fixa do `.env` (`RENDER2GO_ADMIN_TOKENS`) e o código de um único aplicativo autenticador (`RENDER2GO_ADMIN_TOTP`). Para mais de uma pessoa (um revisor, um sócio), isso obriga a dividir a mesma chave e o mesmo QR Code: não dá para saber quem fez o quê, tirar o acesso de uma pessoa sem trocar o de todas, nem dar a quem só revisa um acesso que não mexe em dinheiro e GPUs. Mandar uma senha pronta por mensagem também a deixaria guardada no histórico de alguém.

## Decisão

1. **Uma pessoa, um acesso** (tabela `admins`): nome de acesso único (3 a 32 letras minúsculas, números, ponto, hífen ou sublinhado, começando por letra; `admin-…` fica reservado à chave do servidor), senha guardada só como resumo **scrypt** (n = 2^15, r = 8, p = 1, sal de 16 bytes, no máximo 4 conferências ao mesmo tempo por processo), segredo do aplicativo autenticador **cifrado** (AES-GCM, chave derivada de `RENDER2GO_SEGREDO_CONTAS`, com o id da pessoa como dado associado: copiado para outra linha, não abre), papel, situação, quem criou e o último acesso.
2. **Entrada:** nome de acesso + senha + código do aplicativo **da própria pessoa**:
   - cada código vale uma vez (o passo usado fica gravado, de forma atômica: o mesmo código não entra duas vezes, nem em duas abas ao mesmo tempo);
   - a mesma mensagem para qualquer erro, e a senha é conferida sempre, mesmo quando o nome não existe (contra um resumo de mentira): nem o texto nem o tempo dizem quem existe;
   - limites: os de rede de antes (5 erros bloqueiam a rede por 15 min, mesmo com tudo certo depois) e mais 5 erros em 15 min por nome de acesso, também vindos de outras redes;
   - sessão como antes: cookie `__Secure-render2go-painel`, só no caminho do painel, SameSite=Strict, 12 horas.
3. **Primeiro acesso por convite** (tabela `admin_convites`), o "código de uso único e intransferível" do pedido:
   - o dono cria o convite (nome de acesso + papel); ele vale **24 horas, uma vez só**, e só cria aquele nome. O banco guarda só o SHA-256 do token;
   - o endereço é `<painel>/#convite=<token>`: o token vai no fragmento (depois do `#`), que o navegador nunca manda ao servidor, ao registro do Caddy nem a outros sites (`Referer`). A página lê o fragmento e o manda no corpo de um POST;
   - passos, nesta ordem: (a) o QR Code de um segredo **novo, só desta pessoa** (SVG feito no servidor, mostrado como imagem `blob:`); (b) um código do aplicativo, conferido com esse segredo; (c) **só então** a senha, em até 15 minutos: 12 a 128 caracteres, sem o nome de acesso, com pelo menos 5 caracteres diferentes e fora de uma lista curta de pedaços comuns em vazamentos (NIST SP 800-63B: tamanho acima de regras de composição). O convite é gasto de forma atômica, junto com a criação do acesso;
   - abrir o convite de novo gera outro QR Code (o anterior deixa de valer); um convite novo para o mesmo nome cancela o anterior; um nome que já existe ganha senha e autenticador novos quando a pessoa usar o convite (perdeu o celular, esqueceu a senha), e as sessões antigas dela caem;
   - o **primeiro dono** sai pelo terminal do servidor: `docker compose exec api render2go-servico admin-convite --usuario <nome> --papel dono` imprime o endereço (auditado como `terminal`).
4. **Papéis**, com um mapa de permissões por rota (`PERMISSOES_PAINEL`, em `api.py`):
   - **dono:** tudo (revisão, contas e saldo, pods e parada de emergência, GPUs, pagamentos, configuração, acessos e auditoria);
   - **operador:** revisão dos pedidos e consultas (histórico, contas, suporte, interesses e pods), sem mexer em dinheiro, pods, contas ou acessos;
   - **fail-closed:** rota do painel com sessão que não está no mapa é recusada para todos (um teste confere que toda rota está no mapa). `GET <painel>/api/sessao` diz o papel e as rotas que ele abre; a página esconde o resto, e o servidor confere de novo em cada pedido (403);
   - sempre fica ao menos um dono ativo (mudar o papel ou tirar o acesso do último dono é recusado); ninguém tira o próprio acesso.
5. **A chave do servidor** (`RENDER2GO_ADMIN_TOKENS` + `RENDER2GO_ADMIN_TOTP`) continua, como **acesso de emergência** (papel dono), registrado como `entrada_emergencia`. A sessão aberta por ela cai se a chave sair do `.env`. Sem pessoa nenhuma e sem chave, a API avisa no log como criar o primeiro convite.
6. **Gestão dos acessos** (seção "Acessos" do painel, só dono): listar as pessoas (papel, situação, último acesso, sessões abertas, quem criou), mudar o papel, tirar o acesso (as sessões da pessoa caem na hora), convite novo, e os convites que ainda valem (com "Cancelar").
7. **Auditoria** (tabela `auditoria_admin`, que o `apagar-historico` não apaga): cada ação do painel com quem fez, o quê, em quem, quando, o IP e o navegador (coluna `agente`), e o **antes e o depois** das mudanças (plano, saldo, e-mail, desativar e reativar, duas etapas, papéis, acessos, pausa dos pods). Entradas recusadas, de emergência, convites abertos e usados também. Seção "Auditoria": filtros por pessoa, ação, alvo e período, página seguinte (100 por vez), planilha CSV (com o fuso de quem baixa, `;` como o Excel em português abre); baixar a planilha também fica registrado. Índice `(admin, quando)`.
8. **Visão geral** (`GET <painel>/api/resumo`): o que pede atenção agora (revisões e a mais antiga, pedidos em andamento, fila, pods, orquestrador, suporte, Pix pendentes) e os números de hoje no horário de Brasília; dinheiro só para o dono.

## Alternativas descartadas

- **Mandar a senha pronta (ou o QR Code) por mensagem:** fica guardada no histórico de quem mandou e de quem recebeu. O convite carrega só o direito de criar a senha, uma vez, em 24 horas.
- **Senha antes do código do aplicativo:** quem interceptasse o convite criaria a senha e depois leria o QR Code com calma. Com o código primeiro, o convite só vira acesso para quem tem o aplicativo na mão, e a senha nunca existe sem o segundo fator.
- **Código por SMS ou e-mail no lugar do aplicativo:** SMS sofre troca de chip; e-mail no painel seria mais um alvo. O aplicativo autenticador é o mesmo do cliente (TOTP, RFC 6238).
- **Chaves de acesso (passkeys, WebAuthn):** são o melhor caminho a seguir; ficaram para depois (pedem mais telas e o caso de quem troca de celular). O desenho por pessoa já é a base para elas.
- **bcrypt ou Argon2:** pediriam uma dependência a mais; o scrypt vem no Python (hashlib) e, com estes parâmetros, custa ~0,1 s e 32 MiB por conferência.
- **Tirar a chave do servidor:** sem ela, perder o celular de todos os donos trancaria o painel; o terminal resolve, mas a emergência pelo navegador fica, auditada.

## Consequências

- **Dependência nova** (`cryptography`, com versão fixada) e o QR Code em SVG (`segno`): a imagem da API precisa ser reconstruída na atualização (`docs/09`).
- **`RENDER2GO_SEGREDO_CONTAS` não pode mudar** depois que houver acessos ou verificação em duas etapas ligada: os segredos do aplicativo deixam de abrir e a entrada que depende deles é recusada (fail-closed); a saída é um convite novo para cada pessoa (e, no cliente, o suporte desligar a verificação).
- O endereço do convite é tão sensível quanto uma senha enquanto vale: mandar por um canal seguro (em mãos, gerenciador de senhas ou mensagem que se apaga), nunca por e-mail aberto nem print.
- **Testes:** `servico/tests/test_painel_acessos.py` (convite, entrada, papéis, auditoria, emergência, terminal), `test_paginas_painel.py` (as telas no navegador) e casos novos no teste de invasão (força bruta por pessoa e por nome, convite reaproveitado, código repetido, operador barrado; `docs/13`, seção 3).
