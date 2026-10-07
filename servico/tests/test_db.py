import threading
import time

import pytest

from gpu2go_servico.db import MAX_TENTATIVAS, BancoError


def _lote(b, n_fotos=2, variacoes=1):
    cid, token = b.criar_cliente("Padaria Teste")
    fotos = [
        {"nome": f"f{i}", "tipo": "image/jpeg", "bytes": 100 + i, "chave": f"clientes/{cid}/{{lote}}/entrada/f{i}.jpg"}
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
    assert all(f["chave"].endswith(f"{lid}/entrada/{f['nome']}.jpg") for f in fotos)
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
