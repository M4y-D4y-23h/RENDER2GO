// Páginas do serviço Render2Go (ADR 0003, fase 3): cliente (/), suporte (/suporte) e aprovador (/aprovar).
// Os arquivos nunca passam pela API: as fotos vão direto ao armazenamento (R2) por URLs
// assinadas, uma por foto; os vídeos voltam por links assinados que expiram em 24 h.
// Compilar: cd servico/web && npm ci && npm run build  (gera app.js)

type Receita = { id: string; label: string; nicho: string; acao: string; exige_consentimento: boolean };
// plano do cliente; no teste gratuito, os limites que a API impõe (a página só os mostra)
type Plano = {
  nome: string;
  qualidades?: string[];
  duracoes?: number[];
  max_fotos?: number;
  disponivel?: boolean;
  motivo?: string | null;
};
type Modelo = { id: string; nome: string; descricao: string; disponivel: boolean };
type Receitas = {
  plano?: Plano;
  autorizacao?: { versao: string; texto: string };
  modelos?: Modelo[];
  modelo_padrao?: string;
  receitas: Receita[];
  formatos: string[];
  qualidades: string[];
  limites: { fotos: number; bytes_foto: number; tipos: string[] };
};
type Video = { arquivo: string; status: string; aviso: string | null; url?: string; baixar?: string };
// previsão em segundos; o cliente vê só o tempo, não as etapas internas
type Previsao = { fase: "analise" | "fila" | "gerando"; inicio_max_s?: number; inicio_s?: number; fim_s?: number } | null;
type Lote = {
  lote: string;
  status: string;
  criado: number;
  fotos: number;
  motivo: string | null;
  pedido: { prompt: string; qualidade: string; duracao: number | null };
  prontos: number;
  total: number;
  videos: Video[];
  modelo?: string;
  previsao?: Previsao;
};
type Envio = { original: string; url: string; tipo: string };
// análise automática de cada foto (render2go_servico/pessoas.py); null = ainda analisando
type Analise = { pessoas?: number; rostos?: number; erro?: string } | null;
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
type UsoIp = {
  ip: string;
  pedidos: number;
  fotos: number;
  fotos_teste: number;
  clientes: number;
  dispositivos: number;
  ultimo: number;
};
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
type Autorizacao = { declarante: string; quando: number; ip: string | null; dispositivo: string | null; texto_versao: string };
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
  pedido: {
    prompt: string;
    receita: string;
    qualidade: string;
    duracao: number | null;
    variacoes: number;
    consentimento?: boolean;
  };
  filtro_c1: string;
  analise_ligada?: boolean;
  fotos: { nome: string; url: string; analise?: Analise }[];
};

const ESTADOS: Record<string, string> = {
  aguardando_envio: "Enviando fotos",
  aguardando_aprovacao: "Em análise",
  aprovado: "Na fila",
  processando: "Gerando vídeos",
  concluido: "Pronto",
  concluido_com_falhas: "Pronto, com falhas",
  recusado: "Recusado",
};
const EM_ANDAMENTO = new Set(["aguardando_aprovacao", "aprovado", "processando"]);
const ENVIOS_SIMULTANEOS = 4;

function el<T extends HTMLElement = HTMLElement>(id: string): T {
  const e = document.getElementById(id);
  if (!e) throw new Error(`elemento #${id} ausente`);
  return e as T;
}

function criar<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  props: Partial<HTMLElementTagNameMap[K]> = {},
  filhos: (Node | string)[] = [],
): HTMLElementTagNameMap[K] {
  const e = Object.assign(document.createElement(tag), props);
  for (const f of filhos) e.append(f);
  return e;
}

// token guardado só neste navegador (conveniência; pode falhar em janela anônima)
// chaves com o nome antigo do projeto (antes de Render2Go) são lidas uma vez e migradas
function migrarChave(chave: string): void {
  const antiga = chave.replace("render2go-", "gpu" + "2go-");
  const valor = localStorage.getItem(antiga);
  if (valor && !localStorage.getItem(chave)) localStorage.setItem(chave, valor);
  if (valor) localStorage.removeItem(antiga);
}
function lerToken(chave: string): string {
  try {
    migrarChave(chave);
    return localStorage.getItem(chave) ?? "";
  } catch {
    return "";
  }
}
function gravarToken(chave: string, valor: string): void {
  try {
    if (valor) localStorage.setItem(chave, valor);
    else localStorage.removeItem(chave);
  } catch {
    /* sem armazenamento local: o token vale só nesta página */
  }
}

