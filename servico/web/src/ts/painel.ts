// Painel do dono do serviço (no endereço secreto RENDER2GO_ADMIN_CAMINHO; ADR 0004 e 0007): revisão dos
// pedidos, histórico, uso por IP e por dispositivo, pods ligados (desligar um ou parada de emergência),
// relatório das GPUs, "Tenho interesse" (cada clique), recargas por Pix, suporte, contas e saldo e o que
// está configurado (segurança). Entrada com o token e o código do autenticador; a sessão fica num cookie
// HttpOnly restrito ao caminho do painel. A API do painel fica no mesmo caminho (BASE + "/api/...").
import {
  ESTADOS,
  ErroApi,
  type ItemLista,
  anunciar,
  api,
  aviso,
  avisoComRecarga,
  baixarJson,
  conectado,
  confirmar,
  criar,
  curto,
  el,
  hora,
  icone,
  marcarConectado,
  marcarErro,
  mostrarSenha,
  ocupado,
  plural,
  quando,
  reais,
  reconciliar,
  repetir,
  tempo,
  textoErro,
  urlSegura,
  vazio,
} from "./comum";

// a página e a API do painel ficam no mesmo endereço secreto: /<painel>/ e /<painel>/api/...
const BASE = location.pathname.replace(/\/+$/, "");
const CHAVE_PAINEL = "render2go-painel-conectado";

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

type Gerador = {
  id: string;
  pod: string | null;
  gpu: string | null;
  datacentro: string | null;
  usd_h: number | null;
  estado: string;
  motivo: string | null;
  inicio: number;
  ligado_s: number;
  ligado_estimado: boolean;
  subida_s: number | null;
  custo_usd: number | null;
  videos_ok: number;
  videos_erro: number;
  videos_andamento: number;
  traducoes: number;
  lotes: number;
  segundos_render: number;
};
type RelatorioGpus = {
  desde: number;
  geradores: Gerador[];
  totais: {
    pods: number;
    falharam: number;
    ligados_agora: number;
    horas: number;
    custo_usd: number;
    sem_preco: number;
    videos_ok: number;
    videos_erro: number;
    custo_por_video_usd: number | null;
  };
};

type Interesse = { finalidade: string; titulo: string; tipo: string; clientes: number; clientes_teste: number; ultimo: number };
type Clique = { finalidade: string; titulo: string; tipo: string; quando: number; cliente_id: string; nome: string; email: string | null; plano: string };
type Interesses = { resumo: Interesse[]; cliques: Clique[] };
type Pod = { id: string; worker: string | null; gpu: string | null; usd_h: number | null; estado: string; criado: number; pronto_em: number | null; datacentro: string | null; em_andamento: number };
type Comando = { id: string; tipo: string; alvo: string | null; criado: number; por: string | null; executado_em: number | null; resultado: string | null };
type Pods = { pods: Pod[]; pausado: boolean; pausado_em: number | null; orquestrador_visto_s: number | null; comandos: Comando[] };
type Pagamento = { id: string; cliente_id: string; email: string | null; valor: number; provedor: string; status: string; criado: number; creditado_em: number | null; documento: string | null };
type Acao = { quando: number; admin: string; acao: string; alvo: string | null; detalhe: string | null; ip: string | null };
type Seguranca = {
  turnstile: boolean;
  turnstile_desligado: boolean;
  email: boolean;
  suporte_email: boolean;
  totp: boolean;
  admin_ips: number;
  pagamento: boolean;
  cookie_seguro: boolean;
  analise: string;
  segredo_contas: boolean;
  orquestrador_visto_s: number | null;
  analisador_visto_s?: number | null;
  contas: Record<string, number>;
  auditoria: Acao[];
};

type Cliente = {
  id: string;
  nome: string;
  email: string | null;
  plano: string;
  ativo: number | boolean;
  criado: number;
  ultimo_acesso: number | null;
  excluido_em: number | null;
  saldo: number;
};
type Clientes = { clientes: Cliente[]; resumo: Record<string, number>; precos: Record<string, number>; planos_cobrados: string[] };

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
      await api(`${BASE}/api/lotes/${p.lote}/${acao}`, { method: "POST", body: corpo });
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

function itemHistorico(x: ItemHistorico, aoMudar: () => void): HTMLElement {
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
      baixarJson(await api<unknown>(`${BASE}/api/lotes/${x.lote}/auditoria`), `auditoria-${x.lote}.json`);
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
        await api(`${BASE}/api/clientes/${x.cliente}/desativar`, { method: "POST" });
        anunciar(`Conta de ${x.cliente_nome} desativada.`);
        aoMudar();
      } catch (e) {
        ocupado(botao, false);
        aviso(msg, textoErro(e), "erro");
      }
    });
    acoes.append(botao);
  } else {
    acoes.append(criar("span", { className: "selo erro" }, [icone("bloquear"), "Conta desativada"]));
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

const DOLAR = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 3 });
const SITUACAO_GPU: Record<string, string> = { subindo: "subindo", pronto: "ligada", encerrado: "desligada", falhou: "falhou na subida" };

