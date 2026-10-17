// "Entendemos seu pedido assim" (ADR 0018, 17/10/2026): o que o cliente pediu, o que entendemos (em português), o que o
// serviço acrescenta e por quê, o que continua igual, os limites e, separado, o texto técnico final que foi para o
// gerador (com a versão). No modo "revisar", só a versão aprovada aqui vai para os pods; nos dois modos, uma mudança
// importante na adaptação espera a decisão do cliente. A prévia do formulário usa as mesmas peças, antes do envio.
import { ErroApi, RESOLUCAO, anunciar, api, aviso, contador, criar, dialogo, icone, marcarErro, ocupado, plural, reais } from "./comum";

export type Ajuste = { o_que: string; por_que: string; origem: "cliente" | "estilo" | "sistema" };
export type Pendencia = { codigo: string; mensagem: string; trecho: string; confirmavel: boolean; texto?: string };
type Preservar = { texto: string; origem: string; como: string };
export type GrupoEntendido = {
  id?: string;
  texto: string;
  proprio: boolean;
  fotos?: number;
  exemplos?: string[];
  tipo: string;
  frase: string;
  ajustes: Ajuste[];
  restricoes: { codigo: string; texto: string }[];
  pendencias: Pendencia[];
  estado?: "preparando" | "pronto" | "recusado";
  recusado?: string | null;
  prompt_en?: string | null;
  compilador?: string | null;
};
export type Entendimento = {
  versao: {
    numero: number;
    hash: string;
    curto: string;
    criada: number;
    liberada: boolean;
    liberada_por: string | null;
    numero_liberado: number | null;
    editada: boolean;
    anterior: { grupo?: string; texto?: string } | null;
  };
  revisao: "seguir" | "revisar";
  original: { texto: string; uso: string | null };
  grupos: GrupoEntendido[];
  excecoes: { foto: string; prompt_en: string; motivo: string }[];
  preservar: Preservar[];
  limites: string[];
  especificacoes: {
    estilo: string;
    qualidade: string | null;
    duracao: number | null;
    variacoes: number;
    videos: number;
    preco_video: number | null;
    estimativa: number | null;
  };
  traduzido: boolean;
  decisoes: { motivo: string; foto: string | null; grupo: string | null }[];
  aprovar: boolean;
};
export type Previa = { grupos: GrupoEntendido[]; preservar: Preservar[]; limites: string[]; uso: string | null };
type LoteEntendido = { lote: string; entendimento: Entendimento; espera_cliente?: boolean; editavel?: boolean };

const ORIGEM: Record<string, string> = { cliente: "Você", estilo: "Estilo", sistema: "Serviço" };

// o cartão é refeito quando o pedido muda: o que estava aberto continua aberto, e o aviso de uma ação que mudou a
// versão (outra aba, tradução nova) aparece no cartão novo
const abertos = new Map<string, boolean>();
const avisos = new Map<string, string>();

// -- peças comuns ------------------------------------------------------------------------------

/** As pendências do texto que a API devolveu (422), ou null se o erro é outro. */
export function pendenciasDe(e: unknown): { pendencias: Pendencia[]; confirmavel: boolean } | null {
  if (!(e instanceof ErroApi) || e.status !== 422) return null;
  const d = e.detalhe as { pendencias?: unknown; confirmavel?: unknown } | null;
  if (!d || typeof d !== "object" || !Array.isArray(d.pendencias)) return null;
  return { pendencias: d.pendencias as Pendencia[], confirmavel: d.confirmavel === true };
}

export function listaPendencias(pendencias: Pendencia[]): HTMLElement {
  return criar(
    "ul",
    { className: "pendencias-lista" },
    pendencias.map((p) =>
      criar("li", {}, [
        p.texto ? criar("strong", { textContent: `${p.texto}: ` }) : null,
        p.mensagem,
        p.trecho && !p.mensagem.includes(p.trecho) ? ` (“${p.trecho}”)` : "",
      ]),
    ),
  );
}

