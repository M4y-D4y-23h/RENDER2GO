# ADR 0007: contas por e-mail, sessão em cookie, painel em endereço secreto com dois fatores e token por pod

- **Status:** aceito (09/10/2026), a pedido do dono do projeto: abrir o cadastro ao público antes da divulgação em massa, sem abrir a porta para contas falsas e abuso do teste gratuito, e fechar o painel de administração.
- **Contexto:** até 09/10/2026 o cliente entrava com um código de acesso que o dono criava no `/aprovar` e mandava por um canal seguro; o código ficava no navegador (`localStorage`) e ia no cabeçalho `Authorization`. O painel ficava em `/aprovar`, com um token fixo do `.env`, também guardado no navegador. Todos os pods usavam o mesmo token de worker, fixo. O teste gratuito era limitado por dispositivo e por IP, com limites em memória de cada processo. Com o cadastro aberto, cada um desses pontos vira alvo: robôs criando contas para juntar testes, força bruta em códigos e tokens, e um painel que mexe em saldo e em GPUs num endereço que qualquer um adivinha.

## Decisão

1. **Entrada sem senha, por código no e-mail:**
   - `POST /api/conta/codigo`, nesta ordem: aceite dos Termos de Uso e da Política de Privacidade (versões gravadas com data, IP e porta); formato do e-mail; verificação "não sou um robô" (item 3); limites (10 por hora e 30 por dia por rede, 5 por hora e 10 por dia por e-mail, 10 por hora por aparelho); e só então a lista de provedores. Assim, a resposta para um e-mail de empresa (que diz se há conta criada pelo painel) não sai sem passar pela verificação e pelos limites;
   - código de 8 dígitos, de uso único, válido por 10 minutos, com 5 tentativas; o banco guarda só um HMAC dele (chave `RENDER2GO_SEGREDO_CONTAS`). Pedir um código novo invalida o anterior. A resposta é a mesma com ou sem conta;
   - `POST /api/conta/entrar` confere o código; no 1º acesso, a conta nasce no **teste gratuito** (um pedido, até 5 fotos, vídeos de 3 s em 480p); abre a sessão (item 2). Esta rota não consulta a lista de provedores: só existe código para um e-mail aceito no envio;
   - **provedores aceitos** (`contas.DOMINIOS_PADRAO`): Gmail, Outlook/Hotmail/Live/MSN, Yahoo, iCloud, UOL, BOL e Terra, que pedem verificação (telefone ou desafio) para abrir uma caixa. Domínios descartáveis e próprios ficam de fora; o painel cria a conta de um e-mail de empresa, e `RENDER2GO_EMAIL_DOMINIOS_EXTRA` acrescenta domínios;
   - **um teste por caixa de e-mail:** os apelidos de uma caixa (pontos e "+" no Gmail, "+" nos outros, "-palavra" no Yahoo) são a mesma conta. A conta excluída deixa só um resumo HMAC do e-mail, e o mesmo e-mail volta já no plano pago;
   - os clientes antigos continuam entrando com o código de acesso ("Entrar com código de acesso"), que abre a mesma sessão; integrações seguem com `Authorization: Bearer`.
2. **Sessão em cookie** `__Host-render2go` (HttpOnly, Secure, SameSite=Lax, `Path=/`, 30 dias), guardada no banco só como SHA-256:
   - sair, sair de todos os aparelhos, baixar os dados da conta e excluir a conta (LGPD, art. 18), com o saldo positivo exigindo o reembolso antes;
   - CSRF: além do Fetch Metadata (ADR 0006, item 4), todo pedido que muda algo com a sessão exige o cabeçalho `X-Render2Go: 1`. Outro site só conseguiria mandá-lo com CORS, que a API não libera;
   - **revê o ADR 0006** (item 4 e a alternativa descartada "token em cookie HttpOnly"): com o cadastro aberto, o código no `localStorage` virou um alvo maior, e a mudança passou a valer a pena.
3. **"Não sou um robô" (Cloudflare Turnstile) numa página isolada**, `/verificacao`, aberta num quadro do próprio site:
   - só ela carrega o script da Cloudflare, com uma CSP própria e sem Trusted Types (o script da Cloudflare não funciona com eles); as outras páginas continuam com a CSP estrita;
   - a resposta chega à página por `postMessage`, com a origem e o quadro conferidos;
   - o servidor confere cada resposta na Cloudflare (siteverify, com o IP e a ação), uma vez só. Sem configuração, a entrada recusa (fail-closed), salvo `RENDER2GO_TURNSTILE=desligado`, só para desenvolvimento;
   - vale na entrada por e-mail e no suporte sem conta.
