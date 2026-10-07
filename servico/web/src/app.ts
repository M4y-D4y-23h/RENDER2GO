// Páginas do serviço GPU2GO (ADR 0003, fase 3): cliente (/) e aprovador (/aprovar).
// Os arquivos nunca passam pela API: as fotos vão direto ao armazenamento (R2) por URLs
// assinadas, uma por foto; os vídeos voltam por links assinados que expiram em 24 h.
// Compilar: cd servico/web && npm ci && npm run build  (gera app.js)

type Receita = { id: string; label: string; nicho: string; acao: string; exige_consentimento: boolean };
type Receitas = {
  receitas: Receita[];
  formatos: string[];
  qualidades: string[];
  limites: { fotos: number; bytes_foto: number; tipos: string[] };
};
type Video = { arquivo: string; status: string; aviso: string | null; url?: string };
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
};
type Envio = { original: string; url: string; tipo: string };
type Pendente = {
  lote: string;
  cliente: string;
  criado: number;
  pedido: { prompt: string; receita: string; qualidade: string; duracao: number | null; variacoes: number };
  filtro_c1: string;
  fotos: { nome: string; url: string }[];
};

const ESTADOS: Record<string, string> = {
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
function lerToken(chave: string): string {
  try {
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

async function api<T>(caminho: string, token: string, init: RequestInit = {}): Promise<T> {
  const r = await fetch(caminho, {
    ...init,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", ...(init.headers ?? {}) },
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
  if (l.total) {
    const barra = criar("progress", { max: l.total, value: l.prontos });
    cartao.append(criar("div", { className: "linha" }, [barra, ` ${l.prontos} de ${l.total} vídeo(s)`]));
  }
  if (l.motivo) cartao.append(criar("p", { className: "aviso erro", textContent: `Motivo: ${l.motivo}` }));
  const prontos = l.videos.filter((v) => v.url);
  if (prontos.length) {
    const lista = criar("ul", { className: "videos" });
    for (const v of prontos) {
      lista.append(
        criar("li", {}, [criar("a", { href: v.url!, textContent: v.arquivo, download: v.arquivo, rel: "noopener" })]),
      );
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
    let ignorados = 0;
    for (const f of Array.from(lista)) {
      if (!tipos.has(f.type) || f.size > receitas.limites.bytes_foto) {
        ignorados++;
        continue;
      }
      arquivos.set(f.webkitRelativePath || f.name, f);
    }
    el("contagem").textContent = `${arquivos.size} foto(s) selecionada(s)` + (ignorados ? ` · ${ignorados} ignorada(s) (use JPG, PNG ou WebP até 50 MB)` : "");
  };

  painelToken("gpu2go-token-cliente", async (t) => {
    token = t;
    try {
      receitas = await api<Receitas>("/api/receitas", token);
    } catch (e) {
      aviso(msg, `Token inválido ou serviço fora do ar: ${(e as Error).message}`, "erro");
      return;
    }
    const sel = el<HTMLSelectElement>("receita");
    sel.replaceChildren(...receitas.receitas.map((r) => criar("option", { value: r.id, textContent: `${r.label} (${r.nicho})` })));
    el("painel").hidden = false;
    listar();
  });

  el<HTMLInputElement>("fotos").addEventListener("change", (ev) => escolher((ev.target as HTMLInputElement).files));
  el<HTMLInputElement>("pasta").addEventListener("change", (ev) => escolher((ev.target as HTMLInputElement).files));
  el("limpar").addEventListener("click", () => {
    arquivos.clear();
    el("contagem").textContent = "";
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
      el("contagem").textContent = "";
      el<HTMLFormElement>("form-lote").reset();
      listar();
    } catch (e) {
      aviso(msg, (e as Error).message, "erro");
    } finally {
      botao.disabled = false;
      el<HTMLProgressElement>("progresso").hidden = true;
    }
  });
}

// -- aprovador -----------------------------------------------------------------------------

function cartaoPendente(p: Pendente, token: string, aoMudar: () => void): HTMLElement {
  const msg = criar("p", { className: "aviso", hidden: true });
  const fotos = criar("div", { className: "grade" });
  for (const f of p.fotos) {
    fotos.append(criar("a", { href: f.url, target: "_blank", rel: "noopener" }, [criar("img", { src: f.url, alt: f.nome, loading: "lazy" })]));
  }
  const pedido = p.pedido;
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
      criar("span", { className: "fraco", textContent: ` · cliente ${p.cliente} · ${quando(p.criado)}` }),
    ]),
    criar("p", { className: "pedido", textContent: `“${pedido.prompt}”` }),
    criar("p", {
      className: "fraco",
      textContent: `${pedido.receita} · ${pedido.qualidade} · ${pedido.duracao ?? "padrão"} s · ${pedido.variacoes} variação(ões) · filtro C1: ${p.filtro_c1 || "ok"}`,
    }),
    fotos,
    criar("div", { className: "acoes" }, [aprovar, motivo, recusar]),
    msg,
  ]);
}

function iniciarAprovador(): void {
  const msg = el("msg");
  let token = "";
  const listar = async () => {
    try {
      const lotes = await api<Pendente[]>("/api/admin/lotes", token);
      const alvo = el("pendentes");
      alvo.replaceChildren(...lotes.map((p) => cartaoPendente(p, token, listar)));
      if (!lotes.length) alvo.append(criar("p", { className: "fraco", textContent: "Nada aguardando revisão." }));
      aviso(msg, "");
    } catch (e) {
      aviso(msg, `Não foi possível carregar: ${(e as Error).message}`, "erro");
    }
  };
  painelToken("gpu2go-token-admin", (t) => {
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

document.addEventListener("DOMContentLoaded", () => {
  if (document.body.dataset.pagina === "aprovar") iniciarAprovador();
  else iniciarCliente();
});
