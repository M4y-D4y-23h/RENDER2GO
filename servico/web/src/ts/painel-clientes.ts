// Painel: contas e saldo (cada conta abre numa janela com o que dá para fazer nela: lançar no saldo, mudar o
// plano, ligar um e-mail, desligar a verificação em duas etapas, desativar ou reativar e baixar o extrato), as
// mensagens de suporte e o "Tenho interesse" de cada opção em breve.
import { type ItemLista, anunciar, api, aviso, baixarJson, confirmar, criar, curto, dialogo, el, icone, marcarErro, ocupado, plural, quando, reais, reconciliar, tempo, vazio } from "./comum";
import { type Controle, controle, erroDeAcao, erroDeCarga, iniciarAbas, pode, semLinhas, tabela, tempoCurto, trocar } from "./painel-base";

type Cliente = {
  id: string;
  nome: string;
  email: string | null;
  plano: string;
  ativo: number | boolean;
  criado: number;
  ultimo_acesso: number | null;
  excluido_em: number | null;
  pausada_em: number | null;
  mfa_ativo_em: number | null;
  saldo: number;
};
type Clientes = { clientes: Cliente[]; resumo: Record<string, number>; precos: Record<string, number>; planos_cobrados: string[] };
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
type Interesse = { finalidade: string; titulo: string; tipo: string; clientes: number; clientes_teste: number; ultimo: number };
type Clique = { finalidade: string; titulo: string; tipo: string; quando: number; cliente_id: string; nome: string; email: string | null; plano: string };

