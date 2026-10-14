"""Ciclo de vida dos pods no lote_nuvem (14/10/2026): encerramento só sai do controle com a
confirmação disponível do provedor. Provedores falsos e relógio simulado; nenhuma chamada de
rede."""

import json
from argparse import Namespace

import pytest

from render2go_worker import lote_nuvem as ln


class Relogio:
    """time.monotonic e time.time simulados (avançam só quando o teste manda)."""

    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def andar(self, s):
        self.t += s


class Falso:
    """Provedor que registra cada chamada; `erros` são levantados em ordem pelo encerrar (None =
    aceita)."""

    def __init__(self, erros=(), situacoes=None):
        self.chamadas: list[str] = []
        self.consultas: list[str] = []
        self.erros = list(erros)
        self.situacoes = situacoes  # dict pod → lista de respostas de `situacao` (None = 404)

    def preco(self, gpu, nuvem):
        return 1.0

    def criar(self, nome, opcoes, gpus, env):  # pragma: no cover (não usado aqui)
        raise AssertionError

    def encerrar(self, pod_id):
        self.chamadas.append(pod_id)
        if self.erros:
            e = self.erros.pop(0)
            if e is not None:
                raise e


class ComSituacao(Falso):
    def situacao(self, pod_id):
        self.consultas.append(pod_id)
        fila = (self.situacoes or {}).get(pod_id) or []
        r = fila.pop(0) if fila else None
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def relogio(monkeypatch):
    r = Relogio()
    monkeypatch.setattr(ln.time, "monotonic", r)
    monkeypatch.setattr(ln.time, "time", lambda: 1_700_000_000.0 + r.t)
    monkeypatch.setattr(ln.time, "sleep", lambda s: r.andar(s))
    return r


def _orq(tmp_path, provedor):
    a = Namespace(saida=tmp_path, acabamento=None)
    return ln.Orquestrador(a, [], provedor, ln.Remoto())


def _pod(orq, pid="p1", usd_h=1.0):
    info = ln.PodInfo(pid, "http://127.0.0.1:9", "t" * 40, usd_h, "teste", 1)
    pod = ln.Pod(info, ln.Agente(info.url, info.token))
    with orq.lock:
        orq.pods.append(pod)
        orq._salvar_estado()
    return pod


def _registrados(tmp_path):
    return json.loads((tmp_path / ln.ESTADO / "pods.json").read_text())


def test_a_failed_delete_keeps_the_pod_in_the_inventory_and_is_retried(tmp_path, relogio):
    """Reprodução (14/10/2026): o DELETE falha com erro de rede. Antes, o pod saía do pods.json e a
    2ª
    chamada nem tentava de novo (uma chamada só ao provedor e a lista vazia gravada)."""
    prov = Falso(erros=[OSError("rede caiu"), OSError("rede caiu")])
    orq = _orq(tmp_path, prov)
    pod = _pod(orq)
    orq._encerrar(pod, "fim")
    orq._encerrar(pod, "fim")
    assert prov.chamadas == ["p1", "p1"]
    assert [p["id"] for p in _registrados(tmp_path)] == ["p1"]
    assert _registrados(tmp_path)[0]["encerramento"] == ln.ENC_FALHOU
    assert pod.faturavel and not pod.vivo  # não pega mais trabalho, mas pode estar cobrando


def test_retry_follows_a_backoff_and_stops_once_accepted(tmp_path, relogio):
    prov = Falso(erros=[OSError("rede"), None])
    orq = _orq(tmp_path, prov)
    pod = _pod(orq)
    orq._encerrar(pod, "fim")
    orq.reconciliar()  # antes do prazo do backoff: não chama de novo
    assert prov.chamadas == ["p1"]
    relogio.andar(ln.ENC_BACKOFF_S[0] + 1)
    orq.reconciliar()
    # sem `situacao` no provedor, o aceite é o que se pode saber: sai do registro, marcado assim
    assert prov.chamadas == ["p1", "p1"] and pod.encerramento == ln.ENC_CONFIRMADO
    assert _registrados(tmp_path) == [] and pod.confirmacao == "aceite"


