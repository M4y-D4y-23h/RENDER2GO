// Painel do dono do serviço (no endereço secreto RENDER2GO_ADMIN_CAMINHO; ADR 0004, 0007 e 0009). Uma seção por
// vez, cada uma com a própria rolagem e o próprio endereço (#visao-geral, #revisao, #pods, #acessos...): o
// inicio.js já marcou a seção no <html> antes do primeiro quadro, e o CSS mostra só ela. Cada seção pede à API só
// o que mostra, quando abre; a aberta se atualiza a cada 30 s (com a aba visível), junto com os números do menu.
// O que o papel de quem entrou não abre (operador) fica escondido aqui e é recusado pelo servidor.
import { anunciar, api, avisoComRecarga, cliqueSimples, criar, descartarAntecipados, dialogo, el, marcarConectado, ocupado, repetir, textoErro } from "./comum";
import {
  CHAVE_PAINEL,
  CHAVE_PAPEL,
  type Controle,
  PAPEIS,
  type Sessao,
  contagem,
  controle,
  definirSessao,
  erroDeCarga,
  limparAvisoFlutuante,
  pode,
} from "./painel-base";
import { criarClientes, criarInteresses, criarSuporte } from "./painel-clientes";
import { iniciarEntrada } from "./painel-entrada";
import { criarGpus, criarPagamentos, criarPods } from "./painel-operacao";
import { criarHistorico, criarMontagens, criarRevisao } from "./painel-pedidos";
import { type Resumo, criarAcessos, criarAuditoria, criarSeguranca, criarVisaoGeral } from "./painel-seguranca";
import { type Tema, aplicarTema, temaAtual } from "./tema";

const SECOES = {
  "visao-geral": { titulo: "Visão geral", rota: "GET /api/resumo" },
  revisao: { titulo: "Revisão", rota: "GET /api/lotes" },
  historico: { titulo: "Histórico", rota: "GET /api/historico" },
  montagens: { titulo: "Montagens", rota: "GET /api/montagens" },
  clientes: { titulo: "Contas e saldo", rota: "GET /api/clientes" },
  suporte: { titulo: "Suporte", rota: "GET /api/suporte" },
  interesses: { titulo: "Interesse", rota: "GET /api/interesses" },
  pods: { titulo: "Pods", rota: "GET /api/pods" },
  gpus: { titulo: "GPUs", rota: "GET /api/geradores" },
  pagamentos: { titulo: "Pagamentos", rota: "GET /api/pagamentos" },
  acessos: { titulo: "Acessos", rota: "GET /api/acessos" },
  auditoria: { titulo: "Auditoria", rota: "GET /api/auditoria" },
  seguranca: { titulo: "Configuração", rota: "GET /api/seguranca" },
} as const;
type NomeSecao = keyof typeof SECOES;

function ehSecao(nome: string | null | undefined): nome is NomeSecao {
  return !!nome && Object.prototype.hasOwnProperty.call(SECOES, nome);
}

/** Cópia de um pedaço da página para a janela do celular, com os ids trocados (as ligações de rótulo continuam). */
function copiar(id: string, sufixo: string): HTMLElement {
  const e = el(id).cloneNode(true) as HTMLElement;
  const todos = [e, ...Array.from(e.querySelectorAll<HTMLElement>("*"))];
  for (const x of todos) {
    if (x.id) x.id += sufixo;
    for (const attr of ["aria-labelledby", "aria-describedby", "aria-controls", "for"]) {
      const v = x.getAttribute(attr);
      if (v) x.setAttribute(attr, v.split(/\s+/).map((i) => i + sufixo).join(" "));
    }
  }
  return e;
}

function campoTema(id: string): HTMLElement {
  const sel = criar("select", { id }, [
    criar("option", { value: "auto", textContent: "Automático (o do aparelho)" }),
    criar("option", { value: "claro", textContent: "Claro" }),
    criar("option", { value: "escuro", textContent: "Escuro" }),
  ]);
  ligarTema(sel);
  return criar("div", { className: "campo tema-painel" }, [criar("label", { htmlFor: id, textContent: "Tema neste navegador" }), sel]);
}

function ligarTema(sel: HTMLSelectElement): void {
  sel.value = temaAtual();
  sel.addEventListener("change", () => {
    const tema = sel.value as Tema;
    aplicarTema(tema);
    for (const outro of Array.from(document.querySelectorAll<HTMLSelectElement>(".tema-painel select"))) outro.value = tema;
    anunciar(tema === "auto" ? "Tema automático: igual ao do aparelho." : `Tema ${tema} aplicado.`);
  });
}