// identificador deste navegador (limite do teste por dispositivo e registro de auditoria);
// janela anônima ou dados apagados geram outro, e a API mantém um teto por IP
function uuid4(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}
let DISPOSITIVO_SESSAO = "";
function dispositivo(): string {
  try {
    migrarChave("render2go-dispositivo");
    let d = localStorage.getItem("render2go-dispositivo");
    if (!d) {
      d = uuid4();
      localStorage.setItem("render2go-dispositivo", d);
    }
    return d;
  } catch {
    DISPOSITIVO_SESSAO ||= uuid4();
    return DISPOSITIVO_SESSAO;
  }
}
function fuso(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "";
  } catch {
    return "";
  }
}

// sinais do navegador para o registro de auditoria da autorização (nada sai para terceiros)
function sinaisNavegador(marcadoEm: string | null): Record<string, unknown> {
  const n = navigator as Navigator & { deviceMemory?: number; userAgentData?: { platform?: string } };
  return {
    idioma: n.language || null,
    idiomas: Array.from(n.languages ?? []).slice(0, 10),
    tela: `${screen.width}x${screen.height}@${window.devicePixelRatio || 1}`,
    plataforma: n.userAgentData?.platform || n.platform || null,
    nucleos: n.hardwareConcurrency ?? null,
    memoria: n.deviceMemory ?? null,
    toque: n.maxTouchPoints ?? null,
    marcado_em: marcadoEm,
  };
}

function minutos(s: number): string {
  if (s < 60) return "menos de 1 min";
  const m = Math.ceil(s / 60);
  return m < 90 ? `cerca de ${m} min` : `cerca de ${Math.round(m / 60)} h`;
}

async function api<T>(caminho: string, token: string, init: RequestInit = {}): Promise<T> {
  const r = await fetch(caminho, {
    ...init,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      "Content-Type": "application/json",
      "X-Dispositivo": dispositivo(),
      "X-Fuso": fuso(),
      ...(init.headers ?? {}),
    },
  });
  const corpo = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = (corpo as { detail?: unknown }).detail;
    const msg = typeof d === "string" ? d : d ? JSON.stringify(d) : `erro ${r.status}`;
    throw new Error(msg);
  }
  return corpo as T;
}

function aviso(alvo: HTMLElement, texto: string, tipo: "erro" | "ok" | "info" = "info"): void {
  alvo.textContent = texto;
  alvo.className = `aviso ${tipo}`;
  alvo.hidden = !texto;
}

function quando(epoch: number): string {
  return new Date(epoch * 1000).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}

function painelToken(chave: string, aoEntrar: (token: string) => void): void {
  const form = el<HTMLFormElement>("form-token");
  const campo = el<HTMLInputElement>("token");
  const sair = el<HTMLButtonElement>("sair");
  const atual = lerToken(chave);
  const entrar = (t: string) => {
    form.hidden = true;
    sair.hidden = false;
    aoEntrar(t);
  };
  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const t = campo.value.trim();
    if (!t) return;
    gravarToken(chave, t);
    entrar(t);
  });
  sair.addEventListener("click", () => {
    gravarToken(chave, "");
    location.reload();
  });
  if (atual) entrar(atual);
}

// -- cliente -------------------------------------------------------------------------------

async function enviarArquivos(envios: Envio[], arquivos: Map<string, File>, progresso: HTMLProgressElement) {
  let feitos = 0;
  const fila = [...envios];
  progresso.max = envios.length;
  progresso.value = 0;
  progresso.hidden = false;
  const trabalhar = async () => {
    for (let e = fila.shift(); e; e = fila.shift()) {
      const arq = arquivos.get(e.original);
      if (!arq) throw new Error(`arquivo ${e.original} sumiu da seleção`);
      for (let tentativa = 1; ; tentativa++) {
        const r = await fetch(e.url, { method: "PUT", body: arq, headers: { "Content-Type": e.tipo } }).catch(
          () => null,
        );
        if (r && r.ok) break;
        if (tentativa >= 3) throw new Error(`não foi possível enviar ${e.original}`);
        await new Promise((ok) => setTimeout(ok, 1500 * tentativa));
      }
      progresso.value = ++feitos;
    }
  };
  await Promise.all(Array.from({ length: Math.min(ENVIOS_SIMULTANEOS, envios.length) }, trabalhar));
}

