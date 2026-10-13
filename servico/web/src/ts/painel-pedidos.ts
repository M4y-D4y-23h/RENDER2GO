// Painel: revisão dos pedidos (nada vai para a GPU sem aprovação; ADR 0004) e o histórico, com o uso por IP e
// por dispositivo. No pedido em sequência, cada foto com texto próprio mostra esse texto embaixo da miniatura.
import { ESTADOS, NOTAS_SATISFACAO, anunciar, api, aviso, baixarJson, confirmar, criar, curto, dialogo, el, icone, marcarErro, ocupado, plural, quando, reconciliar, rotular, tempo, textoErro, urlSegura, vazio } from "./comum";
import { type Controle, controle, erroDeAcao, erroDeCarga, iniciarAbas, pode, semLinhas, tabela, tempoCurto, trocar } from "./painel-base";

// análise automática de cada foto (render2go_servico/pessoas.py); null = ainda analisando
type Analise = { pessoas?: number; rostos?: number; erro?: string } | null;
// filtro das fotos (render2go_servico/filtro_fotos.py), antes de qualquer pod: null = avaliando; a foto recusada ou
// inválida não vem como imagem (o dono a abre desfocada, na auditoria) e os vídeos dela não são gerados
type FiltroFoto = "ok" | "recusada" | "invalida" | null;
type DetalheFiltro = { nota?: number; nota_alta?: number; erro?: string; liberada_por?: string } | null;
type Autorizacao = { declarante: string; quando: number; ip: string | null; dispositivo: string | null; texto_versao: string };
type FotoPendente = {
  id: string;
  nome: string;
  url: string | null;
  analise?: Analise;
  texto?: string | null;
  filtro_c1?: string | null;
  filtro?: FiltroFoto;
  filtro_detalhe?: DetalheFiltro;
};
type Pendente = {
  lote: string;
  cliente: string;
  cliente_nome?: string | null;
  plano?: string | null;
  ip?: string | null;
  dispositivo?: string | null;
  autorizacao?: Autorizacao | null;
  modelo?: string;
  criado: number;
  pedido: { prompt: string; receita: string; qualidade: string; duracao: number | null; variacoes: number; consentimento?: boolean; textos_por_foto?: number };
  filtro_c1: string;
  analise_ligada?: boolean;
  fotos: FotoPendente[];
  // pedido grande (12/10/2026): a lista pode ser uma amostra; os totais são do pedido inteiro
  fotos_total?: number;
  fotos_amostra?: boolean;
  fotos_com_pessoas?: number;
  fotos_analisando?: number;
  fotos_recusadas?: number;
  fotos_filtrando?: number;
  fotos_com_texto?: number;
  textos?: { texto: string; fotos: number; filtro_c1: string | null }[];
};
type ItemHistorico = {
  lote: string;
  // por que os vídeos não saíram e o pedido parado (12/10/2026)
  nao_sairam?: { motivo: string; texto: string; videos: number }[];
  interrompido_em?: number | null;
  interrompido_por?: string | null;
  cliente: string;
  cliente_nome: string;
  plano: string;
  cliente_ativo: boolean;
  status: string;
  prompt: string;
  fotos: number;
  ip: string | null;
  criado: number;
  aprovado_em: number | null;
  aprovado_por: string | null;
  concluido_em: number | null;
  motivo: string | null;
  fotos_apagadas: number | null;
  dispositivo: string | null;
  fuso: string | null;
  declarante: string | null;
  autorizado_em: number | null;
  modelo?: string;
  prontos: number;
  total: number;
  barrados?: number;
  avaliacao?: number | null; // satisfação do cliente (1 a 5)
  // legenda por foto (ADR 0014): quantas fotos têm a descrição automática e as primeiras (só o painel)
  legendas?: { total: number; fotos: { nome: string; categoria: string | null; legenda: string | null }[] } | null;
};
type UsoIp = { ip: string; pedidos: number; fotos: number; fotos_teste: number; clientes: number; dispositivos: number; ultimo: number };
type UsoDispositivo = {
  dispositivo: string;
  ips: string[];
  clientes: string[];
  fusos: string[];
  impressoes?: string[];
  mesmo_navegador?: string[];
  pedidos: number;
  fotos_teste: number;
  ultimo: number;
};

const QUALIDADES: Record<string, string> = { rapida: "rápida (480p)", alta: "alta (720p)" };
const FILTRO_OK = "C1: ok";

