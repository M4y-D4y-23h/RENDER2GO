# 09 — Implantação do serviço (fase 3)

Como colocar no ar o serviço de lotes (ADR 0003, fase 3): cliente envia as fotos pela página, o aprovador libera, os pods renderizam e o cliente baixa os vídeos.

## 0. Atualizações do servidor (a mais recente primeiro)

Toda atualização é o mesmo `git pull` e `docker compose up -d --build`, sem pedido gerando (reiniciar o orquestrador desliga os pods ligados). Abaixo, o que muda em cada uma e o passo extra, quando há.

### 09/10/2026 (4): contas por e-mail, painel secreto com 2 fatores, Pix e segurança

**Esta atualização tem passos antes.** Sem eles, ninguém novo consegue entrar e o painel fica fora do ar. Prepare primeiro o que está no `docs/13`, seção 6.1: o e-mail transacional (SMTP com SPF, DKIM e DMARC), o "não sou um robô" (Turnstile), os dados da empresa e, se quiser já no 1º dia, o Mercado Pago. Tenha à mão o aplicativo autenticador no celular (Google Authenticator, Authy ou 1Password) e o gerenciador de senhas. Depois, no servidor, **sem pedido gerando**, cole um bloco de cada vez:

**1) Backup do banco e imagem nova** (os contêineres ainda não mudam):
```bash
cd ~/GPU2GO && git pull && cd servico/implantacao
docker compose exec -T postgres pg_dump -U gpu2go gpu2go | gzip > ~/backup-antes-contas-$(date +%F).sql.gz
ls -lh ~/backup-antes-contas-*.sql.gz
docker compose build
```

**2) Painel e segredos.** O bloco gera o endereço secreto do painel, um token novo (o antigo ficou guardado em navegadores), a chave do autenticador e o segredo das contas (`RENDER2GO_SEGREDO_CONTAS`, mantido se já existir), e esvazia o `RENDER2GO_WORKER_TOKENS`. Ele mostra o endereço, o token e a chave **uma vez**: guarde os três no gerenciador de senhas e cadastre a chave no aplicativo (adicionar conta → inserir chave de configuração → baseada em tempo). Rodar de novo não troca nada; o `.env` anterior fica em `.env.antes-painel`. Depois de guardar, `clear` limpa a tela.
```bash
{
  cd ~/GPU2GO/servico/implantacao && { [ -e .env.antes-painel ] || cp -p .env .env.antes-painel; }
  if grep -qE '^RENDER2GO_ADMIN_CAMINHO=.+' .env; then
    echo "O painel já estava configurado (RENDER2GO_ADMIN_CAMINHO preenchido): nada foi trocado."
  else
    NOVO() { python3 -c 'import secrets; print(secrets.token_urlsafe(32))'; }
    CAMINHO="/painel-$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
    TOKEN=$(NOVO)
    TOTP=$(python3 -c 'import base64, secrets; print(base64.b32encode(secrets.token_bytes(20)).decode())')
    CONTAS=$(sed -n -E 's/^RENDER2GO_SEGREDO_CONTAS=(.+)$/\1/p' .env | tail -n1)
    [ -z "$CONTAS" ] && CONTAS=$(NOVO)
    sed -i -E '/^RENDER2GO_(ADMIN_CAMINHO|ADMIN_TOKENS|ADMIN_TOTP|SEGREDO_CONTAS|WORKER_TOKENS)=/d' .env
    [ -n "$(tail -c1 .env)" ] && echo >> .env
    printf '%s\n' \
      "RENDER2GO_ADMIN_CAMINHO=$CAMINHO" \
      "RENDER2GO_ADMIN_TOKENS=$TOKEN" \
      "RENDER2GO_ADMIN_TOTP=$TOTP" \
      "RENDER2GO_SEGREDO_CONTAS=$CONTAS" \
      'RENDER2GO_WORKER_TOKENS=' >> .env
    echo
    echo "=== GUARDE AGORA no gerenciador de senhas (depois, só no .env) ==="
    echo "Endereço do painel:     https://app.render2go.com$CAMINHO/"
    echo "Token do painel:        $TOKEN"
    echo "Chave do autenticador:  $(echo "$TOTP" | sed -E 's/(.{4})/\1 /g; s/ $//')"
    echo "  (no aplicativo: adicionar conta → inserir chave de configuração; nome: Render2Go painel; baseada em tempo)"
    unset CAMINHO TOKEN TOTP CONTAS
  fi
}
```

**3) Dados da empresa** (rodapé, termos e privacidade; Decreto 7.962/2013). O bloco pergunta e grava; sem apóstrofo nos textos. Pessoa física pode usar o nome completo e o CPF ("CPF 123.456.789-00"); os dados ficam públicos no site. Se errar algum, rode de novo: ele pergunta e troca os cinco; o `.env` de antes da 1ª vez fica em `.env.antes-empresa`.
```bash
{
  cd ~/GPU2GO/servico/implantacao && { [ -e .env.antes-empresa ] || cp -p .env .env.antes-empresa; }
  PERGUNTA() {
    local v=
    while :; do
      read -rp "$1: " v; v=${v//$'\r'}; v=${v//$'\t'/ }
      v="${v#"${v%%[![:space:]]*}"}"; v="${v%"${v##*[![:space:]]}"}"
      if [ -z "$v" ]; then continue; fi
      if [[ "$v" == *"'"* ]]; then echo "  Sem apóstrofo ('), por favor. Digite de novo." >&2; continue; fi
      if [ -n "$2" ] && ! [[ "$v" =~ ^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$ ]]; then echo "  Isso não parece um e-mail. Digite de novo." >&2; continue; fi
      break
    done
    printf '%s' "$v"
  }
  NOME=$(PERGUNTA "Razão social (ou nome completo, se for pessoa física)")
  DOC=$(PERGUNTA "Documento, com o tipo na frente (ex.: CNPJ 12.345.678/0001-90)")
  ENDERECO=$(PERGUNTA "Endereço completo (rua, número, bairro, cidade/UF, CEP)")
  CONTATO=$(PERGUNTA "E-mail de contato" email)
  PRIVACIDADE=$(PERGUNTA "E-mail de privacidade (LGPD; pode ser o mesmo)" email)
  sed -i -E '/^RENDER2GO_(EMPRESA_NOME|EMPRESA_DOCUMENTO|EMPRESA_ENDERECO|EMAIL_CONTATO|EMAIL_PRIVACIDADE)=/d' .env
  [ -n "$(tail -c1 .env)" ] && echo >> .env
  printf '%s\n' \
    "RENDER2GO_EMPRESA_NOME='$NOME'" \
    "RENDER2GO_EMPRESA_DOCUMENTO='$DOC'" \
    "RENDER2GO_EMPRESA_ENDERECO='$ENDERECO'" \
    "RENDER2GO_EMAIL_CONTATO='$CONTATO'" \
    "RENDER2GO_EMAIL_PRIVACIDADE='$PRIVACIDADE'" >> .env
  unset NOME DOC ENDERECO CONTATO PRIVACIDADE
  echo; grep -E '^RENDER2GO_(EMPRESA_|EMAIL_CONTATO|EMAIL_PRIVACIDADE)' .env
}
```