const PLANOS: Record<string, string> = { teste: "Teste gratuito", pago: "Pré-pago", interno: "Interno" };
const EMAIL = /^[^@\s<>"',;]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$/;

function situacao(k: Cliente): string {
  if (k.excluido_em) return "excluída";
  if (!k.ativo) return "desativada pelo painel";
  if (k.pausada_em) return "pausada pelo cliente";
  return "ativa";
}

function nomeConta(k: Cliente): string {
  return k.email ?? k.nome;
}

// -- a janela de uma conta ------------------------------------------------------------------------------------

function janelaConta(inicial: Cliente, ctx: { cobrados: string[]; recarregar: () => Promise<Clientes | null> }): void {
  const { d, corpo, titulo } = dialogo(nomeConta(inicial), "conta-cliente");
  const origem = document.activeElement as HTMLElement | null;
  let k = inicial;

  const acao = async (botao: HTMLButtonElement, caminho: string, corpoPedido: unknown, sucesso: string, msg: HTMLElement) => {
    ocupado(botao, true);
    try {
      await api(`api/clientes/${encodeURIComponent(k.id)}/${caminho}`, { method: "POST", body: corpoPedido === undefined ? undefined : JSON.stringify(corpoPedido) });
      anunciar(sucesso);
      const r = await ctx.recarregar();
      const novo = r?.clientes.find((x) => x.id === k.id);
      if (novo) k = novo;
      desenhar(sucesso);
    } catch (e) {
      ocupado(botao, false);
      erroDeAcao(e, msg);
    }
  };

  const desenhar = (sucesso?: string) => {
    titulo.textContent = nomeConta(k);
    const cobrado = ctx.cobrados.includes(k.plano);
    const vivo = !k.excluido_em;
    const permite = (rota: string) => vivo && pode(rota);
    const msg = criar("div", { className: "aviso", hidden: true, attrs: { role: "alert" } });
    const item = (rotulo: string, valor: string | Node) => criar("div", {}, [criar("dt", { textContent: rotulo }), criar("dd", {}, [valor])]);
    const partes: (Node | null)[] = [];
    if (sucesso) {
      const ok = criar("p", { className: "aviso ok", tabIndex: -1, textContent: sucesso });
      partes.push(ok);
      queueMicrotask(() => ok.focus());
    }
    partes.push(
      criar("dl", { className: "dados-conta" }, [
        item("Plano", PLANOS[k.plano] ?? k.plano),
        cobrado ? item("Saldo", reais(k.saldo)) : null,
        item("Situação", situacao(k)),
        item("Verificação em duas etapas", k.mfa_ativo_em ? `ligada desde ${quando(k.mfa_ativo_em)}` : "desligada"),
        item("Criada", quando(k.criado)),
        item("Último acesso", k.ultimo_acesso ? quando(k.ultimo_acesso) : "—"),
        item("Identificador", criar("code", { textContent: k.id })),
      ]),
    );
    if (k.pausada_em && k.ativo && vivo)
      partes.push(criar("p", { className: "fraco", textContent: `Pausada pelo cliente em ${quando(k.pausada_em)}: volta sozinha quando a pessoa entrar de novo pelo código do e-mail.` }));

    // lançar no saldo (recarga manual ou ajuste)
    if (cobrado && k.ativo && permite("POST /api/clientes/{cliente_id}/saldo")) {
      const valor = criar("input", { id: "saldo-valor", inputMode: "decimal", maxLength: 12, placeholder: "50,00", autocomplete: "off" });
      valor.setAttribute("aria-describedby", "saldo-valor-dica");
      const referencia = criar("input", { id: "saldo-referencia", maxLength: 120, autocomplete: "off", spellcheck: false });
      referencia.setAttribute("aria-describedby", "saldo-referencia-dica");
      const descricao = criar("input", { id: "saldo-descricao", maxLength: 200, placeholder: "Pix de 09/10", autocomplete: "off" });
      const enviar = criar("button", { type: "submit", className: "botao primario", id: "saldo-enviar" }, [icone("ok"), "Lançar no saldo"]);
      const msgSaldo = criar("div", { id: "saldo-msg", className: "aviso", hidden: true, attrs: { role: "alert" } });
      const form = criar("form", { className: "pilha subsecao-conta", noValidate: true, attrs: { "aria-labelledby": "t-saldo" } }, [
        criar("h3", { id: "t-saldo", textContent: "Lançar no saldo" }),
        criar("div", { className: "duas" }, [
          criar("div", { className: "campo" }, [
            criar("label", { htmlFor: "saldo-valor", textContent: "Valor em reais" }),
            valor,
            criar("p", { className: "dica", id: "saldo-valor-dica", textContent: "Recarga manual. Negativo desconta (ajuste). As recargas por Pix entram sozinhas." }),
          ]),
          criar("div", { className: "campo" }, [
            criar("label", { htmlFor: "saldo-referencia", textContent: "Identificador do pagamento (opcional)" }),
            referencia,
            criar("p", { className: "dica", id: "saldo-referencia-dica", textContent: "Ex.: o código do Pix. O mesmo pagamento não entra duas vezes." }),
          ]),
        ]),
        criar("div", { className: "campo" }, [criar("label", { htmlFor: "saldo-descricao", textContent: "Descrição (o cliente vê no extrato)" }), descricao]),
        criar("div", { className: "acoes" }, [enviar]),
        msgSaldo,
      ]);
      for (const campo of [valor, descricao]) campo.addEventListener("input", () => marcarErro(campo, false));
      form.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const v = valor.value.trim();
        const erro: [HTMLElement, string] | null = !/^-?\d{1,7}([.,]\d{1,2})?$/.test(v)
          ? [valor, "Escreva o valor em reais, ex.: 50 ou 19,90."]
          : descricao.value.trim().length < 3
            ? [descricao, "Escreva uma descrição (o cliente vê no extrato)."]
            : null;
        if (erro) {
          marcarErro(erro[0], true);
          aviso(msgSaldo, erro[1], "erro");
          erro[0].focus();
          return;
        }
        const negativo = v.startsWith("-");
        const ok = await confirmar({
          titulo: "Lançar no saldo?",
          texto: `${negativo ? "Descontar" : "Adicionar"} R$ ${v.replace(/^-/, "")} ${negativo ? "do" : "ao"} saldo de ${nomeConta(k)}.`,
          confirmar: "Lançar",
        });
        if (!ok) return;
        ocupado(enviar, true);
        try {
          const r = await api<{ saldo: number }>(`api/clientes/${encodeURIComponent(k.id)}/saldo`, {
            method: "POST",
            body: JSON.stringify({ valor: v, descricao: descricao.value.trim(), referencia: referencia.value.trim() || null }),
          });
          const texto = `Lançado. Saldo de ${nomeConta(k)} agora: ${reais(r.saldo)}.`;
          anunciar(texto);
          const lista = await ctx.recarregar();
          k = lista?.clientes.find((x) => x.id === k.id) ?? { ...k, saldo: r.saldo };
          desenhar(texto);
        } catch (e) {
          ocupado(enviar, false);
          erroDeAcao(e, msgSaldo);
        }
      });
      partes.push(form);
    }

    // plano, e-mail e verificação em duas etapas
    const outras: HTMLElement[] = [];
    if (permite("POST /api/clientes/{cliente_id}/plano")) {
      const plano = criar("select", { id: "conta-plano" }, Object.entries(PLANOS).map(([v, t]) => criar("option", { value: v, textContent: t, selected: v === k.plano })));
      const mudar = criar("button", { type: "button", className: "botao" }, ["Mudar o plano"]);
      mudar.addEventListener("click", async () => {
        if (plano.value === k.plano) {
          aviso(msg, "Escolha um plano diferente do atual.", "erro");
          plano.focus();
          return;
        }
        const ok = await confirmar({ titulo: "Mudar o plano?", texto: `${nomeConta(k)}: de ${PLANOS[k.plano] ?? k.plano} para ${PLANOS[plano.value]}.`, confirmar: "Mudar" });
        if (ok) await acao(mudar, "plano", { plano: plano.value }, `Plano mudado para ${PLANOS[plano.value]}.`, msg);
      });
      outras.push(criar("div", { className: "campo" }, [criar("label", { htmlFor: "conta-plano", textContent: "Plano" }), criar("div", { className: "linha-campo" }, [plano, mudar])]));
    }
    if (!k.email && permite("POST /api/clientes/{cliente_id}/email")) {
      const email = criar("input", { id: "conta-email", type: "email", maxLength: 254, autocomplete: "off", spellcheck: false });
      email.setAttribute("aria-describedby", "conta-email-dica");
      const ligar = criar("button", { type: "button", className: "botao" }, [icone("mensagem"), "Ligar o e-mail"]);
      ligar.addEventListener("click", async () => {
        if (!EMAIL.test(email.value.trim())) {
          marcarErro(email, true);
          aviso(msg, "Escreva o e-mail da conta.", "erro");
          email.focus();
          return;
        }
        await acao(ligar, "email", { email: email.value.trim() }, "E-mail ligado à conta.", msg);
      });
      email.addEventListener("input", () => marcarErro(email, false));
      outras.push(
        criar("div", { className: "campo" }, [
          criar("label", { htmlFor: "conta-email", textContent: "E-mail" }),
          criar("div", { className: "linha-campo" }, [email, ligar]),
          criar("p", { className: "dica", id: "conta-email-dica", textContent: "Conta antiga (código de acesso): com o e-mail, a pessoa passa a entrar pelo código que chega nele." }),
        ]),
      );
    }
    if (outras.length) partes.push(criar("div", { className: "pilha subsecao-conta" }, [criar("h3", { textContent: "Plano e e-mail" }), ...outras]));

    const botoes: HTMLElement[] = [];
    if (cobrado) {
      const extrato = criar("button", { type: "button", className: "botao pequeno" }, [icone("baixar"), "Baixar o extrato"]);
      extrato.addEventListener("click", async () => {
        ocupado(extrato, true);
        try {
          baixarJson(await api<unknown>(`api/clientes/${encodeURIComponent(k.id)}/extrato`), `extrato-${k.id}.json`);
        } catch (e) {
          erroDeAcao(e, msg);
        } finally {
          ocupado(extrato, false);
        }
      });
      botoes.push(extrato);
    }
    if (k.mfa_ativo_em && permite("POST /api/clientes/{cliente_id}/mfa/desligar")) {
      const mfa = criar("button", { type: "button", className: "botao pequeno perigo" }, [icone("cadeado"), "Desligar a verificação em duas etapas"]);
      mfa.addEventListener("click", async () => {
        const ok = await confirmar({
          titulo: "Desligar a verificação em duas etapas?",
          texto: [
            "Só para quem perdeu o celular e os códigos de recuperação, depois de o suporte conferir que é a própria pessoa.",
            `A entrada de ${nomeConta(k)} volta a pedir só o código do e-mail, e o e-mail da conta recebe um aviso.`,
          ],
          confirmar: "Desligar",
          perigo: true,
        });
        if (ok) await acao(mfa, "mfa/desligar", undefined, "Verificação em duas etapas desligada. A conta recebeu um aviso por e-mail.", msg);
      });
      botoes.push(mfa);
    }
    if (vivo && k.ativo && pode("POST /api/clientes/{cliente_id}/desativar")) {
      const desativar = criar("button", { type: "button", className: "botao pequeno perigo" }, [icone("bloquear"), "Desativar a conta"]);
      desativar.addEventListener("click", async () => {
        const ok = await confirmar({
          titulo: "Desativar a conta?",
          texto: `${nomeConta(k)} deixa de entrar na hora (as sessões caem). Só o painel reativa.`,
          confirmar: "Desativar",
          perigo: true,
        });
        if (ok) await acao(desativar, "desativar", undefined, "Conta desativada.", msg);
      });
      botoes.push(desativar);
    } else if (vivo && !k.ativo && pode("POST /api/clientes/{cliente_id}/reativar")) {
      const reativar = criar("button", { type: "button", className: "botao pequeno sucesso" }, [icone("ok"), "Reativar a conta"]);
      reativar.addEventListener("click", async () => {
        const ok = await confirmar({ titulo: "Reativar a conta?", texto: `${nomeConta(k)} volta a entrar.`, confirmar: "Reativar" });
        if (ok) await acao(reativar, "reativar", undefined, "Conta reativada.", msg);
      });
      botoes.push(reativar);
    }
    if (botoes.length) partes.push(criar("div", { className: "acoes subsecao-conta" }, botoes));
    partes.push(msg);
    const fechar = criar("button", { type: "button", className: "botao", textContent: "Fechar" });
    fechar.addEventListener("click", () => d.close());
    partes.push(criar("div", { className: "dialogo-acoes" }, [fechar]));
    corpo.replaceChildren(...partes.filter((p): p is Node => !!p));
  };

  desenhar();
  d.addEventListener("close", () => {
    d.remove();
    // a tabela pode ter sido refeita: o foco volta para o "Gerenciar" desta conta
    const botao = document.querySelector<HTMLElement>(`#clientes-tabela [data-foco="conta:${CSS.escape(k.id)}"]`);
    (botao ?? origem)?.focus();
  });
  d.showModal();
}

