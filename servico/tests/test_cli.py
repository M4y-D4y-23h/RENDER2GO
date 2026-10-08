"""render2go-servico apagar-historico: banco de teste e armazenamento falso."""

from render2go_servico import cli


class Armazem:
    def __init__(self, chaves):
        self.chaves = set(chaves)

    def listar(self, prefixo):
        assert prefixo in cli.PREFIXOS_CLIENTES  # nunca lista (nem apaga) o espelho dos modelos
        return sorted(k for k in self.chaves if k.startswith(prefixo))

    def apagar(self, chave):
        self.chaves.discard(chave)


def _historico(banco):
    cid, token = banco.criar_cliente("Loja")
    fotos = [{"nome": "f0", "tipo": "image/jpeg", "bytes": 10, "chave": f"entrada/{cid}/{{lote}}/f0.jpg"}]
    lid = banco.criar_lote(cid, {"prompt": "um pedido de teste"}, fotos)
    banco.registrar_suporte(cid, {"ip": "1.2.3.4"}, None, "assunto", "mensagem")
    return cid, token, lid


def test_cleanup_dry_run_then_confirmed(banco, capsys):
    cid, token, lid = _historico(banco)
    modelo = "modelos/" + "a" * 64
    armazem = Armazem([f"entrada/{cid}/{lid}/f0.jpg", f"saida/{cid}/{lid}/f0-v1.mp4", modelo])
    assert cli.apagar_historico(banco, armazem, confirmar=False, cookies=False, forcar=False) == 0
    assert "nada foi apagado" in capsys.readouterr().out
    assert banco.lote(lid) is not None and len(armazem.chaves) == 3
    assert cli.apagar_historico(banco, armazem, confirmar=True, cookies=False, forcar=False) == 0
    saida = capsys.readouterr().out
    assert "apagados do R2: 2 arquivo(s)" in saida and "lotes 1" in saida
    assert banco.lote(lid) is None and armazem.chaves == {modelo}  # o espelho dos modelos fica
    assert banco.cliente_por_token(token)["id"] == cid


def test_cleanup_refuses_while_an_order_is_generating(banco, capsys):
    _, _, lid = _historico(banco)
    banco.confirmar_envio(lid, {f["chave"]: f["bytes"] for f in banco.fotos(lid)})
    (f,) = banco.fotos(lid)
    banco.aprovar(lid, "dono", [{"foto_id": f["id"], "variacao": 1, "seed": 1, "arquivo": "f0-v1.mp4"}])
    assert cli.apagar_historico(banco, None, confirmar=True, cookies=False, forcar=False) == 1
    assert banco.lote(lid) is not None and "espere terminar" in capsys.readouterr().out
    assert cli.apagar_historico(banco, None, confirmar=True, cookies=False, forcar=True) == 0
    assert banco.lote(lid) is None
