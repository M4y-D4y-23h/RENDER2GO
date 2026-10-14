// Tela "Montagem" (/montagem, ADR 0015): junta de 2 a 10 vídeos prontos da conta (os dos últimos 7 dias, de
// /api/geracoes) num vídeo só, na ordem escolhida, com corte seco ou esmaecimento, título, preço e chamada por cima,
// logo e trilha opcionais. O servidor monta (ffmpeg num contêiner próprio, fora da API); a lista das montagens se
// atualiza sozinha enquanto alguma não terminou. Quem ainda não pode usar (RENDER2GO_MONTAGEM) vê o que ela faz e o
// "Tenho interesse". Tudo chega de uma vez, no lugar do esqueleto (CLS zero).
import { type ItemLista, anunciar, api, aviso, confirmar, contador, criar, el, icone, ocupado, plural, reais, reconciliar, repetir, tempo, textoErro, urlSegura, vazio } from "./comum";
import { botaoInteresse } from "./interesse";
import { abrirVideo } from "./player";

type Formato = { id: string; nome: string; largura: number; altura: number };
type Trilha = { id: string; titulo: string; autor: string; licenca: string };
type Montagem = {
  id: string;
  status: string;
  criado: number;
  concluido_em: number | null;
  formato: string;
  transicao: string;
  videos: number;
  textos: Record<string, string>;
  logo: boolean;
  trilha: string | null;
  duracao: number | null;
  preco: number | null;
  excluido: boolean;
  motivo?: string;
  arquivo?: string;
  url?: string;
  baixar?: string;
};
type Info = {
  disponivel: boolean;
  preco: number | null;
  plano: string;
  formatos: Formato[];
  transicoes: string[];
  limites: { videos: [number, number]; textos: Record<string, number>; logo_bytes: number; logo_tipos: string[]; dias: number; abertas: number };
  trilhas: Trilha[];
  itens: Montagem[];
};
type Video = { id: string; arquivo: string; gerado_em: number; duracao: number | null; resolucao: string | null; texto: string | null; url: string; baixar: string };

const ESTADOS: Record<string, string> = {
  aguardando_envio: "Enviando o logo",
  aguardando_logo: "Conferindo o logo",
  pendente: "Na fila",
  em_andamento: "Montando",
  ok: "Pronta",
  erro: "Não saiu",
  cancelado: "Cancelada",
};
const ABERTAS = new Set(["aguardando_envio", "aguardando_logo", "pendente", "em_andamento"]);
const TRANSICOES: Record<string, string> = { corte: "Corte seco", fade: "Esmaecer (0,5 s entre os vídeos)" };
const CAMPOS: { id: string; rotulo: string; exemplo: string }[] = [
  { id: "titulo", rotulo: "Título (no alto)", exemplo: "Ex.: Promoção de inverno" },
  { id: "preco", rotulo: "Preço (destaque embaixo)", exemplo: "Ex.: R$ 49,90" },
  { id: "chamada", rotulo: "Chamada (embaixo)", exemplo: "Ex.: Peça já pelo WhatsApp" },
];

