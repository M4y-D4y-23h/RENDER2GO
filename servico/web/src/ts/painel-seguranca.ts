// Painel: a visão geral (o que pede atenção agora e os números de hoje), os acessos por pessoa (convites de
// primeiro acesso, papéis e quem pode entrar; ADR 0009), a auditoria (quem fez o quê, em quem, quando e de onde,
// com o antes e o depois, filtros, página seguinte e planilha) e a configuração do servidor.
import { type NomeIcone, anunciar, api, aviso, confirmar, copiarTexto, criar, dialogo, el, icone, marcarErro, ocupado, plural, quando, reais, tempo } from "./comum";
import {
  type Controle,
  PAPEIS,
  PAPEIS_DESCRICAO,
  type Papel,
  controle,
  desde,
  dinheiro,
  erroDeAcao,
  erroDeCarga,
  navegadorCurto,
  pode,
  semLinhas,
  sessaoAtual,
  tabela,
  trocar,
} from "./painel-base";

export type Resumo = {
  revisao: number;
  revisao_mais_antiga: number | null;
  em_andamento: number;
  videos_na_fila: number;
  videos_gerando: number;
  pods: number;
  videos_hoje: number;
  pedidos_hoje: number;
  cobrado_hoje?: number;
  devolvido_hoje?: number;
  recargas_hoje?: number;
  contas_novas_24h: number;
  interesses_24h: number;
  suporte_24h: number;
  pix_pendentes?: number;
  orquestrador_visto_s: number | null;
  pausado: boolean;
  avisos?: string[];
};

// -- visão geral ----------------------------------------------------------------------------------------------

type Cartao = { secao: string; rota: string; icone: NomeIcone; rotulo: string; valor: string; detalhe: string; estado?: "alerta" | "erro" };

function cartaoResumo(c: Cartao): HTMLElement {
  // o cartão inteiro leva à seção (o painel troca de seção sem recarregar)
  return criar("a", { className: `cartao-resumo${c.estado ? ` ${c.estado}` : ""}`, href: `#${c.secao}`, dados: { secao: c.secao, foco: `resumo:${c.secao}` } }, [
    criar("span", { className: "rotulo-resumo" }, [icone(c.icone), c.rotulo]),
    criar("span", { className: "valor-resumo", textContent: c.valor }),
    criar("span", { className: "detalhe-resumo", textContent: c.detalhe }),
  ]);
}

