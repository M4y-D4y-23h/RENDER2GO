// Painel: revisão dos pedidos (nada vai para a GPU sem aprovação; ADR 0004) e o histórico, com o uso por IP e
// por dispositivo. No pedido em sequência, cada foto com texto próprio mostra esse texto embaixo da miniatura.
import { ESTADOS, anunciar, api, aviso, baixarJson, confirmar, criar, curto, el, icone, marcarErro, ocupado, plural, quando, reconciliar, tempo, textoErro, urlSegura, vazio } from "./comum";
import { type Controle, controle, erroDeAcao, erroDeCarga, iniciarAbas, pode, semLinhas, tabela, tempoCurto, trocar } from "./painel-base";

// análise automática de cada foto (render2go_servico/pessoas.py); null = ainda analisando
type Analise = { pessoas?: number; rostos?: number; erro?: string } | null;
type Autorizacao = { declarante: string; quando: number; ip: string | null; dispositivo: string | null; texto_versao: string };
type FotoPendente = { nome: string; url: string; analise?: Analise; texto?: string | null; filtro_c1?: string | null };
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
  pedido: { prompt: string; receita: string; qualidade: string; duracao: number | null; variacoes: number; consentimento?: boolean; textos_por_foto?: number };
  filtro_c1: string;
  analise_ligada?: boolean;
  fotos: FotoPendente[];
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

const QUALIDADES: Record<string, string> = { rapida: "rápida (480p)", alta: "alta (720p)" };
const FILTRO_OK = "C1: ok";

function seloAnalise(a: Analise | undefined): HTMLElement {
  if (a == null) return criar("span", { className: "selo", textContent: "analisando…" });
  if (a.erro) return criar("span", { className: "selo erro", textContent: "análise indisponível: confira", title: a.erro });
  if (!a.pessoas && !a.rostos) return criar("span", { className: "selo", textContent: "sem pessoas detectadas" });
  const partes = [`contém pessoas (${Math.max(a.pessoas ?? 0, a.rostos ?? 0)})`];
  if (a.rostos) partes.push(plural(a.rostos, "rosto", "rostos"));
  return criar("span", { className: "selo pessoas" }, [icone("pessoa"), partes.join(" · ")]);
}

// resumo do pedido: pessoas detectadas × autorização marcada pelo cliente (ADR 0004, item 5)
function resumoPessoas(p: Pendente): HTMLElement | null {
  if (!p.analise_ligada) return null;
  const com = p.fotos.filter((f) => f.analise && !f.analise.erro && ((f.analise.pessoas ?? 0) > 0 || (f.analise.rostos ?? 0) > 0)).length;
  const analisando = p.fotos.filter((f) => f.analise == null).length;
  if (!com) {
    const texto = analisando ? `Analisando ${plural(analisando, "foto", "fotos")}…` : "Nenhuma pessoa detectada (a análise pode errar; confira as miniaturas).";
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

function cartaoPendente(p: Pendente, aoMudar: () => void, imagens: Map<string, HTMLImageElement>): HTMLElement {
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
    const link = criar("a", { href: url ?? "#", target: "_blank", rel: "noopener noreferrer", className: "foto", title: `Abrir ${f.nome} em outra aba` }, [
      img,
      p.analise_ligada ? seloAnalise(f.analise) : null,
    ]);
    const barrado = f.texto && f.filtro_c1 && f.filtro_c1 !== FILTRO_OK;
    fotos.append(
      criar("figure", { className: `foto-revisao${f.texto ? " com-texto" : ""}` }, [
        link,
        f.texto
          ? criar("figcaption", { className: "texto-foto" }, [criar("span", { className: "visualmente-oculto", textContent: `Texto próprio de ${f.nome}: ` }), criar("q", { textContent: f.texto })])
          : null,
        barrado ? criar("span", { className: "selo erro", textContent: `texto barrado: ${f.filtro_c1}` }) : null,
      ]),
    );
  }
  const pedido = p.pedido;
  const a = p.autorizacao;
  const comTexto = p.fotos.filter((f) => f.texto);
  const aprovar = criar("button", { type: "button", className: "botao sucesso", dados: { acao: "aprovar", foco: `${p.lote}:aprovar` } }, [icone("ok"), "Aprovar"]);
  const recusar = criar("button", { type: "button", className: "botao perigo", dados: { acao: "recusar", foco: `${p.lote}:recusar` } }, [icone("bloquear"), "Recusar"]);
  const motivo = criar("input", { id: `motivo-${p.lote}`, placeholder: "Motivo da recusa (o cliente vê)", maxLength: 500, dados: { foco: `${p.lote}:motivo` } });
  motivo.setAttribute("aria-label", "Motivo da recusa (o cliente vê)");
  const agir = async (acao: "aprovar" | "recusar", botao: HTMLButtonElement) => {
    aprovar.disabled = recusar.disabled = true;
    ocupado(botao, true);
    try {
      const corpo = acao === "recusar" ? JSON.stringify({ motivo: motivo.value.trim() }) : undefined;
      await api(`api/lotes/${encodeURIComponent(p.lote)}/${acao}`, { method: "POST", body: corpo });
      anunciar(acao === "aprovar" ? "Pedido aprovado e enviado para a fila." : "Pedido recusado.");
      aoMudar();
    } catch (e) {
      erroDeAcao(e, msg);
      anunciar(textoErro(e), true);
      aprovar.disabled = recusar.disabled = false;
      ocupado(botao, false);
    }
  };
  aprovar.addEventListener("click", () => void agir("aprovar", aprovar));
  recusar.addEventListener("click", () => {
    if (motivo.value.trim().length < 3) {
      marcarErro(motivo, true);
      aviso(msg, "Escreva o motivo da recusa (o cliente vê).", "erro");
      motivo.focus();
      return;
    }
    void agir("recusar", recusar);
  });
  motivo.addEventListener("input", () => marcarErro(motivo, false));
  const textos = comTexto.length
    ? (() => {
        const caixa = criar("div");
        const distintos = new Set(comTexto.map((f) => f.texto)).size;
        aviso(
          caixa,
          `Pedido em sequência: ${plural(comTexto.length, "foto tem texto próprio", "fotos têm texto próprio")} (${plural(distintos, "texto diferente", "textos diferentes")}), embaixo de cada miniatura; ${plural(p.fotos.length - comTexto.length, "foto segue", "fotos seguem")} o texto do pedido.`,
          "info",
        );
        return caixa;
      })()
    : null;
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
      `filtro ${p.filtro_c1 || FILTRO_OK}`, // o motivo já vem como "C1: ..."
      `IP ${p.ip ?? "—"}`,
    ]),
    criar("p", {
      className: "fraco autorizacao",
      textContent: a
        ? `Autorização marcada em ${quando(a.quando)} · dispositivo ${curto(a.dispositivo)} · IP ${a.ip ?? "—"} · texto ${a.texto_versao}`
        : "Autorização das pessoas das fotos: não marcada.",
    }),
    resumoPessoas(p),
    textos,
    fotos,
    criar("div", { className: "decisao" }, [aprovar, motivo, recusar]),
    msg,
  ]);
}