function seloAnalise(a: Analise | undefined): HTMLElement {
  if (a == null) return criar("span", { className: "selo", textContent: "analisando…" });
  if (a.erro) return criar("span", { className: "selo erro", textContent: "análise indisponível: confira", title: a.erro });
  if (!a.pessoas && !a.rostos) return criar("span", { className: "selo", textContent: "sem pessoas detectadas" });
  const partes = [`contém pessoas (${Math.max(a.pessoas ?? 0, a.rostos ?? 0)})`];
  if (a.rostos) partes.push(plural(a.rostos, "rosto", "rostos"));
  return criar("span", { className: "selo pessoas" }, [icone("pessoa"), partes.join(" · ")]);
}

// resumo do pedido: pessoas detectadas × autorização marcada pelo cliente (ADR 0004, item 5)
function resumoPessoas(p: Pendente): HTMLElement | null {
  if (!p.analise_ligada) return null;
  const com = p.fotos_com_pessoas ?? p.fotos.filter((f) => f.analise && !f.analise.erro && ((f.analise.pessoas ?? 0) > 0 || (f.analise.rostos ?? 0) > 0)).length;
  const analisando = p.fotos_analisando ?? p.fotos.filter((f) => f.analise == null).length;
  const total = p.fotos_total ?? p.fotos.length;
  if (!com) {
    const texto = analisando ? `Analisando ${plural(analisando, "foto", "fotos")}…` : "Nenhuma pessoa detectada (a análise pode errar; confira as miniaturas).";
    return criar("p", { className: "fraco", textContent: texto });
  }
  const autorizado = p.pedido.consentimento === true;
  const caixa = criar("div");
  aviso(
    caixa,
    autorizado
      ? `Pessoas em ${com} de ${plural(total, "foto", "fotos")}. O cliente marcou ter autorização por escrito.`
      : `Pessoas em ${com} de ${plural(total, "foto", "fotos")} e o cliente NÃO marcou a autorização por escrito. Recuse, ou peça a autorização antes de aprovar.`,
    autorizado ? "alerta" : "erro",
  );
  return caixa;
}

// resumo do filtro das fotos: as que não vão virar vídeo e as que ainda esperam a avaliação
function resumoFiltro(p: Pendente): HTMLElement | null {
  const recusadas = p.fotos_recusadas ?? p.fotos.filter((f) => f.filtro === "recusada" || f.filtro === "invalida").length;
  const avaliando = p.fotos_filtrando ?? p.fotos.filter((f) => f.filtro == null).length;
  if (!recusadas && !avaliando) return null;
  const caixa = criar("div");
  if (recusadas) {
    aviso(
      caixa,
      `${plural(recusadas, "foto não passou", "fotos não passaram")} no filtro de imagem: os vídeos delas não serão gerados nem cobrados, e o cliente vê só que a foto não pôde ser usada.`,
      "alerta",
    );
  }
  if (avaliando) caixa.append(criar("p", { className: "fraco", textContent: `Filtro de imagem: avaliando ${plural(avaliando, "foto", "fotos")}…` }));
  return caixa;
}

const notaFiltro = (v: number | undefined) => (v === undefined ? "—" : v.toLocaleString("pt-BR", { maximumFractionDigits: 2 }));

/** No lugar da miniatura, a foto que o filtro das fotos não liberou: o resultado e, para o dono, abrir (desfocada, na
 * auditoria) e liberar (falso positivo, antes de aprovar). */