def test_accepted_delete_waits_for_the_observed_end(tmp_path, relogio):
    """Aceite não é término: com `situacao`, o pod fica no inventário até o 404 (ou TERMINATED)."""
    prov = ComSituacao(situacoes={"p1": ["RUNNING", "RUNNING", RuntimeError("timeout"), None]})
    orq = _orq(tmp_path, prov)
    pod = _pod(orq)
    orq._encerrar(pod, "fim")
    assert pod.encerramento == ln.ENC_ACEITO and [p["id"] for p in _registrados(tmp_path)] == ["p1"]
    orq.reconciliar()  # dentro do intervalo: nem consulta
    assert prov.consultas == ["p1"]
    relogio.andar(ln.ENC_REENVIO_S + 1)
    # ainda RUNNING depois do prazo: o DELETE (idempotente) vai de novo; a consulta logo depois dele
    # não
    # responde (timeout): inconclusiva, nada muda
    orq.reconciliar()
    assert prov.chamadas == ["p1", "p1"] and pod.encerramento == ln.ENC_ACEITO and pod.faturavel
    assert "timeout" in pod.enc_erro and [p["id"] for p in _registrados(tmp_path)] == ["p1"]
    relogio.andar(ln.ENC_BACKOFF_S[-1] + 1)
    orq.reconciliar()  # 404: confirmado
    assert pod.encerramento == ln.ENC_CONFIRMADO and pod.confirmacao == "404"
    assert not pod.faturavel and _registrados(tmp_path) == []


def test_terminated_desired_status_counts_as_observed(tmp_path, relogio):
    prov = ComSituacao(situacoes={"p1": ["TERMINATED"]})
    orq = _orq(tmp_path, prov)
    pod = _pod(orq)
    orq._encerrar(pod, "fim")
    assert pod.encerramento == ln.ENC_CONFIRMADO and pod.confirmacao == "TERMINATED"


def test_404_on_delete_is_a_confirmed_end(tmp_path, relogio, monkeypatch):
    """runpod_pods.encerrar aceita 404 (o pod já não existe): com a consulta, o 404 confirma."""
    prov = ComSituacao(situacoes={"p1": [None]})
    orq = _orq(tmp_path, prov)
    pod = _pod(orq)
    orq._encerrar(pod, "fim")
    assert pod.encerramento == ln.ENC_CONFIRMADO and not pod.faturavel


def test_concurrent_closes_send_a_single_delete(tmp_path, relogio):
    """Dois caminhos fechando o mesmo pod ao mesmo tempo: com o pedido em curso, o 2º não manda
    outro."""
    prov = Falso()
    orq = _orq(tmp_path, prov)
    pod = _pod(orq)
    entrou = []

    def encerrar(pid):
        entrou.append(pid)
        orq._encerrar(pod, "outro caminho")  # chega enquanto o 1º DELETE está no ar
        Falso.encerrar(prov, pid)

    prov.encerrar = encerrar
    orq._encerrar(pod, "fim")
    assert entrou == ["p1"] and prov.chamadas == ["p1"] and pod.encerramento == ln.ENC_CONFIRMADO


def test_a_restart_reloads_the_pending_closes_and_finishes_them(tmp_path, relogio):
    """O processo caiu com um DELETE que falhou: o próximo lê o pods.json e tenta de novo
    (reconciliação)."""
    prov = Falso(erros=[OSError("rede")])
    orq = _orq(tmp_path, prov)
    _pod(orq, "p1")
    _pod(orq, "p2")
    orq._encerrar(orq.pods[0], "fim")
    novo = _orq(tmp_path, ComSituacao(situacoes={"p1": [None], "p2": [None]}))
    carregados = novo.carregar_pendentes()
    assert {p.info.id for p in carregados} == {"p1", "p2"}
    # p2 estava ligado quando o processo caiu (sobra): encerrado; p1 tinha o DELETE pendente
    novo.reconciliar(forcar=True)
    assert sorted(novo.provedor.chamadas) == ["p1", "p2"]
    assert _registrados(tmp_path) == []


def test_limpar_keeps_what_it_could_not_close(tmp_path, relogio):
    prov = Falso(erros=[OSError("rede"), None])
    orq = _orq(tmp_path, prov)
    _pod(orq, "p1")
    _pod(orq, "p2")
    assert ln.limpar(tmp_path, prov) == ln.EXIT_PENDENTE
    assert [p["id"] for p in _registrados(tmp_path)] == [
        "p1"
    ]  # o que falhou fica para a próxima vez
    assert ln.limpar(tmp_path, prov) == ln.EXIT_OK and _registrados(tmp_path) == []


def test_cost_is_counted_until_the_observed_end(tmp_path, relogio):
    prov = ComSituacao(situacoes={"p1": ["RUNNING", None]})
    orq = _orq(tmp_path, prov)
    pod = _pod(orq, usd_h=3600.0)  # US$ 1 por segundo: fácil de ler
    relogio.andar(10)
    orq._encerrar(pod, "fim")  # aceito em t+10, ainda RUNNING
    relogio.andar(5)
    assert orq.gasto() == pytest.approx(15)  # conta até agora: o término não foi observado
    orq.reconciliar(forcar=True)  # 404 em t+15
    relogio.andar(100)
    assert orq.gasto() == pytest.approx(15)
    assert orq.gasto(minimo=True) == pytest.approx(10)  # o aceite é o limite de baixo


