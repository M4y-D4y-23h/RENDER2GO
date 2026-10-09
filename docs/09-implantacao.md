# 09 — Implantação do serviço (fase 3)

Como colocar no ar o serviço de lotes (ADR 0003, fase 3): cliente envia as fotos pela página, o aprovador libera, os pods renderizam e o cliente baixa os vídeos.

## 0. Atualização de 09/10/2026: pré-pago, preços por segundo e teste com 5 fotos

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

## 0.0 Atualização de 08/10/2026 (noite): fila, previsão, subida, GPU e limpeza do histórico (já feita)

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
| API e páginas | contêiner `api` (`render2go-servico api`) | Cadastra lotes, assina URLs, recebe aprovações e conclusões |
| Orquestrador | contêiner `orquestrador` (mesma imagem) | Lê a fila, sobe e encerra pods; só um rodando por vez |
| Banco | Postgres 16 (contêiner ou gerenciado) | Clientes, lotes, jobs, eventos |
| Arquivos | R2 (bucket `gpu2go`, já criado) | Fotos, vídeos, folhas de conferência e espelho dos modelos |
| GPUs | Pods RunPod secure | Worker da fila (`render2go_worker.fila`), sem porta de entrada |
| HTTPS | Caddy | Certificado automático (Let's Encrypt) |

## 2. O que você precisa providenciar

1. **Um servidor pequeno (VPS)** com Docker: 2 vCPU, 2–4 GB de RAM, 20 GB de disco. Provedores como Hetzner, DigitalOcean ou Contabo cobram algo na faixa de US$ 5–15/mês (confira o preço atual). Não precisa de GPU.
2. **Um domínio ou subdomínio** (ex.: `videos.suaempresa.com.br`) com um registro DNS A apontando para o IP do servidor.
3. **A chave de API do RunPod.** Fica só no servidor, no `.env`.
4. **O bucket R2**, que já existe, com as duas configurações da seção 3.

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

# 4) firewall: só 22 (SSH), 80 e 443
```

**Variáveis do `.env`:**

| Variável | Para quê |
|---|---|
| `DOMINIO` | Domínio do serviço (Caddy pede o certificado) |
| `POSTGRES_PASSWORD` | Senha do Postgres local |
| `RENDER2GO_R2_ENDPOINT`, `_BUCKET`, `_ACCESS_KEY_ID`, `_SECRET_ACCESS_KEY` | R2; os mesmos valores do ambiente do Claude |
| `RENDER2GO_SEGREDO_APROVACAO` | Assina o carimbo de aprovação (HMAC) |
| `RENDER2GO_ADMIN_TOKENS` | Token(s) do aprovador, separados por vírgula |
| `RENDER2GO_WORKER_TOKENS` | Token dos pods; o orquestrador usa o primeiro |
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

- **Cadastrar um cliente:** em `https://<domínio>/aprovar`, na seção "Novo cliente". O código aparece uma vez; envie ao cliente por um canal seguro. Pelo terminal:
  ```bash
  docker compose exec api render2go-servico cliente "Padaria do Bairro" --plano teste
  ```
- **Recarregar o saldo de um cliente pago:** em `/aprovar`, seção "Clientes e saldo" (seção 5.1.7).
- **Histórico:** em `/aprovar`, abaixo dos pendentes. Mostra todos os pedidos (filtros: em revisão, aprovados, recusados), com cliente, IP, fotos, quem aprovou, motivo da recusa e andamento. A tabela "Uso por IP" soma as fotos por IP.
- **Código de cliente vazou:** no histórico, "Desativar o código deste cliente". O código deixa de valer na hora.
- **Aprovar pedidos:** em `/aprovar`. Nada vai para a GPU sem aprovação (ADR 0004). O cliente acompanha em `/`.
  - Cada foto ganha um selo "contém pessoas" ou "sem pessoas detectadas" segundos depois do envio (detector no próprio servidor, sem custo de GPU). Com pessoas e sem a caixa de autorização marcada, o lote mostra um aviso em vermelho.
  - O selo é uma ajuda: confira sempre as miniaturas.
- **Parar tudo e encerrar os pods:**
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

- **Regras** (a API impõe; a página só mostra):
  - um pedido só, com até 5 fotos (eram 10 até 09/10/2026);
  - vídeos de 3 s em 480p (3 s existe só no teste; nos outros planos, de 4 a 7 s);
  - no máximo 5 fotos de teste **por dispositivo** (navegador);
  - no máximo 50 fotos de teste **por IP** (`RENDER2GO_TESTE_FOTOS_IP`). Assim cinco pessoas da mesma empresa, na mesma rede, conseguem fazer o teste cada uma, e quem troca de navegador para juntar testes esbarra no teto da rede.
- **Dispositivo:** a página cria um identificador aleatório e guarda no próprio navegador. Janela anônima ou dados apagados geram outro; por isso existe o teto por IP.
- **Sinais no `/aprovar`, tabela "Dispositivos":** um dispositivo com vários IPs trocou de rede (celular, VPN ou proxy); com vários códigos de cliente, alguém pode estar juntando testes; fusos horários diferentes também são sinal de VPN.
- **Pedido recusado libera o teste** do cliente, para ele corrigir e mandar de novo.
- **Autorização das pessoas das fotos:** marcar a caixa não pede nome. O registro de auditoria liga a declaração à conta do cliente, ao IP, ao dispositivo e aos sinais do navegador, inclusive a hora do clique (ADR 0004, item 5). Ele sai em JSON pelo botão "Baixar registro de auditoria" no histórico.
- **Dados pessoais** guardados (IP, identificador do dispositivo, navegador, fuso, nome de quem declara) precisam constar da política de privacidade (LGPD).

## 5.1.1 Acesso interno (só o dono)

Para testar sem os limites do teste gratuito e sem esperar revisão, cadastre um cliente com o plano **Interno** em `/aprovar` ("Novo cliente").
- Os pedidos desse código são aprovados automaticamente no envio e sobem pods na hora, sem a janela de agrupamento.
- O filtro de conteúdo (C1), o aviso de pessoas e o registro de autorização continuam valendo.
- Não entregue esse código a clientes; se vazar, desative no histórico.
- **Agrupamento:** com só testes na fila e nenhum pod ligado, o orquestrador espera juntar 20 vídeos ou 10 min.
  - Cada subida de pod custa ~US$ 0,11 na L40S (subida + 3 min de pod quente), e um vídeo de teste ~US$ 0,011.
  - Dez pedidos de 1 foto em horários diferentes custariam ~US$ 1,24; juntos, ~US$ 0,23.
  - Pedidos pagos sobem na hora (e levam os testes da fila junto).
- **O IP vem do Caddy:** ele troca o `X-Forwarded-For` recebido de fora pelo IP real da conexão.
  - Se um dia o proxy da Cloudflare (nuvem laranja) for ligado, o IP passa a ser o da Cloudflare, e é preciso ler o `CF-Connecting-IP`.
  - Vários clientes podem dividir um IP (escritórios, redes de celular), e uma VPN troca o IP: é um sinal, não uma prova.

## 5.1.2 Suporte por e-mail

A página do cliente tem um link para **https://app.render2go.com/suporte**. A página de suporte funciona de dois jeitos:
- **com o código de acesso** (o navegador já conectado): a mensagem vai ligada à conta;
- **sem código** (quem perdeu o código ou não consegue entrar): o e-mail é obrigatório, e os limites são menores (2 por hora por dispositivo; 3 por hora e 10 por dia por IP).
- **Toda mensagem fica no banco** e aparece no `/aprovar`, em "Mensagens de suporte". Isso vale mesmo sem e-mail configurado.
- **Limite contra spam:** 3 mensagens por hora e 10 por dia por dispositivo; 5 por hora e 20 por dia por IP; 5 por hora e 15 por dia por cliente.
- **Para receber no seu e-mail**, configure no `.env`:

  ```
  RENDER2GO_SMTP_HOST=smtp.gmail.com        # ou o SMTP de um serviço de envio (Brevo, Resend, Mailgun...)
  RENDER2GO_SMTP_PORTA=587
  RENDER2GO_SMTP_TLS=starttls
  RENDER2GO_SMTP_USUARIO=seu-usuario
  RENDER2GO_SMTP_SENHA=senha-de-app        # no Gmail: "senha de app" (exige verificação em duas etapas)
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
- **Estilo "Livre (descreva o que quiser)":** o primeiro da lista, para pedidos que não se encaixam nos outros. Ele usa só ritmo e estilo naturais, e o texto do cliente decide o movimento (`worker/presets/receitas.v2.json`). Foto com pessoa continua exigindo a autorização, e o `/aprovar` avisa.
- **Modelo de geração:** a página mostra os modelos oferecidos (hoje só o Wan 2.2) e o pedido guarda qual foi usado. Para acrescentar um modelo, veja `servico/src/render2go_servico/modelos.py`. O worker recusa modelo que não conhece.

## 5.1.4 Aviso de cookies

- **Quando aparece:** em todo navegador novo, na página do cliente e no `/suporte`.
- **Opções:** "Aceitar todos", "Só os necessários" ou "Gerenciar".
- **Categorias:**
  - **Necessários**, sempre ativos: manter o cliente conectado com o código, segurança e prevenção de abuso (identificador do dispositivo, limite do teste) e lembrar a escolha;
  - **Estatísticas e marketing:** desligados, e nada disso carrega hoje. Se um dia você puser Google Analytics, Plausible ou pixel de anúncio, ele só pode carregar quando a escolha guardada permitir (`render2go-cookies` no navegador).
- **Registro:** cada escolha fica gravada no servidor (tabela `cookies`: dispositivo, IP, navegador, versão e escolha), como prova do consentimento.
- **Mudar a escolha:** pelo link "Preferências de cookies", no rodapé. O aviso é o primeiro item no teclado (logo depois de "Pular para o conteúdo"); aberto pelo rodapé, Esc fecha sem salvar.
- **Falta:** a política de privacidade e os termos de uso, que devem explicar esses dados (LGPD).

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
- **Recarga:** o cliente paga por fora (ex.: Pix). Você lança em `/aprovar`, seção "Clientes e saldo": cliente, valor em reais, descrição (o cliente vê no extrato) e, de preferência, o código do Pix, para o mesmo pagamento nunca entrar duas vezes. Valor negativo desconta (ajuste); o saldo nunca fica negativo. Pelo terminal:
  ```bash
  docker compose exec api render2go-servico clientes                                   # id, plano e saldo
  docker compose exec api render2go-servico saldo cli_xxxxxxxxxxxxxxxx 19,90 --descricao "Pix de 09/10" --referencia E2026100912345
  ```
- **Extrato:** botão "Baixar" na tabela "Clientes e saldo" (JSON com todos os movimentos). O extrato é registro financeiro: o `apagar-historico` não o apaga.
- **Teste gratuito e plano interno** não usam saldo.

## 5.2 O que fica guardado

- **Fotos de entrada:** apagadas pela API assim que o lote fecha (pronto, com falhas ou recusado). A regra `fotos-7-dias` do bucket fica como rede de segurança, para lotes que nunca fecham ou que esperam revisão por dias. Não use menos de 7 dias: um lote parado na revisão perderia as fotos antes de chegar à GPU.
- **Vídeos e folhas de conferência:** pela regra `videos-30-dias` do bucket. Pode ser reduzida (ex.: 7 dias) se os termos de uso disserem que o cliente deve baixar os vídeos nesse prazo.
- **No banco:** pedido, IP, quantidade de fotos, eventos e o resultado da análise de pessoas. Nenhuma imagem.
- **Extrato do saldo** (tabela `movimentos`): recargas, cobranças e devoluções, com data e descrição. Fica mesmo depois do `apagar-historico`.

## 6. Primeiro teste depois de subir

1. Cadastre um cliente de teste (plano `teste`) e entre em `/` com o código.
2. Envie 2–3 fotos com vídeos de 3 s.
3. Aprove em `/aprovar`.
4. Acompanhe com `docker compose logs -f orquestrador`: um pod sobe (~3 min), renderiza, confere e envia os vídeos, e é encerrado ~3 min depois de a fila esvaziar.
5. Custo esperado: ~US$ 0,10–0,20 na L40S (`docs/11`).

## 7. Segurança (resumo)

- **Chaves só no servidor (`.env`, chmod 600):**
  - o pod recebe só o token de worker, a URL da API e os hosts permitidos;
  - o navegador recebe só URLs assinadas, uma por arquivo, que expiram.
- **Tokens:** os dos clientes ficam no banco só como SHA-256. Os tokens de admin e de worker vêm do `.env`; para trocar um, edite e reinicie.
- **Páginas (ADR 0006, docs/10):**
  - CSP estrita: scripts e estilos só do próprio domínio, nada embutido, Trusted Types obrigatório; a página fala só com a própria API e com o R2;
  - SRI nos scripts e no CSS; COOP, CORP e `Permissions-Policy` (câmera, microfone, localização e pagamento desligados);
  - pedido que muda algo na API vindo de outro site é recusado (403), pela checagem de `Sec-Fetch-Site`/`Origin`;
  - respostas da API com `Cache-Control: no-store`; `/aprovar` e a API com `noindex`.
- **Token do R2:** com permissão só no bucket `gpu2go`.
- **RunPod:** pods só secure, com desligamento automático.
- **Arquivos dos clientes:** fotos apagadas em 7 dias, vídeos em 30 (seção 3).

## 8. O que ainda não existe

- **Pagamento automático:** o cliente paga por fora (ex.: Pix) e você lança o saldo em `/aprovar`. O próximo passo é um intermediador brasileiro (Pix e cartão) com um aviso (webhook) que lança a recarga sozinho; o identificador do pagamento já impede que o mesmo pagamento entre duas vezes.
- **Recuperação do código de acesso pelo cliente:** é preciso cadastrar de novo.
- **Download do lote inteiro em um .zip:** hoje são links por vídeo.
- **Medição da fila com vários clientes em pods reais:** depende desta implantação.
