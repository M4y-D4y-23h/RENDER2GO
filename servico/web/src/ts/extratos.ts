// Tela "Extratos" (/extratos): o que a conta gastou, por rodada (cada pedido: o tipo e o modelo de IA, os vídeos e
// o total, que é o cobrado menos o devolvido), as recargas e os ajustes, o total por modelo e a planilha (CSV).
// GET /api/extrato?dias=7|30|90|365|0 e, para a página seguinte, &antes=<quando>. Valores em centavos.
import { ESTADOS, api, criar, el, plural, reais, tempo } from "./comum";
import { aoMudarConta, contaAtual } from "./conta";

type Rodada = {
  tipo: "rodada";
  lote: string;
  quando: number;
  modelo: string;
  finalidade: string | null;
  tipo_modelo: string | null;
  videos: number | null;
  duracao: number | null;
  resolucao: string | null;
  cobrado: number;
  devolvido: number;
  liquido: number;
  status: string | null;
};
type Credito = { tipo: "recarga" | "ajuste"; quando: number; valor: number; descricao: string | null };
type PorModelo = { modelo: string; finalidade: string | null; tipo_modelo: string | null; rodadas: number; cobrado: number; devolvido: number; liquido: number };
type Extrato = {
  plano: string;
  dias: number;
  resumo: { gasto: number; devolvido: number; liquido: number; rodadas: number; recargas: number; ajustes: number; saldo: number };
  por_modelo: PorModelo[];
  itens: (Rodada | Credito)[];
  proximo: number | null;
};

export const PERIODOS: Record<string, string> = { "7": "nos últimos 7 dias", "30": "nos últimos 30 dias", "90": "nos últimos 90 dias", "365": "nos últimos 12 meses", "0": "desde o início" };

/** O valor com o rótulo só para leitores de tela ("Total da rodada: R$ 2,00"). */
function valor(texto: string, rotulo: string, classe: string): HTMLElement {
  return criar("p", { className: `linha-valor ${classe}` }, [criar("span", { className: "visualmente-oculto", textContent: `${rotulo}: ` }), texto]);
}

function linhaItem(x: Rodada | Credito): HTMLElement {
  if (x.tipo === "rodada") {
    const detalhe = [
      x.videos ? `${plural(x.videos, "vídeo", "vídeos")}${x.duracao ? ` de ${x.duracao} s` : ""}${x.resolucao ? ` em ${x.resolucao}` : ""}` : null,
      `cobrado ${reais(x.cobrado)}`,
      x.devolvido ? `devolvido ${reais(x.devolvido)}` : null,
      x.status ? (ESTADOS[x.status] ?? x.status) : "pedido já fora do histórico",
    ].filter(Boolean);
    return criar("li", { className: "linha rodada" }, [
      criar("p", { className: "linha-titulo", textContent: ["Pedido", x.modelo, x.tipo_modelo].filter(Boolean).join(" · ") }),
      valor(x.liquido ? `−${reais(x.liquido)}` : reais(0), "Total da rodada", "debito"),
      criar("p", { className: "linha-detalhe" }, [tempo(x.quando), ` · ${detalhe.join(" · ")}`]),
    ]);
  }
  const rotulo = x.tipo === "recarga" ? "Recarga" : "Ajuste";
  return criar("li", { className: `linha ${x.tipo}` }, [
    criar("p", { className: "linha-titulo", textContent: x.descricao ? `${rotulo} · ${x.descricao}` : rotulo }),
    valor(`${x.valor > 0 ? "+" : "−"}${reais(Math.abs(x.valor))}`, x.valor > 0 ? "Crédito" : "Débito", x.valor > 0 ? "credito" : "debito"),
    criar("p", { className: "linha-detalhe" }, [tempo(x.quando)]),
  ]);
}

