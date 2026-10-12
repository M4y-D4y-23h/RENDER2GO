import threading
import time

import pytest

from render2go_servico.db import MAX_TENTATIVAS, BancoError, SaldoInsuficiente


def _lote(b, n_fotos=2, variacoes=1):
    cid, token = b.criar_cliente("Padaria Teste")
    fotos = [
        {
            "nome": f"f{i}",
            "tipo": "image/jpeg",
            "bytes": 100 + i,
            "chave": f"entrada/{cid}/{{lote}}/f{i}.jpg",
        }
        for i in range(n_fotos)
    ]
    lid = b.criar_lote(cid, {"prompt": "a câmera se aproxima"}, fotos)
    return cid, token, lid


def _aprovar(b, lid, variacoes=1):
    b.confirmar_envio(lid, {f["chave"]: f["bytes"] for f in b.fotos(lid)})
    tarefas = [
        {"foto_id": f["id"], "variacao": k, "seed": 10 + k, "arquivo": f"{f['nome']}-v{k}.mp4"}
        for f in b.fotos(lid)
        for k in range(1, variacoes + 1)
    ]
    b.aprovar(lid, "dono", tarefas)


def test_client_token_is_stored_hashed(banco):
    cid, token = banco.criar_cliente("Loja")
    assert banco.cliente_por_token(token)["id"] == cid
    assert banco.cliente_por_token("errado") is None
    with banco.tx() as c:
        assert token not in str(c.all("SELECT * FROM clientes"))


def test_upload_check_and_no_jobs_before_approval(banco):
    _, _, lid = _lote(banco)
    fotos = banco.fotos(lid)
    assert all(f["chave"] == f"entrada/{f['chave'].split('/')[1]}/{lid}/{f['nome']}.jpg" for f in fotos)
    problemas = banco.confirmar_envio(lid, {fotos[0]["chave"]: fotos[0]["bytes"], fotos[1]["chave"]: 1})
    assert problemas == ["f1: 1 bytes, esperados 101"]
    assert banco.lote(lid)["status"] == "aguardando_envio"
    assert banco.confirmar_envio(lid, {f["chave"]: f["bytes"] for f in fotos}) == []
    assert banco.lote(lid)["status"] == "aguardando_aprovacao"
    assert banco.reivindicar("w1") is None  # fail-closed: nada roda sem aprovação


def test_full_flow_compile_first_then_renders(banco):
    _, _, lid = _lote(banco)
    _aprovar(banco, lid)
    j = banco.reivindicar("w1")
    assert j["tipo"] == "compilar"
    assert banco.reivindicar("w2") is None  # renders esperam o prompt
    banco.definir_prompt(lid, "The camera slowly moves in.", "i2v@5")
    banco.concluir(j["id"], "w1", "ok")
    r1, r2 = banco.reivindicar("w1"), banco.reivindicar("w2")
    assert {r1["arquivo"], r2["arquivo"]} == {"f0-v1.mp4", "f1-v1.mp4"}
    with pytest.raises(BancoError):
        banco.concluir(r1["id"], "w2", "ok")  # job de outro worker
    banco.concluir(r1["id"], "w1", "ok", segundos=55.0)
    banco.concluir(r2["id"], "w2", "erro", erro="CUDA", repetir=True)
    assert banco.lote(lid)["status"] == "processando"
    r3 = banco.reivindicar("w3")
    assert r3["id"] == r2["id"] and r3["tentativas"] == 2
    banco.concluir(r3["id"], "w3", "ok")
    assert banco.lote(lid)["status"] == "concluido"
    assert banco.fila() == {"pendentes": 0, "em_andamento": 0}


def test_expired_lease_requeues_then_fails(banco):
    _, _, lid = _lote(banco, n_fotos=1)
    _aprovar(banco, lid)
    j = banco.reivindicar("w1", lease_s=-1)  # prazo já vencido
    for _ in range(MAX_TENTATIVAS - 1):
        j2 = banco.reivindicar("w2", lease_s=-1)
        assert j2["id"] == j["id"]
    assert banco.reivindicar("w3") is None  # esgotou: compilar falhou, renders cancelados
    estados = {x["tipo"]: x["status"] for x in banco.jobs(lid)}
    assert estados == {"compilar": "erro", "render": "cancelado"}
    assert banco.lote(lid)["status"] == "concluido_com_falhas"