function dolares(v: number | null): string {
  return v == null ? "—" : DOLAR.format(v);
}

/** 754 → "12 min 34 s"; 4000 → "1 h 07 min". */
function duracao(s: number | null): string {
  if (s == null) return "—";
  const t = Math.round(s);
  if (t < 60) return `${t} s`;
  if (t < 3600) return `${Math.floor(t / 60)} min ${String(t % 60).padStart(2, "0")} s`;
  return `${Math.floor(t / 3600)} h ${String(Math.floor((t % 3600) / 60)).padStart(2, "0")} min`;
}

function resumoGpus(r: RelatorioGpus): HTMLElement[] {
  const t = r.totais;
  const item = (rotulo: string, valor: string) => criar("div", {}, [criar("dt", { textContent: rotulo }), criar("dd", { textContent: valor })]);
  return [
    item("GPUs ligadas", `${t.pods}${t.ligados_agora ? ` (${t.ligados_agora} agora)` : ""}`),
    item("Tempo ligado", duracao(t.horas * 3600)),
    item("Custo estimado", dolares(t.custo_usd) + (t.sem_preco ? " *" : "")),
    item("Vídeos entregues", String(t.videos_ok)),
    item("Custo por vídeo", dolares(t.custo_por_video_usd)),
    item("Falhas", `${plural(t.falharam, "subida", "subidas")} · ${plural(t.videos_erro, "vídeo", "vídeos")}`),
  ];
}

function tabelaGpus(r: RelatorioGpus): HTMLElement {
  if (!r.geradores.length) return criar("p", { className: "fraco vazio-tabela", textContent: "Nenhuma GPU ligada neste período." });
  return tabela(
    [
      ["Ligou em", false],
      ["Pod", false],
      ["GPU", false],
      ["Datacenter", false],
      ["US$/h", true],
      ["Até o 1º vídeo", true],
      ["Tempo ligado", true],
      ["Vídeos", true],
      ["Falhas", true],
      ["Pedidos", true],
      ["Uso da GPU", true],
      ["Custo", true],
      ["Situação", false],
    ],
    r.geradores.map((g) => {
      const uso = g.ligado_s > 0 ? `${Math.min(100, Math.round((g.segundos_render / g.ligado_s) * 100))}%` : "—";
      const situacao = [SITUACAO_GPU[g.estado] ?? g.estado, g.motivo && g.estado !== "pronto" ? g.motivo : ""].filter(Boolean).join(": ");
      return {
        classe: g.estado === "falhou" ? "sinal" : "",
        celulas: [
          tempo(g.inicio),
          g.pod ?? "—",
          g.gpu ?? "—",
          g.datacentro ?? "—",
          g.usd_h == null ? "—" : g.usd_h.toFixed(2).replace(".", ","),
          duracao(g.subida_s),
          duracao(g.ligado_s) + (g.ligado_estimado ? " *" : ""),
          g.videos_ok + (g.videos_andamento ? ` (+${g.videos_andamento})` : ""),
          g.videos_erro,
          g.lotes,
          uso,
          dolares(g.custo_usd),
          situacao,
        ],
      };
    }),
    "GPUs ligadas no período",
  );
}

function tabelaInteresses(xs: Interesse[]): HTMLElement {
  if (!xs.length) return criar("p", { className: "fraco vazio-tabela", textContent: "Nenhum interesse registrado ainda." });
  return tabela(
    [["Opção", false], ["Tipo", false], ["Contas", true], ["Destas, no teste grátis", true], ["Último", false]],
    xs.map((x) => ({ celulas: [x.titulo, x.tipo, x.clientes, x.clientes_teste, tempo(x.ultimo)] })),
    "Interesse nas opções em breve",
  );
}

function tabelaCliques(xs: Clique[]): HTMLElement {
  if (!xs.length) return criar("p", { className: "fraco vazio-tabela", textContent: "Nenhum clique ainda." });
  return tabela(
    [["Quando", false], ["Opção", false], ["Conta", false], ["E-mail", false], ["Plano", false]],
    xs.map((x) => ({ celulas: [tempo(x.quando), x.titulo, x.nome, x.email ?? "—", x.plano] })),
    "Cada clique em Tenho interesse",
  );
}

const SITUACAO_POD: Record<string, string> = { subindo: "subindo", pronto: "ligado" };
const COMANDOS: Record<string, string> = { desligar_pod: "Desligar pod", emergencia: "Parada de emergência", retomar: "Retomar" };