def test_a_delete_accepted_but_whose_answer_was_lost_is_resent_and_confirmed(tmp_path, relogio):
    """O provedor aceitou o DELETE, mas a resposta se perdeu (timeout aqui): a nova tentativa recebe
    o 404 do
    pod que já não existe (runpod_pods.encerrar aceita 404) e a consulta confirma o fim."""
    prov = ComSituacao(erros=[TimeoutError("resposta perdida"), None], situacoes={"p1": [None]})
    orq = _orq(tmp_path, prov)
    pod = _pod(orq)
    assert orq._encerrar(pod, "fim") is False and pod.encerramento == ln.ENC_FALHOU
    relogio.andar(ln.ENC_BACKOFF_S[0])
    orq.reconciliar()
    assert (
        prov.chamadas == ["p1", "p1"]
        and pod.encerramento == ln.ENC_CONFIRMADO
        and pod.confirmacao == "404"
    )


def test_the_closing_hook_sees_each_state(tmp_path, relogio):
    prov = ComSituacao(erros=[OSError("rede")], situacoes={"p1": ["RUNNING", None]})
    orq = _orq(tmp_path, prov)
    vistos = []
    orq.ao_fechamento = lambda p: vistos.append(p.encerramento)
    pod = _pod(orq)
    orq._encerrar(pod, "fim")
    orq.reconciliar(forcar=True)  # aceito, ainda RUNNING
    orq.reconciliar(forcar=True)  # 404
    assert vistos == [ln.ENC_FALHOU, ln.ENC_ACEITO, ln.ENC_CONFIRMADO]
    assert pod.estado == "encerrado"


def test_a_stop_is_confirmed_by_exited_and_a_stopped_pod_can_still_be_terminated(tmp_path, relogio):
    class Parada(ComSituacao):
        def parar(self, pid):
            self.chamadas.append(f"parar:{pid}")

    prov = Parada(situacoes={"p1": ["EXITED", None]})
    orq = _orq(tmp_path, prov)
    pod = _pod(orq)
    assert orq.hibernar(pod, "fila vazia") and pod.estado == "parado" and not pod.faturavel
    assert orq.encerrar_por_id("p1", "descartado")  # o disco de volume sai junto
    assert prov.chamadas == ["parar:p1", "p1"] and pod.estado == "encerrado"


def test_one_bad_sample_excludes_a_datacenter_only_for_a_while(tmp_path, monkeypatch):
    """14/10/2026: uma medida ruim tirava o datacenter por 7 dias (um host ruim condenava todos);
    agora só por EXCLUSAO_CURTA_S; duas ruins na semana, por 7 dias; uma boa depois devolve."""
    monkeypatch.setattr(ln, "CACHE", tmp_path)
    t = [1_000_000.0]
    monkeypatch.setattr(ln.time, "time", lambda: t[0])
    ln.lembrar_datacentro("US-KS-2", 12.0, "rede lenta")
    assert ln.datacentros_lentos() == {"US-KS-2"}
    t[0] += ln.EXCLUSAO_CURTA_S + 1
    assert ln.datacentros_lentos() == set()  # recuperação controlada: volta a ser tentado
    ln.lembrar_datacentro("US-KS-2", 15.0, "rede lenta")  # a 2ª ruim na semana
    t[0] += ln.EXCLUSAO_CURTA_S + 1
    assert ln.datacentros_lentos() == {"US-KS-2"}
    ln.lembrar_datacentro("US-KS-2", 420.0, "")  # uma boa depois devolve
    assert ln.datacentros_lentos() == set() and ln.velocidades()["US-KS-2"] > 15


def test_concurrent_notes_on_the_datacenter_memory_are_not_lost(tmp_path, monkeypatch):
    import threading

    monkeypatch.setattr(ln, "CACHE", tmp_path)
    ts = [
        threading.Thread(target=ln.lembrar_datacentro, args=(f"DC-{i}", float(100 + i), ""))
        for i in range(24)
    ]
    for th in ts:
        th.start()
    for th in ts:
        th.join()
    assert len(ln.velocidades()) == 24  # nenhuma anotação perdida, o arquivo inteiro
    assert not list(tmp_path.glob("*.tmp"))


def _copia_worker(tmp_path):
    import shutil

    from render2go_worker import WORKER_DIR

    w = tmp_path / "worker"
    (w / "scripts").mkdir(parents=True)
    for nome in ("requirements.lock", "Dockerfile", "models.lock.json"):
        shutil.copy(WORKER_DIR / nome, w / nome)
    for nome in ("bootstrap_pod.sh", "pull_compiler_model.sh"):
        shutil.copy(WORKER_DIR / "scripts" / nome, w / "scripts" / nome)
    return w


