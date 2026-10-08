"use strict";
// Páginas do serviço GPU2GO (ADR 0003, fase 3): cliente (/) e aprovador (/aprovar).
// Os arquivos nunca passam pela API: as fotos vão direto ao armazenamento (R2) por URLs
// assinadas, uma por foto; os vídeos voltam por links assinados que expiram em 24 h.
// Compilar: cd servico/web && npm ci && npm run build  (gera app.js)
const ESTADOS = {
    aguardando_envio: "Enviando fotos",
    aguardando_aprovacao: "Em revisão",
    aprovado: "Na fila",
    processando: "Gerando vídeos",
    concluido: "Pronto",
    concluido_com_falhas: "Pronto, com falhas",
    recusado: "Recusado",
};
const EM_ANDAMENTO = new Set(["aguardando_aprovacao", "aprovado", "processando"]);
const ENVIOS_SIMULTANEOS = 4;
function el(id) {
    const e = document.getElementById(id);
    if (!e)
        throw new Error(`elemento #${id} ausente`);
    return e;
}
function criar(tag, props = {}, filhos = []) {
    const e = Object.assign(document.createElement(tag), props);
    for (const f of filhos)
        e.append(f);
    return e;
}
// token guardado só neste navegador (conveniência; pode falhar em janela anônima)
function lerToken(chave) {
    try {
        return localStorage.getItem(chave) ?? "";
    }
    catch {
        return "";
    }
}
function gravarToken(chave, valor) {
    try {
        if (valor)
            localStorage.setItem(chave, valor);
        else
            localStorage.removeItem(chave);
    }
    catch {
        /* sem armazenamento local: o token vale só nesta página */
    }
}
// identificador deste navegador (limite do teste por dispositivo e registro de auditoria);
// janela anônima ou dados apagados geram outro, e a API mantém um teto por IP
function uuid4() {
    if (typeof crypto.randomUUID === "function")
        return crypto.randomUUID();
    const b = crypto.getRandomValues(new Uint8Array(16));
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
    return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}
let DISPOSITIVO_SESSAO = "";
function dispositivo() {
    try {
        let d = localStorage.getItem("gpu2go-dispositivo");
        if (!d) {
            d = uuid4();
            localStorage.setItem("gpu2go-dispositivo", d);
        }
        return d;
    }
    catch {
        DISPOSITIVO_SESSAO || (DISPOSITIVO_SESSAO = uuid4());
        return DISPOSITIVO_SESSAO;
    }
}
function fuso() {
    try {
        return Intl.DateTimeFormat().resolvedOptions().timeZone || "";
    }
    catch {
        return "";
    }
}
async function api(caminho, token, init = {}) {
    const r = await fetch(caminho, {
        ...init,
        headers: {
            Authorization: `Bearer ${token}`,
            "Content-Type": "application/json",
            "X-Dispositivo": dispositivo(),
            "X-Fuso": fuso(),
            ...(init.headers ?? {}),
        },
    });
    const corpo = await r.json().catch(() => ({}));
    if (!r.ok) {
        const d = corpo.detail;
        const msg = typeof d === "string" ? d : d ? JSON.stringify(d) : `erro ${r.status}`;
        throw new Error(msg);
    }
    return corpo;
}
function aviso(alvo, texto, tipo = "info") {
    alvo.textContent = texto;
    alvo.className = `aviso ${tipo}`;
    alvo.hidden = !texto;
}
function quando(epoch) {
    return new Date(epoch * 1000).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}