function itemHistorico(x: ItemHistorico, aoMudar: () => void): HTMLElement {
  const msg = criar("div", { className: "aviso", hidden: true });
  const detalhes: string[] = [];
  if (x.aprovado_em) detalhes.push(`aprovado em ${quando(x.aprovado_em)} por ${x.aprovado_por ?? "?"}`);
  if (x.total) detalhes.push(`${x.prontos} de ${plural(x.total, "vídeo pronto", "vídeos prontos")}`);
  if (x.concluido_em) detalhes.push(`fechado em ${quando(x.concluido_em)}`);
  if (x.fotos_apagadas) detalhes.push("fotos de entrada já apagadas");
  detalhes.push(x.autorizado_em ? `autorização marcada em ${quando(x.autorizado_em)}` : "sem autorização marcada");
  if (x.modelo) detalhes.push(`modelo ${x.modelo}`);
  const registro = criar("button", { type: "button", className: "botao pequeno", dados: { foco: `${x.lote}:auditoria` } }, [icone("baixar"), "Baixar registro de auditoria"]);
  registro.addEventListener("click", async () => {
    ocupado(registro, true);
    try {
      baixarJson(await api<unknown>(`api/lotes/${encodeURIComponent(x.lote)}/auditoria`), `auditoria-${x.lote}.json`);
    } catch (e) {
      erroDeAcao(e, msg);
    } finally {
      ocupado(registro, false);
    }
  });
  const acoes = criar("div", { className: "acoes" }, [registro]);
  if (!x.cliente_ativo) acoes.append(criar("span", { className: "selo erro" }, [icone("bloquear"), "Conta desativada"]));
  else if (pode("POST /api/clientes/{cliente_id}/desativar")) {
    const botao = criar("button", { type: "button", className: "botao pequeno perigo", dados: { foco: `${x.lote}:desativar` } }, [icone("bloquear"), "Desativar esta conta"]);
    botao.addEventListener("click", async () => {
      const ok = await confirmar({
        titulo: "Desativar a conta?",
        texto: `A conta de ${x.cliente_nome} deixa de valer na hora: as sessões caem e a pessoa não entra mais (nem por e-mail, nem por código).`,
        confirmar: "Desativar",
        perigo: true,
      });
      if (!ok) return;
      ocupado(botao, true);
      try {
        await api(`api/clientes/${encodeURIComponent(x.cliente)}/desativar`, { method: "POST" });
        anunciar(`Conta de ${x.cliente_nome} desativada.`);
        aoMudar();
      } catch (e) {
        ocupado(botao, false);
        erroDeAcao(e, msg);
      }
    });
    acoes.append(botao);
  }
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
  return criar("article", { className: "cartao hist" }, linhas);
}