function cartaoLote(l: Lote): HTMLElement {
  const estado = ESTADOS[l.status] ?? l.status;
  const cab = criar("div", { className: "cab" }, [
    criar("strong", { textContent: estado }),
    criar("span", { className: "fraco", textContent: ` · ${l.fotos} foto(s) · ${quando(l.criado)}` }),
  ]);
  const pedido = criar("p", { className: "pedido", textContent: `“${l.pedido.prompt}”` });
  const cartao = criar("article", { className: `cartao estado-${l.status}` }, [cab, pedido]);
  const p = l.previsao;
  if (p) {
    const texto =
      p.fase === "analise"
        ? `Recebemos seu pedido. Ele começa em até ${minutos(p.inicio_max_s ?? 3600)}.`
        : p.fase === "fila"
          ? (p.inicio_s ?? 0) < 60
            ? "Seus vídeos começam em instantes."
            : `Seus vídeos começam em ${minutos(p.inicio_s ?? 0)}.`
          : `Gerando seus vídeos · termina em ${minutos(p.fim_s ?? 0)}.`;
    cartao.append(
      criar("div", { className: "previsao", role: "status" }, [criar("span", { className: "pulso", ariaHidden: "true" }), texto]),
      criar("p", { className: "fraco", textContent: `Atualizado às ${new Date().toLocaleTimeString("pt-BR")} · esta página se atualiza sozinha.` }),
    );
  }
  if (l.total) {
    const barra = criar("progress", { max: l.total, value: l.prontos });
    cartao.append(criar("div", { className: "linha" }, [barra, ` ${l.prontos} de ${l.total} vídeo(s)`]));
  }
  if (l.motivo) cartao.append(criar("p", { className: "aviso erro", textContent: `Motivo: ${l.motivo}` }));
  const prontos = l.videos.filter((v) => v.url);
  if (prontos.length) {
    const lista = criar("ul", { className: "videos" });
    for (const v of prontos) {
      const itens: (Node | string)[] = [criar("a", { href: v.url!, textContent: v.arquivo, target: "_blank", rel: "noopener" })];
      if (v.baixar) itens.push(criar("a", { href: v.baixar, textContent: "baixar", className: "baixar", rel: "noopener" }));
      lista.append(criar("li", {}, itens));
    }
    cartao.append(lista, criar("p", { className: "fraco", textContent: "Os links valem por 24 h; recarregue para renovar." }));
  }
  const falhas = l.videos.filter((v) => v.status === "erro").length;
  if (falhas) cartao.append(criar("p", { className: "fraco", textContent: `${falhas} vídeo(s) não saíram.` }));
  return cartao;
}

