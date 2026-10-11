// Peças do painel do dono (endereço secreto; ADR 0004, 0007 e 0009): a sessão e as permissões de quem entrou,
// o jeito de cada seção carregar o que mostra, as tabelas e as abas. A API do painel fica no mesmo endereço da
// página: os caminhos são relativos a ela ("api/lotes" é "<endereço secreto>/api/lotes"), os mesmos que o
// inicio.js já pede no <head>.
import { ErroApi, aviso, criar, el, icone, marcarConectado, plural, quando, quandoIso, reais, textoErro } from "./comum";

export const CHAVE_PAINEL = "render2go-painel-conectado";
/** O papel de quem entrou da última vez: o inicio.js esconde desde o 1º quadro o que o papel não abre. */
export const CHAVE_PAPEL = "render2go-painel-papel";
/** Aviso para a entrada depois de recarregar a página (sessão vencida), só nesta aba. */
export const AVISO_ENTRADA = "render2go-painel-aviso";

export type Papel = "dono" | "operador";
export type Sessao = { admin: string | null; papel?: Papel; emergencia?: boolean; totp?: boolean; rotas?: string[] };

export const PAPEIS: Record<Papel, string> = { dono: "Dono", operador: "Operador" };
// os mesmos textos de PAPEIS_PAINEL (api.py): o que cada papel faz
export const PAPEIS_DESCRICAO: Record<Papel, string> = {
  dono: "Tudo: revisão, contas e saldo, pods e parada de emergência, pagamentos, configuração, acessos e auditoria.",
  operador: "Revisão dos pedidos e consultas (histórico, contas, suporte, interesses e pods), sem mexer em dinheiro, pods ou acessos.",
};

let sessao: Sessao = { admin: null };
let rotas = new Set<string>();

export function definirSessao(s: Sessao): void {
  sessao = s;
  rotas = new Set(s.rotas ?? []);
}

export function sessaoAtual(): Sessao {
  return sessao;
}

/** O papel de quem entrou abre esta rota (ex.: "POST /api/clientes/{cliente_id}/saldo")? O servidor confere de
 * novo em cada pedido: aqui é só para não mostrar o que o papel não usa. */
export function pode(rota: string): boolean {
  return rotas.has(rota);
}

/** A sessão do painel venceu (12 h) ou foi encerrada: volta para a entrada, com o aviso. */
export function sessaoVencida(): void {
  try {
    sessionStorage.setItem(AVISO_ENTRADA, "Sua sessão do painel terminou. Entre de novo.");
  } catch {
    /* sem armazenamento: a entrada aparece sem o aviso */
  }
  marcarConectado(CHAVE_PAINEL, false);
  location.reload();
}

// -- aviso que flutua no pé da tela (atualização em segundo plano que falhou): não empurra a seção -------------

export function avisoFlutuante(texto: string): void {
  const msg = el("msg");
  const fechar = criar("button", { type: "button", className: "botao fantasma pequeno", attrs: { "aria-label": "Fechar o aviso" } }, [icone("fechar")]);
  fechar.addEventListener("click", limparAvisoFlutuante);
  aviso(msg, [criar("p", { textContent: texto }), fechar], "erro");
  msg.classList.add("aviso-flutuante");
}

export function limparAvisoFlutuante(): void {
  const msg = el("msg");
  if (msg.hidden) return;
  aviso(msg, "");
  msg.classList.add("aviso-flutuante");
}

/** Erro ao carregar ou atualizar uma seção: sessão vencida volta para a entrada; na 1ª carga, a mensagem fica no
 * lugar do esqueleto, com "Tentar de novo"; numa atualização, o aviso flutua no pé da tela. */
export function erroDeCarga(e: unknown, regiao: HTMLElement | null, tentar: () => void): void {
  if (e instanceof ErroApi && e.status === 401) {
    sessaoVencida();
    return;
  }
  if (!regiao) {
    avisoFlutuante(`Não foi possível atualizar: ${textoErro(e)}`);
    return;
  }
  const caixa = criar("div");
  const botao = criar("button", { type: "button", className: "botao pequeno", textContent: "Tentar de novo" });
  botao.addEventListener("click", tentar);
  aviso(caixa, [criar("p", { textContent: `Não foi possível carregar: ${textoErro(e)}` }), botao], "erro");
  regiao.replaceChildren(caixa);
  regiao.setAttribute("aria-busy", "false");
}

