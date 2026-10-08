# 09 — Implantação do serviço (fase 3)

Como colocar no ar o serviço de lotes (ADR 0003, fase 3): cliente envia as fotos pela página, o aprovador libera, os pods renderizam e o cliente baixa os vídeos.

## 1. O que roda onde

```
Navegador do cliente ──fotos (URL assinada)──►  R2 (Cloudflare)  ◄──vídeos (URL assinada)── Pods RunPod
        │                                          ▲                                        ▲
        └──── páginas e API (HTTPS) ────► Servidor: API + orquestrador + Postgres ──sobe/encerra─┘
                                                     (docker compose, Caddy com HTTPS)
```

| Peça | Onde | O que faz |
|---|---|---|
| API e páginas | contêiner `api` (`gpu2go-servico api`) | Cadastra lotes, assina URLs, recebe aprovações e conclusões |
| Orquestrador | contêiner `orquestrador` (mesma imagem) | Lê a fila, sobe e encerra pods; só um rodando por vez |
| Banco | Postgres 16 (contêiner ou gerenciado) | Clientes, lotes, jobs, eventos |
| Arquivos | R2 (bucket `gpu2go`, já criado) | Fotos, vídeos, folhas de conferência e espelho dos modelos |
| GPUs | Pods RunPod secure | Worker da fila (`gpu2go_worker.fila`), sem porta de entrada |
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
nano .env    # DOMINIO, POSTGRES_PASSWORD, GPU2GO_R2_*, segredos, GPU2GO_API_URL, RUNPOD_API_KEY

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
| `GPU2GO_R2_ENDPOINT`, `_BUCKET`, `_ACCESS_KEY_ID`, `_SECRET_ACCESS_KEY` | R2; os mesmos valores do ambiente do Claude |
| `GPU2GO_SEGREDO_APROVACAO` | Assina o carimbo de aprovação (HMAC) |
| `GPU2GO_ADMIN_TOKENS` | Token(s) do aprovador, separados por vírgula |
| `GPU2GO_WORKER_TOKENS` | Token dos pods; o orquestrador usa o primeiro |
| `GPU2GO_API_URL` | `https://` + domínio; é por ela que os pods falam com a API |
| `RUNPOD_API_KEY` | O orquestrador cria e encerra pods |
| `GPU2GO_MAX_PODS`, `GPU2GO_MAX_USD_H` | Teto de pods e de gasto por hora |
| `GPU2GO_PRAZO_MIN` | Opcional: prazo de entrega em minutos (mais pods, mais custo) |
| `GPU2GO_AGRUPAR_TESTE_MIN`, `GPU2GO_AGRUPAR_TESTE_VIDEOS` | Opcional: testes gratuitos esperam juntar 20 vídeos ou 10 min antes de subir um pod (padrões) |

**Banco gerenciado (opcional):** troque o Postgres local por um serviço como Neon ou Supabase. Basta pôr `GPU2GO_DB_URL` no `.env` e apagar as linhas `GPU2GO_DB_URL` do `compose.yaml`. O esquema é criado sozinho na primeira subida.

## 5. Operação do dia a dia

- **Cadastrar um cliente:** em `https://<domínio>/aprovar`, na seção "Novo cliente". O código aparece uma vez; envie ao cliente por um canal seguro. Pelo terminal:
  ```bash
  docker compose exec api gpu2go-servico cliente "Padaria do Bairro" --plano teste
  ```
- **Histórico:** em `/aprovar`, abaixo dos pendentes. Mostra todos os pedidos (filtros: em revisão, aprovados, recusados), com cliente, IP, fotos, quem aprovou, motivo da recusa e andamento. A tabela "Uso por IP" soma as fotos por IP.
- **Código de cliente vazou:** no histórico, "Desativar o código deste cliente". O código deixa de valer na hora.
- **Aprovar pedidos:** em `/aprovar`. Nada vai para a GPU sem aprovação (ADR 0004). O cliente acompanha em `/`.
  - Cada foto ganha um selo "contém pessoas" ou "sem pessoas detectadas" segundos depois do envio (detector no próprio servidor, sem custo de GPU). Com pessoas e sem a caixa de autorização marcada, o lote mostra um aviso em vermelho.
  - O selo é uma ajuda: confira sempre as miniaturas.
