"""Finalidades e modelos de geração que o serviço oferece (a página mostra; o pedido guarda o modelo).

Finalidade = o que o cliente quer criar (ex.: "Foto vira vídeo"); cada uma tem um tipo técnico
(imagem para vídeo, texto para imagem...) e os modelos que a atendem. A página do cliente mostra as
finalidades em blocos antes do pedido: as de `destaque` na tela, todas na janela "Ver todas".
Hoje só "Foto vira vídeo" (Wan 2.2) está disponível; as outras aparecem como "Em breve", com o
botão "Tenho interesse", que fica registrado (tabela `interesses`) e contado no /aprovar.

Modelos `previstos` de uma finalidade só entram aqui com a licença comercial conferida
(docs/12-modelos-e-licencas.md). Para ativar um modelo:
    1. template e contrato em worker/workflows/ e modelos no models.lock.json (ADR);
    2. perfil próprio do compilador (compiler-<perfil>.v<N>.txt) e rodada de fidelidade;
    3. o worker passa a aceitar o id em `render2go_worker.fila.MODELOS`;
    4. o modelo entra em MODELOS com `disponivel: True`, e a finalidade dele também.
O worker recusa (sem repetir) pedido de modelo que ele não conhece: fail-closed.
"""

from __future__ import annotations

PADRAO = "wan2.2-i2v"
FINALIDADE_PADRAO = "imagem-video"

MODELOS = [
    {
        "id": "wan2.2-i2v",
        "nome": "Wan 2.2",
        "descricao": "imagem para vídeo · 14B",
        "finalidade": "imagem-video",
        "melhor_para": (
            "produto parado com câmera suave, ambientes, comida e fachadas; vídeos de 3 a 7 s, em 480p ou 720p"
        ),
        "licenca": "Apache 2.0 (uso comercial liberado)",
        "disponivel": True,
    },
]

# entrada: o que o cliente envia (agrupa a janela "Ver todas"); ícones de servico/web/src/icones.json
ENTRADAS = {"foto": "A partir de uma foto", "texto": "A partir de um texto", "video": "A partir de um vídeo",
            "audio": "A partir de um áudio"}  # fmt: skip

