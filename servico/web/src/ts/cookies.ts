// Aviso de cookies (LGPD), na página do cliente, no /suporte e nas páginas legais (política em /cookies).
// Necessários: a sessão de quem entrou (cookie HttpOnly), a verificação "não sou um robô" (Cloudflare
// Turnstile, só na entrada e no suporte sem conta), o identificador do dispositivo (segurança e limite do
// teste) e esta escolha. Estatísticas e marketing: nada carrega hoje; quando houver, só com a permissão
// guardada aqui.
//
// Acessibilidade: o aviso é uma região nomeada no começo do <body> (é o primeiro lugar que o teclado e
// o leitor de tela encontram), embora apareça fixo no rodapé da tela. Aberto pelo link "Preferências de
// cookies", recebe o foco; Esc fecha sem salvar e o foco volta para o link.
import { anunciar, api, criar, icone } from "./comum";

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
  api("/api/cookies", { method: "POST", body: JSON.stringify({ versao: COOKIES_VERSAO, estatisticas, marketing }) }).catch(() => undefined);
}

function avisoCookies(forcar = false, origem: HTMLElement | null = null): void {
  const atual = escolhaCookies();
  if (atual && !forcar) return;
  document.getElementById("aviso-cookies")?.remove();
  const caixa = (id: string, marcado: boolean, desativado = false) => criar("input", { type: "checkbox", id, checked: marcado, disabled: desativado });
  const est = caixa("ck-estatisticas", atual?.estatisticas ?? false);
  const mkt = caixa("ck-marketing", atual?.marketing ?? false);
  const opcao = (entrada: HTMLInputElement, titulo: string, texto: string) =>
    criar("label", {}, [entrada, criar("span", {}, [criar("strong", { textContent: titulo }), ` ${texto}`])]);
  const opcoes = criar("div", { className: "opcoes", hidden: !forcar }, [
    opcao(caixa("ck-necessarios", true, true), "Necessários (sempre ativos):", "manter você conectado à sua conta, segurança (inclusive a verificação “não sou um robô”) e prevenção de abuso, e lembrar esta escolha."),
    opcao(est, "Estatísticas:", "medir visitas para melhorar o site. Não usamos hoje."),
    opcao(mkt, "Marketing:", "anúncios e campanhas. Não usamos hoje."),
  ]);
  const aceitar = criar("button", { type: "button", className: "botao primario", textContent: "Aceitar todos" });
  const necessarios = criar("button", { type: "button", className: "botao", textContent: "Só os necessários" });
  const gerenciar = criar("button", { type: "button", className: "botao fantasma", textContent: forcar ? "Salvar escolhas" : "Gerenciar" });
  gerenciar.setAttribute("aria-expanded", String(forcar));
  const titulo = criar("h2", { id: "titulo-cookies", tabIndex: -1 }, [icone("escudo"), "Cookies e privacidade"]);
  const regiao = criar(
    "section",
    { id: "aviso-cookies", className: "cookies", attrs: { "aria-labelledby": "titulo-cookies" } },
    [
      titulo,
      criar("p", {}, [
        "Usamos cookies e o armazenamento do navegador para o site funcionar. Os opcionais só com a sua permissão, e você pode mudar a escolha a qualquer momento em “Preferências de cookies”, no fim da página. Detalhes na ",
        criar("a", { href: "/cookies", textContent: "Política de Cookies" }),
        ".",
      ]),
      opcoes,
      criar("div", { className: "acoes" }, [aceitar, necessarios, gerenciar]),
    ],
  );

  const fechar = (salvou: boolean) => {
    document.removeEventListener("keydown", aoTeclar);
    regiao.remove();
    if (salvou) anunciar("Preferências de cookies salvas.");
    // o foco volta para quem abriu; no primeiro aviso, vai para o conteúdo da página
    (origem ?? document.getElementById("conteudo"))?.focus({ preventScroll: !origem });
  };
  const salvar = (e: boolean, m: boolean) => {
    salvarCookies(e, m);
    fechar(true);
  };
  const aoTeclar = (ev: KeyboardEvent) => {
    if (ev.key === "Escape" && forcar) fechar(false);
  };
  aceitar.addEventListener("click", () => salvar(true, true));
  necessarios.addEventListener("click", () => salvar(false, false));
  gerenciar.addEventListener("click", () => {
    if (opcoes.hidden) {
      opcoes.hidden = false;
      gerenciar.textContent = "Salvar escolhas";
      gerenciar.setAttribute("aria-expanded", "true");
      est.focus();
    } else {
      salvar(est.checked, mkt.checked);
    }
  });
  document.addEventListener("keydown", aoTeclar);
  // primeiro no DOM (depois do link "Pular para o conteúdo"), fixo no rodapé da tela
  const pular = document.querySelector(".pular");
  if (pular) pular.after(regiao);
  else document.body.prepend(regiao);
  if (forcar) titulo.focus();
}

export function iniciarCookies(): void {
  avisoCookies();
  const botao = document.getElementById("preferencias-cookies");
  botao?.addEventListener("click", () => avisoCookies(true, botao));
}