function tabelaPods(r: Pods, aoMudar: () => void, msg: HTMLElement): HTMLElement {
  if (!r.pods.length) return criar("p", { className: "fraco vazio-tabela", textContent: "Nenhum pod ligado agora." });
  return tabela(
    [["Ligou em", false], ["Pod", false], ["GPU", false], ["Datacenter", false], ["US$/h", true], ["Situação", false], ["Vídeos gerando", true], ["Ação", false]],
    r.pods.map((p) => {
      const desligar = criar("button", { type: "button", className: "botao pequeno perigo" }, [icone("bloquear"), "Desligar"]);
      desligar.setAttribute("aria-label", `Desligar o pod ${p.worker ?? p.id}`);
      desligar.addEventListener("click", async () => {
        const ok = await confirmar({
          titulo: "Desligar este pod?",
          texto: `O pod ${p.worker ?? p.id} (${p.gpu ?? "GPU"}) é encerrado no RunPod. Os vídeos que ele estava gerando voltam para a fila e saem em outro pod.`,
          confirmar: "Desligar",
          perigo: true,
        });
        if (!ok) return;
        ocupado(desligar, true);
        try {
          await api(`${BASE}/api/pods/${encodeURIComponent(p.id)}/desligar`, { method: "POST" });
          anunciar("Pedido de desligamento enviado ao orquestrador.");
          aoMudar();
        } catch (e) {
          ocupado(desligar, false);
          aviso(msg, textoErro(e), "erro");
        }
      });
      return {
        celulas: [tempo(p.criado), p.worker ?? "—", p.gpu ?? "—", p.datacentro ?? "—", p.usd_h == null ? "—" : p.usd_h.toFixed(2).replace(".", ","), SITUACAO_POD[p.estado] ?? p.estado, p.em_andamento, desligar],
      };
    }),
    "Pods ligados agora",
  );
}

function tabelaPagamentos(xs: Pagamento[], aoMudar: () => void, msg: HTMLElement): HTMLElement {
  if (!xs.length) return criar("p", { className: "fraco vazio-tabela", textContent: "Nenhuma recarga ainda." });
  return tabela(
    [["Quando", false], ["Conta", false], ["Valor", true], ["Situação", false], ["Creditado", false], ["Documento", false], ["Ação", false]],
    xs.map((p) => {
      let acao: Node | string = "—";
      if (["pendente", "criando", "divergente", "erro"].includes(p.status)) {
        const conferir = criar("button", { type: "button", className: "botao pequeno" }, [icone("atualizar"), "Conferir"]);
        conferir.setAttribute("aria-label", `Conferir no Mercado Pago a recarga de ${reais(p.valor)}`);
        conferir.addEventListener("click", async () => {
          ocupado(conferir, true);
          try {
            const r = await api<{ status: string }>(`${BASE}/api/pagamentos/${encodeURIComponent(p.id)}/conferir`, { method: "POST" });
            anunciar(`Recarga conferida: ${r.status}.`);
            aoMudar();
          } catch (e) {
            aviso(msg, textoErro(e), "erro");
          } finally {
            ocupado(conferir, false);
          }
        });
        acao = conferir;
      }
      return {
        classe: p.status === "divergente" || p.status === "contestado" ? "sinal" : "",
        celulas: [tempo(p.criado), p.email ?? p.cliente_id, reais(p.valor), p.status, p.creditado_em ? tempo(p.creditado_em) : "—", p.documento ?? "—", acao],
      };
    }),
    "Recargas por Pix",
  );
}

const PLANOS_PAINEL = ["teste", "pago", "interno"];