export function criarMontagem(ctx: { erro: (e: unknown, alvo: HTMLElement) => void }): { abrir: () => Promise<void> } {
  const alvo = el("montagem-dados");
  const msgTela = el("montagem-msg");
  let info: Info | null = null;
  let videos: Video[] = [];
  let escolhidos: string[] = [];
  let lista: HTMLUListElement | null = null;
  let ordem: HTMLOListElement | null = null;
  let resumoEscolha: HTMLElement | null = null;
  let geracao = 0;

  const nomeVideo = (id: string) => videos.find((v) => v.id === id)?.arquivo ?? id;

  // -- a lista das montagens (a página atualiza sozinha enquanto alguma não terminou) ---------------------

  const excluir = async (m: Montagem, botao: HTMLButtonElement) => {
    const ok = await confirmar({
      titulo: "Excluir esta montagem?",
      texto: [`${m.arquivo ?? "A montagem"} sai do armazenamento e não dá para recuperar depois.`, "Os vídeos usados nela continuam nos Logs, e o extrato fica como está."],
      confirmar: "Excluir a montagem",
      perigo: true,
    });
    if (!ok) return;
    ocupado(botao, true);
    try {
      await api(`/api/montagens/${encodeURIComponent(m.id)}/excluir`, { method: "POST" });
      anunciar("Montagem excluída.");
      await atualizar();
      el("titulo-montagens").focus();
    } catch (e) {
      ocupado(botao, false);
      aviso(msgTela, textoErro(e), "erro");
    }
  };

  const item = (m: Montagem): HTMLElement => {
    // a montagem excluída pelo cliente continua na lista (com o que foi pedido), sem o "Pronta" nem os links
    const classe = m.excluido ? "info" : m.status === "ok" ? "ok" : m.status === "erro" || m.status === "cancelado" ? "erro" : "info";
    const estado = criar("span", { className: `selo ${classe}`, textContent: m.excluido ? "Excluída por você" : (ESTADOS[m.status] ?? m.status) });
    const sobre = [
      plural(m.videos, "vídeo", "vídeos"),
      info?.formatos.find((f) => f.id === m.formato)?.nome ?? m.formato,
      TRANSICOES[m.transicao]?.split(" (")[0] ?? m.transicao,
      m.duracao ? `${Math.round(m.duracao)} s` : null,
    ]
      .filter(Boolean)
      .join(" · ");
    const partes: (HTMLElement | null)[] = [criar("p", { className: "linha-detalhe" }, [estado, " ", sobre, " · pedida em ", tempo(m.criado)])];
    const textos = ["titulo", "preco", "chamada"].map((k) => m.textos[k]).filter(Boolean);
    if (textos.length) partes.push(criar("p", { className: "texto-log" }, [criar("q", { textContent: textos.join(" · ") })]));
    if (m.motivo) partes.push(criar("p", { className: "motivo-montagem", textContent: `Motivo: ${m.motivo}. Nada foi cobrado.` }));
    if (m.status === "ok" && m.url) {
      const assistir = criar("button", { type: "button", className: "assistir", dados: { foco: `montagem:assistir:${m.id}` } }, [
        criar("span", { className: "play" }, [icone("play", "cheio")]),
        criar("span", { className: "nome", textContent: m.arquivo ?? "montagem" }),
      ]);
      assistir.setAttribute("aria-label", `Assistir ${m.arquivo ?? "a montagem"}`);
      assistir.addEventListener("click", () => abrirVideo({ arquivo: m.arquivo ?? "montagem", url: m.url, baixar: m.baixar }, assistir));
      const acoes: HTMLElement[] = [assistir];
      const baixar = urlSegura(m.baixar);
      if (baixar) {
        const a = criar("a", { href: baixar, className: "botao pequeno baixar", rel: "noopener", dados: { foco: `montagem:baixar:${m.id}` } }, [icone("baixar"), "Baixar"]);
        a.setAttribute("aria-label", `Baixar ${m.arquivo ?? "a montagem"}`);
        acoes.push(a);
      }
      const lixo = criar("button", { type: "button", className: "botao pequeno perigo", dados: { foco: `montagem:excluir:${m.id}` } }, [icone("lixo"), "Excluir"]);
      lixo.setAttribute("aria-label", `Excluir ${m.arquivo ?? "a montagem"}`);
      lixo.addEventListener("click", () => void excluir(m, lixo));
      acoes.push(lixo);
      partes.unshift(criar("div", { className: "video" }, acoes));
    }
    return criar("li", { className: "geracao montagem" }, partes);
  };

  const desenharLista = () => {
    if (!lista || !info) return;
    const itens: ItemLista[] = info.itens.map((m) => ({ chave: m.id, assinatura: `${m.status}:${m.excluido}:${m.url ?? ""}`, criar: () => item(m) }));
    reconciliar(lista, itens, () =>
      criar("li", { className: "sem-itens" }, [vazio("Nenhuma montagem nos últimos 7 dias", "As montagens que você pedir aparecem aqui, com o andamento e os links para assistir e baixar.", "editar")]),
    );
  };

  const atualizar = async (): Promise<boolean> => {
    const r = await api<Info>("/api/montagens");
    if (info) info.itens = r.itens;
    desenharLista();
    return r.itens.some((m) => ABERTAS.has(m.status));
  };
  const vigia = repetir(atualizar, 5000);

  // -- a escolha dos vídeos e da ordem -------------------------------------------------------------------

  const desenharOrdem = () => {
    if (!ordem || !resumoEscolha || !info) return;
    const [min, max] = info.limites.videos;
    resumoEscolha.textContent = escolhidos.length
      ? `${plural(escolhidos.length, "vídeo escolhido", "vídeos escolhidos")} (de ${min} a ${max}).`
      : `Nenhum vídeo escolhido ainda (de ${min} a ${max}).`;
    ordem.replaceChildren(
      ...escolhidos.map((id, i) => {
        const nome = nomeVideo(id);
        const mover = (passo: number, rotulo: string, chave: string) => {
          const b = criar("button", { type: "button", className: "botao pequeno fantasma", textContent: rotulo, dados: { foco: `ordem:${chave}:${id}` } });
          b.setAttribute("aria-label", `${rotulo} ${nome}`);
          const destino = i + passo;
          b.disabled = destino < 0 || destino >= escolhidos.length;
          b.addEventListener("click", () => {
            [escolhidos[i], escolhidos[destino]] = [escolhidos[destino], escolhidos[i]];
            desenharOrdem();
            anunciar(`${nome} agora é o ${destino + 1}º.`);
            const mesmo = ordem?.querySelector<HTMLButtonElement>(`[data-foco="ordem:${chave}:${id}"]`);
            (mesmo && !mesmo.disabled ? mesmo : ordem?.querySelector<HTMLElement>(`[data-foco^="ordem:"][data-foco$=":${id}"]:not(:disabled)`))?.focus();
          });
          return b;
        };
        const tirar = criar("button", { type: "button", className: "botao pequeno fantasma", textContent: "Tirar", dados: { foco: `ordem:tirar:${id}` } });
        tirar.setAttribute("aria-label", `Tirar ${nome} da montagem`);
        tirar.addEventListener("click", () => {
          marcar(id, false);
          el<HTMLInputElement>(`montagem-video-${id}`).focus();
        });
        return criar("li", {}, [criar("span", { className: "nome", textContent: nome }), criar("span", { className: "acoes-ordem" }, [mover(-1, "Subir", "subir"), mover(1, "Descer", "descer"), tirar])]);
      }),
    );
  };

  const marcar = (id: string, sim: boolean) => {
    const caixa = document.getElementById(`montagem-video-${id}`) as HTMLInputElement | null;
    if (sim && !escolhidos.includes(id)) {
      if (info && escolhidos.length >= info.limites.videos[1]) {
        if (caixa) caixa.checked = false;
        anunciar(`No máximo ${info.limites.videos[1]} vídeos por montagem.`);
        return;
      }
      escolhidos.push(id);
    } else if (!sim) escolhidos = escolhidos.filter((x) => x !== id);
    if (caixa) caixa.checked = sim;
    desenharOrdem();
  };

  const escolhaVideos = (): HTMLElement => {
    if (!videos.length)
      return vazio("Nenhum vídeo pronto nos últimos 7 dias", "A montagem usa os vídeos que você já gerou. Faça um pedido em Pedidos e volte aqui quando os vídeos ficarem prontos.", "video");
    return criar(
      "ul",
      { className: "escolha-videos" },
      videos.map((v) => {
        const caixa = criar("input", { type: "checkbox", id: `montagem-video-${v.id}`, value: v.id });
        caixa.addEventListener("change", () => marcar(v.id, caixa.checked));
        const detalhe = [v.duracao ? `${v.duracao} s` : null, v.resolucao].filter(Boolean).join(" · ");
        const assistir = criar("button", { type: "button", className: "botao pequeno fantasma", dados: { foco: `montagem:previa:${v.id}` } }, [icone("play"), "Ver"]);
        assistir.setAttribute("aria-label", `Ver ${v.arquivo}`);
        assistir.addEventListener("click", () => abrirVideo(v, assistir));
        return criar("li", {}, [
          caixa,
          criar("label", { htmlFor: caixa.id }, [criar("span", { className: "nome", textContent: v.arquivo }), criar("span", { className: "fraco" }, [detalhe ? `${detalhe} · ` : "", tempo(v.gerado_em)])]),
          assistir,
        ]);
      }),
    );
  };

  // -- o formulário --------------------------------------------------------------------------------------

  const grupoRadios = (nome: string, legenda: string, opcoes: [string, string][], marcado: string): HTMLElement =>
    criar("fieldset", { className: "opcoes-montagem" }, [
      criar("legend", { textContent: legenda }),
      ...opcoes.map(([valor, rotulo]) => {
        const r = criar("input", { type: "radio", name: nome, value: valor, id: `montagem-${nome}-${valor.replace(":", "x")}`, checked: valor === marcado });
        return criar("label", { className: "opcao", htmlFor: r.id }, [r, criar("span", { textContent: rotulo })]);
      }),
    ]);

  const formulario = (d: Info): HTMLElement => {
    const msg = criar("p", { className: "aviso", hidden: true, id: "montagem-form-msg" });
    msg.setAttribute("role", "status");
    ordem = criar("ol", { className: "ordem-montagem", id: "montagem-ordem" });
    resumoEscolha = criar("p", { className: "fraco", id: "montagem-escolha" });
    const campos = CAMPOS.map((c) => {
      const max = d.limites.textos[c.id] ?? 40;
      const campo = criar("input", { type: "text", id: `montagem-${c.id}`, name: c.id, maxLength: max, placeholder: c.exemplo, autocomplete: "off" });
      const conta = criar("span", { className: "contador", attrs: { "aria-hidden": "true" } });
      contador(campo, conta);
      return criar("div", { className: "campo" }, [criar("label", { htmlFor: campo.id }, [c.rotulo, " ", criar("span", { className: "fraco", textContent: "(opcional)" })]), campo, conta]);
    });
    const logo = criar("input", { type: "file", id: "montagem-logo", accept: d.limites.logo_tipos.join(",") });
    const campoLogo = criar("div", { className: "campo" }, [
      criar("label", { htmlFor: logo.id }, ["Logo ", criar("span", { className: "fraco", textContent: `(opcional; JPG, PNG ou WebP, até ${Math.round(d.limites.logo_bytes / 1048576)} MB)` })]),
      logo,
      criar("p", { className: "dica", textContent: "Fica no canto de cima. Fundo transparente (PNG) fica melhor. O logo passa pela mesma conferência das fotos." }),
    ]);
    const extras: HTMLElement[] = [];
    if (d.trilhas.length) {
      const sel = criar("select", { id: "montagem-trilha" }, [criar("option", { value: "", textContent: "Sem música" }), ...d.trilhas.map((t) => criar("option", { value: t.id, textContent: `${t.titulo} (${t.autor}, ${t.licenca})` }))]);
      extras.push(criar("div", { className: "campo" }, [criar("label", { htmlFor: sel.id, textContent: "Trilha" }), sel]));
    }
    const custo = criar("p", {
      className: "custo-montagem",
      id: "montagem-custo",
      textContent: d.preco ? `Cada montagem custa ${reais(d.preco)}, cobrada só quando ficar pronta (se não sair, nada é cobrado).` : "Sem custo no seu plano.",
    });
    const enviar = criar("button", { type: "submit", className: "botao primario", id: "montagem-enviar" }, [icone("editar"), "Montar vídeo"]);
    const form = criar("form", { id: "form-montagem", className: "cartao pilha", noValidate: true }, [
      criar("h2", { id: "t-nova-montagem", textContent: "Nova montagem" }),
      criar("fieldset", { className: "videos-montagem" }, [
        criar("legend", { textContent: "Vídeos (marque na ordem; dá para mudar a ordem depois)" }),
        escolhaVideos(),
        resumoEscolha,
        ordem,
      ]),
      grupoRadios("formato", "Formato", d.formatos.map((f) => [f.id, `${f.id} · ${f.nome}`]), "9:16"),
      grupoRadios("transicao", "Entre um vídeo e outro", d.transicoes.map((t) => [t, TRANSICOES[t] ?? t]), "fade"),
      criar("fieldset", { className: "textos-montagem" }, [criar("legend", { textContent: "Textos por cima" }), ...campos]),
      campoLogo,
      ...extras,
      custo,
      msg,
      criar("div", { className: "acoes" }, [enviar]),
    ]);
    form.setAttribute("aria-labelledby", "t-nova-montagem");
    form.addEventListener("submit", (ev) => {
      ev.preventDefault();
      void pedir(form, enviar, msg, logo, d);
    });
    desenharOrdem();
    return form;
  };

  const pedir = async (form: HTMLFormElement, botao: HTMLButtonElement, msg: HTMLElement, logo: HTMLInputElement, d: Info) => {
    const [min, max] = d.limites.videos;
    if (escolhidos.length < min || escolhidos.length > max) {
      aviso(msg, `Escolha de ${min} a ${max} vídeos para a montagem.`, "erro");
      (document.getElementById(`montagem-video-${videos[0]?.id}`) ?? msg).focus();
      return;
    }
    const arquivo = logo.files?.[0] ?? null;
    if (arquivo && (!d.limites.logo_tipos.includes(arquivo.type) || arquivo.size > d.limites.logo_bytes)) {
      aviso(msg, `O logo precisa ser JPG, PNG ou WebP, com até ${Math.round(d.limites.logo_bytes / 1048576)} MB.`, "erro");
      logo.focus();
      return;
    }
    const valor = (nome: string) => (form.querySelector<HTMLInputElement>(`input[name="${nome}"]:checked`)?.value ?? "");
    const textos: Record<string, string> = {};
    for (const c of CAMPOS) textos[c.id] = el<HTMLInputElement>(`montagem-${c.id}`).value.trim();
    const trilha = document.getElementById("montagem-trilha") as HTMLSelectElement | null;
    ocupado(botao, true, "Enviando…");
    aviso(msg, "");
    try {
      const r = await api<{ montagem: string; status: string; envio: { url: string; tipo: string } | null }>("/api/montagens", {
        method: "POST",
        body: JSON.stringify({
          videos: escolhidos,
          formato: valor("formato"),
          transicao: valor("transicao"),
          textos,
          trilha: trilha?.value || null,
          logo: arquivo ? { tipo: arquivo.type, bytes: arquivo.size } : null,
        }),
      });
      if (r.envio && arquivo) {
        const url = urlSegura(r.envio.url);
        if (!url) throw new Error("endereço de envio do logo inválido");
        const resp = await fetch(url, { method: "PUT", body: arquivo, headers: { "Content-Type": r.envio.tipo } }).catch(() => null);
        if (!resp?.ok) throw new Error("o logo não foi enviado; confira a internet e peça de novo (a montagem não será cobrada)");
        await api(`/api/montagens/${encodeURIComponent(r.montagem)}/enviado`, { method: "POST" });
      }
      for (const id of [...escolhidos]) marcar(id, false);
      form.reset();
      for (const c of CAMPOS) el<HTMLInputElement>(`montagem-${c.id}`).dispatchEvent(new Event("input"));
      aviso(msg, "Montagem na fila. Ela aparece em “Suas montagens” e fica pronta em poucos minutos.", "ok");
      anunciar("Montagem na fila.");
      vigia.agora();
    } catch (e) {
      aviso(msg, textoErro(e), "erro");
    } finally {
      ocupado(botao, false, "Montar vídeo");
    }
  };

  // -- a tela --------------------------------------------------------------------------------------------

  const emBreve = (d: Info): HTMLElement => {
    const msg = criar("p", { className: "aviso", hidden: true, id: "montagem-interesse-msg" });
    const lugar = criar("div", { className: "interesse-lugar", id: "interesse-montagem" }, [botaoInteresse("recurso:montagem", "Montagem", (t) => aviso(msg, t, "erro"))]);
    const motivo = d.plano === "teste" ? "A montagem é para quem já tem vídeos prontos no plano pago." : "A montagem ainda não está disponível no seu plano.";
    return criar("section", { className: "cartao pilha em-breve-cartao", attrs: { "aria-labelledby": "t-montagem-breve" } }, [
      criar("h2", { id: "t-montagem-breve", textContent: "Em breve no seu plano" }),
      criar("p", { textContent: motivo }),
      criar("ul", { className: "lista-recursos" }, [
        criar("li", { textContent: "Junte de 2 a 10 vídeos prontos num vídeo só, na ordem que quiser." }),
        criar("li", { textContent: "Título, preço e chamada por cima, nas posições certas para as redes." }),
        criar("li", { textContent: "O seu logo no canto e uma trilha de uma biblioteca com licença conferida." }),
        criar("li", { textContent: "Vertical (Stories e Reels), quadrado (feed) ou horizontal (YouTube), em 720p." }),
      ]),
      msg,
      lugar,
    ]);
  };

  const desenhar = (d: Info) => {
    info = d;
    lista = criar("ul", { className: "geracoes montagens", id: "montagens", attrs: { "aria-labelledby": "titulo-montagens" } });
    const suas = criar("section", { className: "pilha", attrs: { "aria-labelledby": "titulo-montagens" } }, [criar("h2", { id: "titulo-montagens", tabIndex: -1, textContent: "Suas montagens" }), lista]);
    const partes: HTMLElement[] = [d.disponivel ? formulario(d) : emBreve(d)];
    if (d.disponivel || d.itens.length) partes.push(suas);
    alvo.replaceChildren(...partes);
    alvo.setAttribute("aria-busy", "false");
    if (d.disponivel || d.itens.length) desenharLista();
  };

  return {
    abrir: async () => {
      const minha = ++geracao;
      try {
        const [d, g] = await Promise.all([api<Info>("/api/montagens"), api<{ itens: Video[] }>("/api/geracoes?horas=168").catch(() => ({ itens: [] as Video[] }))]);
        if (minha !== geracao) return;
        msgTela.hidden = true;
        videos = g.itens;
        escolhidos = escolhidos.filter((id) => videos.some((v) => v.id === id));
        desenhar(d);
        if (d.itens.some((m) => ABERTAS.has(m.status))) vigia.agora();
      } catch (e) {
        if (minha !== geracao) return;
        alvo.setAttribute("aria-busy", "false");
        ctx.erro(e, msgTela);
      }
    },
  };
}
