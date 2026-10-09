// Página do aprovador (/aprovar, só o dono): revisão dos pedidos (ADR 0004), histórico, uso por IP e
// por dispositivo, mensagens de suporte e cadastro de clientes.
import {
  ESTADOS,
  type ItemLista,
  anunciar,
  api,
  aviso,
  baixarJson,
  confirmar,
  criar,
  curto,
  el,
  hora,
  icone,
  marcarErro,
  ocupado,
  plural,
  quando,
  reais,
  reconciliar,
  repetir,
  sessao,
  tempo,
  textoErro,
  urlSegura,
  vazio,
} from "./comum";

// análise automática de cada foto (render2go_servico/pessoas.py); null = ainda analisando
type Analise = { pessoas?: number; rostos?: number; erro?: string } | null;
type Autorizacao = { declarante: string; quando: number; ip: string | null; dispositivo: string | null; texto_versao: string };
type Pendente = {
  lote: string;
  cliente: string;
  cliente_nome?: string | null;
  plano?: string | null;
  ip?: string | null;
  dispositivo?: string | null;
  autorizacao?: Autorizacao | null;
  modelo?: string;
  criado: number;
  pedido: { prompt: string; receita: string; qualidade: string; duracao: number | null; variacoes: number; consentimento?: boolean };
  filtro_c1: string;
  analise_ligada?: boolean;
  fotos: { nome: string; url: string; analise?: Analise }[];
};
type ItemHistorico = {
  lote: string;
  cliente: string;
  cliente_nome: string;
  plano: string;
  cliente_ativo: boolean;
  status: string;
  prompt: string;
  fotos: number;
  ip: string | null;
  criado: number;
  aprovado_em: number | null;
  aprovado_por: string | null;
  concluido_em: number | null;
  motivo: string | null;
  fotos_apagadas: number | null;
  dispositivo: string | null;
  fuso: string | null;
  declarante: string | null;
  autorizado_em: number | null;
  modelo?: string;
  prontos: number;
  total: number;
};
type UsoIp = { ip: string; pedidos: number; fotos: number; fotos_teste: number; clientes: number; dispositivos: number; ultimo: number };
type UsoDispositivo = {
  dispositivo: string;
  ips: string[];
  clientes: string[];
  fusos: string[];
  impressoes?: string[];
  mesmo_navegador?: string[];
  pedidos: number;
  fotos_teste: number;
  ultimo: number;
};
type MensagemSuporte = {
  id: string;
  cliente_id: string;
  cliente_nome: string | null;
  plano: string | null;
  email: string | null;
  assunto: string | null;
  mensagem: string;
  ip: string | null;
  dispositivo: string | null;
  quando: number;
  enviado: number;
  erro: string | null;
};

type Cliente = { id: string; nome: string; plano: string; ativo: number | boolean; criado: number; saldo: number };
type Clientes = { clientes: Cliente[]; precos: Record<string, number>; planos_cobrados: string[] };

const QUALIDADES: Record<string, string> = { rapida: "rápida (480p)", alta: "alta (720p)" };

function seloAnalise(a: Analise | undefined): HTMLElement {
  if (a == null) return criar("span", { className: "selo", textContent: "analisando…" });
  if (a.erro) return criar("span", { className: "selo erro", textContent: "análise indisponível: confira", title: a.erro });
  if (!a.pessoas && !a.rostos) return criar("span", { className: "selo", textContent: "sem pessoas detectadas" });
  const partes = [`contém pessoas (${Math.max(a.pessoas ?? 0, a.rostos ?? 0)})`];
  if (a.rostos) partes.push(plural(a.rostos, "rosto", "rostos"));
  return criar("span", { className: "selo pessoas" }, [icone("pessoa"), partes.join(" · ")]);
}

