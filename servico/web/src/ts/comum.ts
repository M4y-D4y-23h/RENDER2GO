// Peças comuns às páginas do serviço (docs/10). Nada aqui usa innerHTML ou outro "sink" de HTML:
// todo texto entra por textContent e todo elemento é criado com createElement (a CSP exige Trusted
// Types e bloquearia qualquer HTML montado a partir de texto).
import ICONES from "../icones.json";

export type NomeIcone = Exclude<keyof typeof ICONES, "_comentario">;

export type TipoAviso = "info" | "ok" | "erro" | "alerta";

export const ESTADOS: Record<string, string> = {
  aguardando_envio: "Enviando fotos",
  aguardando_aprovacao: "Em análise",
  aprovado: "Na fila",
  processando: "Gerando vídeos",
  concluido: "Pronto",
  concluido_com_falhas: "Pronto, com falhas",
  recusado: "Recusado",
};

// -- DOM -----------------------------------------------------------------------------------------

export function el<T extends HTMLElement = HTMLElement>(id: string): T {
  const e = document.getElementById(id);
  if (!e) throw new Error(`elemento #${id} ausente`);
  return e as T;
}

/** createElement com propriedades, data-* (dados) e atributos (attrs, ex.: aria-label). */
export function criar<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  props: Partial<HTMLElementTagNameMap[K]> & { dados?: Record<string, string>; attrs?: Record<string, string> } = {},
  filhos: (Node | string | null | undefined | false)[] = [],
): HTMLElementTagNameMap[K] {
  const { dados, attrs, ...resto } = props;
  const e = Object.assign(document.createElement(tag), resto);
  if (dados) for (const [k, v] of Object.entries(dados)) e.dataset[k] = v;
  if (attrs) for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  for (const f of filhos) if (f) e.append(f);
  return e;
}

const SVG = "http://www.w3.org/2000/svg";

/** Ícone de linha (src/icones.json); decorativo: o texto ao lado ou o aria-label dá o nome. */
/** O nome vem da API (catálogo de finalidades): só ícones que existem. */
export function ehIcone(nome: string): nome is NomeIcone {
  return nome !== "_comentario" && Object.prototype.hasOwnProperty.call(ICONES, nome);
}

export function icone(nome: NomeIcone, classes = ""): SVGSVGElement {
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("class", `icone ${classes}`.trim());
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  const p = document.createElementNS(SVG, "path");
  p.setAttribute("d", ICONES[nome]);
  svg.append(p);
  return svg;
}

const ICONE_AVISO: Record<TipoAviso, NomeIcone> = { info: "info", ok: "ok", erro: "alerta", alerta: "alerta" };

/** Satisfação com um pedido terminado (12/10/2026): o rótulo de cada nota, da 1 à 5. */
export const NOTAS_SATISFACAO = ["Muito pouco", "Pouco", "Neutro", "Satisfeito", "Muito satisfeito"];

/** Mostra (ou esconde, com texto vazio) uma caixa de aviso com ícone. */
export function aviso(alvo: HTMLElement, texto: string | (Node | string)[], tipo: TipoAviso = "info"): void {
  alvo.className = `aviso ${tipo}`;
  if (!texto.length) {
    alvo.replaceChildren();
    alvo.hidden = true;
    return;
  }
  const corpo = typeof texto === "string" ? criar("p", { textContent: texto }) : criar("div", {}, texto);
  alvo.replaceChildren(icone(ICONE_AVISO[tipo]), corpo);
  alvo.hidden = false;
}

/** Fala com leitores de tela sem mudar o que está na tela (regiões vivas de src/partes/anuncios.html). */
export function anunciar(texto: string, urgente = false): void {
  const regiao = document.getElementById(urgente ? "anuncio-urgente" : "anuncio");
  if (!regiao) return;
  regiao.textContent = "";
  window.setTimeout(() => (regiao.textContent = texto), 80); // o mesmo texto duas vezes também é lido
}

/** Botão ocupado: desativado, com anel girando e aria-busy (o rótulo pode mudar enquanto isso). */
export function ocupado(botao: HTMLButtonElement, sim: boolean, rotulo?: string): void {
  botao.disabled = sim;
  botao.setAttribute("aria-busy", String(sim));
  if (rotulo !== undefined) {
    const texto = Array.from(botao.childNodes).find((n) => n.nodeType === Node.TEXT_NODE);
    if (texto) texto.textContent = rotulo;
    else botao.append(rotulo);
  }
}