export function criarVisaoGeral(): { desenhar: (r: Resumo) => void } {
  const regiao = el("visao-geral-dados");
  let partes: { agora: HTMLElement; hoje: HTMLElement; avisos: HTMLElement; atualizado: HTMLElement } | null = null;
  const desenhar = (r: Resumo) => {
    if (!partes) {
      const agora = criar("div", { className: "cartoes-resumo", id: "resumo-agora" });
      const hoje = criar("dl", { className: "numeros", id: "resumo-hoje", attrs: { "aria-labelledby": "t-hoje" } });
      const avisos = criar("div", { className: "subsecao-dados", id: "resumo-avisos" });
      const atualizado = criar("p", { className: "fraco atualizado", id: "atualizado" });
      regiao.replaceChildren(criar("h2", { id: "t-agora", className: "visualmente-oculto", textContent: "Agora" }), agora, criar("h2", { id: "t-hoje", textContent: "Hoje" }), hoje, avisos, atualizado);
      partes = { agora, hoje, avisos, atualizado };
    }
    const visto = r.orquestrador_visto_s;
    const semSinal = visto === null || visto >= 120;
    const cartoes: Cartao[] = [
      {
        secao: "revisao",
        rota: "GET /api/lotes",
        icone: "escudo",
        rotulo: "Aguardando revisão",
        valor: r.revisao.toLocaleString("pt-BR"),
        detalhe: r.revisao ? `O mais antigo chegou ${desde(r.revisao_mais_antiga ?? Date.now() / 1000)}.` : "Nada esperando: tudo revisado.",
        estado: r.revisao ? "alerta" : undefined,
      },
      {
        secao: "pods",
        rota: "GET /api/pods",
        icone: "cubo",
        rotulo: "Pods ligados",
        valor: r.pods.toLocaleString("pt-BR"),
        detalhe: r.pausado
          ? "Parada de emergência: nenhum pod novo sobe."
          : `${plural(r.em_andamento, "pedido", "pedidos")} em andamento · ${plural(r.videos_na_fila, "vídeo", "vídeos")} na fila · ${r.videos_gerando.toLocaleString("pt-BR")} gerando.${
              semSinal ? (visto === null ? " O orquestrador ainda não deu sinal." : ` Orquestrador sem sinal há ${Math.round(visto / 60)} min.`) : ""
            }`,
        estado: r.pausado ? "erro" : semSinal ? "alerta" : undefined,
      },
      {
        secao: "suporte",
        rota: "GET /api/suporte",
        icone: "mensagem",
        rotulo: "Suporte",
        valor: r.suporte_24h.toLocaleString("pt-BR"),
        detalhe: `${r.suporte_24h === 1 ? "mensagem" : "mensagens"} nas últimas 24 h.`,
      },
    ];
    if (r.pix_pendentes !== undefined)
      cartoes.push({
        secao: "pagamentos",
        rota: "GET /api/pagamentos",
        icone: "recibo",
        rotulo: "Pix pendentes",
        valor: r.pix_pendentes.toLocaleString("pt-BR"),
        detalhe: r.pix_pendentes ? "Recargas esperando o pagamento." : "Nenhuma recarga esperando.",
      });
    cartoes.push({
      secao: "interesses",
      rota: "GET /api/interesses",
      icone: "brilho",
      rotulo: "Interesse",
      valor: r.interesses_24h.toLocaleString("pt-BR"),
      detalhe: `${r.interesses_24h === 1 ? "clique" : "cliques"} em “Tenho interesse” nas últimas 24 h.`,
    });
    trocar(partes.agora, cartoes.filter((c) => pode(c.rota)).map(cartaoResumo), JSON.stringify(cartoes));
    const numero = (rotulo: string, valor: string) => criar("div", {}, [criar("dt", { textContent: rotulo }), criar("dd", { textContent: valor })]);
    const hoje = [
      numero("Pedidos", r.pedidos_hoje.toLocaleString("pt-BR")),
      numero("Vídeos entregues", r.videos_hoje.toLocaleString("pt-BR")),
      numero("Contas novas (24 h)", r.contas_novas_24h.toLocaleString("pt-BR")),
    ];
    if (r.cobrado_hoje !== undefined) hoje.push(numero("Cobrado", dinheiro(r.cobrado_hoje)), numero("Devolvido", dinheiro(r.devolvido_hoje)), numero("Recargas", dinheiro(r.recargas_hoje)));
    trocar(partes.hoje, hoje, JSON.stringify([r.pedidos_hoje, r.videos_hoje, r.contas_novas_24h, r.cobrado_hoje, r.devolvido_hoje, r.recargas_hoje]));
    const avisos = r.avisos ?? [];
    trocar(
      partes.avisos,
      avisos.length
        ? [
            criar("h2", { textContent: "O que falta configurar" }),
            criar(
              "ul",
              { className: "lista-checagem cartao" },
              avisos.map((t) => criar("li", { className: "falta" }, [icone("alerta"), criar("span", { textContent: t })])),
            ),
            pode("GET /api/seguranca") ? criar("p", {}, [criar("a", { href: "#seguranca", dados: { secao: "seguranca" }, textContent: "Ver a configuração do servidor" })]) : null,
          ]
        : [],
      JSON.stringify(avisos),
    );
    partes.atualizado.textContent = `Atualizado às ${new Date().toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })} (a cada 30 s, com a aba aberta).`;
  };
  return { desenhar };
}

// -- acessos (ADR 0009) ---------------------------------------------------------------------------------------

type Pessoa = {
  id: string;
  usuario: string;
  papel: Papel;
  ativo: number | boolean;
  criado: number;
  criado_por: string | null;
  desativado_em: number | null;
  ultimo_acesso: number | null;
  sessoes: number;
};
type ConvitePendente = { id: string; usuario: string; papel: Papel; criado: number; criado_por: string | null; expira: number; totp_confirmado_em: number | null };
type Acessos = { pessoas: Pessoa[]; convites: ConvitePendente[]; chave_emergencia: boolean; papeis: Record<string, string> };
type ConviteCriado = { convite: string; usuario: string; papel: Papel; refaz: boolean; expira: number; endereco: string };

const USUARIO = /^[a-z][a-z0-9._-]{2,31}$/;