def test_refuse_only_before_processing(banco):
    _, _, lid = _lote(banco)
    banco.recusar(lid, "dono", "foto com pessoa sem consentimento")
    assert banco.lote(lid)["status"] == "recusado"
    with pytest.raises(BancoError):
        banco.recusar(lid, "dono", "de novo")


def test_concurrent_workers_never_share_a_job(banco):
    _, _, lid = _lote(banco, n_fotos=20)
    _aprovar(banco, lid)
    c = banco.reivindicar("w0")
    banco.definir_prompt(lid, "p", "i2v@5")
    banco.concluir(c["id"], "w0", "ok")
    pegos, lock = [], threading.Lock()

    def worker(n):
        while (j := banco.reivindicar(f"w{n}")) is not None:
            with lock:
                pegos.append(j["id"])
            time.sleep(0.001)

    ts = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(pegos) == 20 and len(set(pegos)) == 20


def _lote_de(b, nome, n_fotos, plano="pago"):
    cid, _ = b.criar_cliente(nome, plano=plano)
    fotos = [
        {
            "nome": f"{nome}{i}",
            "tipo": "image/jpeg",
            "bytes": 100,
            "chave": f"entrada/{cid}/{{lote}}/{i}.jpg",
        }
        for i in range(n_fotos)
    ]
    lid = b.criar_lote(cid, {"prompt": "a câmera se aproxima", "qualidade": "rapida"}, fotos)
    _aprovar(b, lid)
    c = b.reivindicar("compilador")
    b.definir_prompt(c["lote_id"], "p", "i2v@5")
    b.concluir(c["id"], "compilador", "ok")
    return lid


def test_queue_interleaves_clients_and_puts_paid_before_trials(banco):
    a = _lote_de(banco, "a", 3, plano="teste")
    b = _lote_de(banco, "b", 3, plano="teste")
    c = _lote_de(banco, "c", 2, plano="teste")
    ordem = []
    while (j := banco.reivindicar("w")) is not None:
        ordem.append(j["lote_id"])
        banco.concluir(j["id"], "w", "ok")
    # vídeo 1 de cada cliente antes do vídeo 2 de qualquer um
    assert ordem[:3] == [a, b, c] and ordem[3:6] == [a, b, c] and ordem[6:] == [a, b]

    t = _lote_de(banco, "t", 2, plano="teste")
    p = _lote_de(banco, "p", 2, plano="pago")  # chegou depois, mas é pago
    assert [x["lote_id"] for x in banco.trabalho()] == [p, t]
    assert banco.reivindicar("w")["lote_id"] == p


def _lote_do_cliente(b, cid, prefixo, n_fotos):
    fotos = [
        {"nome": f"{prefixo}{i}", "tipo": "image/jpeg", "bytes": 100, "chave": f"entrada/{cid}/{{lote}}/{i}.jpg"}
        for i in range(n_fotos)
    ]
    lid = b.criar_lote(cid, {"prompt": "a câmera se aproxima", "qualidade": "rapida"}, fotos)
    _aprovar(b, lid)
    c = b.reivindicar("compilador", tipos=["compilar"])
    b.definir_prompt(c["lote_id"], "p", "i2v@5")
    b.concluir(c["id"], "compilador", "ok")
    return lid


def test_same_client_orders_run_in_arrival_order_and_clients_still_interleave(banco):
    a, _ = banco.criar_cliente("A")
    b, _ = banco.criar_cliente("B")
    a1 = _lote_do_cliente(banco, a, "a1-", 3)
    j = banco.reivindicar("w")  # o 1º vídeo do pedido antigo já saiu
    banco.concluir(j["id"], "w", "ok")
    a2 = _lote_do_cliente(banco, a, "a2-", 2)  # o mesmo cliente pede de novo
    b1 = _lote_do_cliente(banco, b, "b1-", 2)  # e outro cliente chega depois
    previsto = {x: banco.videos_a_frente(banco.lote(x)) for x in (a2, b1)}
    ordem = []
    while (j := banco.reivindicar("w")) is not None:
        ordem.append(j["lote_id"])
        banco.concluir(j["id"], "w", "ok")
    # B ainda não tinha vídeo nenhum: vai primeiro; o pedido novo de A espera o antigo terminar
    assert ordem == [b1, a1, b1, a1, a2, a2]
    # a previsão segue a mesma ordem: vídeos antes do 1º e até o último de cada pedido
    assert previsto[a2] == {"primeiro": 4, "ultimo": 6}
    assert previsto[b1] == {"primeiro": 0, "ultimo": 3}