function janelaNovaConta(aoCriar: () => void): void {
  const { d, corpo } = dialogo("Nova conta", "conta-cliente");
  const origem = document.activeElement as HTMLElement | null;
  const email = criar("input", { id: "cliente-email", type: "email", required: true, maxLength: 254, autocomplete: "off", spellcheck: false });
  const plano = criar("select", { id: "cliente-plano" }, [
    criar("option", { value: "pago", textContent: "Pré-pago (saldo e preço por segundo, vídeos de 4 e 5 s)" }),
    criar("option", { value: "teste", textContent: "Teste gratuito (um pedido, até 5 fotos, 3 s, 480p)" }),
    criar("option", { value: "interno", textContent: "Interno (só você: sem limites e sem revisão)" }),
  ]);
  const cota = criar("input", { id: "cliente-cota", type: "number", inputMode: "numeric", min: "1", max: "100000", value: "500" });
  const msg = criar("div", { id: "cliente-msg", className: "aviso", hidden: true, attrs: { role: "alert" } });
  const criarBotao = criar("button", { type: "submit", className: "botao primario", id: "cliente-enviar" }, [icone("pessoa"), "Criar a conta"]);
  const cancelar = criar("button", { type: "button", className: "botao", textContent: "Fechar" });
  cancelar.addEventListener("click", () => d.close());
  const form = criar("form", { className: "pilha", noValidate: true }, [
    criar("p", {
      className: "fraco",
      textContent: "As pessoas criam a conta sozinhas no primeiro acesso (teste gratuito). Use aqui para abrir uma conta já no pré-pago ou no plano interno, ou com e-mail de empresa: a pessoa entra com o código que chega no e-mail.",
    }),
    criar("div", { className: "campo" }, [criar("label", { htmlFor: "cliente-email", textContent: "E-mail" }), email]),
    criar("div", { className: "campo" }, [criar("label", { htmlFor: "cliente-plano", textContent: "Plano" }), plano]),
    criar("div", { className: "campo" }, [criar("label", { htmlFor: "cliente-cota", textContent: "Fotos por dia" }), cota]),
    msg,
    criar("div", { className: "dialogo-acoes" }, [cancelar, criarBotao]),
  ]);
  email.addEventListener("input", () => marcarErro(email, false));
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (!EMAIL.test(email.value.trim())) {
      marcarErro(email, true);
      aviso(msg, "Escreva o e-mail da conta.", "erro");
      email.focus();
      return;
    }
    ocupado(criarBotao, true);
    try {
      const r = await api<{ cliente: string; email: string; plano: string }>("api/clientes", {
        method: "POST",
        body: JSON.stringify({ email: email.value.trim(), plano: plano.value, cota_fotos_dia: Number(cota.value) || 500 }),
      });
      aviso(msg, `Conta ${r.email} criada (${PLANOS[r.plano] ?? r.plano}). A pessoa entra pela página inicial com o código que chega no e-mail.`, "ok");
      anunciar("Conta criada.");
      form.reset();
      email.focus();
      aoCriar();
    } catch (e) {
      erroDeAcao(e, msg);
    } finally {
      ocupado(criarBotao, false);
    }
  });
  corpo.append(form);
  d.addEventListener("close", () => {
    d.remove();
    origem?.focus();
  });
  d.showModal();
  email.focus();
}