function fotoRecusada(f: FotoPendente, aoMudar: () => void): HTMLElement {
  const d = f.filtro_detalhe ?? {};
  const texto =
    f.filtro === "recusada"
      ? `Recusada pelo filtro de imagem · nota ${notaFiltro(d.nota)} · explícita ${notaFiltro(d.nota_alta)}`
      : `Arquivo inválido: ${d.erro ?? "não abre como foto"}`;
  const area = criar("div", { className: "area-quarentena" });
  const msg = criar("div", { className: "aviso", hidden: true });
  const botoes: HTMLElement[] = [];
  if (f.filtro === "recusada" && pode("GET /api/fotos/{foto_id}/recusada")) {
    const ver = criar("button", { type: "button", className: "botao pequeno", dados: { foco: `${f.id}:ver` } }, ["Ver a foto"]);
    ver.addEventListener("click", async () => {
      ocupado(ver, true);
      try {
        const r = await api<{ foto: string; expira_s: number }>(`api/fotos/${encodeURIComponent(f.id)}/recusada`);
        const src = urlSegura(r.foto);
        const midia = criar("div", { className: "quarentena desfocado" }, [src ? criar("img", { src, alt: `Foto recusada ${f.nome}` }) : null]);
        const mostrar = criar("button", { type: "button", className: "botao pequeno", dados: { foco: `${f.id}:desfoque` } }, ["Mostrar sem desfoque"]);
        mostrar.addEventListener("click", () => rotular(mostrar, midia.classList.toggle("desfocado") ? "Mostrar sem desfoque" : "Desfocar de novo"));
        area.replaceChildren(
          criar("p", {
            className: "fraco",
            textContent: "Cada abertura fica na auditoria; o link vale 10 min. Se aparecer criança ou adolescente, não baixe nem compartilhe: denuncie em new.safernet.org.br.",
          }),
          mostrar,
          midia,
        );
        ver.remove();
        mostrar.focus();
      } catch (e) {
        ocupado(ver, false);
        aviso(msg, textoErro(e), "erro");
      }
    });
    botoes.push(ver);
  }
  if (f.filtro === "recusada" && pode("POST /api/fotos/{foto_id}/liberar")) {
    const liberar = criar("button", { type: "button", className: "botao pequeno", dados: { foco: `${f.id}:liberar` } }, ["Liberar a foto"]);
    liberar.addEventListener("click", async () => {
      const sim = await confirmar({
        titulo: "Liberar a foto?",
        texto: [
          `O filtro de imagem recusou ${f.nome}. Liberada, ela vira vídeo como as outras quando o pedido for aprovado.`,
          "Libere só depois de ver a foto e ter certeza de que é um engano do filtro. Fica no registro do pedido e na auditoria.",
        ],
        confirmar: "Liberar",
      });
      if (!sim) return;
      ocupado(liberar, true);
      try {
        await api(`api/fotos/${encodeURIComponent(f.id)}/liberar`, { method: "POST" });
        anunciar(`Foto ${f.nome} liberada.`);
        aoMudar();
      } catch (e) {
        ocupado(liberar, false);
        aviso(msg, textoErro(e), "erro");
      }
    });
    botoes.push(liberar);
  }
  return criar("div", { className: "foto-recusada" }, [
    criar("span", { className: "nome-foto", textContent: f.nome }),
    criar("span", { textContent: texto }),
    ...botoes,
    msg,
    area,
  ]);
}

function meta(partes: (string | Node)[]): HTMLElement {
  return criar("p", { className: "meta" }, partes.map((x) => criar("span", {}, [x])));
}