def test_videos_of_one_order_follow_the_list_order(banco):
    cid, _ = banco.criar_cliente("Loja")
    lid = _lote_do_cliente(banco, cid, "foto-", 4)
    arquivos = []
    while (j := banco.reivindicar("w")) is not None:
        arquivos.append(j["arquivo"])
        banco.concluir(j["id"], "w", "ok")
    assert arquivos == sorted(arquivos) and banco.lote(lid)["status"] == "concluido"


def test_worker_still_downloading_models_only_takes_compile_jobs(banco):
    _, _, lid = _lote(banco, n_fotos=1)
    _aprovar(banco, lid)
    with pytest.raises(BancoError):
        banco.reivindicar("w", tipos=["outro"])
    c = banco.reivindicar("pod-1", tipos=["compilar"])
    assert c["tipo"] == "compilar"
    banco.definir_prompt(lid, "p", "i2v@5")
    banco.concluir(c["id"], "pod-1", "ok")
    assert banco.reivindicar("pod-1", tipos=["compilar"]) is None  # o render espera os modelos
    assert banco.reivindicar("pod-1")["tipo"] == "render"


def test_generators_state_for_the_estimate(banco):
    assert banco.estado_geradores() == {"prontos": 0, "subindo": [], "subida_s": None}
    g = banco.gerador_subindo("NVIDIA L40S")
    est = banco.estado_geradores()
    assert est["prontos"] == 0 and len(est["subindo"]) == 1
    banco.gerador_pronto(g, "pod-1", "NVIDIA L40S", 1.09)
    assert len(banco.estado_geradores()["subindo"]) == 1  # worker no ar, mas ainda sem vídeo
    _, _, lid = _lote(banco, n_fotos=1)
    _aprovar(banco, lid)
    c = banco.reivindicar("pod-1")
    banco.definir_prompt(lid, "p", "i2v@5")
    banco.concluir(c["id"], "pod-1", "ok")
    banco.reivindicar("pod-1")  # 1º vídeo: fim da subida
    assert banco.estado_geradores()["prontos"] == 1
    with banco.tx() as cur:  # subidas medidas: a de cima (~0 s) e 200, 250, 300 e 350 s
        for i, s in enumerate((200, 250, 300, 350)):
            cur.run(
                "INSERT INTO geradores (id, estado, criado, primeiro_video_em) VALUES (?, 'encerrado', ?, ?)",
                (f"ger_{i}", 1000.0 + i, 1000.0 + i + s),
            )
    assert banco.estado_geradores()["subida_s"] == 250
    banco.gerador_fim(g, "fila vazia")
    assert banco.estado_geradores()["prontos"] == 0
    g2 = banco.gerador_subindo()
    assert banco.geradores_reiniciar() == 1 and banco.estado_geradores()["subindo"] == []
    banco.gerador_fim(g2, "de novo")  # já encerrado: nada muda