// -- a seção "Contas e saldo" ---------------------------------------------------------------------------------

export function criarClientes(): Controle {
  const regiao = el("clientes-dados");
  let busca = "";
  let geracao = 0;
  let montado: { resumo: HTMLElement; precos: HTMLElement; tabela: HTMLElement } | null = null;
  let ultimo: Clientes | null = null;

  const montar = () => {
    const resumo = criar("p", { className: "fraco resumo-contas", id: "resumo-contas" });
    const precos = criar("p", { className: "fraco", id: "precos-info" });
    const tab = criar("div", { className: "tabela", id: "clientes-tabela", attrs: { role: "region", "aria-label": "Contas", tabindex: "0" } });
    regiao.replaceChildren(resumo, precos, tab);
    return { resumo, precos, tabela: tab };
  };

  const desenhar = (r: Clientes) => {
    montado ??= montar();
    const t = r.resumo;
    montado.resumo.textContent =
      `${plural(t.total ?? 0, "conta", "contas")}: ${t.teste ?? 0} no teste grátis, ${t.pago ?? 0} no pré-pago; ` +
      `${t.com_mfa ?? 0} com verificação em duas etapas, ${t.pausadas ?? 0} pausadas pelo cliente; ${t.novos_24h ?? 0} nas últimas 24 h.`;
    const p = r.precos;
    montado.precos.textContent =
      `Pré-pago: 480p a ${reais(p.rapida ?? 0)} e 720p a ${reais(p.alta ?? 0)} por segundo de vídeo. ` +
      "O pedido é cobrado quando as fotos chegam; pedido recusado e vídeo que não sai voltam sozinhos para o saldo.";
    const gerir = pode("POST /api/clientes/{cliente_id}/plano");
    const linhas = r.clientes.map((k) => {
      const cobrado = r.planos_cobrados.includes(k.plano);
      // a conta abre a janela com o que dá para fazer nela (no celular, a 1ª coluna é a que fica na tela)
      const botao = criar("button", { type: "button", className: "botao-linha", dados: { foco: `conta:${k.id}` } }, [
        nomeConta(k),
        criar("span", { className: "visualmente-oculto", textContent: gerir ? " (gerenciar a conta)" : " (ver a conta)" }),
      ]);
      botao.addEventListener("click", () => janelaConta(k, { cobrados: r.planos_cobrados, recarregar }));
      return {
        classe: k.excluido_em || !k.ativo ? "apagada" : "",
        celulas: [
          botao,
          PLANOS[k.plano] ?? k.plano,
          cobrado ? reais(k.saldo) : "—",
          situacao(k),
          k.mfa_ativo_em ? "ligada" : "—",
          k.ultimo_acesso ? tempoCurto(k.ultimo_acesso) : "—",
          tempoCurto(k.criado),
        ],
      };
    });
    trocar(
      montado.tabela,
      [
        r.clientes.length
          ? tabela(
              [["Conta", false], ["Plano", false], ["Saldo", true], ["Situação", false], ["Duas etapas", false], ["Último acesso", false], ["Criada", false]],
              linhas,
              "Contas e saldo",
            )
          : semLinhas(busca ? "Nenhuma conta encontrada nesta busca." : "Nenhuma conta ainda."),
      ],
      JSON.stringify([r, gerir]),
    );
  };

  const carregar = async (): Promise<Clientes | null> => {
    const minha = ++geracao;
    const r = await api<Clientes>(`api/clientes${busca ? `?busca=${encodeURIComponent(busca)}` : ""}`);
    if (minha !== geracao) return null;
    ultimo = r;
    desenhar(r);
    return r;
  };
  // depois de uma ação numa conta: a tabela e a janela mostram o que mudou
  const recarregar = async (): Promise<Clientes | null> => {
    try {
      return (await carregar()) ?? ultimo;
    } catch {
      return ultimo;
    }
  };

  const c = controle({ regiao, carregar: async () => void (await carregar()) });
  el<HTMLFormElement>("form-busca").addEventListener("submit", (ev) => {
    ev.preventDefault();
    busca = el<HTMLInputElement>("busca-cliente").value.trim();
    regiao.setAttribute("aria-busy", "true");
    carregar()
      .then(() => anunciar(busca ? `Busca por ${busca}: ${plural(ultimo?.clientes.length ?? 0, "conta", "contas")}.` : "Todas as contas."))
      .catch((e) => erroDeCarga(e, montado ? null : regiao, () => undefined))
      .finally(() => regiao.setAttribute("aria-busy", "false"));
  });
  el("nova-conta").addEventListener("click", () => janelaNovaConta(() => void recarregar()));
  return c;
}