function cartaoPendente(p: Pendente, aoMudar: () => void, imagens: Map<string, HTMLImageElement>): HTMLElement {
  const msg = criar("div", { className: "aviso", hidden: true });
  const fotos = criar("div", { className: "grade" });
  for (const f of p.fotos) {
    if (f.filtro === "recusada" || f.filtro === "invalida") {
      fotos.append(criar("figure", { className: "foto-revisao" }, [fotoRecusada(f, aoMudar)]));
      continue;
    }
    const chave = `${p.lote}/${f.nome}`;
    const url = urlSegura(f.url);
    // a mesma <img> volta a cada atualização: a URL assinada muda, mas a foto não é baixada de novo
    let img = imagens.get(chave);
    if (!img) {
      img = criar("img", { alt: f.nome, loading: "lazy", decoding: "async", width: 240, height: 240 });
      if (url) img.src = url;
      imagens.set(chave, img);
    }
    const link = criar("a", { href: url ?? "#", target: "_blank", rel: "noopener noreferrer", className: "foto", title: `Abrir ${f.nome} em outra aba` }, [
      img,
      p.analise_ligada ? seloAnalise(f.analise) : null,
      f.filtro == null ? criar("span", { className: "selo", textContent: "filtro de imagem: avaliando…" }) : null,
      f.filtro_detalhe?.liberada_por ? criar("span", { className: "selo", textContent: `liberada por ${f.filtro_detalhe.liberada_por}` }) : null,
    ]);
    const barrado = f.texto && f.filtro_c1 && f.filtro_c1 !== FILTRO_OK;
    fotos.append(
      criar("figure", { className: `foto-revisao${f.texto ? " com-texto" : ""}` }, [
        link,
        f.texto
          ? criar("figcaption", { className: "texto-foto" }, [criar("span", { className: "visualmente-oculto", textContent: `Texto próprio de ${f.nome}: ` }), criar("q", { textContent: f.texto })])
          : null,
        barrado ? criar("span", { className: "selo erro", textContent: `texto barrado: ${f.filtro_c1}` }) : null,
      ]),
    );
  }
  const pedido = p.pedido;
  const a = p.autorizacao;
  const comTexto = p.fotos.filter((f) => f.texto);
  const total = p.fotos_total ?? p.fotos.length;
  const nComTexto = p.fotos_com_texto ?? comTexto.length;
  const aprovar = criar("button", { type: "button", className: "botao sucesso", dados: { acao: "aprovar", foco: `${p.lote}:aprovar` } }, [icone("ok"), "Aprovar"]);
  const recusar = criar("button", { type: "button", className: "botao perigo", dados: { acao: "recusar", foco: `${p.lote}:recusar` } }, [icone("bloquear"), "Recusar"]);
  const motivo = criar("input", { id: `motivo-${p.lote}`, placeholder: "Motivo da recusa (o cliente vê)", maxLength: 500, dados: { foco: `${p.lote}:motivo` } });
  motivo.setAttribute("aria-label", "Motivo da recusa (o cliente vê)");
  const agir = async (acao: "aprovar" | "recusar", botao: HTMLButtonElement) => {
    aprovar.disabled = recusar.disabled = true;
    ocupado(botao, true);
    try {
      const corpo = acao === "recusar" ? JSON.stringify({ motivo: motivo.value.trim() }) : undefined;
      await api(`api/lotes/${encodeURIComponent(p.lote)}/${acao}`, { method: "POST", body: corpo });
      anunciar(acao === "aprovar" ? "Pedido aprovado e enviado para a fila." : "Pedido recusado.");
      aoMudar();
    } catch (e) {
      erroDeAcao(e, msg);
      anunciar(textoErro(e), true);
      aprovar.disabled = recusar.disabled = false;
      ocupado(botao, false);
    }
  };
  aprovar.addEventListener("click", () => void agir("aprovar", aprovar));
  recusar.addEventListener("click", () => {
    if (motivo.value.trim().length < 3) {
      marcarErro(motivo, true);
      aviso(msg, "Escreva o motivo da recusa (o cliente vê).", "erro");
      motivo.focus();
      return;
    }
    void agir("recusar", recusar);
  });
  motivo.addEventListener("input", () => marcarErro(motivo, false));
  const textos = nComTexto
    ? (() => {
        const caixa = criar("div");
        const distintos = p.textos?.length ?? new Set(comTexto.map((f) => f.texto)).size;
        aviso(
          caixa,
          `Pedido em sequência: ${plural(nComTexto, "foto tem texto próprio", "fotos têm texto próprio")} (${plural(distintos, "texto diferente", "textos diferentes")}), ${p.fotos_amostra ? "listados abaixo" : "embaixo de cada miniatura"}; ${plural(total - nComTexto, "foto segue", "fotos seguem")} o texto do pedido.`,
          "info",
        );
        if (p.fotos_amostra && p.textos?.length) {
          // pedido grande: cada texto próprio uma vez, com quantas fotos o usam e o filtro de conteúdo
          caixa.append(
            criar(
              "ul",
              { className: "textos-revisao" },
              p.textos.map((t) =>
                criar("li", {}, [
                  criar("q", { textContent: t.texto }),
                  ` · ${plural(t.fotos, "foto", "fotos")}`,
                  t.filtro_c1 && t.filtro_c1 !== FILTRO_OK ? criar("span", { className: "selo erro", textContent: `texto barrado: ${t.filtro_c1}` }) : null,
                ]),
              ),
            ),
          );
        }
        return caixa;
      })()
    : null;
  const amostra = p.fotos_amostra
    ? criar("p", { className: "fraco", textContent: `Mostrando ${plural(p.fotos.length, "foto", "fotos")} de ${total}: as primeiras e as que a análise marcou (pessoas, rostos ou erro).` })
    : null;
  return criar("article", { className: "cartao pendente surgir", attrs: { "aria-label": `Pedido de ${p.cliente_nome ?? p.cliente}` } }, [
    criar("div", { className: "lote-cab" }, [
      criar("span", { className: "selo estado-aguardando_aprovacao" }, [icone("relogio"), "Em análise"]),
      criar("strong", { textContent: p.cliente_nome ?? p.cliente }),
      p.plano ? criar("span", { className: "selo", textContent: p.plano }) : null,
      criar("span", { className: "fraco" }, [plural(total, "foto", "fotos"), " · ", tempo(p.criado)]),
    ]),
    criar("p", { className: "pedido", textContent: pedido.prompt }),
    meta([
      p.modelo ?? "Wan 2.2",
      pedido.receita,
      QUALIDADES[pedido.qualidade] ?? pedido.qualidade,
      pedido.duracao ? `${pedido.duracao} s` : "duração padrão",
      plural(pedido.variacoes, "variação", "variações"),
      `filtro ${p.filtro_c1 || FILTRO_OK}`, // o motivo já vem como "C1: ..."
      `IP ${p.ip ?? "—"}`,
    ]),
    criar("p", {
      className: "fraco autorizacao",
      textContent: a
        ? `Autorização marcada em ${quando(a.quando)} · dispositivo ${curto(a.dispositivo)} · IP ${a.ip ?? "—"} · texto ${a.texto_versao}`
        : "Autorização das pessoas das fotos: não marcada.",
    }),
    resumoFiltro(p),
    resumoPessoas(p),
    textos,
    amostra,
    fotos,
    criar("div", { className: "decisao" }, [aprovar, motivo, recusar]),
    msg,
  ]);
}