def test_gpu_report_identifies_each_pod_with_time_videos_and_cost(banco):
    # pod que subiu e gerou: 2 vídeos ok e 1 que falhou de vez, 1 tradução
    g = banco.gerador_subindo("NVIDIA L40S")
    banco.gerador_criado(g, "pod-abc", "NVIDIA L40S", 1.09, "EU-RO-1")
    banco.gerador_pronto(g, "pod-abc", "NVIDIA L40S", 1.09)
    _, _, lid = _lote(banco, n_fotos=3)
    _aprovar(banco, lid)
    c = banco.reivindicar("pod-abc")
    banco.definir_prompt(lid, "p", "i2v@6")
    banco.concluir(c["id"], "pod-abc", "ok")
    for status in ("ok", "ok", "erro"):
        j = banco.reivindicar("pod-abc")
        banco.concluir(j["id"], "pod-abc", status, segundos=60.0)
    # pod que o RunPod criou mas a instalação falhou; e uma subida em que o RunPod nem criou o pod
    f = banco.gerador_subindo("NVIDIA L40S")
    banco.gerador_criado(f, "pod-xyz", "NVIDIA L40S", 1.09, None)
    banco.gerador_fim(f, "não subiu", falhou=True)
    banco.gerador_desligado(f)
    n = banco.gerador_subindo("NVIDIA L40S")
    banco.gerador_fim(n, "não subiu", falhou=True)
    with banco.tx() as cur:  # 1 h ligado para o 1º (a cobrança vai da criação ao desligamento)
        cur.run("UPDATE geradores SET pod_criado_em = pod_criado_em - 3600 WHERE id = ?", (g,))
    banco.gerador_fim(g, "fila vazia")
    banco.gerador_desligado(g)
    banco.gerador_desligado(g)  # o 2º registro não muda o horário

    r = banco.relatorio_geradores(time.time() - 86400)
    por_id = {x["id"]: x for x in r["geradores"]}
    a = por_id[g]
    assert (a["pod"], a["gpu"], a["datacentro"], a["estado"], a["motivo"]) == (
        "pod-abc",
        "NVIDIA L40S",
        "EU-RO-1",
        "encerrado",
        "fila vazia",
    )
    assert (a["videos_ok"], a["videos_erro"], a["traducoes"], a["lotes"]) == (2, 1, 1, 1)
    assert a["segundos_render"] == 120.0 and not a["ligado_estimado"]
    assert a["ligado_s"] == pytest.approx(3600, abs=5) and a["custo_usd"] == pytest.approx(1.09, abs=0.01)
    assert por_id[f]["estado"] == "falhou" and por_id[f]["custo_usd"] is not None
    assert por_id[n]["pod"] is None and por_id[n]["custo_usd"] == 0.0 and por_id[n]["ligado_s"] == 0.0
    t = r["totais"]
    assert (t["pods"], t["falharam"], t["videos_ok"], t["ligados_agora"]) == (2, 2, 2, 0)
    assert t["custo_por_video_usd"] == pytest.approx(t["custo_usd"] / 2, abs=1e-4)
    # fora do período: nada
    assert banco.relatorio_geradores(time.time() + 10)["geradores"] == []


def test_history_cleanup_keeps_clients_and_refuses_while_generating(banco):
    cid, token, lid = _lote(banco, n_fotos=2)
    banco.registrar_suporte(cid, {"ip": "1.2.3.4"}, "a@b.co", "dúvida", "mensagem de teste")
    banco.registrar_cookies({"ip": "1.2.3.4"}, "1", {"estatisticas": False})
    banco.gerador_subindo()
    _aprovar(banco, lid)  # em geração: bloqueia
    with pytest.raises(BancoError):
        banco.apagar_historico()
    contagem = banco.contar_historico()
    assert contagem["lotes"] == 1 and contagem["jobs"] == 3 and contagem["suporte"] == 1
    apagados = banco.apagar_historico(forcar=True)
    assert apagados["lotes"] == 1 and apagados["fotos"] == 2 and apagados["eventos"] >= 2
    assert all(n == 0 for n in banco.contar_historico().values())
    assert banco.cliente_por_token(token)["id"] == cid  # o código de acesso continua valendo
    assert banco.contar_historico(cookies=True)["cookies"] == 1  # cookies só com a opção
    banco.apagar_historico(cookies=True)
    assert banco.contar_historico(cookies=True)["cookies"] == 0
    banco.compactar()


def test_work_summary_and_verification_fields(banco):
    lid = _lote_de(banco, "x", 2)
    (w,) = banco.trabalho()
    assert (w["lote_id"], w["pendentes"], w["em_andamento"]) == (lid, 2, 0)
    j = banco.reivindicar("w")
    banco.concluir(j["id"], "w", "ok", segundos=12.0, similaridade=0.998, bytes_video=12345)
    feito = next(x for x in banco.jobs(lid) if x["id"] == j["id"])
    assert feito["similaridade"] == pytest.approx(0.998) and feito["bytes_video"] == 12345
    assert banco.fotos_total(banco.lote(lid)["cliente_id"]) == 2
    with pytest.raises(BancoError):
        banco.criar_cliente("y", plano="vip")