function iniciarCliente(): void {
  const msg = el("msg");
  let token = "";
  let timer = 0;
  let receitas: Receitas | null = null;
  const arquivos = new Map<string, File>();

  const listar = async () => {
    window.clearTimeout(timer);
    try {
      const lotes = await api<Lote[]>("/api/lotes", token);
      const alvo = el("lotes");
      alvo.replaceChildren(...lotes.map(cartaoLote));
      if (!lotes.length) alvo.append(criar("p", { className: "fraco", textContent: "Nenhum pedido ainda." }));
      if (lotes.some((l) => EM_ANDAMENTO.has(l.status))) timer = window.setTimeout(listar, 10000);
    } catch (e) {
      aviso(msg, `Não foi possível carregar os pedidos: ${(e as Error).message}`, "erro");
    }
  };

  const escolher = (lista: FileList | null) => {
    if (!lista || !receitas) return;
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
    if (ignorados) texto += ` · ${ignorados} ignorada(s) (use JPG, PNG ou WebP até 50 MB)`;
    if (excedentes) texto += ` · ${excedentes} a mais ficaram de fora (máximo de ${maximo} fotos por pedido)`;
    el("contagem").textContent = texto;
    el("enviar").textContent = teste && n ? `Enviar pedido com ${n} foto(s)` : "Enviar pedido";
  };

  // teste gratuito: só 480p, vídeos de 3 s, um pedido com até 10 fotos (a API confere tudo de novo)
  const aplicarPlano = (plano: Plano | undefined) => {
    const avisoPlano = el("plano-aviso");
    if (!plano || plano.nome !== "teste") return;
    const qualidade = el<HTMLSelectElement>("qualidade");
    for (const o of Array.from(qualidade.options)) if (!plano.qualidades?.includes(o.value)) o.remove();
    const duracao = el<HTMLSelectElement>("duracao");
    for (const o of Array.from(duracao.options)) if (!plano.duracoes?.includes(Number(o.value))) o.remove();
    duracao.value = String(plano.duracoes?.[0] ?? 3);
    if (plano.disponivel) {
      avisoPlano.replaceChildren(
        criar("strong", { textContent: `Teste gratuito: escolha até ${plano.max_fotos} fotos e envie todas juntas, em um único pedido.` }),
        criar("br"),
        `Cada foto vira um vídeo de 3 s em 480p. Depois de enviar, o teste não aceita outro pedido, então escolha todas as fotos antes de clicar em "Enviar pedido". Os testes são gerados em grupo: os vídeos ficam prontos em até uns 20 minutos depois da revisão.`,
      );
      avisoPlano.className = "aviso info";
      avisoPlano.hidden = false;
      atualizarContagem();
    } else {
      aviso(avisoPlano, `Teste gratuito já usado: ${plano.motivo ?? ""}`.trim(), "erro");
      for (const c of Array.from(el<HTMLFormElement>("form-lote").elements)) (c as HTMLInputElement).disabled = true;
    }
  };

  painelToken("render2go-token-cliente", async (t) => {
    token = t;
    try {
      receitas = await api<Receitas>("/api/receitas", token);
    } catch (e) {
      aviso(msg, `Token inválido ou serviço fora do ar: ${(e as Error).message}`, "erro");
      return;
    }
    const sel = el<HTMLSelectElement>("receita");
    sel.replaceChildren(...receitas.receitas.map((r) => criar("option", { value: r.id, textContent: `${r.label} (${r.nicho})` })));
    aplicarPlano(receitas.plano);
    if (receitas.autorizacao) el("texto-autorizacao").textContent = receitas.autorizacao.texto;
    const modelos = receitas.modelos ?? [];
    const selModelo = el<HTMLSelectElement>("modelo");
    selModelo.replaceChildren(...modelos.map((m) => criar("option", { value: m.id, textContent: `${m.nome} · ${m.descricao}` })));
    if (receitas.modelo_padrao) selModelo.value = receitas.modelo_padrao;
    el("modelo-info").textContent = modelos.length > 1 ? "" : "Novos modelos de geração vão aparecer aqui.";
    el("painel").hidden = false;
    listar();
  });

  el<HTMLInputElement>("fotos").addEventListener("change", (ev) => escolher((ev.target as HTMLInputElement).files));
  el<HTMLInputElement>("pasta").addEventListener("change", (ev) => escolher((ev.target as HTMLInputElement).files));
  el("link-suporte").textContent = `${location.host}/suporte`;
  el("limpar").addEventListener("click", () => {
    arquivos.clear();
    atualizarContagem();
  });
  let marcadoEm: string | null = null; // momento do clique na caixa de autorização (vai no registro)
  el<HTMLInputElement>("consentimento").addEventListener("change", (ev) => {
    marcadoEm = (ev.target as HTMLInputElement).checked ? new Date().toISOString() : null;
  });

  el<HTMLFormElement>("form-lote").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const botao = el<HTMLButtonElement>("enviar");
    if (!arquivos.size) return aviso(msg, "Escolha as fotos ou uma pasta.", "erro");
    const nomes = [...arquivos.keys()];
    const duracao = Number(el<HTMLSelectElement>("duracao").value) || null;
    const corpo = {
      prompt: el<HTMLTextAreaElement>("prompt").value.trim(),
      receita: el<HTMLSelectElement>("receita").value,
      qualidade: el<HTMLSelectElement>("qualidade").value,
      duracao,
      consentimento: el<HTMLInputElement>("consentimento").checked,
      modelo: el<HTMLSelectElement>("modelo").value,
      navegador: sinaisNavegador(el<HTMLInputElement>("consentimento").checked ? marcadoEm : null),
      fotos: nomes.map((n) => ({ nome: n, tipo: arquivos.get(n)!.type, bytes: arquivos.get(n)!.size })),
    };
    botao.disabled = true;
    try {
      aviso(msg, "Criando o pedido…");
      const r = await api<{ lote: string; envios: Envio[] }>("/api/lotes", token, { method: "POST", body: JSON.stringify(corpo) });
      aviso(msg, `Enviando ${r.envios.length} foto(s)…`);
      await enviarArquivos(r.envios, arquivos, el<HTMLProgressElement>("progresso"));
      await api(`/api/lotes/${r.lote}/enviado`, token, { method: "POST" });
      aviso(msg, "Pedido enviado. Ele passa por uma revisão rápida e entra na fila; os vídeos aparecem abaixo.", "ok");
      arquivos.clear();
      el<HTMLFormElement>("form-lote").reset();
      marcadoEm = null;
      atualizarContagem();
      if (receitas?.plano?.nome === "teste") {
        receitas = await api<Receitas>("/api/receitas", token);
        aplicarPlano(receitas.plano);
      }
      listar();
    } catch (e) {
      aviso(msg, (e as Error).message, "erro");
    } finally {
      botao.disabled = receitas?.plano?.disponivel === false;
      el<HTMLProgressElement>("progresso").hidden = true;
    }
  });
}

// -- aprovador -----------------------------------------------------------------------------