// -- suporte --------------------------------------------------------------------------------------------------

function listaSuporte(msgs: MensagemSuporte[]): ItemLista[] {
  return msgs.map((m) => ({
    chave: m.id,
    assinatura: JSON.stringify([m.enviado, m.erro]),
    criar: () => {
      const situacao_ = criar("p", {
        className: m.erro ? "" : "fraco",
        textContent: `${m.enviado ? "enviada por e-mail" : m.erro ? `e-mail não saiu: ${m.erro}` : "guardada (e-mail não configurado)"} · IP ${m.ip ?? "—"} · dispositivo ${curto(m.dispositivo)}`,
      });
      if (m.erro) aviso(situacao_, situacao_.textContent ?? "", "erro");
      const email = m.email ? criar("a", { href: `mailto:${encodeURIComponent(m.email).replace("%40", "@")}`, textContent: m.email }) : "—";
      return criar("article", { className: "cartao msg-suporte pilha" }, [
        criar("div", { className: "lote-cab" }, [
          criar("strong", { textContent: m.assunto || "Sem assunto" }),
          criar("span", { className: "fraco" }, [tempo(m.quando), ` · ${m.cliente_nome ?? (m.cliente_id || "sem conta")} (${m.plano ?? "?"}) · responder para `, email]),
        ]),
        criar("p", { className: "texto", textContent: m.mensagem }),
        situacao_,
      ]);
    },
  }));
}