export function criarExtratos(ctx: { pix: () => void; erro: (e: unknown, alvo: HTMLElement) => void }): { abrir: () => Promise<void>; recarregar: () => void } {
  const alvo = el("extratos-dados");
  const filtros = el("extrato-periodo");
  const csv = el<HTMLAnchorElement>("extrato-csv");
  let dias = 30;
  let carregado = false;
  let geracao = 0;

  const atualizarCsv = () => {
    // o fuso deste aparelho, em minutos a leste de UTC: as datas da planilha saem no horário da pessoa
    csv.href = `/api/extrato.csv?dias=${dias}&fuso_min=${-new Date().getTimezoneOffset()}`;
  };

  const desenhar = (e: Extrato) => {
    const c = contaAtual();
    const partes: HTMLElement[] = [];
    if (e.plano === "teste")
      partes.push(criar("p", { className: "aviso info", textContent: "No teste gratuito não há cobrança. Quando você adicionar saldo por Pix, as recargas e o custo de cada pedido aparecem aqui." }));
    else if (e.plano === "interno") partes.push(criar("p", { className: "aviso info", textContent: "Plano interno: os pedidos não são cobrados." }));
    const r = e.resumo;
    const numero = (rotulo: string, texto: string, dica?: string) =>
      criar("div", {}, [criar("dt", { textContent: rotulo }), criar("dd", { textContent: texto }), dica ? criar("dd", { className: "fraco", textContent: dica }) : null]);
    partes.push(
      criar("dl", { className: "numeros" }, [
        numero("Saldo atual", reais(r.saldo)),
        numero("Total gasto", reais(r.liquido), r.devolvido ? `${reais(r.gasto)} cobrados, ${reais(r.devolvido)} devolvidos` : undefined),
        numero("Pedidos", (r.rodadas ?? 0).toLocaleString("pt-BR")),
        numero("Recargas", reais(r.recargas)),
      ]),
    );
    if (c?.pagamento?.ativo && c.plano !== "interno") {
      const pix = criar("button", { type: "button", className: "botao primario pequeno", id: "extrato-pix" }, ["Adicionar saldo por Pix"]);
      pix.addEventListener("click", ctx.pix);
      partes.push(criar("div", { className: "acoes" }, [pix]));
    }
    if (e.por_modelo.length) {
      const linhas = e.por_modelo.map((m) =>
        criar("tr", {}, [
          criar("th", { scope: "row", textContent: m.modelo }),
          criar("td", { textContent: m.tipo_modelo ?? "—" }),
          criar("td", { className: "num", textContent: m.rodadas.toLocaleString("pt-BR") }),
          criar("td", { className: "num", textContent: reais(m.cobrado) }),
          criar("td", { className: "num", textContent: reais(m.devolvido) }),
          criar("td", { className: "num", textContent: reais(m.liquido) }),
        ]),
      );
      const cab = ["Modelo de IA", "Tipo", "Pedidos", "Cobrado", "Devolvido", "Total"].map((t, i) => criar("th", { scope: "col", className: i >= 2 ? "num" : "", textContent: t }));
      partes.push(
        criar("h2", { id: "t-por-modelo", textContent: "Por modelo de IA" }),
        criar("div", { className: "tabela", attrs: { role: "region", "aria-labelledby": "t-por-modelo", tabindex: "0" } }, [
          criar("table", {}, [criar("thead", {}, [criar("tr", {}, cab)]), criar("tbody", {}, linhas)]),
        ]),
      );
    }
    partes.push(criar("h2", { id: "t-rodadas", tabIndex: -1, textContent: "Pedidos, recargas e ajustes" }));
    const lista = criar("ul", { className: "linhas", id: "extrato-itens", attrs: { "aria-labelledby": "t-rodadas" } }, e.itens.map(linhaItem));
    if (e.itens.length) partes.push(lista);
    else partes.push(criar("p", { className: "vazio-linhas fraco", textContent: `Nenhum movimento ${PERIODOS[String(e.dias)] ?? "neste período"}.` }));
    let proximo = e.proximo;
    if (proximo !== null) {
      const mais = criar("button", { type: "button", className: "botao", id: "extrato-mais" }, ["Mostrar mais"]);
      mais.addEventListener("click", async () => {
        mais.disabled = true;
        mais.setAttribute("aria-busy", "true");
        try {
          const prox = await api<Extrato>(`/api/extrato?dias=${dias}&antes=${proximo}`);
          const novos = prox.itens.map(linhaItem);
          lista.append(...novos);
          proximo = prox.proximo;
          // o foco vai para o primeiro item novo (o leitor de tela continua dali)
          const primeiro = novos[0]?.querySelector<HTMLElement>(".linha-titulo");
          if (primeiro) {
            primeiro.tabIndex = -1;
            primeiro.focus();
          }
          if (proximo === null) {
            mais.remove();
            if (!primeiro) el("t-rodadas").focus();
          }
        } catch (erro) {
          ctx.erro(erro, el("extratos-msg"));
        } finally {
          mais.disabled = false;
          mais.setAttribute("aria-busy", "false");
        }
      });
      partes.push(criar("div", { className: "acoes" }, [mais]));
    }
    alvo.replaceChildren(...partes);
    alvo.setAttribute("aria-busy", "false");
  };

  const carregar = async () => {
    const minha = ++geracao;
    alvo.setAttribute("aria-busy", "true");
    try {
      const e = await api<Extrato>(`/api/extrato?dias=${dias}`);
      if (minha !== geracao) return; // outro período foi escolhido no meio
      el("extratos-msg").hidden = true;
      desenhar(e);
      carregado = true;
    } catch (e) {
      if (minha !== geracao) return;
      if (!carregado) alvo.replaceChildren(); // sem esqueleto sem fim: fica a mensagem
      alvo.setAttribute("aria-busy", "false");
      ctx.erro(e, el("extratos-msg"));
    }
  };

  filtros.addEventListener("click", (ev) => {
    const b = (ev.target as Element).closest<HTMLButtonElement>("button[data-dias]");
    if (!b || Number(b.dataset.dias) === dias) return;
    dias = Number(b.dataset.dias);
    for (const x of Array.from(filtros.querySelectorAll("button"))) x.setAttribute("aria-pressed", String(x === b));
    atualizarCsv();
    void carregar();
  });
  atualizarCsv();
  // a recarga pode deixar de valer para a conta (o painel mudou o plano): o botão some sem redesenhar a tela
  aoMudarConta((c) => {
    const pix = document.getElementById("extrato-pix");
    if (pix) pix.hidden = !c.pagamento?.ativo || c.plano === "interno";
  });

  return {
    // a cada visita à tela o extrato vem de novo (um pedido novo ou uma recarga mudam o saldo); na 1ª, já pedido no
    // <head>
    abrir: () => carregar(),
    recarregar: () => {
      if (carregado) void carregar();
    },
  };
}