function tabelaIps(ips: UsoIp[]): HTMLElement {
  if (!ips.length) return semLinhas("Nenhum pedido com IP registrado ainda.");
  return tabela(
    [["IP", false], ["Fotos", true], ["Fotos de teste", true], ["Pedidos", true], ["Clientes", true], ["Dispositivos", true], ["Último pedido", false]],
    ips.map((u) => ({ celulas: [u.ip, u.fotos, u.fotos_teste, u.pedidos, u.clientes, u.dispositivos, tempoCurto(u.ultimo)] })),
    "Uso por IP",
  );
}

function tabelaDispositivos(ds: UsoDispositivo[]): HTMLElement {
  if (!ds.length) return semLinhas("Nenhum pedido com dispositivo registrado ainda.");
  return tabela(
    [["Dispositivo", false], ["IPs", false], ["Contas", true], ["Fotos de teste", true], ["Fusos", false], ["Sinais", false], ["Último pedido", false]],
    ds.map((d) => {
      const sinais = [
        d.ips.length > 1 ? `trocou de IP (${d.ips.length})` : "",
        d.clientes.length > 1 ? `várias contas (${d.clientes.length})` : "",
        d.fusos.length > 1 ? "fusos diferentes" : "",
        d.mesmo_navegador?.length ? `mesmo navegador de ${d.mesmo_navegador.map(curto).join(", ")}` : "",
      ].filter(Boolean);
      return {
        classe: sinais.length ? "sinal" : "",
        celulas: [curto(d.dispositivo), d.ips.join(", "), d.clientes.length, d.fotos_teste, d.fusos.join(", ") || "—", sinais.length ? `⚠ ${sinais.join(" · ")}` : "—", tempoCurto(d.ultimo)],
      };
    }),
    "Dispositivos",
  );
}

/** Revisão: os pedidos que esperam aprovação. `aoContar` recebe quantos são (o número no menu e no título). */
export function criarRevisao(ctx: { aoContar: (n: number) => void; aoMudar: () => void }): Controle {
  const alvo = el("pendentes");
  const imagens = new Map<string, HTMLImageElement>();
  const carregar = async () => {
    const lotes = await api<Pendente[]>("api/lotes");
    const vivos = new Set(lotes.flatMap((p) => p.fotos.map((f) => `${p.lote}/${f.nome}`)));
    for (const chave of imagens.keys()) if (!vivos.has(chave)) imagens.delete(chave);
    reconciliar(
      alvo,
      lotes.map((p) => ({
        chave: p.lote,
        assinatura: JSON.stringify([p.fotos.map((f) => [f.nome, f.analise, f.texto, f.filtro_c1]), p.autorizacao, p.pedido, p.filtro_c1]),
        criar: () => cartaoPendente(p, mudou, imagens),
      })),
      () => vazio("Nada aguardando revisão.", "Os pedidos novos aparecem aqui assim que as fotos chegam.", "ok"),
    );
    ctx.aoContar(lotes.length);
  };
  const c = controle({ regiao: alvo, carregar });
  const mudou = () => {
    void c.atualizar();
    ctx.aoMudar(); // o histórico, a visão geral e o número no menu também mudam
  };
  return c;
}

export function criarHistorico(): Controle {
  const lista = el("historico-lista");
  const filtros = el("filtros-historico");
  let grupo = "todos";
  let geracao = 0; // só a resposta do pedido mais recente vale (troca de filtro no meio de uma atualização)
  iniciarAbas(el("abas-historico"));
  const carregar = async () => {
    const minha = ++geracao;
    const r = await api<{ lotes: ItemHistorico[]; ips: UsoIp[]; dispositivos: UsoDispositivo[] }>(`api/historico?grupo=${encodeURIComponent(grupo)}`);
    if (minha !== geracao) return;
    reconciliar(
      lista,
      r.lotes.map((x) => ({ chave: x.lote, assinatura: JSON.stringify([x, pode("POST /api/clientes/{cliente_id}/desativar")]), criar: () => itemHistorico(x, () => void c.atualizar()) })),
      () => vazio("Nenhum pedido neste filtro.", "Troque o filtro para ver outros pedidos.", "pedidos"),
    );
    trocar(el("ips"), [tabelaIps(r.ips)], JSON.stringify(r.ips));
    trocar(el("dispositivos"), [tabelaDispositivos(r.dispositivos)], JSON.stringify(r.dispositivos));
  };
  const c = controle({ regiao: lista, ocupado: el("historico-dados"), carregar });
  filtros.addEventListener("click", (ev) => {
    const b = (ev.target as Element).closest<HTMLButtonElement>("button[data-grupo]");
    if (!b || b.dataset.grupo === grupo) return;
    grupo = b.dataset.grupo ?? "todos";
    for (const x of Array.from(filtros.querySelectorAll("button"))) x.setAttribute("aria-pressed", String(x === b));
    lista.setAttribute("aria-busy", "true");
    carregar()
      .catch((e) => erroDeCarga(e, null, () => undefined))
      .finally(() => lista.setAttribute("aria-busy", "false"));
  });
  return c;
}