def test_schema_upgrade_adds_new_columns_to_an_existing_database(tmp_path):
    import sqlite3

    from render2go_servico.db import Banco

    caminho = tmp_path / "antigo.db"
    con = sqlite3.connect(caminho)  # tabela de fotos como na primeira implantação, sem "analise"
    con.execute(
        "CREATE TABLE fotos (id TEXT PRIMARY KEY, lote_id TEXT NOT NULL, nome TEXT NOT NULL, tipo TEXT NOT NULL,"
        " bytes INTEGER NOT NULL, chave TEXT NOT NULL, enviada INTEGER NOT NULL DEFAULT 0, UNIQUE (lote_id, nome))"
    )
    con.commit()
    con.close()
    b = Banco(f"sqlite:///{caminho}")
    b.criar_esquema()
    b.criar_esquema()  # de novo: nada muda
    with b.tx() as c:
        assert "analise" in [r["name"] for r in c.all("PRAGMA table_info(fotos)")]


def test_photo_analysis_is_saved(banco):
    _, _, lid = _lote(banco, n_fotos=1)
    (f,) = banco.fotos(lid)
    assert f["analise"] is None
    banco.salvar_analise(f["id"], {"pessoas": 1, "rostos": 1})
    banco.criar_esquema()  # reaplicar o esquema (IF NOT EXISTS no Postgres) não apaga nada
    assert '"pessoas": 1' in banco.fotos(lid)[0]["analise"]


def _lote_pago(b, cid, n_fotos, preco_video, variacoes=1, cobranca="pedido"):
    """Pedido do pré-pago; cobranca "pedido" = o modo de antes de 12/10/2026 (inteiro no envio, com estorno),
    que continua valendo para os pedidos criados antes; "video" = ADR 0012."""
    fotos = [
        {"nome": f"f{i}", "tipo": "image/jpeg", "bytes": 100, "chave": f"entrada/{cid}/{{lote}}/f{i}.jpg"}
        for i in range(n_fotos)
    ]
    pedido = {"prompt": "a câmera se aproxima", "qualidade": "rapida", "duracao": 4, "variacoes": variacoes}
    return b.criar_lote(cid, pedido, fotos, preco_video=preco_video, cobranca=cobranca)


def test_simultaneous_uploads_never_spend_the_same_balance_twice(banco):
    """10 pedidos da mesma conta terminam de subir ao mesmo tempo, com saldo para 3: exatamente 3 são
    cobrados e o saldo nunca fica negativo (no Postgres, a linha do cliente fica travada na cobrança)."""
    cid, _ = banco.criar_cliente("Loja")
    banco.movimentar(cid, 900, "Pix", "dono", referencia="pagamento:pix-1")
    lotes = [_lote_pago(banco, cid, 3, 100) for _ in range(10)]  # 300 cada
    largada, resultados, lock = threading.Barrier(len(lotes)), [], threading.Lock()

    def enviar(lid):
        largada.wait()
        try:
            banco.confirmar_envio(lid, {f["chave"]: 100 for f in banco.fotos(lid)})
            r = "cobrado"
        except SaldoInsuficiente:
            r = "sem saldo"
        with lock:
            resultados.append(r)

    ts = [threading.Thread(target=enviar, args=(lid,)) for lid in lotes]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(resultados) == ["cobrado"] * 3 + ["sem saldo"] * 7
    assert banco.saldo(cid) == 0
    assert sum(1 for lid in lotes if banco.lote(lid)["status"] != "aguardando_envio") == 3


def _pronto_para_render(b, lid):
    """Envia, aprova e traduz o pedido: os renders ficam na fila."""
    _aprovar(b, lid)
    c = b.reivindicar("compilador", tipos=["compilar"])
    b.definir_prompt(c["lote_id"], "p", "i2v@5")
    b.concluir(c["id"], "compilador", "ok")


