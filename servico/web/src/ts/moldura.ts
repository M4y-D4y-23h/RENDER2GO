// Moldura da área do cliente: menu lateral (computador) ou numa janela de baixo (celular), uma tela por endereço
// (/, /modelos, /extratos, /logs, /perfil, /configuracoes, /projetos, /mix) e a troca de tela sem recarregar a
// página. O inicio.js já marcou a tela (data-vista) antes do primeiro quadro: o CSS mostra só ela e destaca o
// item do menu. Aqui, a troca: o endereço muda (voltar e avançar do navegador funcionam), o título da aba muda,
// a tela nova carrega o que precisa e o foco vai para o título dela.
import { cliqueSimples, dialogo, el } from "./comum";
import { aoMudarConta, desenharAvatar, nomeExibido, resumoConta, resumoPlano } from "./conta";

export type NomeTela = "pedidos" | "modelos" | "extratos" | "logs" | "perfil" | "configuracoes" | "projetos" | "mix";

export const TELAS: Record<NomeTela, { caminho: string; titulo: string }> = {
  pedidos: { caminho: "/", titulo: "Pedidos" },
  modelos: { caminho: "/modelos", titulo: "Modelos de IA" },
  extratos: { caminho: "/extratos", titulo: "Extratos" },
  logs: { caminho: "/logs", titulo: "Logs" },
  perfil: { caminho: "/perfil", titulo: "Perfil" },
  configuracoes: { caminho: "/configuracoes", titulo: "Configurações" },
  projetos: { caminho: "/projetos", titulo: "Projetos" },
  mix: { caminho: "/mix", titulo: "Mix" },
};

export function ehTela(nome: string | undefined | null): nome is NomeTela {
  return !!nome && Object.prototype.hasOwnProperty.call(TELAS, nome);
}

export function telaDoEndereco(caminho = location.pathname): NomeTela {
  const nome = caminho.replace(/^\/+|\/+$/g, "") || "pedidos";
  return ehTela(nome) ? nome : "pedidos";
}

export type Moldura = {
  atual: () => NomeTela;
  ir: (tela: NomeTela, opcoes?: { historico?: boolean; foco?: boolean }) => void;
  /** Entrou na conta (ou a página abriu com a sessão): título da aba, menu e a tela do endereço. */
  entrar: (foco: boolean) => void;
};

export function iniciarMoldura(abrir: (tela: NomeTela) => void): Moldura {
  const raiz = document.documentElement;
  let atual: NomeTela = ehTela(raiz.dataset.vista) ? raiz.dataset.vista : telaDoEndereco();
  let naConta = false;
  let menu: HTMLDialogElement | null = null;
  const botaoMenu = document.getElementById("abrir-menu") as HTMLButtonElement | null;

  const marcarMenu = () => {
    for (const a of Array.from(document.querySelectorAll<HTMLAnchorElement>(".menu-conta a[data-vista]"))) {
      if (a.dataset.vista === atual) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    }
    // "Pedidos" no topo das outras páginas fica marcado só na tela de pedidos
    for (const a of Array.from(document.querySelectorAll<HTMLAnchorElement>('.navegacao a[data-nav="cliente"]'))) {
      if (atual === "pedidos") a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    }
  };

  const focarTitulo = () => {
    const titulo = document.querySelector<HTMLElement>(`#vista-${atual} h1`);
    titulo?.focus({ preventScroll: true });
  };

  const mostrar = (tela: NomeTela, foco: boolean) => {
    const mudou = tela !== atual;
    atual = tela;
    raiz.dataset.vista = tela;
    if (naConta) document.title = `${TELAS[tela].titulo} · Render2Go`;
    marcarMenu();
    if (mudou) window.scrollTo({ top: 0, behavior: "instant" as ScrollBehavior });
    abrir(tela);
    if (foco) focarTitulo();
  };

  const ir: Moldura["ir"] = (tela, opcoes = {}) => {
    const { historico = true, foco = true } = opcoes;
    if (historico && location.pathname !== TELAS[tela].caminho) history.pushState({ tela }, "", TELAS[tela].caminho);
    menu?.close();
    mostrar(tela, foco);
  };

  // links do menu (e outros com data-vista): a troca de tela sem recarregar; Ctrl+clique abre noutra aba
  document.addEventListener("click", (ev) => {
    const a = (ev.target as Element | null)?.closest?.<HTMLAnchorElement>("a[data-vista]");
    if (!a || !ehTela(a.dataset.vista) || !raiz.classList.contains("com-sessao") || !cliqueSimples(ev)) return;
    ev.preventDefault();
    ir(a.dataset.vista);
  });
  window.addEventListener("popstate", () => {
    if (naConta) mostrar(telaDoEndereco(), true);
  });

  // celular: o menu numa janela de baixo, com o foco preso nela (showModal); Esc ou o fundo fecham
  botaoMenu?.addEventListener("click", () => {
    const { d, corpo } = dialogo("Sua conta", "menu-celular");
    const copia = (id: string) => {
      const e = el(id).cloneNode(true) as HTMLElement;
      for (const x of [e, ...Array.from(e.querySelectorAll<HTMLElement>("[id]"))]) x.removeAttribute("id");
      return e;
    };
    corpo.append(copia("quem"), copia("menu-conta"));
    menu = d;
    botaoMenu.setAttribute("aria-expanded", "true");
    d.addEventListener("close", () => {
      menu = null;
      botaoMenu.setAttribute("aria-expanded", "false");
      d.remove();
      // escolheu uma tela: o foco já foi para o título dela; fechou sem escolher: volta para o botão
      if (!d.contains(document.activeElement) && document.activeElement !== document.body) return;
      botaoMenu.focus();
    });
    d.showModal();
    (corpo.querySelector<HTMLElement>("a[aria-current]") ?? corpo.querySelector<HTMLElement>("a"))?.focus();
  });
  matchMedia("(min-width: 64em)").addEventListener("change", (ev) => ev.matches && menu?.close());

  // quem entrou: foto ou inicial, nome (ou e-mail) e plano, no alto do menu (altura fixa: nada se mexe)
  aoMudarConta((c) => {
    el("quem-nome").textContent = nomeExibido(c);
    const resumo = el("conta-resumo");
    resumo.textContent = resumoPlano(c);
    el("quem").title = resumoConta(c);
    desenharAvatar(el("avatar"), c);
  });

  return {
    atual: () => atual,
    ir,
    entrar: (foco) => {
      naConta = true;
      history.replaceState({ tela: atual }, "", location.pathname + location.search + location.hash);
      mostrar(atual, foco);
    },
  };
}
