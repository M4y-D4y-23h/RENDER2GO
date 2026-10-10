// Pedido em sequência (aba "Sequência (texto por foto)"): o texto padrão vale para todas as fotos, e algumas podem
// ganhar um texto próprio (ex.: 100 fotos com "apresente o cardápio" e, em 5 delas, "o café vira um café com
// leite"). A API recebe o texto em fotos[].prompt (até 600 caracteres, no máximo 100 textos diferentes por pedido)
// e confere cada um como confere o padrão (filtro de conteúdo e estilos).
//
// Até 1.000 fotos por pedido: a grade mostra 40 por página, com miniaturas pequenas (JPEG de ~10 KB feito no
// navegador, uma de cada vez e só das fotos que aparecem), para a memória não crescer com o tamanho do pedido.
import { type ItemLista, anunciar, aviso, contador, criar, dialogo, el, marcarErro, plural, reconciliar } from "./comum";

export const MAX_TEXTO = 600;
export const MAX_TEXTOS = 100;
const POR_PAGINA = 40;
const LARGURA_MINIATURA = 200;

export type Sequencia = {
  /** A aba "Sequência" está escolhida (na outra, todas as fotos seguem o texto único). */
  ativa: () => boolean;
  /** O texto próprio da foto, só com a aba "Sequência" escolhida. */
  textoDe: (chave: string) => string | undefined;
  quantos: () => number;
  /** As fotos escolhidas mudaram (chegou, saiu ou limpou): textos e marcas de quem saiu vão junto. */
  fotosMudaram: () => void;
  /** Depois do envio: sem textos, sem marcas e sem miniaturas. */
  limpar: () => void;
  /** Revisão dos textos antes de enviar (true: enviar). Sem textos próprios, nem pergunta. */
  revisar: (padrao: string) => Promise<boolean>;
};

const juntar = (t: string) => t.replace(/\s+/g, " ").trim();

/** Miniatura pequena feita no navegador (a foto original nunca é desenhada inteira); null se não abrir. */
async function reduzir(arq: File): Promise<string | null> {
  try {
    const bmp = await createImageBitmap(arq, { resizeWidth: LARGURA_MINIATURA, resizeQuality: "medium" });
    const c = criar("canvas", { width: bmp.width, height: bmp.height });
    c.getContext("2d")?.drawImage(bmp, 0, 0);
    bmp.close();
    const blob = await new Promise<Blob | null>((ok) => c.toBlob(ok, "image/jpeg", 0.75));
    return blob ? URL.createObjectURL(blob) : null;
  } catch {
    return null;
  }
}