def test_per_video_billing_charges_each_delivered_video_once_and_never_the_failed_ones(banco):
    """ADR 0012: nada no envio; cada vídeo é cobrado quando sai certo, uma vez só; o que falha, nunca."""
    cid, _ = banco.criar_cliente("Loja")
    banco.movimentar(cid, 1000, "Pix", "dono", referencia="pagamento:pix-1")
    lid = _lote_pago(banco, cid, 3, 300, cobranca="video")
    _pronto_para_render(banco, lid)
    assert banco.saldo(cid) == 1000 and banco.cobranca(lid) == {"cobrado": 0, "devolvido": 0}
    a, b, d = (banco.reivindicar(f"w{i}") for i in range(3))
    assert banco.saldo_livre(cid) == 1000 - 900  # os 3 em andamento estão reservados
    banco.concluir(a["id"], "w0", "ok")
    banco.concluir(b["id"], "w1", "erro", erro="falhou")  # não cobra
    banco.concluir(d["id"], "w2", "ok")
    assert banco.saldo(cid) == 400 and banco.cobranca(lid) == {"cobrado": 600, "devolvido": 0}
    assert banco.saldo_livre(cid) == 400 and banco.lote(lid)["status"] == "concluido_com_falhas"
    videos = [m for m in banco.extrato(cid) if m["tipo"] == "video"]
    assert sorted(m["valor"] for m in videos) == [-300, -300]
    assert all(m["descricao"].startswith("1 vídeo(s) de 4 s em 480p (") for m in videos)
    with banco.tx() as c:  # a mesma entrega de novo não cobra de novo (referência única)
        banco._cobrar_video(c, {**a, "lote_id": lid})
    assert banco.saldo(cid) == 400


def test_per_video_orders_wait_for_balance_without_holding_pods_and_resume_after_recharge(banco):
    cid, _ = banco.criar_cliente("Loja")
    banco.movimentar(cid, 500, "Pix", "dono", referencia="pagamento:pix-1")
    lid = _lote_pago(banco, cid, 3, 300, cobranca="video")
    _pronto_para_render(banco, lid)
    (t,) = banco.trabalho()
    assert (t["pendentes"], t["em_andamento"]) == (1, 0)  # o saldo paga 1 vídeo agora: o orquestrador vê 1
    v1 = banco.reivindicar("w1")
    assert banco.reivindicar("w2") is None  # livre: 500 − 300 reservados = 200 < 300
    (t,) = banco.trabalho()
    assert (t["pendentes"], t["em_andamento"]) == (0, 1)
    banco.concluir(v1["id"], "w1", "ok")
    assert banco.saldo(cid) == 200 and banco.reivindicar("w2") is None
    assert banco.trabalho() == []  # nada que o saldo pague: nenhum pod fica ligado esperando
    banco.movimentar(cid, 1000, "Pix", "dono", referencia="pagamento:pix-2")
    assert [x["pendentes"] for x in banco.trabalho()] == [2]  # volta sozinho com a recarga
    assert banco.reivindicar("w2") is not None and banco.reivindicar("w3") is not None


def test_orders_of_the_same_account_start_only_after_the_previous_one_ends(banco):
    """Pedido do dono (12/10/2026): mesmo com vários pods livres, o pedido seguinte da mesma conta só
    começa quando o anterior termina; outra conta não espera."""
    a, _ = banco.criar_cliente("A")
    b, _ = banco.criar_cliente("B")
    a1 = _lote_do_cliente(banco, a, "a1-", 2)
    a2 = _lote_do_cliente(banco, a, "a2-", 2)  # a tradução do 2º pedido já pode ter saído
    x, y = banco.reivindicar("w1"), banco.reivindicar("w2")
    assert {x["lote_id"], y["lote_id"]} == {a1}
    assert banco.reivindicar("w3") is None  # o a2 espera o a1 terminar, com pods livres
    b1 = _lote_do_cliente(banco, b, "b1-", 1)
    assert banco.reivindicar("w3")["lote_id"] == b1  # outra conta não espera
    banco.concluir(x["id"], "w1", "ok")
    assert banco.reivindicar("w1") is None  # o a1 ainda tem um vídeo em andamento
    banco.concluir(y["id"], "w2", "ok")
    assert banco.reivindicar("w1")["lote_id"] == a2