- **Parar tudo e encerrar os pods:**
  ```bash
  docker compose stop orquestrador
  docker compose run --rm orquestrador gpu2go-servico limpar
  ```
- **Atualizar o código:** `git pull && docker compose up -d --build`.
- **Backup do banco** (ex.: diário, por cron):
  ```bash
  docker compose exec -T postgres pg_dump -U gpu2go gpu2go | gzip > backup-$(date +%F).sql.gz
  ```
- **Gasto:** o painel do RunPod mostra os pods. O orquestrador respeita `GPU2GO_MAX_USD_H` e o saldo da conta, e cada pod se desliga sozinho em no máximo 2 h.

## 5.1 Teste gratuito e custo

- **Regras** (a API impõe; a página só mostra):
  - um pedido só, com até 10 fotos;
  - vídeos de 3 s em 480p;
  - no máximo 10 fotos de teste por IP, somando todos os clientes de teste.
- **Pedido recusado libera o teste** do cliente, para ele corrigir e mandar de novo.
- **Agrupamento:** com só testes na fila e nenhum pod ligado, o orquestrador espera juntar 20 vídeos ou 10 min.
  - Cada subida de pod custa ~US$ 0,20 (subida + 3 min de pod quente), e um vídeo de teste ~US$ 0,015.
  - Dez pedidos de 1 foto em horários diferentes custariam ~US$ 2,15; juntos, ~US$ 0,35.
  - Pedidos pagos sobem na hora (e levam os testes da fila junto).
- **O IP vem do Caddy:** ele troca o `X-Forwarded-For` recebido de fora pelo IP real da conexão.
  - Se um dia o proxy da Cloudflare (nuvem laranja) for ligado, o IP passa a ser o da Cloudflare, e é preciso ler o `CF-Connecting-IP`.
  - Vários clientes podem dividir um IP (escritórios, redes de celular), e uma VPN troca o IP: é um sinal, não uma prova.

## 5.2 O que fica guardado

- **Fotos de entrada:** apagadas pela API assim que o lote fecha (pronto, com falhas ou recusado). A regra `fotos-7-dias` do bucket fica como rede de segurança, para lotes que nunca fecham ou que esperam revisão por dias. Não use menos de 7 dias: um lote parado na revisão perderia as fotos antes de chegar à GPU.
- **Vídeos e folhas de conferência:** pela regra `videos-30-dias` do bucket. Pode ser reduzida (ex.: 7 dias) se os termos de uso disserem que o cliente deve baixar os vídeos nesse prazo.
- **No banco:** pedido, IP, quantidade de fotos, eventos e o resultado da análise de pessoas. Nenhuma imagem.

## 6. Primeiro teste depois de subir

1. Cadastre um cliente de teste (plano `teste`) e entre em `/` com o código.
2. Envie 2–3 fotos com vídeos de 3 s.
3. Aprove em `/aprovar`.
4. Acompanhe com `docker compose logs -f orquestrador`: um pod sobe (~3 min), renderiza, confere e envia os vídeos, e é encerrado ~3 min depois de a fila esvaziar.
5. Custo esperado: ~US$ 0,15–0,25.

## 7. Segurança (resumo)

- **Chaves só no servidor (`.env`, chmod 600):**
  - o pod recebe só o token de worker, a URL da API e os hosts permitidos;
  - o navegador recebe só URLs assinadas, uma por arquivo, que expiram.
- **Tokens:** os dos clientes ficam no banco só como SHA-256. Os tokens de admin e de worker vêm do `.env`; para trocar um, edite e reinicie.
- **CSP da página:** permite falar só com a própria API e com o R2.
- **Token do R2:** com permissão só no bucket `gpu2go`.
- **RunPod:** pods só secure, com desligamento automático.
- **Arquivos dos clientes:** fotos apagadas em 7 dias, vídeos em 30 (seção 3).

## 8. O que ainda não existe

- **Pagamento:** você cobra por fora (ex.: Pix) e cadastra o cliente como `pago` em `/aprovar`.
- **Recuperação do código de acesso pelo cliente:** é preciso cadastrar de novo.
- **Download do lote inteiro em um .zip:** hoje são links por vídeo.
- **Medição da fila com vários clientes em pods reais:** depende desta implantação.