// por que os vídeos não saíram (12/10/2026): o rótulo curto do painel para cada motivo (render2go_servico.motivos)
const MOTIVOS_CURTOS: Record<string, string> = {
  filtro_imagem: "barrados pelo filtro de imagem",
  conferencia: "reprovados na conferência",
  texto: "com o texto recusado pelo filtro",
  traducao: "sem tradução do texto",
  foto: "com problema na foto",
  foto_nao_aceita: "com a foto recusada no envio",
  interrompido_cliente: "cancelados: o cliente parou",
  interrompido_painel: "cancelados: a equipe parou",
  prazo_fotos: "cancelados: 7 dias das fotos",
  tecnico: "com erro técnico",
};
type Falha = {
  job: string;
  arquivo: string;
  status: string;
  motivo: string;
  erro: string | null;
  tentativas: number;
  nsfw: number | null;
  nsfw_alto?: number | null;
  deriva_cor: number | null;
  salto_cor: number | null;
  similaridade: number | null;
  quarentena: boolean;
  // legenda por foto (ADR 0014): a descrição automática da foto e a categoria (só o painel, nunca o cliente)
  legenda?: string | null;
  categoria?: string | null;
};

const numeroOu = (v: number | null, casas = 2) => (v === null || v === undefined ? "—" : v.toLocaleString("pt-BR", { maximumFractionDigits: casas }));

/** O vídeo barrado na quarentena (só o dono, ADR 0005): desfocado até a pessoa pedir; cada abertura fica na auditoria. */
async function verQuarentena(f: Falha, alvo: HTMLElement, botao: HTMLButtonElement): Promise<void> {
  ocupado(botao, true);
  try {
    const q = await api<{ video: string; folha: string; expira_s: number }>(`api/jobs/${encodeURIComponent(f.job)}/quarentena`);
    const video = urlSegura(q.video);
    const folha = urlSegura(q.folha);
    const midia = criar("div", { className: "quarentena desfocado" }, [
      video ? criar("video", { src: video, controls: true, preload: "metadata", attrs: { "aria-label": `Vídeo barrado ${f.arquivo}` } }) : null,
      folha ? criar("img", { src: folha, alt: `Quadros do vídeo barrado ${f.arquivo}`, loading: "lazy" }) : null,
    ]);
    const mostrar = criar("button", { type: "button", className: "botao pequeno", dados: { foco: `${f.job}:desfoque` } }, ["Mostrar sem desfoque"]);
    mostrar.addEventListener("click", () => rotular(mostrar, midia.classList.toggle("desfocado") ? "Mostrar sem desfoque" : "Desfocar de novo"));
    alvo.replaceChildren(
      criar("p", {
        className: "fraco",
        textContent:
          "O filtro considerou este vídeo impróprio. Cada abertura fica na auditoria; os links valem 10 min e o vídeo é apagado 7 dias depois de barrado. Se aparecer criança ou adolescente, não baixe nem compartilhe: denuncie em new.safernet.org.br e procure orientação jurídica.",
      }),
      mostrar,
      midia,
    );
    botao.remove();
    mostrar.focus();
  } catch (e) {
    ocupado(botao, false);
    aviso(alvo, textoErro(e), "erro");
  }
}