function seloAnalise(a: Analise | undefined): HTMLElement {
  if (a == null) return criar("span", { className: "selo", textContent: "analisando…" });
  if (a.erro) return criar("span", { className: "selo erro", textContent: "análise indisponível: confira", title: a.erro });
  if (!a.pessoas && !a.rostos) return criar("span", { className: "selo", textContent: "sem pessoas detectadas" });
  const partes = [`contém pessoas (${Math.max(a.pessoas ?? 0, a.rostos ?? 0)})`];
  if (a.rostos) partes.push(`${a.rostos} rosto(s)`);
  return criar("span", { className: "selo pessoas", textContent: `⚠ ${partes.join(" · ")}` });
}

// resumo do lote: pessoas detectadas × autorização marcada pelo cliente (ADR 0004, item 5)
function resumoPessoas(p: Pendente): HTMLElement | null {
  if (!p.analise_ligada) return null;
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

function cartaoPendente(p: Pendente, token: string, aoMudar: () => void): HTMLElement {
  const msg = criar("p", { className: "aviso", hidden: true });
  const fotos = criar("div", { className: "grade" });
  for (const f of p.fotos) {
    const filhos: (Node | string)[] = [criar("img", { src: f.url, alt: f.nome, loading: "lazy" })];
    if (p.analise_ligada) filhos.push(seloAnalise(f.analise));
    fotos.append(criar("a", { href: f.url, target: "_blank", rel: "noopener", className: "foto" }, filhos));
  }
  const pedido = p.pedido;
  const pessoas = resumoPessoas(p);
  const a = p.autorizacao;
  const linhaAutorizacao = criar("p", {
    className: "fraco autorizacao",
    textContent: a
      ? `Autorização marcada em ${quando(a.quando)} · dispositivo ${curto(a.dispositivo)} · IP ${a.ip ?? "—"} · texto ${a.texto_versao}`
      : "Autorização das pessoas das fotos: não marcada.",
  });
  const aprovar = criar("button", { textContent: "Aprovar", className: "primario" });
  const recusar = criar("button", { textContent: "Recusar" });
  const motivo = criar("input", { placeholder: "Motivo da recusa (o cliente vê)", maxLength: 500 });
  const agir = async (acao: "aprovar" | "recusar") => {
    aprovar.disabled = recusar.disabled = true;
    try {
      const corpo = acao === "recusar" ? JSON.stringify({ motivo: motivo.value.trim() }) : undefined;
      await api(`/api/admin/lotes/${p.lote}/${acao}`, token, { method: "POST", body: corpo });
      aoMudar();
    } catch (e) {
      aviso(msg, (e as Error).message, "erro");
      aprovar.disabled = recusar.disabled = false;
    }
  };
  aprovar.addEventListener("click", () => agir("aprovar"));
  recusar.addEventListener("click", () => {
    if (motivo.value.trim().length < 3) return aviso(msg, "Escreva o motivo da recusa.", "erro");
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
      textContent: `${p.modelo ?? "Wan 2.2"} · ${pedido.receita} · ${pedido.qualidade} · ${pedido.duracao ?? "padrão"} s · ${pedido.variacoes} variação(ões) · filtro C1: ${p.filtro_c1 || "ok"}`,
    }),
    linhaAutorizacao,
    ...(pessoas ? [pessoas] : []),
    fotos,
    criar("div", { className: "acoes" }, [aprovar, motivo, recusar]),
    msg,
  ]);
}

function curto(id: string | null | undefined): string {
  return id ? id.slice(0, 8) : "—";
}

async function baixarAuditoria(lote: string, token: string): Promise<void> {
  const dados = await api<unknown>(`/api/admin/lotes/${lote}/auditoria`, token);
  const url = URL.createObjectURL(new Blob([JSON.stringify(dados, null, 2)], { type: "application/json" }));
  const a = criar("a", { href: url, download: `auditoria-${lote}.json` });
  document.body.append(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 10000);
}

