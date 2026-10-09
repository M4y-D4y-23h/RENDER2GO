// Página do cliente (/): entrada com código, novo pedido e acompanhamento (ADR 0003, fase 3).
// As fotos nunca passam pela API: vão direto ao armazenamento (R2) por URLs assinadas, uma por foto;
// os vídeos voltam por links assinados que expiram em 24 h.
import {
  ESTADOS,
  ErroApi,
  type ItemLista,
  anunciar,
  api,
  aviso,
  bytes,
  contador,
  criar,
  dialogo,
  ehIcone,
  el,
  hora,
  icone,
  marcarConectado,
  marcarErro,
  minutos,
  ocupado,
  plural,
  reais,
  reconciliar,
  repetir,
  rotular,
  tempo,
  textoErro,
  urlSegura,
  vazio,
} from "./comum";
import { type Conta, abrirConta, resumoConta } from "./conta";
import { iniciarCookies } from "./cookies";
import { CHAVE_CONECTADO, iniciarEntrada } from "./entrada";
import { abrirPix } from "./pix";

type Receita = { id: string; label: string; nicho: string; acao: string; duracao?: number; exige_consentimento: boolean };
// plano do cliente: durações aceitas; no teste gratuito, os limites que a API impõe (a página só os mostra);
// no pré-pago, o preço por segundo de cada qualidade (centavos) e o saldo
type Plano = {
  nome: string;
  qualidades?: string[];
  duracoes?: number[];
  max_fotos?: number;
  disponivel?: boolean;
  motivo?: string | null;
  precos?: Record<string, number>;
  saldo?: number;
};
type Movimento = { quando: number; tipo: string; valor: number; lote_id: string | null; descricao: string | null };
type Saldo = { pre_pago: boolean; saldo?: number; precos?: Record<string, number>; extrato?: Movimento[] };
type Modelo = { id: string; nome: string; descricao: string; disponivel: boolean; finalidade?: string; melhor_para?: string; licenca?: string };
// tela antes do pedido: o que o cliente quer criar (render2go_servico/modelos.py)
type Previsto = { nome: string; melhor_para?: string; licenca?: string };
type Finalidade = {
  id: string;
  titulo: string;
  tipo: string;
  descricao: string;
  entrada: string;
  icone: string;
  destaque: boolean;
  disponivel: boolean;
  modelos: Modelo[];
  previstos: Previsto[];
};
type Receitas = {
  plano?: Plano;
  pagamento?: boolean;
  conta?: { email: string | null; plano: string };
  autorizacao?: { versao: string; texto: string };
  modelos?: Modelo[];
  modelo_padrao?: string;
  finalidades?: Finalidade[];
  entradas?: Record<string, string>;
  finalidade_padrao?: string;
  interesses?: string[];
  receitas: Receita[];
  formatos: string[];
  // tamanho do vídeo (largura, altura) por formato e qualidade, a regra do formato automático
  // (largura/altura abaixo da 1ª = vertical, acima da 2ª = horizontal) e a ampliação que vira aviso
  saida?: Record<string, Record<string, [number, number]>>;
  proporcao_auto?: [number, number];
  ampliacao_aviso?: number;
  qualidades: string[];
  limites: { fotos: number; bytes_foto: number; tipos: string[] };
};
type Video = { arquivo: string; status: string; aviso: string | null; url?: string; baixar?: string };
// previsão em segundos; o cliente vê só o tempo, não as etapas internas
type Previsao = { fase: "analise" | "fila" | "gerando"; inicio_max_s?: number; inicio_s?: number; fim_s?: number } | null;
type Lote = {
  lote: string;
  status: string;
  criado: number;
  fotos: number;
  motivo: string | null;
  pedido: { prompt: string; qualidade: string; duracao: number | null };
  prontos: number;
  total: number;
  videos: Video[];
  modelo?: string;
  previsao?: Previsao;
  cobranca?: { cobrado: number; devolvido: number } | null;
};
type Envio = { original: string; url: string; tipo: string };

const EM_ANDAMENTO = new Set(["aguardando_aprovacao", "aprovado", "processando"]);
const ENVIOS_SIMULTANEOS = 4;
const MAX_PREVIA = 60; // miniaturas desenhadas; acima disso, um quadro "+N"
const NICHOS: Record<string, string> = { geral: "Geral", produto: "Produtos", imovel: "Imóveis", comida: "Comida e bebida", pessoa: "Pessoas" };
const RESOLUCAO: Record<string, string> = { rapida: "480p", alta: "720p" };
const MOVIMENTOS: Record<string, string> = { recarga: "Recarga", ajuste: "Ajuste", pedido: "Pedido", estorno: "Devolvido" };

// sinais do navegador para o registro de auditoria da autorização (nada sai para terceiros)
function sinaisNavegador(marcadoEm: string | null): Record<string, unknown> {
  const n = navigator as Navigator & { deviceMemory?: number; userAgentData?: { platform?: string } };
  return {
    idioma: n.language || null,
    idiomas: Array.from(n.languages ?? []).slice(0, 10),
    tela: `${screen.width}x${screen.height}@${window.devicePixelRatio || 1}`,
    plataforma: n.userAgentData?.platform || n.platform || null,
    nucleos: n.hardwareConcurrency ?? null,
    memoria: n.deviceMemory ?? null,
    toque: n.maxTouchPoints ?? null,
    marcado_em: marcadoEm,
  };
}

// -- envio das fotos ---------------------------------------------------------------------------

/** Um PUT direto ao armazenamento, com progresso em bytes (XHR: o fetch não informa o envio). */
function enviarUm(url: string, arq: File, tipo: string, aoAvancar: (enviados: number) => void): Promise<void> {
  return new Promise((ok, falha) => {
    const x = new XMLHttpRequest();
    x.open("PUT", url);
    x.setRequestHeader("Content-Type", tipo);
    x.upload.addEventListener("progress", (ev) => aoAvancar(ev.loaded));
    x.addEventListener("load", () => (x.status >= 200 && x.status < 300 ? ok() : falha(new Error(`HTTP ${x.status}`))));
    x.addEventListener("error", () => falha(new Error("rede")));
    x.addEventListener("abort", () => falha(new Error("cancelado")));
    x.send(arq);
  });
}