/** O endereço do convite, numa janela (nada é inserido na página): copiar e mandar por um canal seguro. */
function janelaConvite(c: ConviteCriado, origem: HTMLElement | null): void {
  const { d, corpo } = dialogo("Convite criado", "convite-criado");
  const endereco = `${location.origin}${c.endereco}`;
  const copiar = criar("button", { type: "button", className: "botao primario", id: "copiar-convite" }, [icone("copiar"), "Copiar o endereço"]);
  copiar.addEventListener("click", async () => {
    if (await copiarTexto(endereco)) {
      copiar.replaceChildren(icone("ok"), "Copiado");
      anunciar("Endereço do convite copiado.");
    } else {
      const sel = getSelection();
      sel?.selectAllChildren(codigo);
      anunciar("Não foi possível copiar: o endereço ficou selecionado.");
    }
  });
  const codigo = criar("code", { id: "endereco-convite", textContent: endereco });
  const fechar = criar("button", { type: "button", className: "botao", textContent: "Fechar" });
  fechar.addEventListener("click", () => d.close());
  const partes: (Node | null)[] = [
    criar("p", {}, [
      "Mande este endereço para ",
      criar("strong", { textContent: c.usuario }),
      ` (${PAPEIS[c.papel].toLowerCase()}) por um canal seguro. Ele vale até ${quando(c.expira)} e uma vez só.`,
    ]),
    criar("div", { className: "codigo-gerado" }, [codigo]),
    criar("p", { className: "fraco", textContent: "No primeiro acesso, a pessoa lê o QR Code no aplicativo autenticador dela, confirma um código e só então cria a senha. O endereço não aparece de novo: se perder, crie outro convite." }),
    c.refaz
      ? (() => {
          const caixa = criar("div");
          aviso(caixa, `${c.usuario} já tinha acesso: quando o convite for usado, a senha e o aplicativo autenticador antigos deixam de valer e as sessões abertas caem.`, "alerta");
          return caixa;
        })()
      : null,
    criar("div", { className: "dialogo-acoes" }, [fechar, copiar]),
  ];
  corpo.append(...partes.filter((x): x is Node => !!x));
  d.addEventListener("close", () => {
    d.remove();
    origem?.focus();
  });
  d.showModal();
  copiar.focus();
}