/** O que entendemos de um texto: a frase em português, os ajustes (com a origem e o motivo) e as restrições. */
function blocoTexto(g: GrupoEntendido): HTMLElement[] {
  const out: HTMLElement[] = [criar("p", { className: "frase", textContent: g.frase })];
  if (g.ajustes.length)
    out.push(
      criar(
        "ul",
        { className: "ajustes", attrs: { "aria-label": "O que entra no vídeo além do seu texto" } },
        g.ajustes.map((a) =>
          criar("li", {}, [
            criar("span", { className: `origem origem-${a.origem}`, textContent: ORIGEM[a.origem] ?? a.origem }),
            criar("span", {}, [criar("strong", { textContent: a.o_que }), ` · ${a.por_que}`]),
          ]),
        ),
      ),
    );
  if (g.restricoes.length) out.push(criar("p", { className: "fraco", textContent: `Não pode acontecer: ${g.restricoes.map((r) => r.texto.toLowerCase()).join(", ")}.` }));
  return out;
}

/** `titulo`: o nível do título (h3 no cartão do pedido, abaixo de "Seus pedidos"; h4 na prévia, abaixo do h3 dela). */
function listaPreservar(itens: Preservar[], titulo: "h3" | "h4"): HTMLElement | null {
  if (!itens.length) return null;
  return criar("div", { className: "secao-entendimento" }, [
    criar(titulo, { textContent: "O que precisa continuar igual" }),
    criar(
      "ul",
      { className: "preservar" },
      itens.map((p) => criar("li", {}, [p.texto, criar("span", { className: "fraco", textContent: ` · ${ORIGEM[p.origem] ?? p.origem}: ${p.como}` })])),
    ),
  ]);
}

function listaLimites(limites: string[]): HTMLElement {
  return criar("details", { className: "limites-video" }, [criar("summary", { textContent: "Limites do vídeo" }), criar("ul", { className: "fraco" }, limites.map((t) => criar("li", { textContent: t })))]);
}

// -- prévia no formulário --------------------------------------------------------------------------

/** "Ver como vamos entender": a mesma leitura do envio, antes de criar o pedido. */
export function previaEntendimento(p: Previa): (Node | string)[] {
  const out: (Node | string)[] = [
    criar("h3", { textContent: "Como vamos entender o seu pedido" }),
    criar("p", { className: "fraco", textContent: "Prévia: o texto final do gerador é preparado depois do envio e aparece no pedido, em “Ver texto usado na geração”." }),
  ];
  if (p.uso) out.push(criar("p", { className: "fraco", textContent: `Uso: ${p.uso} (só contexto: não entra na cena).` }));
  for (const [i, g] of p.grupos.entries()) {
    const rotulo = !g.proprio ? (p.grupos.length > 1 ? "Texto padrão" : "Seu texto") : `Texto próprio ${i}`;
    const itens: (Node | string)[] = [criar("h4", { textContent: rotulo }), criar("p", { className: "pedido", textContent: g.texto }), ...blocoTexto(g)];
    if (g.pendencias.length) {
      const caixa = criar("div", { className: "aviso alerta" });
      aviso(caixa, [criar("p", { textContent: "Antes de enviar:" }), listaPendencias(g.pendencias)], "alerta");
      itens.push(caixa);
    }
    out.push(criar("div", { className: "grupo-entendido" }, itens));
  }
  const preservar = listaPreservar(p.preservar, "h4");
  if (preservar) out.push(preservar);
  out.push(listaLimites(p.limites));
  return out;
}

// -- cartão do pedido ------------------------------------------------------------------------------

function estadoVersao(l: LoteEntendido): [string, string] {
  const e = l.entendimento;
  if (e.versao.liberada) return e.versao.liberada_por === "cliente" ? ["Aprovado por você", "ok"] : ["Seguindo a adaptação", "ok"];
  if (e.decisoes.length) return ["Precisa da sua decisão", "alerta"];
  if (e.revisao === "revisar") return e.aprovar ? ["Aguardando a sua aprovação", "alerta"] : ["Preparando para você revisar", "info"];
  return ["Preparando", "info"];
}

function rotuloGrupo(e: Entendimento, g: GrupoEntendido): string {
  const fotos = plural(g.fotos ?? 0, "foto", "fotos");
  if (g.proprio) return `Texto próprio · ${fotos}`;
  return e.grupos.some((x) => x.proprio) ? `Texto padrão · ${fotos}` : `Todas as fotos · ${fotos}`;
}