/** Cada vídeo do pedido que não saiu: o motivo que o cliente vê, o texto técnico e as notas dos filtros. */
async function janelaFalhas(x: ItemHistorico, origem: HTMLElement): Promise<void> {
  const { d, corpo } = dialogo(`Vídeos que não saíram · ${x.cliente_nome}`, "falhas");
  d.addEventListener("close", () => {
    d.remove();
    origem.focus();
  });
  const lista = criar("div", { className: "lista-falhas" }, [criar("p", { className: "fraco", textContent: "Carregando…" })]);
  corpo.append(lista);
  d.showModal();
  try {
    const r = await api<{ videos: Falha[] }>(`api/lotes/${encodeURIComponent(x.lote)}/falhas`);
    lista.replaceChildren(
      ...r.videos.map((f) => {
        const area = criar("div", { className: "area-quarentena" });
        const notas = [plural(f.tentativas, "tentativa", "tentativas"), `filtro de imagem ${numeroOu(f.nsfw)} (explícita ${numeroOu(f.nsfw_alto ?? null)})`, `1º quadro ${numeroOu(f.similaridade, 3)}`, `deriva ${numeroOu(f.deriva_cor, 1)}`, `salto ${numeroOu(f.salto_cor, 1)}`];
        const itens: (Node | null)[] = [
          criar("strong", { textContent: f.arquivo }),
          criar("p", { textContent: f.motivo }),
          criar("p", { className: "fraco", textContent: `${f.erro ?? "sem detalhe"} · ${notas.join(" · ")}` }),
          f.legenda ? criar("p", { className: "fraco", textContent: `Legenda da foto (${f.categoria ?? "sem categoria"}): ${f.legenda}` }) : null,
        ];
        if (f.quarentena && pode("GET /api/jobs/{job_id}/quarentena")) {
          const ver = criar("button", { type: "button", className: "botao pequeno", dados: { foco: `${f.job}:quarentena` } }, [icone("play"), "Ver o vídeo barrado"]);
          ver.addEventListener("click", () => void verQuarentena(f, area, ver));
          itens.push(ver);
        }
        itens.push(area);
        return criar("article", { className: "falha" }, itens);
      }),
    );
  } catch (e) {
    aviso(lista, textoErro(e), "erro");
  }
}

/** Legenda por foto (ADR 0014): a descrição automática de cada foto, que o tradutor recebeu (só o painel). */
function legendasDoPedido(x: ItemHistorico): HTMLElement | null {
  const l = x.legendas;
  if (!l?.total) return null;
  const itens = l.fotos.map((f) => criar("li", { textContent: `${f.nome} (${f.categoria ?? "sem categoria"}): ${f.legenda ?? "—"}` }));
  const resto = l.total - l.fotos.length;
  if (resto > 0) itens.push(criar("li", { className: "fraco", textContent: `e mais ${plural(resto, "foto", "fotos")} (no registro de auditoria)` }));
  return criar("details", { className: "legendas" }, [criar("summary", { textContent: `Legendas automáticas das fotos (${l.total})` }), criar("ul", {}, itens)]);
}