export function criarAcessos(): Controle {
  const regiao = el("acessos-dados");
  const form = el<HTMLFormElement>("form-convite");
  const usuario = el<HTMLInputElement>("convite-novo-usuario");
  const papel = el<HTMLSelectElement>("convite-novo-papel");
  const msgForm = criar("div", { className: "aviso", hidden: true, attrs: { role: "alert" } });
  form.append(msgForm);
  let partes: { pessoas: HTMLElement; convites: HTMLElement; chave: HTMLElement; msg: HTMLElement } | null = null;

  const convidar = async (nome: string, p: Papel, botao: HTMLButtonElement, msg: HTMLElement): Promise<boolean> => {
    if (p === "dono") {
      const ok = await confirmar({
        titulo: `Convidar ${nome} como dono?`,
        texto: "O papel de dono mexe em tudo: saldo, pods e parada de emergência, pagamentos, configuração e acessos.",
        confirmar: "Convidar como dono",
      });
      if (!ok) return false;
    }
    // o foco volta para quem pediu o convite (o botão fica desativado enquanto o servidor responde)
    const origem = document.activeElement instanceof HTMLElement && document.activeElement !== document.body ? document.activeElement : botao;
    ocupado(botao, true);
    try {
      const c = await api<ConviteCriado>("api/acessos/convites", { method: "POST", body: JSON.stringify({ usuario: nome, papel: p }) });
      janelaConvite(c, origem);
      void ctl.atualizar();
      return true;
    } catch (e) {
      erroDeAcao(e, msg);
      return false;
    } finally {
      ocupado(botao, false);
    }
  };

  papel.addEventListener("change", () => (el("convite-novo-papel-dica").textContent = PAPEIS_DESCRICAO[papel.value as Papel]));
  usuario.addEventListener("input", () => marcarErro(usuario, false));
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    aviso(msgForm, "");
    const nome = usuario.value.trim().toLowerCase();
    if (!USUARIO.test(nome) || nome.startsWith("admin-")) {
      marcarErro(usuario, true);
      aviso(msgForm, "Use de 3 a 32 letras minúsculas, números, ponto, hífen ou sublinhado, começando por uma letra (ex.: maria.silva).", "erro");
      usuario.focus();
      return;
    }
    if (await convidar(nome, papel.value as Papel, el<HTMLButtonElement>("convite-novo-enviar"), msgForm)) form.reset();
    el("convite-novo-papel-dica").textContent = PAPEIS_DESCRICAO[papel.value as Papel];
  });

  const carregar = async () => {
    const r = await api<Acessos>("api/acessos");
    if (!partes) {
      const pessoas = criar("div", { className: "tabela", id: "acessos-pessoas", attrs: { role: "region", "aria-labelledby": "t-pessoas", tabindex: "0" } });
      const convites = criar("div", { className: "tabela", id: "acessos-convites", attrs: { role: "region", "aria-labelledby": "t-convites", tabindex: "0" } });
      const chave = criar("p", { className: "fraco", id: "acessos-chave" });
      const msg = criar("div", { className: "aviso", hidden: true, attrs: { role: "alert" } });
      regiao.replaceChildren(
        msg,
        criar("h2", { id: "t-pessoas", textContent: "Pessoas com acesso" }),
        pessoas,
        criar("h2", { id: "t-convites", textContent: "Convites que ainda valem" }),
        convites,
        chave,
      );
      partes = { pessoas, convites, chave, msg };
    }
    const eu = sessaoAtual().admin;
    const msg = partes.msg;
    const linhasPessoas = r.pessoas.map((a) => {
      const voce = a.usuario === eu;
      const acoes = criar("div", { className: "acoes-linha" });
      if (!voce) {
        if (a.ativo) {
          const sel = criar("select", { dados: { foco: `papel:${a.id}` } }, (["operador", "dono"] as Papel[]).map((p) => criar("option", { value: p, textContent: PAPEIS[p], selected: p === a.papel })));
          sel.setAttribute("aria-label", `Papel de ${a.usuario}`);
          const mudar = criar("button", { type: "button", className: "botao pequeno", dados: { foco: `mudar:${a.id}` } }, ["Mudar o papel"]);
          mudar.setAttribute("aria-label", `Mudar o papel de ${a.usuario}`);
          mudar.addEventListener("click", async () => {
            const novo = sel.value as Papel;
            if (novo === a.papel) {
              aviso(msg, `Escolha um papel diferente do atual (${PAPEIS[a.papel].toLowerCase()}).`, "erro");
              sel.focus();
              return;
            }
            const ok = await confirmar({ titulo: "Mudar o papel?", texto: `${a.usuario}: de ${PAPEIS[a.papel].toLowerCase()} para ${PAPEIS[novo].toLowerCase()}. Vale a partir do próximo clique da pessoa no painel.`, confirmar: "Mudar" });
            if (!ok) return;
            ocupado(mudar, true);
            try {
              await api(`api/acessos/${encodeURIComponent(a.id)}/papel`, { method: "POST", body: JSON.stringify({ papel: novo }) });
              anunciar(`Papel de ${a.usuario} mudado para ${PAPEIS[novo].toLowerCase()}.`);
              void ctl.atualizar();
            } catch (e) {
              ocupado(mudar, false);
              erroDeAcao(e, msg);
            }
          });
          const tirar = criar("button", { type: "button", className: "botao pequeno perigo", dados: { foco: `tirar:${a.id}` } }, [icone("bloquear"), "Tirar o acesso"]);
          tirar.setAttribute("aria-label", `Tirar o acesso de ${a.usuario}`);
          tirar.addEventListener("click", async () => {
            const ok = await confirmar({
              titulo: `Tirar o acesso de ${a.usuario}?`,
              texto: "A pessoa deixa de entrar na hora (as sessões abertas caem). Para voltar, só com um convite novo.",
              confirmar: "Tirar o acesso",
              perigo: true,
            });
            if (!ok) return;
            ocupado(tirar, true);
            try {
              await api(`api/acessos/${encodeURIComponent(a.id)}/desativar`, { method: "POST" });
              anunciar(`Acesso de ${a.usuario} retirado.`);
              void ctl.atualizar();
            } catch (e) {
              ocupado(tirar, false);
              erroDeAcao(e, msg);
            }
          });
          acoes.append(sel, mudar, tirar);
        }
        const refazer = criar("button", { type: "button", className: "botao pequeno", dados: { foco: `refazer:${a.id}` } }, [icone("chave"), a.ativo ? "Convite novo" : "Convidar de novo"]);
        refazer.setAttribute("aria-label", `${a.ativo ? "Convite novo para" : "Convidar de novo"} ${a.usuario}`);
        refazer.addEventListener("click", async () => {
          if (a.ativo) {
            const ok = await confirmar({
              titulo: `Convite novo para ${a.usuario}?`,
              texto: "Para quem perdeu o celular ou esqueceu a senha. Quando o convite novo for usado, a senha e o aplicativo antigos deixam de valer e as sessões abertas caem.",
              confirmar: "Criar o convite",
            });
            if (!ok) return;
          }
          await convidar(a.usuario, a.papel, refazer, msg);
        });
        acoes.append(refazer);
      }
      return {
        classe: a.ativo ? "" : "apagada",
        celulas: [
          voce ? criar("span", {}, [a.usuario, criar("span", { className: "fraco", textContent: " (você)" })]) : a.usuario,
          PAPEIS[a.papel] ?? a.papel,
          a.ativo ? "com acesso" : `sem acesso desde ${a.desativado_em ? quando(a.desativado_em) : "?"}`,
          a.ultimo_acesso ? tempo(a.ultimo_acesso) : "nunca entrou",
          a.sessoes,
          criar("span", {}, [tempo(a.criado), ` · ${a.criado_por ?? "?"}`]),
          voce ? "—" : acoes,
        ],
      };
    });
    trocar(
      partes.pessoas,
      [
        r.pessoas.length
          ? tabela(
              [["Nome de acesso", false], ["Papel", false], ["Situação", false], ["Último acesso", false], ["Sessões abertas", true], ["Criado", false], ["Ações", false]],
              linhasPessoas,
              "Pessoas com acesso ao painel",
            )
          : semLinhas("Ninguém tem acesso por pessoa ainda: convide alguém acima (o primeiro convite também sai pelo terminal do servidor)."),
      ],
      JSON.stringify([r.pessoas, eu]),
    );
    trocar(
      partes.convites,
      [
        r.convites.length
          ? tabela(
              [["Nome de acesso", false], ["Papel", false], ["Criado", false], ["Vale até", false], ["Situação", false], ["Ação", false]],
              r.convites.map((cv) => {
                const cancelar = criar("button", { type: "button", className: "botao pequeno perigo", dados: { foco: `cancelar:${cv.id}` } }, [icone("fechar"), "Cancelar"]);
                cancelar.setAttribute("aria-label", `Cancelar o convite de ${cv.usuario}`);
                cancelar.addEventListener("click", async () => {
                  const ok = await confirmar({ titulo: "Cancelar o convite?", texto: `O endereço enviado para ${cv.usuario} deixa de valer na hora.`, confirmar: "Cancelar o convite", perigo: true });
                  if (!ok) return;
                  ocupado(cancelar, true);
                  try {
                    await api(`api/acessos/convites/${encodeURIComponent(cv.id)}/cancelar`, { method: "POST" });
                    anunciar(`Convite de ${cv.usuario} cancelado.`);
                    void ctl.atualizar();
                  } catch (e) {
                    ocupado(cancelar, false);
                    erroDeAcao(e, msg);
                  }
                });
                return {
                  celulas: [
                    cv.usuario,
                    PAPEIS[cv.papel] ?? cv.papel,
                    criar("span", {}, [tempo(cv.criado), ` · ${cv.criado_por ?? "?"}`]),
                    tempo(cv.expira),
                    cv.totp_confirmado_em ? "código confirmado; falta a senha" : "esperando a pessoa",
                    cancelar,
                  ],
                };
              }),
              "Convites que ainda valem",
            )
          : semLinhas("Nenhum convite esperando."),
      ],
      JSON.stringify(r.convites),
    );
    partes.chave.textContent = r.chave_emergencia
      ? "A chave de emergência do servidor (RENDER2GO_ADMIN_TOKENS) está ligada: ela entra como dono e fica registrada na auditoria. Use só quando ninguém conseguir entrar."
      : "Sem chave de emergência (RENDER2GO_ADMIN_TOKENS vazio): só os acessos por pessoa entram; um convite novo também sai pelo terminal do servidor.";
  };
  const ctl = controle({ regiao, carregar });
  return ctl;
}