// resumo do lote: pessoas detectadas × autorização marcada pelo cliente (ADR 0004, item 5)
function resumoPessoas(p: Pendente): HTMLElement | null {
  if (!p.analise_ligada) return null;
  const com = p.fotos.filter((f) => f.analise && !f.analise.erro && ((f.analise.pessoas ?? 0) > 0 || (f.analise.rostos ?? 0) > 0)).length;
  const pendentes = p.fotos.filter((f) => f.analise == null).length;
  if (!com) {
    const texto = pendentes ? `Analisando ${plural(pendentes, "foto", "fotos")}…` : "Nenhuma pessoa detectada (a análise pode errar; confira as miniaturas).";
    return criar("p", { className: "fraco", textContent: texto });
  }
  const autorizado = p.pedido.consentimento === true;
  const caixa = criar("div");
  aviso(
    caixa,
    autorizado
      ? `Pessoas em ${com} de ${plural(p.fotos.length, "foto", "fotos")}. O cliente marcou ter autorização por escrito.`
      : `Pessoas em ${com} de ${plural(p.fotos.length, "foto", "fotos")} e o cliente NÃO marcou a autorização por escrito. Recuse, ou peça a autorização antes de aprovar.`,
    autorizado ? "alerta" : "erro",
  );
  return caixa;
}

function meta(partes: (string | Node)[]): HTMLElement {
  return criar("p", { className: "meta" }, partes.map((x) => criar("span", {}, [x])));
}

function cartaoPendente(p: Pendente, token: string, aoMudar: () => void, imagens: Map<string, HTMLImageElement>): HTMLElement {
  const msg = criar("div", { className: "aviso", hidden: true });
  const fotos = criar("div", { className: "grade" });
  for (const f of p.fotos) {
    const chave = `${p.lote}/${f.nome}`;
    const url = urlSegura(f.url);
    // a mesma <img> volta a cada atualização: a URL assinada muda, mas a foto não é baixada de novo
    let img = imagens.get(chave);
    if (!img) {
      img = criar("img", { alt: f.nome, loading: "lazy", decoding: "async", width: 240, height: 240 });
      if (url) img.src = url;
      imagens.set(chave, img);
    }
    const filhos: (Node | string)[] = [img];
    if (p.analise_ligada) filhos.push(seloAnalise(f.analise));
    fotos.append(criar("a", { href: url ?? "#", target: "_blank", rel: "noopener noreferrer", className: "foto", title: `Abrir ${f.nome} em outra aba` }, filhos));
  }
  const pedido = p.pedido;
  const a = p.autorizacao;
  const aprovar = criar("button", { type: "button", className: "botao sucesso", dados: { acao: "aprovar", foco: `${p.lote}:aprovar` } }, [icone("ok"), "Aprovar"]);
  const recusar = criar("button", { type: "button", className: "botao perigo", dados: { acao: "recusar", foco: `${p.lote}:recusar` } }, [icone("bloquear"), "Recusar"]);
  const idMotivo = `motivo-${p.lote}`;
  const motivo = criar("input", { id: idMotivo, placeholder: "Motivo da recusa (o cliente vê)", maxLength: 500, dados: { foco: `${p.lote}:motivo` } });
  motivo.setAttribute("aria-label", "Motivo da recusa (o cliente vê)");
  const agir = async (acao: "aprovar" | "recusar", botao: HTMLButtonElement) => {
    aprovar.disabled = recusar.disabled = true;
    ocupado(botao, true);
    try {
      const corpo = acao === "recusar" ? JSON.stringify({ motivo: motivo.value.trim() }) : undefined;
      await api(`/api/admin/lotes/${p.lote}/${acao}`, token, { method: "POST", body: corpo });
      anunciar(acao === "aprovar" ? "Pedido aprovado e enviado para a fila." : "Pedido recusado.");
      aoMudar();
    } catch (e) {
      aviso(msg, textoErro(e), "erro");
      anunciar(textoErro(e), true);
      aprovar.disabled = recusar.disabled = false;
      ocupado(botao, false);
    }
  };
  aprovar.addEventListener("click", () => agir("aprovar", aprovar));
  recusar.addEventListener("click", () => {
    if (motivo.value.trim().length < 3) {
      marcarErro(motivo, true);
      aviso(msg, "Escreva o motivo da recusa (o cliente vê).", "erro");
      motivo.focus();
      return;
    }
    agir("recusar", recusar);
  });
  motivo.addEventListener("input", () => marcarErro(motivo, false));
  return criar("article", { className: "cartao pendente surgir", attrs: { "aria-label": `Pedido de ${p.cliente_nome ?? p.cliente}` } }, [
    criar("div", { className: "lote-cab" }, [
      criar("span", { className: "selo estado-aguardando_aprovacao" }, [icone("relogio"), "Em análise"]),
      criar("strong", { textContent: p.cliente_nome ?? p.cliente }),
      p.plano ? criar("span", { className: "selo", textContent: p.plano }) : null,
      criar("span", { className: "fraco" }, [plural(p.fotos.length, "foto", "fotos"), " · ", tempo(p.criado)]),
    ]),
    criar("p", { className: "pedido", textContent: pedido.prompt }),
    meta([
      p.modelo ?? "Wan 2.2",
      pedido.receita,
      QUALIDADES[pedido.qualidade] ?? pedido.qualidade,
      pedido.duracao ? `${pedido.duracao} s` : "duração padrão",
      plural(pedido.variacoes, "variação", "variações"),
      `filtro ${p.filtro_c1 || "C1: ok"}`, // o motivo já vem como "C1: ..."
      `IP ${p.ip ?? "—"}`,
    ]),
    criar("p", {
      className: "fraco autorizacao",
      textContent: a
        ? `Autorização marcada em ${quando(a.quando)} · dispositivo ${curto(a.dispositivo)} · IP ${a.ip ?? "—"} · texto ${a.texto_versao}`
        : "Autorização das pessoas das fotos: não marcada.",
    }),
    resumoPessoas(p),
    fotos,
    criar("div", { className: "decisao" }, [aprovar, motivo, recusar]),
    msg,
  ]);
}

