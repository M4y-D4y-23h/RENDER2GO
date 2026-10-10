// Tela "Modelos de IA" (/modelos): o catálogo inteiro (render2go_servico/modelos.py), agrupado pelo que o cliente
// envia. O disponível leva, com um clique, à tela de pedidos com aquela opção escolhida; os em breve mostram os
// modelos previstos (só com a licença comercial conferida, docs/12) e o "Tenho interesse".
import { type Finalidade, iconeDe, obterReceitas } from "./catalogo";
import { aviso, criar, el, plural } from "./comum";
import { botaoInteresse } from "./interesse";

export function criarModelosIA(ctx: { usar: (finalidade: string) => void; erro: (e: unknown, alvo: HTMLElement) => void }): { abrir: () => Promise<void> } {
  const alvo = el("modelos-lista");
  let pronto = false;

  const cartao = (f: Finalidade): HTMLElement => {
    const idTitulo = `modelo-${f.id}`;
    const cab = criar("div", { className: "modelo-cab" }, [
      iconeDe(f),
      criar("div", { className: "modelo-nome" }, [criar("h3", { id: idTitulo, textContent: f.titulo }), criar("p", { className: "bloco-tipo", textContent: f.tipo })]),
      criar("span", { className: f.disponivel ? "selo ok" : "selo", textContent: f.disponivel ? "Disponível" : "Em breve" }),
    ]);
    const corpo: HTMLElement[] = [criar("p", { className: "fraco", textContent: f.descricao })];
    const modelos = f.disponivel ? f.modelos : f.previstos;
    if (modelos.length) {
      corpo.push(
        criar("ul", { className: "modelos-da-opcao", attrs: { "aria-label": f.disponivel ? `Modelos de ${f.titulo}` : `Modelos previstos para ${f.titulo}` } },
          modelos.map((m) =>
            criar("li", {}, [
              criar("p", {}, [criar("strong", { textContent: m.nome }), f.disponivel ? "" : " (previsto)"]),
              m.melhor_para ? criar("p", { className: "fraco", textContent: `Melhor para: ${m.melhor_para}.` }) : null,
              m.licenca ? criar("p", { className: "fraco", textContent: `Licença: ${m.licenca}.` }) : null,
            ]),
          ),
        ),
      );
    }
    let acao: HTMLElement;
    if (f.disponivel) {
      acao = criar("button", { type: "button", className: "botao primario", dados: { foco: `usar:${f.id}` } }, ["Usar este modelo"]);
      acao.setAttribute("aria-describedby", idTitulo);
      acao.addEventListener("click", () => ctx.usar(f.id));
    } else acao = botaoInteresse(f.id, f.titulo, (t) => aviso(el("msg-painel"), t, "erro"));
    return criar("li", { className: `cartao modelo${f.disponivel ? " disponivel" : ""}`, dados: { finalidade: f.id } }, [cab, ...corpo, criar("div", { className: "acoes" }, [acao])]);
  };

  const desenhar = async () => {
    const r = await obterReceitas();
    const todas = r.finalidades ?? [];
    const disponiveis = todas.filter((f) => f.disponivel).length;
    const grupos = Object.entries(r.entradas ?? {}).map(([entrada, rotulo]) => {
      const itens = todas.filter((f) => f.entrada === entrada);
      // dentro do grupo, o que já pode ser usado vem primeiro
      itens.sort((a, b) => Number(b.disponivel) - Number(a.disponivel));
      const id = `grupo-modelos-${entrada}`;
      return itens.length ? criar("section", { className: "grupo-modelos", attrs: { "aria-labelledby": id } }, [criar("h2", { id, textContent: rotulo }), criar("ul", { className: "cartoes-modelos" }, itens.map(cartao))]) : null;
    });
    alvo.replaceChildren(
      criar("p", { className: "resumo-modelos" }, [criar("strong", { textContent: plural(disponiveis, "disponível agora", "disponíveis agora") }), ` · ${plural(todas.length - disponiveis, "em breve", "em breve")}`]),
      ...grupos.filter((g): g is HTMLElement => !!g),
    );
    alvo.setAttribute("aria-busy", "false");
    pronto = true;
  };

  return {
    abrir: async () => {
      if (pronto) return;
      try {
        await desenhar();
      } catch (e) {
        alvo.replaceChildren(); // sem esqueleto sem fim: fica a mensagem, com "Tentar de novo"
        alvo.setAttribute("aria-busy", "false");
        ctx.erro(e, el("modelos-msg"));
      }
    },
  };
}