// -- auditoria ------------------------------------------------------------------------------------------------

type Registro = { quando: number; admin: string; acao: string; alvo: string | null; detalhe: Record<string, unknown> | null; ip: string | null; agente?: string | null };
type Auditoria = { itens: Registro[]; proximo: number | null; pessoas: string[]; acoes: string[] };

export const ACOES: Record<string, string> = {
  entrada: "Entrou no painel",
  entrada_emergencia: "Entrou com a chave do servidor",
  entrada_recusada: "Entrada recusada",
  convite_criado: "Criou um convite",
  convite_aberto: "Abriu o convite",
  convite_usado: "Usou o convite (criou a senha)",
  convite_cancelado: "Cancelou um convite",
  acesso_desativado: "Tirou um acesso",
  papel: "Mudou um papel",
  aprovar: "Aprovou um pedido",
  recusar: "Recusou um pedido",
  criar_conta: "Criou uma conta",
  saldo: "Lançou no saldo",
  plano: "Mudou o plano",
  email: "Ligou um e-mail",
  desativar: "Desativou uma conta",
  reativar: "Reativou uma conta",
  mfa_desligado: "Desligou a verificação em duas etapas",
  desligar_pod: "Desligou um pod",
  emergencia: "Parada de emergência",
  retomar: "Retomou os pods",
  conferir_pagamento: "Conferiu uma recarga",
  auditoria_exportada: "Baixou a auditoria",
};
const ROTULOS: Record<string, string> = {
  plano: "plano",
  saldo: "saldo",
  ativo: "com acesso",
  papel: "papel",
  email: "e-mail",
  pausado: "pausado",
  mfa: "duas etapas",
  motivo: "motivo",
  valor: "valor",
  descricao: "descrição",
  ref: "pagamento",
  modo: "modo",
  usuario: "nome de acesso",
  status: "situação",
  convite: "convite",
  novo: "acesso novo",
  totp: "código do autenticador",
};