function itemHistorico(x: ItemHistorico, token: string, aoMudar: () => void): HTMLElement {
  const msg = criar("div", { className: "aviso", hidden: true });
  const detalhes: string[] = [];
  if (x.aprovado_em) detalhes.push(`aprovado em ${quando(x.aprovado_em)} por ${x.aprovado_por ?? "?"}`);
  if (x.total) detalhes.push(`${x.prontos} de ${plural(x.total, "vídeo pronto", "vídeos prontos")}`);
  if (x.concluido_em) detalhes.push(`fechado em ${quando(x.concluido_em)}`);
  if (x.fotos_apagadas) detalhes.push("fotos de entrada já apagadas");
  detalhes.push(x.autorizado_em ? `autorização marcada em ${quando(x.autorizado_em)}` : "sem autorização marcada");
  if (x.modelo) detalhes.push(`modelo ${x.modelo}`);
  const auditoria = criar("button", { type: "button", className: "botao pequeno", dados: { foco: `${x.lote}:auditoria` } }, [icone("baixar"), "Baixar registro de auditoria"]);
  auditoria.addEventListener("click", async () => {
    ocupado(auditoria, true);
    try {
      baixarJson(await api<unknown>(`/api/admin/lotes/${x.lote}/auditoria`, token), `auditoria-${x.lote}.json`);
    } catch (e) {
      aviso(msg, textoErro(e), "erro");
    } finally {
      ocupado(auditoria, false);
    }
  });
  const acoes = criar("div", { className: "acoes" }, [auditoria]);
  const linhas: (Node | null)[] = [
    criar("div", { className: "lote-cab" }, [
      criar("span", { className: `selo estado-${x.status}`, textContent: ESTADOS[x.status] ?? x.status }),
      criar("strong", { textContent: x.cliente_nome }),
      criar("span", { className: "selo", textContent: x.plano }),
      criar("span", { className: "fraco" }, [tempo(x.criado)]),
    ]),
    criar("p", { className: "pedido", textContent: x.prompt }),
    meta([plural(x.fotos, "foto", "fotos"), `IP ${x.ip ?? "—"}`, `dispositivo ${curto(x.dispositivo)}`]),
    criar("p", { className: "fraco", textContent: detalhes.join(" · ") }),
  ];
  if (x.motivo) {
    const m = criar("div");
    aviso(m, `Motivo: ${x.motivo}`, "erro");
    linhas.push(m);
  }
  linhas.push(acoes, msg);
  if (x.cliente_ativo) {
    const botao = criar("button", { type: "button", className: "botao pequeno perigo", dados: { foco: `${x.lote}:desativar` } }, [icone("bloquear"), "Desativar o código deste cliente"]);
    botao.addEventListener("click", async () => {
      const ok = await confirmar({
        titulo: "Desativar o código?",
        texto: `O código de acesso de ${x.cliente_nome} deixa de valer na hora: a pessoa não entra mais na página.`,
        confirmar: "Desativar",
        perigo: true,
      });
      if (!ok) return;
      ocupado(botao, true);
      try {
        await api(`/api/admin/clientes/${x.cliente}/desativar`, token, { method: "POST" });
        anunciar(`Código de ${x.cliente_nome} desativado.`);
        aoMudar();
      } catch (e) {
        ocupado(botao, false);
        aviso(msg, textoErro(e), "erro");
      }
    });
    acoes.append(botao);
  } else {
    acoes.append(criar("span", { className: "selo erro" }, [icone("bloquear"), "Código do cliente desativado"]));
  }
  return criar("article", { className: "cartao hist" }, linhas);
}