**4) E-mail (SMTP):** o bloco do `docs/13`, seção 6.1.1, passo 14 (credenciais SMTP do SES). O Turnstile (seção 6.1.2) e o Mercado Pago (seção 6.1, item 5) têm blocos e passos próprios.

**5) Sobe tudo**, com o contêiner novo "analisador", e confere:
```bash
cd ~/GPU2GO/servico/implantacao
comm -23 <(grep -oE '^RENDER2GO_[A-Z0-9_]+' exemplo.env | sort -u) <(grep -oE '^RENDER2GO_[A-Z0-9_]+' .env | sort -u)   # variáveis do exemplo que o .env não tem (quase todas têm padrão)
docker compose up -d
docker compose ps --format "table {{.Service}}\t{{.Status}}"
docker compose logs api | grep AVISO      # o que ainda falta configurar (aparece também no painel, em "Segurança")
```

- **Painel:** sai do `/aprovar`, que passa a dar 404, para `https://app.render2go.com<RENDER2GO_ADMIN_CAMINHO>/`. A entrada pede o token e o código do aplicativo autenticador. Guarde o endereço num gerenciador de senhas.
- **O que muda para os clientes:**
  - quem chega entra com o e-mail (Gmail, Outlook/Hotmail, Yahoo, iCloud, UOL, BOL ou Terra) e um código de 8 dígitos; a conta nova nasce no teste gratuito;
  - os clientes antigos entram com o código de acesso de sempre ("Entrar com código de acesso") ou pelo e-mail, depois que você ligar o e-mail à conta (painel, "Contas e saldo", ou `docker compose exec api render2go-servico email cli_xxx fulano@gmail.com`);
  - "Sua conta": baixar os dados, sair de todos os aparelhos e excluir a conta;
  - com o Mercado Pago configurado, o cliente recarrega por Pix, e o saldo entra sozinho (seção 5.1.7).
- **O que muda no painel:** seções novas "Pods" (desligar um pod, parada de emergência, retomar), "Interesse" (cada clique em "Tenho interesse", com a conta), "Recargas por Pix", "Contas e saldo" (busca, plano, e-mail, desativar) e "Segurança" (o que falta configurar e as ações do painel).
- **Servidor:**
  - contêineres com disco só de leitura e sem privilégios;
  - a análise das fotos roda no contêiner "analisador";
  - a API roda em `RENDER2GO_API_WORKERS` processos;
  - o Caddy grava o log de acesso no volume dele (`/data/logs`) e ganha HTTP/3. O Compose recria o Caddy sozinho, porque a configuração dele mudou;
  - libere 443/udp no firewall, para o HTTP/3 (opcional).
- **Páginas novas:** `/termos`, `/privacidade` e `/cookies`, com a identificação da empresa no rodapé (`RENDER2GO_EMPRESA_*`). Peça a revisão de um advogado antes da divulgação (`docs/13`, seção 6.7).
- **Se algo der errado:** volte para o commit anterior (`git log --oneline -5` mostra; ex.: `git checkout 1943f11`) e rode `docker compose up -d --build`. O banco aceita a versão anterior, porque as tabelas e colunas novas só se somam; o `/aprovar` volta com o token de antes. Para voltar à versão nova: `git checkout main && git pull`. O backup do passo 1 é só para o pior caso.

### 09/10/2026 (3): tela de finalidades e licenças dos modelos

Sem passo extra: a tabela `interesses` é criada sozinha na partida da API.

- **O que muda:** antes do pedido, o cliente escolhe o que quer criar, em blocos (seção 5.1.10). Só "Foto vira vídeo" funciona; as outras opções aparecem como "Em breve", com "Tenho interesse", e a contagem aparece no `/aprovar`, seção "Interesse".
- **Licenças:** o registro dos modelos que podem ser usados comercialmente, e com que condições, está no `docs/12`. Ele recomenda trocar o Wan em produção (ajuste da comunidade "Uncensored") pelos arquivos oficiais, com ADR e teste em GPU.

### 09/10/2026 (2): qualidade dos vídeos, pedidos leigos e relatório de GPUs

No servidor, **sem pedido gerando** (reiniciar o orquestrador desliga os pods ligados):

```bash
cd ~/GPU2GO && git pull
cd servico/implantacao
docker compose up -d --build
docker compose ps --format "table {{.Service}}\t{{.Status}}"
```

- **O que muda:**
  - **Vídeo que troca de produto no meio não chega ao cliente.** O worker confere a continuidade do vídeo inteiro (cor do centro de cada quadro); corte brusco ou mudança de cor reprova, e o vídeo é refeito com outra seed. Se não sair em 3 tentativas, ele falha e, no pré-pago, volta para o saldo (seção 5.1.9).
  - **Tradutor v6 e termos de cena única** em todos os estilos: o texto vale para cada foto; "crie um vídeo desses carros pra cada foto" vira a cena de um carro; pedido leigo vira movimento suave.
  - **Página do cliente:** aviso de foto pequena (a foto vai ser ampliada) e exemplos de pedido simples e detalhado.
  - **`/aprovar`, seção "GPUs":** cada pod que subiu, com tempo ligado, vídeos e custo estimado (seção 5.1.8).
- **Recomendado, se ainda não fez:** `RENDER2GO_AGRUPAR_TESTE_MIN=0` no `.env` (teste gratuito sem os 10 min de espera para juntar testes; seção 5.1) e saldo no RunPod (abaixo de ~US$ 1,60 o orquestrador não liga pods).

### 09/10/2026 (1): pré-pago, preços por segundo e teste com 5 fotos

No servidor:

```bash
cd ~/GPU2GO && git pull
cd servico/implantacao
docker compose up -d --build
docker compose ps --format "table {{.Service}}\t{{.Status}}"
# clientes do plano pago passam a precisar de saldo: veja quem são e lance o saldo de cada um
docker compose exec api render2go-servico clientes
docker compose exec api render2go-servico saldo cli_xxxxxxxxxxxxxxxx 50 --descricao "Pix de 09/10"
```