function valorAuditoria(chave: string, v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "boolean") return v ? "sim" : "não";
  if ((chave === "saldo" || chave === "valor") && typeof v === "number") return reais(v);
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

/** "plano: teste → pago · motivo: …": o antes e o depois de cada campo e o resto do detalhe. */
export function mudanca(d: Record<string, unknown> | null): string {
  if (!d) return "—";
  const antes = (d.antes && typeof d.antes === "object" ? d.antes : {}) as Record<string, unknown>;
  const depois = (d.depois && typeof d.depois === "object" ? d.depois : {}) as Record<string, unknown>;
  const campos = [...new Set([...Object.keys(antes), ...Object.keys(depois)])];
  const partes = campos.map((k) => `${ROTULOS[k] ?? k}: ${k in antes ? valorAuditoria(k, antes[k]) : "—"} → ${valorAuditoria(k, depois[k])}`);
  for (const [k, v] of Object.entries(d)) {
    if (k === "antes" || k === "depois" || v == null || v === "") continue;
    partes.push(`${ROTULOS[k] ?? k}: ${valorAuditoria(k, v)}`);
  }
  return partes.join(" · ") || "—";
}

function quemFez(admin: string): string {
  if (admin === "?") return "desconhecido";
  if (admin.startsWith("admin-")) return `chave do servidor (${admin})`;
  return admin;
}

/** "2026-10-10" no fuso deste aparelho → epoch em segundos (início do dia; `fim`: início do dia seguinte). */
function dia(valor: string, fim = false): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(valor);
  if (!m) return null;
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]) + (fim ? 1 : 0)).getTime() / 1000;
}