/** Troca o texto de um botão sem perder o ícone. */
export function rotular(botao: HTMLElement, rotulo: string): void {
  const texto = Array.from(botao.childNodes).find((n) => n.nodeType === Node.TEXT_NODE);
  if (texto) texto.textContent = rotulo;
  else botao.append(rotulo);
}

/** Contador de caracteres ligado a um campo (fica em alerta perto do limite). */
export function contador(campo: HTMLInputElement | HTMLTextAreaElement, saida: HTMLElement): () => void {
  const max = campo.maxLength > 0 ? campo.maxLength : 0;
  const atualizar = () => {
    const n = campo.value.length;
    saida.textContent = max ? `${n}/${max}` : String(n);
    saida.classList.toggle("perto", max > 0 && n >= max * 0.9);
  };
  campo.addEventListener("input", atualizar);
  atualizar();
  return atualizar;
}

/** Campo com erro: aria-invalid, mensagem ligada por aria-describedby e foco. */
export function marcarErro(campo: HTMLElement, erro: boolean): void {
  if (erro) campo.setAttribute("aria-invalid", "true");
  else campo.removeAttribute("aria-invalid");
}

/** Botão "Mostrar/Ocultar" de um campo de código. */
export function mostrarSenha(botao: HTMLButtonElement, campo: HTMLInputElement): void {
  botao.addEventListener("click", () => {
    const mostrar = campo.type === "password";
    campo.type = mostrar ? "text" : "password";
    botao.setAttribute("aria-pressed", String(mostrar));
    botao.textContent = mostrar ? "Ocultar" : "Mostrar";
  });
}

// -- texto, números e datas -----------------------------------------------------------------------

export function plural(n: number, um: string, varios: string): string {
  return `${n.toLocaleString("pt-BR")} ${n === 1 ? um : varios}`;
}

export function capitalizar(texto: string): string {
  return texto ? texto[0].toLocaleUpperCase("pt-BR") + texto.slice(1) : texto;
}

export function quando(epoch: number): string {
  return new Date(epoch * 1000).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}

export function quandoIso(epoch: number): string {
  return new Date(epoch * 1000).toISOString();
}

export function tempo(epoch: number): HTMLTimeElement {
  return criar("time", { dateTime: quandoIso(epoch), textContent: quando(epoch) });
}

export function hora(): string {
  return new Date().toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
}

export function minutos(s: number): string {
  if (s < 60) return "menos de 1 min";
  const m = Math.ceil(s / 60);
  return m < 90 ? `cerca de ${m} min` : `cerca de ${Math.round(m / 60)} h`;
}

export function bytes(n: number): string {
  if (n < 1024 * 1024) return `${Math.max(1, Math.round(n / 1024))} KB`;
  const mb = n / (1024 * 1024);
  return mb < 1024 ? `${mb.toLocaleString("pt-BR", { maximumFractionDigits: mb < 10 ? 1 : 0 })} MB` : `${(mb / 1024).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} GB`;
}

/** Centavos → "R$ 1.234,50". */
export function reais(centavos: number): string {
  return (centavos / 100).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

export function curto(id: string | null | undefined): string {
  return id ? id.slice(0, 8) : "—";
}

/** Só https (ou http quando a própria página é http, no desenvolvimento): nada de javascript:, data: etc. */
export function urlSegura(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const u = new URL(url, location.href);
    if (u.protocol === "https:" || (u.protocol === "http:" && location.protocol === "http:")) return u.href;
  } catch {
    /* URL malformada */
  }
  return null;
}

// -- armazenamento no navegador --------------------------------------------------------------------

/** Marca "entrou neste navegador" do cliente (inicio.ts, entrada.ts e o topo de todas as páginas). */
export const CHAVE_CONECTADO = "render2go-conectado";

// a sessão fica num cookie HttpOnly (o JavaScript não lê nem guarda credencial nenhuma); aqui fica só a
// marca "entrou neste navegador", para o painel aparecer desde o 1º quadro (inicio.ts)
export function conectado(chave: string): boolean {
  try {
    return localStorage.getItem(chave) === "1";
  } catch {
    return false;
  }
}