function tabelaClientes(r: Clientes, msg: HTMLElement, aoMudar: () => void): HTMLElement {
  if (!r.clientes.length) return criar("p", { className: "fraco vazio-tabela", textContent: "Nenhuma conta encontrada." });
  const acaoConta = async (botao: HTMLButtonElement, caminho: string, corpo: unknown, sucesso: string) => {
    ocupado(botao, true);
    try {
      await api(`${BASE}/api/clientes/${caminho}`, { method: "POST", body: corpo === undefined ? undefined : JSON.stringify(corpo) });
      anunciar(sucesso);
      aoMudar();
    } catch (e) {
      aviso(msg, textoErro(e), "erro");
      ocupado(botao, false);
    }
  };
  return tabela(
    [["Conta", false], ["Plano", false], ["Saldo", true], ["Situação", false], ["Último acesso", false], ["Criada", false], ["Ações", false]],
    r.clientes.map((k) => {
      const cobrado = r.planos_cobrados.includes(k.plano);
      const id = encodeURIComponent(k.id);
      const acoes = criar("div", { className: "acoes-linha" });
      if (cobrado) {
        const extrato = criar("button", { type: "button", className: "botao pequeno" }, [icone("baixar"), "Extrato"]);
        extrato.setAttribute("aria-label", `Baixar o extrato de ${k.email ?? k.nome}`);
        extrato.addEventListener("click", async () => {
          ocupado(extrato, true);
          try {
            baixarJson(await api<unknown>(`${BASE}/api/clientes/${id}/extrato`), `extrato-${k.id}.json`);
          } catch (e) {
            aviso(msg, textoErro(e), "erro");
          } finally {
            ocupado(extrato, false);
          }
        });
        acoes.append(extrato);
      }
      if (!k.excluido_em) {
        const plano = criar("select", {}, PLANOS_PAINEL.map((p) => criar("option", { value: p, textContent: p, selected: p === k.plano })));
        plano.setAttribute("aria-label", `Plano de ${k.email ?? k.nome}`);
        const mudar = criar("button", { type: "button", className: "botao pequeno" }, ["Mudar plano"]);
        mudar.addEventListener("click", async () => {
          if (plano.value === k.plano) return;
          const ok = await confirmar({ titulo: "Mudar o plano?", texto: `${k.email ?? k.nome}: de ${k.plano} para ${plano.value}.`, confirmar: "Mudar" });
          if (ok) await acaoConta(mudar, `${id}/plano`, { plano: plano.value }, "Plano alterado.");
        });
        acoes.append(plano, mudar);
        if (!k.email) {
          const ligar = criar("button", { type: "button", className: "botao pequeno" }, [icone("mensagem"), "Ligar e-mail"]);
          ligar.addEventListener("click", async () => {
            const email = window.prompt(`E-mail para ${k.nome} entrar sem o código de acesso:`)?.trim();
            if (email) await acaoConta(ligar, `${id}/email`, { email }, "E-mail ligado à conta.");
          });
          acoes.append(ligar);
        }
        const ativo = Boolean(k.ativo);
        const alternar = criar("button", { type: "button", className: `botao pequeno ${ativo ? "perigo" : "sucesso"}` }, [icone(ativo ? "bloquear" : "ok"), ativo ? "Desativar" : "Reativar"]);
        alternar.addEventListener("click", async () => {
          const ok = await confirmar({
            titulo: ativo ? "Desativar a conta?" : "Reativar a conta?",
            texto: ativo ? `${k.email ?? k.nome} deixa de entrar na hora (as sessões caem).` : `${k.email ?? k.nome} volta a entrar.`,
            confirmar: ativo ? "Desativar" : "Reativar",
            perigo: ativo,
          });
          if (ok) await acaoConta(alternar, `${id}/${ativo ? "desativar" : "reativar"}`, undefined, ativo ? "Conta desativada." : "Conta reativada.");
        });
        acoes.append(alternar);
      }
      const situacao = k.excluido_em ? "excluída" : k.ativo ? "ativa" : "desativada";
      return {
        celulas: [k.email ?? k.nome, k.plano, cobrado ? reais(k.saldo) : "—", situacao, k.ultimo_acesso ? tempo(k.ultimo_acesso) : "—", tempo(k.criado), acoes],
      };
    }),
    "Contas e saldo",
  );
}

function listaChecagem(r: Seguranca): HTMLElement[] {
  const item = (ok: boolean, texto: string, falta: string) =>
    criar("li", { className: ok ? "ok" : "falta" }, [icone(ok ? "ok" : "alerta"), criar("span", { textContent: ok ? texto : falta })]);
  const visto = r.orquestrador_visto_s;
  return [
    item(r.turnstile, "Verificação “não sou um robô” (Turnstile) ligada.", r.turnstile_desligado ? "Turnstile DESLIGADO de propósito (só para desenvolvimento)." : "Turnstile não configurado: a entrada por e-mail e o suporte sem conta estão recusando pedidos."),
    item(r.email, "E-mail (SMTP) configurado: os códigos de entrada saem.", "SMTP não configurado: ninguém consegue receber o código para entrar."),
    item(r.suporte_email, "Mensagens de suporte chegam no seu e-mail.", "Suporte só no painel (falta RENDER2GO_SUPORTE_PARA)."),
    item(r.totp, "Painel com código do autenticador (2 fatores).", "Painel SEM o código do autenticador: configure RENDER2GO_ADMIN_TOTP."),
    item(r.admin_ips > 0, `Painel restrito a ${plural(r.admin_ips, "rede", "redes")}.`, "Painel aberto a qualquer IP (opcional: RENDER2GO_ADMIN_IPS)."),
    item(r.pagamento, "Recarga por Pix (Mercado Pago) ligada.", "Recarga por Pix desligada: os clientes recarregam pelo suporte."),
    item(r.cookie_seguro, "Cookies só por HTTPS.", "Cookies sem HTTPS (só para desenvolvimento local)."),
    item(r.segredo_contas, "Segredo próprio das contas configurado.", "Sem RENDER2GO_SEGREDO_CONTAS (os códigos usam uma chave derivada do segredo de aprovação)."),
    analiseItem(r, item),
    item(visto !== null && visto < 120, `Orquestrador ativo (visto há ${Math.round(visto ?? 0)} s).`, visto === null ? "O orquestrador ainda não deu sinal." : `Orquestrador sem sinal há ${Math.round(visto)} s: confira o contêiner.`),
  ];
}