function itemHistorico(x: ItemHistorico, aoMudar: () => void): HTMLElement {
  const msg = criar("div", { className: "aviso", hidden: true });
  const detalhes: string[] = [];
  if (x.aprovado_em) detalhes.push(`aprovado em ${quando(x.aprovado_em)} por ${x.aprovado_por ?? "?"}`);
  if (x.total) detalhes.push(`${x.prontos} de ${plural(x.total, "vídeo pronto", "vídeos prontos")}`);
  if (x.concluido_em) detalhes.push(`fechado em ${quando(x.concluido_em)}`);
  if (x.fotos_apagadas) detalhes.push("fotos de entrada já apagadas");
  detalhes.push(x.autorizado_em ? `autorização marcada em ${quando(x.autorizado_em)}` : "sem autorização marcada");
  if (x.modelo) detalhes.push(`modelo ${x.modelo}`);
  if (x.avaliacao) detalhes.push(`satisfação do cliente: ${NOTAS_SATISFACAO[x.avaliacao - 1] ?? x.avaliacao}`);
  if (x.interrompido_em) detalhes.push(`parado em ${quando(x.interrompido_em)} por ${x.interrompido_por === "cliente" ? "o cliente" : (x.interrompido_por ?? "?")}`);
  const registro = criar("button", { type: "button", className: "botao pequeno", dados: { foco: `${x.lote}:auditoria` } }, [icone("baixar"), "Baixar registro de auditoria"]);
  registro.addEventListener("click", async () => {
    ocupado(registro, true);
    try {
      baixarJson(await api<unknown>(`api/lotes/${encodeURIComponent(x.lote)}/auditoria`), `auditoria-${x.lote}.json`);
    } catch (e) {
      erroDeAcao(e, msg);
    } finally {
      ocupado(registro, false);
    }
  });
  const acoes = criar("div", { className: "acoes" }, [registro]);
  // filtro de imagem nos quadros (ADR 0005): o vídeo não foi entregue; o registro de auditoria tem a nota
  if (x.barrados) acoes.append(criar("span", { className: "selo erro" }, [icone("alerta"), `${plural(x.barrados, "vídeo barrado", "vídeos barrados")} pelo filtro de imagem`]));
  const naoSairam = (x.nao_sairam ?? []).reduce((n, m) => n + m.videos, 0);
  if (naoSairam && pode("GET /api/lotes/{lote_id}/falhas")) {
    const ver = criar("button", { type: "button", className: "botao pequeno", dados: { foco: `${x.lote}:falhas` } }, [icone("alerta"), `Ver os que não saíram (${naoSairam})`]);
    ver.addEventListener("click", () => void janelaFalhas(x, ver));
    acoes.append(ver);
  }
  if ((x.status === "aprovado" || x.status === "processando") && !x.interrompido_em && pode("POST /api/lotes/{lote_id}/interromper")) {
    const parar = criar("button", { type: "button", className: "botao pequeno perigo", dados: { foco: `${x.lote}:parar` } }, [icone("bloquear"), "Parar o pedido"]);
    parar.addEventListener("click", async () => {
      const ok = await confirmar({
        titulo: "Parar este pedido?",
        texto: [
          `Os vídeos que faltam do pedido de ${x.cliente_nome} são cancelados, sem custo; os prontos ficam com o cliente.`,
          "O vídeo que já está sendo gerado termina. O cliente vê que a equipe parou o pedido.",
        ],
        confirmar: "Parar o pedido",
        perigo: true,
      });
      if (!ok) return;
      ocupado(parar, true);
      try {
        const r = await api<{ cancelados: number }>(`api/lotes/${encodeURIComponent(x.lote)}/interromper`, { method: "POST" });
        anunciar(`Pedido parado: ${plural(r.cancelados, "vídeo cancelado", "vídeos cancelados")}.`);
        aoMudar();
      } catch (e) {
        ocupado(parar, false);
        erroDeAcao(e, msg);
      }
    });
    acoes.append(parar);
  }
  if (!x.cliente_ativo) acoes.append(criar("span", { className: "selo erro" }, [icone("bloquear"), "Conta desativada"]));
  else if (pode("POST /api/clientes/{cliente_id}/desativar")) {
    const botao = criar("button", { type: "button", className: "botao pequeno perigo", dados: { foco: `${x.lote}:desativar` } }, [icone("bloquear"), "Desativar esta conta"]);
    botao.addEventListener("click", async () => {
      const ok = await confirmar({
        titulo: "Desativar a conta?",
        texto: `A conta de ${x.cliente_nome} deixa de valer na hora: as sessões caem e a pessoa não entra mais (nem por e-mail, nem por código).`,
        confirmar: "Desativar",
        perigo: true,
      });
      if (!ok) return;
      ocupado(botao, true);
      try {
        await api(`api/clientes/${encodeURIComponent(x.cliente)}/desativar`, { method: "POST" });
        anunciar(`Conta de ${x.cliente_nome} desativada.`);
        aoMudar();
      } catch (e) {
        ocupado(botao, false);
        erroDeAcao(e, msg);
      }
    });
    acoes.append(botao);
  }
  const linhas: (Node | null)[] = [
    criar("div", { className: "lote-cab" }, [
      criar("span", { className: `selo estado-${x.status}`, textContent: ESTADOS[x.status] ?? x.status }),
      criar("strong", { textContent: x.cliente_nome }),
      criar("span", { className: "selo", textContent: x.plano }),
      criar("span", { className: "fraco" }, [tempo(x.criado)]),
    ]),
    criar("p", { className: "pedido", textContent: x.prompt }),
    meta([plural(x.fotos, "foto", "fotos"), `IP ${x.ip ?? "—"}`, `dispositivo ${curto(x.dispositivo)}`]),
    criar("p", { className: "fraco", textContent: detalhes.join(" · ") }),
    x.nao_sairam?.length
      ? criar("p", { className: "fraco", textContent: `Não saíram: ${x.nao_sairam.map((m) => `${m.videos} ${MOTIVOS_CURTOS[m.motivo] ?? m.texto}`).join(" · ")}` })
      : null,
    legendasDoPedido(x),
  ];
  if (x.motivo) {
    const m = criar("div");
    aviso(m, `Motivo: ${x.motivo}`, "erro");
    linhas.push(m);
  }
  linhas.push(acoes, msg);
  return criar("article", { className: "cartao hist" }, linhas);
}

function tabelaIps(ips: UsoIp[]): HTMLElement {
  if (!ips.length) return semLinhas("Nenhum pedido com IP registrado ainda.");
  return tabela(
    [["IP", false], ["Fotos", true], ["Fotos de teste", true], ["Pedidos", true], ["Clientes", true], ["Dispositivos", true], ["Último pedido", false]],
    ips.map((u) => ({ celulas: [u.ip, u.fotos, u.fotos_teste, u.pedidos, u.clientes, u.dispositivos, tempoCurto(u.ultimo)] })),
    "Uso por IP",
  );
}