export function criarAuditoria(): Controle {
  const regiao = el("auditoria-dados");
  const form = el<HTMLFormElement>("form-auditoria");
  const pessoa = el<HTMLSelectElement>("aud-pessoa");
  const acao = el<HTMLSelectElement>("aud-acao");
  const alvo = el<HTMLInputElement>("aud-alvo");
  const de = el<HTMLInputElement>("aud-desde");
  const ate = el<HTMLInputElement>("aud-ate");
  const csv = el<HTMLAnchorElement>("aud-csv");
  let filtros = new URLSearchParams();
  let geracao = 0;
  let proximo: number | null = null;
  let extras = false; // "Mostrar mais" já trouxe páginas antigas: a atualização periódica não refaz a lista
  let partes: { resumo: HTMLElement; lista: HTMLElement; mais: HTMLButtonElement; total: number } | null = null;

  const linha = (a: Registro): HTMLLIElement => {
    const sinal = ["entrada_recusada", "entrada_emergencia", "emergencia"].includes(a.acao);
    const mud = mudanca(a.detalhe);
    return criar("li", { className: `linha registro${sinal ? " sinal" : ""}` }, [
      criar("p", { className: "linha-titulo" }, [criar("span", { textContent: ACOES[a.acao] ?? a.acao, title: a.acao }), a.alvo ? criar("code", { textContent: a.alvo }) : null]),
      mud !== "—" ? criar("p", { className: "linha-mudanca", textContent: mud }) : null,
      criar("p", { className: "linha-detalhe" }, [
        tempo(a.quando),
        ` · ${quemFez(a.admin)} · IP ${a.ip ?? "—"} · `,
        criar("span", { textContent: navegadorCurto(a.agente), title: a.agente ?? "" }),
      ]),
    ]);
  };

  const opcoes = (sel: HTMLSelectElement, valores: string[], rotulo: (v: string) => string) => {
    const escolhido = sel.value;
    const novos = ["", ...valores];
    const atuais = Array.from(sel.options).map((o) => o.value);
    if (JSON.stringify(novos) === JSON.stringify(atuais)) return; // sem mudar o <select> enquanto alguém escolhe
    sel.replaceChildren(criar("option", { value: "", textContent: "Todas" }), ...valores.map((v) => criar("option", { value: v, textContent: rotulo(v) })));
    sel.value = valores.includes(escolhido) ? escolhido : "";
  };

  const atualizarCsv = () => {
    const q = new URLSearchParams(filtros);
    q.set("fuso_min", String(-new Date().getTimezoneOffset())); // as datas da planilha no horário de quem baixa
    csv.href = `api/auditoria.csv?${q}`;
  };

  const carregar = async () => {
    const minha = ++geracao;
    const q = filtros.toString();
    const r = await api<Auditoria>(`api/auditoria${q ? `?${q}` : ""}`);
    if (minha !== geracao) return;
    if (!partes) {
      const resumo = criar("p", { className: "fraco", id: "auditoria-resumo" });
      const lista = criar("ul", { className: "linhas", id: "auditoria-lista", tabIndex: -1, attrs: { "aria-label": "Ações do painel" } });
      const mais = criar("button", { type: "button", className: "botao", id: "auditoria-mais" }, ["Mostrar mais"]);
      mais.addEventListener("click", () => void maisItens());
      regiao.replaceChildren(resumo, lista, criar("div", { className: "acoes" }, [mais]));
      partes = { resumo, lista, mais, total: 0 };
    }
    opcoes(pessoa, r.pessoas, quemFez);
    opcoes(acao, r.acoes, (v) => ACOES[v] ?? v);
    const p = partes;
    const itens = r.itens.length ? r.itens.map(linha) : [criar("li", { className: "fraco sem-itens", textContent: q ? "Nenhuma ação com estes filtros." : "Nenhuma ação registrada ainda." })];
    if (!trocar(p.lista, itens, JSON.stringify([q, r.itens]))) return; // nada novo: a lista fica como está
    proximo = r.proximo;
    p.total = r.itens.length;
    p.resumo.textContent = r.itens.length ? `${plural(r.itens.length, "ação", "ações")}${proximo ? " (as mais recentes; há mais)" : ""}, da mais recente para a mais antiga.` : "";
    p.mais.hidden = proximo === null;
  };

  const maisItens = async () => {
    if (!partes || proximo === null) return;
    const p = partes;
    ocupado(p.mais, true);
    try {
      const q = new URLSearchParams(filtros);
      q.set("ate", String(proximo));
      const r = await api<Auditoria>(`api/auditoria?${q}`);
      p.lista.append(...r.itens.map(linha));
      extras = true;
      p.total += r.itens.length;
      proximo = r.proximo;
      p.resumo.textContent = `${plural(p.total, "ação", "ações")}${proximo ? " (há mais)" : ""}, da mais recente para a mais antiga.`;
      anunciar(`Mais ${plural(r.itens.length, "ação carregada", "ações carregadas")}.`);
      if (proximo === null) {
        p.mais.hidden = true;
        p.lista.focus();
      }
    } catch (e) {
      erroDeCarga(e, null, () => undefined);
    } finally {
      ocupado(p.mais, false);
    }
  };

  const filtrar = () => {
    const q = new URLSearchParams();
    if (pessoa.value) q.set("pessoa", pessoa.value);
    if (acao.value) q.set("acao", acao.value);
    if (alvo.value.trim()) q.set("alvo", alvo.value.trim());
    const d0 = dia(de.value);
    const d1 = dia(ate.value, true);
    if (d0 !== null) q.set("desde", String(d0));
    if (d1 !== null) q.set("ate", String(d1));
    filtros = q;
    extras = false;
    atualizarCsv();
    regiao.setAttribute("aria-busy", "true");
    carregar()
      .then(() => anunciar(partes?.total ? `${plural(partes.total, "ação encontrada", "ações encontradas")}.` : "Nenhuma ação com estes filtros."))
      .catch((e) => erroDeCarga(e, partes ? null : regiao, () => undefined))
      .finally(() => regiao.setAttribute("aria-busy", "false"));
  };
  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    if (de.value && ate.value && de.value > ate.value) {
      marcarErro(ate, true);
      anunciar("A data final vem antes da inicial.", true);
      ate.focus();
      return;
    }
    marcarErro(ate, false);
    filtrar();
  });
  el("aud-limpar").addEventListener("click", () => {
    form.reset();
    marcarErro(ate, false);
    filtrar();
  });
  atualizarCsv();
  return controle({ regiao, carregar: () => (extras ? Promise.resolve() : carregar()) });
}

// -- configuração do servidor ---------------------------------------------------------------------------------

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
  acessos: { pessoas: number; donos: number; chave_emergencia: boolean };
  empresa_faltando: string[];
};