/** Erro de uma ação (aprovar, lançar no saldo...): sessão vencida volta para a entrada; o resto vai para `alvo`. */
export function erroDeAcao(e: unknown, alvo: HTMLElement): void {
  if (e instanceof ErroApi && e.status === 401) {
    sessaoVencida();
    return;
  }
  aviso(alvo, textoErro(e), "erro");
}

// -- seções: carregam quando abrem; depois, atualizam sem tirar da tela o que já está nela -------------------

export type Controle = {
  /** A seção abriu: carrega na 1ª vez; depois, atualiza (se a última carga tiver mais de alguns segundos). */
  abrir: () => Promise<boolean>;
  /** Atualiza agora (botão "Atualizar" e a cada 30 s, só a seção aberta); false se não deu (o aviso já está na
   * tela). */
  atualizar: () => Promise<boolean>;
};

export function controle(opcoes: {
  /** Onde a mensagem de erro entra, no lugar do esqueleto, se a 1ª carga falhar. */
  regiao: HTMLElement;
  /** Quem fica com aria-busy até a 1ª carga (o padrão é a própria região). */
  ocupado?: HTMLElement;
  carregar: () => Promise<void>;
}): Controle {
  let carregado = false;
  let ultima = 0;
  let emCurso: Promise<boolean> | null = null;
  const rodar = (): Promise<boolean> => {
    if (emCurso) return emCurso; // a seção abriu enquanto a atualização periódica já pedia: um pedido só
    emCurso = (async () => {
      try {
        await opcoes.carregar();
        carregado = true;
        ultima = Date.now();
        (opcoes.ocupado ?? opcoes.regiao).setAttribute("aria-busy", "false");
        return true;
      } catch (e) {
        erroDeCarga(e, carregado ? null : opcoes.regiao, () => void rodar());
        if (!carregado) opcoes.ocupado?.setAttribute("aria-busy", "false");
        return false;
      } finally {
        emCurso = null;
      }
    })();
    return emCurso;
  };
  return {
    abrir: () => (carregado && Date.now() - ultima < 5000 ? Promise.resolve(true) : rodar()),
    atualizar: rodar,
  };
}

/** Troca o conteúdo de `alvo` (só se a assinatura dos dados mudou), devolvendo o foco ao elemento equivalente
 * (data-foco) para quem estava num botão da tabela: a atualização a cada 30 s não tira ninguém do lugar. */
const assinaturas = new WeakMap<HTMLElement, string>();
export function trocar(alvo: HTMLElement, filhos: (Node | null | undefined | false)[], assinatura?: string): boolean {
  if (assinatura !== undefined) {
    if (assinaturas.get(alvo) === assinatura) return false;
    assinaturas.set(alvo, assinatura);
  }
  const ativo = document.activeElement instanceof HTMLElement && alvo.contains(document.activeElement) ? document.activeElement : null;
  const chave = ativo?.closest<HTMLElement>("[data-foco]")?.dataset.foco;
  alvo.replaceChildren(...filhos.filter((f): f is Node => !!f));
  if (ativo && chave) {
    const novo = Array.from(alvo.querySelectorAll<HTMLElement>("[data-foco]")).find((x) => x.dataset.foco === chave);
    novo?.focus({ preventScroll: true });
  }
  return true;
}

// -- tabelas, contagens, abas e formatos ----------------------------------------------------------------------

export type Linha = { celulas: (string | number | Node | null)[]; classe?: string };

/** Tabela com legenda (para leitores de tela) e cabeçalhos de coluna; [título, é número?]. */
export function tabela(cabecalhos: [string, boolean][], linhas: Linha[], legenda: string): HTMLTableElement {
  const th = ([t, num]: [string, boolean]) => criar("th", { textContent: t, className: num ? "num" : "", scope: "col" });
  const corpo = linhas.map((l) =>
    criar(
      "tr",
      { className: l.classe ?? "" },
      l.celulas.map((c, i) => criar("td", { className: cabecalhos[i][1] ? "num" : "" }, [c instanceof Node ? c : c == null ? "—" : String(c)])),
    ),
  );
  return criar("table", {}, [criar("caption", { className: "visualmente-oculto", textContent: legenda }), criar("thead", {}, [criar("tr", {}, cabecalhos.map(th))]), criar("tbody", {}, corpo)]);
}

export function semLinhas(texto: string): HTMLElement {
  return criar("p", { className: "fraco vazio-tabela", textContent: texto });
}