function textoTecnico(e: Entendimento, g: { prompt_en?: string | null; compilador?: string | null }, resumo = "Ver texto usado na geração"): HTMLElement {
  return criar("details", { className: "texto-tecnico" }, [
    criar("summary", { textContent: resumo }),
    criar("p", { className: "fraco", textContent: "O texto técnico, em inglês, que vai para o gerador. É exatamente este que a versão cobre." }),
    criar("p", { className: "prompt-en", lang: "en", textContent: g.prompt_en ?? "" }),
    criar("p", { className: "fraco", textContent: `Versão ${e.versao.numero} · ${e.versao.curto}${g.compilador ? ` · tradutor ${g.compilador}` : ""}` }),
  ]);
}

/** Mudar o texto de um grupo antes de qualquer vídeo começar: as mesmas conferências do envio, numa janela. */
function editarTexto(l: LoteEntendido, g: GrupoEntendido, recarregar: () => void, origem: HTMLElement): void {
  const e = l.entendimento;
  const { d, corpo } = dialogo(g.proprio ? "Mudar o texto próprio" : "Mudar o texto do pedido", "texto-proprio");
  const base = d.getAttribute("aria-labelledby");
  const campo = criar("textarea", { id: `${base}-texto`, maxLength: 600, minLength: 3, spellcheck: true, autocapitalize: "sentences", required: true });
  campo.value = g.texto;
  const cont = criar("span", { className: "contador", id: `${base}-contador` });
  campo.setAttribute("aria-describedby", `${base}-dica ${base}-contador`);
  contador(campo, cont);
  const msg = criar("div", { className: "aviso", hidden: true, attrs: { role: "alert" } });
  const depois = e.revisao === "revisar" ? "e você aprova a versão nova antes de gerar" : "e os vídeos começam com a versão nova";
  const cancelar = criar("button", { type: "button", className: "botao", textContent: "Cancelar" });
  const salvar = criar("button", { type: "submit", className: "botao primario", textContent: "Salvar o texto novo" });
  const form = criar("form", { noValidate: true, className: "pilha" }, [
    criar("p", { textContent: `Vale para ${plural(g.fotos ?? 0, "foto", "fotos")}. O pedido volta para a revisão de conteúdo ${depois}. Nada foi gerado nem cobrado ainda.` }),
    criar("div", { className: "campo" }, [
      criar("label", { htmlFor: campo.id, textContent: "O que deve acontecer nos vídeos?" }),
      campo,
      criar("div", { className: "linha-campo" }, [criar("p", { className: "dica", id: `${base}-dica`, textContent: "Em português, do seu jeito: o que se mexe numa foto só." }), cont]),
    ]),
    msg,
    criar("div", { className: "dialogo-acoes" }, [cancelar, salvar]),
  ]);
  corpo.append(form);
  let confirmar = false;
  campo.addEventListener("input", () => {
    confirmar = false;
    marcarErro(campo, false);
  });
  cancelar.addEventListener("click", () => d.close());
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const texto = campo.value.split(/\s+/).filter(Boolean).join(" ");
    if (texto.length < 3) {
      marcarErro(campo, true);
      aviso(msg, "Escreva o que deve acontecer (pelo menos 3 letras).", "erro");
      campo.focus();
      return;
    }
    ocupado(salvar, true);
    try {
      await api(`/api/lotes/${l.lote}/texto`, {
        method: "POST",
        body: JSON.stringify({ numero: e.versao.numero, hash: e.versao.hash, grupo: g.id, texto, confirmar_texto: confirmar }),
      });
      avisos.set(l.lote, "Texto novo salvo: o pedido volta para a revisão de conteúdo.");
      abertos.set(l.lote, true);
      d.close();
      anunciar("Texto novo salvo. O pedido volta para a revisão de conteúdo.");
      recarregar();
    } catch (err) {
      ocupado(salvar, false);
      const p = pendenciasDe(err);
      if (p) {
        const itens: (Node | string)[] = [listaPendencias(p.pendencias)];
        if (p.confirmavel) {
          const seguir = criar("button", { type: "button", className: "botao pequeno", textContent: "Salvar com o texto como está" });
          seguir.addEventListener("click", () => {
            confirmar = true;
            form.requestSubmit();
          });
          itens.push(seguir);
        }
        aviso(msg, itens, "alerta");
        campo.focus();
        return;
      }
      if (err instanceof ErroApi && err.motivo === "versao") recarregar();
      aviso(msg, err instanceof Error ? err.message : String(err), "erro");
    }
  });
  d.addEventListener("close", () => {
    d.remove();
    origem.focus();
  });
  d.showModal();
  campo.focus();
}