function itemHistorico(x: ItemHistorico, token: string, aoMudar: () => void): HTMLElement {
  const linhas: (Node | string)[] = [
    criar("div", { className: "cab" }, [
      criar("span", { className: `etiqueta ${x.status}`, textContent: ESTADOS[x.status] ?? x.status }),
      criar("span", {
        className: "fraco",
        textContent: ` · ${quando(x.criado)} · ${x.cliente_nome} (${x.plano}) · ${x.fotos} foto(s) · IP ${x.ip ?? "—"} · dispositivo ${curto(x.dispositivo)}`,
      }),
    ]),
    criar("p", { className: "pedido", textContent: `“${x.prompt}”` }),
  ];
  const detalhes: string[] = [];
  if (x.aprovado_em) detalhes.push(`aprovado em ${quando(x.aprovado_em)} por ${x.aprovado_por ?? "?"}`);
  if (x.total) detalhes.push(`${x.prontos} de ${x.total} vídeo(s) prontos`);
  if (x.concluido_em) detalhes.push(`fechado em ${quando(x.concluido_em)}`);
  if (x.fotos_apagadas) detalhes.push("fotos de entrada já apagadas");
  detalhes.push(x.autorizado_em ? `autorização marcada em ${quando(x.autorizado_em)}` : "sem autorização marcada");
  if (x.modelo) detalhes.push(`modelo ${x.modelo}`);
  if (detalhes.length) linhas.push(criar("p", { className: "fraco", textContent: detalhes.join(" · ") }));
  if (x.motivo) linhas.push(criar("p", { className: "aviso erro", textContent: `Motivo: ${x.motivo}` }));
  const auditoria = criar("button", { type: "button", textContent: "Baixar registro de auditoria" });
  auditoria.addEventListener("click", () => baixarAuditoria(x.lote, token).catch((e) => window.alert((e as Error).message)));
  const acoes = criar("div", { className: "acoes" }, [auditoria]);
  linhas.push(acoes);
  if (x.cliente_ativo) {
    const botao = criar("button", { type: "button", textContent: "Desativar o código deste cliente" });
    botao.addEventListener("click", async () => {
      if (!window.confirm(`Desativar o código de acesso de ${x.cliente_nome}? Ele deixa de entrar na página.`)) return;
      botao.disabled = true;
      try {
        await api(`/api/admin/clientes/${x.cliente}/desativar`, token, { method: "POST" });
        aoMudar();
      } catch (e) {
        botao.disabled = false;
        window.alert((e as Error).message);
      }
    });
    acoes.append(botao);
  } else {
    linhas.push(criar("p", { className: "fraco", textContent: "Código do cliente desativado." }));
  }
  return criar("article", { className: "cartao hist" }, linhas);
}

function tabelaIps(ips: UsoIp[]): HTMLElement {
  if (!ips.length) return criar("p", { className: "fraco", textContent: "Nenhum pedido com IP registrado ainda." });
  const th = (t: string, num = false) => criar("th", { textContent: t, className: num ? "num" : "", scope: "col" });
  const td = (t: string | number, num = false) => criar("td", { textContent: String(t), className: num ? "num" : "" });
  const cab = criar("tr", {}, [
    th("IP"),
    th("Fotos", true),
    th("Fotos de teste", true),
    th("Pedidos", true),
    th("Clientes", true),
    th("Dispositivos", true),
    th("Último pedido"),
  ]);
  const linhas = ips.map((u) =>
    criar("tr", {}, [
      td(u.ip),
      td(u.fotos, true),
      td(u.fotos_teste, true),
      td(u.pedidos, true),
      td(u.clientes, true),
      td(u.dispositivos, true),
      td(quando(u.ultimo)),
    ]),
  );
  return criar("table", {}, [criar("thead", {}, [cab]), criar("tbody", {}, linhas)]);
}