def _mudar(arq, de, para):
    texto = arq.read_text()
    assert de in texto, de
    arq.write_text(texto.replace(de, para, 1))


def test_the_candidate_manifest_covers_every_real_dependency_of_the_volume(tmp_path, monkeypatch):
    """Etapa 4 (14/10/2026): o manifesto v2 (RENDER2GO_MANIFESTO_HIB=2) só pode deixar de fora o
    que o disco de volume não usa. Cada ARG que o bootstrap lê (em qualquer forma: $(arg X),
    $(arg X | sed ...)) está nele; mudar qualquer dependência real muda a versão; mudar um modelo de
    outro conjunto ou um comentário do Dockerfile, não (o v1 mudava)."""
    import json as js
    import re as rx

    from render2go_worker import WORKER_DIR

    boot = (WORKER_DIR / "scripts" / "bootstrap_pod.sh").read_text()
    lidos = set(rx.findall(r"\barg ([A-Z][A-Z0-9_]+)", boot))
    assert lidos and lidos == set(ln.manifesto_hibernacao()["args"])
    assert all(v is not None for v in ln.manifesto_hibernacao()["args"].values())

    def versoes(w):
        monkeypatch.setenv("RENDER2GO_MANIFESTO_HIB", "1")
        v1 = ln.versao_hibernacao(w)
        monkeypatch.setenv("RENDER2GO_MANIFESTO_HIB", "2")
        return v1, ln.versao_hibernacao(w)

    w = _copia_worker(tmp_path)
    base = versoes(w)
    # o que não está no volume: o v2 não muda (o v1 mudava e encerrava os hibernados à toa)
    (w / "Dockerfile").write_text((w / "Dockerfile").read_text() + "\n# comentário novo\n")
    lock = js.loads((w / "models.lock.json").read_text())
    t2v = next(n for n in lock["sets"]["t2v_a14b_fp8"] if n not in lock["sets"]["i2v_r1v2"])
    lock["models"][t2v]["sha256"] = "0" * 64
    (w / "models.lock.json").write_text(js.dumps(lock))
    v1, v2 = versoes(w)
    assert v1 != base[0] and v2 == base[1]
    # cada dependência real muda o v2
    casos = [
        lambda: _mudar(w / "requirements.lock", "==", "== "),
        lambda: _mudar(w / "Dockerfile", "ARG COMFYUI_COMMIT=", "ARG COMFYUI_COMMIT=0"),
        lambda: _mudar(w / "Dockerfile", "ARG UV_IMAGE=", "ARG UV_IMAGE=x"),
        lambda: _mudar(w / "Dockerfile", "ARG LEGENDA_MODEL_DIGEST=", "ARG LEGENDA_MODEL_DIGEST=x"),
        lambda: _mudar(w / "scripts" / "bootstrap_pod.sh", "LAYOUT_HIB=", "LAYOUT_HIB=9"),
        lambda: _mudar(w / "scripts" / "pull_compiler_model.sh", "\n", "\n# x\n"),
    ]
    anterior = versoes(w)[1]
    for mudar in casos:
        mudar()
        agora = versoes(w)[1]
        assert agora != anterior
        anterior = agora
    lock = js.loads((w / "models.lock.json").read_text())
    i2v = lock["sets"]["i2v_r1v2"][0]
    lock["models"][i2v]["sha256"] = "1" * 64  # um modelo do conjunto do pod
    (w / "models.lock.json").write_text(js.dumps(lock))
    assert versoes(w)[1] != anterior
    anterior = versoes(w)[1]
    monkeypatch.setenv("RENDER2GO_LEGENDA_POR_FOTO", "1")
    assert versoes(w)[1] != anterior


def test_the_pod_image_needs_a_digest_and_enters_the_version(monkeypatch):
    monkeypatch.delenv("RENDER2GO_POD_IMAGEM", raising=False)
    monkeypatch.delenv("RENDER2GO_MANIFESTO_HIB", raising=False)
    padrao = ln.versao_hibernacao()
    assert ln.imagem_do_pod() == ln.IMAGEM_PADRAO
    monkeypatch.setenv("RENDER2GO_POD_IMAGEM", "ghcr.io/x/render2go-pod:1")  # sem digest: recusada
    assert ln.imagem_do_pod() == ln.IMAGEM_PADRAO and ln.versao_hibernacao() == padrao
    img = "ghcr.io/x/render2go-pod@sha256:" + "a" * 64
    monkeypatch.setenv("RENDER2GO_POD_IMAGEM", img)
    assert ln.imagem_do_pod() == img and ln.versao_hibernacao() != padrao