def test_many_pods_never_take_more_videos_than_the_balance_pays(banco):
    """10 pods pedem vídeo ao mesmo tempo, com saldo para 3: só 3 saem (a reserva e a trava do cliente)."""
    cid, _ = banco.criar_cliente("Loja")
    banco.movimentar(cid, 900, "Pix", "dono", referencia="pagamento:pix-1")
    lid = _lote_pago(banco, cid, 10, 300, cobranca="video")
    _pronto_para_render(banco, lid)
    largada, pegos, lock = threading.Barrier(10), [], threading.Lock()

    def pod(n):
        largada.wait()
        j = banco.reivindicar(f"w{n}")
        with lock:
            pegos.append(j)

    ts = [threading.Thread(target=pod, args=(n,)) for n in range(10)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    saidos = [j for j in pegos if j is not None]
    assert len(saidos) == 3 and banco.saldo_livre(cid) == 0
    for j in saidos:
        banco.concluir(j["id"], j["worker"], "ok")
    assert banco.saldo(cid) == 0 and banco.reivindicar("w0") is None


def test_prepaid_charge_on_upload_refund_per_failed_video_and_ledger_survives_cleanup(banco):
    cid, _ = banco.criar_cliente("Loja")
    assert banco.saldo(cid) == 0
    with pytest.raises(BancoError, match="negativo"):
        banco.movimentar(cid, -1, "ajuste", "dono")
    assert banco.movimentar(cid, 1000, "Pix", "dono", referencia="pagamento:pix-1") == 1000
    with pytest.raises(BancoError, match="já registrado"):  # o mesmo pagamento não entra duas vezes
        banco.movimentar(cid, 1000, "Pix", "dono", referencia="pagamento:pix-1")

    # sem saldo para o pedido inteiro: nada muda (o lote continua esperando o envio)
    caro = _lote_pago(banco, cid, 2, 600)
    with pytest.raises(SaldoInsuficiente) as e:
        banco.confirmar_envio(caro, {f["chave"]: 100 for f in banco.fotos(caro)})
    assert (e.value.custo, e.value.saldo) == (1200, 1000)
    assert banco.lote(caro)["status"] == "aguardando_envio" and banco.saldo(cid) == 1000

    # 3 fotos × 2 variações × 100: cobrado quando as fotos chegam, não antes
    lid = _lote_pago(banco, cid, 3, 100, variacoes=2)
    assert banco.saldo(cid) == 1000
    _aprovar(banco, lid, variacoes=2)
    assert banco.saldo(cid) == 400 and banco.cobranca(lid) == {"cobrado": 600, "devolvido": 0}
    (mov,) = [m for m in banco.extrato(cid) if m["tipo"] == "pedido"]
    assert mov["valor"] == -600 and mov["descricao"] == "6 vídeo(s) de 4 s em 480p"

    # a compilação falha de vez: os 6 renders são cancelados e cada um volta, uma vez só
    comp = banco.reivindicar("w1")
    banco.concluir(comp["id"], "w1", "erro", erro="falhou")
    assert banco.lote(lid)["status"] == "concluido_com_falhas"
    assert banco.saldo(cid) == 1000 and banco.cobranca(lid) == {"cobrado": 600, "devolvido": 600}
    with banco.tx() as c:
        banco._fechar_lote(c, lid)  # fechar de novo não devolve de novo
    assert banco.saldo(cid) == 1000

    # pedido recusado na revisão: volta inteiro
    rec = _lote_pago(banco, cid, 2, 150)
    banco.confirmar_envio(rec, {f["chave"]: 100 for f in banco.fotos(rec)})
    assert banco.saldo(cid) == 700
    banco.recusar(rec, "dono", "foto borrada")
    assert banco.saldo(cid) == 1000 and banco.cobranca(rec) == {"cobrado": 300, "devolvido": 300}

    # a limpeza do histórico apaga os pedidos, mas o extrato (registro financeiro) fica
    banco.apagar_historico()
    assert banco.saldo(cid) == 1000 and len(banco.extrato(cid)) == 1 + 1 + 6 + 1 + 1
    assert [k["saldo"] for k in banco.clientes() if k["id"] == cid] == [1000]