FINALIDADES = [
    {
        "id": "imagem-video",
        "titulo": "Foto vira vídeo",
        "tipo": "Imagem para vídeo",
        "descricao": "Sua foto ganha movimento: produto, ambiente, comida ou fachada.",
        "entrada": "foto",
        "icone": "video",
        "destaque": True,
        "previstos": [],
    },
    {
        "id": "texto-video",
        "titulo": "Vídeo a partir de um texto",
        "tipo": "Texto para vídeo",
        "descricao": "Descreva a cena e receba um vídeo, sem precisar de foto.",
        "entrada": "texto",
        "icone": "texto",
        "destaque": True,
        "previstos": [
            {
                "nome": "Wan 2.2 (texto para vídeo)",
                "melhor_para": "cenas descritas em texto, sem foto; mesma família do modelo em uso",
                "licenca": "Apache 2.0",
            }
        ],
    },
    {
        "id": "imagem-imagem",
        "titulo": "Editar ou melhorar uma foto",
        "tipo": "Imagem para imagem",
        "descricao": "Troque o fundo, a luz ou o estilo da foto do produto.",
        "entrada": "foto",
        "icone": "editar",
        "destaque": True,
        "previstos": [
            {
                "nome": "Qwen-Image-Edit",
                "melhor_para": "trocar fundo, luz e estilo da foto mantendo o produto",
                "licenca": "Apache 2.0",
            }
        ],
    },
    {
        "id": "texto-imagem",
        "titulo": "Imagem a partir de um texto",
        "tipo": "Texto para imagem",
        "descricao": "Crie artes e imagens de divulgação só com uma descrição.",
        "entrada": "texto",
        "icone": "imagens",
        "destaque": True,
        "previstos": [
            {
                "nome": "Qwen-Image",
                "melhor_para": "artes de divulgação, inclusive com texto escrito na imagem",
                "licenca": "Apache 2.0",
            }
        ],
    },
    {
        "id": "audio-video",
        "titulo": "Pessoa falando o seu áudio",
        "tipo": "Áudio para vídeo",
        "descricao": "A foto de uma pessoa (com autorização dela) fala ou canta o seu áudio.",
        "entrada": "audio",
        "icone": "pessoa",
        "destaque": True,
        "previstos": [
            {
                "nome": "Wan 2.2 S2V",
                "melhor_para": "pessoa falando ou cantando o áudio enviado (com autorização dela)",
                "licenca": "Apache 2.0",
            }
        ],
    },
    {
        "id": "video-video",
        "titulo": "Mudar o estilo de um vídeo",
        "tipo": "Vídeo para vídeo",
        "descricao": "Transforme um vídeo seu em outro estilo ou anime um personagem com ele.",
        "entrada": "video",
        "icone": "camadas",
        "destaque": True,
        "previstos": [
            {
                "nome": "Wan 2.2 Animate",
                "melhor_para": "animar um personagem com o movimento de um vídeo",
                "licenca": "Apache 2.0",
            }
        ],
    },
    {
        "id": "ampliar",
        "titulo": "Aumentar a resolução",
        "tipo": "Ampliação de imagem e vídeo",
        "descricao": "Deixe uma foto ou um vídeo pequeno mais nítido e maior.",
        "entrada": "foto",
        "icone": "ampliar",
        "destaque": False,
        "previstos": [
            {
                "nome": "SeedVR2",
                "melhor_para": "deixar imagens e vídeos pequenos maiores e mais nítidos",
                "licenca": "Apache 2.0",
            }
        ],
    },
    {
        "id": "imagem-3d",
        "titulo": "Objeto 3D a partir de uma foto",
        "tipo": "Imagem para 3D",
        "descricao": "Um modelo 3D do produto para girar na loja virtual.",
        "entrada": "foto",
        "icone": "cubo",
        "destaque": False,
        "previstos": [{"nome": "TRELLIS", "melhor_para": "objeto 3D a partir da foto do produto", "licenca": "MIT"}],
    },
    {
        "id": "texto-voz",
        "titulo": "Locução a partir de um texto",
        "tipo": "Texto para voz",
        "descricao": "Uma voz lê o seu texto para usar no vídeo.",
        "entrada": "texto",
        "icone": "audio",
        "destaque": False,
        "previstos": [
            {"nome": "Chatterbox Multilingual", "melhor_para": "locução em português do Brasil", "licenca": "MIT"}
        ],
    },
    {
        "id": "texto-musica",
        "titulo": "Trilha sonora a partir de um texto",
        "tipo": "Texto para música",
        "descricao": "Uma música de fundo no clima que você descrever.",
        "entrada": "texto",
        "icone": "musica",
        "destaque": False,
        "previstos": [
            {"nome": "ACE-Step", "melhor_para": "música de fundo a partir de uma descrição", "licenca": "Apache 2.0"}
        ],
    },
    {
        "id": "video-audio",
        "titulo": "Som e efeitos para um vídeo",
        "tipo": "Vídeo para áudio",
        "descricao": "Sons que combinam com o que acontece no vídeo.",
        "entrada": "video",
        "icone": "audio",
        "destaque": False,
        "previstos": [],
    },
    {
        "id": "audio-imagem",
        "titulo": "Imagem a partir de um áudio",
        "tipo": "Áudio para imagem",
        "descricao": "Uma imagem inspirada no som ou na música que você enviar.",
        "entrada": "audio",
        "icone": "imagens",
        "destaque": False,
        "previstos": [],
    },
]
IDS_FINALIDADES = {f["id"] for f in FINALIDADES}


def disponiveis() -> list[dict]:
    return [m for m in MODELOS if m["disponivel"]]


def nome(modelo_id: str | None) -> str:
    m = next((m for m in MODELOS if m["id"] == (modelo_id or PADRAO)), None)
    return m["nome"] if m else (modelo_id or PADRAO)


def finalidades() -> list[dict]:
    """Catálogo para a página: cada finalidade com os modelos disponíveis e se já pode ser usada."""
    out = []
    for f in FINALIDADES:
        mods = [m for m in disponiveis() if m["finalidade"] == f["id"]]
        out.append({**f, "modelos": mods, "disponivel": bool(mods)})
    return out


def disponivel(finalidade_id: str) -> bool:
    return any(m["finalidade"] == finalidade_id for m in disponiveis())