function tabela(cabecalhos: [string, boolean][], linhas: { celulas: (string | number | Node)[]; classe?: string }[], legenda: string): HTMLElement {
  const th = ([t, num]: [string, boolean]) => criar("th", { textContent: t, className: num ? "num" : "", scope: "col" });
  const corpo = linhas.map((l) =>
    criar(
      "tr",
      { className: l.classe ?? "" },
      l.celulas.map((c, i) => criar("td", { className: cabecalhos[i][1] ? "num" : "" }, [c instanceof Node ? c : String(c)])),
    ),
  );
  return criar("table", {}, [criar("caption", { className: "visualmente-oculto", textContent: legenda }), criar("thead", {}, [criar("tr", {}, cabecalhos.map(th))]), criar("tbody", {}, corpo)]);
}

function tabelaIps(ips: UsoIp[]): HTMLElement {
  if (!ips.length) return criar("p", { className: "fraco vazio-tabela", textContent: "Nenhum pedido com IP registrado ainda." });
  return tabela(
    [["IP", false], ["Fotos", true], ["Fotos de teste", true], ["Pedidos", true], ["Clientes", true], ["Dispositivos", true], ["Último pedido", false]],
    ips.map((u) => ({ celulas: [u.ip, u.fotos, u.fotos_teste, u.pedidos, u.clientes, u.dispositivos, tempo(u.ultimo)] })),
    "Uso por IP",
  );
}

function tabelaDispositivos(ds: UsoDispositivo[]): HTMLElement {
  if (!ds.length) return criar("p", { className: "fraco vazio-tabela", textContent: "Nenhum pedido com dispositivo registrado ainda." });
  return tabela(
    [["Dispositivo", false], ["IPs", false], ["Códigos", true], ["Fotos de teste", true], ["Fusos", false], ["Sinais", false], ["Último pedido", false]],
    ds.map((d) => {
      const sinais = [
        d.ips.length > 1 ? `trocou de IP (${d.ips.length})` : "",
        d.clientes.length > 1 ? `vários códigos (${d.clientes.length})` : "",
        d.fusos.length > 1 ? "fusos diferentes" : "",
        d.mesmo_navegador?.length ? `mesmo navegador de ${d.mesmo_navegador.map(curto).join(", ")}` : "",
      ].filter(Boolean);
      return {
        classe: sinais.length ? "sinal" : "",
        celulas: [curto(d.dispositivo), d.ips.join(", "), d.clientes.length, d.fotos_teste, d.fusos.join(", ") || "—", sinais.length ? `⚠ ${sinais.join(" · ")}` : "—", tempo(d.ultimo)],
      };
    }),
    "Dispositivos",
  );
}