export function criarSuporte(): Controle {
  const regiao = el("suporte-dados");
  let montado: { config: HTMLElement; lista: HTMLElement } | null = null;
  const carregar = async () => {
    const r = await api<{ email_configurado: boolean; mensagens: MensagemSuporte[] }>("api/suporte");
    if (!montado) {
      const config = criar("p", { className: "fraco", id: "suporte-config" });
      const lista = criar("div", { className: "lista", id: "suporte-lista" });
      regiao.replaceChildren(config, lista);
      montado = { config, lista };
    }
    montado.config.textContent = r.email_configurado
      ? "As mensagens também chegam no seu e-mail."
      : "E-mail do suporte não configurado: as mensagens ficam só aqui (docs/09, seção do suporte).";
    reconciliar(montado.lista, listaSuporte(r.mensagens), () => vazio("Nenhuma mensagem ainda.", "As mensagens enviadas pelo /suporte aparecem aqui.", "mensagem"));
  };
  return controle({ regiao, carregar });
}

// -- interesse nas opções em breve ----------------------------------------------------------------------------

export function criarInteresses(): Controle {
  iniciarAbas(el("abas-interesses"));
  const carregar = async () => {
    const r = await api<{ resumo: Interesse[]; cliques: Clique[] }>("api/interesses");
    trocar(
      el("interesses-tabela"),
      [
        r.resumo.length
          ? tabela(
              [["Opção", false], ["Tipo", false], ["Contas", true], ["Destas, no teste grátis", true], ["Último clique", false]],
              r.resumo.map((x) => ({ celulas: [x.titulo, x.tipo, x.clientes, x.clientes_teste, tempoCurto(x.ultimo)] })),
              "Interesse por opção",
            )
          : semLinhas("Nenhum interesse registrado ainda."),
      ],
      JSON.stringify(r.resumo),
    );
    trocar(
      el("cliques"),
      [
        r.cliques.length
          ? tabela(
              [["Quando", false], ["Opção", false], ["Conta", false], ["E-mail", false], ["Plano", false]],
              r.cliques.map((x) => ({ celulas: [tempoCurto(x.quando), x.titulo, x.nome, x.email ?? "—", PLANOS[x.plano] ?? x.plano] })),
              "Cada clique em Tenho interesse",
            )
          : semLinhas("Nenhum clique ainda."),
      ],
      JSON.stringify(r.cliques),
    );
  };
  return controle({ regiao: el("interesses-tabela"), ocupado: el("interesses-dados"), carregar });
}