async function enviarArquivos(
  envios: Envio[],
  arquivos: Map<string, File>,
  aoAvancar: (enviados: number, total: number, prontos: number) => void,
  aoTerminarFoto: (chave: string) => void,
): Promise<void> {
  const fila = [...envios];
  const total = envios.reduce((s, e) => s + (arquivos.get(e.original)?.size ?? 0), 0);
  const porArquivo = new Map<string, number>();
  let prontos = 0;
  const avisar = () => aoAvancar([...porArquivo.values()].reduce((a, b) => a + b, 0), total, prontos);
  const trabalhar = async () => {
    for (let e = fila.shift(); e; e = fila.shift()) {
      const arq = arquivos.get(e.original);
      if (!arq) throw new Error(`a foto ${e.original} saiu da seleção`);
      const url = urlSegura(e.url);
      if (!url) throw new Error("endereço de envio inválido");
      for (let tentativa = 1; ; tentativa++) {
        try {
          await enviarUm(url, arq, e.tipo, (n) => {
            porArquivo.set(e!.original, n);
            avisar();
          });
          break;
        } catch {
          porArquivo.set(e.original, 0);
          if (tentativa >= 3) throw new Error(`não foi possível enviar ${e.original}; confira a internet e tente de novo`);
          await new Promise((ok) => setTimeout(ok, 1500 * tentativa));
        }
      }
      porArquivo.set(e.original, arq.size);
      prontos++;
      avisar();
      aoTerminarFoto(e.original);
    }
  };
  await Promise.all(Array.from({ length: Math.min(ENVIOS_SIMULTANEOS, envios.length) }, trabalhar));
}

// -- miniaturas das fotos escolhidas --------------------------------------------------------------

/** Miniatura reduzida no próprio navegador (a foto original não é desenhada inteira: menos memória). */
async function desenharMiniatura(arq: File): Promise<HTMLElement> {
  try {
    const bmp = await createImageBitmap(arq, { resizeWidth: 320, resizeQuality: "medium" });
    const c = criar("canvas", { width: bmp.width, height: bmp.height });
    const ctx = c.getContext("bitmaprenderer");
    if (ctx) ctx.transferFromImageBitmap(bmp);
    else {
      c.getContext("2d")?.drawImage(bmp, 0, 0);
      bmp.close();
    }
    return c;
  } catch {
    const img = criar("img", { alt: "", decoding: "async" });
    const url = URL.createObjectURL(arq);
    img.addEventListener("load", () => URL.revokeObjectURL(url), { once: true });
    img.addEventListener("error", () => URL.revokeObjectURL(url), { once: true });
    img.src = url;
    return img;
  }
}

/** Largura e altura da foto, já com a rotação do celular (EXIF) aplicada; null se não abrir. */
function medirFoto(arq: File): Promise<[number, number] | null> {
  return new Promise((ok) => {
    const url = URL.createObjectURL(arq);
    const img = new Image();
    img.decoding = "async";
    const fim = (v: [number, number] | null) => {
      URL.revokeObjectURL(url);
      ok(v);
    };
    img.addEventListener("load", () => fim(img.naturalWidth && img.naturalHeight ? [img.naturalWidth, img.naturalHeight] : null), { once: true });
    img.addEventListener("error", () => fim(null), { once: true });
    img.src = url;
  });
}

// arrastar uma pasta: percorre as subpastas (webkitGetAsEntry), guardando o caminho de cada foto
type Escolhida = { arquivo: File; caminho: string };