/** O número ao lado de uma seção do menu; o leitor de tela ouve também o que ele conta ("Revisão 3 aguardando"). */
export function contagem(alvo: HTMLElement, n: number, sufixo: string): void {
  if (!n) {
    alvo.replaceChildren();
    return;
  }
  alvo.replaceChildren(n > 99 ? "99+" : String(n), criar("span", { className: "visualmente-oculto", textContent: ` ${sufixo}` }));
}

/** Abas (role="tab"): clique, setas, Home e End; só o painel da aba escolhida aparece. */
export function iniciarAbas(lista: HTMLElement): void {
  const abas = Array.from(lista.querySelectorAll<HTMLButtonElement>('[role="tab"]'));
  const escolher = (aba: HTMLButtonElement, focar: boolean) => {
    for (const a of abas) {
      const sim = a === aba;
      a.setAttribute("aria-selected", String(sim));
      a.tabIndex = sim ? 0 : -1;
      const painel = document.getElementById(a.getAttribute("aria-controls") ?? "");
      if (painel) painel.hidden = !sim;
    }
    if (focar) aba.focus();
  };
  for (const a of abas) a.addEventListener("click", () => escolher(a, false));
  lista.addEventListener("keydown", (ev) => {
    const i = abas.indexOf(document.activeElement as HTMLButtonElement);
    const destino: Record<string, number> = { ArrowLeft: i - 1, ArrowRight: i + 1, Home: 0, End: abas.length - 1 };
    if (i < 0 || !(ev.key in destino)) return;
    ev.preventDefault();
    escolher(abas[(destino[ev.key] + abas.length) % abas.length], true);
  });
}

const DOLAR = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 3 });

export function dolares(v: number | null): string {
  return v == null ? "—" : DOLAR.format(v);
}

/** 754 → "12 min 34 s"; 4000 → "1 h 06 min". */
export function duracao(s: number | null): string {
  if (s == null) return "—";
  const t = Math.round(s);
  if (t < 60) return `${t} s`;
  if (t < 3600) return `${Math.floor(t / 60)} min ${String(t % 60).padStart(2, "0")} s`;
  return `${Math.floor(t / 3600)} h ${String(Math.floor((t % 3600) / 60)).padStart(2, "0")} min`;
}

/** Há quanto tempo (epoch em segundos): "há 3 min", "há 2 h", "há 4 dias". */
export function desde(epoch: number): string {
  const s = Math.max(0, Date.now() / 1000 - epoch);
  if (s < 60) return "há menos de 1 min";
  if (s < 3600) return `há ${Math.floor(s / 60)} min`;
  if (s < 86400 * 2) return `há ${plural(Math.floor(s / 3600), "hora", "horas")}`;
  return `há ${plural(Math.floor(s / 86400), "dia", "dias")}`;
}

/** O navegador e o sistema de um User-Agent ("Chrome no Android"); o texto inteiro fica no title. */
export function navegadorCurto(ua: string | null | undefined): string {
  if (!ua) return "—";
  const nav = /Edg\//.test(ua)
    ? "Edge"
    : /OPR\//.test(ua)
      ? "Opera"
      : /Firefox\//.test(ua)
        ? "Firefox"
        : /Chrome\//.test(ua)
          ? "Chrome"
          : /Safari\//.test(ua)
            ? "Safari"
            : /curl|python|requests|httpx|wget/i.test(ua)
              ? "Programa"
              : "Navegador";
  const sistema = /Android/.test(ua) ? "Android" : /iPhone|iPad|iPod/.test(ua) ? "iPhone/iPad" : /Windows/.test(ua) ? "Windows" : /Mac OS X|Macintosh/.test(ua) ? "Mac" : /Linux/.test(ua) ? "Linux" : "";
  return sistema ? `${nav} no ${sistema}` : nav;
}

/** Data curta para tabelas ("10/10 23:50"; de outro ano, "10/10/25 23:50"); a data inteira fica no title. */
export function tempoCurto(epoch: number): HTMLTimeElement {
  const d = new Date(epoch * 1000);
  const mesmoAno = d.getFullYear() === new Date().getFullYear();
  const dia = d.toLocaleDateString("pt-BR", mesmoAno ? { day: "2-digit", month: "2-digit" } : { day: "2-digit", month: "2-digit", year: "2-digit" });
  const hora = d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  return criar("time", { dateTime: quandoIso(epoch), textContent: `${dia} ${hora}`, title: quando(epoch) });
}

export function dinheiro(centavos: number | null | undefined): string {
  return centavos == null ? "—" : reais(centavos);
}