function tabelaDispositivos(ds: UsoDispositivo[]): HTMLElement {
  if (!ds.length) return semLinhas("Nenhum pedido com dispositivo registrado ainda.");
  return tabela(
    [["Dispositivo", false], ["IPs", false], ["Contas", true], ["Fotos de teste", true], ["Fusos", false], ["Sinais", false], ["Último pedido", false]],
    ds.map((d) => {
      const sinais = [
        d.ips.length > 1 ? `trocou de IP (${d.ips.length})` : "",
        d.clientes.length > 1 ? `várias contas (${d.clientes.length})` : "",
        d.fusos.length > 1 ? "fusos diferentes" : "",
        d.mesmo_navegador?.length ? `mesmo navegador de ${d.mesmo_navegador.map(curto).join(", ")}` : "",
      ].filter(Boolean);
      return {
        classe: sinais.length ? "sinal" : "",
        celulas: [curto(d.dispositivo), d.ips.join(", "), d.clientes.length, d.fotos_teste, d.fusos.join(", ") || "—", sinais.length ? `⚠ ${sinais.join(" · ")}` : "—", tempoCurto(d.ultimo)],
      };
    }),
    "Dispositivos",
  );
}

/** Revisão: os pedidos que esperam aprovação. `aoContar` recebe quantos são (o número no menu e no título). */
export function criarRevisao(ctx: { aoContar: (n: number) => void; aoMudar: () => void }): Controle {
  const alvo = el("pendentes");
  const imagens = new Map<string, HTMLImageElement>();
  const carregar = async () => {
    const lotes = await api<Pendente[]>("api/lotes");
    const vivos = new Set(lotes.flatMap((p) => p.fotos.map((f) => `${p.lote}/${f.nome}`)));
    for (const chave of imagens.keys()) if (!vivos.has(chave)) imagens.delete(chave);
    reconciliar(
      alvo,
      lotes.map((p) => ({
        chave: p.lote,
        assinatura: JSON.stringify([p.fotos.map((f) => [f.nome, f.analise, f.texto, f.filtro_c1, f.filtro, f.filtro_detalhe]), p.autorizacao, p.pedido, p.filtro_c1]),
        criar: () => cartaoPendente(p, mudou, imagens),
      })),
      () => vazio("Nada aguardando revisão.", "Os pedidos novos aparecem aqui assim que as fotos chegam.", "ok"),
    );
    ctx.aoContar(lotes.length);
  };
  const c = controle({ regiao: alvo, carregar });
  const mudou = () => {
    void c.atualizar();
    ctx.aoMudar(); // o histórico, a visão geral e o número no menu também mudam
  };
  return c;
}

export function criarHistorico(): Controle {
  const lista = el("historico-lista");
  const filtros = el("filtros-historico");
  let grupo = "todos";
  let geracao = 0; // só a resposta do pedido mais recente vale (troca de filtro no meio de uma atualização)
  iniciarAbas(el("abas-historico"));
  const carregar = async () => {
    const minha = ++geracao;
    const r = await api<{ lotes: ItemHistorico[]; ips: UsoIp[]; dispositivos: UsoDispositivo[] }>(`api/historico?grupo=${encodeURIComponent(grupo)}`);
    if (minha !== geracao) return;
    reconciliar(
      lista,
      r.lotes.map((x) => ({ chave: x.lote, assinatura: JSON.stringify([x, pode("POST /api/clientes/{cliente_id}/desativar")]), criar: () => itemHistorico(x, () => void c.atualizar()) })),
      () => vazio("Nenhum pedido neste filtro.", "Troque o filtro para ver outros pedidos.", "pedidos"),
    );
    trocar(el("ips"), [tabelaIps(r.ips)], JSON.stringify(r.ips));
    trocar(el("dispositivos"), [tabelaDispositivos(r.dispositivos)], JSON.stringify(r.dispositivos));
  };
  const c = controle({ regiao: lista, ocupado: el("historico-dados"), carregar });
  filtros.addEventListener("click", (ev) => {
    const b = (ev.target as Element).closest<HTMLButtonElement>("button[data-grupo]");
    if (!b || b.dataset.grupo === grupo) return;
    grupo = b.dataset.grupo ?? "todos";
    for (const x of Array.from(filtros.querySelectorAll("button"))) x.setAttribute("aria-pressed", String(x === b));
    lista.setAttribute("aria-busy", "true");
    carregar()
      .catch((e) => erroDeCarga(e, null, () => undefined))
      .finally(() => lista.setAttribute("aria-busy", "false"));
  });
  return c;
}