function listaSuporte(msgs: MensagemSuporte[]): ItemLista[] {
  return msgs.map((m) => ({
    chave: m.id,
    assinatura: JSON.stringify([m.enviado, m.erro]),
    criar: () => {
      const situacao = criar("p", {
        className: m.erro ? "" : "fraco",
        textContent: `${m.enviado ? "enviada por e-mail" : m.erro ? `e-mail não saiu: ${m.erro}` : "guardada (e-mail não configurado)"} · IP ${m.ip ?? "—"} · dispositivo ${curto(m.dispositivo)}`,
      });
      if (m.erro) aviso(situacao, situacao.textContent ?? "", "erro");
      const email = m.email ? criar("a", { href: `mailto:${encodeURIComponent(m.email).replace("%40", "@")}`, textContent: m.email }) : "—";
      return criar("article", { className: "cartao msg-suporte pilha" }, [
        criar("div", { className: "lote-cab" }, [
          criar("strong", { textContent: m.assunto || "Sem assunto" }),
          criar("span", { className: "fraco" }, [tempo(m.quando), ` · ${m.cliente_nome ?? (m.cliente_id || "sem código")} (${m.plano ?? "?"}) · responder para `, email]),
        ]),
        criar("p", { className: "texto", textContent: m.mensagem }),
        situacao,
      ]);
    },
  }));
}

function tabelaClientes(r: Clientes, token: string, msg: HTMLElement): HTMLElement {
  if (!r.clientes.length) return criar("p", { className: "fraco vazio-tabela", textContent: "Nenhum cliente cadastrado ainda." });
  return tabela(
    [["Cliente", false], ["Plano", false], ["Saldo", true], ["Situação", false], ["Cadastrado", false], ["Extrato", false]],
    r.clientes.map((k) => {
      const cobrado = r.planos_cobrados.includes(k.plano);
      let extrato: Node | string = "—";
      if (cobrado) {
        const botao = criar("button", { type: "button", className: "botao pequeno" }, [icone("baixar"), "Baixar"]);
        botao.setAttribute("aria-label", `Baixar o extrato de ${k.nome}`);
        botao.addEventListener("click", async () => {
          ocupado(botao, true);
          try {
            baixarJson(await api<unknown>(`/api/admin/clientes/${k.id}/extrato`, token), `extrato-${k.id}.json`);
          } catch (e) {
            aviso(msg, textoErro(e), "erro");
          } finally {
            ocupado(botao, false);
          }
        });
        extrato = botao;
      }
      return { celulas: [k.nome, k.plano, cobrado ? reais(k.saldo) : "—", k.ativo ? "ativo" : "desativado", tempo(k.criado), extrato] };
    }),
    "Clientes e saldo",
  );
}