4. **Limites no banco** (tabela `limites`, UNLOGGED, janelas fixas), que valem entre os processos da API e entre máquinas:
   - rede = IPv4 inteiro ou IPv6 /64 (trocar de endereço dentro do /64 não zera o limite);
   - o IP e a porta reais vêm do Caddy (`X-Render2Go-IP` e `X-Render2Go-Porta`, sempre reescritos: o valor mandado de fora não passa);
   - credenciais erradas (sessão, código, token, token de worker) contam por rede e bloqueiam com 429.
5. **Painel em endereço secreto, com dois fatores:**
   - `RENDER2GO_ADMIN_CAMINHO` (de 12 a 64 caracteres aleatórios); sem ele, o painel fica desligado (404). O endereço não aparece em nenhuma página, script, sitemap ou 404 (o teste de invasão confere), e o script do painel (`painel.js`) só é servido nesse endereço;
   - entrada com o token (`RENDER2GO_ADMIN_TOKENS`) **e** o código do aplicativo autenticador (TOTP, RFC 6238, `RENDER2GO_ADMIN_TOTP`); o mesmo código não vale duas vezes;
   - 5 erros bloqueiam a rede por 15 minutos, mesmo com o código certo; lista opcional de IPs (`RENDER2GO_ADMIN_IPS`; fora dela, 404);
   - sessão em cookie `__Secure-render2go-painel`, só no caminho do painel, SameSite=Strict, 12 horas;
   - toda ação do painel (entrar, aprovar, recusar, saldo, plano, e-mail, desativar, desligar pod, emergência) fica na tabela `auditoria_admin`, com quem fez e o IP. As entradas recusadas também.
6. **Pods pelo painel:** desligar um pod ou parada de emergência (tabela `comandos`, que o orquestrador executa em até ~10 s).
   - A emergência pausa a subida de pods até "Retomar" e encerra os pods do orquestrador, inclusive os `render2go-*` que o RunPod lista e o processo não conhece.
   - O orquestrador dá sinal de vida na tabela `estado`, e o painel mostra há quanto tempo.
   - Trava de líder (`pg_try_advisory_lock`): só um orquestrador manda nos pods.
7. **Token de worker por pod:** cada pod recebe um token próprio (tabela `tokens_worker`, só SHA-256), preso ao nome do worker e válido até o desligamento automático + 15 min. Ele é revogado quando o pod é encerrado, pelo orquestrador ou pelo painel. O token fixo `RENDER2GO_WORKER_TOKENS` fica só para testes manuais (vazio em produção).
8. **Registros** que o `apagar-historico` não apaga:
   - aceites (versão, data, IP);
   - acessos (Marco Civil, art. 15: conta, IP, porta, data e hora), guardados por 6 meses e apagados de hora em hora depois de 190 dias;
   - auditoria do painel e pagamentos.

## Alternativas descartadas

- **Senha:** mais um segredo para o cliente esquecer e para o serviço guardar (vazamento, troca de senha, força bruta). O código no e-mail prova a posse da caixa, que é o que o teste gratuito precisa.
- **Entrar com o Google (OAuth):** resolveria só o Gmail e prenderia o cadastro a um terceiro. Pode vir depois, como opção.
- **Link mágico no e-mail:** antivírus e pré-visualizações clicam nos links (gastam o link), e o cliente aprende a clicar em "entrar" dentro de e-mails, que é o que o phishing explora. O código de 8 dígitos não tem link.
- **reCAPTCHA ou hCaptcha:** o Turnstile quase nunca pede para clicar em imagens, é gratuito, não usa os dados para anúncios, e a Cloudflare já é fornecedora (DNS e R2).
- **Turnstile direto em cada página:** exigiria tirar o Trusted Types e liberar o domínio da Cloudflare em todas as páginas. Isolado no quadro, o script de fora fica numa página sem dados.
- **Painel só por VPN ou túnel SSH:** mais forte, mas impraticável no celular. A lista de IPs (opcional) dá parte disso.
- **Limites em memória ou no Redis:** em memória não valem entre processos; o Redis seria mais uma peça para operar. A tabela UNLOGGED do Postgres aguenta o volume previsto (`docs/13`, seção 4).

## Consequências

- **Atualizar o servidor exige configurar antes** o SMTP, o Turnstile, o endereço do painel, um token novo e o autenticador (`docs/09`, atualização "09/10/2026 (4)"). Sem SMTP ou sem Turnstile, ninguém novo entra; a API escreve no log o que falta.
- **O `/aprovar` deixa de existir** (404). O endereço novo não deve ir para favoritos sincronizados com contas de terceiros nem para prints.
- **O e-mail transacional passa a ser crítico:** o domínio do remetente precisa de SPF, DKIM e DMARC, senão os códigos caem no spam (`docs/13`, seção 6).
- **Teste de invasão** (`servico/tests/teste_invasao.py`): confere estes pontos de fora, como um invasor faria; rodar a cada mudança relevante (`docs/13`, seção 3).