function tabelaDispositivos(ds: UsoDispositivo[]): HTMLElement {
  if (!ds.length) return criar("p", { className: "fraco", textContent: "Nenhum pedido com dispositivo registrado ainda." });
  const th = (t: string, num = false) => criar("th", { textContent: t, className: num ? "num" : "", scope: "col" });
  const td = (t: string | number, num = false) => criar("td", { textContent: String(t), className: num ? "num" : "" });
  const cab = criar("tr", {}, [th("Dispositivo"), th("IPs"), th("Códigos", true), th("Fotos de teste", true), th("Fusos"), th("Sinais"), th("Último pedido")]);
  const linhas = ds.map((d) => {
    const sinais = [
      d.ips.length > 1 ? `trocou de IP (${d.ips.length})` : "",
      d.clientes.length > 1 ? `vários códigos (${d.clientes.length})` : "",
      d.fusos.length > 1 ? "fusos diferentes" : "",
      d.mesmo_navegador?.length ? `mesmo navegador de ${d.mesmo_navegador.map(curto).join(", ")}` : "",
    ].filter(Boolean);
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

function listaSuporte(msgs: MensagemSuporte[]): HTMLElement[] {
  if (!msgs.length) return [criar("p", { className: "fraco", textContent: "Nenhuma mensagem ainda." })];
  return msgs.map((m) =>
    criar("article", { className: "cartao msg-suporte" }, [
      criar("div", { className: "cab" }, [
        criar("strong", { textContent: m.assunto || "Sem assunto" }),
        criar("span", {
          className: "fraco",
          textContent: ` · ${quando(m.quando)} · ${m.cliente_nome ?? m.cliente_id} (${m.plano ?? "?"}) · responder para ${m.email ?? "—"}`,
        }),
      ]),
      criar("p", { className: "texto", textContent: m.mensagem }),
      criar("p", {
        className: m.erro ? "aviso erro" : "fraco",
        textContent: `${m.enviado ? "enviada por e-mail" : m.erro ? `e-mail não saiu: ${m.erro}` : "guardada (e-mail não configurado)"} · IP ${m.ip ?? "—"} · dispositivo ${curto(m.dispositivo)}`,
      }),
    ]),
  );
}

function iniciarAprovador(): void {
  const msg = el("msg");
  let token = "";
  let grupo = "todos";
  const listarHistorico = async () => {
    const r = await api<{ lotes: ItemHistorico[]; ips: UsoIp[]; dispositivos: UsoDispositivo[] }>(`/api/admin/historico?grupo=${grupo}`, token);
    const alvo = el("historico");
    alvo.replaceChildren(...r.lotes.map((x) => itemHistorico(x, token, listar)));
    if (!r.lotes.length) alvo.append(criar("p", { className: "fraco", textContent: "Nenhum pedido neste filtro." }));
    el("ips").replaceChildren(tabelaIps(r.ips));
    el("dispositivos").replaceChildren(tabelaDispositivos(r.dispositivos));
    const sup = await api<{ email_configurado: boolean; mensagens: MensagemSuporte[] }>("/api/admin/suporte", token);
    el("suporte-config").textContent = sup.email_configurado
      ? "As mensagens também chegam no seu e-mail."
      : "E-mail não configurado: as mensagens ficam só aqui (veja docs/09, seção de suporte).";
    el("suporte").replaceChildren(...listaSuporte(sup.mensagens));
  };
  const listar = async () => {
    try {
      const lotes = await api<Pendente[]>("/api/admin/lotes", token);
      const alvo = el("pendentes");
      alvo.replaceChildren(...lotes.map((p) => cartaoPendente(p, token, listar)));
      if (!lotes.length) alvo.append(criar("p", { className: "fraco", textContent: "Nada aguardando revisão." }));
      await listarHistorico();
      aviso(msg, "");
    } catch (e) {
      aviso(msg, `Não foi possível carregar: ${(e as Error).message}`, "erro");
    }
  };
  for (const b of Array.from(el("filtros-historico").querySelectorAll<HTMLButtonElement>("button"))) {
    b.addEventListener("click", () => {
      grupo = b.dataset.grupo ?? "todos";
      for (const o of Array.from(el("filtros-historico").querySelectorAll<HTMLButtonElement>("button"))) {
        o.classList.toggle("ativo", o === b);
        o.setAttribute("aria-pressed", String(o === b));
      }
      listarHistorico().catch((e) => aviso(msg, `Não foi possível carregar: ${(e as Error).message}`, "erro"));
    });
  }
  painelToken("render2go-token-admin", (t) => {
    token = t;
    el("painel").hidden = false;
    listar();
    window.setInterval(listar, 30000);
  });
  el("atualizar").addEventListener("click", listar);
  el<HTMLFormElement>("form-cliente").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const saida = el("token-novo");
    try {
      const r = await api<{ cliente: string; token: string }>("/api/admin/clientes", token, {
        method: "POST",
        body: JSON.stringify({
          nome: el<HTMLInputElement>("cliente-nome").value.trim(),
          plano: el<HTMLSelectElement>("cliente-plano").value,
          cota_fotos_dia: Number(el<HTMLInputElement>("cliente-cota").value) || 500,
        }),
      });
      saida.textContent = `Cliente ${r.cliente}. Token (aparece só agora; envie ao cliente por um canal seguro): ${r.token}`;
      saida.className = "aviso ok token";
      saida.hidden = false;
      el<HTMLFormElement>("form-cliente").reset();
    } catch (e) {
      aviso(saida, (e as Error).message, "erro");
    }
  });
}

// -- suporte (/suporte) ---------------------------------------------------------------------

function iniciarSuporte(): void {
  const token = lerToken("render2go-token-cliente"); // conectado: a mensagem vai ligada à conta
  const conta = el("suporte-conta");
  const email = el<HTMLInputElement>("suporte-email");
  if (token) {
    aviso(conta, "Você está conectado com o seu código de acesso: a mensagem vai junto com os seus pedidos.");
  } else {
    email.required = true;
  }
  el<HTMLFormElement>("form-suporte").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const saida = el("suporte-msg");
    const botao = el<HTMLButtonElement>("suporte-enviar");
    botao.disabled = true;
    try {
      await api("/api/suporte", token, {
        method: "POST",
        body: JSON.stringify({
          assunto: el<HTMLInputElement>("suporte-assunto").value.trim(),
          mensagem: el<HTMLTextAreaElement>("suporte-mensagem").value.trim(),
          email: email.value.trim() || null,
        }),
      });
      aviso(saida, "Mensagem enviada. Respondemos assim que possível.", "ok");
      el<HTMLFormElement>("form-suporte").reset();
    } catch (e) {
      aviso(saida, (e as Error).message, "erro");
    } finally {
      botao.disabled = false;
    }
  });
}