/** O cartão "Entendemos seu pedido assim" de um pedido com versões. `recarregar`: a lista de pedidos de novo. */
export function cartaoEntendimento(l: LoteEntendido, recarregar: () => void): HTMLElement {
  const e = l.entendimento;
  const v = e.versao;
  const [estado, tipo] = estadoVersao(l);
  const caixa = criar("details", { className: "entendimento" }, [
    criar("summary", { dados: { foco: `${l.lote}:entendimento` } }, [
      criar("span", { className: "entendimento-titulo", textContent: "Entendemos seu pedido assim" }),
      criar("span", { className: `selo ${tipo}`, textContent: estado }),
    ]),
  ]);
  caixa.open = abertos.get(l.lote) ?? !!l.espera_cliente;
  caixa.addEventListener("toggle", () => abertos.set(l.lote, caixa.open));
  const corpo = criar("div", { className: "entendimento-corpo" });
  caixa.append(corpo);
  const modo = e.revisao === "revisar" ? "você revisa antes de gerar" : "seguir com a adaptação";
  corpo.append(criar("p", { className: "fraco", textContent: `Versão ${v.numero} · ${modo}${v.editada ? " · texto mudado por você" : ""}` }));
  const nota = avisos.get(l.lote);
  if (nota) {
    avisos.delete(l.lote);
    const n = criar("div", { className: "aviso" });
    aviso(n, nota, "info");
    corpo.append(n);
  }

  // o que espera o cliente (nos dois modos): a falta de resposta não é aprovação
  if (e.decisoes.length) {
    const d = criar("div", { className: "aviso alerta" });
    aviso(
      d,
      [
        criar("p", {}, [criar("strong", { textContent: "Antes de gerar, precisamos da sua decisão:" })]),
        criar("ul", {}, e.decisoes.map((x) => criar("li", { textContent: x.foto ? `${x.foto}: ${x.motivo}` : x.motivo }))),
        criar("p", { textContent: "Sem a sua resposta, nada é gerado nem cobrado. Você pode seguir com esta adaptação, mudar o texto ou parar o pedido." }),
      ],
      "alerta",
    );
    corpo.append(d);
  } else if (e.revisao === "revisar" && !v.liberada) {
    const d = criar("div", { className: "aviso" });
    aviso(d, e.aprovar ? "Confira o texto de cada grupo de fotos e aprove para gerar. Sem a aprovação, nada é gerado nem cobrado." : "Estamos preparando o texto final do gerador. Quando ficar pronto, ele aparece aqui para você aprovar.", e.aprovar ? "alerta" : "info");
    corpo.append(d);
  }

  // o pedido original, separado do que entendemos
  const pediu: HTMLElement[] = [criar("dt", { textContent: "Você pediu" }), criar("dd", { textContent: e.original.texto })];
  if (v.anterior?.texto) pediu.push(criar("dt", { textContent: "Texto de antes" }), criar("dd", { className: "fraco", textContent: v.anterior.texto }));
  if (e.original.uso) pediu.push(criar("dt", { textContent: "Uso" }), criar("dd", { textContent: `${e.original.uso} (só contexto: não entra na cena)` }));
  corpo.append(criar("dl", { className: "pedido-original" }, pediu));

  // o que vamos fazer, por grupo de fotos com o mesmo texto
  const grupos = criar("div", { className: "secao-entendimento" }, [criar("h3", { textContent: "O que vamos fazer" })]);
  for (const g of e.grupos) {
    const itens: (Node | string)[] = [criar("p", { className: "rotulo-grupo" }, [criar("strong", { textContent: rotuloGrupo(e, g) }), g.exemplos?.length ? criar("span", { className: "fraco", textContent: ` · ex.: ${g.exemplos.join(", ")}` }) : null])];
    if (g.proprio) itens.push(criar("p", { className: "pedido", textContent: g.texto }));
    itens.push(...blocoTexto(g));
    if (g.estado === "recusado") {
      const r = criar("p", { className: "aviso erro" });
      aviso(r, `${g.recusado ?? "Este texto não pôde ser usado"}. As fotos deste texto não viram vídeo (sem custo).`, "erro");
      itens.push(r);
    } else if (g.estado === "preparando") itens.push(criar("p", { className: "fraco", textContent: "Preparando o texto final do gerador…" }));
    else if (g.prompt_en) itens.push(textoTecnico(e, g));
    if (l.editavel && g.id && g.estado !== "recusado") {
      const editar = criar("button", { type: "button", className: "botao pequeno", dados: { foco: `${l.lote}:editar:${g.id}` } }, [icone("editar"), "Mudar o texto"]);
      editar.addEventListener("click", () => editarTexto(l, g, recarregar, editar));
      itens.push(editar);
    }
    grupos.append(criar("div", { className: "grupo-entendido" }, itens));
  }
  corpo.append(grupos);

  // fotos com outro texto na hora de ir para o pod (a guarda final): cada uma com o motivo
  if (e.excecoes.length)
    corpo.append(
      criar("div", { className: "secao-entendimento" }, [
        criar("h3", { textContent: "Fotos com texto diferente do grupo" }),
        ...e.excecoes.map((x) => criar("div", { className: "grupo-entendido" }, [criar("p", {}, [criar("strong", { textContent: `${x.foto}: ` }), x.motivo]), textoTecnico(e, { prompt_en: x.prompt_en })])),
      ]),
    );
  const preservar = listaPreservar(e.preservar, "h3");
  if (preservar) corpo.append(preservar);
  corpo.append(listaLimites(e.limites));

  const s = e.especificacoes;
  const specs = [s.estilo, s.qualidade ? (RESOLUCAO[s.qualidade] ?? s.qualidade) : null, s.duracao ? `${s.duracao} s` : null, plural(s.videos, "vídeo", "vídeos")].filter(Boolean).join(" · ");
  const preco = s.preco_video ? ` · ${reais(s.preco_video)} por vídeo entregue${s.estimativa ? ` (até ${reais(s.estimativa)})` : ""}` : "";
  corpo.append(criar("p", { className: "fraco especificacoes", textContent: `${specs}${preco}` }));

  if (e.aprovar) {
    const msg = criar("div", { className: "aviso", hidden: true, attrs: { tabindex: "-1" } });
    const rotulo = e.decisoes.length ? "Seguir com esta adaptação" : "Aprovar e gerar";
    const aprovar = criar("button", { type: "button", className: "botao primario", dados: { foco: `${l.lote}:aprovar-versao` } }, [icone("ok"), rotulo]);
    aprovar.addEventListener("click", async () => {
      ocupado(aprovar, true);
      try {
        await api(`/api/lotes/${l.lote}/versao/aprovar`, { method: "POST", body: JSON.stringify({ numero: v.numero, hash: v.hash }) });
        avisos.set(l.lote, `Versão ${v.numero} aprovada: os vídeos entram na fila.`);
        anunciar(`Versão ${v.numero} aprovada. Os vídeos entram na fila.`);
        recarregar();
      } catch (err) {
        ocupado(aprovar, false);
        const mudou = err instanceof ErroApi && err.motivo === "versao";
        const texto = mudou
          ? "O pedido mudou depois que você abriu esta versão (outra aba, um texto novo ou uma tradução nova). Confira a versão nova antes de aprovar."
          : err instanceof Error
            ? err.message
            : String(err);
        anunciar(texto, true);
        if (mudou) {
          avisos.set(l.lote, texto);
          recarregar();
        } else {
          aviso(msg, texto, "erro");
          msg.focus();
        }
      }
    });
    corpo.append(criar("div", { className: "acoes-pedido" }, [aprovar]), msg);
  }
  return caixa;
}