function iniciarAprovador(): void {
  const msg = el("msg");
  const atualizar = el<HTMLButtonElement>("atualizar");
  const imagens = new Map<string, HTMLImageElement>();
  let token = "";
  let grupo = "todos";

  const listarHistorico = async () => {
    const r = await api<{ lotes: ItemHistorico[]; ips: UsoIp[]; dispositivos: UsoDispositivo[] }>(`/api/admin/historico?grupo=${encodeURIComponent(grupo)}`, token);
    reconciliar(
      el("historico"),
      r.lotes.map((x) => ({ chave: x.lote, assinatura: JSON.stringify(x), criar: () => itemHistorico(x, token, listar) })),
      () => vazio("Nenhum pedido neste filtro.", "Troque o filtro para ver outros pedidos.", "pedidos"),
    );
    el("ips").replaceChildren(tabelaIps(r.ips));
    el("dispositivos").replaceChildren(tabelaDispositivos(r.dispositivos));
    const sup = await api<{ email_configurado: boolean; mensagens: MensagemSuporte[] }>("/api/admin/suporte", token);
    el("suporte-config").textContent = sup.email_configurado
      ? "As mensagens também chegam no seu e-mail."
      : "E-mail não configurado: as mensagens ficam só aqui (veja docs/09, seção de suporte).";
    reconciliar(el("suporte"), listaSuporte(sup.mensagens), () => vazio("Nenhuma mensagem ainda.", "As mensagens enviadas pelo /suporte aparecem aqui.", "mensagem"));
    await listarClientes();
  };

  // -- clientes e saldo (pré-pago) ---------------------------------------------------------------
  const selCliente = el<HTMLSelectElement>("saldo-cliente");
  let assinaturaClientes = "";
  const listarClientes = async () => {
    const r = await api<Clientes>("/api/admin/clientes", token);
    const assinatura = JSON.stringify(r);
    if (assinatura === assinaturaClientes) return; // nada mudou: a tabela e a escolha ficam como estão
    assinaturaClientes = assinatura;
    el("clientes").replaceChildren(tabelaClientes(r, token, el("saldo-msg")));
    const escolhido = selCliente.value;
    const pagantes = r.clientes.filter((k) => k.ativo && r.planos_cobrados.includes(k.plano));
    selCliente.replaceChildren(
      criar("option", { value: "", textContent: pagantes.length ? "Escolha o cliente" : "Nenhum cliente do plano pago" }),
      ...pagantes.map((k) => criar("option", { value: k.id, textContent: `${k.nome} · saldo ${reais(k.saldo)}` })),
    );
    if (pagantes.some((k) => k.id === escolhido)) selCliente.value = escolhido;
    const p = r.precos;
    el("precos-info").textContent =
      `Pré-pago: 480p a ${reais(p.rapida ?? 0)} e 720p a ${reais(p.alta ?? 0)} por segundo de vídeo. ` +
      "O pedido é cobrado quando as fotos chegam; pedido recusado e vídeo que não sai voltam sozinhos para o saldo.";
  };

  const listar = async (): Promise<boolean> => {
    try {
      const lotes = await api<Pendente[]>("/api/admin/lotes", token);
      const vivos = new Set(lotes.flatMap((p) => p.fotos.map((f) => `${p.lote}/${f.nome}`)));
      for (const chave of imagens.keys()) if (!vivos.has(chave)) imagens.delete(chave);
      reconciliar(
        el("pendentes"),
        lotes.map((p) => ({
          chave: p.lote,
          assinatura: JSON.stringify([p.fotos.map((f) => [f.nome, f.analise]), p.autorizacao, p.pedido, p.filtro_c1]),
          criar: () => cartaoPendente(p, token, listar, imagens),
        })),
        () => vazio("Nada aguardando revisão.", "Os pedidos novos aparecem aqui assim que as fotos chegam.", "ok"),
      );
      el("n-pendentes").textContent = lotes.length ? String(lotes.length) : "";
      document.title = lotes.length ? `(${lotes.length}) Revisão · Render2Go` : "Revisão · Render2Go";
      await listarHistorico();
      el("atualizado").textContent = `Atualizado às ${hora()}`;
      aviso(msg, "");
    } catch (e) {
      aviso(msg, `Não foi possível carregar: ${textoErro(e)}`, "erro");
    }
    return true; // a revisão continua atualizando enquanto a aba está visível
  };
  const atualizacao = repetir(listar, 30000);

  for (const b of Array.from(el("filtros-historico").querySelectorAll<HTMLButtonElement>("button"))) {
    b.addEventListener("click", () => {
      grupo = b.dataset.grupo ?? "todos";
      for (const o of Array.from(el("filtros-historico").querySelectorAll<HTMLButtonElement>("button"))) o.setAttribute("aria-pressed", String(o === b));
      listarHistorico().catch((e) => aviso(msg, `Não foi possível carregar: ${textoErro(e)}`, "erro"));
    });
  }
  sessao("render2go-token-admin", async (t) => {
    token = t;
    await api("/api/admin/suporte", token); // confere o token antes de mostrar o painel
    atualizar.hidden = false;
    atualizacao.agora();
  });
  atualizar.addEventListener("click", async () => {
    ocupado(atualizar, true);
    await listar();
    ocupado(atualizar, false);
    anunciar("Lista atualizada.");
  });

  const formCliente = el<HTMLFormElement>("form-cliente");
  const nome = el<HTMLInputElement>("cliente-nome");
  nome.addEventListener("input", () => marcarErro(nome, false));
  formCliente.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const saida = el("token-novo");
    if (nome.value.trim().length < 2) {
      marcarErro(nome, true);
      aviso(saida, "Escreva o nome do cliente (pelo menos 2 letras).", "erro");
      nome.focus();
      return;
    }
    const botao = el<HTMLButtonElement>("cliente-enviar");
    ocupado(botao, true);
    try {
      const r = await api<{ cliente: string; token: string }>("/api/admin/clientes", token, {
        method: "POST",
        body: JSON.stringify({
          nome: nome.value.trim(),
          plano: el<HTMLSelectElement>("cliente-plano").value,
          cota_fotos_dia: Number(el<HTMLInputElement>("cliente-cota").value) || 500,
        }),
      });
      const codigo = criar("code", { id: "token-valor", textContent: r.token });
      const copiar = criar("button", { type: "button", className: "botao pequeno" }, [icone("copiar"), "Copiar"]);
      copiar.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(r.token);
          anunciar("Código copiado.");
          copiar.replaceChildren(icone("ok"), "Copiado");
        } catch {
          getSelection()?.selectAllChildren(codigo); // sem permissão: deixa selecionado para Ctrl+C
        }
      });
      aviso(
        saida,
        [
          criar("p", { textContent: `Cliente ${r.cliente}. Código de acesso (aparece só agora; envie ao cliente por um canal seguro):` }),
          criar("div", { className: "codigo-gerado" }, [codigo, copiar]),
        ],
        "ok",
      );
      saida.classList.add("token");
      anunciar("Cliente cadastrado. O código aparece só agora.");
      formCliente.reset();
      copiar.focus();
      void listarClientes().catch(() => undefined);
    } catch (e) {
      aviso(saida, textoErro(e), "erro");
    } finally {
      ocupado(botao, false);
    }
  });

  const formSaldo = el<HTMLFormElement>("form-saldo");
  const valor = el<HTMLInputElement>("saldo-valor");
  const descricao = el<HTMLInputElement>("saldo-descricao");
  for (const campo of [selCliente, valor, descricao]) campo.addEventListener("input", () => marcarErro(campo, false));
  formSaldo.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const saida = el("saldo-msg");
    const faltando: [HTMLElement, string][] = [
      [selCliente, selCliente.value ? "" : "Escolha o cliente."],
      [valor, /^-?\d{1,7}([.,]\d{1,2})?$/.test(valor.value.trim()) ? "" : "Escreva o valor em reais, ex.: 50 ou 19,90."],
      [descricao, descricao.value.trim().length >= 3 ? "" : "Escreva uma descrição (o cliente vê no extrato)."],
    ];
    const erro = faltando.find(([, m]) => m);
    if (erro) {
      marcarErro(erro[0], true);
      aviso(saida, erro[1], "erro");
      erro[0].focus();
      return;
    }
    const nome = selCliente.selectedOptions[0]?.textContent?.split(" · ")[0] ?? "o cliente";
    const ok = await confirmar({
      titulo: "Lançar no saldo?",
      texto: `${valor.value.trim().startsWith("-") ? "Descontar" : "Adicionar"} R$ ${valor.value.trim().replace(/^-/, "")} ${valor.value.trim().startsWith("-") ? "do" : "ao"} saldo de ${nome}.`,
      confirmar: "Lançar",
    });
    if (!ok) return;
    const botao = el<HTMLButtonElement>("saldo-enviar");
    ocupado(botao, true);
    try {
      const r = await api<{ saldo: number }>(`/api/admin/clientes/${selCliente.value}/saldo`, token, {
        method: "POST",
        body: JSON.stringify({ valor: valor.value.trim(), descricao: descricao.value.trim(), referencia: el<HTMLInputElement>("saldo-referencia").value.trim() || null }),
      });
      aviso(saida, `Lançado. Saldo de ${nome} agora: ${reais(r.saldo)}.`, "ok");
      anunciar(`Lançado. Saldo agora ${reais(r.saldo)}.`);
      formSaldo.reset();
      await listarClientes();
    } catch (e) {
      aviso(saida, textoErro(e), "erro");
    } finally {
      ocupado(botao, false);
    }
  });
}

document.addEventListener("DOMContentLoaded", iniciarAprovador);