export function marcarConectado(chave: string, sim: boolean): void {
  try {
    if (sim) localStorage.setItem(chave, "1");
    else localStorage.removeItem(chave);
    // códigos de acesso que versões antigas da página guardavam no navegador saem daqui
    for (const antiga of ["render2go-token-cliente", "render2go-token-admin", "gpu" + "2go-token-cliente", "gpu" + "2go-token-admin"]) {
      localStorage.removeItem(antiga);
    }
  } catch {
    /* sem armazenamento local: a página confere a sessão com a API a cada visita */
  }
}

// chaves com o nome antigo do projeto (antes de Render2Go) são lidas uma vez e migradas
function migrarChave(chave: string): void {
  const antiga = chave.replace("render2go-", "gpu" + "2go-");
  const valor = localStorage.getItem(antiga);
  if (valor && !localStorage.getItem(chave)) localStorage.setItem(chave, valor);
  if (valor) localStorage.removeItem(antiga);
}

// identificador deste navegador (limite do teste por dispositivo e registro de auditoria);
// janela anônima ou dados apagados geram outro (o teto por rede, RENDER2GO_TESTE_FOTOS_IP, vem desligado)
function uuid4(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

let DISPOSITIVO_SESSAO = "";
export function dispositivo(): string {
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

// -- API -------------------------------------------------------------------------------------------

export class ErroApi extends Error {
  constructor(
    mensagem: string,
    readonly status: number,
    // por que a API recusou, quando a página pode resolver (X-Render2Go-Motivo): "confirmar" (confirme que é
    // você, configurações da conta) ou "aceite" (termos ou privacidade em versão nova)
    readonly motivo: string | null = null,
  ) {
    super(mensagem);
  }
}

function mensagemDeErro(detalhe: unknown, status: number): string {
  if (typeof detalhe === "string") return detalhe;
  if (Array.isArray(detalhe)) {
    const msgs = detalhe.map((d) => (d && typeof d === "object" && "msg" in d ? String(d.msg) : "")).filter(Boolean);
    if (msgs.length) return msgs.join("; ");
  }
  if (detalhe && typeof detalhe === "object" && "problemas" in detalhe && Array.isArray(detalhe.problemas)) {
    return `algumas fotos não chegaram ao armazenamento (${detalhe.problemas.slice(0, 5).join("; ")}); tente enviar de novo`;
  }
  if (status === 429) return "muitas tentativas seguidas; espere um pouco e tente de novo";
  if (status >= 500) return "o serviço está com um problema agora; tente de novo em instantes";
  return detalhe ? JSON.stringify(detalhe) : `erro ${status}`;
}

/** Respostas pedidas no <head> com a sessão que a página tinha ao abrir: quando ela não vale mais (venceu, ou a
 * pessoa entrou de novo agora), nenhuma delas serve, e as telas pedem de novo. */
export function descartarAntecipados(): void {
  delete (window as Window & { __render2go?: Record<string, Promise<Response>> }).__render2go;
}

/** Resposta que o inicio.js já pediu no <head> (usada uma vez só); sem ela, null. */
function antecipado(caminho: string): Promise<Response> | null {
  const pedidos = (window as Window & { __render2go?: Record<string, Promise<Response>> }).__render2go;
  const p = pedidos?.[caminho] ?? null;
  if (p) delete pedidos![caminho];
  return p;
}

/** Pedido à API: a sessão vai no cookie (mesmo site); o cabeçalho X-Render2Go mostra que o pedido saiu
 * desta página (outro site não consegue mandá-lo), e a API recusa sem ele quem muda alguma coisa. */
export async function api<T>(caminho: string, init: RequestInit = {}): Promise<T> {
  const pedir = () =>
    fetch(caminho, {
      credentials: "same-origin",
      ...init,
      headers: {
        "Content-Type": "application/json",
        "X-Render2Go": "1",
        "X-Dispositivo": dispositivo(),
        "X-Fuso": fuso(),
        ...(init.headers ?? {}),
      },
    });
  const pronto = (init.method ?? "GET") === "GET" ? antecipado(caminho) : null;
  let r: Response;
  try {
    r = pronto ? await pronto.catch(pedir) : await pedir();
  } catch {
    throw new ErroApi("sem conexão com o serviço; confira a internet e tente de novo", 0);
  }
  const corpo = await r.json().catch(() => ({}));
  if (!r.ok) throw new ErroApi(mensagemDeErro((corpo as { detail?: unknown }).detail, r.status), r.status, r.headers.get("X-Render2Go-Motivo"));
  return corpo as T;
}

export function textoErro(e: unknown): string {
  return capitalizar(e instanceof Error ? e.message : String(e));
}

/** Botão de "Tentar de novo" numa caixa de aviso (falha de rede ao carregar o painel). */
export function avisoComRecarga(alvo: HTMLElement, texto: string): void {
  const tentar = criar("button", { type: "button", className: "botao pequeno", textContent: "Tentar de novo" });
  tentar.addEventListener("click", () => location.reload());
  aviso(alvo, [criar("p", { textContent: texto }), tentar], "erro");
}

// -- listas que se atualizam sozinhas ---------------------------------------------------------------

export type ItemLista = { chave: string; assinatura: string; criar: () => HTMLElement };

/**
 * Atualiza uma lista trocando só os itens que mudaram (pela assinatura). Quem está com o foco num item
 * trocado volta para o elemento equivalente (data-foco), e itens iguais nem saem do lugar: o leitor
 * de tela não repete nada e as imagens não são baixadas de novo.
 */
export function reconciliar(alvo: HTMLElement, itens: ItemLista[], vazio?: () => HTMLElement): void {
  const ativo = document.activeElement instanceof HTMLElement && alvo.contains(document.activeElement) ? document.activeElement : null;
  const focoChave = ativo?.closest<HTMLElement>("[data-foco]")?.dataset.foco;
  let perdeuFoco = false;
  const atuais = new Map<string, HTMLElement>();
  for (const filho of Array.from(alvo.children) as HTMLElement[]) {
    if (filho.dataset.chave && itens.length) atuais.set(filho.dataset.chave, filho);
    else {
      if (ativo && filho.contains(ativo)) perdeuFoco = true;
      filho.remove();
    }
  }
  let anterior: Element | null = null;
  for (const it of itens) {
    let e = atuais.get(it.chave);
    atuais.delete(it.chave);
    if (!e || e.dataset.assinatura !== it.assinatura) {
      const novo = it.criar();
      novo.dataset.chave = it.chave;
      novo.dataset.assinatura = it.assinatura;
      if (e) {
        if (ativo && e.contains(ativo)) perdeuFoco = true;
        e.replaceWith(novo);
      }
      e = novo;
    }
    const esperado: Element | null = anterior ? anterior.nextElementSibling : alvo.firstElementChild;
    if (esperado !== e) {
      if (ativo && e.contains(ativo)) perdeuFoco = true;
      alvo.insertBefore(e, esperado);
    }
    anterior = e;
  }
  for (const sobra of atuais.values()) {
    if (ativo && sobra.contains(ativo)) perdeuFoco = true;
    sobra.remove();
  }
  if (!itens.length && vazio) alvo.append(vazio());
  alvo.setAttribute("aria-busy", "false");
  if (perdeuFoco && focoChave) {
    const alvoFoco = Array.from(alvo.querySelectorAll<HTMLElement>("[data-foco]")).find((x) => x.dataset.foco === focoChave);
    alvoFoco?.focus({ preventScroll: true });
  }
}

export function vazio(titulo: string, texto: string, nomeIcone: NomeIcone = "video"): HTMLElement {
  return criar("div", { className: "vazio" }, [icone(nomeIcone), criar("strong", { textContent: titulo }), criar("p", { textContent: texto })]);
}

/**
 * Repete `fn` a cada `ms` enquanto `continuar()` for verdadeiro, só com a aba visível. Ao voltar
 * para a aba, atualiza na hora se a última atualização tiver mais de `ms`.
 */
export function repetir(fn: () => Promise<boolean>, ms: number): { agora: () => void } {
  let timer = 0;
  let ultima = 0;
  let ativo = false;
  let geracao = 0; // só a rodada mais recente agenda a próxima (sem timers em dobro)
  const rodar = async () => {
    window.clearTimeout(timer);
    const minha = ++geracao;
    ultima = Date.now();
    const continuar = await fn().catch(() => true);
    if (minha !== geracao) return;
    ativo = continuar;
    if (ativo && !document.hidden) timer = window.setTimeout(rodar, ms);
  };
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) window.clearTimeout(timer);
    else if (ativo || Date.now() - ultima > ms) rodar();
  });
  return { agora: () => void rodar() };
}