async function lerEntrada(entrada: FileSystemEntry, saida: Escolhida[]): Promise<void> {
  if (entrada.isFile) {
    const arquivo = await new Promise<File>((ok, falha) => (entrada as FileSystemFileEntry).file(ok, falha));
    saida.push({ arquivo, caminho: entrada.fullPath.replace(/^\//, "") || arquivo.name });
  } else if (entrada.isDirectory) {
    const leitor = (entrada as FileSystemDirectoryEntry).createReader();
    for (;;) {
      const lote = await new Promise<FileSystemEntry[]>((ok, falha) => leitor.readEntries(ok, falha));
      if (!lote.length) break;
      for (const filho of lote) await lerEntrada(filho, saida);
    }
  }
}

async function arquivosSoltos(dt: DataTransfer): Promise<Escolhida[]> {
  const entradas = Array.from(dt.items ?? [])
    .map((i) => (typeof i.webkitGetAsEntry === "function" ? i.webkitGetAsEntry() : null))
    .filter((x): x is FileSystemEntry => !!x);
  if (!entradas.length) return Array.from(dt.files, (arquivo) => ({ arquivo, caminho: arquivo.name }));
  const saida: Escolhida[] = [];
  for (const e of entradas) await lerEntrada(e, saida);
  return saida;
}

// -- cartões dos pedidos ---------------------------------------------------------------------------

function textoPrevisao(p: NonNullable<Previsao>): string {
  if (p.fase === "analise") return `Recebemos seu pedido. Ele começa em até ${minutos(p.inicio_max_s ?? 3600)}.`;
  if (p.fase === "fila") return (p.inicio_s ?? 0) < 60 ? "Seus vídeos começam em instantes." : `Seus vídeos começam em ${minutos(p.inicio_s ?? 0)}.`;
  return `Gerando seus vídeos · termina em ${minutos(p.fim_s ?? 0)}.`;
}

function cartaoLote(l: Lote, abrir: (v: Video, origem: HTMLElement) => void): HTMLElement {
  const estado = ESTADOS[l.status] ?? l.status;
  const cartao = criar("article", { className: `cartao lote estado-${l.status} surgir`, attrs: { "aria-label": `Pedido de ${plural(l.fotos, "foto", "fotos")}, ${estado}` } }, [
    criar("div", { className: "lote-cab" }, [
      criar("span", { className: `selo estado-${l.status}` }, [icone(l.status.startsWith("concluido") ? "ok" : l.status === "recusado" ? "alerta" : "relogio"), estado]),
      criar("span", { className: "fraco" }, [plural(l.fotos, "foto", "fotos"), " · ", tempo(l.criado)]),
    ]),
    criar("p", { className: "pedido", textContent: l.pedido.prompt }),
  ]);
  if (l.previsao) cartao.append(criar("div", { className: "previsao" }, [criar("span", { className: "pulso", attrs: { "aria-hidden": "true" } }), textoPrevisao(l.previsao)]));
  if (l.total) {
    const barra = criar("progress", { max: l.total, value: l.prontos });
    barra.setAttribute("aria-label", `Vídeos prontos do pedido`);
    cartao.append(
      criar("div", { className: "progresso" }, [
        criar("div", { className: "progresso-texto" }, [criar("span", { textContent: `${l.prontos} de ${plural(l.total, "vídeo pronto", "vídeos prontos")}` })]),
        barra,
      ]),
    );
  }
  if (l.motivo) {
    const motivo = criar("p", { className: "aviso erro" });
    aviso(motivo, `Motivo: ${l.motivo}`, "erro");
    cartao.append(motivo);
  }
  const prontos = l.videos.filter((v) => urlSegura(v.url));
  if (prontos.length) {
    const lista = criar("ul", { className: "videos", attrs: { "aria-label": "Vídeos prontos" } });
    for (const v of prontos) {
      const assistir = criar("button", { type: "button", className: "assistir", dados: { foco: `${l.lote}:assistir:${v.arquivo}` } }, [
        criar("span", { className: "play" }, [icone("play", "cheio")]),
        criar("span", { className: "nome", textContent: v.arquivo }),
      ]);
      assistir.setAttribute("aria-label", `Assistir ${v.arquivo}`);
      assistir.addEventListener("click", () => abrir(v, assistir));
      const itens: HTMLElement[] = [assistir];
      const baixar = urlSegura(v.baixar);
      if (baixar) {
        const a = criar("a", { href: baixar, className: "botao pequeno baixar", rel: "noopener", dados: { foco: `${l.lote}:baixar:${v.arquivo}` } }, [icone("baixar"), "Baixar"]);
        a.setAttribute("aria-label", `Baixar ${v.arquivo}`);
        itens.push(a);
      }
      lista.append(criar("li", { className: "video" }, itens));
    }
    cartao.append(lista, criar("p", { className: "fraco", textContent: "Os links valem por 24 h; ao voltar à página, eles são renovados." }));
  }
  const falhas = l.videos.filter((v) => v.status === "erro").length;
  if (falhas) cartao.append(criar("p", { className: "fraco", textContent: `${plural(falhas, "vídeo não saiu", "vídeos não saíram")}.` }));
  if (l.cobranca?.cobrado) {
    const devolvido = l.cobranca.devolvido ? ` · ${reais(l.cobranca.devolvido)} voltaram para o saldo` : "";
    cartao.append(criar("p", { className: "fraco", textContent: `Custo do pedido: ${reais(l.cobranca.cobrado)}${devolvido}` }));
  }
  return cartao;
}

// player: um <dialog> só, reaproveitado; "Abrir em outra aba" e "Baixar" ficam junto do vídeo
function criarPlayer(): (v: Video, origem: HTMLElement) => void {
  let player: { d: HTMLDialogElement; video: HTMLVideoElement; abrir: HTMLAnchorElement; baixar: HTMLAnchorElement; titulo: HTMLElement } | null = null;
  let origem: HTMLElement | null = null;
  return (v, quem) => {
    if (!player) {
      const { d, corpo, titulo } = dialogo("Vídeo");
      const video = criar("video", { controls: true, playsInline: true, loop: true, preload: "auto" });
      const abrir = criar("a", { className: "botao", target: "_blank", rel: "noopener noreferrer" }, [icone("abrir"), "Abrir em outra aba"]);
      const baixar = criar("a", { className: "botao primario baixar", rel: "noopener" }, [icone("baixar"), "Baixar"]);
      corpo.append(video, criar("div", { className: "dialogo-acoes" }, [abrir, baixar]));
      d.addEventListener("close", () => {
        video.pause();
        video.removeAttribute("src");
        video.load();
        origem?.focus();
      });
      player = { d, video, abrir, baixar, titulo };
    }
    origem = quem;
    const url = urlSegura(v.url);
    if (!url) return;
    player.titulo.textContent = v.arquivo;
    player.video.src = url;
    player.abrir.href = url;
    const baixar = urlSegura(v.baixar);
    player.baixar.hidden = !baixar;
    if (baixar) player.baixar.href = baixar;
    player.d.showModal();
    player.video.play().catch(() => undefined); // sem autoplay permitido: o controle de play fica à mão
  };
}

// -- página ------------------------------------------------------------------------------------

function iniciarCliente(): void {
  const msg = el("msg");
  const form = el<HTMLFormElement>("form-lote");
  const campos = el<HTMLFieldSetElement>("campos-lote");
  const enviar = el<HTMLButtonElement>("enviar");
  const prompt = el<HTMLTextAreaElement>("prompt");
  const selReceita = el<HTMLSelectElement>("receita");
  const consentimento = el<HTMLInputElement>("consentimento");
  const previa = el<HTMLUListElement>("previa");
  const abrirVideo = criarPlayer();
  let receitas: Receitas | null = null;
  let enviando = false;
  const arquivos = new Map<string, File>(); // caminho → arquivo (o caminho vai como nome da foto)
  const miniaturas = new Map<string, HTMLElement>(); // caminho → <li> já desenhado
  let geracaoPrevia = 0;
  const estados = new Map<string, string>(); // estado anterior de cada pedido (anúncio de mudanças)
  let primeiraLista = true;
  const dimensoes = new Map<string, [number, number] | null>(); // caminho → largura e altura da foto
  let medindo = false;
  const atualizarContador = contador(prompt, el("prompt-contador"));
  const selDuracao = el<HTMLSelectElement>("duracao");
  const selQualidade = el<HTMLSelectElement>("qualidade");

  // -- saldo (pré-pago) --------------------------------------------------------------------------
  let saldo: Saldo = { pre_pago: false };

  const duracaoEscolhida = (): number => {
    const r = receitas?.receitas.find((x) => x.id === selReceita.value);
    return Number(selDuracao.value) || r?.duracao || 5;
  };

  /** Custo do pedido como está agora (centavos), ou null fora do pré-pago. */
  const custoAtual = (): { custo: number; porVideo: number; duracao: number; resolucao: string } | null => {
    if (!saldo.pre_pago || !saldo.precos) return null;
    const duracao = duracaoEscolhida();
    const porVideo = (saldo.precos[selQualidade.value] ?? 0) * duracao;
    return { custo: porVideo * arquivos.size, porVideo, duracao, resolucao: RESOLUCAO[selQualidade.value] ?? selQualidade.value };
  };

  const atualizarCusto = () => {
    const alvo = el("custo");
    const c = custoAtual();
    alvo.hidden = !c;
    if (!c) return;
    const disponivel = saldo.saldo ?? 0;
    const falta = c.custo > disponivel;
    alvo.classList.toggle("falta", falta);
    alvo.textContent = arquivos.size
      ? `Este pedido: ${plural(arquivos.size, "vídeo", "vídeos")} de ${c.duracao} s em ${c.resolucao} = ${reais(c.custo)}` +
        (falta ? ` · faltam ${reais(c.custo - disponivel)} de saldo` : ` · saldo depois: ${reais(disponivel - c.custo)}`)
      : `Cada vídeo de ${c.duracao} s em ${c.resolucao} custa ${reais(c.porVideo)}.`;
  };

  const mostrarSaldo = () => {
    const caixa = el("saldo");
    caixa.hidden = !saldo.pre_pago;
    if (!saldo.pre_pago) return;
    el("saldo-valor").textContent = reais(saldo.saldo ?? 0);
    const precos = saldo.precos ?? {};
    el("saldo-precos").textContent = Object.entries(RESOLUCAO)
      .filter(([q]) => precos[q])
      .map(([q, r]) => `${r}: ${reais(precos[q])} por segundo de vídeo`)
      .join(" · ");
    // o extrato vem só quando a pessoa abre "Ver extrato" (e a cada atualização do saldo depois disso)
    const extrato = saldo.extrato;
    const lista = el("extrato-lista");
    if (!extrato) lista.replaceChildren(criar("li", { className: "fraco", textContent: "Carregando…" }));
    else if (!extrato.length) lista.replaceChildren(criar("li", { className: "fraco", textContent: "Nenhum movimento ainda." }));
    else
      lista.replaceChildren(
        ...extrato.map((m) =>
          criar("li", {}, [
            criar("span", {}, [tempo(m.quando), ` · ${MOVIMENTOS[m.tipo] ?? m.tipo}${m.descricao ? `: ${m.descricao}` : ""}`]),
            criar("span", { className: "valor", textContent: `${m.valor > 0 ? "+" : ""}${reais(m.valor)}` }),
          ]),
        ),
      );
    atualizarCusto();
  };

  const carregarSaldo = async () => {
    try {
      saldo = await api<Saldo>("/api/saldo");
      mostrarSaldo();
    } catch {
      /* fica o saldo anterior; a próxima atualização tenta de novo */
    }
  };
  let assinaturaCobranca = "";
  el("extrato").addEventListener("toggle", () => {
    if ((el("extrato") as HTMLDetailsElement).open) void carregarSaldo();
  });

  // -- pedidos ---------------------------------------------------------------------------------
  const lotesAlvo = el("lotes");
  const listar = async (): Promise<boolean> => {
    let lotes: Lote[];
    try {
      lotes = await api<Lote[]>("/api/lotes");
    } catch (e) {
      if (e instanceof ErroApi && e.status === 401) {
        marcarConectado(CHAVE_CONECTADO, false); // sessão vencida ou conta desativada: volta à entrada
        location.reload();
      }
      aviso(el("msg-painel"), `Não foi possível atualizar os pedidos: ${textoErro(e)}`, "erro");
      return true;
    }
    aviso(el("msg-painel"), "");
    const itens: ItemLista[] = lotes.map((l) => ({
      chave: l.lote,
      // sem as URLs assinadas (mudam a cada consulta): o cartão só é refeito quando algo muda de fato
      assinatura: JSON.stringify([l.status, l.prontos, l.total, l.motivo, l.previsao && textoPrevisao(l.previsao), l.videos.map((v) => [v.arquivo, v.status, !!v.url])]),
      criar: () => cartaoLote(l, abrirVideo),
    }));
    reconciliar(lotesAlvo, itens, () => vazio("Nenhum pedido ainda", "Quando você enviar fotos, os pedidos aparecem aqui."));
    el("atualizado").textContent = `Atualizado às ${hora()}`;
    for (const l of lotes) {
      const antes = estados.get(l.lote);
      if (!primeiraLista && antes && antes !== l.status) {
        const texto = l.status === "concluido" ? "Seus vídeos estão prontos." : `Pedido de ${plural(l.fotos, "foto", "fotos")}: ${ESTADOS[l.status] ?? l.status}.`;
        anunciar(texto);
        if (document.hidden && l.status.startsWith("concluido")) document.title = `✓ Vídeos prontos · ${document.title.replace(/^✓ Vídeos prontos · /, "")}`;
      }
      estados.set(l.lote, l.status);
    }
    primeiraLista = false;
    // pré-pago: pedido cobrado ou vídeo devolvido muda o saldo
    const cobrancas = JSON.stringify(lotes.map((l) => [l.lote, l.status, l.cobranca]));
    if (saldo.pre_pago && assinaturaCobranca && cobrancas !== assinaturaCobranca) void carregarSaldo();
    assinaturaCobranca = cobrancas;
    return lotes.some((l) => EM_ANDAMENTO.has(l.status));
  };
  const atualizacao = repetir(listar, 10000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) document.title = document.title.replace(/^✓ Vídeos prontos · /, "");
  });

  // -- fotos -----------------------------------------------------------------------------------
  const maximo = () => receitas?.plano?.max_fotos ?? receitas?.limites.fotos ?? 0;

  const atualizarContagem = (ignorados = 0, excedentes = 0) => {
    const teste = receitas?.plano?.nome === "teste";
    const n = arquivos.size;
    const max = maximo();
    let texto = teste
      ? `${n} de ${max} fotos escolhidas` + (n < max ? ` · ainda cabem ${max - n} neste pedido` : " · pedido completo")
      : n
        ? `${plural(n, "foto escolhida", "fotos escolhidas")} · ${bytes([...arquivos.values()].reduce((s, f) => s + f.size, 0))}`
        : "";
    if (ignorados) texto += ` · ${plural(ignorados, "ignorada", "ignoradas")} (use JPG, PNG ou WebP até 50 MB)`;
    if (excedentes) texto += ` · ${plural(excedentes, "ficou de fora", "ficaram de fora")} (máximo de ${max} fotos por pedido)`;
    el("contagem").textContent = texto;
    el("barra-fotos").hidden = !n && !ignorados && !excedentes;
    el("limpar").hidden = !n;
    rotular(enviar, n ? `Enviar pedido com ${plural(n, "foto", "fotos")}` : "Enviar pedido");
    atualizarCusto();
    return texto;
  };

  // -- foto pequena: o vídeo amplia a foto e pode sair borrado ou com detalhes diferentes ------------
  /** Tamanho do vídeo desta foto e quanto ela vai ser ampliada (formato automático, qualidade escolhida). */
  const ampliacao = (d: [number, number]): { amp: number; w: number; h: number } | null => {
    const auto = receitas?.proporcao_auto;
    if (!receitas?.saida || !auto) return null;
    const prop = d[0] / d[1];
    const formato = prop < auto[0] ? "vertical" : prop > auto[1] ? "horizontal" : "quadrado";
    const tam = receitas.saida[formato]?.[selQualidade.value];
    return tam ? { amp: Math.max(tam[0] / d[0], tam[1] / d[1]), w: tam[0], h: tam[1] } : null;
  };

  const atualizarAvisoFotos = () => {
    const limite = receitas?.ampliacao_aviso ?? 1.5;
    const pequenas: { nome: string; texto: string }[] = [];
    let video = "";
    for (const chave of arquivos.keys()) {
      const d = dimensoes.get(chave);
      const a = d ? ampliacao(d) : null;
      const pequena = !!d && !!a && a.amp >= limite;
      const nome = chave.split("/").pop() ?? chave;
      if (pequena && d && a) {
        pequenas.push({ nome, texto: `${nome} (${d[0]}×${d[1]})` });
        video ||= `${a.w}×${a.h}`;
      }
      const li = miniaturas.get(chave);
      if (!li) continue;
      li.classList.toggle("pequena", pequena);
      li.setAttribute("aria-label", pequena ? `${nome}, baixa resolução` : nome);
      const selo = li.querySelector(".baixa");
      if (pequena && !selo) li.append(criar("span", { className: "baixa", textContent: "baixa resolução", attrs: { "aria-hidden": "true" } }));
      else if (!pequena) selo?.remove();
    }
    const caixa = el("aviso-fotos");
    if (!pequenas.length) {
      aviso(caixa, "");
      return;
    }
    const lista = pequenas.slice(0, 3).map((x) => x.texto).join(", ") + (pequenas.length > 3 ? ` e mais ${pequenas.length - 3}` : "");
    aviso(
      caixa,
      [
        criar("p", {}, [criar("strong", { textContent: `${plural(pequenas.length, "foto tem", "fotos têm")} baixa resolução para o vídeo de ${video}: ${lista}.` })]),
        criar("p", {
          textContent:
            "Elas vão ser ampliadas, e o vídeo pode sair borrado ou com detalhes diferentes da foto. Se puder, troque pela foto original (do celular ou da câmera). Você pode enviar assim mesmo.",
        }),
      ],
      "alerta",
    );
  };

  /** Mede as fotos novas uma por vez, sem travar a página, e atualiza o aviso. */
  const medirFotos = async () => {
    if (medindo) return;
    medindo = true;
    try {
      for (let chave = [...arquivos.keys()].find((k) => !dimensoes.has(k)); chave; chave = [...arquivos.keys()].find((k) => !dimensoes.has(k))) {
        const arq = arquivos.get(chave);
        dimensoes.set(chave, arq ? await medirFoto(arq) : null);
        atualizarAvisoFotos();
      }
    } finally {
      medindo = false;
    }
  };

  const desenharPrevia = async () => {
    const minha = ++geracaoPrevia;
    const chaves = [...arquivos.keys()];
    const itens: HTMLElement[] = [];
    for (const chave of chaves.slice(0, MAX_PREVIA)) {
      let li = miniaturas.get(chave);
      if (!li) {
        const nome = chave.split("/").pop() ?? chave;
        const remover = criar("button", { type: "button", className: "remover", attrs: { "aria-label": `Remover ${nome}` } }, [icone("fechar")]);
        remover.addEventListener("click", () => {
          const posicao = [...arquivos.keys()].indexOf(chave);
          arquivos.delete(chave);
          miniaturas.delete(chave);
          atualizarContagem();
          void desenharPrevia(); // a lista é refeita na hora; as miniaturas chegam depois
          anunciar(`${nome} removida. ${el("contagem").textContent ?? ""}`);
          const botoes = previa.querySelectorAll<HTMLButtonElement>(".remover");
          (botoes[Math.min(posicao, botoes.length - 1)] ?? el<HTMLInputElement>("fotos")).focus();
        });
        li = criar("li", { className: "miniatura", title: nome, dados: { chave } }, [remover, criar("span", { className: "estado", attrs: { "aria-hidden": "true" } }, [icone("ok")])]);
        li.setAttribute("aria-label", nome);
        miniaturas.set(chave, li);
      }
      itens.push(li);
    }
    const resto = chaves.length - MAX_PREVIA;
    if (resto > 0) itens.push(criar("li", { className: "miniatura mais", textContent: `+${resto}` }));
    previa.replaceChildren(...itens);
    previa.hidden = !chaves.length;
    atualizarAvisoFotos();
    // desenha uma por vez, sem travar a página; uma seleção nova cancela a anterior
    for (const chave of chaves.slice(0, MAX_PREVIA)) {
      if (minha !== geracaoPrevia) return;
      const li = miniaturas.get(chave);
      const arq = arquivos.get(chave);
      if (!li || !arq || li.querySelector("canvas, img")) continue;
      li.prepend(await desenharMiniatura(arq));
    }
  };

  const escolher = (lista: Escolhida[]) => {
    if (!receitas || enviando) return;
    const tipos = new Set(receitas.limites.tipos);
    const max = maximo();
    let ignorados = 0;
    let excedentes = 0;
    for (const { arquivo, caminho } of lista) {
      if (!tipos.has(arquivo.type) || arquivo.size > receitas.limites.bytes_foto) {
        if (!caminho.split("/").pop()?.startsWith(".")) ignorados++; // arquivos ocultos de sistema não contam
        continue;
      }
      if (arquivos.has(caminho)) continue;
      if (arquivos.size >= max) {
        excedentes++;
        continue;
      }
      arquivos.set(caminho, arquivo);
    }
    marcarErro(el("fotos"), false);
    anunciar(atualizarContagem(ignorados, excedentes));
    void desenharPrevia();
    void medirFotos();
  };
  const doInput = (ev: Event) => {
    const entrada = ev.target as HTMLInputElement;
    escolher(Array.from(entrada.files ?? [], (arquivo) => ({ arquivo, caminho: arquivo.webkitRelativePath || arquivo.name })));
    entrada.value = ""; // escolher a mesma foto de novo também dispara "change"
  };
  el<HTMLInputElement>("fotos").addEventListener("change", doInput);
  el<HTMLInputElement>("pasta").addEventListener("change", doInput);
  el("limpar").addEventListener("click", () => {
    arquivos.clear();
    miniaturas.clear();
    atualizarContagem();
    void desenharPrevia();
    anunciar("Seleção de fotos limpa.");
    el<HTMLInputElement>("fotos").focus();
  });
  // celular: escolher pasta não existe; a área toda abre a escolha de fotos
  if (matchMedia("(hover: none) and (pointer: coarse)").matches) {
    el("pasta").hidden = true;
    el("rotulo-pasta").hidden = true;
  }
  const soltar = el("soltar");
  soltar.addEventListener("click", (ev) => {
    if (!campos.disabled && !(ev.target as HTMLElement).closest("label, button, input")) el<HTMLInputElement>("fotos").click();
  });
  let arrastando = 0;
  soltar.addEventListener("dragenter", (ev) => {
    ev.preventDefault();
    if (!campos.disabled) soltar.classList.toggle("arrastando", ++arrastando > 0);
  });
  soltar.addEventListener("dragover", (ev) => {
    ev.preventDefault();
    if (ev.dataTransfer) ev.dataTransfer.dropEffect = campos.disabled ? "none" : "copy";
  });
  soltar.addEventListener("dragleave", () => {
    arrastando = Math.max(0, arrastando - 1);
    soltar.classList.toggle("arrastando", arrastando > 0);
  });
  soltar.addEventListener("drop", async (ev) => {
    ev.preventDefault();
    arrastando = 0;
    soltar.classList.remove("arrastando");
    if (campos.disabled || !ev.dataTransfer) return;
    escolher(await arquivosSoltos(ev.dataTransfer));
  });
  // foto solta fora da área não abre no lugar da página (texto solto num campo continua valendo)
  const arquivoArrastado = (ev: DragEvent) => !!ev.dataTransfer && Array.from(ev.dataTransfer.types).includes("Files");
  window.addEventListener("dragover", (ev) => arquivoArrastado(ev) && ev.preventDefault());
  window.addEventListener("drop", (ev) => arquivoArrastado(ev) && ev.preventDefault());

  // -- finalidades: o que o cliente quer criar, antes do pedido -----------------------------------
  const blocos = el<HTMLUListElement>("blocos");
  const selModelo = el<HTMLSelectElement>("modelo");
  const interessadas = new Set<string>(); // finalidades em breve em que o cliente marcou "Tenho interesse"
  let escolhida: Finalidade | null = null;
  let todas: { d: HTMLDialogElement; corpo: HTMLElement } | null = null;
  let origemTodas: HTMLElement | null = null;

  const iconeDe = (f: Finalidade) => icone(ehIcone(f.icone) ? f.icone : "brilho");
  const lista = () => receitas?.finalidades ?? [];

  /** Botão "Tenho interesse" (ou a confirmação, se já marcou). */
  const interesse = (f: Finalidade): HTMLElement => {
    if (interessadas.has(f.id)) return criar("span", { className: "interesse-ok" }, [icone("ok"), "Interesse registrado"]);
    const b = criar("button", { type: "button", className: "botao pequeno", dados: { foco: `interesse:${f.id}` } }, ["Tenho interesse"]);
    b.setAttribute("aria-label", `Tenho interesse em ${f.titulo}`);
    b.addEventListener("click", async () => {
      ocupado(b, true);
      try {
        await api("/api/interesses", { method: "POST", body: JSON.stringify({ finalidade: f.id }) });
        interessadas.add(f.id);
        anunciar(`Interesse em “${f.titulo}” registrado. Obrigado!`);
        const voltar = b.closest("dialog") ? "dialogo" : "blocos";
        desenharBlocos();
        if (todas?.d.open) desenharTodas();
        // o foco vai para a confirmação que ficou no lugar do botão
        const alvo = voltar === "dialogo" ? todas?.corpo : blocos;
        alvo?.querySelector<HTMLElement>(`[data-finalidade="${f.id}"] .interesse-ok`)?.setAttribute("tabindex", "-1");
        alvo?.querySelector<HTMLElement>(`[data-finalidade="${f.id}"] .interesse-ok`)?.focus();
      } catch (e) {
        ocupado(b, false);
        aviso(el("msg-painel"), textoErro(e), "erro");
      }
    });
    return b;
  };

  const blocoFinalidade = (f: Finalidade): HTMLElement => {
    const textos = [
      iconeDe(f),
      criar("span", { className: "bloco-titulo", textContent: f.titulo }),
      criar("span", { className: "bloco-tipo", textContent: f.tipo }),
      criar("span", { className: "bloco-desc", textContent: f.descricao }),
    ];
    if (f.disponivel) {
      const b = criar("button", { type: "button", className: "bloco", dados: { foco: `finalidade:${f.id}` } }, [
        ...textos,
        criar("span", { className: "selo ok", textContent: f.modelos.length ? `Disponível · ${f.modelos.map((m) => m.nome).join(", ")}` : "Disponível" }),
      ]);
      b.addEventListener("click", () => escolherFinalidade(f.id));
      return criar("li", { dados: { finalidade: f.id } }, [b]);
    }
    return criar("li", { dados: { finalidade: f.id } }, [
      criar("div", { className: "bloco em-breve" }, [...textos, criar("span", { className: "selo", textContent: "Em breve" }), interesse(f)]),
    ]);
  };

  const desenharBlocos = () => {
    const destaques = lista().filter((f) => f.destaque);
    const verTodas = criar("button", { type: "button", className: "bloco ver-todas", dados: { foco: "finalidade:todas" } }, [
      icone("grade"),
      criar("span", { className: "bloco-titulo", textContent: "Ver todas as opções" }),
      criar("span", { className: "bloco-tipo", textContent: plural(lista().length, "tipo de geração", "tipos de geração") }),
      criar("span", { className: "bloco-desc", textContent: "Voz, música, 3D, ampliação de resolução e mais." }),
    ]);
    verTodas.addEventListener("click", () => abrirTodas(verTodas));
    blocos.replaceChildren(...destaques.map(blocoFinalidade), criar("li", {}, [verTodas]));
    blocos.setAttribute("aria-busy", "false");
  };

  /** Janela "Ver todas": todas as finalidades, agrupadas pelo que o cliente envia. */
  const desenharTodas = () => {
    if (!todas) return;
    const grupos = receitas?.entradas ?? {};
    const secoes = Object.entries(grupos).map(([entrada, rotulo]) => {
      const itens = lista()
        .filter((f) => f.entrada === entrada)
        .map((f) => {
          const detalhe: HTMLElement[] = [criar("p", { className: "fraco", textContent: f.descricao })];
          for (const m of f.modelos) detalhe.push(criar("p", { className: "modelo-uso", textContent: `${m.nome}: melhor para ${m.melhor_para ?? m.descricao}.` }));
          if (!f.disponivel && f.previstos.length)
            detalhe.push(criar("p", { className: "fraco", textContent: `Modelos previstos: ${f.previstos.map((m) => m.nome).join(", ")}.` }));
          let acao: HTMLElement;
          if (f.disponivel) {
            acao = criar("button", { type: "button", className: "botao primario pequeno" }, ["Usar"]);
            acao.setAttribute("aria-label", `Usar ${f.titulo}`);
            acao.addEventListener("click", () => {
              todas?.d.close();
              escolherFinalidade(f.id);
            });
          } else acao = interesse(f);
          return criar("li", { className: "opcao", dados: { finalidade: f.id } }, [
            iconeDe(f),
            criar("div", { className: "opcao-texto" }, [
              criar("p", { className: "opcao-titulo" }, [criar("strong", { textContent: f.titulo }), " ", criar("span", { className: f.disponivel ? "selo ok" : "selo", textContent: f.disponivel ? "Disponível" : "Em breve" })]),
              criar("p", { className: "bloco-tipo", textContent: f.tipo }),
              ...detalhe,
            ]),
            acao,
          ]);
        });
      return criar("section", { className: "grupo-opcoes" }, [criar("h3", { textContent: rotulo }), criar("ul", { className: "opcoes" }, itens)]);
    });
    todas.corpo.replaceChildren(...secoes);
  };

  const abrirTodas = (origem: HTMLElement) => {
    origemTodas = origem;
    if (!todas) {
      const { d, corpo } = dialogo("Todas as opções de geração", "todas-finalidades");
      d.addEventListener("close", () => origemTodas?.isConnected && !escolhida && origemTodas.focus());
      todas = { d, corpo };
    }
    desenharTodas();
    todas.d.showModal();
  };

  const escolherFinalidade = (id: string) => {
    const f = lista().find((x) => x.id === id && x.disponivel);
    if (!f) return;
    escolhida = f;
    const modelo = f.modelos[0];
    el("escolhida-icone").replaceChildren(iconeDe(f));
    el("escolhida-titulo").textContent = f.titulo;
    el("escolhida-tipo").textContent = modelo ? `${f.tipo} · modelo ${modelo.nome}` : f.tipo;
    el("modelo-uso").textContent = modelo ? `${modelo.nome} é melhor para: ${modelo.melhor_para ?? modelo.descricao}.` : "";
    selModelo.replaceChildren(...f.modelos.map((m) => criar("option", { value: m.id, textContent: `${m.nome} · ${m.descricao}` })));
    el("modelo-info").textContent = f.modelos.length > 1 ? "" : "Por enquanto, este é o único modelo desta opção.";
    el("finalidades").hidden = true;
    el("escolhida").hidden = false;
    form.hidden = false;
    el("escolhida-titulo").focus();
    anunciar(`${f.titulo}, com o modelo ${modelo?.nome ?? ""}. Agora escolha as fotos.`);
  };

  el("trocar-finalidade").addEventListener("click", () => {
    const antes = escolhida?.id;
    escolhida = null;
    form.hidden = true;
    el("escolhida").hidden = true;
    el("finalidades").hidden = false;
    (blocos.querySelector<HTMLElement>(`[data-finalidade="${antes}"] button`) ?? el("titulo-finalidades")).focus();
  });
  selModelo.addEventListener("change", () => {
    const m = escolhida?.modelos.find((x) => x.id === selModelo.value);
    if (m) el("modelo-uso").textContent = `${m.nome} é melhor para: ${m.melhor_para ?? m.descricao}.`;
  });

  // -- estilo, sugestão e autorização ------------------------------------------------------------
  const aplicarReceita = () => {
    const r = receitas?.receitas.find((x) => x.id === selReceita.value);
    el("sugestao").hidden = !r?.acao;
    el("sugestao-texto").textContent = r?.acao ?? "";
    const exigida = !!r?.exige_consentimento;
    consentimento.required = exigida;
    el("caixa-consentimento").classList.toggle("exigida", exigida);
    const padrao = Array.from(selDuracao.options).find((o) => !o.value);
    if (padrao && r?.duracao) padrao.textContent = `Padrão do estilo (${r.duracao} s)`;
    atualizarCusto();
  };
  selReceita.addEventListener("change", aplicarReceita);
  selDuracao.addEventListener("change", atualizarCusto);
  selQualidade.addEventListener("change", () => {
    atualizarCusto();
    atualizarAvisoFotos(); // em 720p a mesma foto pode precisar de mais ampliação
  });
  el("usar-sugestao").addEventListener("click", () => {
    prompt.value = el("sugestao-texto").textContent ?? "";
    atualizarContador();
    marcarErro(prompt, false);
    prompt.focus();
    prompt.setSelectionRange(prompt.value.length, prompt.value.length);
    anunciar("Exemplo copiado para o pedido. Você pode editar o texto.");
  });
  prompt.addEventListener("input", () => marcarErro(prompt, false));
  let marcadoEm: string | null = null; // momento do clique na caixa de autorização (vai no registro)
  consentimento.addEventListener("change", () => {
    marcadoEm = consentimento.checked ? new Date().toISOString() : null;
    marcarErro(consentimento, false);
  });

  // durações do plano (3 s só no teste; nos outros, de 4 a 7 s); teste gratuito: só 480p, vídeos de 3 s,
  // um pedido com até 5 fotos (a API confere tudo de novo)
  const aplicarPlano = (plano: Plano | undefined) => {
    const avisoPlano = el("plano-aviso");
    if (!plano) return;
    const teste = plano.nome === "teste";
    if (plano.duracoes) for (const o of Array.from(selDuracao.options)) if (o.value ? !plano.duracoes.includes(Number(o.value)) : teste) o.remove();
    if (!teste) return;
    for (const o of Array.from(selQualidade.options)) if (!plano.qualidades?.includes(o.value)) o.remove();
    selDuracao.value = String(plano.duracoes?.[0] ?? 3);
    if (plano.disponivel) {
      aviso(
        avisoPlano,
        [
          criar("p", {}, [criar("strong", { textContent: `Teste gratuito: escolha até ${plano.max_fotos} fotos e envie todas juntas, em um único pedido.` })]),
          criar("p", {
            textContent:
              "Cada foto vira um vídeo de 3 s em 480p. Depois de enviar, o teste não aceita outro pedido, então escolha todas as fotos antes de clicar em “Enviar pedido”. Os testes são gerados em grupo: os vídeos ficam prontos em até uns 20 minutos depois da revisão.",
          }),
        ],
        "info",
      );
      atualizarContagem();
    } else {
      const textos: (Node | string)[] = [criar("p", { textContent: `Teste gratuito já usado: ${plano.motivo ?? ""}`.trim() })];
      if (receitas?.pagamento) {
        const precos = plano.precos ?? {};
        const linha = Object.entries(RESOLUCAO)
          .filter(([q]) => precos[q])
          .map(([q, r]) => `${r} a ${reais(precos[q])} por segundo`)
          .join(" e ");
        const recarregar = criar("button", { type: "button", className: "botao primario pequeno" }, [icone("brilho"), "Adicionar saldo por Pix"]);
        recarregar.addEventListener("click", pix);
        textos.push(criar("p", { textContent: `Para continuar, adicione saldo: você paga só os vídeos que pedir (${linha}).` }), recarregar);
      }
      aviso(avisoPlano, textos, "alerta");
      campos.disabled = true;
    }
  };

  // -- conta, saldo e recarga por Pix --------------------------------------------------------------
  let conta: Conta | null = null;
  const pix = () => {
    if (!conta?.pagamento) return;
    const eraTeste = conta.plano === "teste";
    abrirPix(conta.pagamento.minimo, conta.pagamento.maximo, () => {
      if (eraTeste) window.setTimeout(() => location.reload(), 2500); // a 1ª recarga passa a conta para o plano pago
      else void carregarSaldo().then(carregarConta);
    });
  };
  const carregarConta = async () => {
    conta = await api<Conta>("/api/conta");
    el("conta-resumo").textContent = resumoConta(conta);
    const botao = el<HTMLButtonElement>("adicionar-saldo");
    botao.hidden = !conta.pagamento?.ativo || conta.plano === "interno";
    el("saldo-recarga").textContent = conta.pagamento?.ativo
      ? "O saldo entra assim que o Pix é confirmado. Vídeo que não sai volta para o saldo."
      : "Para recarregar, fale com a gente pelo suporte. Vídeo que não sai volta para o saldo.";
  };
  el("abrir-conta").addEventListener("click", () => conta && abrirConta(conta, pix));
  el("adicionar-saldo").addEventListener("click", pix);

  // -- entrada -----------------------------------------------------------------------------------
  iniciarEntrada(async () => {
    receitas = await api<Receitas>("/api/receitas");
    // estilos agrupados por nicho; o "Livre" (geral) vem primeiro, como a API manda
    const grupos = new Map<string, HTMLOptGroupElement>();
    const opcoes: HTMLElement[] = [];
    for (const r of receitas.receitas) {
      const opcao = criar("option", { value: r.id, textContent: r.label });
      const nomeGrupo = NICHOS[r.nicho] ?? r.nicho;
      let g = grupos.get(nomeGrupo);
      if (!g) {
        g = criar("optgroup", { label: nomeGrupo });
        grupos.set(nomeGrupo, g);
        opcoes.push(g);
      }
      g.append(opcao);
    }
    selReceita.replaceChildren(...opcoes);
    if (receitas.autorizacao) el("texto-autorizacao").textContent = receitas.autorizacao.texto;
    for (const f of receitas.interesses ?? []) interessadas.add(f);
    desenharBlocos(); // o pedido começa pela escolha do que criar (o modelo vem da finalidade)
    campos.disabled = false;
    aplicarPlano(receitas.plano);
    if (receitas.plano?.nome === "pago" && receitas.plano.precos) {
      saldo = { pre_pago: true, saldo: receitas.plano.saldo ?? 0, precos: receitas.plano.precos };
      mostrarSaldo();
    }
    aplicarReceita();
    atualizarContagem();
    atualizacao.agora();
    await carregarConta();
  });
  el("link-suporte").textContent = `${location.host}/suporte`;

  // -- envio -------------------------------------------------------------------------------------
  const avisarSaida = (ev: BeforeUnloadEvent) => {
    ev.preventDefault();
    ev.returnValue = ""; // navegadores antigos
  };

  const validar = (): HTMLElement | null => {
    if (!arquivos.size) {
      marcarErro(el("fotos"), true);
      aviso(msg, "Escolha as fotos ou uma pasta antes de enviar.", "erro");
      return el("fotos");
    }
    if (prompt.value.trim().length < 3) {
      marcarErro(prompt, true);
      aviso(msg, "Escreva o que deve acontecer nos vídeos (pelo menos 3 letras).", "erro");
      return prompt;
    }
    const c = custoAtual();
    if (c && c.custo > (saldo.saldo ?? 0)) {
      aviso(msg, `Saldo insuficiente: este pedido custa ${reais(c.custo)} e o seu saldo é ${reais(saldo.saldo ?? 0)}. Tire algumas fotos, escolha vídeos mais curtos ou adicione saldo por Pix.`, "erro");
      return el("fotos");
    }
    if (consentimento.required && !consentimento.checked) {
      marcarErro(consentimento, true);
      aviso(msg, "Este estilo mostra pessoas: marque a autorização por escrito para enviar.", "erro");
      return consentimento;
    }
    return null;
  };

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (enviando || campos.disabled) return;
    const invalido = validar();
    if (invalido) {
      invalido.focus();
      anunciar(msg.textContent ?? "", true);
      return;
    }
    const nomes = [...arquivos.keys()];
    const corpo = {
      prompt: prompt.value.trim(),
      receita: selReceita.value,
      qualidade: selQualidade.value,
      duracao: Number(selDuracao.value) || null,
      consentimento: consentimento.checked,
      modelo: selModelo.value,
      navegador: sinaisNavegador(consentimento.checked ? marcadoEm : null),
      fotos: nomes.map((n) => ({ nome: n, tipo: arquivos.get(n)!.type, bytes: arquivos.get(n)!.size })),
    };
    enviando = true;
    ocupado(enviar, true);
    campos.disabled = true;
    window.addEventListener("beforeunload", avisarSaida);
    const area = el("area-progresso");
    const barra = el<HTMLProgressElement>("progresso");
    try {
      aviso(msg, "Criando o pedido…");
      const r = await api<{ lote: string; envios: Envio[] }>("/api/lotes", { method: "POST", body: JSON.stringify(corpo) });
      aviso(msg, "");
      area.hidden = false;
      barra.max = 1;
      barra.value = 0;
      el("progresso-texto").textContent = `Enviando ${plural(r.envios.length, "foto", "fotos")}…`;
      anunciar(`Enviando ${plural(r.envios.length, "foto", "fotos")}. Não feche a página até terminar.`);
      let ultimoAnuncio = 0;
      for (const li of miniaturas.values()) li.classList.add("enviando");
      await enviarArquivos(
        r.envios,
        arquivos,
        (enviados, total, prontos) => {
          barra.max = total || 1;
          barra.value = enviados;
          const pct = total ? Math.floor((enviados / total) * 100) : 0;
          el("progresso-pct").textContent = `${pct}% · ${bytes(enviados)} de ${bytes(total)}`;
          el("progresso-texto").textContent = `Enviando: ${prontos} de ${plural(r.envios.length, "foto", "fotos")}`;
          if (pct - ultimoAnuncio >= 25) {
            ultimoAnuncio = pct - (pct % 25);
            anunciar(`${ultimoAnuncio}% enviado.`);
          }
        },
        (chave) => {
          const li = miniaturas.get(chave);
          li?.classList.remove("enviando");
          li?.classList.add("enviada");
        },
      );
      el("progresso-texto").textContent = "Conferindo o envio…";
      await api(`/api/lotes/${r.lote}/enviado`, { method: "POST" });
      window.removeEventListener("beforeunload", avisarSaida);
      aviso(msg, "Pedido enviado. Ele passa por uma revisão rápida e entra na fila; acompanhe em “Seus pedidos”.", "ok");
      anunciar("Pedido enviado. Acompanhe em Seus pedidos.");
      arquivos.clear();
      miniaturas.clear();
      form.reset();
      marcadoEm = null;
      atualizarContador();
      void desenharPrevia();
      campos.disabled = false;
      if (receitas?.plano?.nome === "teste") {
        receitas = await api<Receitas>("/api/receitas");
        aplicarPlano(receitas.plano);
      }
      aplicarReceita();
      atualizarContagem();
      if (saldo.pre_pago) void carregarSaldo();
      atualizacao.agora();
      if (matchMedia("(max-width: 63.99em)").matches) el("titulo-pedidos").focus();
    } catch (e) {
      for (const li of miniaturas.values()) li.classList.remove("enviando", "enviada");
      aviso(msg, textoErro(e), "erro");
      anunciar(textoErro(e), true);
      campos.disabled = receitas?.plano?.disponivel === false;
    } finally {
      enviando = false;
      window.removeEventListener("beforeunload", avisarSaida);
      ocupado(enviar, false);
      area.hidden = true;
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  iniciarCookies();
  iniciarCliente();
});
