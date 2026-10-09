"""Painel no endereço secreto (ADR 0007): dois fatores (token + autenticador), bloqueio por tentativas,
IPs permitidos, pods (desligar um ou parada de emergência), tokens de worker por pod e auditoria."""

import time

from test_api import ADM, ADMIN, PAINEL, TOTP, WORKER, _cliente, anonimo, cliente_teste, config_teste, entrar_admin, h

from render2go_servico import contas


def _codigo(delta=0):
    return contas.totp(TOTP, int(time.time() // 30) + delta)


def test_panel_lives_only_at_the_secret_address_and_needs_both_factors(banco):
    api = cliente_teste(config_teste(banco), admin=False)
    pagina = api.get(f"{PAINEL}/")
    assert pagina.status_code == 200 and 'data-pagina="painel"' in pagina.text
    assert pagina.headers["cache-control"] == "no-store" and pagina.headers["x-robots-tag"] == "noindex, nofollow"
    assert api.get(f"{PAINEL}/api/sessao").json() == {"admin": None}  # sem sessão: não é erro
    entrar = f"{PAINEL}/api/entrar"
    assert api.post(entrar, json={"token": ADMIN}, headers=ADM).status_code == 401  # sem o autenticador
    assert api.post(entrar, json={"token": "x" * 40, "totp": _codigo()}, headers=ADM).status_code == 401
    assert api.post(entrar, json={"token": ADMIN, "totp": "000000"}, headers=ADM).status_code == 401
    r = api.post(entrar, json={"token": ADMIN, "totp": _codigo()}, headers=ADM)
    assert r.status_code == 200 and r.json()["admin"].startswith("admin-")
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("__Secure-render2go-painel=")
    for atributo in ("HttpOnly", "Secure", f"Path={PAINEL}", "SameSite=strict", "Max-Age=43200"):
        assert atributo in cookie, atributo
    assert api.get(f"{PAINEL}/api/sessao").json() == {"admin": r.json()["admin"], "totp": True}
    # o mesmo código do autenticador não vale duas vezes (alguém que viu a tela não entra com ele)
    outro = anonimo(api)
    assert outro.post(entrar, json={"token": ADMIN, "totp": _codigo()}, headers=ADM).status_code == 401
    # a sessão do painel não vale fora do caminho do painel, nem sem o cabeçalho da página
    assert api.post(f"{PAINEL}/api/pods/emergencia").status_code == 403
    assert api.get("/api/admin/lotes").status_code == 404
    # sair encerra a sessão
    assert api.post(f"{PAINEL}/api/sair", headers=ADM).status_code == 200
    assert api.get(f"{PAINEL}/api/sessao").json() == {"admin": None}


def test_wrong_attempts_lock_the_panel_for_the_network(banco):
    api = cliente_teste(config_teste(banco), admin=False)
    for _ in range(5):
        r = api.post(f"{PAINEL}/api/entrar", json={"token": ADMIN, "totp": "123456"}, headers=ADM)
        assert r.status_code == 401
    r = api.post(f"{PAINEL}/api/entrar", json={"token": ADMIN, "totp": _codigo()}, headers=ADM)
    assert r.status_code == 429  # bloqueado mesmo com o código certo
    # outra rede continua podendo entrar; as tentativas recusadas ficam na auditoria
    outra = {**ADM, "X-Render2Go-IP": "203.0.113.50"}
    assert api.post(f"{PAINEL}/api/entrar", json={"token": ADMIN, "totp": _codigo()}, headers=outra).status_code == 200
    acoes = [a["acao"] for a in api.get(f"{PAINEL}/api/seguranca", headers=outra).json()["auditoria"]]
    assert acoes.count("entrada_recusada") == 5 and "entrada" in acoes


def test_allowed_networks_and_a_panel_that_is_not_configured(banco):
    api = cliente_teste(config_teste(banco, admin_ips=["198.51.100.0/24"]), admin=False)
    assert api.get(f"{PAINEL}/").status_code == 404  # o IP de quem pede não está na lista
    de_casa = {**ADM, "X-Render2Go-IP": "198.51.100.7"}
    assert api.get(f"{PAINEL}/", headers=de_casa).status_code == 200
    r = api.post(f"{PAINEL}/api/entrar", json={"token": ADMIN, "totp": _codigo()}, headers=de_casa)
    assert r.status_code == 200
    assert api.get(f"{PAINEL}/api/sessao").status_code == 404  # o cookie vale, mas o IP não
    sem_painel = cliente_teste(config_teste(banco, admin_caminho=None), admin=False)
    assert sem_painel.get(f"{PAINEL}/").status_code == 404
    assert sem_painel.post(f"{PAINEL}/api/entrar", json={"token": ADMIN}, headers=ADM).status_code == 404


def test_pods_can_be_shut_down_one_by_one_or_all_at_once(banco):
    api = cliente_teste(config_teste(banco))
    g1, g2 = banco.gerador_subindo("NVIDIA L40S"), banco.gerador_subindo("NVIDIA L40S")
    banco.gerador_pronto(g1, "pod-a1", "NVIDIA L40S", 1.09)
    banco.gerador_criado(g2, "pod-b2", "NVIDIA L40S", 1.09, "EU-RO-1")
    tok_a = banco.criar_token_worker("pod-a1", g1, 3600)
    tok_b = banco.criar_token_worker("pod-b2", g2, 3600)
    r = api.get(f"{PAINEL}/api/pods").json()
    assert sorted(p["worker"] for p in r["pods"]) == ["pod-a1", "pod-b2"] and r["pausado"] is False
    assert r["orquestrador_visto_s"] is None  # o orquestrador ainda não deu sinal
    assert api.post(f"{PAINEL}/api/pods/ger_x/desligar", headers=ADM).status_code == 404
    assert api.post(f"{PAINEL}/api/pods/{g1}/desligar", headers=ADM).status_code == 200
    # o pod perde na hora a permissão de pegar trabalho; o orquestrador encerra no próximo ciclo
    corpo = {"worker": "pod-a1"}
    assert api.post("/api/worker/reivindicar", json=corpo, headers=h(tok_a)).status_code == 401
    assert api.post("/api/worker/reivindicar", json={"worker": "pod-b2"}, headers=h(tok_b)).status_code == 200
    (cmd,) = banco.comandos_pendentes()
    assert (cmd["tipo"], cmd["alvo"]) == ("desligar_pod", g1)
    # parada de emergência: pausa, nenhum pod pega trabalho e o orquestrador encerra todos
    assert api.post(f"{PAINEL}/api/pods/emergencia", headers=ADM).json()["pausado"] is True
    assert api.post("/api/worker/reivindicar", json={"worker": "pod-b2"}, headers=h(tok_b)).status_code == 401
    r = api.get(f"{PAINEL}/api/pods").json()
    assert r["pausado"] is True and [c["tipo"] for c in r["comandos"]][:2] == ["emergencia", "desligar_pod"]
    assert api.post(f"{PAINEL}/api/pods/retomar", headers=ADM).json()["pausado"] is False
    assert api.get(f"{PAINEL}/api/pods").json()["pausado"] is False
    acoes = [a["acao"] for a in api.get(f"{PAINEL}/api/seguranca").json()["auditoria"]]
    assert {"desligar_pod", "emergencia", "retomar"} <= set(acoes)


def test_each_pod_token_works_only_for_its_own_worker_and_until_it_expires(banco):
    api = cliente_teste(config_teste(banco), admin=False)
    tok = banco.criar_token_worker("pod-p1", None, 3600)
    assert api.post("/api/worker/reivindicar", json={"worker": "pod-p1"}, headers=h(tok)).status_code == 200
    r = api.post("/api/worker/reivindicar", json={"worker": "pod-p2"}, headers=h(tok))
    assert r.status_code == 403 and "outro worker" in r.json()["detail"]
    vencido = banco.criar_token_worker("pod-p3", None, -1)
    assert api.post("/api/worker/reivindicar", json={"worker": "pod-p3"}, headers=h(vencido)).status_code == 401
    banco.revogar_tokens_worker(worker="pod-p1")
    assert api.post("/api/worker/reivindicar", json={"worker": "pod-p1"}, headers=h(tok)).status_code == 401
    # o token fixo do servidor (testes manuais) segue valendo com qualquer nome
    assert api.post("/api/worker/reivindicar", json={"worker": "pod-x"}, headers=h(WORKER)).status_code == 200


def test_security_overview_accounts_and_audit(banco):
    api = cliente_teste(config_teste(banco))
    seg = api.get(f"{PAINEL}/api/seguranca").json()
    assert seg["totp"] is True and seg["turnstile"] is False and seg["turnstile_desligado"] is True
    assert seg["pagamento"] is False and seg["cookie_seguro"] is True and seg["contas"]["total"] == 0
    tok = _cliente(api, "Antiga", plano="teste")
    (k,) = api.get(f"{PAINEL}/api/clientes?busca=antiga").json()["clientes"]
    assert k["nome"] == "Antiga" and k["email"] is None
    assert api.post(
        f"{PAINEL}/api/clientes/{k['id']}/email", json={"email": "antiga@gmail.com"}, headers=ADM
    ).is_success
    assert api.post(f"{PAINEL}/api/clientes/{k['id']}/plano", json={"plano": "vip"}, headers=ADM).status_code == 422
    assert api.post(f"{PAINEL}/api/clientes/{k['id']}/plano", json={"plano": "pago"}, headers=ADM).is_success
    assert api.post(f"{PAINEL}/api/clientes/{k['id']}/desativar", headers=ADM).is_success
    assert api.get("/api/lotes", headers=h(tok)).status_code == 401
    assert api.post(f"{PAINEL}/api/clientes/{k['id']}/reativar", headers=ADM).is_success
    assert api.get("/api/lotes", headers=h(tok)).status_code == 200
    (k,) = api.get(f"{PAINEL}/api/clientes?busca=antiga@gmail").json()["clientes"]
    assert (k["email"], k["plano"], k["ativo"]) == ("antiga@gmail.com", "pago", 1)
    acoes = [a["acao"] for a in api.get(f"{PAINEL}/api/seguranca").json()["auditoria"]]
    assert {"entrada", "email", "plano", "desativar", "reativar"} <= set(acoes)
    entrar_admin(anonimo(api))  # outra sessão do painel ao mesmo tempo


def test_hostile_text_never_breaks_the_api(banco):
    """Achados do teste de invasão (docs/13): NUL na busca ou numa mensagem e credenciais com caracteres
    fora do ASCII davam erro 500 (no Postgres e no compare_digest); agora são recusados ou limpos."""
    api = cliente_teste(config_teste(banco))
    r = api.get(f"{PAINEL}/api/clientes", params={"busca": "a\x00' OR 1=1 --"})
    assert r.status_code == 200 and r.json()["clientes"] == []
    tok = _cliente(api, "Loja Nula", plano="teste")
    corpo = {"mensagem": "preciso de ajuda\x00 com o pedido", "email": "x@gmail.com"}
    assert api.post("/api/suporte", json=corpo, headers={**h(tok), **ADM}).status_code == 200
    (m,) = api.get(f"{PAINEL}/api/suporte").json()["mensagens"]
    assert "\x00" not in m["mensagem"]
    anon = anonimo(api)
    assert anon.post(f"{PAINEL}/api/entrar", json={"token": "é" * 40, "totp": "١٢٣٤٥٦"}, headers=ADM).status_code == 401
    assert anon.get("/api/lotes", headers={"Authorization": ("Bearer " + "é" * 40).encode()}).status_code == 401
    w = banco.criar_token_worker("pod-u", None, 3600)
    assert anon.post("/api/worker/reivindicar", json={"worker": "pod-é"}, headers=h(w)).status_code == 403