- **O que muda:**
  - **Plano pago = pré-pago.** O cliente tem saldo em reais e paga por segundo de vídeo: **480p a R$ 0,25/s e 720p a R$ 0,50/s** (US$ 0,05 e US$ 0,10 no câmbio de R$ 5,00). Os preços mudam no `.env` (`RENDER2GO_PRECO_480P_S`, `RENDER2GO_PRECO_720P_S`; seção 4).
  - **Cobrança:** o pedido é cobrado inteiro quando as fotos terminam de chegar. Sem saldo, a página avisa antes do envio, com o preço.
  - **Devolução automática:** pedido recusado na revisão volta inteiro; cada vídeo que não sai (erro ou cancelado) volta sozinho para o saldo.
  - **Duração:** 3 s só no teste gratuito. Pago e interno, de 4 a 7 s.
  - **Teste gratuito:** um pedido, vídeos de 3 s em 480p, **até 5 fotos** (eram 10); 5 por dispositivo e 50 por IP.
  - **Recarga:** o cliente paga por fora (Pix) e você lança em `/aprovar`, seção "Clientes e saldo", ou pelo terminal (seção 5.1.7). Ainda não há pagamento automático.
- **Atenção:** um cliente do plano pago com saldo zero não consegue mais pedir. Lance o saldo dos clientes atuais logo depois de atualizar.

### 08/10/2026 (noite): fila, previsão, subida, GPU e limpeza do histórico (já feita)

No servidor:

```bash
cd ~/GPU2GO && git pull
cd servico/implantacao
docker compose up -d --build
# limpeza do histórico dos testes: primeiro só mostra o que seria apagado, depois apaga
docker compose exec api render2go-servico apagar-historico
docker compose exec api render2go-servico apagar-historico --confirmar
# recria os contêineres: os registros (logs) antigos de cada um somem junto
docker compose up -d --force-recreate
docker compose ps --format "table {{.Service}}\t{{.Status}}"
```

- **O que muda:**
  - **Fila:** pedidos de uma mesma conta saem na ordem em que chegaram. Entre contas diferentes, a vez continua intercalada.
  - **Previsão:** usa o estado real dos pods (desligado, subindo, pronto) e as subidas medidas (seção 5.1.3).
  - **Subida mais rápida:** os modelos vêm só do espelho no R2 quando ele passa no teste de rede (37 GB em 44 s num host bom); os serviços sobem e o pedido é traduzido enquanto os modelos ainda baixam; o orquestrador olha a fila a cada 10 s (eram 30 s). Medido em pods reais: de 6,8 para 4,2–5,1 min até o 1º vídeo (seção 5.1.6).
  - **GPU:** a 1ª opção passa a ser a L40S, porque a RTX PRO 6000 subiu para US$ 2,49/h (seção 4, `RENDER2GO_GPUS`, e `docs/07`).
  - **Custos e planos:** `docs/11`.
- **`apagar-historico`** apaga os pedidos, os textos dos pedidos (prompts), os trabalhos, as autorizações, os eventos, as mensagens de suporte e o registro dos pods. Também apaga os arquivos dos clientes no R2 (`entrada/` e `saida/`); o espelho dos modelos (`modelos/`) fica.
  - Os **clientes e os códigos de acesso continuam valendo**. As escolhas de cookies ficam, salvo com `--cookies`.
  - Com um pedido em geração, ele recusa; espere terminar (ou use `--forcar`).
  - Depois de apagar, ele reescreve as tabelas (`VACUUM FULL`), para as linhas apagadas não ficarem no disco do banco.
  - Se você fez backups com `pg_dump` (seção 5), apague os arquivos `backup-*.sql.gz` antigos também.
- **Pods do RunPod:** não guardam nada. O disco de cada pod é apagado quando ele é encerrado.

## 0.1 Rodar 24 horas sem o PowerShell aberto

O serviço já roda sozinho no servidor. O PowerShell é só o controle remoto.
- **Fechar a conexão não para nada:**
  - os contêineres sobem com `docker compose up -d` (o `-d` solta do terminal) e têm `restart: unless-stopped`;
  - o Docker liga junto com o servidor (`systemctl is-enabled docker` responde `enabled`), então, depois de um reinício, tudo volta sozinho;
  - `docker compose logs -f` só mostra os registros, e Ctrl+C fecha a visualização sem parar o serviço.
- **O que para o serviço:**
  - `docker compose down` ou `docker compose stop`;
  - desligar o servidor no painel da Hetzner;
  - `docker compose up` sem o `-d`, que prende o serviço ao terminal: se o terminal fechar, ele para. Use sempre `-d`.
- **Conferir de fora:** abra https://app.render2go.com com o PowerShell fechado. Para checar a saúde: `curl -s https://app.render2go.com/saude`.

## 0.2 Servidor já instalado: migrar para o nome Render2Go (08/10/2026, já feito)

O projeto passou a se chamar Render2Go, e o código novo está na `main`. No servidor:

```bash
cd ~/GPU2GO
git fetch origin && git checkout main && git pull
cd servico/implantacao
docker compose -p gpu2go down             # para os contêineres com o nome antigo; os dados ficam nos volumes
sed -i 's/^GPU2GO_/RENDER2GO_/' .env      # opcional: os nomes antigos continuam valendo
docker compose up -d --build
docker compose ps --format "table {{.Service}}\t{{.Status}}"
```

- **O que não muda:**
  - o banco, o estado do orquestrador e os certificados, porque o `compose.yaml` usa os mesmos volumes (`gpu2go_*`);
  - o bucket R2 `gpu2go`;
  - o usuário e o banco Postgres `gpu2go`.
- **Aviso esperado:** o Compose mostra `volume "gpu2go_postgres" already exists but was created for project "gpu2go"`. Ele só informa e segue usando o volume.
- **Repositório no GitHub (opcional):** para trocar o nome do repositório, vá em Settings → General → Repository name. O GitHub redireciona o endereço antigo, mas atualize o servidor com `git remote set-url origin git@github.com:M4y-D4y-23h/<nome-novo>.git`.

## 1. O que roda onde

```
Navegador do cliente ──fotos (URL assinada)──►  R2 (Cloudflare)  ◄──vídeos (URL assinada)── Pods RunPod
        │                                          ▲                                        ▲
        └──── páginas e API (HTTPS) ────► Servidor: API + orquestrador + Postgres ──sobe/encerra─┘
                                                     (docker compose, Caddy com HTTPS)
```