function iniciarPainel(): void {
  const raiz = document.documentElement;
  const botaoAtualizar = el<HTMLButtonElement>("atualizar");
  const botaoMenu = el<HTMLButtonElement>("abrir-menu");
  let atual: NomeSecao = ehSecao(raiz.dataset.secao) ? raiz.dataset.secao : "visao-geral";
  let naSessao = false;
  let pendentes = 0;
  let menu: HTMLDialogElement | null = null;
  const rolagem: Partial<Record<NomeSecao, number>> = {};

  const titulo = () => {
    document.title = `${pendentes ? `(${pendentes}) ` : ""}${SECOES[atual].titulo} · Painel · Render2Go`;
  };
  const contarPendentes = (n: number) => {
    pendentes = n;
    contagem(el("n-pendentes"), n, "aguardando");
    titulo();
  };

  // -- números do menu e visão geral: GET /api/resumo ----------------------------------------------------------
  const visao = criarVisaoGeral();
  let resumoEmCurso: Promise<void> | null = null;
  const atualizarResumo = (): Promise<void> => {
    resumoEmCurso ??= (async () => {
      try {
        const r = await api<Resumo>("api/resumo");
        contarPendentes(r.revisao);
        contagem(el("n-suporte"), r.suporte_24h, "nas últimas 24 horas");
        contagem(el("n-pods"), r.pods, r.pods === 1 ? "ligado" : "ligados");
        visao.desenhar(r);
      } finally {
        resumoEmCurso = null;
      }
    })();
    return resumoEmCurso;
  };

  const aoMudar = () => void atualizarResumo().catch(() => undefined);
  const controles: Record<NomeSecao, Controle> = {
    "visao-geral": controle({ regiao: el("visao-geral-dados"), carregar: atualizarResumo }),
    revisao: criarRevisao({ aoContar: contarPendentes, aoMudar }),
    historico: criarHistorico(),
    montagens: criarMontagens(),
    clientes: criarClientes(),
    suporte: criarSuporte(),
    interesses: criarInteresses(),
    pods: criarPods({ aoMudar }),
    gpus: criarGpus(),
    pagamentos: criarPagamentos(),
    acessos: criarAcessos(),
    auditoria: criarAuditoria(),
    seguranca: criarSeguranca(),
  };

  // -- seções por endereço ---------------------------------------------------------------------------------------
  const marcarMenu = () => {
    for (const a of Array.from(document.querySelectorAll<HTMLAnchorElement>(".menu-painel a[data-secao]"))) {
      if (a.dataset.secao === atual) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    }
  };

  const mostrar = (pedida: NomeSecao, foco: boolean) => {
    const secao: NomeSecao = pode(SECOES[pedida].rota) ? pedida : "visao-geral";
    if (secao !== atual) {
      rolagem[atual] = window.scrollY; // cada seção volta para onde estava
      atual = secao;
      raiz.dataset.secao = secao;
      window.scrollTo({ top: rolagem[secao] ?? 0, behavior: "instant" as ScrollBehavior });
    }
    if (location.hash !== `#${secao}`) history.replaceState(null, "", `#${secao}`);
    marcarMenu();
    titulo();
    void controles[secao].abrir();
    if (foco) el(`t-${secao}`).focus({ preventScroll: true });
  };

  // os links das seções (menu, cartões da visão geral): troca sem recarregar; Ctrl+clique abre noutra aba
  document.addEventListener("click", (ev) => {
    const a = (ev.target as Element | null)?.closest?.<HTMLAnchorElement>("a[data-secao]");
    if (!a || !naSessao || !ehSecao(a.dataset.secao) || !cliqueSimples(ev)) return;
    ev.preventDefault();
    const secao = a.dataset.secao;
    if (secao !== atual) history.pushState(null, "", `#${secao}`);
    menu?.close();
    mostrar(secao, true);
  });
  const aoMudarEndereco = () => {
    const h = location.hash.slice(1);
    if (h.startsWith("convite=")) {
      location.reload(); // um convite colado no endereço: a entrada do primeiro acesso
      return;
    }
    if (!naSessao) return;
    if (ehSecao(h)) {
      if (h !== atual) mostrar(h, true);
    } else history.replaceState(null, "", `#${atual}`); // "#conteudo" (pular para o conteúdo) e afins
  };
  window.addEventListener("popstate", aoMudarEndereco);
  window.addEventListener("hashchange", aoMudarEndereco);

  // -- quem entrou e o que o papel abre ---------------------------------------------------------------------------
  const aplicarPermissoes = (s: Sessao) => {
    definirSessao(s);
    raiz.dataset.papel = s.papel ?? "dono";
    try {
      localStorage.setItem(CHAVE_PAPEL, s.papel ?? "dono");
    } catch {
      /* sem armazenamento: o papel vale só nesta visita */
    }
    for (const e of Array.from(document.querySelectorAll<HTMLElement>("[data-rota]"))) e.hidden = !pode(e.dataset.rota ?? "");
    for (const g of Array.from(document.querySelectorAll<HTMLElement>(".menu-painel .grupo"))) g.hidden = !g.querySelector("li:not([hidden])");
    const nome = s.emergencia ? "Chave do servidor" : (s.admin ?? "");
    const papel = PAPEIS[s.papel ?? "dono"];
    el("quem-nome").textContent = nome;
    el("quem-papel").textContent = s.emergencia ? `${papel} · emergência` : papel;
    el("quem").title = s.emergencia ? "Acesso de emergência (chave do servidor): use só quando ninguém conseguir entrar" : `Conectado como ${nome} (${papel.toLowerCase()})`;
    const inicial = el("avatar").querySelector<HTMLElement>(".avatar-inicial");
    if (inicial) inicial.textContent = s.emergencia ? "!" : (Array.from(nome).find((ch) => /\p{L}|\p{N}/u.test(ch)) ?? "?").toLocaleUpperCase("pt-BR");
  };

  // uma rodada: os números do menu (e a visão geral) e a seção aberta; o aviso de erro sai quando tudo volta
  const resumoOk = () =>
    atualizarResumo().then(
      () => true,
      (e) => {
        erroDeCarga(e, null, () => undefined); // sessão vencida volta para a entrada; o resto flutua no pé
        return false;
      },
    );
  const rodada = async () => {
    const oks = await Promise.all([resumoOk(), atual === "visao-geral" ? true : controles[atual].atualizar()]);
    if (oks.every(Boolean)) limparAvisoFlutuante();
    return oks.every(Boolean);
  };
  const atualizacao = repetir(async () => {
    if (!naSessao) return false;
    await rodada();
    return naSessao;
  }, 30000);

  const entrar = (s: Sessao, foco: boolean) => {
    aplicarPermissoes(s);
    naSessao = true;
    raiz.classList.add("com-sessao");
    marcarConectado(CHAVE_PAINEL, true);
    const pedida = ehSecao(location.hash.slice(1)) ? (location.hash.slice(1) as NomeSecao) : atual;
    mostrar(pedida, foco);
    atualizacao.agora(); // os números do menu (a seção já está pedindo os dados dela)
  };

  const semSessao = () => {
    naSessao = false;
    raiz.classList.remove("com-sessao");
    delete raiz.dataset.papel;
    marcarConectado(CHAVE_PAINEL, false);
    try {
      localStorage.removeItem(CHAVE_PAPEL);
    } catch {
      /* sem armazenamento */
    }
    descartarAntecipados(); // as respostas pedidas no <head> eram da sessão que acabou
    entrada.semSessao();
  };

  // depois de qualquer modo de entrada: a sessão já está no cookie; o papel e as rotas vêm do /api/sessao
  const depoisDaEntrada = async () => {
    descartarAntecipados();
    const s = await api<Sessao>("api/sessao");
    if (!s.admin) throw new Error("a sessão não abriu; tente de novo");
    entrar(s, true);
    anunciar(`Você entrou no painel como ${s.emergencia ? "chave do servidor" : s.admin}.`);
  };
  const entrada = iniciarEntrada(depoisDaEntrada);

  // -- topo: atualizar, sair e o menu no celular ----------------------------------------------------------------
  botaoAtualizar.addEventListener("click", async () => {
    ocupado(botaoAtualizar, true);
    const ok = await rodada();
    ocupado(botaoAtualizar, false);
    if (ok) anunciar(`${SECOES[atual].titulo}: atualizado.`);
  });
  el("sair").addEventListener("click", async () => {
    await api("api/sair", { method: "POST" }).catch(() => undefined);
    marcarConectado(CHAVE_PAINEL, false);
    try {
      localStorage.removeItem(CHAVE_PAPEL);
    } catch {
      /* sem armazenamento */
    }
    location.reload();
  });
  botaoMenu.addEventListener("click", () => {
    const { d, corpo } = dialogo("Seções do painel", "menu-celular");
    corpo.append(copiar("quem", "-celular"), copiar("menu-painel", "-celular"), campoTema("tema-painel-celular"));
    menu = d;
    botaoMenu.setAttribute("aria-expanded", "true");
    d.addEventListener("close", () => {
      menu = null;
      botaoMenu.setAttribute("aria-expanded", "false");
      d.remove();
      // escolheu uma seção: o foco já foi para o título dela; fechou sem escolher: volta para o botão
      if (!d.contains(document.activeElement) && document.activeElement !== document.body) return;
      botaoMenu.focus();
    });
    d.showModal();
    (corpo.querySelector<HTMLElement>("a[aria-current]") ?? corpo.querySelector<HTMLElement>("a"))?.focus();
  });
  matchMedia("(min-width: 64em)").addEventListener("change", (ev) => ev.matches && menu?.close());
  ligarTema(el<HTMLSelectElement>("tema-painel"));

  // -- ao abrir: o convite (#convite=…) já está na tela; senão, a sessão do cookie decide -------------------------
  if (raiz.dataset.convite !== undefined) return;
  api<Sessao>("api/sessao")
    .then((s) => (s.admin ? entrar(s, false) : semSessao()))
    .catch((e) => {
      semSessao();
      avisoComRecarga(el("msg-entrada"), `Não foi possível falar com o servidor: ${textoErro(e)}`);
    });
}

document.addEventListener("DOMContentLoaded", iniciarPainel);