function checagem(r: Seguranca): HTMLElement[] {
  const item = (ok: boolean, texto: string, falta: string) =>
    criar("li", { className: ok ? "ok" : "falta" }, [icone(ok ? "ok" : "alerta"), criar("span", { textContent: ok ? texto : falta })]);
  const visto = r.orquestrador_visto_s;
  const a = r.acessos;
  const analise = (() => {
    if (r.analise === "desligada") return item(false, "", "Aviso de pessoas nas fotos desligado.");
    if (r.analise !== "separada") return item(true, "Aviso de pessoas nas fotos: na própria API.", "");
    const v = r.analisador_visto_s ?? null;
    return item(
      v !== null && v < 180,
      `Aviso de pessoas nas fotos: serviço próprio, visto há ${Math.round(v ?? 0)} s.`,
      v === null ? "Serviço de análise das fotos ainda sem sinal (contêiner analisador)." : `Análise das fotos sem sinal há ${Math.round(v)} s: confira o contêiner analisador.`,
    );
  })();
  return [
    item(a.donos > 0, `${plural(a.pessoas, "pessoa", "pessoas")} com acesso próprio ao painel (${plural(a.donos, "dono", "donos")}).`, "Nenhum dono com acesso próprio: crie um convite em Acessos (ou pelo terminal: render2go-servico admin-convite)."),
    a.chave_emergencia
      ? item(r.totp, "Chave de emergência ligada, com o código do autenticador do servidor.", "Chave de emergência SEM o código do autenticador: configure RENDER2GO_ADMIN_TOTP.")
      : item(true, "Sem chave de emergência: só os acessos por pessoa entram no painel.", ""),
    item(r.admin_ips > 0, `Painel restrito a ${plural(r.admin_ips, "rede", "redes")}.`, "Painel aberto a qualquer IP (opcional: RENDER2GO_ADMIN_IPS)."),
    item(
      r.turnstile,
      "Verificação “não sou um robô” (Turnstile) ligada.",
      r.turnstile_desligado ? "Turnstile DESLIGADO de propósito (só para desenvolvimento)." : "Turnstile não configurado: a entrada por e-mail e o suporte sem conta estão recusando pedidos.",
    ),
    item(r.email, "E-mail configurado: os códigos de entrada saem.", "E-mail não configurado: ninguém consegue receber o código para entrar."),
    item(r.suporte_email, "Mensagens de suporte chegam no seu e-mail.", "Suporte só no painel (falta RENDER2GO_SUPORTE_PARA)."),
    item(r.pagamento, "Recarga por Pix (Mercado Pago) ligada.", "Recarga por Pix desligada: os clientes recarregam pelo suporte."),
    item(r.cookie_seguro, "Cookies só por HTTPS.", "Cookies sem HTTPS (só para desenvolvimento local)."),
    item(r.segredo_contas, "Segredo próprio das contas configurado.", "Sem RENDER2GO_SEGREDO_CONTAS (os códigos e os segredos do autenticador usam uma chave derivada do segredo de aprovação)."),
    item(
      !r.empresa_faltando.length,
      "Identificação da empresa completa nas páginas legais e no rodapé.",
      `Identificação da empresa incompleta nas páginas legais e no rodapé: falta ${r.empresa_faltando.join(", ")}.`,
    ),
    analise,
    item(visto !== null && visto < 120, `Orquestrador ativo (visto há ${Math.round(visto ?? 0)} s).`, visto === null ? "O orquestrador ainda não deu sinal." : `Orquestrador sem sinal há ${Math.round(visto)} s: confira o contêiner.`),
  ];
}

export function criarSeguranca(): Controle {
  const regiao = el("seguranca-dados");
  let partes: { lista: HTMLElement; contas: HTMLElement } | null = null;
  const carregar = async () => {
    const r = await api<Seguranca>("api/seguranca");
    if (!partes) {
      const lista = criar("ul", { className: "lista-checagem cartao", id: "checagem" });
      const contas = criar("dl", { className: "numeros", id: "seguranca-contas", attrs: { "aria-labelledby": "t-contas-servidor" } });
      regiao.replaceChildren(lista, criar("h2", { id: "t-contas-servidor", textContent: "Contas" }), contas);
      partes = { lista, contas };
    }
    trocar(partes.lista, checagem(r), JSON.stringify({ ...r, orquestrador_visto_s: Math.round((r.orquestrador_visto_s ?? -1) / 30) }));
    const k = r.contas;
    const numero = (rotulo: string, valor: number | undefined) => criar("div", {}, [criar("dt", { textContent: rotulo }), criar("dd", { textContent: (valor ?? 0).toLocaleString("pt-BR") })]);
    trocar(
      partes.contas,
      [numero("Contas", k.total), numero("No teste grátis", k.teste), numero("No pré-pago", k.pago), numero("Com duas etapas", k.com_mfa), numero("Pausadas pelo cliente", k.pausadas), numero("Novas em 24 h", k.novos_24h)],
      JSON.stringify(k),
    );
  };
  return controle({ regiao, carregar });
}
