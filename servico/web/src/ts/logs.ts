// Tela "Logs" (/logs): os vídeos que a conta gerou nas últimas 24 horas (ou 3 e 7 dias) e não excluiu, com o
// modelo que gerou, a hora e os links (assistir e baixar, assinados por 24 h). Excluir apaga o arquivo do
// armazenamento (POST /api/geracoes/{id}/excluir), com confirmação; o pedido continua no histórico.
import { type ItemLista, anunciar, api, aviso, confirmar, criar, el, icone, ocupado, reconciliar, tempo, textoErro, urlSegura, vazio } from "./comum";
import { abrirVideo } from "./player";

type Geracao = {
  id: string;
  arquivo: string;
  lote: string;
  gerado_em: number;
  modelo: string;
  finalidade: string | null;
  tipo_modelo: string | null;
  duracao: number | null;
  resolucao: string | null;
  texto: string | null;
  texto_proprio: boolean;
  url: string;
  baixar: string;
};

const PERIODOS: Record<string, string> = { "24": "nas últimas 24 horas", "72": "nos últimos 3 dias", "168": "nos últimos 7 dias" };

export function criarLogs(ctx: { erro: (e: unknown, alvo: HTMLElement) => void }): { abrir: () => Promise<void>; recarregar: () => void } {
  const alvo = el("logs-lista");
  const filtros = el("logs-periodo");
  let horas = 24;
  let itens: Geracao[] = [];
  let carregado = false;
  let geracao = 0;

  const excluir = async (g: Geracao, botao: HTMLButtonElement) => {
    const ok = await confirmar({
      titulo: "Excluir este vídeo?",
      texto: [`${g.arquivo} sai do armazenamento e não dá para recuperar depois.`, "O pedido continua no seu histórico e no extrato."],
      confirmar: "Excluir o vídeo",
      perigo: true,
    });
    if (!ok) return;
    ocupado(botao, true);
    try {
      await api(`/api/geracoes/${encodeURIComponent(g.id)}/excluir`, { method: "POST" });
      const posicao = itens.findIndex((x) => x.id === g.id);
      itens = itens.filter((x) => x.id !== g.id);
      desenhar();
      anunciar(`${g.arquivo} excluído.`);
      // o foco vai para o vídeo que ficou no lugar (ou para o título, se a lista acabou)
      const botoes = alvo.querySelectorAll<HTMLElement>(".assistir");
      (botoes[Math.min(posicao, botoes.length - 1)] ?? el("titulo-logs")).focus();
    } catch (e) {
      ocupado(botao, false);
      aviso(el("logs-msg"), textoErro(e), "erro");
    }
  };

  const item = (g: Geracao): HTMLElement => {
    const assistir = criar("button", { type: "button", className: "assistir", dados: { foco: `log:assistir:${g.id}` } }, [
      criar("span", { className: "play" }, [icone("play", "cheio")]),
      criar("span", { className: "nome", textContent: g.arquivo }),
    ]);
    assistir.setAttribute("aria-label", `Assistir ${g.arquivo}`);
    assistir.addEventListener("click", () => abrirVideo(g, assistir));
    const acoes: HTMLElement[] = [];
    const baixar = urlSegura(g.baixar);
    if (baixar) {
      const a = criar("a", { href: baixar, className: "botao pequeno baixar", rel: "noopener", dados: { foco: `log:baixar:${g.id}` } }, [icone("baixar"), "Baixar"]);
      a.setAttribute("aria-label", `Baixar ${g.arquivo}`);
      acoes.push(a);
    }
    const lixo = criar("button", { type: "button", className: "botao pequeno perigo", dados: { foco: `log:excluir:${g.id}` } }, [icone("lixo"), "Excluir"]);
    lixo.setAttribute("aria-label", `Excluir ${g.arquivo}`);
    lixo.addEventListener("click", () => void excluir(g, lixo));
    acoes.push(lixo);
    const sobre = [g.modelo, g.finalidade, g.duracao && g.resolucao ? `${g.duracao} s em ${g.resolucao}` : null].filter(Boolean).join(" · ");
    const partes: HTMLElement[] = [
      criar("div", { className: "video" }, [assistir, ...acoes]),
      criar("p", { className: "linha-detalhe" }, [`${sobre} · gerado em `, tempo(g.gerado_em)]),
    ];
    if (g.texto)
      partes.push(
        criar("p", { className: "texto-log" }, [g.texto_proprio ? criar("span", { className: "selo info", textContent: "Texto próprio" }) : null, " ", criar("q", { textContent: g.texto })]),
      );
    return criar("li", { className: "geracao" }, partes);
  };

  const desenhar = () => {
    const lista: ItemLista[] = itens.map((g) => ({ chave: g.id, assinatura: g.id, criar: () => item(g) }));
    reconciliar(alvo, lista, () =>
      criar("li", { className: "sem-itens" }, [
        vazio(`Nenhum vídeo ${PERIODOS[String(horas)]}`, "Os vídeos que você gerar aparecem aqui, com o modelo, a hora e os links para assistir e baixar.", "historico"),
      ]),
    );
  };

  const carregar = async () => {
    const minha = ++geracao;
    alvo.setAttribute("aria-busy", "true");
    try {
      const r = await api<{ itens: Geracao[] }>(`/api/geracoes?horas=${horas}`);
      if (minha !== geracao) return;
      el("logs-msg").hidden = true;
      itens = r.itens;
      // a lista nova troca a anterior inteira (os links assinados mudam a cada consulta)
      alvo.replaceChildren();
      desenhar();
      carregado = true;
    } catch (e) {
      if (minha !== geracao) return;
      if (!carregado) alvo.replaceChildren();
      alvo.setAttribute("aria-busy", "false");
      ctx.erro(e, el("logs-msg"));
    }
  };

  filtros.addEventListener("click", (ev) => {
    const b = (ev.target as Element).closest<HTMLButtonElement>("button[data-horas]");
    if (!b || Number(b.dataset.horas) === horas) return;
    horas = Number(b.dataset.horas);
    for (const x of Array.from(filtros.querySelectorAll("button"))) x.setAttribute("aria-pressed", String(x === b));
    void carregar();
  });

  return {
    abrir: async () => {
      // os links valem 24 h: a cada visita à tela a lista vem de novo (na 1ª, já pedida no <head>)
      await carregar();
    },
    recarregar: () => {
      if (carregado) void carregar();
    },
  };
}