// -- diálogos -------------------------------------------------------------------------------------

/** Diálogo nativo (<dialog>): foco preso, Esc fecha e o foco volta para quem abriu. */
export function dialogo(titulo: string, classe = ""): { d: HTMLDialogElement; corpo: HTMLElement; titulo: HTMLElement } {
  const id = `dialogo-${Math.random().toString(36).slice(2, 9)}`;
  const h = criar("h2", { id, textContent: titulo });
  const fechar = criar("button", { type: "button", className: "botao fantasma pequeno", attrs: { "aria-label": "Fechar" } }, [icone("fechar")]);
  const corpo = criar("div", { className: "dialogo-corpo" });
  const d = criar("dialog", { className: classe }, [criar("div", { className: "dialogo-cab" }, [h, fechar]), corpo]);
  d.setAttribute("aria-labelledby", id);
  fechar.addEventListener("click", () => d.close());
  d.addEventListener("click", (ev) => {
    if (ev.target === d) d.close(); // clique no fundo escurecido
  });
  document.body.append(d);
  return { d, corpo, titulo: h };
}

/** Pergunta antes de uma ação. Com `palavra`, a pessoa digita a palavra para liberar o botão (ação sem volta). */
export function confirmar(opcoes: { titulo: string; texto: string | string[]; confirmar: string; perigo?: boolean; palavra?: string }): Promise<boolean> {
  const origem = document.activeElement as HTMLElement | null;
  const { d, corpo } = dialogo(opcoes.titulo, "confirmacao");
  const nao = criar("button", { type: "button", className: "botao", textContent: "Cancelar" });
  const sim = criar("button", { type: "button", className: `botao ${opcoes.perigo ? "perigo" : "primario"}`, textContent: opcoes.confirmar });
  const textos = typeof opcoes.texto === "string" ? [opcoes.texto] : opcoes.texto;
  corpo.append(...textos.map((t) => criar("p", { textContent: t })));
  let campo: HTMLInputElement | null = null;
  if (opcoes.palavra) {
    const palavra = opcoes.palavra;
    const id = `${d.getAttribute("aria-labelledby")}-palavra`;
    campo = criar("input", { id, autocomplete: "off", spellcheck: false, autocapitalize: "characters", maxLength: 20 });
    sim.disabled = true;
    campo.addEventListener("input", () => (sim.disabled = campo!.value.trim().toUpperCase() !== palavra));
    corpo.append(criar("div", { className: "campo" }, [criar("label", { htmlFor: id, textContent: `Para confirmar, digite ${palavra}` }), campo]));
  }
  corpo.append(criar("div", { className: "dialogo-acoes" }, [nao, sim]));
  nao.addEventListener("click", () => d.close("nao"));
  sim.addEventListener("click", () => d.close("sim"));
  return new Promise((resolver) => {
    d.addEventListener("close", () => {
      resolver(d.returnValue === "sim");
      d.remove();
      origem?.focus();
    });
    d.showModal();
    (campo ?? nao).focus();
  });
}

/** Clique comum (botão principal, sem Ctrl, Shift, Alt ou ⌘): os outros abrem o link numa aba nova, como sempre. */
export function cliqueSimples(ev: MouseEvent): boolean {
  return ev.button === 0 && !ev.metaKey && !ev.ctrlKey && !ev.shiftKey && !ev.altKey && !ev.defaultPrevented;
}

/** Copia para a área de transferência; false sem permissão (a página deixa o texto selecionado). */
export async function copiarTexto(texto: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(texto);
    return true;
  } catch {
    return false;
  }
}

/** Baixa um texto gerado na página (códigos de recuperação). */
export function baixarTexto(texto: string, nome: string): void {
  const url = URL.createObjectURL(new Blob([texto], { type: "text/plain;charset=utf-8" }));
  const a = criar("a", { href: url, download: nome });
  document.body.append(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 10000);
}

/** Baixa um JSON gerado na página (registro de auditoria). */
export function baixarJson(dados: unknown, nome: string): void {
  const url = URL.createObjectURL(new Blob([JSON.stringify(dados, null, 2)], { type: "application/json" }));
  const a = criar("a", { href: url, download: nome });
  document.body.append(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 10000);
}
