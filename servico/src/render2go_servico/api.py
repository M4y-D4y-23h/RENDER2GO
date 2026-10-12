"""API do serviço (ADR 0003, fase 3). Os bytes nunca passam por aqui: só URLs assinadas.

Conta (ADR 0007): entrada sem senha, pelo código que chega no e-mail; a sessão fica num cookie
HttpOnly (`__Host-render2go`). No 1º acesso a conta nasce no teste gratuito. Pedidos que mudam algo
precisam do cabeçalho X-Render2Go (a página manda; outro site não consegue).
    GET  /api/publico                  o que a página de entrada precisa (chave do Turnstile, domínios)
    POST /api/conta/codigo             manda o código para o e-mail (Turnstile e limites por rede e e-mail)
    POST /api/conta/entrar             confere o código; cria a conta no 1º acesso; abre a sessão (com a
                                       verificação em duas etapas, devolve o desafio do aplicativo)
    POST /api/conta/entrar-codigo      clientes antigos: entra com o código de acesso (abre a mesma sessão)
    POST /api/conta/entrar-mfa         2º passo: código do aplicativo autenticador ou de recuperação
    POST /api/conta/sair | /sair-de-todos
    GET  /api/conta                    e-mail, plano, saldo, teste, perfil, tema, MFA, confirmação e aceites
    GET  /api/conta/dados              tudo o que guardamos sobre a conta (LGPD, art. 18)
    POST /api/conta/excluir            exclui a conta (LGPD; confirmação recente); com saldo, reembolso antes
Configurações da conta (só a sessão do navegador; as marcadas com * pedem a confirmação recente, 10 min):
    POST /api/conta/confirmar/codigo | /confirmar   "é você mesmo?": código do e-mail (ou, com a verificação
                                       em duas etapas, só o do aplicativo ou um de recuperação)
    POST /api/conta/mfa/iniciar* | /ativar | /desligar* | /codigos*   verificação em duas etapas (TOTP)
    POST /api/conta/email/codigo* | /email   troca de e-mail, com código no endereço novo
    GET  /api/conta/sessoes            aparelhos conectados; POST .../{ref}/encerrar e .../encerrar-outras
    POST /api/conta/desativar          pausa a conta (entrar de novo reativa); o saldo fica
    POST /api/conta/perfil | /preferencias   nome, sobrenome, Gravatar (por escolha) e tema
    GET  /api/conta/foto               a foto do Gravatar, baixada pelo servidor (sem terceiros na página)
    POST /api/conta/aceitar            aceite da versão nova dos termos e da privacidade (sessão aberta antes)

Cliente (sessão; ou Authorization: Bearer <código de acesso antigo>):
    GET  /api/receitas                 receitas, formatos, limites e as finalidades (tela antes do pedido)
    POST /api/interesses               "Tenho interesse" numa finalidade em breve
    POST /api/lotes                    cria o lote; devolve uma URL de envio por foto (pré-pago: confere o saldo)
    POST /api/lotes/{id}/enviado       confere no armazenamento, cobra (pré-pago) e manda para aprovação
    GET  /api/lotes  /api/lotes/{id}   andamento, previsão, cobrança e links dos vídeos (abrir e baixar)
    GET  /api/saldo                    pré-pago: saldo, preços por segundo e extrato
    POST /api/pagamentos/pix           recarga por Pix (ADR 0008): QR Code e copia e cola
    GET  /api/pagamentos  /api/pagamentos/{id}   recargas e a situação (confere no Mercado Pago)
    POST /api/suporte                  mensagem para o suporte (e-mail), com limite contra spam; sem conta,
                                       exige e-mail, o "não sou um robô" e tem limites menores (/suporte)
    POST /api/cookies                  escolha do aviso de cookies (prova do consentimento)

Mercado Pago (sem sessão; assinatura x-signature e conferência na API dele):
    POST /api/pagamentos/mercadopago   aviso de pagamento (webhook)

Painel (RENDER2GO_ADMIN_CAMINHO, ex.: /painel-<aleatório>): página e API só nesse endereço. Acessos por pessoa
(ADR 0009): nome de acesso + senha + código do aplicativo autenticador da própria pessoa, que nasce de um convite
de uso único; papéis dono e operador, com o mapa de permissões por rota (PERMISSOES_PAINEL). A chave do servidor
(RENDER2GO_ADMIN_TOKENS + RENDER2GO_ADMIN_TOTP) fica como acesso de emergência. Sessão num cookie restrito ao
caminho do painel (12 h); RENDER2GO_ADMIN_IPS limita de onde. Sem o caminho, não há painel.
    POST <painel>/api/entrar | /sair   GET <painel>/api/sessao (quem, o papel e as rotas que ele abre)
    POST <painel>/api/convite/iniciar | /confirmar | /senha   primeiro acesso: QR Code → código → senha
    GET  <painel>/api/acessos          pessoas e convites; POST .../convites, .../convites/{id}/cancelar,
                                       .../{id}/desativar e .../{id}/papel (só o dono)
    GET  <painel>/api/auditoria | /auditoria.csv   quem fez o quê, em quem, quando, de onde (filtros, página
                                       seguinte, antes e depois); GET <painel>/api/resumo: a visão geral
    GET  <painel>/api/lotes            lotes aguardando aprovação, com miniaturas e aviso de pessoas
    POST <painel>/api/lotes/{id}/aprovar | /recusar
    GET  <painel>/api/historico        pedidos feitos, aprovados e recusados; uso por IP e por dispositivo
    GET  <painel>/api/lotes/{id}/auditoria  registro completo de um lote (origem, autorização, fotos, eventos)
    GET  <painel>/api/suporte          últimas mensagens de suporte
    GET  <painel>/api/geradores?dias=30  relatório das GPUs (pods): id, GPU, horários, vídeos e custo estimado
    GET  <painel>/api/pods             pods ligados agora, pausa e sinal de vida do orquestrador
    POST <painel>/api/pods/{gerador}/desligar | /api/pods/emergencia | /api/pods/retomar
    GET  <painel>/api/interesses       "Tenho interesse": total por opção e cada clique (conta, e-mail, quando)
    GET  <painel>/api/clientes?busca=  contas com plano e saldo; POST cria uma conta por e-mail
    POST <painel>/api/clientes/{id}/saldo | /plano | /email | /desativar | /reativar
    GET  <painel>/api/clientes/{id}/extrato
    GET  <painel>/api/pagamentos       recargas; POST <painel>/api/pagamentos/{id}/conferir
    GET  <painel>/api/seguranca        o que está configurado e o que o painel fez (auditoria)

Teste gratuito (docs/08): um pedido só, até 5 fotos, vídeos de 3 s em 480p; no máximo 5 fotos de
teste por dispositivo (navegador). Sem teto por rede desde 11/10/2026, a pedido do dono
(`RENDER2GO_TESTE_FOTOS_IP` > 0 liga de novo: IP, IPv6 pelo /64, somando as contas de teste).
Plano pago (pré-pago, docs/11): saldo recarregado e preço por segundo de vídeo (`precos`); vídeos de
4 a 7 s. O pedido é cobrado quando as fotos chegam; recusa e vídeo que não sai voltam para o saldo.
A 1ª recarga por Pix passa a conta do teste para o plano pago.
Plano interno (o dono do serviço): sem limites de teste e com aprovação automática; o C1 continua.
As fotos de entrada são apagadas quando o lote fecha (pronto, com falhas ou recusado); os vídeos
ficam até a regra de retenção do bucket. A caixa de autorização gera um registro de auditoria
(quem declarou, texto e versão, IP, dispositivo, fotos e suas impressões SHA-256).

Workers (Bearer <token do pod>, criado pelo orquestrador para cada pod e revogado quando ele é
encerrado; o nome do worker vem do token), sem porta de entrada nos pods (eles puxam):
    POST /api/worker/reivindicar       próximo job, com URLs assinadas e carimbo de aprovação
    POST /api/worker/jobs/{id}/prompt  prompt compilado (passa pelo C1 aqui também)
    POST /api/worker/jobs/{id}/concluir  "ok" de render só com o vídeo no armazenamento, a
                                       conferência do 1º quadro acima do limiar e a da
                                       continuidade dentro dos limites (docs/08)

Páginas: / (cliente), /suporte, /termos, /privacidade e /cookies, e o painel no caminho secreto,
compiladas em servico/web/publico (docs/10). Os arquivos de /estatico/ levam ?v=<hash> e ficam um ano
em cache; as páginas são revalidadas a cada visita (ETag). Sem robots.txt (nada de lista de caminhos):
o que não é público responde com X-Robots-Tag: noindex. Sem /openapi.json nem /docs. Cabeçalhos de
segurança em toda resposta (CSP com Trusted Types, COOP, CORP, Permissions-Policy) e proteção contra
CSRF: pedido que muda algo na API só da própria página.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import html
import json
import math
import os
import queue
import re
import smtplib
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from render2go_worker import conferencia, filtro_imagem, filtros, lote, preparo, presets
from starlette.exceptions import HTTPException as StarletteHTTPException

from render2go_servico import SERVICO_DIR, contas, modelos, pagamentos, perfil, pessoas, precos, suporte
from render2go_servico.armazenamento import Armazenamento, chave_entrada, chave_saida
from render2go_servico.db import Banco, BancoError, SaldoInsuficiente, novo_id

TIPOS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
MAX_FOTOS_LOTE = 1000  # por envio (pedido HTTP): o pedido maior vai em partes, sem limite no total (pago e interno)
FOTOS_POR_ENVIO = MAX_FOTOS_LOTE  # a página divide nessas partes (os testes diminuem)
# pedido em sequência: textos próprios distintos por lote (cada um vira uma compilação a mais no pod)
MAX_TEXTOS_FOTO = 100
VIDEOS_POR_PAGINA = 100  # vídeos de um pedido grande por resposta (a página pede mais)
PREVIA_REVISAO = 60  # fotos de um pedido grande que o revisor vê de cara (mais as marcadas pela análise)
MARCADAS_REVISAO = 300  # teto das fotos marcadas (pessoas, rostos, erro) mostradas além da prévia
DIAS_EXTRATO = (7, 30, 90, 365, 0)  # períodos do extrato (0 = tudo)
MAX_LINHAS_CSV = 5000
RESOLUCAO_EXTRATO = {"rapida": "480p", "alta": "720p"}
DESCRICAO_PEDIDO = re.compile(r"^(?P<videos>\d+) vídeo\(s\) de (?P<duracao>\d+) s em (?P<resolucao>\w+)")
MAX_BYTES_FOTO = 50 * 1024**2
URL_ENVIO_S = 3600
URL_DOWNLOAD_S = 24 * 3600
# teste gratuito (docs/08): um pedido só, qualidade rápida (480p), vídeos de 3 s, uma variação,
# até 5 fotos (eram 10 até 09/10/2026); no máximo 5 fotos de teste por dispositivo. O teto por rede
# (somando todos os clientes de teste) saiu em 11/10/2026, a pedido do dono: 0 = sem teto (o padrão)
TESTE = {
    "qualidade": "rapida",
    "duracao": 3,
    "variacoes": 1,
    "fotos": 5,
    "fotos_dispositivo": 5,
    "fotos_ip": int(os.environ.get("RENDER2GO_TESTE_FOTOS_IP", 0)),
}
# texto da caixa de autorização: a página mostra este, e o registro guarda o texto e o SHA-256.
# Mudou o texto = versão nova (o registro antigo continua apontando para o texto que a pessoa viu).
AUTORIZACAO = {
    "versao": "autorizacao-v1",
    "texto": (
        "Declaro que tenho autorização por escrito de todas as pessoas que aparecem nestas fotos "
        "(ou de seus responsáveis legais) para criar e usar os vídeos, e que posso apresentá-la se for pedida."
    ),
}
# previsão mostrada ao cliente (sem detalhar as etapas internas)
PRAZO_ANALISE_S = int(os.environ.get("RENDER2GO_PRAZO_REVISAO_MIN", 60)) * 60  # tempo típico da revisão
# subida típica enquanto não há 3 subidas medidas (tabela geradores): da decisão de ligar o pod até o
# 1º vídeo começar, já com a tradução do pedido (07–08/10/2026: 3,5 a 4,5 min)
PREPARO_S = 4 * 60
VIDEO_PADRAO_S = 30.0  # sem histórico: ~25–30 s por vídeo de 3 s (medido em 08/10)
TRADUCAO_S = 45.0  # tradução do pedido num gerador já ligado, antes do 1º vídeo
CICLO_S = float(os.environ.get("RENDER2GO_CICLO_S", 10))  # o orquestrador olha a fila a cada CICLO_S
# os mesmos padrões do orquestrador (testes gratuitos esperam juntar vídeos antes de começar)
AGRUPAR_TESTE_S = float(os.environ.get("RENDER2GO_AGRUPAR_TESTE_MIN", 10)) * 60
AGRUPAR_TESTE_VIDEOS = int(os.environ.get("RENDER2GO_AGRUPAR_TESTE_VIDEOS", 20))
RETRATO_S = 3.0  # a fila e os pods são lidos uma vez a cada RETRATO_S para todas as previsões
DISPOSITIVO = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
FUSO = re.compile(r"^[A-Za-z0-9_+\-/]{1,64}$")
CAMINHO_ADMIN = re.compile(r"^/[A-Za-z0-9_-]{12,64}$")
LOTE_FECHADO = ("concluido", "concluido_com_falhas", "recusado")
NOME = re.compile(r"[^A-Za-z0-9_-]+")
PUBLICO = SERVICO_DIR / "web" / "publico"  # páginas compiladas (cd servico/web && npm run build)
PAGINAS = {"/": "index.html", "/suporte": "suporte.html", "/termos": "termos.html", "/privacidade": "privacidade.html",
           "/cookies": "cookies.html", "/verificacao": "verificacao.html"}  # fmt: skip
# telas da área do cliente (menu lateral): a mesma página da raiz, que mostra a tela pelo endereço (ts/inicio.ts e
# ts/moldura.ts). São da conta de quem entrou: noindex e fora do sitemap.xml
TELAS_CONTA = ("/modelos", "/extratos", "/logs", "/perfil", "/configuracoes", "/projetos", "/mix")
PAGINAS.update(dict.fromkeys(TELAS_CONTA, "index.html"))
# /verificacao: a página isolada do "não sou um robô" (Turnstile), aberta num quadro das páginas de entrada e
# de suporte. Só ela carrega o script da Cloudflare, com uma política própria (sem Trusted Types, que o
# script de terceiros não segue); as outras páginas continuam com trusted-types 'none'.
CSP_VERIFICACAO = (
    "default-src 'self'; script-src 'self' https://challenges.cloudflare.com; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data: https://challenges.cloudflare.com; "
    "frame-src https://challenges.cloudflare.com; connect-src 'self' https://challenges.cloudflare.com; "
    "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
)
TIPOS_WEB = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".webmanifest": "application/manifest+json",
    ".txt": "text/plain; charset=utf-8",
    ".xml": "application/xml; charset=utf-8",
}
CACHE_ANO = "public, max-age=31536000, immutable"  # /estatico/...?v=<hash atual>: o conteúdo nunca muda
CACHE_DIA = "public, max-age=86400"
CACHE_REVALIDAR = "no-cache"  # guarda, mas confere (ETag) a cada uso: página nova chega na hora
PERMISSOES = (
    "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()"
)
# cookies da sessão: com HTTPS, o prefixo __Host- prende o cookie a este domínio (sem subdomínios) e o
# __Secure- garante o envio só por HTTPS. Sem HTTPS (só desenvolvimento local), nomes sem prefixo.
COOKIES = {
    ("cliente", True): "__Host-render2go",
    ("cliente", False): "render2go-sessao",
    ("admin", True): "__Secure-render2go-painel",
    ("admin", False): "render2go-painel",
}
CABECALHO_PAGINA = "x-render2go"  # a página manda em todo pedido; outro site não consegue (CORS)
# identificação do serviço nas páginas (Decreto 7.962/2013: nome, CNPJ, endereço e contato visíveis):
# vem do .env, sem compilar as páginas de novo; o que faltar aparece como "[preencher]"
EMPRESA = {
    "EMPRESA_NOME": "RENDER2GO_EMPRESA_NOME",
    "EMPRESA_DOCUMENTO": "RENDER2GO_EMPRESA_DOCUMENTO",
    "EMPRESA_ENDERECO": "RENDER2GO_EMPRESA_ENDERECO",
    "EMAIL_CONTATO": "RENDER2GO_EMAIL_CONTATO",
    "EMAIL_PRIVACIDADE": "RENDER2GO_EMAIL_PRIVACIDADE",
}
# acessos do painel por pessoa (ADR 0009): o que cada papel faz. A chave do servidor (emergência) é "dono".
PAPEIS_PAINEL = {
    "dono": "tudo: revisão, contas e saldo, pods e parada de emergência, pagamentos, segurança, acessos e auditoria",
    "operador": "revisão dos pedidos e consultas (histórico, contas, suporte, interesses e pods, sem mexer neles)",
}
_TODOS, _DONO = frozenset(PAPEIS_PAINEL), frozenset({"dono"})
# mapa de permissões por rota do painel (caminho depois do endereço secreto). Rota com sessão que não está aqui é
# recusada para todos (fail-closed): a rota nova só funciona depois de dizer quem pode usá-la
PERMISSOES_PAINEL: dict[tuple[str, str], frozenset[str]] = {
    ("GET", "/api/resumo"): _TODOS,
    ("GET", "/api/lotes"): _TODOS,
    ("POST", "/api/lotes/{lote_id}/aprovar"): _TODOS,
    ("POST", "/api/lotes/{lote_id}/recusar"): _TODOS,
    ("GET", "/api/lotes/{lote_id}/auditoria"): _TODOS,
    ("GET", "/api/historico"): _TODOS,
    ("GET", "/api/suporte"): _TODOS,
    ("GET", "/api/interesses"): _TODOS,
    ("GET", "/api/pods"): _TODOS,
    ("GET", "/api/clientes"): _TODOS,
    ("GET", "/api/clientes/{cliente_id}/extrato"): _TODOS,
    ("POST", "/api/clientes"): _DONO,
    ("POST", "/api/clientes/{cliente_id}/saldo"): _DONO,
    ("POST", "/api/clientes/{cliente_id}/plano"): _DONO,
    ("POST", "/api/clientes/{cliente_id}/email"): _DONO,
    ("POST", "/api/clientes/{cliente_id}/desativar"): _DONO,
    ("POST", "/api/clientes/{cliente_id}/reativar"): _DONO,
    ("POST", "/api/clientes/{cliente_id}/mfa/desligar"): _DONO,
    ("GET", "/api/geradores"): _DONO,
    ("POST", "/api/pods/{gerador}/desligar"): _DONO,
    ("POST", "/api/pods/emergencia"): _DONO,
    ("POST", "/api/pods/retomar"): _DONO,
    ("GET", "/api/pagamentos"): _DONO,
    ("POST", "/api/pagamentos/{pid}/conferir"): _DONO,
    ("GET", "/api/seguranca"): _DONO,
    ("GET", "/api/acessos"): _DONO,
    ("POST", "/api/acessos/convites"): _DONO,
    ("POST", "/api/acessos/convites/{cid}/cancelar"): _DONO,
    ("POST", "/api/acessos/{aid}/desativar"): _DONO,
    ("POST", "/api/acessos/{aid}/papel"): _DONO,
    ("GET", "/api/auditoria"): _DONO,
    ("GET", "/api/auditoria.csv"): _DONO,
}
CONVITE_SENHA_S = 15 * 60  # entre o código do aplicativo conferido e a senha criada, no primeiro acesso

# a página anterior ao front-end de 08/10/2026, se ficou no cache de algum navegador, pede /app.js:
# este script recarrega uma vez, e a página nova (sempre revalidada) chega
RECARREGAR_JS = (
    b'(function(){try{if(sessionStorage.getItem("render2go-recarregou"))return;'
    b'sessionStorage.setItem("render2go-recarregou","1")}catch(e){}location.reload()})();\n'
)


@dataclass(frozen=True)
class ArquivoWeb:
    dados: bytes
    tipo: str
    versao: str  # 12 primeiros hex do SHA-256: o mesmo ?v= que o construir.mjs põe nas páginas
    etag: str


def substituicoes_empresa() -> dict[str, str]:
    return {k: os.environ.get(v, "").strip() or "[preencher]" for k, v in EMPRESA.items()}


def carregar_publico(raiz: Path = PUBLICO, valores: dict[str, str] | None = None) -> dict[str, ArquivoWeb]:
    """Arquivos de servico/web/publico na memória (poucas centenas de KB): resposta e ETag prontas.
    Nas páginas, [[CHAVE]] vira o valor de `valores` (identificação da empresa), já escapado."""
    valores = substituicoes_empresa() if valores is None else valores
    arquivos = {}
    for p in sorted(raiz.rglob("*")):
        if p.is_file() and p.suffix in TIPOS_WEB:
            dados = p.read_bytes()
            if p.suffix == ".html":
                texto = dados.decode()
                for k, v in valores.items():
                    texto = texto.replace(f"[[{k}]]", html.escape(v))
                dados = texto.encode()
            h = hashlib.sha256(dados).hexdigest()
            arquivos[p.relative_to(raiz).as_posix()] = ArquivoWeb(dados, TIPOS_WEB[p.suffix], h[:12], f'"{h[:32]}"')
    if "index.html" not in arquivos:
        raise RuntimeError(f"páginas ausentes em {raiz}: rode `npm ci && npm run build` em servico/web")
    return arquivos


def servir(request: Request, arq: ArquivoWeb, cache: str, status: int = 200) -> Response:
    cab = {"Cache-Control": cache, "ETag": arq.etag}
    pedido = request.headers.get("if-none-match", "")
    if status == 200 and any(t.strip().removeprefix("W/") in (arq.etag, "*") for t in pedido.split(",") if t.strip()):
        return Response(status_code=304, headers=cab)
    return Response(arq.dados, status_code=status, media_type=arq.tipo, headers=cab)


def mesma_origem(request: Request) -> bool:
    """Proteção contra CSRF (Fetch Metadata): o navegador diz de onde vem o pedido (Sec-Fetch-Site; os
    antigos, só Origin). Sem nenhum dos dois não é um navegador (pods, curl, testes): a API exige o token."""
    site = request.headers.get("sec-fetch-site")
    if site is not None:
        return site in ("same-origin", "none")
    origem = request.headers.get("origin")
    return origem is None or urlsplit(origem).netloc == request.headers.get("host", "")


def caminho_admin_do_ambiente() -> str | None:
    """RENDER2GO_ADMIN_CAMINHO: /<12 a 64 letras, números, _ ou ->. Ausente = painel desligado."""
    caminho = os.environ.get("RENDER2GO_ADMIN_CAMINHO", "").strip().rstrip("/")
    if not caminho:
        return None
    if not CAMINHO_ADMIN.match(caminho):
        raise SystemExit(
            "RENDER2GO_ADMIN_CAMINHO inválido: use /<de 12 a 64 letras, números, _ ou -> (ex.: /painel-<aleatório>)"
        )
    return caminho


class AnaliseFotos:
    """Análise das fotos em segundo plano (aviso de pessoas no painel): um thread, fila por lote.

    Agendada no envio e de novo quando o painel lista um lote com foto ainda sem análise
    (assim uma reinicialização da API no meio não deixa foto para trás). Com muitos envios, a análise
    sai da API e roda no serviço `render2go-servico analisar` (RENDER2GO_ANALISE=separada)."""

    def __init__(self, banco: Banco, armazenamento: Any, analisar: Callable[[bytes], dict]):
        self.banco, self.armazenamento, self.analisar = banco, armazenamento, analisar
        self.fila: queue.Queue[str] = queue.Queue()
        self.agendados: set[str] = set()
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None

    def agendar(self, lote_id: str) -> None:
        with self.lock:
            if lote_id in self.agendados:
                return
            self.agendados.add(lote_id)
            if self.thread is None:
                self.thread = threading.Thread(target=self._rodar, name="analise-fotos", daemon=True)
                self.thread.start()
        self.fila.put(lote_id)

    def esperar(self) -> None:
        self.fila.join()

    def _rodar(self) -> None:
        while True:
            lote_id = self.fila.get()
            try:
                analisar_lote(self.banco, self.armazenamento, self.analisar, lote_id)
            except Exception as e:  # noqa: BLE001 (o thread não pode morrer; a próxima listagem reagenda)
                print(f"[analise] AVISO: lote {lote_id}: {e}", flush=True)
            finally:
                with self.lock:
                    self.agendados.discard(lote_id)
                self.fila.task_done()


def analisar_lote(banco: Banco, armazenamento: Any, analisar: Callable[[bytes], dict], lote_id: str) -> int:
    """Analisa as fotos enviadas do lote que ainda não têm análise; devolve quantas analisou."""
    n = 0
    for f in banco.fotos(lote_id):
        if f["analise"] is not None or not f["enviada"]:
            continue
        impressao = None
        try:
            dados = armazenamento.ler(f["chave"], MAX_BYTES_FOTO)
            impressao = hashlib.sha256(dados).hexdigest()  # fica no banco depois que a foto é apagada
            a = analisar(dados)
        except Exception as e:  # noqa: BLE001 (foto ilegível ou armazenamento fora: o revisor vê o erro)
            a = {"versao": pessoas.VERSAO, "erro": str(e)[:200]}
        banco.salvar_analise(f["id"], a, impressao)
        n += 1
    return n


def analisar_continuamente(banco: Banco, armazenamento: Any, analisar: Callable[[bytes], dict], parar: threading.Event,
                           intervalo_s: float = 5, vivo: Path | None = None) -> None:  # fmt: skip
    """Serviço próprio da análise de pessoas (`render2go-servico analisar`, RENDER2GO_ANALISE=separada na
    API): com muitos envios, a análise (CPU) não disputa a máquina com as requisições da API.
    Sinal de vida: o arquivo `vivo` a cada volta (healthcheck do contêiner) e o banco a cada minuto
    (o painel mostra, em Segurança, há quanto tempo a análise não dá sinal)."""
    sinal = 0.0
    while not parar.is_set():
        try:
            if vivo is not None:
                vivo.touch()
            if time.time() - sinal >= 60:
                banco.definir_estado("analisador_vivo", None)
                sinal = time.time()
            lotes = banco.lotes_para_analisar()
            for lid in lotes:
                n = analisar_lote(banco, armazenamento, analisar, lid)
                print(f"[analise] lote {lid}: {n} foto(s) analisada(s)", flush=True)
        except Exception as e:  # noqa: BLE001 (o laço não pode morrer; tenta de novo no próximo intervalo)
            print(f"[analise] AVISO: {e}", flush=True)
            lotes = []
        if not lotes:
            parar.wait(intervalo_s)


@dataclass
class Config:
    banco: Banco
    armazenamento: Any  # Armazenamento ou um equivalente nos testes
    admin_tokens: list[str]
    worker_tokens: list[str]  # tokens fixos (só no servidor); os pods recebem um token próprio cada
    segredo_aprovacao: str
    limite_por_minuto: int = 120
    analisar: Callable[[bytes], dict] | None = None  # None: sem aviso de pessoas na API
    email: suporte.ConfigEmail | None = None  # None: suporte só no banco e login por e-mail fora
    enviar_email: Callable = suporte.enviar
    # contas (ADR 0007)
    segredo_contas: str | None = None
    turnstile: contas.ConfigTurnstile | None = None
    turnstile_desligado: bool = False  # só desenvolvimento e testes (RENDER2GO_TURNSTILE=desligado)
    cookie_seguro: bool = True  # False só em desenvolvimento local sem HTTPS (RENDER2GO_COOKIE_INSEGURO=1)
    # painel
    admin_caminho: str | None = None
    admin_totp: str | None = None
    admin_ips: list[str] = field(default_factory=list)
    # pagamentos (ADR 0008)
    mp: pagamentos.ConfigMP | None = None
    provedor_pagamento: Callable[[pagamentos.ConfigMP], Any] = pagamentos.MercadoPago
    analise: str = "api"  # "api" (thread na API), "separada" (serviço próprio) ou "desligada"
    retrato_s: float = RETRATO_S  # validade do retrato da fila e dos pods (0: sempre lê do banco)
    # foto do perfil: quem baixa do Gravatar (os testes trocam; nenhum teste fala com o Gravatar de verdade)
    baixar_gravatar: Callable[[str], Any] = perfil.baixar_gravatar
    _fotos: perfil.CacheFotos | None = None
    _janelas: dict = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _analises: AnaliseFotos | None = None
    _fundo: ThreadPoolExecutor | None = None
    _correio: ThreadPoolExecutor | None = None
    _pendentes_fundo: list = field(default_factory=list)
    _retratos: dict = field(default_factory=dict)

    def em_segundo_plano(self, fn: Callable, *args, correio: bool = False) -> None:
        """Trabalho fora da requisição. E-mails (códigos de entrada) num grupo próprio e maior: um
        pico de cadastros não atrasa o resto, e o resto não atrasa os códigos."""
        with self._lock:
            if correio and self._correio is None:
                n = max(2, int(os.environ.get("RENDER2GO_EMAIL_PARALELO", 8)))
                self._correio = ThreadPoolExecutor(max_workers=n, thread_name_prefix="correio")
            if not correio and self._fundo is None:
                self._fundo = ThreadPoolExecutor(max_workers=2, thread_name_prefix="fundo")
            executor = self._correio if correio else self._fundo
            self._pendentes_fundo = [f for f in self._pendentes_fundo if not f.done()]
            self._pendentes_fundo.append(executor.submit(fn, *args))

    def encerrar(self) -> None:
        """Fim do processo: os e-mails em andamento terminam (cada um tem tempo limite); o banco fecha."""
        for executor in (self._correio, self._fundo):
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=True)
        self.banco.fechar()

    def esperar_fundo(self) -> None:  # testes
        with self._lock:
            pendentes = list(self._pendentes_fundo)
        for f in pendentes:
            f.result()

    @property
    def analises(self) -> AnaliseFotos | None:
        if self.analisar is None or self.analise != "api":
            return None
        with self._lock:
            if self._analises is None:
                self._analises = AnaliseFotos(self.banco, self.armazenamento, self.analisar)
            return self._analises

    @property
    def analise_ligada(self) -> bool:
        return self.analise == "separada" or (self.analise == "api" and self.analisar is not None)

    @property
    def chave_codigos(self) -> bytes:
        return contas.chave_contas(self.segredo_contas, self.segredo_aprovacao)

    @property
    def chave_segredos(self) -> bytes:
        """Chave AES-GCM dos segredos do aplicativo autenticador guardados no banco."""
        return contas.chave_segredos(self.segredo_contas, self.segredo_aprovacao)

    @property
    def fotos(self) -> perfil.CacheFotos:
        with self._lock:
            if self._fotos is None:
                self._fotos = perfil.CacheFotos(self.baixar_gravatar)
            return self._fotos

    def retrato(self, chave: str, fn: Callable[[], Any]) -> Any:
        """O mesmo valor para todas as requisições de um intervalo curto (fila, pods, médias): com
        milhares de páginas atualizando, o banco faz a consulta uma vez por intervalo, não por página."""
        agora = time.monotonic()
        with self._lock:
            v = self._retratos.get(chave)
            if v and agora - v[0] < self.retrato_s:
                return v[1]
        valor = fn()
        with self._lock:
            self._retratos[chave] = (agora, valor)
        return valor

    @classmethod
    def do_ambiente(cls) -> Config:
        def lista(nome: str) -> list[str]:
            return [t for t in os.environ.get(nome, "").split(",") if len(t) >= 32]

        segredo = os.environ.get("RENDER2GO_SEGREDO_APROVACAO", "")
        if len(segredo) < 32:
            raise SystemExit("RENDER2GO_SEGREDO_APROVACAO ausente ou curto (mínimo 32 caracteres)")
        segredo_contas = os.environ.get("RENDER2GO_SEGREDO_CONTAS", "").strip() or None
        if segredo_contas and len(segredo_contas) < 32:
            raise SystemExit("RENDER2GO_SEGREDO_CONTAS curto (mínimo 32 caracteres)")
        totp = os.environ.get("RENDER2GO_ADMIN_TOTP", "").strip().replace(" ", "") or None
        if totp:
            try:
                contas.totp(totp, 0)
            except (ValueError, TypeError) as e:
                raise SystemExit("RENDER2GO_ADMIN_TOTP inválido (gere com: render2go-servico totp-novo)") from e
        armazenamento = Armazenamento.do_ambiente()  # RENDER2GO_R2_* (mesmas do lote_nuvem)
        if armazenamento is None:
            raise SystemExit("faltam RENDER2GO_R2_ENDPOINT, _BUCKET, _ACCESS_KEY_ID ou _SECRET_ACCESS_KEY")
        analise = os.environ.get("RENDER2GO_ANALISE", "api").strip().lower()
        if analise not in ("api", "separada", "desligada"):
            raise SystemExit("RENDER2GO_ANALISE: api, separada ou desligada")
        banco = Banco(os.environ["RENDER2GO_DB_URL"])
        banco.criar_esquema()
        return cls(
            banco=banco,
            armazenamento=armazenamento,
            admin_tokens=lista("RENDER2GO_ADMIN_TOKENS"),
            worker_tokens=lista("RENDER2GO_WORKER_TOKENS"),
            segredo_aprovacao=segredo,
            analisar=pessoas.analisador() if analise == "api" else None,
            email=suporte.ConfigEmail.do_ambiente(),
            segredo_contas=segredo_contas,
            turnstile=contas.ConfigTurnstile.do_ambiente(),
            turnstile_desligado=os.environ.get("RENDER2GO_TURNSTILE", "").strip().lower() == "desligado",
            cookie_seguro=os.environ.get("RENDER2GO_COOKIE_INSEGURO", "") != "1",
            admin_caminho=caminho_admin_do_ambiente(),
            admin_totp=totp,
            admin_ips=[r for r in os.environ.get("RENDER2GO_ADMIN_IPS", "").split(",") if r.strip()],
            mp=pagamentos.ConfigMP.do_ambiente(),
            analise=analise,
        )

    def limitar(self, chave: str) -> None:
        """Limite simples de requisições por minuto, por conta (em cada processo da API)."""
        agora = time.monotonic()
        with self._lock:
            janela = [t for t in self._janelas.get(chave, []) if agora - t < 60]
            if len(janela) >= self.limite_por_minuto:
                raise HTTPException(429, "muitas requisições; tente em um minuto")
            janela.append(agora)
            self._janelas[chave] = janela


def carimbo(segredo: str, lote_id: str, aprovado_em: float) -> str:
    """Carimbo de aprovação (HMAC) que o worker confere antes de renderizar (ADR 0004)."""
    return hmac.new(segredo.encode(), f"{lote_id}:{aprovado_em:.6f}".encode(), hashlib.sha256).hexdigest()


def _token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "token ausente")
    return authorization.removeprefix("Bearer ").strip()


def _um_de(token: str, validos: list[str]) -> bool:
    return any(contas.iguais(token, v) for v in validos)


def ip_de(request: Request) -> str:
    """IP de quem fez a requisição. A API só é alcançável pelo Caddy, que sempre sobrescreve o
    X-Render2Go-IP com o IP real do cliente ({client_ip}: com a Cloudflare na frente, o CF-Connecting-IP,
    aceito só quando a conexão vem dela). Sem ele, o último do X-Forwarded-For (Caddy antigo)."""
    ip = request.headers.get("x-render2go-ip", "").strip()
    if ip:
        return ip[:64]
    xff = request.headers.get("x-forwarded-for", "")
    ip = xff.split(",")[-1].strip() if xff else ""
    return (ip or (request.client.host if request.client else "desconhecido"))[:64]


def porta_de(request: Request) -> int | None:
    """Porta de origem da conexão (Marco Civil: com o IP e a hora, identifica quem usa IP compartilhado)."""
    p = request.headers.get("x-render2go-porta", "").strip()
    if p.isdigit() and 0 < int(p) < 65536:
        return int(p)
    return request.client.port if request.client and not request.headers.get("x-render2go-ip") else None


def origem_de(request: Request) -> dict:
    """De onde veio o pedido: IP (do Caddy), rede (IPv6 pelo /64), porta, dispositivo (identificador
    que a página guarda no navegador), User-Agent e fuso horário do navegador. Valores fora do formato
    viram None."""
    disp = request.headers.get("x-dispositivo", "").strip().lower()
    fuso = request.headers.get("x-fuso", "").strip()
    ip = ip_de(request)
    return {
        "ip": ip,
        "rede": contas.rede(ip),
        "porta": porta_de(request),
        "dispositivo": disp if DISPOSITIVO.match(disp) else None,
        "agente": request.headers.get("user-agent", "")[:300] or None,
        "fuso": fuso if FUSO.match(fuso) else None,
    }


def impressao_navegador(origem: dict, nav: NavegadorIn | None) -> str | None:
    """Resumo (SHA-256) dos sinais do navegador: acha o mesmo navegador quando o identificador muda
    (janela anônima, dados apagados). Aparelhos iguais podem ter a mesma impressão: é sinal, não prova."""
    if nav is None:
        return None
    partes = [
        origem.get("agente") or "",
        nav.idioma or "",
        ",".join(nav.idiomas),
        nav.tela or "",
        nav.plataforma or "",
        str(nav.nucleos),
        str(nav.memoria),
        str(nav.toque),
        origem.get("fuso") or "",
    ]
    return hashlib.sha256("|".join(partes).encode()).hexdigest()[:24]


def nome_cookie(c: Config, tipo: str) -> str:
    return COOKIES[(tipo, c.cookie_seguro)]


def definir_cookie(resp: Response, c: Config, tipo: str, token: str, validade_s: float) -> None:
    resp.set_cookie(
        nome_cookie(c, tipo),
        token,
        max_age=int(validade_s),
        path="/" if tipo == "cliente" else (c.admin_caminho or "/"),
        secure=c.cookie_seguro,
        httponly=True,  # o JavaScript da página não lê a sessão (um script injetado também não)
        samesite="lax" if tipo == "cliente" else "strict",
    )


def apagar_cookie(resp: Response, c: Config, tipo: str) -> None:
    resp.delete_cookie(
        nome_cookie(c, tipo),
        path="/" if tipo == "cliente" else (c.admin_caminho or "/"),
        secure=c.cookie_seguro,
        httponly=True,
        samesite="lax" if tipo == "cliente" else "strict",
    )


def _contar_falha(c: Config, origem: dict, motivo: str, janela_s: int = 600, maximo: int = 60) -> None:
    """Credencial errada (sessão, código, token): conta por rede; passou do limite, 429 por um tempo."""
    if c.banco.contar(f"falha:{motivo}:{origem['rede']}", janela_s) > maximo:
        raise HTTPException(429, "muitas tentativas recusadas desta rede; tente de novo mais tarde")


def _apagar_fotos(c: Config, lote_id: str) -> None:
    """Fotos de entrada de um lote fechado: apagadas do armazenamento (minimiza o que guardamos)."""
    falhas = 0
    for f in c.banco.fotos(lote_id):
        try:
            c.armazenamento.apagar(f["chave"])
        except Exception as e:  # noqa: BLE001 (a regra de ciclo de vida do bucket apaga depois)
            falhas += 1
            print(f"[fotos] AVISO: {f['chave']}: {e}", flush=True)
    if not falhas:
        c.banco.marcar_fotos_apagadas(lote_id)


# num pico de cadastros, o provedor recusa por alguns segundos o que passa do limite por segundo (no SES,
# "454 Throttling failure: Maximum sending rate exceeded"): o envio, já em segundo plano, tenta de novo
ESPERA_REENVIO_S = (1.0, 4.0)


def _falha_temporaria(e: Exception) -> bool:
    """Vale tentar de novo: recusa temporária do servidor (código 4xx), conexão que caiu, rede ou tempo
    esgotado. Destinatário recusado, senha errada e outros erros definitivos não (o SMTPException do
    Python é um OSError: por isso ele é conferido antes)."""
    if isinstance(e, smtplib.SMTPResponseException):
        return 400 <= e.smtp_code < 500
    if isinstance(e, smtplib.SMTPServerDisconnected):
        return True
    if isinstance(e, smtplib.SMTPException):
        return False
    return isinstance(e, OSError)


def _enviar_com_reenvio(c: Config, mensagem: Any) -> None:
    for espera in (*ESPERA_REENVIO_S, None):
        try:
            c.enviar_email(c.email, mensagem)
            return
        except Exception as e:
            if espera is None or not _falha_temporaria(e):
                raise
            time.sleep(espera)


def _enviar_suporte(c: Config, sid: str, dados: dict) -> None:
    try:
        _enviar_com_reenvio(c, suporte.montar(c.email, dados))
        c.banco.marcar_suporte(sid, True)
    except Exception as e:  # noqa: BLE001 (a mensagem fica no banco; o painel mostra o erro)
        c.banco.marcar_suporte(sid, False, str(e)[:300])
        print(f"[suporte] AVISO: e-mail {sid} não saiu: {e}", flush=True)


def _enviar_codigo(c: Config, para: str, codigo: str, motivo: str = "entrada") -> None:
    try:
        _enviar_com_reenvio(c, suporte.montar_codigo(c.email, para, codigo, contas.CODIGO_VALIDADE_S // 60, motivo))
    except Exception as e:  # noqa: BLE001 (a pessoa pede outro código; o erro fica no registro)
        print(f"[conta] AVISO: código para {para.split('@')[-1]} não saiu: {type(e).__name__}: {e}", flush=True)


def _enviar_aviso(c: Config, para: str, assunto: str, texto: str) -> None:
    try:
        _enviar_com_reenvio(c, suporte.montar_aviso(c.email, para, assunto, texto))
    except Exception as e:  # noqa: BLE001 (o aviso é um extra; o registro de acesso já guardou o evento)
        print(f"[conta] AVISO: aviso para {para.split('@')[-1]} não saiu: {type(e).__name__}: {e}", flush=True)


# avisos de segurança no e-mail da conta: (assunto, texto); só texto e sem links, como os códigos
AVISOS = {
    "conta_excluida": (
        "sua conta foi excluída",
        "A sua conta do Render2Go foi excluída, como você pediu. O e-mail saiu do serviço; ficam só os registros "
        "que a lei manda guardar, pelo prazo legal.",
    ),
    "conta_desativada": (
        "sua conta foi desativada",
        "A sua conta do Render2Go foi desativada, como você pediu. O saldo continua guardado. Para reativar, é só "
        "entrar de novo com o código do e-mail.",
    ),
    "mfa_ligado": (
        "verificação em duas etapas ligada",
        "A verificação em duas etapas foi ligada na sua conta. Daqui em diante, a entrada pede também o código do "
        "aplicativo autenticador.",
    ),
    "mfa_desligado": (
        "verificação em duas etapas desligada",
        "A verificação em duas etapas foi desligada na sua conta. A entrada volta a pedir só o código do e-mail.",
    ),
    "mfa_suporte": (
        "verificação em duas etapas desligada pelo suporte",
        "O suporte do Render2Go desligou a verificação em duas etapas da sua conta, a seu pedido. A entrada volta a "
        "pedir só o código do e-mail; ligue de novo em Configurações quando quiser.",
    ),
    "codigos_novos": (
        "códigos de recuperação novos",
        "Foram gerados códigos de recuperação novos para a sua conta. Os anteriores deixaram de valer.",
    ),
    "recuperacao_usada": (
        "um código de recuperação foi usado",
        "Um código de recuperação da verificação em duas etapas foi usado na sua conta. Restam {restantes}. Se você "
        "está sem o celular, entre e gere códigos novos em Configurações.",
    ),
    "email_trocado": (
        "o e-mail da sua conta mudou",
        "O e-mail da sua conta do Render2Go foi trocado para {novo}. Este endereço não recebe mais os códigos de "
        "entrada.",
    ),
}


def _avisar(c: Config, para: str | None, aviso: str, **valores: Any) -> None:
    """Aviso de segurança (`AVISOS`) no e-mail da conta, em segundo plano (sem SMTP ou sem e-mail, nada sai)."""
    if c.email is not None and para:
        assunto, texto = AVISOS[aviso]
        c.em_segundo_plano(_enviar_aviso, c, para, assunto, texto.format(**valores), correio=True)


def _se_fechou(c: Config, lote_id: str) -> None:
    lt = c.banco.lote(lote_id)
    if lt and lt["status"] in LOTE_FECHADO and not lt["fotos_apagadas"]:
        c.em_segundo_plano(_apagar_fotos, c, lote_id)


def _limites_teste(c: Config, cli: dict, origem: dict, n_fotos: int, exceto: str | None = None) -> None:
    """Teste gratuito: um pedido só (todas as fotos de uma vez) e até 5 fotos por dispositivo. O teto por
    rede (RENDER2GO_TESTE_FOTOS_IP) só vale se configurado: desde 11/10/2026, o padrão é sem teto."""
    if c.banco.pedidos_ativos(cli["id"], exceto) > 0:
        raise HTTPException(
            429,
            f"o teste gratuito é um pedido só, com até {TESTE['fotos']} fotos; para mais vídeos, adicione saldo",
        )
    disp = origem.get("dispositivo")
    if disp and c.banco.fotos_teste_dispositivo(disp, exceto) + n_fotos > TESTE["fotos_dispositivo"]:
        raise HTTPException(429, f"este dispositivo já usou o teste gratuito ({TESTE['fotos_dispositivo']} fotos)")
    teto_rede = TESTE["fotos_ip"]
    if teto_rede > 0 and c.banco.fotos_teste_ip(origem["ip"], exceto, rede=origem.get("rede")) + n_fotos > teto_rede:
        raise HTTPException(429, "esta rede já usou todos os testes gratuitos disponíveis; fale com a gente")


def _anti_robo(c: Config, resposta: str | None, origem: dict, acao: str) -> None:
    """Confere o "não sou um robô" (Turnstile). Sem configuração, recusa (fail-closed), salvo quando
    desligado de propósito (RENDER2GO_TURNSTILE=desligado, só desenvolvimento)."""
    if c.turnstile_desligado:
        return
    if c.turnstile is None:
        raise HTTPException(503, "verificação anti-robô não configurada; tente mais tarde")
    motivo = contas.turnstile_confere(c.turnstile, resposta or "", origem.get("ip"), acao)
    if motivo:
        _contar_falha(c, origem, "robo", 3600, 30)
        raise HTTPException(403, f"não foi possível confirmar que você não é um robô ({motivo}); tente de novo")


def _celula_csv(v: str) -> str:
    """Célula de CSV: aspas quando preciso e, contra fórmulas (=, +, -, @) abertas no Excel, um apóstrofo."""
    v = str(v)
    if v[:1] in ("=", "+", "-", "@") and not re.fullmatch(r"-?[0-9]+(,[0-9]+)?", v):
        v = "'" + v
    if any(ch in v for ch in (";", '"', "\n", "\r")):
        v = '"' + v.replace('"', '""') + '"'
    return v


# Recusa do filtro de palavras (C1) para o cliente: diz as regras, nunca qual palavra ou categoria barrou
# (11/10/2026: a categoria ajudava a testar sinônimos até passar). A categoria fica no registro de acesso
# e no painel do revisor.
RECUSA_FILTRO = (
    "o texto não passou no filtro de conteúdo: ele tem palavras que não aceitamos (conteúdo adulto, crianças, "
    "violência, armas, drogas ou pessoas reais; item 5 dos termos). Reescreva descrevendo só o produto, o "
    "ambiente e o movimento da câmera."
)


def _textos_por_foto(body: Any, receita: dict) -> list[str | None]:
    """Pedido em sequência: o texto próprio de cada foto (None = o texto padrão do pedido), conferido como o
    texto padrão (filtro de conteúdo C1 e estilos). Texto igual ao padrão não conta como próprio."""
    padrao = " ".join(body.prompt.split())
    textos: list[str | None] = []
    for f in body.fotos:
        t = " ".join((f.prompt or "").split())
        if not t or t == padrao:
            textos.append(None)
            continue
        if len(t) < 3:
            raise HTTPException(422, f"{f.nome}: o texto próprio da foto precisa de pelo menos 3 letras")
        d = filtros.avaliar(t)
        if not d.allowed:  # C1 também em cada texto próprio (ADR 0004)
            raise HTTPException(422, f"{f.nome}: {RECUSA_FILTRO}")
        try:
            presets.compose("i2v", t, receita["presets"])
        except presets.PresetError as e:
            raise HTTPException(422, f"{f.nome}: {e}") from e
        textos.append(t)
    if len({t for t in textos if t}) > MAX_TEXTOS_FOTO:
        raise HTTPException(
            422, f"no máximo {MAX_TEXTOS_FOTO} textos diferentes por pedido (fotos com o mesmo texto contam uma vez)"
        )
    return textos


def _fotos_do_envio(cliente_id: str, fotos_in: list, textos: list[str | None], usados: set[str]) -> list[dict]:
    """Fotos de um envio, conferidas (tipo e tamanho) e com nome único no pedido (`usados`: os nomes que o
    pedido já tem; é atualizado). A chave no armazenamento é a pasta do pedido ({lote} trocado no banco)."""
    fotos = []
    for f, texto in zip(fotos_in, textos, strict=True):
        if f.tipo not in TIPOS:
            raise HTTPException(422, f"{f.nome}: use JPG, PNG ou WebP")
        if f.bytes > MAX_BYTES_FOTO:
            raise HTTPException(422, f"{f.nome}: acima de {MAX_BYTES_FOTO // 1024**2} MB")
        base = NOME.sub("-", Path(f.nome).stem).strip("-")[:80] or "foto"
        nome, n = base, 2
        while nome in usados:
            nome, n = f"{base}-{n}", n + 1
        usados.add(nome)
        arq = f"{nome}.{TIPOS[f.tipo]}"
        fotos.append(
            {
                "nome": nome,
                "tipo": f.tipo,
                "bytes": f.bytes,
                "chave": chave_entrada(cliente_id, "{lote}", arq),
                "original": f.nome,
                "prompt": texto,
            }
        )
    return fotos


def duracoes(plano: str) -> list[int]:
    """Durações aceitas: 3 s só no teste gratuito; nos outros planos, de 4 a 7 s."""
    if plano == "teste":
        return [TESTE["duracao"]]
    return [d for d in lote.DURACOES if d >= precos.DURACAO_MIN]


def sem_saldo_video(preco: int, saldo: int) -> str:
    return (
        f"saldo insuficiente: cada vídeo deste pedido custa {precos.reais(preco)} e o seu saldo livre é "
        f"{precos.reais(max(0, saldo))}. Adicione saldo por Pix no seu painel"
    )


def sem_saldo(custo: int, saldo: int) -> str:
    return (
        f"saldo insuficiente: este pedido custa {precos.reais(custo)} e o seu saldo é {precos.reais(saldo)}. "
        "Adicione saldo por Pix no seu painel"
    )


class FotoIn(BaseModel):
    nome: str = Field(min_length=1, max_length=200)
    tipo: str = Field(max_length=40)
    bytes: int = Field(gt=0)
    # pedido em sequência: o texto só desta foto (vazio = o texto padrão do pedido)
    prompt: str | None = Field(None, max_length=600)


class NavegadorIn(BaseModel):
    """Sinais do navegador enviados com o pedido (registro de auditoria e impressão do navegador)."""

    idioma: str | None = Field(None, max_length=35)
    idiomas: list[str] = Field(default_factory=list, max_length=10)
    tela: str | None = Field(None, max_length=40)
    plataforma: str | None = Field(None, max_length=60)
    nucleos: int | None = Field(None, ge=0, le=1024)
    memoria: float | None = Field(None, ge=0, le=4096)
    toque: int | None = Field(None, ge=0, le=64)
    marcado_em: str | None = Field(None, max_length=40)  # quando a caixa de autorização foi marcada

    def model_post_init(self, _: Any) -> None:
        self.idiomas = [i[:35] for i in self.idiomas]


class CookiesIn(BaseModel):
    versao: str = Field(min_length=1, max_length=20)
    estatisticas: bool = False
    marketing: bool = False


class SuporteIn(BaseModel):
    assunto: str = Field("", max_length=120)
    mensagem: str = Field(min_length=10, max_length=2000)
    email: str | None = Field(None, max_length=200)
    turnstile: str | None = Field(None, max_length=2048)


class FotosIn(BaseModel):
    """Mais fotos num pedido grande, em partes (12/10/2026)."""

    fotos: list[FotoIn] = Field(min_length=1, max_length=MAX_FOTOS_LOTE)


class LoteIn(BaseModel):
    prompt: str = Field(min_length=3, max_length=600)
    receita: str = Field("produto-close", max_length=60)
    formato: str = Field("auto", max_length=30)
    qualidade: str = Field("rapida", max_length=30)
    duracao: int | None = None
    variacoes: int = Field(1, ge=1, le=lote.MAX_VARIACOES)
    consentimento: bool = False
    declarante: str | None = Field(None, max_length=120)  # opcional; a página não pede mais
    modelo: str = Field(modelos.PADRAO, max_length=60)
    navegador: NavegadorIn | None = None
    fotos: list[FotoIn] = Field(max_length=MAX_FOTOS_LOTE)


class PromptIn(BaseModel):
    worker: str = Field(max_length=100)
    prompt_en: str = Field(min_length=3, max_length=2000)
    compilador: str = Field(max_length=200)


class ConcluirIn(BaseModel):
    worker: str = Field(max_length=100)
    status: str = Field(max_length=20)
    segundos: float | None = Field(None, ge=0, le=86400)
    aviso: str | None = Field(None, max_length=1000)
    erro: str | None = Field(None, max_length=1000)
    repetir: bool = False
    similaridade: float | None = None  # 1º quadro × foto (render2go_worker.conferencia)
    bytes_video: int | None = Field(None, ge=0)
    deriva_cor: float | None = None  # continuidade até o fim do vídeo (render2go_worker.conferencia)
    salto_cor: float | None = None
    # filtro de imagem nos quadros (ADR 0005): maior P(medium)+P(high) e quantos quadros passaram
    nsfw: float | None = Field(None, ge=0, le=1)
    nsfw_quadros: int | None = Field(None, ge=0, le=100000)
    barrado: bool = False  # o worker barrou o vídeo (não enviou); não é refeito


class InteresseIn(BaseModel):
    finalidade: str = Field(min_length=2, max_length=40)


class RecusaIn(BaseModel):
    motivo: str = Field(min_length=3, max_length=500)


class ReivindicarIn(BaseModel):
    worker: str = Field(min_length=3, max_length=100)
    # o pod que ainda baixa os modelos pede só "compilar" (traduz o pedido enquanto o download termina)
    tipos: list[Literal["compilar", "render"]] | None = Field(None, min_length=1, max_length=2)
    # modelos de geração que este pod roda: só recebe pedidos deles
    modelos: list[str] | None = Field(None, min_length=1, max_length=10)


class ClienteIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    plano: str = Field("pago", max_length=20)
    cota_fotos_dia: int = Field(500, ge=1, le=100_000)


class SaldoIn(BaseModel):
    valor: str = Field(min_length=1, max_length=20)  # reais: "50", "19,90"; negativo desconta (ajuste)
    descricao: str = Field(min_length=3, max_length=200)  # ex.: "Pix de 09/10, comprovante 123"
    referencia: str | None = Field(None, max_length=120)  # id do pagamento: o mesmo nunca entra duas vezes


class PlanoIn(BaseModel):
    plano: str = Field(max_length=20)


class EmailIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class CodigoIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    aceite: bool = False  # termos de uso e política de privacidade
    turnstile: str | None = Field(None, max_length=2048)


class EntrarIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    codigo: str = Field(min_length=1, max_length=12)
    aceite: bool = False


class EntrarCodigoIn(BaseModel):
    codigo: str = Field(min_length=20, max_length=200)
    aceite: bool = False


class ExcluirIn(BaseModel):
    confirmacao: str = Field(max_length=20)


class EntrarMfaIn(BaseModel):
    desafio: str = Field(min_length=20, max_length=200)  # devolvido pela entrada (e-mail ou código de acesso)
    codigo: str = Field(min_length=1, max_length=40)  # do aplicativo autenticador ou de recuperação


class ConfirmarIn(BaseModel):
    codigo: str = Field(min_length=1, max_length=40)  # do e-mail, do aplicativo ou de recuperação


class CodigoAppIn(BaseModel):
    codigo: str = Field(min_length=1, max_length=12)


class TrocarEmailIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    codigo: str | None = Field(None, max_length=12)  # o código que chegou no endereço novo (2º passo)


class PerfilIn(BaseModel):
    """Só os campos enviados mudam; vazio apaga o nome."""

    primeiro_nome: str | None = Field(None, max_length=120)
    ultimo_nome: str | None = Field(None, max_length=120)
    gravatar: bool | None = None


class PreferenciasIn(BaseModel):
    tema: Literal["claro", "escuro", "auto"]


class AceitarIn(BaseModel):
    """As versões que a página mostrou (se mudaram de novo no meio, a página recarrega)."""

    documentos: dict[str, str] = Field(max_length=5)


class AdminEntrarIn(BaseModel):
    """Pessoa: `usuario` + `senha` + `totp`. Emergência: `token` (a chave do servidor) + `totp`."""

    token: str | None = Field(None, min_length=1, max_length=300)
    usuario: str | None = Field(None, max_length=64)
    senha: str | None = Field(None, max_length=200)
    totp: str | None = Field(None, max_length=12)


class ConviteIn(BaseModel):
    """Primeiro acesso por convite: o token do endereço; depois o código do aplicativo; por fim a senha."""

    convite: str = Field(min_length=20, max_length=200)
    codigo: str | None = Field(None, max_length=12)
    senha: str | None = Field(None, max_length=200)


class NovoConviteIn(BaseModel):
    usuario: str = Field(min_length=3, max_length=64)
    papel: Literal["dono", "operador"]


class PapelIn(BaseModel):
    papel: Literal["dono", "operador"]


class PixIn(BaseModel):
    valor: str = Field(min_length=1, max_length=12)  # reais: "50" ou "19,90"
    documento: str = Field(min_length=11, max_length=20)  # CPF ou CNPJ de quem paga (o Mercado Pago exige)


def _resumo_pagamento(p: dict) -> dict:
    pendente = p["status"] in ("criando", "pendente") and (p["expira"] or 0) > time.time()
    return {
        "id": p["id"],
        "valor": p["valor"],
        "status": p["status"] if pendente or p["status"] not in ("criando", "pendente") else "expirado",
        "criado": p["criado"],
        "expira": p["expira"],
        "creditado_em": p["creditado_em"],
        # o QR e o copia e cola só enquanto o Pix pode ser pago
        "copia_cola": p.get("copia_cola") if pendente else None,
        "qr_base64": p.get("qr_base64") if pendente else None,
    }


def avisos_de_configuracao(c: Config) -> list[str]:
    """O que falta configurar: sai no log da API ao subir (e o painel mostra em Segurança)."""
    avisos = []
    if c.admin_caminho is None:
        avisos.append("painel desligado: falta RENDER2GO_ADMIN_CAMINHO (ninguém revisa os pedidos)")
    elif not c.admin_tokens and not c.banco.contar_donos():
        avisos.append(
            "ninguém consegue entrar no painel: crie o 1º acesso com"
            " `render2go-servico admin-convite --usuario <nome> --papel dono` (ou a chave RENDER2GO_ADMIN_TOKENS)"
        )
    elif c.admin_tokens and c.admin_totp is None:
        avisos.append("chave do painel SEM o código do autenticador: configure RENDER2GO_ADMIN_TOTP (totp-novo)")
    if c.email is None:
        avisos.append("SMTP não configurado: os códigos de entrada não saem e ninguém entra pelo e-mail")
    if c.turnstile is None:
        avisos.append(
            "Turnstile DESLIGADO de propósito (RENDER2GO_TURNSTILE=desligado): só para desenvolvimento"
            if c.turnstile_desligado
            else "Turnstile não configurado: a entrada por e-mail e o suporte sem conta recusam os pedidos"
        )
    if c.mp is None:
        avisos.append("recarga por Pix desligada (RENDER2GO_MP_*): o saldo só entra pelo painel")
    if not c.cookie_seguro:
        avisos.append("cookies sem Secure (RENDER2GO_COOKIE_INSEGURO=1): só para desenvolvimento local")
    if vazios := [v for v in EMPRESA.values() if not os.environ.get(v, "").strip()]:
        avisos.append(f"identificação da empresa incompleta (páginas legais e rodapé): {', '.join(vazios)}")
    return avisos


def criar_app(cfg: Config | None = None) -> FastAPI:
    @contextlib.asynccontextmanager
    async def ciclo(app_: FastAPI):
        criada_aqui = app_.state.cfg is None
        if criada_aqui:
            # na subida, não no 1º pedido: variável faltando derruba a API com a mensagem no log
            app_.state.cfg = Config.do_ambiente()
            for aviso in avisos_de_configuracao(app_.state.cfg):
                print(f"[api] AVISO: {aviso}", flush=True)
        yield
        if criada_aqui:
            app_.state.cfg.encerrar()

    # sem /docs, /redoc e /openapi.json: o mapa da API não fica público
    app = FastAPI(title="Render2Go", docs_url=None, redoc_url=None, openapi_url=None, lifespan=ciclo)
    app.state.cfg = cfg
    painel = cfg.admin_caminho if cfg is not None else caminho_admin_do_ambiente()

    def config() -> Config:
        if app.state.cfg is None:
            app.state.cfg = Config.do_ambiente()
        return app.state.cfg

    def _sessao_cliente(request: Request, c: Config) -> dict | None:
        tok = request.cookies.get(nome_cookie(c, "cliente"))
        if not tok or len(tok) > 200:
            return None
        s = c.banco.sessao(tok, "cliente")
        if s is None:
            _contar_falha(c, origem_de(request), "sessao")
            return None
        if request.method not in ("GET", "HEAD") and request.headers.get(CABECALHO_PAGINA) != "1":
            raise HTTPException(403, "pedido sem o cabeçalho da página")
        cli = s["cliente"]
        # a sessão do navegador: o que as configurações da conta precisam (confirmação recente, aparelho atual)
        cli["_sessao"], cli["_sessao_id"] = tok, s["id"]
        cli["_sessao_criada"], cli["_confirmada_ate"] = float(s["criado"]), s.get("confirmada_ate")
        return cli

    def cliente(request: Request, authorization: str | None = Header(None), c: Config = Depends(config)) -> dict:
        cli = _sessao_cliente(request, c)
        if cli is None and authorization:  # código de acesso antigo (integrações e clientes de antes)
            tok = _token(authorization)
            cli = c.banco.cliente_por_token(tok) if len(tok) <= 200 else None
            if cli is None:
                _contar_falha(c, origem_de(request), "token")
                raise HTTPException(401, "token inválido")
        if cli is None:
            cab = None
            if request.cookies.get(nome_cookie(c, "cliente")):  # sessão vencida ou revogada: o cookie sai
                seguro = "; Secure" if c.cookie_seguro else ""
                cab = {"Set-Cookie": f"{nome_cookie(c, 'cliente')}=; Max-Age=0; Path=/{seguro}; HttpOnly; SameSite=Lax"}
            raise HTTPException(401, "entre na sua conta para continuar", headers=cab)
        c.limitar(cli["id"])
        return cli

    def cliente_sessao(request: Request, authorization: str | None = Header(None), c: Config = Depends(config)) -> dict:
        """Só a sessão do navegador: as configurações da conta (e-mail, verificação em duas etapas, aparelhos,
        desativar e excluir) não aceitam o código de acesso antigo no cabeçalho."""
        cli = cliente(request, authorization, c)
        if "_sessao" not in cli:
            raise HTTPException(403, "use a página da sua conta para isto (o código de acesso não vale aqui)")
        return cli

    def _confirmada_s(cli: dict) -> int:
        """Segundos que faltam da confirmação recente desta sessão (a sessão nova já conta como confirmada)."""
        if "_sessao" not in cli:
            return 0
        ate = max(float(cli.get("_confirmada_ate") or 0), cli["_sessao_criada"] + contas.CONFIRMACAO_S)
        return max(0, int(ate - time.time()))

    def _exigir_confirmacao(cli: dict) -> None:
        if _confirmada_s(cli) <= 0:
            raise HTTPException(403, "confirme que é você para continuar", headers={"X-Render2Go-Motivo": "confirmar"})

    def _exigir_aceite(cli: dict) -> None:
        """Termos ou privacidade em versão nova: quem está na página aceita antes de pedir ou pagar (o código de
        acesso das integrações segue valendo: o aceite delas é combinado com o dono do serviço)."""
        if "_sessao" in cli and contas.documentos_pendentes(cli):
            raise HTTPException(
                428,
                "os Termos de Uso e a Política de Privacidade mudaram: leia e aceite a versão nova para continuar",
                headers={"X-Render2Go-Motivo": "aceite"},
            )

    def cliente_opcional(
        request: Request, authorization: str | None = Header(None), c: Config = Depends(config)
    ) -> dict | None:
        """Cliente conectado, ou None sem conta (página /suporte). Sessão vencida no cookie vira "sem
        conta"; um código de acesso errado no cabeçalho continua sendo recusado (401)."""
        if authorization:
            return cliente(request, authorization, c)
        if not request.cookies.get(nome_cookie(c, "cliente")):
            return None
        try:
            return cliente(request, None, c)
        except HTTPException as e:
            if e.status_code == 401:
                return None
            raise

    def worker_auth(request: Request, authorization: str | None = Header(None), c: Config = Depends(config)) -> str:
        """Token fixo (do servidor: "*") ou o token do pod (devolve o nome do worker dono dele)."""
        tok = _token(authorization)
        if _um_de(tok, c.worker_tokens):
            return "*"
        nome = c.banco.worker_do_token(tok) if len(tok) <= 200 else None
        if nome is None:
            _contar_falha(c, origem_de(request), "worker")
            raise HTTPException(401, "token inválido")
        return nome

    def _mesmo_worker(dono: str, worker: str) -> None:
        if dono != "*" and not contas.iguais(dono, worker):
            raise HTTPException(403, "este token é de outro worker")

    # -- páginas (servico/web/publico; docs/10) ---------------------------------------------

    web = carregar_publico()

    def rota_pagina(arquivo: str):
        def rota(request: Request):
            return servir(request, web[arquivo], CACHE_REVALIDAR)

        return rota

    for caminho, arquivo in PAGINAS.items():
        if arquivo in web:
            app.add_api_route(caminho, rota_pagina(arquivo), methods=["GET"], include_in_schema=False)

    @app.get("/estatico/{nome}", include_in_schema=False)
    def estatico(nome: str, request: Request, v: str | None = None):
        arq = web.get(f"estatico/{nome}")
        if arq is None:
            raise HTTPException(404, "arquivo não encontrado")
        return servir(request, arq, CACHE_ANO if v == arq.versao else CACHE_REVALIDAR)

    def arquivo_raiz(nome: str):
        def rota(request: Request, v: str | None = None):
            arq = web[nome]
            return servir(request, arq, CACHE_ANO if v == arq.versao else CACHE_DIA)

        return rota

    for nome in ("favicon.ico", "sitemap.xml", "manifest.webmanifest"):
        app.add_api_route(f"/{nome}", arquivo_raiz(nome), methods=["GET"], include_in_schema=False)

    @app.get("/app.js", include_in_schema=False)
    def js_antigo():
        return Response(RECARREGAR_JS, media_type=TIPOS_WEB[".js"], headers={"Cache-Control": CACHE_REVALIDAR})

    @app.get("/estilo.css", include_in_schema=False)
    def css_antigo(request: Request):
        return servir(request, web["estatico/estilo.css"], CACHE_REVALIDAR)

    @app.exception_handler(StarletteHTTPException)
    async def erro_http(request: Request, exc: StarletteHTTPException):
        """Endereço inexistente aberto no navegador: página 404 com caminhos de volta (a API segue em JSON)."""
        if (
            exc.status_code == 404
            and request.method == "GET"
            and "/api/" not in request.url.path
            and "text/html" in request.headers.get("accept", "")
        ):
            return servir(request, web["404.html"], CACHE_REVALIDAR, status=404)
        return await http_exception_handler(request, exc)

    @app.get("/saude")
    def saude():
        return {"ok": True}

    # -- conta (ADR 0007) -------------------------------------------------------------------

    @app.get("/api/publico")
    def publico(c: Config = Depends(config)):
        """O que a página de entrada precisa antes de alguém entrar."""
        return {
            "turnstile": c.turnstile.site if c.turnstile else None,
            "entrada_email": c.email is not None,
            "dominios": sorted(contas.dominios_aceitos()),
            "documentos": contas.DOCUMENTOS,
            "pagamento": c.mp is not None,
        }

    @app.post("/api/conta/codigo")
    def pedir_codigo(body: CodigoIn, request: Request, c: Config = Depends(config)):
        """Manda o código de entrada. A resposta é a mesma com ou sem conta (não revela quem é cliente)."""
        origem = origem_de(request)
        if not body.aceite:
            raise HTTPException(422, "para continuar, aceite os Termos de Uso e a Política de Privacidade")
        email, norm = _email_valido(body.email)
        if c.email is None:
            raise HTTPException(503, "a entrada por e-mail está indisponível agora; tente de novo mais tarde")
        _anti_robo(c, body.turnstile, origem, "codigo")
        limites = [
            (f"codigo:rede:{origem['rede']}", 3600, 10),
            (f"codigo:rede:{origem['rede']}", 86400, 30),
            (f"codigo:email:{norm}", 3600, 5),
            (f"codigo:email:{norm}", 86400, 10),
        ]
        if origem["dispositivo"]:
            limites.append((f"codigo:disp:{origem['dispositivo']}", 3600, 10))
        for chave, janela, maximo in limites:
            if c.banco.contar(chave, janela) > maximo:
                espera = "uma hora" if janela <= 3600 else "amanhã"
                raise HTTPException(429, f"muitos códigos pedidos; tente de novo em {espera}")
        # só depois do anti-robô e dos limites: a resposta para um e-mail de empresa diz se há conta
        _dominio_aceito(c, body.email, norm)
        conta = c.banco.conta_por_email(norm)
        if conta is None or conta["ativo"]:  # conta desativada: responde igual, mas não manda
            codigo = contas.novo_codigo()
            resumo = contas.resumo_codigo(c.chave_codigos, norm, codigo)
            c.banco.criar_codigo(norm, email, resumo, contas.CODIGO_VALIDADE_S, origem)
            c.em_segundo_plano(_enviar_codigo, c, email, codigo, correio=True)
        return {"ok": True, "email": email, "validade_s": contas.CODIGO_VALIDADE_S}

    def _email_valido(texto: str) -> tuple[str, str]:
        """Só o formato: a lista de provedores é conferida depois (`_dominio_aceito`)."""
        try:
            return contas.normalizar(texto, exigir_dominio=False)
        except contas.EmailRecusado as e:
            raise HTTPException(422, str(e)) from e

    def _dominio_aceito(c: Config, texto: str, norm: str) -> None:
        """E-mail de um provedor da lista; fora dela, só o de uma conta que o painel criou."""
        try:
            contas.normalizar(texto)
        except contas.DominioRecusado as e:
            if not c.banco.conta_por_email(norm):
                raise HTTPException(422, str(e)) from e

    def _abrir_sessao(c: Config, conta: dict, origem: dict, evento: str, detalhe: dict) -> JSONResponse:
        """Fim da entrada (e-mail, código de acesso e, com a verificação em duas etapas, o aplicativo): a conta
        pausada pelo cliente volta a valer, o aceite dos documentos é gravado e a sessão vai para o cookie."""
        if conta.get("pausada_em") and c.banco.reativar_pausada(conta["id"]):
            c.banco.registrar_acesso(conta["id"], "conta_reativada", origem)
            detalhe = {**detalhe, "reativada": True}
        c.banco.registrar_aceite(conta["id"], contas.DOCUMENTOS, origem)
        token = c.banco.criar_sessao("cliente", contas.SESSAO_CLIENTE_S, origem, cliente_id=conta["id"])
        c.banco.registrar_acesso(conta["id"], evento, origem, detalhe)
        atual = c.banco.conta(conta["id"]) or conta
        resp = JSONResponse({"ok": True, **detalhe, "conta": _resumo_conta(c, atual, sessao_nova=True)})
        definir_cookie(resp, c, "cliente", token, contas.SESSAO_CLIENTE_S)
        return resp

    def _entrar(c: Config, conta: dict, origem: dict, detalhe: dict) -> JSONResponse:
        """Com a verificação em duas etapas ligada, a sessão só abre depois do código do aplicativo (ou de
        recuperação): aqui sai só o desafio, que vale 5 minutos e nunca é uma sessão de uso."""
        if conta.get("mfa_segredo"):
            desafio = c.banco.criar_sessao(
                "mfa", contas.MFA_DESAFIO_S, origem, cliente_id=conta["id"], contexto={"detalhe": detalhe}
            )
            return JSONResponse({"ok": True, "mfa": True, "desafio": desafio, "validade_s": contas.MFA_DESAFIO_S})
        return _abrir_sessao(c, conta, origem, "entrada", detalhe)

    def _limite_erros(c: Config, chave: str, mensagem: str) -> None:
        """Erros de um código (do aplicativo, de recuperação, de confirmação) por conta: 5 em 15 min, 20 por dia."""
        if c.banco.contar(chave, 900, somar=False) >= 5 or c.banco.contar(chave, 86400, somar=False) >= 20:
            raise HTTPException(429, mensagem)

    def _errou(c: Config, chave: str) -> None:
        c.banco.contar(chave, 900)
        c.banco.contar(chave, 86400)

    def _segundo_fator(c: Config, conta: dict, codigo: str, origem: dict) -> str | None:
        """Confere o código do aplicativo autenticador (uma vez só) ou um de recuperação (gasta o código e avisa
        no e-mail). Devolve o meio usado ou None. Segredo que não abre (chave trocada): recusa (fail-closed)."""
        try:
            segredo = contas.decifrar(c.chave_segredos, conta.get("mfa_segredo"), f"cliente:{conta['id']}")
        except ValueError as e:
            print(f"[conta] ALERTA: segredo do autenticador de {conta['id']} não abre: {e}", flush=True)
            raise HTTPException(
                503, "não foi possível conferir a verificação em duas etapas agora; fale com o suporte"
            ) from e
        digitos = re.sub(r"[\s-]", "", codigo or "")
        if re.fullmatch(r"[0-9]{6}", digitos):
            contador = contas.totp_confere(segredo, digitos)
            return "autenticador" if contador is not None and c.banco.mfa_usar_contador(conta["id"], contador) else None
        rec = contas.limpar_recuperacao(codigo)
        if rec is None:
            return None
        restantes = c.banco.usar_codigo_recuperacao(
            conta["id"], contas.resumo_recuperacao(c.chave_codigos, conta["id"], rec)
        )
        if restantes is None:
            return None
        c.banco.registrar_acesso(conta["id"], "codigo_recuperacao_usado", origem, {"restantes": restantes})
        _avisar(c, conta.get("email"), "recuperacao_usada", restantes=restantes)
        return "recuperacao"

    @app.post("/api/conta/entrar")
    def entrar(body: EntrarIn, request: Request, c: Config = Depends(config)):
        origem = origem_de(request)
        if not body.aceite:
            raise HTTPException(422, "para continuar, aceite os Termos de Uso e a Política de Privacidade")
        # sem conferir a lista de provedores aqui: só existe código para e-mail aceito no envio (e sem
        # essa conferência, esta rota não serve para descobrir se um e-mail de empresa tem conta)
        email, norm = _email_valido(body.email)
        tentativas = [
            (f"entrar:rede:{origem['rede']}", 3600, 30),
            (f"entrar:email:{norm}", 3600, 10),
            (f"entrar:email:{norm}", 86400, 20),
        ]
        if any(c.banco.contar(chave, janela) > maximo for chave, janela, maximo in tentativas):
            raise HTTPException(429, "muitas tentativas; peça um código novo mais tarde")
        codigo = contas.limpar_codigo(body.codigo)
        if not contas.CODIGO.match(codigo) or not c.banco.conferir_codigo(
            norm, contas.resumo_codigo(c.chave_codigos, norm, codigo), contas.CODIGO_TENTATIVAS
        ):
            raise HTTPException(401, "código inválido ou vencido; confira o código ou peça outro")
        conta, nova = c.banco.conta_do_email(
            email, norm, plano="teste", resumo=contas.resumo_email(c.chave_codigos, norm)
        )
        if not conta["ativo"]:
            raise HTTPException(403, "esta conta está desativada; fale com o suporte")
        return _entrar(c, conta, origem, {"nova": nova})

    @app.post("/api/conta/entrar-codigo")
    def entrar_codigo(body: EntrarCodigoIn, request: Request, c: Config = Depends(config)):
        """Quem já tinha um código de acesso entra com ele (a sessão é a mesma do e-mail)."""
        origem = origem_de(request)
        if not body.aceite:
            raise HTTPException(422, "para continuar, aceite os Termos de Uso e a Política de Privacidade")
        if c.banco.contar(f"falha:codigo-acesso:{origem['rede']}", 600, somar=False) > 20:
            raise HTTPException(429, "muitas tentativas recusadas desta rede; tente de novo mais tarde")
        conta = c.banco.cliente_por_token(body.codigo.strip(), pausadas=True)
        if conta is None:
            _contar_falha(c, origem, "codigo-acesso", 600, 20)
            raise HTTPException(401, "código de acesso inválido ou desativado")
        return _entrar(c, conta, origem, {"nova": False, "codigo_acesso": True})

    @app.post("/api/conta/entrar-mfa")
    def entrar_mfa(body: EntrarMfaIn, request: Request, c: Config = Depends(config)):
        """2º passo da entrada com a verificação em duas etapas: o código do aplicativo ou um de recuperação."""
        origem = origem_de(request)
        d = c.banco.desafio_mfa(body.desafio)
        if d is None:
            _contar_falha(c, origem, "mfa-desafio")
            raise HTTPException(401, "a verificação venceu; entre de novo pelo código do e-mail")
        conta = d["cliente"]
        erros = f"mfa:erro:{conta['id']}"
        _limite_erros(c, erros, "muitos códigos errados; espere 15 minutos e entre de novo")
        meio = _segundo_fator(c, conta, body.codigo, origem)
        if meio is None:
            _errou(c, erros)
            raise HTTPException(401, "código inválido; confira no aplicativo autenticador (ou use um de recuperação)")
        if not c.banco.revogar_sessao(body.desafio):  # o desafio vale uma vez só (outro pedido usou antes)
            raise HTTPException(401, "a verificação venceu; entre de novo pelo código do e-mail")
        return _abrir_sessao(c, conta, origem, "entrada", {**d["contexto"].get("detalhe", {}), "mfa": meio})

    @app.post("/api/conta/sair")
    def sair(request: Request, c: Config = Depends(config)):
        tok = request.cookies.get(nome_cookie(c, "cliente"))
        if tok:
            s = c.banco.sessao(tok, "cliente")
            c.banco.revogar_sessao(tok)
            if s:
                c.banco.registrar_acesso(s["cliente_id"], "saida", origem_de(request))
        resp = JSONResponse({"ok": True})
        apagar_cookie(resp, c, "cliente")
        return resp

    @app.post("/api/conta/sair-de-todos")
    def sair_de_todos(request: Request, cli: dict = Depends(cliente), c: Config = Depends(config)):
        n = c.banco.revogar_sessoes(cli["id"])
        c.banco.registrar_acesso(cli["id"], "saida_todos", origem_de(request), {"sessoes": n})
        resp = JSONResponse({"ok": True, "sessoes": n})
        apagar_cookie(resp, c, "cliente")
        return resp

    def _foto_url(c: Config, cli: dict) -> str | None:
        """Endereço da foto do Gravatar (só com a escolha da pessoa). O ?v= muda com o e-mail (o navegador guarda
        a foto por um dia) e é um HMAC: o registro de acesso do Caddy não ganha um resumo do e-mail."""
        if not cli.get("gravatar") or not cli.get("email"):
            return None
        v = hmac.new(c.chave_codigos, f"foto:{cli['email']}".encode(), hashlib.sha256).hexdigest()[:12]
        return f"/api/conta/foto?v={v}"

    def _resumo_conta(c: Config, cli: dict, sessao_nova: bool = False) -> dict:
        """A conta como toda página do cliente vê (pedida no <head> de cada uma): uma transação só no banco."""
        mfa = bool(cli.get("mfa_segredo"))
        extra = c.banco.resumo_conta(cli["id"], teste=cli["plano"] == "teste", mfa=mfa)
        teste = None
        if cli["plano"] == "teste":
            teste = {"usado": extra["teste_usado"], **{k: TESTE[k] for k in ("fotos", "duracao")}}
        return {
            "id": cli["id"],
            "email": cli.get("email"),
            "nome": cli.get("nome"),
            "plano": cli["plano"],
            "criado": cli.get("criado"),
            "saldo": extra["saldo"],
            "teste": teste,
            "pagamento": {"ativo": c.mp is not None, "minimo": c.mp.minimo, "maximo": c.mp.maximo} if c.mp else None,
            "perfil": {
                "primeiro_nome": cli.get("primeiro_nome"),
                "ultimo_nome": cli.get("ultimo_nome"),
                "gravatar": bool(cli.get("gravatar")),
                "foto": _foto_url(c, cli),
            },
            "tema": cli.get("tema") or "auto",
            "mfa": {"ativo": mfa, "desde": cli.get("mfa_ativo_em"), "codigos": extra.get("codigos")},
            # confirmação recente ("é você mesmo?"): quantos segundos ainda valem nesta sessão
            "confirmada_s": contas.CONFIRMACAO_S if sessao_nova else _confirmada_s(cli),
            "sessao": sessao_nova or "_sessao" in cli,  # False: código de acesso no cabeçalho (integração)
            "documentos": {"atuais": contas.DOCUMENTOS, "pendentes": contas.documentos_pendentes(cli)},
            "interesses": extra["interesses"],
        }

    @app.get("/api/conta")
    def minha_conta(cli: dict = Depends(cliente), c: Config = Depends(config)):
        return _resumo_conta(c, cli)

    @app.get("/api/conta/dados")
    def meus_dados(request: Request, cli: dict = Depends(cliente), c: Config = Depends(config)):
        """Cópia dos dados pessoais da conta, em JSON (LGPD, art. 18, II e V)."""
        c.banco.registrar_acesso(cli["id"], "dados_exportados", origem_de(request))
        dados = c.banco.dados_da_conta(cli["id"])
        for p in dados["pedidos"]:
            with contextlib.suppress(ValueError, TypeError):
                p["pedido"] = json.loads(p["pedido"])
        return JSONResponse(
            dados, headers={"Content-Disposition": f'attachment; filename="render2go-dados-{cli["id"]}.json"'}
        )

    @app.post("/api/conta/excluir")
    def excluir_conta(
        body: ExcluirIn, request: Request, cli: dict = Depends(cliente_sessao), c: Config = Depends(config)
    ):
        if body.confirmacao.strip().upper() != "EXCLUIR":
            raise HTTPException(422, 'para excluir, escreva "EXCLUIR"')
        _exigir_confirmacao(cli)
        resumo = None
        if cli.get("email"):  # o mesmo e-mail não ganha outro teste grátis depois
            resumo = contas.resumo_email(c.chave_codigos, contas.normalizar(cli["email"], exigir_dominio=False)[1])
        try:
            c.banco.excluir_conta(cli["id"], resumo)
        except BancoError as e:
            raise HTTPException(409, str(e)) from e
        c.banco.registrar_acesso(cli["id"], "conta_excluida", origem_de(request))
        _avisar(c, cli.get("email"), "conta_excluida")
        resp = JSONResponse({"ok": True})
        apagar_cookie(resp, c, "cliente")
        return resp

    # -- configurações da conta (10/10/2026) --------------------------------------------------------

    @app.post("/api/conta/confirmar/codigo")
    def pedir_confirmacao(request: Request, cli: dict = Depends(cliente_sessao), c: Config = Depends(config)):
        """Confirmação recente pelo e-mail (sem a verificação em duas etapas): um código com chave própria, que
        não invalida um código de entrada pedido em outro aparelho."""
        if cli.get("mfa_segredo"):
            raise HTTPException(409, "confirme com o código do aplicativo autenticador ou um código de recuperação")
        if not cli.get("email"):
            raise HTTPException(409, "a conta não tem e-mail: saia e entre de novo para confirmar")
        if c.email is None:
            raise HTTPException(503, "o envio de e-mail está indisponível agora; tente de novo mais tarde")
        for janela, maximo in ((3600, 5), (86400, 10)):
            if c.banco.contar(f"confirmar:codigo:{cli['id']}", janela) > maximo:
                raise HTTPException(429, "muitos códigos pedidos; tente de novo mais tarde")
        codigo = contas.novo_codigo()
        chave = f"confirmar:{cli['id']}"
        resumo = contas.resumo_codigo(c.chave_codigos, chave, codigo)
        c.banco.criar_codigo(chave, cli["email"], resumo, contas.CODIGO_VALIDADE_S, origem_de(request))
        c.em_segundo_plano(_enviar_codigo, c, cli["email"], codigo, "confirmar", correio=True)
        return {"ok": True, "email": cli["email"], "validade_s": contas.CODIGO_VALIDADE_S}

    @app.post("/api/conta/confirmar")
    def confirmar(
        body: ConfirmarIn, request: Request, cli: dict = Depends(cliente_sessao), c: Config = Depends(config)
    ):
        """Confirma que é a pessoa (10 min): com a verificação em duas etapas, o código do aplicativo ou um de
        recuperação (o do e-mail sozinho não basta: quem tomou o e-mail não desliga a proteção); sem ela, o código
        que chegou no e-mail."""
        origem = origem_de(request)
        erros = f"confirmar:erro:{cli['id']}"
        _limite_erros(c, erros, "muitos códigos errados; espere 15 minutos")
        meio = None
        if cli.get("mfa_segredo"):
            meio = _segundo_fator(c, cli, body.codigo, origem)
        else:
            codigo, chave = contas.limpar_codigo(body.codigo), f"confirmar:{cli['id']}"
            if contas.CODIGO.match(codigo) and c.banco.conferir_codigo(
                chave, contas.resumo_codigo(c.chave_codigos, chave, codigo), contas.CODIGO_TENTATIVAS
            ):
                meio = "email"
        if meio is None:
            _errou(c, erros)
            raise HTTPException(401, "código inválido ou vencido; confira o código ou peça outro")
        c.banco.confirmar_sessao(cli["_sessao"], time.time() + contas.CONFIRMACAO_S)
        c.banco.registrar_acesso(cli["id"], "confirmacao", origem, {"meio": meio})
        return {"ok": True, "confirmada_s": contas.CONFIRMACAO_S}

    @app.post("/api/conta/mfa/iniciar")
    def mfa_iniciar(cli: dict = Depends(cliente_sessao), c: Config = Depends(config)):
        """1º passo para ligar a verificação em duas etapas: o segredo novo (para o aplicativo), a URI e o QR Code.
        Só liga de fato quando a pessoa confirmar um código dele (`/api/conta/mfa/ativar`)."""
        _exigir_confirmacao(cli)
        if cli.get("mfa_segredo"):
            raise HTTPException(409, "a verificação em duas etapas já está ligada")
        if c.banco.contar(f"mfa:iniciar:{cli['id']}", 3600) > 10:
            raise HTTPException(429, "muitas tentativas; tente de novo mais tarde")
        segredo = contas.novo_segredo_totp()
        if not c.banco.mfa_iniciar(cli["id"], contas.cifrar(c.chave_segredos, segredo, f"cliente:{cli['id']}")):
            raise HTTPException(409, "a verificação em duas etapas já está ligada")
        uri = contas.uri_totp(segredo, cli.get("email") or cli["id"])
        return {"segredo": segredo, "uri": uri, "qr_svg": contas.qr_svg(uri), "validade_s": contas.MFA_PENDENTE_S}

    @app.post("/api/conta/mfa/ativar")
    def mfa_ativar(
        body: CodigoAppIn, request: Request, cli: dict = Depends(cliente_sessao), c: Config = Depends(config)
    ):
        """2º passo: um código do aplicativo confere com o segredo novo; liga e devolve os códigos de recuperação
        (mostrados uma vez só: o banco guarda só o HMAC de cada um). Sem pedir a confirmação de novo: o segredo
        pendente só existe porque o 1º passo foi confirmado, e só a tela que o recebeu tem o código dele."""
        erros = f"mfa:ativar:{cli['id']}"
        _limite_erros(c, erros, "muitos códigos errados; espere 15 minutos e comece de novo")
        conta = c.banco.conta(cli["id"]) or {}
        pendente = conta.get("mfa_pendente")
        if conta.get("mfa_segredo"):
            raise HTTPException(409, "a verificação em duas etapas já está ligada")
        if not pendente or float(conta.get("mfa_pendente_em") or 0) < time.time() - contas.MFA_PENDENTE_S:
            raise HTTPException(409, "o QR Code venceu; comece de novo")
        try:
            segredo = contas.decifrar(c.chave_segredos, pendente, f"cliente:{cli['id']}")
        except ValueError as e:
            raise HTTPException(409, "o QR Code venceu; comece de novo") from e
        contador = contas.totp_confere(segredo, re.sub(r"\s", "", body.codigo))
        if contador is None:
            _errou(c, erros)
            raise HTTPException(401, "o código não confere: confira se o aplicativo leu o QR Code desta tela")
        codigos = contas.novos_codigos_recuperacao()
        resumos = [contas.resumo_recuperacao(c.chave_codigos, cli["id"], k) for k in codigos]
        if not c.banco.mfa_ativar(cli["id"], pendente, contador, resumos, contas.MFA_PENDENTE_S):
            raise HTTPException(409, "o QR Code mudou em outra aba; comece de novo")
        c.banco.registrar_acesso(cli["id"], "mfa_ligado", origem_de(request))
        _avisar(c, cli.get("email"), "mfa_ligado")
        return {"ok": True, "codigos": [contas.formatar_recuperacao(k) for k in codigos]}

    @app.post("/api/conta/mfa/desligar")
    def mfa_desligar(request: Request, cli: dict = Depends(cliente_sessao), c: Config = Depends(config)):
        _exigir_confirmacao(cli)
        if not c.banco.mfa_desligar(cli["id"]):
            raise HTTPException(409, "a verificação em duas etapas já está desligada")
        c.banco.registrar_acesso(cli["id"], "mfa_desligado", origem_de(request))
        _avisar(c, cli.get("email"), "mfa_desligado")
        return {"ok": True}

    @app.post("/api/conta/mfa/codigos")
    def mfa_codigos(request: Request, cli: dict = Depends(cliente_sessao), c: Config = Depends(config)):
        """Códigos de recuperação novos; os anteriores deixam de valer."""
        _exigir_confirmacao(cli)
        codigos = contas.novos_codigos_recuperacao()
        resumos = [contas.resumo_recuperacao(c.chave_codigos, cli["id"], k) for k in codigos]
        if not c.banco.trocar_codigos_recuperacao(cli["id"], resumos):
            raise HTTPException(409, "ligue a verificação em duas etapas antes")
        c.banco.registrar_acesso(cli["id"], "codigos_recuperacao_novos", origem_de(request))
        _avisar(c, cli.get("email"), "codigos_novos")
        return {"ok": True, "codigos": [contas.formatar_recuperacao(k) for k in codigos]}

    def _email_novo(c: Config, cli: dict, texto: str) -> tuple[str, str]:
        email, norm = _email_valido(texto)
        try:
            contas.normalizar(texto)
        except contas.DominioRecusado as e:
            raise HTTPException(422, str(e)) from e
        if norm == cli.get("email_norm"):
            raise HTTPException(422, "este já é o e-mail da sua conta")
        return email, norm

    @app.post("/api/conta/email/codigo")
    def email_codigo(
        body: TrocarEmailIn, request: Request, cli: dict = Depends(cliente_sessao), c: Config = Depends(config)
    ):
        """Troca de e-mail, 1º passo: com a confirmação recente, um código vai para o endereço novo. Pode dizer que
        o endereço já é de outra conta: a pessoa está conectada, e há limite de 5 por hora e 10 por dia."""
        _exigir_confirmacao(cli)
        for janela, maximo in ((3600, 5), (86400, 10)):
            if c.banco.contar(f"email-novo:{cli['id']}", janela) > maximo:
                raise HTTPException(429, "muitas trocas de e-mail pedidas; tente de novo mais tarde")
        email, norm = _email_novo(c, cli, body.email)
        dono = c.banco.conta_por_email(norm)
        if dono and dono["id"] != cli["id"]:
            raise HTTPException(409, "este e-mail já é de outra conta")
        if c.email is None:
            raise HTTPException(503, "o envio de e-mail está indisponível agora; tente de novo mais tarde")
        codigo, chave = contas.novo_codigo(), f"email-novo:{cli['id']}"
        resumo = contas.resumo_codigo(c.chave_codigos, f"{chave}:{norm}", codigo)  # o código só vale para este e-mail
        c.banco.criar_codigo(chave, email, resumo, contas.CODIGO_VALIDADE_S, origem_de(request))
        c.em_segundo_plano(_enviar_codigo, c, email, codigo, "email-novo", correio=True)
        return {"ok": True, "email": email, "validade_s": contas.CODIGO_VALIDADE_S}

    @app.post("/api/conta/email")
    def trocar_email(
        body: TrocarEmailIn, request: Request, cli: dict = Depends(cliente_sessao), c: Config = Depends(config)
    ):
        """Troca de e-mail, 2º passo: o código que chegou no endereço novo (só existe se o 1º passo foi confirmado,
        por isso a confirmação não é pedida de novo: o e-mail pode demorar). O endereço antigo recebe um aviso."""
        email, norm = _email_novo(c, cli, body.email)
        codigo, chave = contas.limpar_codigo(body.codigo or ""), f"email-novo:{cli['id']}"
        if not contas.CODIGO.match(codigo) or not c.banco.conferir_codigo(
            chave, contas.resumo_codigo(c.chave_codigos, f"{chave}:{norm}", codigo), contas.CODIGO_TENTATIVAS
        ):
            raise HTTPException(401, "código inválido ou vencido; confira o código ou peça outro")
        antigo_norm = cli.get("email_norm")
        resumo_antigo = contas.resumo_email(c.chave_codigos, antigo_norm) if antigo_norm else None
        try:
            antigo = c.banco.trocar_email(cli["id"], email, norm, resumo_antigo)
        except BancoError as e:
            raise HTTPException(409, str(e)) from e
        mascaras = {"de": contas.mascarar_email(antigo), "para": contas.mascarar_email(email)}
        c.banco.registrar_acesso(cli["id"], "email_trocado", origem_de(request), mascaras)
        _avisar(c, antigo, "email_trocado", novo=mascaras["para"])
        return {"ok": True, "email": email}

    @app.get("/api/conta/sessoes")
    def minhas_sessoes(cli: dict = Depends(cliente_sessao), c: Config = Depends(config)):
        """Aparelhos conectados (até 50): o aparelho pelo navegador, o IP, quando entrou e o último uso. A
        referência de cada um é derivada do id da sessão, que nunca sai do servidor."""
        return {
            "sessoes": [
                {
                    "ref": contas.ref_sessao(s["id"]),
                    "aparelho": contas.aparelho(s["agente"]),
                    "ip": s["ip"],
                    "criada": s["criado"],
                    "ultimo_uso": s["ultimo_uso"],
                    "atual": s["id"] == cli["_sessao_id"],
                }
                for s in c.banco.sessoes_ativas(cli["id"], cli["_sessao_id"])
            ]
        }

    @app.post("/api/conta/sessoes/encerrar-outras")
    def encerrar_outras(request: Request, cli: dict = Depends(cliente_sessao), c: Config = Depends(config)):
        n = c.banco.revogar_outras(cli["id"], cli["_sessao_id"])
        c.banco.registrar_acesso(cli["id"], "saida_outras", origem_de(request), {"sessoes": n})
        return {"ok": True, "sessoes": n}

    @app.post("/api/conta/sessoes/{ref}/encerrar")
    def encerrar_sessao(ref: str, request: Request, cli: dict = Depends(cliente_sessao), c: Config = Depends(config)):
        alvo = next((s for s in c.banco.sessoes_ativas(cli["id"], cli["_sessao_id"])
                     if contas.iguais(contas.ref_sessao(s["id"]), ref[:40])), None)  # fmt: skip
        if alvo is None or not c.banco.revogar_sessao_id(cli["id"], alvo["id"]):
            raise HTTPException(404, "aparelho não encontrado (a sessão pode já ter terminado)")
        atual = alvo["id"] == cli["_sessao_id"]
        c.banco.registrar_acesso(
            cli["id"], "sessao_encerrada", origem_de(request), {"aparelho": contas.aparelho(alvo["agente"])}
        )
        resp = JSONResponse({"ok": True, "atual": atual})
        if atual:
            apagar_cookie(resp, c, "cliente")
        return resp

    @app.post("/api/conta/desativar")
    def desativar_conta(
        body: ExcluirIn, request: Request, cli: dict = Depends(cliente_sessao), c: Config = Depends(config)
    ):
        """O cliente pausa a própria conta: as sessões caem, o saldo fica, e entrar de novo pelo código do e-mail
        reativa. (A desativação feita pelo painel é outra coisa: só o suporte desfaz.)"""
        if body.confirmacao.strip().upper() != "DESATIVAR":
            raise HTTPException(422, 'para desativar, escreva "DESATIVAR"')
        try:
            r = c.banco.pausar_conta(cli["id"])
        except BancoError as e:
            raise HTTPException(409, str(e)) from e
        for lid in r["lotes"]:  # fotos de um envio que não terminou saem do armazenamento
            c.em_segundo_plano(_apagar_fotos, c, lid)
        c.banco.registrar_acesso(cli["id"], "conta_desativada", origem_de(request), {"sessoes": r["sessoes"]})
        _avisar(c, cli.get("email"), "conta_desativada")
        resp = JSONResponse({"ok": True})
        apagar_cookie(resp, c, "cliente")
        return resp

    @app.post("/api/conta/perfil")
    def mudar_perfil(body: PerfilIn, cli: dict = Depends(cliente_sessao), c: Config = Depends(config)):
        """Nome, sobrenome e a escolha da foto do Gravatar (só os campos enviados)."""
        campos: dict[str, Any] = {}
        for campo in ("primeiro_nome", "ultimo_nome"):
            if campo in body.model_fields_set:
                try:
                    campos[campo] = perfil.nome_pessoa(getattr(body, campo))
                except ValueError as e:
                    rotulo = "nome" if campo == "primeiro_nome" else "sobrenome"
                    raise HTTPException(422, f"{rotulo}: {e}") from e
        if "gravatar" in body.model_fields_set:
            campos["gravatar"] = 1 if body.gravatar else None
        if not campos:
            raise HTTPException(422, "nada para mudar")
        c.banco.atualizar_perfil(cli["id"], campos)
        return _resumo_conta(c, {**cli, **campos})

    @app.post("/api/conta/preferencias")
    def mudar_preferencias(body: PreferenciasIn, cli: dict = Depends(cliente_sessao), c: Config = Depends(config)):
        c.banco.definir_tema(cli["id"], None if body.tema == "auto" else body.tema)
        return {"ok": True, "tema": body.tema}

    @app.get("/api/conta/foto")
    def minha_foto(request: Request, cli: dict = Depends(cliente), c: Config = Depends(config)):
        """A foto do Gravatar, servida daqui (o navegador não fala com terceiros). Sem a escolha da pessoa: 404.
        Com a escolha, mas sem foto no Gravatar: 204 (a página mostra a inicial, sem erro no console do navegador)."""
        if not cli.get("gravatar") or not cli.get("email"):
            raise HTTPException(404, "sem foto")
        foto = c.fotos.foto(cli["email"])
        if foto is None:
            return Response(status_code=204, headers={"Cache-Control": "private, max-age=3600"})
        dados, tipo = foto
        etag = '"' + hashlib.sha256(dados).hexdigest()[:32] + '"'
        cab = {"Cache-Control": "private, max-age=86400", "ETag": etag}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=cab)
        return Response(dados, media_type=tipo, headers=cab)

    @app.post("/api/conta/aceitar")
    def aceitar_documentos(
        body: AceitarIn, request: Request, cli: dict = Depends(cliente_sessao), c: Config = Depends(config)
    ):
        """Quem já estava conectado quando os termos ou a privacidade mudaram aceita a versão nova aqui."""
        if body.documentos != contas.DOCUMENTOS:
            raise HTTPException(409, "os documentos mudaram de novo; recarregue a página para ler a versão atual")
        origem = origem_de(request)
        c.banco.registrar_aceite(cli["id"], contas.DOCUMENTOS, origem)
        c.banco.registrar_acesso(cli["id"], "aceite", origem, contas.DOCUMENTOS)
        return {"ok": True, "documentos": contas.DOCUMENTOS}

    # -- cliente ------------------------------------------------------------------------

    @app.get("/api/receitas")
    def receitas(request: Request, cli: dict = Depends(cliente), c: Config = Depends(config)):
        r = lote.load_receitas()
        minima = duracoes(cli["plano"])[0]  # o "padrão do estilo" nunca fica abaixo dela
        plano: dict[str, Any] = {"nome": cli["plano"], "duracoes": duracoes(cli["plano"])}
        if cli["plano"] in precos.PLANOS_COBRADOS:
            plano.update(precos=precos.PRECOS, saldo=c.banco.saldo(cli["id"]))
        if cli["plano"] == "teste":
            motivo = None
            try:
                _limites_teste(c, cli, origem_de(request), 1)
            except HTTPException as e:
                motivo = e.detail
            plano.update(
                qualidades=[TESTE["qualidade"]],
                max_fotos=TESTE["fotos"],
                disponivel=motivo is None,
                motivo=motivo,
                precos=precos.PRECOS,  # o que custa depois do teste (recarga por Pix)
            )
        return {
            "plano": plano,
            "conta": {"email": cli.get("email"), "plano": cli["plano"]},
            "pagamento": c.mp is not None,
            "autorizacao": AUTORIZACAO,
            "modelos": modelos.disponiveis(),
            "modelo_padrao": modelos.PADRAO,
            # tela de finalidades antes do pedido: o catálogo, os grupos da janela "Ver todas" e as
            # finalidades em breve em que este cliente já marcou "Tenho interesse"
            "finalidades": modelos.finalidades(),
            "entradas": modelos.ENTRADAS,
            "finalidade_padrao": modelos.FINALIDADE_PADRAO,
            "interesses": c.banco.interesses_do_cliente(cli["id"]),
            "receitas": [
                {
                    "id": k,
                    "label": v["label"],
                    "nicho": v["nicho"],
                    "acao": v["acao"],
                    "duracao": max(v["duracao"], minima) if cli["plano"] != "teste" else minima,
                    "exige_consentimento": bool(v.get("exige_consentimento")),
                }
                for k, v in r["receitas"].items()
            ],
            "formatos": ["auto", *r["formatos"]],
            # aviso de foto pequena na página: o tamanho do vídeo de cada formato e qualidade, a regra do
            # formato automático (largura/altura) e a ampliação que o preparo já marca como aviso
            "saida": {
                f: {q: [v[q]["width"], v[q]["height"]] for q in lote.QUALIDADES} for f, v in r["formatos"].items()
            },
            "proporcao_auto": [lote.VERTICAL, lote.HORIZONTAL],
            "ampliacao_aviso": preparo.AMPLIACAO_AVISO,
            "qualidades": list(lote.QUALIDADES),
            "limites": {
                "fotos": None,  # sem limite no pago e no interno; o teste tem o dele (plano.max_fotos)
                "por_envio": FOTOS_POR_ENVIO,  # a página manda o pedido maior em partes
                "bytes_foto": MAX_BYTES_FOTO,
                "tipos": list(TIPOS),
            },
        }

    @app.post("/api/interesses")
    def registrar_interesse(body: InteresseIn, cli: dict = Depends(cliente), c: Config = Depends(config)):
        """ "Tenho interesse" numa finalidade ou num recurso em breve (Mix, Projetos, indicação, telefone, nome de
        usuário): aparece no painel (total e cada clique)."""
        if body.finalidade not in modelos.IDS_FINALIDADES and body.finalidade not in modelos.RECURSOS:
            raise HTTPException(422, "finalidade desconhecida")
        if modelos.disponivel(body.finalidade):
            raise HTTPException(422, "esta finalidade já está disponível")
        novo = c.banco.registrar_interesse(cli["id"], body.finalidade)
        return {"ok": True, "novo": novo}

    @app.post("/api/lotes")
    def criar_lote(body: LoteIn, request: Request, cli: dict = Depends(cliente), c: Config = Depends(config)):
        _exigir_aceite(cli)
        origem = origem_de(request)
        origem["impressao"] = impressao_navegador(origem, body.navegador)
        if body.modelo not in {m["id"] for m in modelos.disponiveis()}:
            raise HTTPException(422, "modelo de geração indisponível")
        d = filtros.avaliar(body.prompt)
        if not d.allowed:  # C1 já na entrada (ADR 0004); a categoria só no registro de acesso
            c.banco.registrar_acesso(
                cli["id"], "pedido_recusado_filtro", origem, {"motivo": d.reason, "texto": body.prompt[:300]}
            )
            raise HTTPException(422, RECUSA_FILTRO[0].upper() + RECUSA_FILTRO[1:])
        rec = lote.load_receitas()
        r = rec["receitas"].get(body.receita)
        if r is None:
            raise HTTPException(422, "receita desconhecida")
        if r.get("exige_consentimento") and not body.consentimento:
            raise HTTPException(422, "esta receita exige a autorização por escrito das pessoas das fotos")
        if body.formato not in ("auto", *rec["formatos"]) or body.qualidade not in lote.QUALIDADES:
            raise HTTPException(422, "formato ou qualidade inválidos")
        if cli["plano"] != "teste":  # sem duração escolhida: a do estilo (nunca abaixo do mínimo)
            aceitas = duracoes(cli["plano"])
            body.duracao = max(r["duracao"], aceitas[0]) if body.duracao is None else body.duracao
            if body.duracao not in aceitas:
                raise HTTPException(422, f"duração de {aceitas[0]} a {aceitas[-1]} s")
        try:
            presets.compose("i2v", body.prompt, r["presets"])
        except presets.PresetError as e:
            raise HTTPException(422, str(e)) from e
        if not 1 <= len(body.fotos) <= FOTOS_POR_ENVIO:
            raise HTTPException(422, f"de 1 a {FOTOS_POR_ENVIO} fotos por envio; o pedido maior vai em partes")
        textos = _textos_por_foto(body, r)
        if cli["plano"] == "teste":
            pedido_teste = (body.qualidade, body.duracao or TESTE["duracao"], body.variacoes)
            if pedido_teste != (TESTE["qualidade"], TESTE["duracao"], TESTE["variacoes"]):
                raise HTTPException(422, "teste gratuito: qualidade rápida (480p), vídeos de 3 s, 1 variação")
            if len(body.fotos) > TESTE["fotos"]:
                raise HTTPException(429, f"o teste gratuito vale para até {TESTE['fotos']} fotos, num pedido só")
            _limites_teste(c, cli, origem, len(body.fotos))
            body.duracao = TESTE["duracao"]
        # sem limite de fotos nos planos pago e interno (pedido do dono, 12/10/2026): a cota diária fica só
        # no teste gratuito, que já tem o teto de 5 fotos
        if cli["plano"] == "teste" and c.banco.fotos_hoje(cli["id"]) + len(body.fotos) > cli["cota_fotos_dia"]:
            raise HTTPException(429, f"cota diária de {cli['cota_fotos_dia']} fotos atingida")
        preco_video = None
        if cli["plano"] in precos.PLANOS_COBRADOS:  # pré-pago: cada vídeo é cobrado quando sai certo (ADR 0012)
            preco_video = precos.preco_video(body.qualidade, body.duracao)
            livre = c.banco.saldo_livre(cli["id"])
            if livre < preco_video:  # o saldo paga ao menos o 1º vídeo; o resto espera a recarga
                raise HTTPException(402, sem_saldo_video(preco_video, livre))
        fotos = _fotos_do_envio(cli["id"], body.fotos, textos, set())
        pedido = body.model_dump(exclude={"fotos", "declarante", "navegador"})
        if any(textos):  # pedido em sequência: os textos ficam nas fotos; aqui, só quantos são
            pedido["textos_por_foto"] = len({t for t in textos if t})
        autorizacao = None
        if body.consentimento:  # registro de auditoria: conta, de onde, qual texto, quais fotos, sinais
            sinais = body.navegador.model_dump() if body.navegador else {}
            sinais["accept_language"] = request.headers.get("accept-language", "")[:200] or None
            sinais["impressao"] = origem["impressao"]
            autorizacao = {
                "declarante": (body.declarante or "").strip(),
                "texto_versao": AUTORIZACAO["versao"],
                "texto": AUTORIZACAO["texto"],
                "sinais": sinais,
            }
        if cli["plano"] == "teste":  # envio anterior que não terminou não segura o teste
            c.banco.substituir_envios(cli["id"])
        lid = c.banco.criar_lote(
            cli["id"], pedido, fotos, origem=origem, autorizacao=autorizacao, preco_video=preco_video
        )
        c.banco.registrar_acesso(cli["id"], "pedido", origem, {"lote": lid, "fotos": len(fotos)})
        envios = _envios(c, fotos, c.banco.fotos(lid))
        # custo: a estimativa do pedido inteiro; cada vídeo é cobrado só quando sai certo (ADR 0012)
        custo = len(fotos) * body.variacoes * preco_video if preco_video else 0
        return {"lote": lid, "envios": envios, "expira_s": URL_ENVIO_S, "custo": custo, "preco_video": preco_video}

    def _envios(c: Config, fotos: list[dict], gravadas: list[dict]) -> list[dict]:
        """URL de envio de cada foto: só aceita exatamente os bytes declarados (o R2 recusa outro tamanho)."""
        originais = {x["nome"]: x["original"] for x in fotos}
        return [
            {
                "original": originais[f["nome"]],
                "url": c.armazenamento.url(
                    "PUT", f["chave"], URL_ENVIO_S, content_type=f["tipo"], content_length=f["bytes"]
                ),
                "tipo": f["tipo"],
            }
            for f in gravadas
            if f["nome"] in originais
        ]

    @app.post("/api/lotes/{lote_id}/fotos")
    def mais_fotos(lote_id: str, body: FotosIn, cli: dict = Depends(cliente), c: Config = Depends(config)):
        """Pedido grande em partes (sem limite de fotos no pago e no interno, 12/10/2026): mais fotos num
        pedido que ainda espera o envio, com as mesmas conferências da criação (tipo, tamanho, textos)."""
        _exigir_aceite(cli)
        if cli["plano"] == "teste":
            raise HTTPException(429, f"o teste gratuito vale para até {TESTE['fotos']} fotos, num pedido só")
        lt = c.banco.lote(lote_id, cli["id"])
        if lt is None:
            raise HTTPException(404, "lote não encontrado")
        if lt["status"] != "aguardando_envio":
            raise HTTPException(409, "este pedido já foi enviado; as fotos novas vão num pedido novo")
        if len(body.fotos) > FOTOS_POR_ENVIO:
            raise HTTPException(422, f"de 1 a {FOTOS_POR_ENVIO} fotos por envio")
        pedido = json.loads(lt["pedido"])
        receita = lote.load_receitas()["receitas"].get(pedido.get("receita"))
        if receita is None:
            raise HTTPException(422, "receita desconhecida")
        envio = SimpleNamespace(prompt=pedido["prompt"], fotos=body.fotos)
        textos = _textos_por_foto(envio, receita)
        existentes = c.banco.fotos(lote_id)
        distintos = {f["prompt"] for f in existentes if f["prompt"]} | {t for t in textos if t}
        if len(distintos) > MAX_TEXTOS_FOTO:
            raise HTTPException(
                422,
                f"no máximo {MAX_TEXTOS_FOTO} textos diferentes por pedido (fotos com o mesmo texto contam uma vez)",
            )
        fotos = _fotos_do_envio(cli["id"], body.fotos, textos, {f["nome"] for f in existentes})
        try:
            novas = c.banco.acrescentar_fotos(lote_id, cli["id"], fotos)
        except BancoError as e:
            raise HTTPException(409, str(e)) from e
        return {"lote": lote_id, "envios": _envios(c, fotos, novas), "expira_s": URL_ENVIO_S}

    @app.post("/api/lotes/{lote_id}/enviado")
    def enviado(lote_id: str, cli: dict = Depends(cliente), c: Config = Depends(config)):
        lt = c.banco.lote(lote_id, cli["id"])
        if lt is None:
            raise HTTPException(404, "lote não encontrado")
        if cli["plano"] == "teste":  # de novo aqui: dois pedidos enviados ao mesmo tempo
            origem = {"ip": lt["ip"] or "desconhecido", "dispositivo": lt["dispositivo"], "rede": lt.get("rede")}
            _limites_teste(c, cli, origem, lt["n_fotos"], exceto=lote_id)
        fotos = c.banco.fotos(lote_id)
        with ThreadPoolExecutor(max_workers=32) as ex:  # pedido grande: milhares de consultas ao R2
            tamanhos = dict(
                zip(
                    [f["chave"] for f in fotos],
                    ex.map(lambda f: c.armazenamento.tamanho(f["chave"]), fotos),
                    strict=False,
                )
            )
        try:
            problemas = c.banco.confirmar_envio(lote_id, tamanhos)
        except SaldoInsuficiente as e:  # outro pedido gastou o saldo enquanto as fotos subiam
            with contextlib.suppress(BancoError):
                c.banco.recusar(lote_id, "saldo", "saldo insuficiente quando as fotos terminaram de chegar")
            _se_fechou(c, lote_id)
            raise HTTPException(402, sem_saldo(e.custo, e.saldo)) from e
        except BancoError as e:
            raise HTTPException(409, str(e)) from e
        if problemas:
            raise HTTPException(422, {"problemas": problemas})
        if c.analises is not None:  # aviso de pessoas para o revisor, em segundo plano
            c.analises.agendar(lote_id)
        if cli["plano"] == "interno":  # o dono do serviço revisa o próprio pedido ao fazê-lo (ADR 0004)
            r = _aprovar(c, lote_id, "auto:interno")
            return {"lote": lote_id, "status": "aprovado", "videos": r["videos"]}
        return {"lote": lote_id, "status": "aguardando_aprovacao"}

    def _video(c: Config, cliente_id: str, lote_id: str, j: dict) -> dict:
        """Um vídeo do pedido para a página; pronto (e não excluído), com os links assinados por 24 h."""
        item = {"id": j["id"], "arquivo": j["arquivo"], "status": j["status"], "aviso": j["aviso"],
                "texto": j.get("texto"), "excluido": bool(j.get("excluido_em"))}  # fmt: skip
        if j["status"] == "ok" and not j.get("excluido_em"):
            chave = chave_saida(cliente_id, lote_id, j["arquivo"])
            item["url"] = c.armazenamento.url("GET", chave, URL_DOWNLOAD_S)  # abre e toca no navegador
            item["baixar"] = c.armazenamento.url(  # o mesmo vídeo, salvo como arquivo
                "GET",
                chave,
                URL_DOWNLOAD_S,
                extra={"response-content-disposition": f'attachment; filename="{j["arquivo"]}"'},
            )
        return item

    @app.get("/api/lotes/{lote_id}/videos")
    def videos_do_lote(lote_id: str, depois: int = 0, cli: dict = Depends(cliente), c: Config = Depends(config)):
        """Vídeos prontos de um pedido grande, de VIDEOS_POR_PAGINA em VIDEOS_POR_PAGINA, na ordem da lista
        (`depois`: quantos a página já mostra)."""
        lt = c.banco.lote(lote_id, cli["id"])
        if lt is None:
            raise HTTPException(404, "lote não encontrado")
        if depois < 0:
            raise HTTPException(422, "depois: a partir de 0")
        pedido = json.loads(lt["pedido"])
        com_texto = [lote_id] if pedido.get("textos_por_foto") else []
        prontos = [
            j
            for j in c.banco.jobs_de([lote_id], com_texto=com_texto)[lote_id]
            if j["status"] == "ok" and not j.get("excluido_em")
        ]
        pagina = prontos[depois : depois + VIDEOS_POR_PAGINA]
        return {
            "videos": [_video(c, cli["id"], lote_id, j) for j in pagina],
            "mais": max(0, len(prontos) - depois - len(pagina)),
        }

    def _resumo(
        lt: dict,
        c: Config,
        cliente_id: str,
        jobs: list[dict] | None = None,
        cobranca: dict | None = None,
        pedido: dict | None = None,
        livre: int | None = None,
    ) -> dict:
        if jobs is None:
            jobs = [j for j in c.banco.jobs(lt["id"]) if j["tipo"] == "render"]
        contagem = {
            "prontos": sum(1 for j in jobs if j["status"] == "ok"),
            "falhas": sum(1 for j in jobs if j["status"] == "erro"),
            "excluidos": sum(1 for j in jobs if j.get("excluido_em")),
        }
        pendente = any(j["status"] == "pendente" for j in jobs)
        # pedido grande (12/10/2026): a página recebe os primeiros vídeos prontos e as contagens; os
        # seguintes vêm de /api/lotes/{lote}/videos, aos poucos (nada de milhares de links a cada 10 s)
        mais = 0
        if len(jobs) > VIDEOS_POR_PAGINA:
            prontos = [j for j in jobs if j["status"] == "ok" and not j.get("excluido_em")]
            mais = max(0, len(prontos) - VIDEOS_POR_PAGINA)
            jobs_lista = prontos[:VIDEOS_POR_PAGINA]
        else:
            jobs_lista = jobs
        videos = [_video(c, cliente_id, lt["id"], j) for j in jobs_lista]
        pedido = pedido if pedido is not None else json.loads(lt["pedido"])
        if lt["preco_video"] is not None and cobranca is None:
            cobranca = c.banco.cobranca(lt["id"])
        por_video = lt.get("cobranca") == "video" and bool(lt["preco_video"])
        # cobrança por vídeo (ADR 0012): sem saldo livre para o próximo vídeo, o pedido espera a recarga
        aguardando_saldo = False
        if por_video and lt["status"] in ("aprovado", "processando") and pendente:
            livre = c.banco.saldo_livre(cliente_id) if livre is None else livre
            aguardando_saldo = livre < int(lt["preco_video"])
        return {
            "lote": lt["id"],
            "status": lt["status"],
            "criado": lt["criado"],
            "fotos": lt["n_fotos"],
            "motivo": lt["motivo"],
            "pedido": pedido,
            "modelo": modelos.nome(pedido.get("modelo")),
            "prontos": contagem["prontos"],
            "total": len(jobs),
            "falhas": contagem["falhas"],
            "excluidos": contagem["excluidos"],
            "videos": videos,
            "videos_mais": mais,  # vídeos prontos além dos listados (pedido grande)
            "previsao": {"fase": "saldo"} if aguardando_saldo else _previsao(c, lt, jobs),
            # pré-pago: quanto o pedido custou e quanto já voltou para o saldo; no modo por vídeo, o que já
            # foi cobrado dos vídeos entregues, o preço de cada um e se espera a recarga
            "cobranca": cobranca if lt["preco_video"] is not None else None,
            "por_video": por_video,
            "preco_video": lt["preco_video"],
            "aguardando_saldo": aguardando_saldo,
        }

    def _previsao(c: Config, lt: dict, jobs: list[dict]) -> dict | None:
        """Quanto falta para começar e para terminar, em segundos (o cliente vê só o tempo).

        Usa o estado real dos pods (o orquestrador registra cada um na tabela geradores) e a subida
        medida: pod desligado, a subida típica inteira; pod subindo, o que falta dela; pod pronto,
        só a tradução do pedido, se ainda faltar. A fila segue a ordem de `Banco.reivindicar`. Fila,
        pods e médias vêm de um retrato de poucos segundos, o mesmo para todas as páginas."""
        if lt["status"] == "aguardando_aprovacao":
            return {"fase": "analise", "inicio_max_s": PRAZO_ANALISE_S}
        if lt["status"] not in ("aprovado", "processando"):
            return None
        por_video = c.retrato("por_video", c.banco.segundos_por_video) or VIDEO_PADRAO_S
        g = c.retrato("geradores", c.banco.estado_geradores)
        vazao = por_video / max(1, g["prontos"] + len(g["subindo"]))
        fila = c.banco.videos_a_frente(lt, c.retrato("fila", c.banco.linhas_fila))
        if any(j["status"] in ("em_andamento", "ok") for j in jobs):
            return {"fase": "gerando", "fim_s": math.ceil(fila["ultimo"] * vazao)}
        subida = g["subida_s"] or PREPARO_S
        if g["prontos"]:
            espera = TRADUCAO_S if lt["prompt_en"] is None else 0.0
        elif g["subindo"]:  # o pod mais adiantado já andou parte da subida
            espera = max(TRADUCAO_S, subida - (time.time() - g["subindo"][0]))
        else:
            espera = subida + CICLO_S / 2
            if lt["prioridade"] > 0:  # teste gratuito: espera juntar com outros testes
                trabalho = c.retrato("trabalho", c.banco.trabalho)
                if trabalho and not any(t["prioridade"] == 0 for t in trabalho):
                    videos = sum(int(t["pendentes"] or 0) + int(t["em_andamento"] or 0) for t in trabalho)
                    idade = time.time() - min(float(t["mais_antigo"]) for t in trabalho)
                    if videos < AGRUPAR_TESTE_VIDEOS:
                        espera += max(0.0, AGRUPAR_TESTE_S - idade)
        return {
            "fase": "fila",
            "inicio_s": math.ceil(espera + fila["primeiro"] * vazao),
            "fim_s": math.ceil(espera + fila["ultimo"] * vazao),
        }

    @app.post("/api/suporte")
    def mensagem_suporte(
        body: SuporteIn,
        request: Request,
        cli: dict | None = Depends(cliente_opcional),
        c: Config = Depends(config),
    ):
        origem = origem_de(request)
        email = (body.email or "").strip() or None
        if email and not suporte.EMAIL.match(email):
            raise HTTPException(422, "e-mail para resposta inválido")
        if cli is None and not email:
            raise HTTPException(422, "informe o seu e-mail para podermos responder")
        cliente_id = cli["id"] if cli else ""
        valores = {"ip": origem["ip"], "dispositivo": origem["dispositivo"], "cliente_id": cliente_id}
        for campo, janela, maximo in suporte.LIMITES if cli else suporte.LIMITES_ANONIMO:
            if c.banco.contar_suporte(campo, valores[campo], janela) >= maximo:
                espera = "uma hora" if janela <= 3600 else "amanhã"
                raise HTTPException(429, f"muitas mensagens seguidas; tente de novo em {espera}")
        if cli is None:  # sem conta: o "não sou um robô" também aqui
            _anti_robo(c, body.turnstile, origem, "suporte")
        mensagem = body.mensagem.strip()
        sid = c.banco.registrar_suporte(cliente_id, origem, email, body.assunto.strip(), mensagem)
        if c.email is not None and c.email.para:
            dados = {
                "id": sid,
                "cliente_id": cliente_id,
                "cliente_nome": (cli.get("email") or cli["nome"]) if cli else None,
                "plano": cli["plano"] if cli else None,
                "email": email or (cli.get("email") if cli else None),
                "assunto": body.assunto.strip(),
                "mensagem": mensagem,
                "ip": origem["ip"],
                "dispositivo": origem["dispositivo"],
                "quando": time.time(),
            }
            c.em_segundo_plano(_enviar_suporte, c, sid, dados)
        return {"ok": True, "id": sid}

    @app.post("/api/cookies")
    def escolha_cookies(body: CookiesIn, request: Request, c: Config = Depends(config)):
        """Guarda a escolha do aviso de cookies (sem login). Os necessários não têm escolha."""
        origem = origem_de(request)
        if c.banco.contar(f"cookies:{origem['rede']}", 3600) > 60:  # contra quem tenta encher o banco
            raise HTTPException(429, "muitas escolhas seguidas")
        escolha = {"necessarios": True, "estatisticas": body.estatisticas, "marketing": body.marketing}
        return {"ok": True, "id": c.banco.registrar_cookies(origem, body.versao, escolha)}

    @app.get("/api/lotes")
    def meus_lotes(cli: dict = Depends(cliente), c: Config = Depends(config)):
        lotes = c.banco.lotes(cliente_id=cli["id"], limite=20)
        pedidos = {lt["id"]: json.loads(lt["pedido"]) for lt in lotes}
        ids = [lt["id"] for lt in lotes]
        # uma consulta para os jobs de todos os pedidos da página; o texto de cada foto, só nos pedidos em sequência
        jobs = c.banco.jobs_de(ids, com_texto=[i for i in ids if pedidos[i].get("textos_por_foto")])
        cobr = c.banco.cobrancas([lt["id"] for lt in lotes if lt["preco_video"] is not None])
        livre = c.banco.saldo_livre(cli["id"]) if any(lt.get("cobranca") == "video" for lt in lotes) else None
        return [_resumo(lt, c, cli["id"], jobs[lt["id"]], cobr.get(lt["id"]), pedidos[lt["id"]], livre) for lt in lotes]

    @app.get("/api/saldo")
    def meu_saldo(cli: dict = Depends(cliente), c: Config = Depends(config)):
        if cli["plano"] not in precos.PLANOS_COBRADOS:
            return {"pre_pago": False, "pagamento": c.mp is not None}
        return {
            "pre_pago": True,
            "saldo": c.banco.saldo(cli["id"]),
            "precos": precos.PRECOS,
            "extrato": c.banco.extrato(cli["id"], 20),
            "pagamento": c.mp is not None,
        }

    # -- extrato por rodada e logs de geração (10/10/2026) -----------------------------------------

    def _periodo(dias: int) -> float:
        if dias not in DIAS_EXTRATO:
            raise HTTPException(422, f"período: {', '.join(map(str, DIAS_EXTRATO))} dias (0 = tudo)")
        return time.time() - dias * 86400 if dias else 0.0

    def _rodada(r: dict) -> dict:
        """Uma rodada do extrato: o pedido, o tipo e o modelo de geração, os vídeos e quanto custou."""
        lt = r["lote"]
        info = modelos.info(r["modelo"] or (lt or {}).get("modelo"))
        videos = duracao = resolucao = None
        if lt:
            p = json.loads(lt["pedido"])
            videos = int(lt["n_fotos"]) * int(p.get("variacoes") or 1)
            duracao, resolucao = p.get("duracao"), RESOLUCAO_EXTRATO.get(p.get("qualidade"), p.get("qualidade"))
        elif m := DESCRICAO_PEDIDO.match(r["descricao"] or ""):  # pedido que já saiu do histórico
            videos, duracao, resolucao = int(m["videos"]), int(m["duracao"]), m["resolucao"]
        cobrado, devolvido = int(r["cobrado"]), int(r["devolvido"])
        if int(r.get("videos_cobrados") or 0):  # cobrança por vídeo (ADR 0012): os vídeos entregues da rodada
            videos = int(r["videos_cobrados"])
        return {
            "tipo": "rodada",
            "lote": r["lote_id"],
            "quando": r["quando"],
            "modelo": info["nome"],
            "modelo_id": info["id"],
            "finalidade": info["titulo"],
            "tipo_modelo": info["tipo"],
            "videos": videos,
            "duracao": duracao,
            "resolucao": resolucao,
            "cobrado": cobrado,
            "devolvido": devolvido,
            "liquido": cobrado - devolvido,
            "status": lt["status"] if lt else None,
        }

    def _itens_extrato(
        c: Config, cliente_id: str, desde: float, antes: float, limite: int
    ) -> tuple[list, float | None]:
        rodadas = [_rodada(r) for r in c.banco.extrato_rodadas(cliente_id, desde, antes, limite)]
        creditos = [
            {"tipo": m["tipo"], "quando": m["quando"], "valor": int(m["valor"]), "descricao": m["descricao"]}
            for m in c.banco.extrato_creditos(cliente_id, desde, antes, limite)
        ]
        itens = sorted(rodadas + creditos, key=lambda x: x["quando"], reverse=True)
        proximo = itens[limite - 1]["quando"] if len(itens) > limite else None
        return itens[:limite], proximo

    @app.get("/api/extrato")
    def extrato(dias: int = 30, antes: float | None = None, cli: dict = Depends(cliente), c: Config = Depends(config)):
        """Extrato por rodada: cada pedido com o tipo e o modelo de geração e o total (cobrado − devolvido),
        as recargas e os ajustes, e o resumo do período (com o gasto por modelo). Paginado por `antes`."""
        desde = _periodo(dias)
        itens, proximo = _itens_extrato(c, cli["id"], desde, antes or time.time() + 1, 50)
        resumo = c.banco.extrato_resumo(cli["id"], desde)
        por_modelo = []
        for m in resumo["por_modelo"]:
            info = modelos.info(m["modelo"])
            cobrado, devolvido = int(m["cobrado"] or 0), int(m["devolvido"] or 0)
            por_modelo.append({"modelo": info["nome"], "modelo_id": info["id"], "finalidade": info["titulo"],
                               "tipo_modelo": info["tipo"], "rodadas": int(m["rodadas"]), "cobrado": cobrado,
                               "devolvido": devolvido, "liquido": cobrado - devolvido})  # fmt: skip
        gasto = sum(m["cobrado"] for m in por_modelo)
        devolvido = sum(m["devolvido"] for m in por_modelo)
        return {
            "plano": cli["plano"],
            "dias": dias,
            "resumo": {
                "gasto": gasto,
                "devolvido": devolvido,
                "liquido": gasto - devolvido,
                "rodadas": sum(m["rodadas"] for m in por_modelo),
                "recargas": resumo["recargas"],
                "ajustes": resumo["ajustes"],
                "saldo": resumo["saldo"],
            },
            "por_modelo": sorted(por_modelo, key=lambda m: m["liquido"], reverse=True),
            "itens": itens,
            "proximo": proximo,
        }

    @app.get("/api/extrato.csv")
    def extrato_csv(dias: int = 30, fuso_min: int = 0, cli: dict = Depends(cliente), c: Config = Depends(config)):
        """O extrato do período numa planilha (CSV com ; e vírgula decimal, como o Excel em português abre)."""
        if not -14 * 60 <= fuso_min <= 14 * 60:
            raise HTTPException(422, "fuso inválido")
        itens, _ = _itens_extrato(c, cli["id"], _periodo(dias), time.time() + 1, MAX_LINHAS_CSV)

        def reais_csv(v: int | None) -> str:
            return "" if v is None else f"{v / 100:.2f}".replace(".", ",")

        linhas = [
            "Data;Tipo;Pedido;Tipo de modelo;Modelo de IA;Vídeos;Cobrado (R$);Devolvido (R$);"
            "Total da rodada (R$);Crédito (R$);Descrição"
        ]
        for x in itens:
            quando = time.strftime("%d/%m/%Y %H:%M", time.gmtime(x["quando"] + fuso_min * 60))
            if x["tipo"] == "rodada":
                desc = f"{x['videos']} vídeo(s) de {x['duracao']} s em {x['resolucao']}" if x["videos"] else ""
                campos = [
                    quando, "Pedido", x["lote"], x["tipo_modelo"] or "", x["modelo"], str(x["videos"] or ""),
                    reais_csv(x["cobrado"]), reais_csv(x["devolvido"]), reais_csv(x["liquido"]), "", desc,
                ]  # fmt: skip
            else:
                tipo = "Recarga" if x["tipo"] == "recarga" else "Ajuste"
                campos = [quando, tipo, "", "", "", "", "", "", "", reais_csv(x["valor"]), x["descricao"] or ""]
            linhas.append(";".join(_celula_csv(v) for v in campos))
        corpo = "\ufeff" + "\r\n".join(linhas) + "\r\n"  # BOM: o Excel reconhece o UTF-8 (acentos)
        nome = f"render2go-extrato-{time.strftime('%Y-%m-%d')}.csv"
        return Response(
            corpo.encode(), media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{nome}"', "Cache-Control": "no-store"},
        )  # fmt: skip

    @app.get("/api/geracoes")
    def geracoes(horas: int = 24, cli: dict = Depends(cliente), c: Config = Depends(config)):
        """Logs de geração: os vídeos prontos (e não excluídos) das últimas `horas`, com o modelo que gerou,
        a hora e os links (assistir e baixar, assinados por 24 h)."""
        if not 1 <= horas <= 7 * 24:
            raise HTTPException(422, "de 1 a 168 horas")
        itens = []
        for j in c.banco.geracoes(cli["id"], time.time() - horas * 3600):
            p = json.loads(j["pedido"])
            info = modelos.info(j["modelo"] or p.get("modelo"))
            chave = chave_saida(cli["id"], j["lote_id"], j["arquivo"])
            itens.append(
                {
                    "id": j["id"],
                    "arquivo": j["arquivo"],
                    "lote": j["lote_id"],
                    "gerado_em": j["gerado_em"],
                    "modelo": info["nome"],
                    "finalidade": info["titulo"],
                    "tipo_modelo": info["tipo"],
                    "duracao": p.get("duracao"),
                    "resolucao": RESOLUCAO_EXTRATO.get(p.get("qualidade"), p.get("qualidade")),
                    "texto": j["texto"] or p.get("prompt"),
                    "texto_proprio": j["texto"] is not None,
                    "url": c.armazenamento.url("GET", chave, URL_DOWNLOAD_S),
                    "baixar": c.armazenamento.url(
                        "GET",
                        chave,
                        URL_DOWNLOAD_S,
                        extra={"response-content-disposition": f'attachment; filename="{j["arquivo"]}"'},
                    ),  # fmt: skip
                }
            )
        return {"horas": horas, "itens": itens}

    @app.post("/api/geracoes/{job_id}/excluir")
    def excluir_geracao(job_id: str, request: Request, cli: dict = Depends(cliente), c: Config = Depends(config)):
        """O cliente exclui um vídeo pronto: o arquivo (e a folha de conferência) sai do armazenamento."""
        j = c.banco.excluir_video(job_id[:80], cli["id"])
        if j is None:
            raise HTTPException(404, "vídeo não encontrado")
        stem = Path(j["arquivo"]).stem
        for nome in (j["arquivo"], f"conferencia/{stem}.jpg"):
            with contextlib.suppress(Exception):  # o armazenamento fora do ar não desfaz a exclusão
                c.armazenamento.apagar(chave_saida(cli["id"], j["lote_id"], nome))
        c.banco.registrar_acesso(
            cli["id"], "video_excluido", origem_de(request), {"lote": j["lote_id"], "arquivo": j["arquivo"]}
        )
        return {"ok": True}

    @app.get("/api/lotes/{lote_id}")
    def ver_lote(lote_id: str, cli: dict = Depends(cliente), c: Config = Depends(config)):
        lt = c.banco.lote(lote_id, cli["id"])
        if lt is None:
            raise HTTPException(404, "lote não encontrado")
        return _resumo(lt, c, cli["id"])

    # -- pagamentos (ADR 0008) --------------------------------------------------------------

    def _conferir_pagamento(c: Config, p: dict) -> dict:
        """Pergunta ao Mercado Pago e aplica a resposta (crédito, recusa, devolução). Só o que a API
        dele diz vale: o aviso (webhook) é só o gatilho."""
        if c.mp is None or not p.get("provedor_id"):
            return p
        try:
            info = c.provedor_pagamento(c.mp).consultar(p["provedor_id"])
        except pagamentos.PagamentoError as e:
            print(f"[pagamentos] AVISO: conferir {p['id']}: {e}", flush=True)
            c.banco.marcar_conferido(p["id"])
            return c.banco.pagamento(p["id"]) or p
        _aplicar_pagamento(c, p, info)
        return c.banco.pagamento(p["id"]) or p

    def _aplicar_pagamento(c: Config, p: dict, info: dict) -> None:
        if info["id"] != str(p["provedor_id"]) or (info.get("referencia") not in (None, p["id"])):
            print(f"[pagamentos] AVISO: {p['id']}: resposta de outro pagamento ({info['id']})", flush=True)
            return
        if info["status"] == "aprovado":
            if info.get("moeda") not in (None, "BRL") or info.get("valor") != p["valor"]:
                c.banco.atualizar_pagamento(p["id"], "divergente", info)  # o painel mostra para conferir
                print(f"[pagamentos] ALERTA: {p['id']}: valor ou moeda diferente do pedido: {info}", flush=True)
                return
            try:
                if c.banco.creditar_pagamento(p["id"], info["valor"], info["id"], info):
                    c.banco.registrar_acesso(
                        p["cliente_id"], "recarga", {}, {"pagamento": p["id"], "valor": p["valor"]}
                    )
            except BancoError as e:
                print(f"[pagamentos] ALERTA: {p['id']}: {e}", flush=True)
        elif info["status"] in ("devolvido", "contestado"):
            if p["creditado_em"]:
                c.banco.estornar_pagamento(p["id"], info["status"], info)
            else:
                c.banco.atualizar_pagamento(p["id"], info["status"], info)
        else:
            c.banco.atualizar_pagamento(p["id"], info["status"], info)

    @app.post("/api/pagamentos/pix")
    def criar_pix(body: PixIn, request: Request, cli: dict = Depends(cliente), c: Config = Depends(config)):
        _exigir_aceite(cli)
        if c.mp is None:
            raise HTTPException(503, "a recarga por Pix ainda não está disponível; fale com o suporte")
        if cli["plano"] == "interno":
            raise HTTPException(422, "o plano interno não usa saldo")
        if not cli.get("email"):
            raise HTTPException(422, "a recarga precisa de um e-mail na conta; fale com o suporte")
        try:
            valor = precos.centavos(body.valor)
            doc = pagamentos.documento(body.documento)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        if not c.mp.minimo <= valor <= c.mp.maximo:
            raise HTTPException(422, f"a recarga vai de {precos.reais(c.mp.minimo)} a {precos.reais(c.mp.maximo)}")
        if c.banco.pagamentos_pendentes(cli["id"]) >= 3:
            raise HTTPException(429, "você já tem Pix aguardando pagamento; pague um deles ou espere vencer")
        if c.banco.contar(f"pix:{cli['id']}", 3600) > 10:
            raise HTTPException(429, "muitas recargas pedidas; tente de novo em uma hora")
        pid = c.banco.criar_pagamento(cli["id"], valor, "mercadopago", pagamentos.mascarar(*doc))
        try:
            r = c.provedor_pagamento(c.mp).criar_pix(pid, valor, cli["email"], doc)
        except pagamentos.PagamentoError as e:
            c.banco.atualizar_pagamento(pid, "erro", {"erro": str(e)[:300]})
            print(f"[pagamentos] AVISO: {pid}: {e}", flush=True)
            raise HTTPException(502, "não foi possível gerar o Pix agora; tente de novo em instantes") from e
        c.banco.pagamento_criado(pid, r["id"], r["status"], r["expira"], r["copia_cola"], r["qr_base64"])
        c.banco.registrar_acesso(cli["id"], "pix", origem_de(request), {"pagamento": pid, "valor": valor})
        return _resumo_pagamento(c.banco.pagamento(pid))

    @app.get("/api/pagamentos")
    def meus_pagamentos(cli: dict = Depends(cliente), c: Config = Depends(config)):
        return [_resumo_pagamento(p) for p in c.banco.pagamentos(cli["id"], 20)]

    @app.get("/api/pagamentos/{pid}")
    def ver_pagamento(pid: str, cli: dict = Depends(cliente), c: Config = Depends(config)):
        p = c.banco.pagamento(pid, cli["id"])
        if p is None:
            raise HTTPException(404, "recarga não encontrada")
        recente = time.time() - float(p["conferido_em"] or p["criado"]) < pagamentos.CONFERIR_A_CADA_S
        if p["status"] in ("criando", "pendente") and not recente:  # sem o aviso, confere direto
            p = _conferir_pagamento(c, p)
        return _resumo_pagamento(p)

    @app.post("/api/pagamentos/mercadopago")
    def aviso_mercadopago(request: Request, c: Config = Depends(config)):
        """Aviso de pagamento do Mercado Pago: confere a assinatura e consulta o pagamento na API dele."""
        if c.mp is None:
            raise HTTPException(404, "Not Found")
        q = request.query_params
        data_id = q.get("data.id") or q.get("id")
        if not pagamentos.assinatura_confere(
            c.mp.segredo_webhook, request.headers.get("x-signature", ""), request.headers.get("x-request-id"), data_id
        ):
            _contar_falha(c, origem_de(request), "webhook", 600, 30)
            raise HTTPException(401, "assinatura inválida")
        if (q.get("type") or q.get("topic")) != "payment" or not data_id:
            return {"ok": True}
        try:
            info = c.provedor_pagamento(c.mp).consultar(data_id)
        except pagamentos.PagamentoError as e:  # o Mercado Pago repete o aviso depois
            raise HTTPException(502, "não foi possível consultar o pagamento agora") from e
        p = c.banco.pagamento(info["referencia"]) if info.get("referencia") else None
        if p is None or p["provedor"] != "mercadopago":
            return {"ok": True, "ignorado": True}
        if not p["provedor_id"]:  # o aviso chegou antes de a criação terminar de gravar
            return JSONResponse({"detail": "tente de novo"}, status_code=409)
        _aplicar_pagamento(c, p, info)
        return {"ok": True}

    # -- painel (ADR 0004 e 0007) ----------------------------------------------------------

    rotas = APIRouter()

    def _admin_rede(request: Request, c: Config) -> None:
        if not contas.ip_permitido(ip_de(request), c.admin_ips):
            raise HTTPException(404, "Not Found")

    def _sessao_painel(request: Request, c: Config) -> dict | None:
        """A sessão do painel deste cookie, com quem é; a da chave do servidor cai se a chave saiu do .env."""
        tok = request.cookies.get(nome_cookie(c, "admin"))
        s = c.banco.sessao_painel(tok) if tok and len(tok) <= 200 else None
        if s is not None and s["contexto"].get("emergencia") and not c.admin_tokens:
            return None
        return s

    def admin(request: Request, c: Config = Depends(config)) -> str:
        """Sessão do painel + o papel da pessoa no mapa de permissões (PERMISSOES_PAINEL). Rota fora do mapa é
        recusada (fail-closed): rota nova só funciona depois de dizer quem pode usá-la."""
        _admin_rede(request, c)
        s = _sessao_painel(request, c)
        if s is None:
            if request.cookies.get(nome_cookie(c, "admin")):
                _contar_falha(c, origem_de(request), "painel-sessao", 600, 30)
            raise HTTPException(401, "sessão do painel ausente ou vencida")
        if request.method not in ("GET", "HEAD") and request.headers.get(CABECALHO_PAGINA) != "1":
            raise HTTPException(403, "pedido sem o cabeçalho da página")
        papel = s["pessoa"]["papel"] if s["pessoa"] else "dono"  # a chave do servidor é o acesso de emergência
        rota = request.scope.get("route")
        caminho = getattr(rota, "path", "")
        caminho = caminho[len(painel) :] if painel and caminho.startswith(painel) else ""
        if papel not in PERMISSOES_PAINEL.get((request.method, caminho), ()):
            raise HTTPException(403, "o seu papel no painel não permite isto")
        request.state.painel = {"quem": s["admin"], "papel": papel, "emergencia": s["pessoa"] is None}
        return s["admin"]

    def _auditar(c: Config, request: Request, quem: str, acao: str, alvo: str | None = None, detalhe: Any = None):
        c.banco.auditar(quem, acao, alvo, detalhe, ip_de(request), request.headers.get("user-agent"))

    @rotas.get("", include_in_schema=False)
    def painel_sem_barra(request: Request, c: Config = Depends(config)):
        _admin_rede(request, c)
        return RedirectResponse(f"{c.admin_caminho}/", status_code=308)

    @rotas.get("/", include_in_schema=False)
    def pagina_painel(request: Request, c: Config = Depends(config)):
        _admin_rede(request, c)
        return servir(request, web["painel.html"], "no-store")

    @rotas.get("/painel.js", include_in_schema=False)
    def js_painel(request: Request, v: str | None = None, c: Config = Depends(config)):
        _admin_rede(request, c)
        arq = web["privado/painel.js"]
        return servir(request, arq, "private, max-age=31536000, immutable" if v == arq.versao else "no-store")

    def _abrir_painel(c: Config, origem: dict, quem: str, papel: str, contexto: dict | None = None) -> JSONResponse:
        token = c.banco.criar_sessao("admin", contas.SESSAO_ADMIN_S, origem, admin=quem, contexto=contexto)
        resp = JSONResponse({"ok": True, "admin": quem, "papel": papel})
        definir_cookie(resp, c, "admin", token, contas.SESSAO_ADMIN_S)
        return resp

    def _recusar_entrada(c: Config, origem: dict, chaves: list[str], detalhe: dict) -> None:
        for chave in chaves:
            c.banco.contar(chave, 900)
        c.banco.auditar("?", "entrada_recusada", None, detalhe, origem["ip"], origem["agente"])
        raise HTTPException(401, "usuário, senha ou código do autenticador inválidos")

    @rotas.post("/api/entrar")
    def admin_entrar(body: AdminEntrarIn, request: Request, c: Config = Depends(config)):
        """Pessoa: nome de acesso + senha + código do aplicativo dela (cada código vale uma vez). Emergência: a chave
        do servidor (RENDER2GO_ADMIN_TOKENS) + o código do RENDER2GO_ADMIN_TOTP. A mesma mensagem para qualquer erro;
        5 erros bloqueiam a rede e o nome de acesso por 15 min, mesmo com tudo certo depois."""
        _admin_rede(request, c)
        origem = origem_de(request)
        rede = f"falha:painel:{origem['rede']}"
        if c.banco.contar(rede, 900, somar=False) >= 5 or c.banco.contar(f"painel:{origem['rede']}", 900) > 10:
            raise HTTPException(429, "muitas tentativas; espere 15 minutos")
        codigo = re.sub(r"\s", "", body.totp or "")
        if body.token is None:  # pessoa
            usuario = (body.usuario or "").strip().lower()[:64]
            por_nome = f"falha:painel-usuario:{hashlib.sha256(usuario.encode()).hexdigest()[:24]}"
            if c.banco.contar(por_nome, 900, somar=False) >= 5:
                raise HTTPException(429, "muitas tentativas; espere 15 minutos")
            a = c.banco.admin_por_usuario(usuario) if contas.USUARIO_ADMIN.match(usuario) else None
            a = a if a and a["ativo"] else None
            # a senha é conferida sempre (sem a pessoa, contra um resumo de mentira): o tempo não diz quem existe
            ok = contas.senha_confere(body.senha or "", a["senha_hash"] if a else None)
            if ok and a:
                try:
                    segredo = contas.decifrar(c.chave_segredos, a["totp_segredo"], f"admin:{a['id']}")
                except ValueError:
                    print(f"[painel] ALERTA: o segredo do autenticador de {usuario} não abre", flush=True)
                    segredo = None
                contador = contas.totp_confere(segredo, codigo) if segredo else None
                ok = contador is not None and c.banco.admin_codigo_usado(a["id"], contador)
            if not ok or not a:
                _recusar_entrada(c, origem, [rede, por_nome], {"modo": "pessoa", "usuario": usuario[:32]})
            c.banco.auditar(a["usuario"], "entrada", None, {"modo": "pessoa", "papel": a["papel"]}, origem["ip"],
                            origem["agente"])  # fmt: skip
            return _abrir_painel(c, origem, a["usuario"], a["papel"])
        # chave do servidor (emergência)
        ok = _um_de(body.token.strip(), c.admin_tokens)
        if ok and c.admin_totp:
            contador = contas.totp_confere(c.admin_totp, codigo)
            ok = contador is not None and c.banco.usar_contador_estado("totp_ultimo", contador)
        if not ok:
            _recusar_entrada(c, origem, [rede], {"modo": "chave", "totp": bool(c.admin_totp)})
        quem = "admin-" + hashlib.sha256(body.token.strip().encode()).hexdigest()[:8]
        c.banco.auditar(quem, "entrada_emergencia", None, {"modo": "chave", "totp": bool(c.admin_totp)}, origem["ip"],
                        origem["agente"])  # fmt: skip
        return _abrir_painel(c, origem, quem, "dono", {"emergencia": True})

    # -- primeiro acesso por convite: QR Code do aplicativo → um código dele → a senha --------------------

    def _convite_valido(c: Config, request: Request, token: str) -> tuple[dict, dict]:
        _admin_rede(request, c)
        origem = origem_de(request)
        rede = f"falha:convite:{origem['rede']}"
        if c.banco.contar(rede, 900, somar=False) >= 10 or c.banco.contar(f"convite:{origem['rede']}", 900) > 40:
            raise HTTPException(429, "muitas tentativas; espere 15 minutos")
        conv = c.banco.convite(token) if len(token) <= 200 else None
        if conv is None:
            c.banco.contar(rede, 900)
            raise HTTPException(401, "convite inválido, já usado ou vencido; peça outro a quem convidou você")
        return conv, origem

    @rotas.post("/api/convite/iniciar")
    def convite_iniciar(body: ConviteIn, request: Request, c: Config = Depends(config)):
        """1º passo: o segredo novo do aplicativo autenticador (só desta pessoa), com o QR Code."""
        conv, origem = _convite_valido(c, request, body.convite)
        segredo = contas.novo_segredo_totp()
        cifrado = contas.cifrar(c.chave_segredos, segredo, f"convite:{conv['id']}")
        if not c.banco.convite_segredo(conv["id"], cifrado, origem["ip"]):
            raise HTTPException(401, "convite inválido, já usado ou vencido; peça outro a quem convidou você")
        c.banco.auditar(conv["usuario"], "convite_aberto", conv["id"], {"papel": conv["papel"]}, origem["ip"],
                        origem["agente"])  # fmt: skip
        uri = contas.uri_totp(segredo, f"painel:{conv['usuario']}")
        return {"usuario": conv["usuario"], "papel": conv["papel"], "expira": conv["expira"], "segredo": segredo,
                "uri": uri, "qr_svg": contas.qr_svg(uri)}  # fmt: skip

    @rotas.post("/api/convite/confirmar")
    def convite_confirmar(body: ConviteIn, request: Request, c: Config = Depends(config)):
        """2º passo: um código do aplicativo confere com o segredo novo. Só então a senha pode ser criada."""
        conv, _ = _convite_valido(c, request, body.convite)
        erros = f"convite:erro:{conv['id']}"
        _limite_erros(c, erros, "muitos códigos errados; espere 15 minutos")
        if not conv["totp_pendente"]:
            raise HTTPException(409, "leia o QR Code primeiro")
        try:
            segredo = contas.decifrar(c.chave_segredos, conv["totp_pendente"], f"convite:{conv['id']}")
        except ValueError as e:
            raise HTTPException(409, "leia o QR Code de novo") from e
        contador = contas.totp_confere(segredo, re.sub(r"\s", "", body.codigo or ""))
        if contador is None:
            _errou(c, erros)
            raise HTTPException(401, "o código não confere: confira se o aplicativo leu o QR Code desta tela")
        if not c.banco.convite_confirmado(conv["id"], conv["totp_pendente"], contador):
            raise HTTPException(409, "o QR Code mudou em outra aba; leia de novo")
        return {"ok": True, "usuario": conv["usuario"]}

    @rotas.post("/api/convite/senha")
    def convite_senha(body: ConviteIn, request: Request, c: Config = Depends(config)):
        """3º passo: a senha (só depois do código do aplicativo). O convite é gasto e a sessão abre."""
        conv, origem = _convite_valido(c, request, body.convite)
        if not conv["totp_confirmado_em"] or float(conv["totp_confirmado_em"]) < time.time() - CONVITE_SENHA_S:
            raise HTTPException(409, "confirme antes um código do aplicativo autenticador")
        senha = body.senha or ""
        if motivo := contas.senha_fraca(senha, conv["usuario"]):
            raise HTTPException(422, motivo)
        existente = c.banco.admin_por_usuario(conv["usuario"])
        aid = existente["id"] if existente else novo_id("adm")
        try:
            segredo = contas.decifrar(c.chave_segredos, conv["totp_pendente"], f"convite:{conv['id']}")
            pessoa = c.banco.usar_convite(
                conv["id"],
                aid,
                contas.hash_senha(senha),
                contas.cifrar(c.chave_segredos, segredo, f"admin:{aid}"),
                int(conv["totp_contador"]),
                CONVITE_SENHA_S,
            )
        except (ValueError, BancoError) as e:
            raise HTTPException(409, "convite já usado, cancelado ou vencido; peça outro") from e
        detalhe = {"papel": pessoa["papel"], "novo": pessoa["novo"]}
        c.banco.auditar(pessoa["usuario"], "convite_usado", conv["id"], detalhe, origem["ip"], origem["agente"])
        c.banco.auditar(pessoa["usuario"], "entrada", None, {"modo": "convite", "papel": pessoa["papel"]},
                        origem["ip"], origem["agente"])  # fmt: skip
        return _abrir_painel(c, origem, pessoa["usuario"], pessoa["papel"])

    @rotas.post("/api/sair")
    def admin_sair(request: Request, c: Config = Depends(config)):
        tok = request.cookies.get(nome_cookie(c, "admin"))
        if tok:
            c.banco.revogar_sessao(tok)
        resp = JSONResponse({"ok": True})
        apagar_cookie(resp, c, "admin")
        return resp

    @rotas.get("/api/sessao")
    def admin_sessao(request: Request, c: Config = Depends(config)):
        """A página consulta ao abrir; sem sessão a resposta é `admin: null` (não é erro). Com sessão: quem, o papel
        e o que o papel abre (a página mostra só essas seções; o servidor confere de novo em cada rota)."""
        _admin_rede(request, c)
        s = _sessao_painel(request, c)
        if s is None:
            return {"admin": None}
        papel = s["pessoa"]["papel"] if s["pessoa"] else "dono"
        return {
            "admin": s["admin"],
            "papel": papel,
            "emergencia": s["pessoa"] is None,
            "totp": c.admin_totp is not None,
            "rotas": sorted(f"{m} {p}" for (m, p), papeis in PERMISSOES_PAINEL.items() if papel in papeis),
        }

    # -- acessos do painel por pessoa (ADR 0009) e auditoria -------------------------------------------

    @rotas.get("/api/acessos")
    def acessos(_: str = Depends(admin), c: Config = Depends(config)):
        """Quem tem acesso (sem senha nem segredo), os convites que ainda valem e se a chave de emergência existe."""
        return {
            "pessoas": c.banco.admins(),
            "convites": c.banco.convites_pendentes(),
            "chave_emergencia": bool(c.admin_tokens),
            "papeis": PAPEIS_PAINEL,
        }

    @rotas.post("/api/acessos/convites")
    def novo_convite(body: NovoConviteIn, request: Request, quem: str = Depends(admin), c: Config = Depends(config)):
        """Convite de uso único (24 h) para um nome de acesso. Nome que já existe: refaz a senha e o autenticador
        dessa pessoa quando ela usar (e reativa, se estava desativada)."""
        try:
            usuario = contas.normalizar_usuario(body.usuario)
        except ValueError as e:
            raise HTTPException(422, f"nome de acesso: {e}") from e
        existente = c.banco.admin_por_usuario(usuario)
        try:
            cid, token = c.banco.criar_convite(usuario, body.papel, quem, contas.CONVITE_VALIDADE_S)
        except BancoError as e:
            raise HTTPException(409, str(e)) from e
        antes = {"papel": existente["papel"], "ativo": bool(existente["ativo"])} if existente else None
        _auditar(c, request, quem, "convite_criado", usuario, {"convite": cid, "antes": antes,
                 "depois": {"papel": body.papel, "ativo": True}})  # fmt: skip
        expira = time.time() + contas.CONVITE_VALIDADE_S
        return {"convite": cid, "usuario": usuario, "papel": body.papel, "refaz": existente is not None,
                "expira": expira, "endereco": f"{painel}/#convite={token}"}  # fmt: skip

    @rotas.post("/api/acessos/convites/{cid}/cancelar")
    def cancelar_convite(cid: str, request: Request, quem: str = Depends(admin), c: Config = Depends(config)):
        conv = c.banco.cancelar_convite(cid[:80])
        if conv is None:
            raise HTTPException(404, "convite não encontrado, já usado ou cancelado")
        _auditar(c, request, quem, "convite_cancelado", conv["usuario"], {"convite": cid})
        return {"ok": True}

    def _pessoa(c: Config, aid: str) -> dict:
        a = c.banco.admin(aid[:80])
        if a is None:
            raise HTTPException(404, "acesso não encontrado")
        return a

    @rotas.post("/api/acessos/{aid}/desativar")
    def desativar_acesso(aid: str, request: Request, quem: str = Depends(admin), c: Config = Depends(config)):
        """Tira o acesso de uma pessoa (as sessões dela caem na hora); para voltar, um convite novo."""
        a = _pessoa(c, aid)
        if a["usuario"] == quem:
            raise HTTPException(409, "você não pode tirar o próprio acesso")
        try:
            c.banco.desativar_admin(a["id"])
        except BancoError as e:
            raise HTTPException(409, str(e)) from e
        _auditar(c, request, quem, "acesso_desativado", a["usuario"], {"antes": {"ativo": True},
                 "depois": {"ativo": False}})  # fmt: skip
        return {"ok": True}

    @rotas.post("/api/acessos/{aid}/papel")
    def mudar_papel(aid: str, body: PapelIn, request: Request, quem: str = Depends(admin), c: Config = Depends(config)):
        a = _pessoa(c, aid)
        try:
            antes = c.banco.mudar_papel_admin(a["id"], body.papel)
        except BancoError as e:
            raise HTTPException(409, str(e)) from e
        _auditar(c, request, quem, "papel", a["usuario"], {"antes": {"papel": antes["papel"]},
                 "depois": {"papel": body.papel}})  # fmt: skip
        return {"ok": True, "papel": body.papel}

    def _filtros_auditoria(pessoa, acao, alvo, desde, ate) -> dict:
        return {"admin": (pessoa or "")[:120] or None, "acao": (acao or "")[:60] or None,
                "alvo": (alvo or "")[:120] or None, "desde": desde, "ate": ate}  # fmt: skip

    @rotas.get("/api/auditoria")
    def ver_auditoria(
        pessoa: str | None = None,
        acao: str | None = None,
        alvo: str | None = None,
        desde: float | None = None,
        ate: float | None = None,
        _: str = Depends(admin),
        c: Config = Depends(config),
    ):
        """O que foi feito no painel (quem, o quê, em quem, quando, de onde e de qual navegador), com filtros e a
        página seguinte por `ate` (o `proximo` da resposta). Cada mudança traz "antes" e "depois"."""
        itens = c.banco.auditoria_filtrada(**_filtros_auditoria(pessoa, acao, alvo, desde, ate), limite=101)
        proximo = itens[99]["quando"] if len(itens) > 100 else None
        for i in itens:
            with contextlib.suppress(ValueError, TypeError):
                i["detalhe"] = json.loads(i["detalhe"]) if i["detalhe"] else None
        return {"itens": itens[:100], "proximo": proximo, **c.banco.opcoes_auditoria()}

    @rotas.get("/api/auditoria.csv")
    def auditoria_csv(
        request: Request,
        pessoa: str | None = None,
        acao: str | None = None,
        alvo: str | None = None,
        desde: float | None = None,
        ate: float | None = None,
        fuso_min: int = 0,
        quem: str = Depends(admin),
        c: Config = Depends(config),
    ):
        """A auditoria filtrada numa planilha (até 10 mil linhas; CSV com ; como o Excel em português abre)."""
        if not -14 * 60 <= fuso_min <= 14 * 60:
            raise HTTPException(422, "fuso inválido")
        filtros = _filtros_auditoria(pessoa, acao, alvo, desde, ate)
        itens = c.banco.auditoria_filtrada(**filtros, limite=10_000)
        linhas = ["Data;Pessoa;Ação;Alvo;Antes;Depois;Detalhe;IP;Navegador"]
        for i in itens:
            detalhe = {}
            with contextlib.suppress(ValueError, TypeError):
                detalhe = json.loads(i["detalhe"]) if i["detalhe"] else {}
            antes, depois = detalhe.pop("antes", None), detalhe.pop("depois", None)

            def js(v):
                return "" if v in (None, {}) else json.dumps(v, ensure_ascii=False)

            quando = time.strftime("%d/%m/%Y %H:%M:%S", time.gmtime(i["quando"] + fuso_min * 60))
            campos = [quando, i["admin"], i["acao"], i["alvo"] or "", js(antes), js(depois), js(detalhe),
                      i["ip"] or "", i.get("agente") or ""]  # fmt: skip
            linhas.append(";".join(_celula_csv(v) for v in campos))
        _auditar(c, request, quem, "auditoria_exportada", None, {k: v for k, v in filtros.items() if v is not None})
        corpo = "﻿" + "\r\n".join(linhas) + "\r\n"
        nome = f"render2go-auditoria-{time.strftime('%Y-%m-%d')}.csv"
        return Response(corpo.encode(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{nome}"'})  # fmt: skip

    @rotas.get("/api/resumo")
    def resumo_painel(request: Request, fuso_min: int = -180, _: str = Depends(admin), c: Config = Depends(config)):
        """Visão geral: o que pede atenção agora (revisões, fila, pods, suporte) e os números de hoje (no fuso da
        página; padrão: Brasília). Valores em dinheiro só para o dono."""
        if not -14 * 60 <= fuso_min <= 14 * 60:
            raise HTTPException(422, "fuso inválido")
        local = time.time() + fuso_min * 60
        r = c.banco.resumo_painel(local - local % 86400 - fuso_min * 60)
        vivo, pausa = c.banco.ler_estado("orquestrador_vivo"), c.banco.ler_estado("pausado")
        r["orquestrador_visto_s"] = round(time.time() - float(vivo["atualizado"]), 1) if vivo else None
        r["pausado"] = bool(pausa and pausa["valor"] == "1")
        if request.state.painel["papel"] == "dono":
            r["avisos"] = avisos_de_configuracao(c)
        else:
            for k in ("cobrado_hoje", "devolvido_hoje", "recargas_hoje", "pix_pendentes"):
                r.pop(k, None)
        return r

    @rotas.get("/api/lotes")
    def pendentes(status: str = "aguardando_aprovacao", _: str = Depends(admin), c: Config = Depends(config)):
        out = []
        for lt in c.banco.lotes(status=status, limite=50):
            fotos = c.banco.fotos(lt["id"])
            if c.analises is not None and any(f["analise"] is None and f["enviada"] for f in fotos):
                c.analises.agendar(lt["id"])  # a API pode ter reiniciado no meio da análise
            cli = c.banco.cliente(lt["cliente_id"]) or {}
            out.append(
                {
                    "lote": lt["id"],
                    "cliente": lt["cliente_id"],
                    "cliente_nome": cli.get("email") or cli.get("nome"),
                    "plano": cli.get("plano"),
                    "ip": lt["ip"],
                    "dispositivo": lt["dispositivo"],
                    "autorizacao": c.banco.autorizacao(lt["id"]),
                    "modelo": modelos.nome(json.loads(lt["pedido"]).get("modelo")),
                    "criado": lt["criado"],
                    "pedido": json.loads(lt["pedido"]),
                    "filtro_c1": filtros.avaliar(json.loads(lt["pedido"])["prompt"]).reason,
                    "analise_ligada": c.analise_ligada,
                    "fotos": [
                        {
                            "nome": f["nome"],
                            "url": c.armazenamento.url("GET", f["chave"], 3600),
                            "analise": json.loads(f["analise"]) if f["analise"] else None,
                            # pedido em sequência: o revisor vê e aprova cada texto próprio
                            "texto": f["prompt"],
                            "filtro_c1": filtros.avaliar(f["prompt"]).reason if f["prompt"] else None,
                        }
                        for f in _amostra_revisao(fotos)
                    ],
                    **_contagem_revisao(fotos),
                }
            )
        return out

    def _amostra_revisao(fotos: list[dict]) -> list[dict]:
        """Pedido grande (12/10/2026): o revisor vê as primeiras fotos e todas as que a análise marcou
        (pessoas, rostos ou erro), até um teto; as contagens do pedido inteiro vêm em _contagem_revisao."""
        if len(fotos) <= PREVIA_REVISAO:
            return fotos
        primeiras = fotos[:PREVIA_REVISAO]
        marcadas = [f for f in fotos[PREVIA_REVISAO:] if _marcada(f)][:MARCADAS_REVISAO]
        return primeiras + marcadas

    def _marcada(f: dict) -> bool:
        a = json.loads(f["analise"]) if f["analise"] else None
        return bool(a) and bool(a.get("erro") or a.get("pessoas") or a.get("rostos"))

    def _contagem_revisao(fotos: list[dict]) -> dict:
        """Totais do pedido inteiro para o revisor (a lista pode ser só uma amostra) e cada texto próprio
        distinto, com o filtro de conteúdo, uma vez só."""
        textos: dict[str, int] = {}
        for f in fotos:
            if f["prompt"]:
                textos[f["prompt"]] = textos.get(f["prompt"], 0) + 1
        analises = [json.loads(f["analise"]) if f["analise"] else None for f in fotos]
        return {
            "fotos_total": len(fotos),
            "fotos_amostra": len(fotos) > PREVIA_REVISAO,
            "fotos_com_pessoas": sum(
                1 for a in analises if a and not a.get("erro") and (a.get("pessoas") or a.get("rostos"))
            ),
            "fotos_analisando": sum(1 for a in analises if a is None),
            "fotos_com_texto": sum(textos.values()),
            "textos": [{"texto": t, "fotos": n, "filtro_c1": filtros.avaliar(t).reason} for t, n in textos.items()],
        }

    @rotas.post("/api/lotes/{lote_id}/aprovar")
    def aprovar(lote_id: str, request: Request, quem: str = Depends(admin), c: Config = Depends(config)):
        r = _aprovar(c, lote_id, quem)
        _auditar(c, request, quem, "aprovar", lote_id)
        return r

    def _aprovar(c: Config, lote_id: str, quem: str) -> dict:
        lt = c.banco.lote(lote_id)
        if lt is None:
            raise HTTPException(404, "lote não encontrado")
        pedido = json.loads(lt["pedido"])
        tarefas = [
            {
                "foto_id": f["id"],
                "variacao": k,
                "seed": lote.seed_de(f["nome"], k),
                "arquivo": f"{f['nome']}-v{k}.mp4",
            }
            for f in c.banco.fotos(lote_id)
            for k in range(1, pedido["variacoes"] + 1)
        ]
        try:
            c.banco.aprovar(lote_id, quem, tarefas)
        except BancoError as e:
            raise HTTPException(409, str(e)) from e
        return {"lote": lote_id, "status": "aprovado", "videos": len(tarefas)}

    @rotas.post("/api/lotes/{lote_id}/recusar")
    def recusar(
        lote_id: str, body: RecusaIn, request: Request, quem: str = Depends(admin), c: Config = Depends(config)
    ):
        try:
            c.banco.recusar(lote_id, quem, body.motivo)
        except BancoError as e:
            raise HTTPException(409, str(e)) from e
        _se_fechou(c, lote_id)
        _auditar(c, request, quem, "recusar", lote_id, {"motivo": body.motivo})
        return {"lote": lote_id, "status": "recusado"}

    @rotas.get("/api/clientes")
    def lista_clientes(busca: str | None = None, _: str = Depends(admin), c: Config = Depends(config)):
        return {
            "clientes": c.banco.clientes(busca=busca),
            "resumo": c.banco.contar_clientes(),
            "precos": precos.PRECOS,
            "planos_cobrados": precos.PLANOS_COBRADOS,
        }

    @rotas.post("/api/clientes")
    def nova_conta(body: ClienteIn, request: Request, quem: str = Depends(admin), c: Config = Depends(config)):
        """Conta criada pelo painel, já com e-mail: a pessoa entra pelo código que chega no e-mail."""
        try:
            email, norm = contas.normalizar(body.email, exigir_dominio=False)  # o painel aceita e-mail de empresa
        except contas.EmailRecusado as e:
            raise HTTPException(422, str(e)) from e
        try:
            cota = 100_000 if body.plano == "interno" else body.cota_fotos_dia
            cid, _ = c.banco.criar_cliente(email, cota, body.plano, email=email, email_norm=norm)
        except BancoError as e:
            raise HTTPException(422, str(e)) from e
        _auditar(c, request, quem, "criar_conta", cid, {"depois": {"email": email, "plano": body.plano}})
        return {"cliente": cid, "email": email, "plano": body.plano}

    @rotas.post("/api/clientes/{cliente_id}/saldo")
    def adicionar_saldo(
        cliente_id: str, body: SaldoIn, request: Request, quem: str = Depends(admin), c: Config = Depends(config)
    ):
        try:
            valor = precos.centavos(body.valor)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        if not 0 < abs(valor) <= precos.MAX_MOVIMENTO:
            raise HTTPException(422, f"valor de R$ 0,01 a {precos.reais(precos.MAX_MOVIMENTO)} (negativo desconta)")
        cli = c.banco.cliente(cliente_id)
        if cli is None:
            raise HTTPException(404, "cliente não encontrado")
        if cli["plano"] not in precos.PLANOS_COBRADOS:
            raise HTTPException(422, f"o plano {cli['plano']} não usa saldo")
        try:
            ref = f"pagamento:{body.referencia.strip()}" if (body.referencia or "").strip() else None
            saldo = c.banco.movimentar(cliente_id, valor, body.descricao.strip(), quem, ref)
        except BancoError as e:
            raise HTTPException(422, str(e)) from e
        _auditar(c, request, quem, "saldo", cliente_id, {"valor": valor, "descricao": body.descricao, "ref": ref,
                 "antes": {"saldo": saldo - valor}, "depois": {"saldo": saldo}})  # fmt: skip
        return {"cliente": cliente_id, "saldo": saldo}

    @rotas.get("/api/clientes/{cliente_id}/extrato")
    def extrato_cliente(cliente_id: str, _: str = Depends(admin), c: Config = Depends(config)):
        cli = c.banco.cliente(cliente_id)
        if cli is None:
            raise HTTPException(404, "cliente não encontrado")
        return {**cli, "saldo": c.banco.saldo(cliente_id), "movimentos": c.banco.extrato(cliente_id, 1000)}

    @rotas.post("/api/clientes/{cliente_id}/plano")
    def mudar_plano(
        cliente_id: str, body: PlanoIn, request: Request, quem: str = Depends(admin), c: Config = Depends(config)
    ):
        antes = c.banco.cliente(cliente_id)
        try:
            c.banco.mudar_plano(cliente_id, body.plano)
        except BancoError as e:
            raise HTTPException(422, str(e)) from e
        _auditar(c, request, quem, "plano", cliente_id, {"antes": {"plano": antes["plano"] if antes else None},
                 "depois": {"plano": body.plano}})  # fmt: skip
        return {"cliente": cliente_id, "plano": body.plano}

    @rotas.post("/api/clientes/{cliente_id}/email")
    def ligar_email(
        cliente_id: str, body: EmailIn, request: Request, quem: str = Depends(admin), c: Config = Depends(config)
    ):
        """Cliente antigo (código de acesso): liga um e-mail para ele entrar pelo e-mail."""
        antes = c.banco.cliente(cliente_id)
        try:
            email, norm = contas.normalizar(body.email, exigir_dominio=False)
            c.banco.definir_email(cliente_id, email, norm)
        except (contas.EmailRecusado, BancoError) as e:
            raise HTTPException(422, str(e)) from e
        _auditar(c, request, quem, "email", cliente_id, {"antes": {"email": antes["email"] if antes else None},
                 "depois": {"email": email}})  # fmt: skip
        return {"cliente": cliente_id, "email": email}

    @rotas.post("/api/clientes/{cliente_id}/desativar")
    def desativar(cliente_id: str, request: Request, quem: str = Depends(admin), c: Config = Depends(config)):
        if not c.banco.desativar_cliente(cliente_id, quem):
            raise HTTPException(404, "cliente não encontrado ou já desativado")
        c.banco.revogar_sessoes(cliente_id)  # quem estava conectado cai na hora
        _auditar(c, request, quem, "desativar", cliente_id, {"antes": {"ativo": True}, "depois": {"ativo": False}})
        return {"cliente": cliente_id, "ativo": False}

    @rotas.post("/api/clientes/{cliente_id}/reativar")
    def reativar(cliente_id: str, request: Request, quem: str = Depends(admin), c: Config = Depends(config)):
        if not c.banco.reativar_cliente(cliente_id):
            raise HTTPException(404, "cliente não encontrado, já ativo ou excluído")
        _auditar(c, request, quem, "reativar", cliente_id, {"antes": {"ativo": False}, "depois": {"ativo": True}})
        return {"cliente": cliente_id, "ativo": True}

    @rotas.post("/api/clientes/{cliente_id}/mfa/desligar")
    def desligar_mfa_cliente(
        cliente_id: str, request: Request, quem: str = Depends(admin), c: Config = Depends(config)
    ):
        """Para quem perdeu o celular e os códigos de recuperação (depois de o suporte conferir que é a pessoa):
        a entrada volta a pedir só o código do e-mail, e o e-mail da conta recebe um aviso."""
        cli = c.banco.conta(cliente_id)
        if cli is None or not c.banco.mfa_desligar(cliente_id):
            raise HTTPException(404, "cliente não encontrado ou sem a verificação em duas etapas")
        # no registro da conta, sem IP (não foi um acesso dela); quem fez e de onde ficam na auditoria do painel
        c.banco.registrar_acesso(cliente_id, "mfa_desligado", {}, {"por": "suporte"})
        _auditar(c, request, quem, "mfa_desligado", cliente_id, {"antes": {"mfa": True}, "depois": {"mfa": False}})
        _avisar(c, cli.get("email"), "mfa_suporte")
        return {"cliente": cliente_id, "mfa": False}

    @rotas.get("/api/historico")
    def historico(grupo: str = "todos", _: str = Depends(admin), c: Config = Depends(config)):
        try:
            lotes = c.banco.historico(grupo, limite=100)
        except BancoError as e:
            raise HTTPException(422, str(e)) from e
        return {
            "lotes": [
                {
                    "lote": lt["id"],
                    "cliente": lt["cliente_id"],
                    "cliente_nome": lt["cliente_nome"],
                    "plano": lt["plano"],
                    "cliente_ativo": bool(lt["cliente_ativo"]),
                    "status": lt["status"],
                    "prompt": json.loads(lt["pedido"]).get("prompt", ""),
                    "fotos": lt["n_fotos"],
                    "ip": lt["ip"],
                    "criado": lt["criado"],
                    "aprovado_em": lt["aprovado_em"],
                    "aprovado_por": lt["aprovado_por"],
                    "concluido_em": lt["concluido_em"],
                    "motivo": lt["motivo"],
                    "fotos_apagadas": lt["fotos_apagadas"],
                    "dispositivo": lt["dispositivo"],
                    "fuso": lt["fuso"],
                    "declarante": lt["declarante"],
                    "modelo": modelos.nome(json.loads(lt["pedido"]).get("modelo")),
                    "autorizado_em": lt["autorizado_em"],
                    "prontos": lt["prontos"],
                    "total": lt["total"],
                    "barrados": int(lt.get("barrados") or 0),  # filtro de imagem (ADR 0005): não entregues
                }
                for lt in lotes
            ],
            "ips": c.banco.uso_por_ip(),
            "dispositivos": c.banco.uso_por_dispositivo(),
        }

    @rotas.get("/api/geradores")
    def relatorio_geradores(dias: int = 30, _: str = Depends(admin), c: Config = Depends(config)):
        """Relatório das GPUs (pods) que subiram: identificação, tempo ligado, vídeos e custo estimado."""
        if not 1 <= dias <= 366:
            raise HTTPException(422, "dias: de 1 a 366")
        return c.banco.relatorio_geradores(time.time() - dias * 86400)

    @rotas.get("/api/pods")
    def pods(_: str = Depends(admin), c: Config = Depends(config)):
        """Pods ligados agora, a pausa e o sinal de vida do orquestrador (quem executa os comandos)."""
        vivo = c.banco.ler_estado("orquestrador_vivo")
        pausa = c.banco.ler_estado("pausado")
        runpod = c.banco.ler_estado("runpod")  # saldo, prazo de um pod novo e hibernados (ADR 0011)
        try:
            runpod_info = json.loads(runpod["valor"]) if runpod else None
        except ValueError:
            runpod_info = None
        return {
            "pods": c.banco.geradores_ativos(),
            "pausado": bool(pausa and pausa["valor"] == "1"),
            "pausado_em": pausa["atualizado"] if pausa and pausa["valor"] == "1" else None,
            "orquestrador_visto_s": round(time.time() - float(vivo["atualizado"]), 1) if vivo else None,
            "comandos": c.banco.comandos_recentes(10),
            "runpod": runpod_info,
        }

    @rotas.post("/api/pods/{gerador}/desligar")
    def desligar_pod(gerador: str, request: Request, quem: str = Depends(admin), c: Config = Depends(config)):
        if not any(g["id"] == gerador for g in c.banco.geradores_ativos()):
            raise HTTPException(404, "este pod não está ligado")
        cid = c.banco.comando("desligar_pod", quem, gerador)
        c.banco.revogar_tokens_worker(gerador=gerador)  # o pod deixa de pegar jobs na hora
        _auditar(c, request, quem, "desligar_pod", gerador)
        return {"ok": True, "comando": cid}

    @rotas.post("/api/pods/emergencia")
    def emergencia(request: Request, quem: str = Depends(admin), c: Config = Depends(config)):
        """Parada de emergência: nenhum pod novo sobe (pausa) e todos os ligados são encerrados."""
        c.banco.definir_estado("pausado", "1")
        c.banco.revogar_tokens_worker()  # nenhum pod pega job novo, mesmo antes do orquestrador agir
        cid = c.banco.comando("emergencia", quem)
        _auditar(c, request, quem, "emergencia", None, {"depois": {"pausado": True}})
        return {"ok": True, "comando": cid, "pausado": True}

    @rotas.post("/api/pods/retomar")
    def retomar(request: Request, quem: str = Depends(admin), c: Config = Depends(config)):
        c.banco.definir_estado("pausado", "0")
        cid = c.banco.comando("retomar", quem)
        _auditar(c, request, quem, "retomar", None, {"depois": {"pausado": False}})
        return {"ok": True, "comando": cid, "pausado": False}

    @rotas.get("/api/interesses")
    def interesses(_: str = Depends(admin), c: Config = Depends(config)):
        """ "Tenho interesse": quantos clientes em cada opção (finalidades e recursos em breve) e cada clique."""

        def titulo(i: dict) -> dict:
            return {**i, **modelos.titulo_interesse(i["finalidade"])}

        return {
            "resumo": [titulo(i) for i in c.banco.interesses()],
            "cliques": [titulo(i) for i in c.banco.interesses_detalhados()],
        }

    @rotas.get("/api/suporte")
    def ver_suporte(_: str = Depends(admin), c: Config = Depends(config)):
        return {
            "email_configurado": c.email is not None and bool(c.email.para),
            "mensagens": c.banco.suporte_recente(),
        }

    @rotas.get("/api/lotes/{lote_id}/auditoria")
    def auditoria(lote_id: str, _: str = Depends(admin), c: Config = Depends(config)):
        a = c.banco.auditoria(lote_id)
        if a is None:
            raise HTTPException(404, "lote não encontrado")

        def js(v):
            return json.loads(v) if isinstance(v, str) and v[:1] in "[{" else v

        a["lote"]["pedido"] = js(a["lote"]["pedido"])
        if a["autorizacao"]:
            a["autorizacao"]["fotos"] = js(a["autorizacao"]["fotos"])
            a["autorizacao"]["sinais"] = js(a["autorizacao"].get("sinais"))
        for f in a["fotos"]:
            f["analise"] = js(f["analise"])
        for e in a["eventos"]:
            e["detalhe"] = js(e["detalhe"])
        return a

    @rotas.get("/api/pagamentos")
    def pagamentos_painel(_: str = Depends(admin), c: Config = Depends(config)):
        return {"configurado": c.mp is not None, "pagamentos": c.banco.pagamentos(limite=100)}

    @rotas.post("/api/pagamentos/{pid}/conferir")
    def conferir_pagamento(pid: str, request: Request, quem: str = Depends(admin), c: Config = Depends(config)):
        p = c.banco.pagamento(pid)
        if p is None:
            raise HTTPException(404, "recarga não encontrada")
        p = _conferir_pagamento(c, p)
        _auditar(c, request, quem, "conferir_pagamento", pid, {"status": p["status"]})
        return {"id": pid, "status": p["status"], "creditado_em": p["creditado_em"]}

    @rotas.get("/api/seguranca")
    def seguranca(_: str = Depends(admin), c: Config = Depends(config)):
        """O que está ligado (e o que falta) e as últimas ações do painel."""
        vivo = c.banco.ler_estado("orquestrador_vivo")
        analisador = c.banco.ler_estado("analisador_vivo") if c.analise == "separada" else None
        return {
            "turnstile": c.turnstile is not None,
            "turnstile_desligado": c.turnstile_desligado,
            "email": c.email is not None,
            "suporte_email": c.email is not None and bool(c.email.para),
            "totp": c.admin_totp is not None,
            "admin_ips": len(c.admin_ips),
            "pagamento": c.mp is not None,
            "cookie_seguro": c.cookie_seguro,
            "analise": c.analise,
            "segredo_contas": c.segredo_contas is not None,
            "orquestrador_visto_s": round(time.time() - float(vivo["atualizado"]), 1) if vivo else None,
            "analisador_visto_s": round(time.time() - float(analisador["atualizado"]), 1) if analisador else None,
            "contas": c.banco.contar_clientes(),
            "acessos": {
                "pessoas": sum(1 for a in c.banco.admins() if a["ativo"]),
                "donos": c.banco.contar_donos(),
                "chave_emergencia": bool(c.admin_tokens),
            },
            "avisos": avisos_de_configuracao(c),
            # identificação da empresa nas páginas legais e no rodapé (Decreto 7.962/2013): o que falta no .env
            "empresa_faltando": [v for v in EMPRESA.values() if not os.environ.get(v, "").strip()],
            "auditoria": c.banco.auditoria_admin(50),
        }

    if painel:
        app.include_router(rotas, prefix=painel)

    # -- workers --------------------------------------------------------------------------

    @app.post("/api/worker/reivindicar")
    def reivindicar(body: ReivindicarIn, dono: str = Depends(worker_auth), c: Config = Depends(config)):
        _mesmo_worker(dono, body.worker)
        modelos_pod = [m[:40] for m in body.modelos] if body.modelos else None
        job = c.banco.reivindicar(body.worker, tipos=body.tipos, modelos=modelos_pod)
        if job is None:
            return {"job": None, "fila": c.banco.fila()}
        lt = c.banco.lote(job["lote_id"])
        pedido = json.loads(lt["pedido"])
        foto = c.banco.foto(job["foto_id"]) if job["foto_id"] else None
        if job["tipo"] == "compilar" and foto is not None:
            # texto próprio de uma foto: o worker compila esse texto como compilaria o do pedido
            pedido = {**pedido, "prompt": foto["prompt"]}
        out: dict[str, Any] = {
            "job_id": job["id"],
            "tipo": job["tipo"],
            "lote": lt["id"],
            "pedido": pedido,
            "aprovacao": carimbo(c.segredo_aprovacao, lt["id"], lt["aprovado_em"]),
            "aprovado_em": lt["aprovado_em"],
        }
        if job["tipo"] == "render":
            stem = Path(job["arquivo"]).stem
            out |= {
                "prompt_en": foto["prompt_en"] if foto["prompt"] else lt["prompt_en"],
                "arquivo": job["arquivo"],
                "seed": job["seed"],
                "foto_url": c.armazenamento.url("GET", foto["chave"], 3600),
                "foto_ext": TIPOS[foto["tipo"]],
                "video_url": c.armazenamento.url(
                    "PUT",
                    chave_saida(lt["cliente_id"], lt["id"], job["arquivo"]),
                    3600,
                    content_type="video/mp4",
                ),
                "folha_url": c.armazenamento.url(
                    "PUT",
                    chave_saida(lt["cliente_id"], lt["id"], f"conferencia/{stem}.jpg"),
                    3600,
                    content_type="image/jpeg",
                ),
            }
        return {"job": out}

    def _do_worker(job_id: str, worker: str, c: Config) -> dict:
        with c.banco.tx() as cur:
            job = cur.one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        if job is None or job["worker"] != worker or job["status"] != "em_andamento":
            raise HTTPException(409, "job não está com este worker")
        return job

    @app.post("/api/worker/jobs/{job_id}/prompt")
    def prompt(job_id: str, body: PromptIn, dono: str = Depends(worker_auth), c: Config = Depends(config)):
        _mesmo_worker(dono, body.worker)
        job = _do_worker(job_id, body.worker, c)
        d = filtros.avaliar(body.prompt_en)
        if not d.allowed:  # C1 também no prompt compilado
            c.banco.concluir(job_id, body.worker, "erro", erro=d.reason)
            if not job["foto_id"]:  # texto próprio recusado derruba só as fotos com esse texto
                with contextlib.suppress(BancoError):  # já recusado ou já em andamento
                    c.banco.recusar(job["lote_id"], "filtro-c1", d.reason)
            _se_fechou(c, job["lote_id"])
            raise HTTPException(422, d.reason)
        if job["foto_id"]:
            c.banco.definir_prompt_foto(job["foto_id"], body.prompt_en, body.compilador)
        else:
            c.banco.definir_prompt(job["lote_id"], body.prompt_en, body.compilador)
        return {"ok": True}

    @app.post("/api/worker/jobs/{job_id}/concluir")
    def concluir(job_id: str, body: ConcluirIn, dono: str = Depends(worker_auth), c: Config = Depends(config)):
        _mesmo_worker(dono, body.worker)
        job = _do_worker(job_id, body.worker, c)
        status, erro, repetir = body.status, body.erro, body.repetir
        barrado = job["tipo"] == "render" and (
            body.barrado or (status == "ok" and body.nsfw is not None and body.nsfw >= filtro_imagem.LIMIAR)
        )
        if barrado:
            # nudez em algum quadro (ADR 0005): nada chega ao cliente, nem a folha de conferência; o vídeo
            # não é refeito e volta para o saldo como os outros que falham. O registro fica (evento e nota)
            lt = c.banco.lote(job["lote_id"])
            for nome in (job["arquivo"], f"conferencia/{Path(job['arquivo']).stem}.jpg"):
                with contextlib.suppress(Exception):
                    c.armazenamento.apagar(chave_saida(lt["cliente_id"], lt["id"], nome))
            status, repetir = "erro", False
            erro = f"barrado pelo filtro de imagem (nota {body.nsfw or 0:.2f})"
        elif job["tipo"] == "render" and status == "ok":
            # fail-closed: o vídeo precisa estar no armazenamento, com o tamanho informado, a
            # conferência do 1º quadro precisa dizer que ele é dessa foto e a da continuidade, que
            # ele não troca de produto no meio
            lt = c.banco.lote(job["lote_id"])
            chave = chave_saida(lt["cliente_id"], lt["id"], job["arquivo"])
            tamanho = c.armazenamento.tamanho(chave)
            problema = None
            if body.similaridade is None or body.similaridade < conferencia.LIMIAR:
                problema = f"conferência do 1º quadro reprovada ({body.similaridade})"
            elif p := conferencia.problema_continuidade(body.deriva_cor, body.salto_cor):
                problema = p
            elif body.nsfw is None or not body.nsfw_quadros:
                problema = "filtro de imagem ausente: o vídeo não foi conferido"
            elif not body.bytes_video or tamanho != body.bytes_video:
                problema = f"vídeo ausente ou incompleto ({tamanho} de {body.bytes_video} bytes)"
            if problema:
                c.armazenamento.apagar(chave)  # nada que não passou chega ao cliente
                status, erro, repetir = "erro", problema, True
        try:
            r = c.banco.concluir(
                job_id,
                body.worker,
                status,
                segundos=body.segundos,
                aviso=body.aviso,
                erro=erro,
                repetir=repetir,
                similaridade=body.similaridade,
                bytes_video=body.bytes_video,
                deriva_cor=body.deriva_cor,
                salto_cor=body.salto_cor,
                nsfw=body.nsfw,
                nsfw_quadros=body.nsfw_quadros,
                barrado=barrado,
                # vídeo refeito sai de outra seed: a mesma seed repetiria o mesmo defeito
                seed=lote.seed_de(job["seed"], job["id"], job["tentativas"]) if job["tipo"] == "render" else None,
            )
        except BancoError as e:
            raise HTTPException(409, str(e)) from e
        _se_fechou(c, job["lote_id"])
        return {"status": r["status"]}

    def origem_armazenamento() -> str:
        """Só o armazenamento (R2) além da própria API: fotos sobem e vídeos descem de lá."""
        try:
            arm = config().armazenamento  # carrega a configuração na 1ª requisição, se preciso
        except (Exception, SystemExit):  # noqa: BLE001 (sem configuração: CSP só com 'self')
            return ""
        u = urlsplit(getattr(arm, "endpoint", None) or getattr(arm, "base", ""))
        return f"{u.scheme}://{u.netloc}" if u.netloc else ""

    @app.middleware("http")
    async def cabecalhos(request: Request, call_next):
        caminho = request.url.path
        do_painel = bool(painel) and (caminho == painel or caminho.startswith(painel + "/"))
        da_api = caminho.startswith("/api/") or caminho == "/saude" or (do_painel and "/api/" in caminho)
        if da_api and request.method not in ("GET", "HEAD", "OPTIONS") and not mesma_origem(request):
            resp: Response = JSONResponse({"detail": "pedido de outro site recusado"}, status_code=403)
        else:
            resp = await call_next(request)
        arm = origem_armazenamento()
        h = resp.headers
        h["X-Content-Type-Options"] = "nosniff"
        h["Referrer-Policy"] = "no-referrer"
        h["X-Frame-Options"] = "DENY"
        h["Cross-Origin-Opener-Policy"] = "same-origin"
        # imagens da marca podem aparecer em prévias de link de outros sites; o resto, só aqui
        marca = caminho.startswith("/estatico/") and caminho.endswith((".png", ".jpg", ".svg"))
        h["Cross-Origin-Resource-Policy"] = "cross-origin" if marca or caminho == "/favicon.ico" else "same-origin"
        h["Permissions-Policy"] = PERMISSOES
        h["Content-Security-Policy"] = (
            f"default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; manifest-src 'self'; "
            f"img-src 'self' blob: {arm}; media-src {arm}; connect-src 'self' {arm}; frame-src 'self'; "
            "object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; "
            "require-trusted-types-for 'script'; trusted-types 'none'"
        )
        if caminho == "/verificacao":  # a página isolada do Turnstile, só dentro de um quadro do próprio site
            h["Content-Security-Policy"] = CSP_VERIFICACAO
            h["X-Frame-Options"] = "SAMEORIGIN"
            h["X-Robots-Tag"] = "noindex, nofollow"
        if caminho in TELAS_CONTA:
            h["X-Robots-Tag"] = "noindex, nofollow"
        if da_api or do_painel:
            h.setdefault("Cache-Control", "no-store")  # respostas com dados da conta não ficam em cache
            h["X-Robots-Tag"] = "noindex, nofollow"
        return resp

    app.add_middleware(HeadComoGet)
    return app


class HeadComoGet:
    """HEAD nas páginas responde como o GET, sem o corpo: monitores de disponibilidade usam HEAD.
    A API (/api/...) fica de fora: lá só os métodos declarados valem."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "HEAD" or "/api/" in scope["path"]:
            return await self.app(scope, receive, send)

        async def sem_corpo(mensagem):
            if mensagem["type"] == "http.response.body":
                mensagem = {**mensagem, "body": b""}
            await send(mensagem)

        return await self.app(dict(scope, method="GET"), receive, sem_corpo)