function analiseItem(r: Seguranca, item: (ok: boolean, texto: string, falta: string) => HTMLElement): HTMLElement {
  if (r.analise === "desligada") return item(false, "", "Aviso de pessoas desligado.");
  if (r.analise !== "separada") return item(true, "Aviso de pessoas nas fotos: na API.", "");
  const visto = r.analisador_visto_s ?? null;
  return item(
    visto !== null && visto < 180,
    `Aviso de pessoas nas fotos: serviço próprio, visto há ${Math.round(visto ?? 0)} s.`,
    visto === null ? "Serviço de análise das fotos ainda sem sinal (contêiner analisador)." : `Análise das fotos sem sinal há ${Math.round(visto)} s: confira o contêiner analisador.`,
  );
}

function tabelaAuditoria(xs: Acao[]): HTMLElement {
  if (!xs.length) return criar("p", { className: "fraco vazio-tabela", textContent: "Nenhuma ação registrada ainda." });
  return tabela(
    [["Quando", false], ["Quem", false], ["Ação", false], ["Alvo", false], ["IP", false]],
    xs.map((a) => ({ classe: a.acao === "entrada_recusada" ? "sinal" : "", celulas: [tempo(a.quando), a.admin, a.acao, a.alvo ?? "—", a.ip ?? "—"] })),
    "Ações do painel",
  );
}