function painelToken(chave, aoEntrar) {
    const form = el("form-token");
    const campo = el("token");
    const sair = el("sair");
    const atual = lerToken(chave);
    const entrar = (t) => {
        form.hidden = true;
        sair.hidden = false;
        aoEntrar(t);
    };
    form.addEventListener("submit", (ev) => {
        ev.preventDefault();
        const t = campo.value.trim();
        if (!t)
            return;
        gravarToken(chave, t);
        entrar(t);
    });
    sair.addEventListener("click", () => {
        gravarToken(chave, "");
        location.reload();
    });
    if (atual)
        entrar(atual);
}
// -- cliente -------------------------------------------------------------------------------
async function enviarArquivos(envios, arquivos, progresso) {
    let feitos = 0;
    const fila = [...envios];
    progresso.max = envios.length;
    progresso.value = 0;
    progresso.hidden = false;
    const trabalhar = async () => {
        for (let e = fila.shift(); e; e = fila.shift()) {
            const arq = arquivos.get(e.original);
            if (!arq)
                throw new Error(`arquivo ${e.original} sumiu da seleção`);
            for (let tentativa = 1;; tentativa++) {
                const r = await fetch(e.url, { method: "PUT", body: arq, headers: { "Content-Type": e.tipo } }).catch(() => null);
                if (r && r.ok)
                    break;
                if (tentativa >= 3)
                    throw new Error(`não foi possível enviar ${e.original}`);
                await new Promise((ok) => setTimeout(ok, 1500 * tentativa));
            }
            progresso.value = ++feitos;
        }
    };
    await Promise.all(Array.from({ length: Math.min(ENVIOS_SIMULTANEOS, envios.length) }, trabalhar));
}
function cartaoLote(l) {
    const estado = ESTADOS[l.status] ?? l.status;
    const cab = criar("div", { className: "cab" }, [
        criar("strong", { textContent: estado }),
        criar("span", { className: "fraco", textContent: ` · ${l.fotos} foto(s) · ${quando(l.criado)}` }),
    ]);
    const pedido = criar("p", { className: "pedido", textContent: `“${l.pedido.prompt}”` });
    const cartao = criar("article", { className: `cartao estado-${l.status}` }, [cab, pedido]);
    if (l.total) {
        const barra = criar("progress", { max: l.total, value: l.prontos });
        cartao.append(criar("div", { className: "linha" }, [barra, ` ${l.prontos} de ${l.total} vídeo(s)`]));
    }
    if (l.motivo)
        cartao.append(criar("p", { className: "aviso erro", textContent: `Motivo: ${l.motivo}` }));
    const prontos = l.videos.filter((v) => v.url);
    if (prontos.length) {
        const lista = criar("ul", { className: "videos" });
        for (const v of prontos) {
            lista.append(criar("li", {}, [criar("a", { href: v.url, textContent: v.arquivo, download: v.arquivo, rel: "noopener" })]));
        }
        cartao.append(lista, criar("p", { className: "fraco", textContent: "Os links valem por 24 h; recarregue para renovar." }));
    }
    const falhas = l.videos.filter((v) => v.status === "erro").length;
    if (falhas)
        cartao.append(criar("p", { className: "fraco", textContent: `${falhas} vídeo(s) não saíram.` }));
    return cartao;
}
function iniciarCliente() {
    const msg = el("msg");
    let token = "";
    let timer = 0;
    let receitas = null;
    const arquivos = new Map();
    const listar = async () => {
        window.clearTimeout(timer);
        try {
            const lotes = await api("/api/lotes", token);
            const alvo = el("lotes");
            alvo.replaceChildren(...lotes.map(cartaoLote));
            if (!lotes.length)
                alvo.append(criar("p", { className: "fraco", textContent: "Nenhum pedido ainda." }));
            if (lotes.some((l) => EM_ANDAMENTO.has(l.status)))
                timer = window.setTimeout(listar, 10000);
        }
        catch (e) {
            aviso(msg, `Não foi possível carregar os pedidos: ${e.message}`, "erro");
        }
    };
    const escolher = (lista) => {
        if (!lista || !receitas)
            return;
        const tipos = new Set(receitas.limites.tipos);
        const maximo = receitas.plano?.max_fotos ?? receitas.limites.fotos;
        let ignorados = 0;
        let excedentes = 0;
        for (const f of Array.from(lista)) {
            if (!tipos.has(f.type) || f.size > receitas.limites.bytes_foto) {
                ignorados++;
                continue;
            }
            if (arquivos.size >= maximo) {
                excedentes++;
                continue;
            }
            arquivos.set(f.webkitRelativePath || f.name, f);
        }
        atualizarContagem(ignorados, excedentes);
    };
    const atualizarContagem = (ignorados = 0, excedentes = 0) => {
        const teste = receitas?.plano?.nome === "teste";
        const maximo = receitas?.plano?.max_fotos ?? receitas?.limites.fotos ?? 0;
        const n = arquivos.size;
        let texto = teste
            ? `${n} de ${maximo} fotos escolhidas` + (n < maximo ? ` · ainda cabem ${maximo - n} neste pedido` : " · pedido completo")
            : n
                ? `${n} foto(s) selecionada(s)`
                : "";
        if (ignorados)
            texto += ` · ${ignorados} ignorada(s) (use JPG, PNG ou WebP até 50 MB)`;
        if (excedentes)
            texto += ` · ${excedentes} a mais ficaram de fora (máximo de ${maximo} fotos por pedido)`;
        el("contagem").textContent = texto;
        el("enviar").textContent = teste && n ? `Enviar pedido com ${n} foto(s)` : "Enviar pedido";
    };
    // teste gratuito: só 480p, vídeos de 3 s, um pedido com até 10 fotos (a API confere tudo de novo)
    const aplicarPlano = (plano) => {
        const avisoPlano = el("plano-aviso");
        if (!plano || plano.nome !== "teste")
            return;
        const qualidade = el("qualidade");
        for (const o of Array.from(qualidade.options))
            if (!plano.qualidades?.includes(o.value))
                o.remove();
        const duracao = el("duracao");
        for (const o of Array.from(duracao.options))
            if (!plano.duracoes?.includes(Number(o.value)))
                o.remove();
        duracao.value = String(plano.duracoes?.[0] ?? 3);
        if (plano.disponivel) {
            avisoPlano.replaceChildren(criar("strong", { textContent: `Teste gratuito: escolha até ${plano.max_fotos} fotos e envie todas juntas, em um único pedido.` }), criar("br"), `Cada foto vira um vídeo de 3 s em 480p. Depois de enviar, o teste não aceita outro pedido, então escolha todas as fotos antes de clicar em "Enviar pedido". Os testes são gerados em grupo: os vídeos ficam prontos em até uns 20 minutos depois da revisão.`);
            avisoPlano.className = "aviso info";
            avisoPlano.hidden = false;
            atualizarContagem();
        }
        else {
            aviso(avisoPlano, `Teste gratuito já usado: ${plano.motivo ?? ""}`.trim(), "erro");
            for (const c of Array.from(el("form-lote").elements))
                c.disabled = true;
        }
    };
    painelToken("gpu2go-token-cliente", async (t) => {
        token = t;
        try {
            receitas = await api("/api/receitas", token);
        }
        catch (e) {
            aviso(msg, `Token inválido ou serviço fora do ar: ${e.message}`, "erro");
            return;
        }
        const sel = el("receita");
        sel.replaceChildren(...receitas.receitas.map((r) => criar("option", { value: r.id, textContent: `${r.label} (${r.nicho})` })));
        aplicarPlano(receitas.plano);
        if (receitas.autorizacao)
            el("texto-autorizacao").textContent = receitas.autorizacao.texto;
        el("painel").hidden = false;
        listar();
    });
    el("fotos").addEventListener("change", (ev) => escolher(ev.target.files));
    el("pasta").addEventListener("change", (ev) => escolher(ev.target.files));
    el("limpar").addEventListener("click", () => {
        arquivos.clear();
        atualizarContagem();
    });
    el("consentimento").addEventListener("change", (ev) => {
        const marcado = ev.target.checked;
        el("bloco-declarante").hidden = !marcado;
        el("declarante").required = marcado;
    });
    el("form-lote").addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const botao = el("enviar");
        if (!arquivos.size)
            return aviso(msg, "Escolha as fotos ou uma pasta.", "erro");
        const nomes = [...arquivos.keys()];
        const duracao = Number(el("duracao").value) || null;
        const corpo = {
            prompt: el("prompt").value.trim(),
            receita: el("receita").value,
            qualidade: el("qualidade").value,
            duracao,
            consentimento: el("consentimento").checked,
            declarante: el("consentimento").checked ? el("declarante").value.trim() : null,
            fotos: nomes.map((n) => ({ nome: n, tipo: arquivos.get(n).type, bytes: arquivos.get(n).size })),
        };
        botao.disabled = true;
        try {
            aviso(msg, "Criando o pedido…");
            const r = await api("/api/lotes", token, { method: "POST", body: JSON.stringify(corpo) });
            aviso(msg, `Enviando ${r.envios.length} foto(s)…`);
            await enviarArquivos(r.envios, arquivos, el("progresso"));
            await api(`/api/lotes/${r.lote}/enviado`, token, { method: "POST" });
            aviso(msg, "Pedido enviado. Ele passa por uma revisão rápida e entra na fila; os vídeos aparecem abaixo.", "ok");
            arquivos.clear();
            el("form-lote").reset();
            el("bloco-declarante").hidden = true;
            atualizarContagem();
            if (receitas?.plano?.nome === "teste") {
                receitas = await api("/api/receitas", token);
                aplicarPlano(receitas.plano);
            }
            listar();
        }
        catch (e) {
            aviso(msg, e.message, "erro");
        }
        finally {
            botao.disabled = receitas?.plano?.disponivel === false;
            el("progresso").hidden = true;
        }
    });
}
// -- aprovador -----------------------------------------------------------------------------
function seloAnalise(a) {
    if (a == null)
        return criar("span", { className: "selo", textContent: "analisando…" });
    if (a.erro)
        return criar("span", { className: "selo erro", textContent: "análise indisponível: confira", title: a.erro });
    if (!a.pessoas && !a.rostos)
        return criar("span", { className: "selo", textContent: "sem pessoas detectadas" });
    const partes = [`contém pessoas (${Math.max(a.pessoas ?? 0, a.rostos ?? 0)})`];
    if (a.rostos)
        partes.push(`${a.rostos} rosto(s)`);
    return criar("span", { className: "selo pessoas", textContent: `⚠ ${partes.join(" · ")}` });
}
// resumo do lote: pessoas detectadas × autorização marcada pelo cliente (ADR 0004, item 5)
function resumoPessoas(p) {
    if (!p.analise_ligada)
        return null;
    const com = p.fotos.filter((f) => f.analise && !f.analise.erro && ((f.analise.pessoas ?? 0) > 0 || (f.analise.rostos ?? 0) > 0)).length;
    const pendentes = p.fotos.filter((f) => f.analise == null).length;
    if (!com) {
        const texto = pendentes ? `Analisando ${pendentes} foto(s)…` : "Nenhuma pessoa detectada (a análise pode errar; confira as miniaturas).";
        return criar("p", { className: "fraco", textContent: texto });
    }
    const autorizado = p.pedido.consentimento === true;
    return criar("p", {
        className: autorizado ? "aviso alerta" : "aviso erro",
        textContent: autorizado
            ? `⚠ Pessoas em ${com} de ${p.fotos.length} foto(s). O cliente marcou ter autorização por escrito.`
            : `⚠ Pessoas em ${com} de ${p.fotos.length} foto(s) e o cliente NÃO marcou a autorização por escrito. Recuse, ou peça a autorização antes de aprovar.`,
    });
}
function cartaoPendente(p, token, aoMudar) {
    const msg = criar("p", { className: "aviso", hidden: true });
    const fotos = criar("div", { className: "grade" });
    for (const f of p.fotos) {
        const filhos = [criar("img", { src: f.url, alt: f.nome, loading: "lazy" })];
        if (p.analise_ligada)
            filhos.push(seloAnalise(f.analise));
        fotos.append(criar("a", { href: f.url, target: "_blank", rel: "noopener", className: "foto" }, filhos));
    }
    const pedido = p.pedido;
    const pessoas = resumoPessoas(p);
    const a = p.autorizacao;
    const linhaAutorizacao = criar("p", {
        className: "fraco autorizacao",
        textContent: a
            ? `Autorização declarada por ${a.declarante} em ${quando(a.quando)} · dispositivo ${curto(a.dispositivo)} · IP ${a.ip ?? "—"} · texto ${a.texto_versao}`
            : "Autorização das pessoas das fotos: não marcada.",
    });
    const aprovar = criar("button", { textContent: "Aprovar", className: "primario" });
    const recusar = criar("button", { textContent: "Recusar" });
    const motivo = criar("input", { placeholder: "Motivo da recusa (o cliente vê)", maxLength: 500 });
    const agir = async (acao) => {
        aprovar.disabled = recusar.disabled = true;
        try {
            const corpo = acao === "recusar" ? JSON.stringify({ motivo: motivo.value.trim() }) : undefined;
            await api(`/api/admin/lotes/${p.lote}/${acao}`, token, { method: "POST", body: corpo });
            aoMudar();
        }
        catch (e) {
            aviso(msg, e.message, "erro");
            aprovar.disabled = recusar.disabled = false;
        }
    };
    aprovar.addEventListener("click", () => agir("aprovar"));
    recusar.addEventListener("click", () => {
        if (motivo.value.trim().length < 3)
            return aviso(msg, "Escreva o motivo da recusa.", "erro");
        agir("recusar");
    });
    return criar("article", { className: "cartao" }, [
        criar("div", { className: "cab" }, [
            criar("strong", { textContent: `${p.fotos.length} foto(s)` }),
            criar("span", {
                className: "fraco",
                textContent: ` · ${p.cliente_nome ?? p.cliente}${p.plano ? ` (${p.plano})` : ""} · IP ${p.ip ?? "—"} · ${quando(p.criado)}`,
            }),
        ]),
        criar("p", { className: "pedido", textContent: `“${pedido.prompt}”` }),
        criar("p", {
            className: "fraco",
            textContent: `${pedido.receita} · ${pedido.qualidade} · ${pedido.duracao ?? "padrão"} s · ${pedido.variacoes} variação(ões) · filtro C1: ${p.filtro_c1 || "ok"}`,
        }),
        linhaAutorizacao,
        ...(pessoas ? [pessoas] : []),
        fotos,
        criar("div", { className: "acoes" }, [aprovar, motivo, recusar]),
        msg,
    ]);
}
function curto(id) {
    return id ? id.slice(0, 8) : "—";
}
async function baixarAuditoria(lote, token) {
    const dados = await api(`/api/admin/lotes/${lote}/auditoria`, token);
    const url = URL.createObjectURL(new Blob([JSON.stringify(dados, null, 2)], { type: "application/json" }));
    const a = criar("a", { href: url, download: `auditoria-${lote}.json` });
    document.body.append(a);
    a.click();
    a.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 10000);
}
function itemHistorico(x, token, aoMudar) {
    const linhas = [
        criar("div", { className: "cab" }, [
            criar("span", { className: `etiqueta ${x.status}`, textContent: ESTADOS[x.status] ?? x.status }),
            criar("span", {
                className: "fraco",
                textContent: ` · ${quando(x.criado)} · ${x.cliente_nome} (${x.plano}) · ${x.fotos} foto(s) · IP ${x.ip ?? "—"} · dispositivo ${curto(x.dispositivo)}`,
            }),
        ]),
        criar("p", { className: "pedido", textContent: `“${x.prompt}”` }),
    ];
    const detalhes = [];
    if (x.aprovado_em)
        detalhes.push(`aprovado em ${quando(x.aprovado_em)} por ${x.aprovado_por ?? "?"}`);
    if (x.total)
        detalhes.push(`${x.prontos} de ${x.total} vídeo(s) prontos`);
    if (x.concluido_em)
        detalhes.push(`fechado em ${quando(x.concluido_em)}`);
    if (x.fotos_apagadas)
        detalhes.push("fotos de entrada já apagadas");
    detalhes.push(x.declarante ? `autorização declarada por ${x.declarante}` : "sem autorização declarada");
    if (detalhes.length)
        linhas.push(criar("p", { className: "fraco", textContent: detalhes.join(" · ") }));
    if (x.motivo)
        linhas.push(criar("p", { className: "aviso erro", textContent: `Motivo: ${x.motivo}` }));
    const auditoria = criar("button", { type: "button", textContent: "Baixar registro de auditoria" });
    auditoria.addEventListener("click", () => baixarAuditoria(x.lote, token).catch((e) => window.alert(e.message)));
    const acoes = criar("div", { className: "acoes" }, [auditoria]);
    linhas.push(acoes);
    if (x.cliente_ativo) {
        const botao = criar("button", { type: "button", textContent: "Desativar o código deste cliente" });
        botao.addEventListener("click", async () => {
            if (!window.confirm(`Desativar o código de acesso de ${x.cliente_nome}? Ele deixa de entrar na página.`))
                return;
            botao.disabled = true;
            try {
                await api(`/api/admin/clientes/${x.cliente}/desativar`, token, { method: "POST" });
                aoMudar();
            }
            catch (e) {
                botao.disabled = false;
                window.alert(e.message);
            }
        });
        acoes.append(botao);
    }
    else {
        linhas.push(criar("p", { className: "fraco", textContent: "Código do cliente desativado." }));
    }
    return criar("article", { className: "cartao hist" }, linhas);
}
function tabelaIps(ips) {
    if (!ips.length)
        return criar("p", { className: "fraco", textContent: "Nenhum pedido com IP registrado ainda." });
    const th = (t, num = false) => criar("th", { textContent: t, className: num ? "num" : "", scope: "col" });
    const td = (t, num = false) => criar("td", { textContent: String(t), className: num ? "num" : "" });
    const cab = criar("tr", {}, [
        th("IP"),
        th("Fotos", true),
        th("Fotos de teste", true),
        th("Pedidos", true),
        th("Clientes", true),
        th("Dispositivos", true),
        th("Último pedido"),
    ]);
    const linhas = ips.map((u) => criar("tr", {}, [
        td(u.ip),
        td(u.fotos, true),
        td(u.fotos_teste, true),
        td(u.pedidos, true),
        td(u.clientes, true),
        td(u.dispositivos, true),
        td(quando(u.ultimo)),
    ]));
    return criar("table", {}, [criar("thead", {}, [cab]), criar("tbody", {}, linhas)]);
}
function tabelaDispositivos(ds) {
    if (!ds.length)
        return criar("p", { className: "fraco", textContent: "Nenhum pedido com dispositivo registrado ainda." });
    const th = (t, num = false) => criar("th", { textContent: t, className: num ? "num" : "", scope: "col" });
    const td = (t, num = false) => criar("td", { textContent: String(t), className: num ? "num" : "" });
    const cab = criar("tr", {}, [th("Dispositivo"), th("IPs"), th("Códigos", true), th("Fotos de teste", true), th("Fusos"), th("Sinais"), th("Último pedido")]);
    const linhas = ds.map((d) => {
        const sinais = [d.ips.length > 1 ? `trocou de IP (${d.ips.length})` : "", d.clientes.length > 1 ? `vários códigos (${d.clientes.length})` : "", d.fusos.length > 1 ? "fusos diferentes" : ""].filter(Boolean);
        return criar("tr", { className: sinais.length ? "sinal" : "" }, [
            td(curto(d.dispositivo)),
            td(d.ips.join(", ")),
            td(d.clientes.length, true),
            td(d.fotos_teste, true),
            td(d.fusos.join(", ") || "—"),
            td(sinais.length ? `⚠ ${sinais.join(" · ")}` : "—"),
            td(quando(d.ultimo)),
        ]);
    });
    return criar("table", {}, [criar("thead", {}, [cab]), criar("tbody", {}, linhas)]);
}
function iniciarAprovador() {
    const msg = el("msg");
    let token = "";
    let grupo = "todos";
    const listarHistorico = async () => {
        const r = await api(`/api/admin/historico?grupo=${grupo}`, token);
        const alvo = el("historico");
        alvo.replaceChildren(...r.lotes.map((x) => itemHistorico(x, token, listar)));
        if (!r.lotes.length)
            alvo.append(criar("p", { className: "fraco", textContent: "Nenhum pedido neste filtro." }));
        el("ips").replaceChildren(tabelaIps(r.ips));
        el("dispositivos").replaceChildren(tabelaDispositivos(r.dispositivos));
    };
    const listar = async () => {
        try {
            const lotes = await api("/api/admin/lotes", token);
            const alvo = el("pendentes");
            alvo.replaceChildren(...lotes.map((p) => cartaoPendente(p, token, listar)));
            if (!lotes.length)
                alvo.append(criar("p", { className: "fraco", textContent: "Nada aguardando revisão." }));
            await listarHistorico();
            aviso(msg, "");
        }
        catch (e) {
            aviso(msg, `Não foi possível carregar: ${e.message}`, "erro");
        }
    };
    for (const b of Array.from(el("filtros-historico").querySelectorAll("button"))) {
        b.addEventListener("click", () => {
            grupo = b.dataset.grupo ?? "todos";
            for (const o of Array.from(el("filtros-historico").querySelectorAll("button"))) {
                o.classList.toggle("ativo", o === b);
                o.setAttribute("aria-pressed", String(o === b));
            }
            listarHistorico().catch((e) => aviso(msg, `Não foi possível carregar: ${e.message}`, "erro"));
        });
    }
    painelToken("gpu2go-token-admin", (t) => {
        token = t;
        el("painel").hidden = false;
        listar();
        window.setInterval(listar, 30000);
    });
    el("atualizar").addEventListener("click", listar);
    el("form-cliente").addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const saida = el("token-novo");
        try {
            const r = await api("/api/admin/clientes", token, {
                method: "POST",
                body: JSON.stringify({
                    nome: el("cliente-nome").value.trim(),
                    plano: el("cliente-plano").value,
                    cota_fotos_dia: Number(el("cliente-cota").value) || 500,
                }),
            });
            saida.textContent = `Cliente ${r.cliente}. Token (aparece só agora; envie ao cliente por um canal seguro): ${r.token}`;
            saida.className = "aviso ok token";
            saida.hidden = false;
            el("form-cliente").reset();
        }
        catch (e) {
            aviso(saida, e.message, "erro");
        }
    });
}
document.addEventListener("DOMContentLoaded", () => {
    if (document.body.dataset.pagina === "aprovar")
        iniciarAprovador();
    else
        iniciarCliente();
});
