import threading
import time

import pytest

from render2go_servico.db import MAX_TENTATIVAS, BancoError


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