| Peça | Onde | O que faz |
|---|---|---|
| API e páginas | contêiner `api` (`render2go-servico api`, `RENDER2GO_API_WORKERS` processos) | Contas e sessões, lotes, URLs assinadas, Pix, painel do dono no endereço secreto (ADR 0007) |
| Análise das fotos | contêiner `analisador` (mesma imagem) | Aviso de pessoas nas fotos, fora da API |
| Orquestrador | contêiner `orquestrador` (mesma imagem) | Lê a fila, sobe e encerra pods, executa os comandos do painel; só um manda por vez |
| Banco | Postgres 16 (contêiner ou gerenciado) | Contas, sessões, lotes, jobs, saldo, pagamentos, limites, registros |
| Arquivos | R2 (bucket `gpu2go`, já criado) | Fotos, vídeos, folhas de conferência e espelho dos modelos |
| GPUs | Pods RunPod secure | Worker da fila (`render2go_worker.fila`), sem porta de entrada |
| HTTPS | Caddy | Certificado automático (Let's Encrypt) |

## 2. O que você precisa providenciar

1. **Um servidor pequeno (VPS)** com Docker: 2 vCPU, 2–4 GB de RAM, 20 GB de disco. Provedores como Hetzner, DigitalOcean ou Contabo cobram algo na faixa de US$ 5–15/mês (confira o preço atual). Não precisa de GPU.
2. **Um domínio ou subdomínio** (ex.: `videos.suaempresa.com.br`) com um registro DNS A apontando para o IP do servidor.
3. **A chave de API do RunPod.** Fica só no servidor, no `.env`.
4. **O bucket R2**, que já existe, com as duas configurações da seção 3.
5. **Um provedor de e-mail transacional** (SMTP), para os códigos de entrada, com SPF, DKIM e DMARC no domínio do remetente (`docs/13`, seção 6.1).
6. **O "não sou um robô" da Cloudflare (Turnstile)**: um widget para o domínio, com a chave pública e a secreta.
7. **Os dados da empresa** para o rodapé e as páginas legais (razão social, CNPJ, endereço e e-mails).
8. **Opcional: uma conta no Mercado Pago** com chave Pix, para a recarga automática (ADR 0008).

## 3. Configuração do R2 (painel da Cloudflare, bucket `gpu2go` → Settings)

**CORS:** o navegador do cliente envia as fotos direto ao R2. Em "CORS Policy", cole o JSON abaixo, trocando o domínio:

```json
[
  {
    "AllowedOrigins": ["https://videos.suaempresa.com.br"],
    "AllowedMethods": ["PUT", "GET"],
    "AllowedHeaders": ["Content-Type"],
    "MaxAgeSeconds": 3600
  }
]
```

**Ciclo de vida:** em "Object lifecycle rules", adicione:

| Regra | Prefixo | Ação |
|---|---|---|
| `fotos-7-dias` | `entrada/` | Apagar objetos depois de 7 dias |
| `videos-30-dias` | `saida/` | Apagar objetos depois de 30 dias |
| `envios-incompletos` | (bucket todo) | Abortar envios em partes incompletos depois de 1 dia |

**Não crie regra para `modelos/`:** é o espelho dos modelos, que os pods usam como reserva.

## 4. Passo a passo no servidor

```bash
# 1) Docker e o código
curl -fsSL https://get.docker.com | sh
git clone https://github.com/M4y-D4y-23h/GPU2GO.git && cd GPU2GO/servico/implantacao   # repositório privado: use uma deploy key (SSH) só de leitura

# 2) configuração (nunca versionar o .env)
cp exemplo.env .env && chmod 600 .env
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # rode uma vez para cada segredo
nano .env    # DOMINIO, POSTGRES_PASSWORD, RENDER2GO_R2_*, segredos, RENDER2GO_API_URL, RUNPOD_API_KEY

# 3) subir
docker compose up -d --build
docker compose logs -f api orquestrador     # Ctrl+C para sair dos logs

# 4) firewall: só 22 (SSH), 80, 443 e 443/udp (HTTP/3); veja docs/13, seção 6.5
```

**Variáveis do `.env`:**

| Variável | Para quê |
|---|---|
| `DOMINIO` | Domínio do serviço (Caddy pede o certificado) |
| `POSTGRES_PASSWORD` | Senha do Postgres local |
| `RENDER2GO_R2_ENDPOINT`, `_BUCKET`, `_ACCESS_KEY_ID`, `_SECRET_ACCESS_KEY` | R2; os mesmos valores do ambiente do Claude |
| `RENDER2GO_SEGREDO_APROVACAO` | Assina o carimbo de aprovação (HMAC); vai também para os pods |
| `RENDER2GO_SEGREDO_CONTAS` | Chave dos códigos de entrada e do resumo dos e-mails excluídos (só a API). Defina na 1ª subida e não troque |
| `RENDER2GO_ADMIN_CAMINHO` | Endereço secreto do painel: `/` + 12 a 64 letras, números, `_` ou `-`. Sem ele, o painel fica desligado |
| `RENDER2GO_ADMIN_TOKENS` | Token(s) de entrada no painel, separados por vírgula (32+ caracteres) |
| `RENDER2GO_ADMIN_TOTP` | Segredo do aplicativo autenticador do painel (`render2go-servico totp-novo`) |
| `RENDER2GO_ADMIN_IPS` | Opcional: só estes IPs ou redes abrem o painel, separados por vírgula |
| `RENDER2GO_WORKER_TOKENS` | Só para testes manuais: cada pod recebe um token próprio. Deixe vazio |
| `RENDER2GO_SMTP_*`, `RENDER2GO_EMAIL_DE` | E-mail transacional: servidor, porta 587, usuário, senha e o remetente dos códigos |
| `RENDER2GO_TURNSTILE_SITE`, `_SEGREDO` | "Não sou um robô" (Cloudflare Turnstile). Sem eles, ninguém novo entra |
| `RENDER2GO_MP_ACCESS_TOKEN`, `RENDER2GO_MP_WEBHOOK_SEGREDO` | Opcional: recarga por Pix (Mercado Pago). `RENDER2GO_RECARGA_MIN`/`_MAX`: valores em reais (padrões 10 e 2000) |
| `RENDER2GO_EMPRESA_NOME`, `_DOCUMENTO`, `_ENDERECO`, `RENDER2GO_EMAIL_CONTATO`, `RENDER2GO_EMAIL_PRIVACIDADE` | Identificação do vendedor no rodapé e nas páginas legais |
| `RENDER2GO_API_WORKERS`, `RENDER2GO_DB_POOL` | Processos da API (padrão 1; recomendado 2) e conexões do banco por processo (padrão 10) |
| `RENDER2GO_EMAIL_DOMINIOS_EXTRA` | Opcional: domínios de e-mail aceitos além da lista padrão |
| `RENDER2GO_API_URL` | `https://` + domínio; é por ela que os pods falam com a API |
| `RUNPOD_API_KEY` | O orquestrador cria e encerra pods |
| `RENDER2GO_MAX_PODS`, `RENDER2GO_MAX_USD_H` | Teto de pods e de gasto por hora (o gasto de um pod novo conta pelo preço da GPU mais cara da lista que tiver vaga) |
| `RENDER2GO_PRAZO_MIN` | Opcional: prazo de entrega em minutos (mais pods, mais custo) |
| `RENDER2GO_AGRUPAR_TESTE_MIN`, `RENDER2GO_AGRUPAR_TESTE_VIDEOS` | Opcional: testes gratuitos esperam juntar 20 vídeos ou 10 min antes de subir um pod (padrões) |
| `RENDER2GO_GPUS` | Opcional: GPUs em ordem de preferência, separadas por vírgula, com o nome exato do RunPod. Padrão: `NVIDIA L40S,NVIDIA RTX 6000 Ada Generation,NVIDIA RTX PRO 6000 Blackwell Server Edition,NVIDIA GeForce RTX 4090`. Para trocar: edite e rode `docker compose up -d orquestrador` |
| `RENDER2GO_CICLO_S` | Opcional: de quantos em quantos segundos o orquestrador olha a fila (padrão 10, mínimo 5) |
| `RENDER2GO_PRECO_480P_S`, `RENDER2GO_PRECO_720P_S` | Opcional: preço do pré-pago em reais por segundo de vídeo (padrões 0,25 e 0,50). Vale para os pedidos novos; o pedido já cobrado devolve pelo preço que pagou. Depois de mudar: `docker compose up -d api` |
| `RENDER2GO_POD_OCIOSO_MIN` | Opcional: minutos que o pod fica ligado esperando o próximo pedido depois que a fila esvazia (padrão 3). Mais tempo = menos subidas, mais custo parado |

**Banco gerenciado (opcional):** troque o Postgres local por um serviço como Neon ou Supabase. Basta pôr `RENDER2GO_DB_URL` no `.env` e apagar as linhas `RENDER2GO_DB_URL` do `compose.yaml`. O esquema é criado sozinho na primeira subida.

## 5. Operação do dia a dia

- **Painel do dono:** `https://<domínio><RENDER2GO_ADMIN_CAMINHO>/`, com o token e o código do aplicativo autenticador (ADR 0007). Cinco erros bloqueiam a rede por 15 min.
- **Contas:** os clientes se cadastram sozinhos, pelo e-mail. Para criar a conta de um e-mail de empresa (fora da lista de provedores) ou já no plano pago: painel, seção "Nova conta". Pelo terminal:
  ```bash
  docker compose exec api render2go-servico cliente "Padaria do Bairro" --email dono@padariadobairro.com.br --plano pago
  ```
- **Recarregar o saldo de um cliente pago:** sozinho, por Pix, quando o Mercado Pago está configurado; à mão, no painel, seção "Contas e saldo" (seção 5.1.7).
- **Histórico:** no painel, abaixo dos pendentes. Mostra todos os pedidos (filtros: em revisão, aprovados, recusados), com cliente, IP, fotos, quem aprovou, motivo da recusa e andamento. A tabela "Uso por IP" soma as fotos por IP.
- **Conta comprometida:** no painel, "Desativar" (seção "Contas e saldo" ou no histórico). As sessões e o código antigo deixam de valer na hora.
- **Aprovar pedidos:** no painel. Nada vai para a GPU sem aprovação (ADR 0004). O cliente acompanha em `/`.
  - Cada foto ganha um selo "contém pessoas" ou "sem pessoas detectadas" segundos depois do envio (detector no próprio servidor, sem custo de GPU). Com pessoas e sem a caixa de autorização marcada, o lote mostra um aviso em vermelho.
  - O selo é uma ajuda: confira sempre as miniaturas.
- **Parar tudo e encerrar os pods:** no painel, seção "Pods", "Parada de emergência": encerra todos os pods em até ~10 s e não sobe outro até "Retomar". Cada pod também tem "Desligar". Pelo terminal (se o painel estiver fora do ar):
  ```bash
  docker compose stop orquestrador
  docker compose run --rm orquestrador render2go-servico limpar
  ```
- **Atualizar o código:** `git pull && docker compose up -d --build`.
  - Quando o `Caddyfile` muda (como em 08/10/2026, compressão zstd), rode também `docker compose restart caddy`: o Compose não percebe mudança no arquivo montado.
  - As páginas vêm prontas em `servico/web/publico/` (o servidor não precisa de Node). Navegadores com a página antiga em cache recarregam sozinhos uma vez.
- **Backup do banco** (ex.: diário, por cron):
  ```bash
  docker compose exec -T postgres pg_dump -U gpu2go gpu2go | gzip > backup-$(date +%F).sql.gz
  ```
- **Apagar o histórico** (pedidos, prompts, auditoria, suporte e arquivos dos clientes; os clientes ficam):
  ```bash
  docker compose exec api render2go-servico apagar-historico              # só mostra o que seria apagado
  docker compose exec api render2go-servico apagar-historico --confirmar  # apaga
  ```
  Antes de usar com clientes de verdade, confira os termos de uso: o registro de autorização (auditoria) também é apagado.
- **Gasto:** o painel do RunPod mostra os pods. O orquestrador respeita `RENDER2GO_MAX_USD_H` e o saldo da conta, e cada pod se desliga sozinho em no máximo 2 h.
- **Trocar a GPU:** `RENDER2GO_GPUS` no `.env` e `docker compose up -d orquestrador`. Os preços do dia saem em `docker compose exec orquestrador python -m render2go_worker.runpod_pods disponibilidade --gpu "NVIDIA L40S"`.

## 5.1 Teste gratuito e custo

- **Quem tem:** cada conta nova, criada pelo e-mail (ADR 0007). Um teste por caixa de e-mail: os apelidos do Gmail (pontos e "+") e de outros provedores contam como a mesma caixa, e o e-mail de uma conta excluída volta já sem o teste.
- **Regras** (a API impõe; a página só mostra):
  - um pedido só, com até 5 fotos (eram 10 até 09/10/2026);
  - vídeos de 3 s em 480p (3 s existe só no teste; nos outros planos, de 4 a 7 s);
  - no máximo 5 fotos de teste **por dispositivo** (navegador);
  - no máximo 50 fotos de teste **por IP** (`RENDER2GO_TESTE_FOTOS_IP`). Assim cinco pessoas da mesma empresa, na mesma rede, conseguem fazer o teste cada uma, e quem troca de navegador para juntar testes esbarra no teto da rede.
- **Dispositivo:** a página cria um identificador aleatório e guarda no próprio navegador. Janela anônima ou dados apagados geram outro; por isso existe o teto por IP.
- **Sinais no painel, tabela "Dispositivos":** um dispositivo com vários IPs trocou de rede (celular, VPN ou proxy); com vários códigos de cliente, alguém pode estar juntando testes; fusos horários diferentes também são sinal de VPN.
- **Pedido recusado libera o teste** do cliente, para ele corrigir e mandar de novo.
- **Autorização das pessoas das fotos:** marcar a caixa não pede nome. O registro de auditoria liga a declaração à conta do cliente, ao IP, ao dispositivo e aos sinais do navegador, inclusive a hora do clique (ADR 0004, item 5). Ele sai em JSON pelo botão "Baixar registro de auditoria" no histórico.
- **Dados pessoais** guardados (IP, identificador do dispositivo, navegador, fuso, nome de quem declara) precisam constar da política de privacidade (LGPD).

## 5.1.1 Acesso interno (só o dono)

Para testar sem os limites do teste gratuito e sem esperar revisão, crie uma conta com o plano **Interno** no painel ("Nova conta"), com um e-mail seu.
- Os pedidos desse código são aprovados automaticamente no envio e sobem pods na hora, sem a janela de agrupamento.
- O filtro de conteúdo (C1), o aviso de pessoas e o registro de autorização continuam valendo.
- Não entregue esse código a clientes; se vazar, desative no histórico.
- **Agrupamento:** com só testes na fila e nenhum pod ligado, o orquestrador espera juntar 20 vídeos ou 10 min.
  - Cada subida de pod custa ~US$ 0,11 na L40S (subida + 3 min de pod quente), e um vídeo de teste ~US$ 0,011.
  - Dez pedidos de 1 foto em horários diferentes custariam ~US$ 1,24; juntos, ~US$ 0,23.
  - Pedidos pagos sobem na hora (e levam os testes da fila junto).
- **O IP vem do Caddy:** ele sempre reescreve o `X-Render2Go-IP` (e a porta, `X-Render2Go-Porta`) com o IP real da conexão; o valor mandado de fora não passa. A API não lê o `X-Forwarded-For`.
  - Se um dia o proxy da Cloudflare (nuvem laranja) for ligado, o IP passa a ser o da Cloudflare, e é preciso ler o `CF-Connecting-IP`.
  - Vários clientes podem dividir um IP (escritórios, redes de celular), e uma VPN troca o IP: é um sinal, não uma prova.

## 5.1.2 Suporte por e-mail

A página do cliente tem um link para **https://app.render2go.com/suporte**. A página de suporte funciona de dois jeitos:
- **conectado** (entrou pelo e-mail ou com o código de acesso): a mensagem vai ligada à conta;
- **sem entrar** (quem não consegue entrar): o e-mail e o "não sou um robô" são obrigatórios, e os limites são menores (2 por hora por dispositivo; 3 por hora e 10 por dia por IP).
- **Toda mensagem fica no banco** e aparece no painel, em "Mensagens de suporte". Isso vale mesmo sem e-mail configurado.
- **Limite contra spam:** 3 mensagens por hora e 10 por dia por dispositivo; 5 por hora e 20 por dia por IP; 5 por hora e 15 por dia por cliente.
- **Para receber no seu e-mail**, configure no `.env` (o mesmo SMTP manda os códigos de entrada, com o remetente `RENDER2GO_EMAIL_DE`):

  ```
  RENDER2GO_SMTP_HOST=email-smtp.us-east-2.amazonaws.com   # Amazon SES, o escolhido (docs/13, seção 6.1.1)
  RENDER2GO_SMTP_PORTA=587
  RENDER2GO_SMTP_TLS=starttls
  RENDER2GO_SMTP_USUARIO=seu-usuario
  RENDER2GO_SMTP_SENHA=senha-smtp          # "SMTP credentials" do console do SES
  RENDER2GO_SUPORTE_DE=seu-remetente@...
  RENDER2GO_SUPORTE_PARA=seu-email@...
  ```

  Depois rode `docker compose up -d`.
- **Porta:** use a 587. Provedores de nuvem costumam bloquear a saída pelas portas 25 e 465 em contas novas.
- **Responder:** o e-mail chega com "Responder para" apontando para o e-mail que o cliente informou.

## 5.1.3 O que o cliente vê enquanto espera

- **Em análise:** "começa em até N min". O tempo vem de `RENDER2GO_PRAZO_REVISAO_MIN` (padrão 60); ajuste ao seu ritmo de revisão.
- **Na fila:** "começa em cerca de N min". Desde 08/10 (noite), a conta usa o estado real dos pods, que o orquestrador registra na tabela `geradores`:
  - **nenhum pod ligado:** a subida inteira. É a mediana das últimas 10 subidas medidas, da decisão de ligar o pod até o 1º vídeo começar, com a tradução do pedido; até haver 3 medidas, vale 4 min. Soma-se o ciclo do orquestrador e, nos testes grátis, a espera de juntar testes;
  - **pod subindo:** só o que falta da subida;
  - **pod pronto:** a tradução do pedido (~45 s), se ainda não foi feita;
  - **vídeos à frente:** contados na mesma ordem da fila (seção 5.1.5).
  - Antes, um pod desligado havia menos de 10 min contava como ligado, e a página prometia "em instantes" para um pedido que ainda ia esperar a subida inteira.
- **Gerando:** "termina em cerca de N min", pela média dos últimos vídeos prontos e pelos vídeos de outros clientes que se intercalam com os dele.
- **Sempre:** um ponto pulsando e "Atualizado às HH:MM:SS". A página se atualiza sozinha a cada 10 s. Nenhuma etapa interna aparece.
- **Vídeos prontos:** "Assistir" abre o vídeo na própria página (com "Abrir em outra aba" e "Baixar" ao lado); "Baixar" também aparece em cada vídeo da lista.
- **Antes de enviar:** o cliente vê as miniaturas das fotos escolhidas (pode tirar uma por uma), um exemplo de texto para o estilo escolhido e o envio em bytes e porcentagem, com um ✓ em cada foto que terminou de subir. Fechar a página no meio do envio pede confirmação.
- **Estilo "Livre (descreva o que quiser)":** o primeiro da lista, para pedidos que não se encaixam nos outros. Ele usa só ritmo e estilo naturais, e o texto do cliente decide o movimento (`worker/presets/receitas.v3.json`). Foto com pessoa continua exigindo a autorização, e o painel avisa.
- **Modelo de geração:** a página mostra os modelos oferecidos (hoje só o Wan 2.2) e o pedido guarda qual foi usado. Para acrescentar um modelo, veja `servico/src/render2go_servico/modelos.py`. O worker recusa modelo que não conhece.

## 5.1.4 Aviso de cookies

- **Quando aparece:** em todo navegador novo, na página do cliente e no `/suporte`.
- **Opções:** "Aceitar todos", "Só os necessários" ou "Gerenciar".
- **Categorias:**
  - **Necessários**, sempre ativos: manter o cliente conectado (cookie da sessão), segurança e prevenção de abuso (identificador do dispositivo, limite do teste, "não sou um robô") e lembrar a escolha;
  - **Estatísticas e marketing:** desligados, e nada disso carrega hoje. Se um dia você puser Google Analytics, Plausible ou pixel de anúncio, ele só pode carregar quando a escolha guardada permitir (`render2go-cookies` no navegador).
- **Registro:** cada escolha fica gravada no servidor (tabela `cookies`: dispositivo, IP, navegador, versão e escolha), como prova do consentimento.
- **Mudar a escolha:** pelo link "Preferências de cookies", no rodapé. O aviso é o primeiro item no teclado (logo depois de "Pular para o conteúdo"); aberto pelo rodapé, Esc fecha sem salvar.
- **Documentos:** a Política de Cookies (`/cookies`), a Política de Privacidade (`/privacidade`) e os Termos de Uso (`/termos`) explicam esses dados (LGPD), com links no rodapé de todas as páginas.

## 5.1.5 Ordem da fila

- **Compilações primeiro:** a tradução de cada pedido é rápida e libera os vídeos dele.
- **Pagos e internos antes dos testes grátis.**
- **Entre clientes, a vez é intercalada:** vai primeiro quem tem menos vídeos começados nos pedidos em andamento. O vídeo 1 de cada cliente sai antes do vídeo 2 de qualquer um.
- **No mesmo cliente, ordem de chegada:** o pedido novo espera o anterior terminar. Até 08/10 a vez era por pedido, e o pedido novo de uma conta passava na frente do antigo da mesma conta.
- **No mesmo pedido:** os vídeos saem na ordem da lista que o cliente vê.

## 5.1.6 Subida do pod (do pedido aprovado ao 1º vídeo)

1. O orquestrador vê o pedido aprovado em até 10 s (`RENDER2GO_CICLO_S`).
2. O RunPod liga o pod na 1ª GPU da lista com vaga. Antes de instalar, o pod passa no teste de CUDA e no de rede; o espelho dos modelos no R2 é testado primeiro.
3. O pod instala o ambiente (venv, ComfyUI e Ollama) e baixa os modelos (37 GB) ao mesmo tempo. Com o espelho aprovado, os modelos vêm só do R2: em 08/10, 44 s num host bom.
4. Com o ambiente pronto, sobem o ComfyUI e o Ollama, e o worker já traduz o pedido. Os modelos ainda podem estar baixando.
5. Com os modelos conferidos (bytes e SHA-256), começa o 1º vídeo.

- **Medido em 08/10** (`worker/evals/gpu-2026-10-08-subida/`): do pedido do pod ao 1º vídeo de 3 s pronto, **4,2 a 5,1 min**. Antes das mudanças, 6,8 min num host lento.
- **O que mais pesa agora:**
  - a instalação do ambiente pelo PyPI e pelo GitHub: 1 a 3 min, conforme o host;
  - o 1º vídeo, que carrega os modelos na GPU: ~30 a 55 s.
- **Cada subida fica registrada** na tabela `geradores` e já entra na previsão. Para ver as últimas:
  ```bash
  docker compose exec postgres psql -U gpu2go gpu2go -c "SELECT gpu, round(primeiro_video_em - criado) AS subida_s, to_timestamp(criado) AS quando FROM geradores ORDER BY criado DESC LIMIT 10"
  ```

## 5.1.7 Saldo pré-pago (plano pago)

- **Preço:** por segundo de vídeo, 480p a R$ 0,25 e 720p a R$ 0,50 (padrões; seção 4). Um vídeo de 5 s em 480p custa R$ 1,25; de 7 s em 720p, R$ 3,50. Custos e margens: `docs/11`, seção 4.
- **O que o cliente vê** no cartão "Novo pedido": o saldo, os preços, o extrato (ao abrir "Ver extrato") e, junto do botão de envio, o custo do pedido e o saldo depois dele. Cada pedido mostra quanto custou e quanto voltou para o saldo.
- **Cobrança:** quando as fotos terminam de chegar, o pedido inteiro (fotos × preço do vídeo). Se outro pedido gastou o saldo enquanto as fotos subiam, o pedido é recusado sem cobrança e as fotos são apagadas.
- **Devolução automática:** pedido recusado na revisão volta inteiro; cada vídeo que termina em erro ou cancelado volta sozinho. Nada é cobrado ou devolvido duas vezes.
- **Recarga por Pix, sozinha** (ADR 0008), com o Mercado Pago configurado: o cliente clica em "Adicionar saldo", escolhe o valor (R$ 10 a R$ 2.000), informa o CPF ou CNPJ de quem paga e paga o QR (vale 1 hora). O saldo entra quando o Mercado Pago confirma, uma vez só, e a 1ª recarga tira a conta do teste gratuito. O painel lista as recargas em "Recargas por Pix"; "Conferir" pergunta de novo ao Mercado Pago.
- **Recarga à mão** (pagamento por fora, ajustes): no painel, seção "Contas e saldo": cliente, valor em reais, descrição (o cliente vê no extrato) e, de preferência, o código do Pix, para o mesmo pagamento nunca entrar duas vezes. Valor negativo desconta (ajuste); o saldo nunca fica negativo. Pelo terminal:
  ```bash
  docker compose exec api render2go-servico clientes                                   # id, plano e saldo
  docker compose exec api render2go-servico saldo cli_xxxxxxxxxxxxxxxx 19,90 --descricao "Pix de 09/10" --referencia E2026100912345
  ```
- **Extrato:** botão "Baixar" na tabela "Clientes e saldo" (JSON com todos os movimentos). O extrato é registro financeiro: o `apagar-historico` não o apaga.
- **Teste gratuito e plano interno** não usam saldo.

## 5.1.8 Relatório de GPUs (painel, seção "GPUs")

- **Uma linha por GPU (pod) que o orquestrador ligou:** quando ligou, id do pod no RunPod (`pod-…`), GPU, datacenter, preço por hora, tempo até o 1º vídeo, tempo ligado, vídeos entregues (entre parênteses, os que estão gerando), vídeos que falharam, pedidos atendidos, uso da GPU (tempo gerando ÷ tempo ligado), custo estimado e situação ("ligada", "desligada: fila vazia", "falhou na subida: não subiu").
- **Totais do período** (24 h, 7, 30 ou 90 dias): GPUs ligadas, tempo ligado, custo estimado, vídeos entregues, custo por vídeo e falhas. "Baixar relatório" salva tudo em JSON.
- **Como o custo é calculado:** preço por hora × tempo ligado, da criação do pod pelo RunPod até o encerramento. O pod que falha na instalação também aparece, porque também é cobrado. Um asterisco marca o tempo estimado: o orquestrador parou sem registrar o desligamento (o desligamento automático encerra o pod em no máximo 2 h) ou a GPU não informou o preço.
- **Confira com a fatura do RunPod** de vez em quando: o relatório é uma estimativa e não inclui armazenamento nem taxas.
- **Pods de antes de 09/10** aparecem sem datacenter e com o desligamento estimado (fim da fila).
- **A limpeza do histórico** (`apagar-historico`) apaga também estes registros.

## 5.1.9 Conferência automática dos vídeos

- **1º quadro:** o vídeo começa na foto certa (similaridade ≥ 0,9).
- **Continuidade (desde 09/10):** a cor média do centro de cada quadro não pode se afastar mais de 40 da do 1º quadro (deriva) nem pular mais de 12 de um quadro para o seguinte (salto). Pega o corte para outro produto e a fusão em outro produto de outra cor. Não pega a troca por um produto da mesma cor. Calibração e medidas de cada vídeo: `worker/evals/conferencia-2026-10-09/`.
- **Vídeo reprovado:** não é enviado; volta para a fila com outra seed. Depois de 3 tentativas, o vídeo falha (no pré-pago, volta para o saldo). O motivo fica no job (`erro`), e as medidas em `deriva_cor` e `salto_cor`, para calibrar os limites:
  ```bash
  docker compose exec postgres psql -U gpu2go gpu2go -c "SELECT arquivo, status, tentativas, round(deriva_cor::numeric, 1) AS deriva, round(salto_cor::numeric, 1) AS salto, erro FROM jobs WHERE tipo = 'render' ORDER BY atualizado DESC LIMIT 20"
  ```
- **A folha de conferência** (foto, início, meio e fim de cada vídeo) continua no armazenamento, para revisar um lote em minutos.

## 5.1.10 Tela de finalidades ("O que você quer criar?")

- **Onde:** no cartão "Novo pedido", logo depois de entrar, antes do formulário. O saldo (pré-pago) e o aviso do teste gratuito ficam acima.
- **Blocos:** as 6 finalidades mais usadas e "Ver todas as opções", que abre a lista completa (12), agrupada pelo que o cliente envia. Cada uma tem título pelo resultado ("Foto vira vídeo"), o tipo técnico embaixo ("Imagem para vídeo") e uma frase.
- **Disponível:** hoje só "Foto vira vídeo", com o Wan 2.2. Ao escolher, aparecem o modelo e para que ele é melhor, e o formulário; "Trocar" volta aos blocos.
- **Em breve:** não deixa escolher. "Tenho interesse" registra o pedido do cliente (uma vez por opção; não promete aviso, mas o painel guarda o e-mail de cada clique, para avisar quando a opção sair). Na janela, cada opção mostra os modelos previstos, todos com licença comercial conferida (`docs/12`, seção 4).
- **Painel, seção "Interesse":** quantos clientes marcaram cada opção, quantos deles estão no teste grátis e o pedido mais recente; abaixo, cada clique, com a conta, o e-mail, o plano e a hora. A limpeza do histórico não apaga esses registros (ficam com os clientes).
- **Para ativar uma opção:** o modelo entra em `servico/src/render2go_servico/modelos.py` com `disponivel: True` depois do ADR, do perfil do tradutor e dos testes (`docs/12`, seção 5). A tela muda sozinha.

## 5.2 O que fica guardado

- **Fotos de entrada:** apagadas pela API assim que o lote fecha (pronto, com falhas ou recusado). A regra `fotos-7-dias` do bucket fica como rede de segurança, para lotes que nunca fecham ou que esperam revisão por dias. Não use menos de 7 dias: um lote parado na revisão perderia as fotos antes de chegar à GPU.
- **Vídeos e folhas de conferência:** pela regra `videos-30-dias` do bucket. Pode ser reduzida (ex.: 7 dias) se os termos de uso disserem que o cliente deve baixar os vídeos nesse prazo.
- **No banco:** contas (e-mail, plano, saldo), sessões (só o resumo SHA-256), pedido, IP, quantidade de fotos, eventos e o resultado da análise de pessoas. Nenhuma imagem.
- **Registros que ficam mesmo depois do `apagar-historico`:** aceites dos termos (versão, data, IP), registros de acesso (Marco Civil: IP, porta, data e hora; apagados sozinhos depois de 6 meses), pagamentos e a auditoria do painel. Prazos completos na Política de Privacidade (`/privacidade`, seção 4).
- **Extrato do saldo** (tabela `movimentos`): recargas, cobranças e devoluções, com data e descrição. Fica mesmo depois do `apagar-historico`.

## 6. Primeiro teste depois de subir

1. Em `/`, entre com um Gmail seu: chega um código de 8 dígitos, e a conta nasce no teste gratuito.
2. Envie 2–3 fotos com vídeos de 3 s.
3. Aprove no painel (endereço secreto).
4. Acompanhe com `docker compose logs -f orquestrador`: um pod sobe (~3 min), renderiza, confere e envia os vídeos, e é encerrado ~3 min depois de a fila esvaziar.
5. Custo esperado: ~US$ 0,10–0,20 na L40S (`docs/11`).

## 7. Segurança (resumo)

Revisão completa, teste de invasão e a lista do dono: `docs/13`. Decisões: ADR 0006 (páginas), ADR 0007 (contas, sessões, painel e pods) e ADR 0008 (Pix).
- **Chaves só no servidor (`.env`, chmod 600)**, e cada contêiner só com as que usa (o compose esvazia as outras):
  - o pod recebe só o token dele (válido até o desligamento automático e revogado no fim), a URL da API e os hosts permitidos;
  - o navegador recebe só URLs assinadas, uma por arquivo, que expiram; a de envio só aceita o tamanho declarado.
- **Contas e painel:** sessões em cookie HttpOnly guardadas só como SHA-256; códigos de entrada de uso único; painel no endereço secreto com 2 fatores, bloqueio por tentativas e auditoria. Para trocar o token ou o autenticador do painel, edite o `.env` e rode `docker compose up -d api`.
- **Páginas (ADR 0006, docs/10):**
  - CSP estrita: scripts e estilos só do próprio domínio, nada embutido, Trusted Types obrigatório; a página fala só com a própria API e com o R2;
  - SRI nos scripts e no CSS; COOP, CORP e `Permissions-Policy` (câmera, microfone, localização e pagamento desligados);
  - pedido que muda algo na API vindo de outro site é recusado (403), pela checagem de `Sec-Fetch-Site`/`Origin` e do cabeçalho `X-Render2Go`;
  - respostas da API com `Cache-Control: no-store`; o painel, a verificação e a API com `noindex`; sem `robots.txt` (decisão do dono).
- **Servidor:** contêineres com disco só de leitura, sem as capacidades do Linux e com `no-new-privileges`; logs com tamanho limitado; Caddy com tempos limite e sem os cabeçalhos que revelam o servidor (`docs/13`, seção 2).
- **Token do R2:** com permissão só no bucket `gpu2go`.
- **RunPod:** pods só secure, com desligamento automático.
- **Arquivos dos clientes:** fotos apagadas em 7 dias, vídeos em 30 (seção 3).

## 8. O que ainda não existe

- **Cartão de crédito:** a recarga automática é só por Pix (ADR 0008); cartão fica para depois, com 3-D Secure e limites por conta.
- **Grupo de pods por modelo:** só quando entrar o 2º modelo (`docs/13`, seção 5).
- **Download do lote inteiro em um .zip:** hoje são links por vídeo.
- **Medição da fila com vários clientes em pods reais:** depende desta implantação.