// -- aviso de cookies (LGPD) -------------------------------------------------------------------
// Necessários: código de acesso, identificador do dispositivo (segurança e limite do teste) e esta
// escolha. Estatísticas e marketing: nada carrega hoje; quando houver, só com a permissão guardada aqui.

const COOKIES_VERSAO = "1";
type EscolhaCookies = { versao: string; estatisticas: boolean; marketing: boolean; quando: string };

function escolhaCookies(): EscolhaCookies | null {
  try {
    const e = JSON.parse(localStorage.getItem("render2go-cookies") ?? "null") as EscolhaCookies | null;
    return e && e.versao === COOKIES_VERSAO ? e : null;
  } catch {
    return null;
  }
}

function salvarCookies(estatisticas: boolean, marketing: boolean): void {
  const e: EscolhaCookies = { versao: COOKIES_VERSAO, estatisticas, marketing, quando: new Date().toISOString() };
  try {
    localStorage.setItem("render2go-cookies", JSON.stringify(e));
  } catch {
    /* sem armazenamento: o aviso volta na próxima visita */
  }
  // registro no servidor (prova do consentimento); falha não atrapalha a página
  api("/api/cookies", "", { method: "POST", body: JSON.stringify({ versao: COOKIES_VERSAO, estatisticas, marketing }) }).catch(() => undefined);
  document.getElementById("aviso-cookies")?.remove();
}

function avisoCookies(forcar = false): void {
  const atual = escolhaCookies();
  if (atual && !forcar) return;
  document.getElementById("aviso-cookies")?.remove();
  const caixa = (id: string, marcado: boolean, desativado = false) =>
    criar("input", { type: "checkbox", id, checked: marcado, disabled: desativado });
  const est = caixa("ck-estatisticas", atual?.estatisticas ?? false);
  const mkt = caixa("ck-marketing", atual?.marketing ?? false);
  const opcoes = criar("div", { className: "opcoes", hidden: !forcar }, [
    criar("label", {}, [caixa("ck-necessarios", true, true), "Necessários (sempre ativos): manter você conectado com o seu código, segurança e prevenção de abuso, e lembrar esta escolha."]),
    criar("label", {}, [est, "Estatísticas: medir visitas para melhorar o site. Não usamos hoje."]),
    criar("label", {}, [mkt, "Marketing: anúncios e campanhas. Não usamos hoje."]),
  ]);
  const aceitar = criar("button", { type: "button", className: "primario", textContent: "Aceitar todos" });
  const necessarios = criar("button", { type: "button", textContent: "Só os necessários" });
  const gerenciar = criar("button", { type: "button", textContent: forcar ? "Salvar escolhas" : "Gerenciar" });
  aceitar.addEventListener("click", () => salvarCookies(true, true));
  necessarios.addEventListener("click", () => salvarCookies(false, false));
  gerenciar.addEventListener("click", () => {
    if (opcoes.hidden) {
      opcoes.hidden = false;
      gerenciar.textContent = "Salvar escolhas";
    } else {
      salvarCookies(est.checked, mkt.checked);
    }
  });
  document.body.append(
    criar("section", { id: "aviso-cookies", className: "cookies", role: "dialog", ariaLabel: "Cookies e privacidade" }, [
      criar("h2", { textContent: "Cookies e privacidade" }),
      criar("p", {
        textContent:
          "Usamos cookies e o armazenamento do navegador para o site funcionar. Os opcionais só com a sua permissão, e você pode mudar a escolha a qualquer momento em \"Preferências de cookies\", no fim da página.",
      }),
      opcoes,
      criar("div", { className: "acoes" }, [aceitar, necessarios, gerenciar]),
    ]),
  );
}

document.addEventListener("DOMContentLoaded", () => {
  const pagina = document.body.dataset.pagina;
  if (pagina === "aprovar") return iniciarAprovador();
  avisoCookies();
  document.getElementById("preferencias-cookies")?.addEventListener("click", () => avisoCookies(true));
  if (pagina === "suporte") iniciarSuporte();
  else iniciarCliente();
});