function iniciarPainel(): void {
  const raiz = document.documentElement;
  const msg = el("msg");
  const atualizar = el<HTMLButtonElement>("atualizar");
  const sair = el<HTMLButtonElement>("sair");
  const imagens = new Map<string, HTMLImageElement>();
  let grupo = "todos";

  const listarHistorico = async () => {
    const r = await api<{ lotes: ItemHistorico[]; ips: UsoIp[]; dispositivos: UsoDispositivo[] }>(`${BASE}/api/historico?grupo=${encodeURIComponent(grupo)}`);
    reconciliar(
      el("historico"),
      r.lotes.map((x) => ({ chave: x.lote, assinatura: JSON.stringify(x), criar: () => itemHistorico(x, listar) })),
      () => vazio("Nenhum pedido neste filtro.", "Troque o filtro para ver outros pedidos.", "pedidos"),
    );
    el("ips").replaceChildren(tabelaIps(r.ips));
    el("dispositivos").replaceChildren(tabelaDispositivos(r.dispositivos));
  };

  const listarSuporte = async () => {
    const sup = await api<{ email_configurado: boolean; mensagens: MensagemSuporte[] }>(`${BASE}/api/suporte`);
    el("suporte-config").textContent = sup.email_configurado
      ? "As mensagens também chegam no seu e-mail."
      : "E-mail do suporte não configurado: as mensagens ficam só aqui (veja docs/09, seção de suporte).";
    reconciliar(el("suporte"), listaSuporte(sup.mensagens), () => vazio("Nenhuma mensagem ainda.", "As mensagens enviadas pelo /suporte aparecem aqui.", "mensagem"));
  };

  // -- pods: desligar um ou parar tudo ------------------------------------------------------------
  const listarPods = async () => {
    const r = await api<Pods>(`${BASE}/api/pods`);
    const visto = r.orquestrador_visto_s;
    el("estado-orquestrador").textContent = [
      r.pausado ? `PAUSADO desde ${r.pausado_em ? quando(r.pausado_em) : "agora"}: nenhum pod novo sobe.` : "Funcionando: o orquestrador liga pods conforme a fila.",
      visto === null ? "O orquestrador ainda não deu sinal." : visto < 120 ? `Orquestrador visto há ${Math.round(visto)} s.` : `ATENÇÃO: orquestrador sem sinal há ${Math.round(visto)} s.`,
    ].join(" ");
    el("emergencia").hidden = r.pausado && !r.pods.length;
    el("retomar").hidden = !r.pausado;
    el("pods").replaceChildren(tabelaPods(r, () => void aposAcaoPods(), msg));
    el("n-pods").textContent = r.pods.length ? String(r.pods.length) : "";
    el("comandos").replaceChildren(
      ...(r.comandos.length
        ? r.comandos.map((c) =>
            criar("li", {}, [
              criar("span", {}, [tempo(c.criado), ` · ${COMANDOS[c.tipo] ?? c.tipo}${c.alvo ? ` (${curto(c.alvo)})` : ""} · ${c.por ?? "?"}`]),
              criar("span", { className: "valor", textContent: c.executado_em ? c.resultado ?? "feito" : "aguardando o orquestrador" }),
            ]),
          )
        : [criar("li", { className: "fraco", textContent: "Nenhum comando ainda." })]),
    );
  };
  // depois de uma ação nos pods, a lista e o registro de ações (auditoria) mudam juntos
  const aposAcaoPods = () =>
    Promise.all([listarPods(), listarSeguranca()]).catch((e) => aviso(msg, `Não foi possível atualizar: ${textoErro(e)}`, "erro"));
  el("emergencia").addEventListener("click", async () => {
    const ok = await confirmar({
      titulo: "Parada de emergência?",
      texto: "Todos os pods são encerrados e nenhum pod novo sobe até você clicar em “Retomar”. Os vídeos em andamento voltam para a fila e os clientes esperam.",
      confirmar: "Parar tudo",
      perigo: true,
    });
    if (!ok) return;
    const b = el<HTMLButtonElement>("emergencia");
    ocupado(b, true);
    try {
      await api(`${BASE}/api/pods/emergencia`, { method: "POST" });
      anunciar("Parada de emergência enviada. Nenhum pod novo sobe até retomar.", true);
      await aposAcaoPods();
    } catch (e) {
      aviso(msg, textoErro(e), "erro");
    } finally {
      ocupado(b, false);
    }
  });
  el("retomar").addEventListener("click", async () => {
    const b = el<HTMLButtonElement>("retomar");
    ocupado(b, true);
    try {
      await api(`${BASE}/api/pods/retomar`, { method: "POST" });
      anunciar("Pods liberados de novo.");
      await aposAcaoPods();
    } catch (e) {
      aviso(msg, textoErro(e), "erro");
    } finally {
      ocupado(b, false);
    }
  });

  // -- GPUs (pods do orquestrador) ---------------------------------------------------------------
  let dias = 30;
  let relatorioGpus: RelatorioGpus | null = null;
  let assinaturaGpus = "";
  const listarGpus = async () => {
    const r = await api<RelatorioGpus>(`${BASE}/api/geradores?dias=${dias}`);
    relatorioGpus = r;
    // a cada 30 s só o tempo das GPUs ligadas muda: a tabela é refeita, o foco e a rolagem ficam
    const assinatura = JSON.stringify([dias, r.geradores.map((g) => [g.id, g.estado, g.videos_ok, g.videos_erro, g.videos_andamento, Math.round(g.ligado_s / 60)])]);
    if (assinatura === assinaturaGpus) return;
    assinaturaGpus = assinatura;
    el("resumo-gpus").replaceChildren(...resumoGpus(r));
    const nota = r.geradores.some((g) => g.ligado_estimado) || r.totais.sem_preco
      ? criar("p", { className: "fraco", textContent: "* Estimado: o orquestrador parou sem registrar o desligamento (o desligamento automático do pod encerra no máximo 2 h depois) ou a GPU não informou o preço." })
      : null;
    el("gpus").replaceChildren(...[tabelaGpus(r), nota].filter((x): x is HTMLElement => !!x));
  };
  for (const b of Array.from(el("filtros-gpus").querySelectorAll<HTMLButtonElement>("button"))) {
    b.addEventListener("click", () => {
      dias = Number(b.dataset.dias) || 30;
      for (const o of Array.from(el("filtros-gpus").querySelectorAll<HTMLButtonElement>("button"))) o.setAttribute("aria-pressed", String(o === b));
      listarGpus().catch((e) => aviso(msg, `Não foi possível carregar as GPUs: ${textoErro(e)}`, "erro"));
    });
  }
  el("baixar-gpus").addEventListener("click", async () => {
    const botao = el<HTMLButtonElement>("baixar-gpus");
    ocupado(botao, true);
    try {
      await listarGpus();
      if (relatorioGpus) baixarJson(relatorioGpus, `gpus-${dias}-dias.json`);
    } catch (e) {
      aviso(msg, textoErro(e), "erro");
    } finally {
      ocupado(botao, false);
    }
  });

  // -- interesse, pagamentos e segurança ------------------------------------------------------------
  const listarInteresses = async () => {
    const r = await api<Interesses>(`${BASE}/api/interesses`);
    el("interesses").replaceChildren(tabelaInteresses(r.resumo));
    el("cliques").replaceChildren(tabelaCliques(r.cliques));
  };
  const listarPagamentos = async () => {
    const r = await api<{ configurado: boolean; pagamentos: Pagamento[] }>(`${BASE}/api/pagamentos`);
    el("pagamentos-config").textContent = r.configurado
      ? "Recarga automática ligada (Mercado Pago). Cada Pix pago entra sozinho no saldo, uma vez só. “Conferir” pergunta de novo ao Mercado Pago."
      : "Recarga automática desligada: configure o Mercado Pago (docs/09) ou lance as recargas à mão em “Contas e saldo”.";
    el("pagamentos").replaceChildren(tabelaPagamentos(r.pagamentos, () => void listarPagamentos(), msg));
  };
  const listarSeguranca = async () => {
    const r = await api<Seguranca>(`${BASE}/api/seguranca`);
    el("checagem").replaceChildren(...listaChecagem(r));
    el("auditoria").replaceChildren(tabelaAuditoria(r.auditoria));
  };

  // -- contas e saldo (pré-pago) ------------------------------------------------------------------
  const selCliente = el<HTMLSelectElement>("saldo-cliente");
  let busca = "";
  let assinaturaClientes = "";
  const listarClientes = async () => {
    const r = await api<Clientes>(`${BASE}/api/clientes${busca ? `?busca=${encodeURIComponent(busca)}` : ""}`);
    const assinatura = JSON.stringify(r);
    if (assinatura === assinaturaClientes) return; // nada mudou: a tabela e a escolha ficam como estão
    assinaturaClientes = assinatura;
    el("clientes").replaceChildren(tabelaClientes(r, el("saldo-msg"), () => void listarClientes()));
    const t = r.resumo;
    el("resumo-contas").textContent = `${plural(t.total ?? 0, "conta", "contas")}: ${t.teste ?? 0} no teste grátis, ${t.pago ?? 0} no pré-pago; ${t.novos_24h ?? 0} nas últimas 24 h.`;
    const escolhido = selCliente.value;
    const pagantes = r.clientes.filter((k) => k.ativo && r.planos_cobrados.includes(k.plano));
    selCliente.replaceChildren(
      criar("option", { value: "", textContent: pagantes.length ? "Escolha a conta" : "Nenhuma conta do plano pago nesta busca" }),
      ...pagantes.map((k) => criar("option", { value: k.id, textContent: `${k.email ?? k.nome} · saldo ${reais(k.saldo)}` })),
    );
    if (pagantes.some((k) => k.id === escolhido)) selCliente.value = escolhido;
    const p = r.precos;
    el("precos-info").textContent =
      `Pré-pago: 480p a ${reais(p.rapida ?? 0)} e 720p a ${reais(p.alta ?? 0)} por segundo de vídeo. ` +
      "O pedido é cobrado quando as fotos chegam; pedido recusado e vídeo que não sai voltam sozinhos para o saldo.";
  };
  el<HTMLFormElement>("form-busca").addEventListener("submit", (ev) => {
    ev.preventDefault();
    busca = el<HTMLInputElement>("busca-cliente").value.trim();
    listarClientes().catch((e) => aviso(msg, textoErro(e), "erro"));
  });

  const listar = async (): Promise<boolean> => {
    try {
      const lotes = await api<Pendente[]>(`${BASE}/api/lotes`);
      const vivos = new Set(lotes.flatMap((p) => p.fotos.map((f) => `${p.lote}/${f.nome}`)));
      for (const chave of imagens.keys()) if (!vivos.has(chave)) imagens.delete(chave);
      reconciliar(
        el("pendentes"),
        lotes.map((p) => ({
          chave: p.lote,
          assinatura: JSON.stringify([p.fotos.map((f) => [f.nome, f.analise]), p.autorizacao, p.pedido, p.filtro_c1]),
          criar: () => cartaoPendente(p, listar, imagens),
        })),
        () => vazio("Nada aguardando revisão.", "Os pedidos novos aparecem aqui assim que as fotos chegam.", "ok"),
      );
      el("n-pendentes").textContent = lotes.length ? String(lotes.length) : "";
      document.title = lotes.length ? `(${lotes.length}) Painel · Render2Go` : "Painel · Render2Go";
      await Promise.all([listarPods(), listarHistorico(), listarSuporte(), listarClientes(), listarGpus(), listarInteresses(), listarPagamentos(), listarSeguranca()]);
      el("atualizado").textContent = `Atualizado às ${hora()}`;
      aviso(msg, "");
    } catch (e) {
      if (e instanceof ErroApi && e.status === 401) {
        marcarConectado(CHAVE_PAINEL, false);
        location.reload(); // a sessão do painel venceu (12 h): volta para a entrada
        return false;
      }
      aviso(msg, `Não foi possível carregar: ${textoErro(e)}`, "erro");
    }
    return true; // o painel continua atualizando enquanto a aba está visível
  };
  const atualizacao = repetir(listar, 30000);

  for (const b of Array.from(el("filtros-historico").querySelectorAll<HTMLButtonElement>("button"))) {
    b.addEventListener("click", () => {
      grupo = b.dataset.grupo ?? "todos";
      for (const o of Array.from(el("filtros-historico").querySelectorAll<HTMLButtonElement>("button"))) o.setAttribute("aria-pressed", String(o === b));
      listarHistorico().catch((e) => aviso(msg, `Não foi possível carregar: ${textoErro(e)}`, "erro"));
    });
  }

  // -- entrada no painel: token + código do autenticador ---------------------------------------------
  const form = el<HTMLFormElement>("form-admin");
  const campoToken = el<HTMLInputElement>("admin-token");
  const campoTotp = el<HTMLInputElement>("admin-totp");
  const msgEntrada = el("msg-entrada");
  mostrarSenha(el<HTMLButtonElement>("mostrar-token"), campoToken);
  const mostrarPainel = (focar: boolean) => {
    raiz.classList.add("com-sessao");
    atualizar.hidden = sair.hidden = false;
    marcarConectado(CHAVE_PAINEL, true);
    atualizacao.agora();
    if (focar) el("titulo-painel").focus();
  };
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const token = campoToken.value.trim();
    if (!token) {
      aviso(msgEntrada, "Digite o token de administração.", "erro");
      marcarErro(campoToken, true);
      campoToken.focus();
      return;
    }
    const botao = el<HTMLButtonElement>("admin-entrar");
    ocupado(botao, true);
    try {
      await api(`${BASE}/api/entrar`, { method: "POST", body: JSON.stringify({ token, totp: campoTotp.value.replace(/\D/g, "") || null }) });
      campoToken.value = campoTotp.value = "";
      aviso(msgEntrada, "");
      mostrarPainel(true);
    } catch (e) {
      aviso(msgEntrada, textoErro(e), "erro");
      anunciar(textoErro(e), true);
      campoTotp.value = "";
      (e instanceof ErroApi && e.status === 401 ? campoTotp : campoToken).focus();
    } finally {
      ocupado(botao, false);
    }
  });
  for (const campo of [campoToken, campoTotp]) campo.addEventListener("input", () => marcarErro(campo, false));
  sair.addEventListener("click", async () => {
    await api(`${BASE}/api/sair`, { method: "POST" }).catch(() => undefined);
    marcarConectado(CHAVE_PAINEL, false);
    location.reload();
  });
  atualizar.addEventListener("click", async () => {
    ocupado(atualizar, true);
    await listar();
    ocupado(atualizar, false);
    anunciar("Painel atualizado.");
  });
  // sessão ainda válida (cookie HttpOnly): o painel aparece direto
  if (conectado(CHAVE_PAINEL)) raiz.classList.add("com-sessao");
  const semSessao = () => {
    raiz.classList.remove("com-sessao");
    marcarConectado(CHAVE_PAINEL, false);
    campoToken.focus();
  };
  api<{ admin: string | null }>(`${BASE}/api/sessao`)
    .then((s) => (s.admin ? mostrarPainel(false) : semSessao()))
    .catch((e) => {
      semSessao();
      avisoComRecarga(msgEntrada, `Não foi possível falar com o servidor: ${textoErro(e)}`);
    });

  // -- nova conta --------------------------------------------------------------------------------
  const formCliente = el<HTMLFormElement>("form-cliente");
  const email = el<HTMLInputElement>("cliente-email");
  email.addEventListener("input", () => marcarErro(email, false));
  formCliente.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const saida = el("cliente-msg");
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.value.trim())) {
      marcarErro(email, true);
      aviso(saida, "Escreva o e-mail da conta.", "erro");
      email.focus();
      return;
    }
    const botao = el<HTMLButtonElement>("cliente-enviar");
    ocupado(botao, true);
    try {
      const r = await api<{ cliente: string; email: string; plano: string }>(`${BASE}/api/clientes`, {
        method: "POST",
        body: JSON.stringify({
          email: email.value.trim(),
          plano: el<HTMLSelectElement>("cliente-plano").value,
          cota_fotos_dia: Number(el<HTMLInputElement>("cliente-cota").value) || 500,
        }),
      });
      aviso(saida, `Conta ${r.email} criada no plano ${r.plano}. A pessoa entra pela página inicial com o código que chega no e-mail.`, "ok");
      anunciar("Conta criada.");
      formCliente.reset();
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
      [selCliente, selCliente.value ? "" : "Escolha a conta (busque por e-mail acima)."],
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
    const nome = selCliente.selectedOptions[0]?.textContent?.split(" · ")[0] ?? "a conta";
    const ok = await confirmar({
      titulo: "Lançar no saldo?",
      texto: `${valor.value.trim().startsWith("-") ? "Descontar" : "Adicionar"} R$ ${valor.value.trim().replace(/^-/, "")} ${valor.value.trim().startsWith("-") ? "do" : "ao"} saldo de ${nome}.`,
      confirmar: "Lançar",
    });
    if (!ok) return;
    const botao = el<HTMLButtonElement>("saldo-enviar");
    ocupado(botao, true);
    try {
      const r = await api<{ saldo: number }>(`${BASE}/api/clientes/${encodeURIComponent(selCliente.value)}/saldo`, {
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

document.addEventListener("DOMContentLoaded", iniciarPainel);