export function criarSequencia(opcoes: {
  chaves: () => string[];
  arquivo: (chave: string) => File | undefined;
  aoMudarModo: (sequencia: boolean) => void;
}): Sequencia {
  const abaMesmo = el<HTMLButtonElement>("aba-mesmo");
  const abaSequencia = el<HTMLButtonElement>("aba-sequencia");
  const painel = el("painel-texto");
  const caixa = el("sequencia");
  const grade = el<HTMLUListElement>("seq-grade");
  const contagem = el("seq-contagem");
  const nota = el("nota-textos");
  const textos = new Map<string, string>(); // chave da foto → texto próprio
  const marcadas = new Set<string>();
  const urls = new Map<string, string>(); // chave → miniatura (blob:)
  const imgs = new Map<string, HTMLImageElement>(); // as da página que está na tela
  const fila: string[] = [];
  let gerando = false;
  let modo = false;
  let pagina = 0;
  let filtro: "todas" | "com" | "sem" = "todas";

  const nome = (chave: string) => chave.split("/").pop() ?? chave;
  const existentes = () => new Set(opcoes.chaves());

  // -- miniaturas: uma de cada vez, só das fotos da página -----------------------------------------
  const gerarMiniaturas = async () => {
    if (gerando) return;
    gerando = true;
    try {
      for (let chave = fila.shift(); chave !== undefined; chave = fila.shift()) {
        if (urls.has(chave) || !imgs.has(chave)) continue;
        const arq = opcoes.arquivo(chave);
        if (!arq) continue;
        const url = await reduzir(arq);
        if (!url) continue;
        if (!opcoes.arquivo(chave)) {
          URL.revokeObjectURL(url); // a foto saiu enquanto a miniatura era feita
          continue;
        }
        urls.set(chave, url);
        const img = imgs.get(chave);
        if (img) img.src = url;
      }
    } finally {
      gerando = false;
    }
  };

  // -- contagem e textos -----------------------------------------------------------------------------
  const distintos = () => new Set(textos.values()).size;

  const textoContagem = (): string => {
    const total = opcoes.chaves().length;
    const com = textos.size;
    if (!total) return "Escolha as fotos no passo 1. Depois, marque aqui as que devem seguir outro texto.";
    if (!com) return total === 1 ? "A foto segue o texto padrão." : `Nenhuma foto com texto próprio: as ${total.toLocaleString("pt-BR")} seguem o texto padrão.`;
    const resto = total - com;
    const outras = resto === 0 ? "nenhuma segue o texto padrão" : resto === 1 ? "a outra segue o texto padrão" : `as outras ${resto.toLocaleString("pt-BR")} seguem o texto padrão`;
    return `${plural(com, "foto com texto próprio", "fotos com texto próprio")}; ${outras} · ${plural(distintos(), "texto diferente", "textos diferentes")} (máximo ${MAX_TEXTOS})`;
  };

  const atualizarNota = () => {
    const n = textos.size;
    nota.hidden = modo || !n;
    nota.textContent = n ? `${plural(n, "foto tem texto próprio", "fotos têm texto próprio")} na aba “Sequência”. Nesta aba, todas seguem o texto abaixo.` : "";
  };

  // -- grade ---------------------------------------------------------------------------------------
  const filtradas = () => {
    const todas = opcoes.chaves();
    if (filtro === "com") return todas.filter((k) => textos.has(k));
    if (filtro === "sem") return todas.filter((k) => !textos.has(k));
    return todas;
  };

  let idTexto = 0;
  const itemFoto = (chave: string): HTMLElement => {
    const texto = textos.get(chave);
    const marcada = marcadas.has(chave);
    const caixaMarcar = criar("input", { type: "checkbox", checked: marcada, dados: { foco: `seq:${chave}` } });
    const img = criar("img", { alt: "", width: LARGURA_MINIATURA, height: 150, decoding: "async" });
    const url = urls.get(chave);
    if (url) img.src = url;
    const li = criar("li", { className: `seq-foto${texto ? " tem-texto" : ""}${marcada ? " marcada" : ""}` }, [
      criar("label", { className: "seq-marcar" }, [caixaMarcar, criar("span", { className: "seq-imagem" }, [img]), criar("span", { className: "seq-nome", textContent: nome(chave) })]),
    ]);
    if (texto) {
      const id = `seq-texto-${++idTexto}`;
      li.append(criar("p", { className: "seq-texto", id }, [criar("span", { className: "visualmente-oculto", textContent: "Texto próprio: " }), texto]));
      caixaMarcar.setAttribute("aria-describedby", id);
    }
    caixaMarcar.addEventListener("change", () => {
      if (caixaMarcar.checked) marcadas.add(chave);
      else marcadas.delete(chave);
      li.classList.toggle("marcada", caixaMarcar.checked);
      li.dataset.assinatura = JSON.stringify([caixaMarcar.checked, texto ?? null]); // o item já está certo: fica
      atualizarAcoes();
    });
    return li;
  };

  const atualizarAcoes = () => {
    const n = marcadas.size;
    el("seq-marcadas").textContent = n ? plural(n, "foto marcada", "fotos marcadas") : "Nenhuma foto marcada";
    for (const id of ["seq-escrever", "seq-padrao", "seq-desmarcar"]) el<HTMLButtonElement>(id).disabled = !n;
    const naPagina = paginaAtual();
    const todas = naPagina.length > 0 && naPagina.every((k) => marcadas.has(k));
    el("seq-marcar-pagina").textContent = todas ? "Desmarcar as desta página" : "Marcar todas desta página";
    el<HTMLButtonElement>("seq-marcar-pagina").disabled = !naPagina.length;
  };

  const paginaAtual = () => {
    const lista = filtradas();
    const paginas = Math.max(1, Math.ceil(lista.length / POR_PAGINA));
    pagina = Math.min(pagina, paginas - 1);
    return lista.slice(pagina * POR_PAGINA, (pagina + 1) * POR_PAGINA);
  };

  const desenhar = () => {
    contagem.textContent = textoContagem();
    atualizarNota();
    if (!modo) return;
    const lista = filtradas();
    const paginas = Math.max(1, Math.ceil(lista.length / POR_PAGINA));
    const itens = paginaAtual();
    imgs.clear();
    fila.length = 0;
    const lista_: ItemLista[] = itens.map((chave) => ({
      chave,
      assinatura: JSON.stringify([marcadas.has(chave), textos.get(chave) ?? null]),
      criar: () => itemFoto(chave),
    }));
    reconciliar(grade, lista_, () =>
      criar("li", { className: "seq-vazio fraco", textContent: opcoes.chaves().length ? "Nenhuma foto neste filtro." : "As fotos escolhidas aparecem aqui." }),
    );
    // as imagens da página (novas ou que ficaram iguais): as que ainda não têm miniatura entram na fila
    for (const li of Array.from(grade.children) as HTMLElement[]) {
      const chave = li.dataset.chave;
      const img = li.querySelector("img");
      if (chave && img) {
        imgs.set(chave, img);
        if (!urls.has(chave)) fila.push(chave);
      }
    }
    el("seq-pagina").textContent = lista.length ? `Página ${pagina + 1} de ${paginas}` : "";
    el<HTMLButtonElement>("seq-anterior").disabled = pagina === 0;
    el<HTMLButtonElement>("seq-proxima").disabled = pagina >= paginas - 1;
    el("seq-paginas").hidden = paginas <= 1;
    atualizarAcoes();
    void gerarMiniaturas();
  };

  // -- abas ----------------------------------------------------------------------------------------
  const escolherModo = (sequencia: boolean, focar = false) => {
    modo = sequencia;
    abaMesmo.setAttribute("aria-selected", String(!sequencia));
    abaSequencia.setAttribute("aria-selected", String(sequencia));
    abaMesmo.tabIndex = sequencia ? -1 : 0;
    abaSequencia.tabIndex = sequencia ? 0 : -1;
    painel.setAttribute("aria-labelledby", sequencia ? "aba-sequencia" : "aba-mesmo");
    caixa.hidden = !sequencia;
    opcoes.aoMudarModo(sequencia);
    desenhar();
    if (focar) (sequencia ? abaSequencia : abaMesmo).focus();
  };
  abaMesmo.addEventListener("click", () => escolherModo(false));
  abaSequencia.addEventListener("click", () => escolherModo(true));
  el("abas-texto").addEventListener("keydown", (ev) => {
    const teclas: Record<string, boolean | undefined> = { ArrowLeft: false, ArrowRight: true, Home: false, End: true };
    if (!(ev.key in teclas)) return;
    ev.preventDefault();
    const sequencia = ev.key === "ArrowLeft" || ev.key === "ArrowRight" ? !modo : (teclas[ev.key] as boolean);
    escolherModo(sequencia, true);
  });

  // -- barra de filtros, páginas e ações -------------------------------------------------------------
  const filtros = el("seq-filtros");
  filtros.addEventListener("click", (ev) => {
    const b = (ev.target as Element).closest<HTMLButtonElement>("button[data-filtro]");
    if (!b) return;
    filtro = b.dataset.filtro as typeof filtro;
    pagina = 0;
    for (const x of Array.from(filtros.querySelectorAll("button"))) x.setAttribute("aria-pressed", String(x === b));
    desenhar();
  });
  el("seq-anterior").addEventListener("click", () => {
    pagina = Math.max(0, pagina - 1);
    desenhar();
    anunciar(el("seq-pagina").textContent ?? "");
  });
  el("seq-proxima").addEventListener("click", () => {
    pagina += 1;
    desenhar();
    anunciar(el("seq-pagina").textContent ?? "");
  });
  el("seq-marcar-pagina").addEventListener("click", () => {
    const naPagina = paginaAtual();
    const todas = naPagina.every((k) => marcadas.has(k));
    for (const k of naPagina) {
      if (todas) marcadas.delete(k);
      else marcadas.add(k);
    }
    desenhar();
    anunciar(todas ? "Fotos desta página desmarcadas." : `${plural(naPagina.length, "foto marcada", "fotos marcadas")} nesta página.`);
  });
  el("seq-desmarcar").addEventListener("click", () => {
    marcadas.clear();
    desenhar();
    anunciar("Nenhuma foto marcada.");
    el("seq-marcar-pagina").focus();
  });
  el("seq-padrao").addEventListener("click", () => {
    const alvo = [...marcadas];
    for (const k of alvo) textos.delete(k);
    marcadas.clear();
    desenhar();
    anunciar(`${plural(alvo.length, "foto voltou", "fotos voltaram")} para o texto padrão. ${textoContagem()}`);
    contagem.focus();
  });
  el("seq-escrever").addEventListener("click", () => escrever());

  /** Janela do texto próprio das fotos marcadas (com os textos já escritos à mão, para repetir). */
  const escrever = () => {
    const alvo = opcoes.chaves().filter((k) => marcadas.has(k));
    if (!alvo.length) return;
    const atuais = new Set(alvo.map((k) => textos.get(k) ?? ""));
    const { d, corpo } = dialogo(alvo.length === 1 ? `Texto próprio de ${nome(alvo[0])}` : `Texto próprio de ${plural(alvo.length, "foto", "fotos")}`, "texto-proprio");
    const base = d.getAttribute("aria-labelledby");
    const campo = criar("textarea", { id: `${base}-texto`, maxLength: MAX_TEXTO, spellcheck: true, autocapitalize: "sentences", required: true });
    campo.value = atuais.size === 1 ? [...atuais][0] : "";
    const cont = criar("span", { className: "contador", id: `${base}-contador` });
    campo.setAttribute("aria-describedby", `${base}-dica ${base}-contador`);
    const atualizar = contador(campo, cont);
    const msg = criar("div", { className: "aviso", hidden: true, attrs: { role: "alert" } });
    const nomes = alvo.slice(0, 3).map(nome).join(", ") + (alvo.length > 3 ? ` e mais ${alvo.length - 3}` : "");
    const itens: HTMLElement[] = [
      criar("p", { textContent: `Vale para ${alvo.length === 1 ? "a foto" : "as fotos"} ${nomes}. As outras fotos seguem o texto padrão.` }),
      criar("div", { className: "campo" }, [
        criar("label", { htmlFor: campo.id, textContent: "O que deve acontecer nesta foto?" }),
        campo,
        criar("div", { className: "linha-campo" }, [
          criar("p", { className: "dica", id: `${base}-dica`, textContent: "Do seu jeito, como no texto padrão: o que acontece nesta foto. Ex.: “o café vira um café com leite”." }),
          cont,
        ]),
      ]),
    ];
    const outros = [...new Set(textos.values())].filter((t) => !atuais.has(t)).slice(-10).reverse();
    if (outros.length) {
      const botoes = outros.map((t) => {
        const b = criar("button", { type: "button", className: "botao pequeno texto-pronto", title: t, textContent: t.length > 70 ? `${t.slice(0, 70)}…` : t });
        b.setAttribute("aria-label", `Usar o texto: ${t}`);
        b.addEventListener("click", () => {
          campo.value = t;
          atualizar();
          marcarErro(campo, false);
          campo.focus();
        });
        return b;
      });
      itens.push(criar("details", { className: "textos-prontos" }, [criar("summary", { textContent: "Usar um texto já escrito" }), criar("div", { className: "acoes" }, botoes)]));
    }
    const cancelar = criar("button", { type: "button", className: "botao", textContent: "Cancelar" });
    const salvar = criar("button", { type: "submit", className: "botao primario", textContent: "Salvar o texto" });
    const form = criar("form", { noValidate: true, className: "pilha" }, [...itens, msg, criar("div", { className: "dialogo-acoes" }, [cancelar, salvar])]);
    corpo.append(form);
    campo.addEventListener("input", () => marcarErro(campo, false));
    cancelar.addEventListener("click", () => d.close());
    let salvo = false;
    form.addEventListener("submit", (ev) => {
      ev.preventDefault();
      const t = juntar(campo.value);
      if (t.length < 3) {
        marcarErro(campo, true);
        aviso(msg, "Escreva o que deve acontecer (pelo menos 3 letras).", "erro");
        campo.focus();
        return;
      }
      const depois = new Set([...textos].filter(([k]) => !marcadas.has(k)).map(([, v]) => v));
      depois.add(t);
      if (depois.size > MAX_TEXTOS) {
        marcarErro(campo, true);
        aviso(msg, `No máximo ${MAX_TEXTOS} textos diferentes por pedido. Use um dos textos já escritos.`, "erro");
        campo.focus();
        return;
      }
      for (const k of alvo) textos.set(k, t);
      marcadas.clear();
      salvo = true;
      d.close();
    });
    d.addEventListener("close", () => {
      d.remove();
      if (!salvo) {
        el<HTMLButtonElement>("seq-escrever").focus();
        return;
      }
      desenhar();
      anunciar(`Texto próprio em ${plural(alvo.length, "foto", "fotos")}. ${textoContagem()}`);
      // o foco volta para a primeira foto que ganhou o texto, se ela está nesta página
      const primeira = Array.from(grade.querySelectorAll<HTMLInputElement>("input[type=checkbox]")).find((x) => x.dataset.foco === `seq:${alvo[0]}`);
      (primeira ?? contagem).focus();
    });
    d.showModal();
    campo.focus();
  };

  return {
    ativa: () => modo,
    textoDe: (chave) => (modo ? textos.get(chave) : undefined),
    quantos: () => (modo ? textos.size : 0),
    fotosMudaram: () => {
      const vivas = existentes();
      for (const k of [...textos.keys()]) if (!vivas.has(k)) textos.delete(k);
      for (const k of [...marcadas]) if (!vivas.has(k)) marcadas.delete(k);
      for (const [k, url] of [...urls]) {
        if (!vivas.has(k)) {
          URL.revokeObjectURL(url);
          urls.delete(k);
        }
      }
      desenhar();
    },
    limpar: () => {
      textos.clear();
      marcadas.clear();
      for (const url of urls.values()) URL.revokeObjectURL(url);
      urls.clear();
      pagina = 0;
      desenhar();
    },
    revisar: (padrao) => {
      if (!modo || !textos.size) return Promise.resolve(true);
      const origem = document.activeElement as HTMLElement | null;
      const grupos = new Map<string, string[]>();
      for (const k of opcoes.chaves()) {
        const t = textos.get(k);
        if (t) grupos.set(t, [...(grupos.get(t) ?? []), k]);
      }
      const semTexto = opcoes.chaves().length - textos.size;
      const { d, corpo } = dialogo("Revise os textos do pedido", "revisao-textos");
      const lista = [...grupos].map(([t, ks]) =>
        criar("li", {}, [
          criar("q", { textContent: t }),
          criar("p", { className: "fraco", textContent: `${plural(ks.length, "foto", "fotos")}: ${ks.slice(0, 5).map(nome).join(", ")}${ks.length > 5 ? ` e mais ${ks.length - 5}` : ""}` }),
        ]),
      );
      const nao = criar("button", { type: "button", className: "botao", textContent: "Voltar e editar" });
      const sim = criar("button", { type: "button", className: "botao primario", textContent: "Enviar pedido" });
      corpo.append(
        criar("section", { className: "pilha" }, [
          criar("h3", { textContent: "Texto padrão" }),
          criar("p", { className: "fraco", textContent: semTexto ? `${plural(semTexto, "foto segue", "fotos seguem")} este texto:` : "Nenhuma foto segue este texto (todas têm texto próprio)." }),
          criar("q", { className: "texto-padrao", textContent: juntar(padrao) }),
        ]),
        criar("section", { className: "pilha" }, [criar("h3", { textContent: `Textos próprios (${plural(grupos.size, "texto", "textos")})` }), criar("ul", { className: "lista-textos" }, lista)]),
        criar("div", { className: "dialogo-acoes" }, [nao, sim]),
      );
      nao.addEventListener("click", () => d.close("nao"));
      sim.addEventListener("click", () => d.close("sim"));
      return new Promise((resolver) => {
        d.addEventListener("close", () => {
          resolver(d.returnValue === "sim");
          d.remove();
          if (d.returnValue !== "sim" && origem?.isConnected) origem.focus();
        });
        d.showModal();
        sim.focus();
      });
    },
  };
}
