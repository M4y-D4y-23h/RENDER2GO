// Retorno sobre cada vídeo entregue (17/10/2026): "Pode usar como está?" e, se não, "O que precisa mudar?" (o motivo,
// o critério do pedido que ficou de fora e um comentário curto). Com o retorno negativo, "Pedir correção": um pedido
// por vídeo, dentro do prazo; a equipe classifica (falha técnica, fora do combinado ou pedido novo) e responde aqui.
// A cobrança do vídeo não muda com o retorno (ADR 0012); o crédito de uma correção aceita aparece no extrato.
import { anunciar, api, aviso, confirmar, contador, criar, dialogo, marcarErro, ocupado, textoErro } from "./comum";

export type RetornoVideo = { usavel: boolean; motivo: string | null; motivo_texto?: string | null; criterio: string | null; comentario: string | null };
export type CorrecaoVideo = { status: "aberta" | "decidida"; texto: string; credito: number };
export type CriterioPedido = { id: string; texto: string; tipo: "obrigatorio" | "preferencia"; grupo: string | null };
type VideoComRetorno = { id?: string; arquivo: string; retorno?: RetornoVideo | null; correcao?: CorrecaoVideo | null; pode_corrigir?: boolean; grupo?: string };

let motivos: { id: string; texto: string }[] = [];
let politica = { ligada: false, prazo_h: 72 };

/** Os motivos de "O que precisa mudar?" e a política de correção, do catálogo (/api/receitas). */
export function configurarRetorno(r: { motivos_retorno?: { id: string; texto: string }[]; correcao?: { ligada: boolean; prazo_h: number } }): void {
  motivos = r.motivos_retorno ?? [];
  politica = r.correcao ?? politica;
}

/** "O que precisa mudar?": o motivo (obrigatório), o critério do pedido e um comentário curto. */
function janelaMudar(v: VideoComRetorno, criterios: CriterioPedido[], origem: HTMLElement, aoSalvar: () => void): void {
  const { d, corpo } = dialogo(`O que precisa mudar em ${v.arquivo}?`, "retorno-video");
  const base = d.getAttribute("aria-labelledby");
  const opcoes = motivos.map((m) =>
    criar("label", { className: "marcacao" }, [criar("input", { type: "radio", name: `${base}-motivo`, value: m.id, checked: v.retorno?.motivo === m.id }), criar("span", { textContent: m.texto })]),
  );
  const grupo = criar("fieldset", { className: "motivos-retorno" }, [criar("legend", { textContent: "O que aconteceu?" }), criar("div", { className: "marcacoes" }, opcoes)]);
  const doVideo = criterios.filter((c) => c.grupo === null || c.grupo === (v.grupo ?? "pedido"));
  const selCriterio = criar("select", { id: `${base}-criterio` }, [
    criar("option", { value: "", textContent: "Nenhum em especial" }),
    ...doVideo.map((c) => criar("option", { value: c.id, textContent: c.texto, selected: v.retorno?.criterio === c.id })),
  ]);
  const comentario = criar("textarea", { id: `${base}-comentario`, maxLength: 300, spellcheck: true, autocapitalize: "sentences" });
  comentario.value = v.retorno?.comentario ?? "";
  const cont = criar("span", { className: "contador", id: `${base}-contador` });
  comentario.setAttribute("aria-describedby", `${base}-contador`);
  contador(comentario, cont);
  const msg = criar("div", { className: "aviso", hidden: true, attrs: { role: "alert" } });
  const cancelar = criar("button", { type: "button", className: "botao", textContent: "Cancelar" });
  const salvar = criar("button", { type: "submit", className: "botao primario", textContent: "Enviar" });
  cancelar.addEventListener("click", () => d.close());
  const itens: HTMLElement[] = [grupo];
  if (doVideo.length)
    itens.push(criar("div", { className: "campo" }, [criar("label", { htmlFor: selCriterio.id, textContent: "Qual parte do pedido ficou de fora? (opcional)" }), selCriterio]));
  itens.push(
    criar("div", { className: "campo" }, [criar("label", { htmlFor: comentario.id, textContent: "Comentário (opcional)" }), comentario, criar("div", { className: "linha-campo" }, [criar("span"), cont])]),
    msg,
    criar("div", { className: "dialogo-acoes" }, [cancelar, salvar]),
  );
  const form = criar("form", { noValidate: true, className: "pilha" }, itens);
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const motivo = grupo.querySelector<HTMLInputElement>("input:checked")?.value;
    if (!motivo) {
      aviso(msg, "Escolha o que aconteceu.", "erro");
      grupo.querySelector<HTMLInputElement>("input")?.focus();
      return;
    }
    ocupado(salvar, true);
    try {
      await api(`/api/videos/${encodeURIComponent(v.id ?? "")}/retorno`, {
        method: "POST",
        body: JSON.stringify({ usavel: false, motivo, criterio: selCriterio.value || null, comentario: comentario.value.trim() || null }),
      });
      d.close();
      anunciar("Obrigado: o que precisa mudar ficou registrado.");
      aoSalvar();
    } catch (e) {
      ocupado(salvar, false);
      marcarErro(comentario, true);
      aviso(msg, textoErro(e), "erro");
    }
  });
  comentario.addEventListener("input", () => marcarErro(comentario, false));
  d.addEventListener("close", () => {
    d.remove();
    origem.focus();
  });
  corpo.append(form);
  d.showModal();
  (grupo.querySelector<HTMLInputElement>("input:checked") ?? grupo.querySelector<HTMLInputElement>("input"))?.focus();
}

/** O retorno de um vídeo entregue no cartão do pedido: a pergunta, a resposta dada e a correção. */
export function retornoDoVideo(v: VideoComRetorno, criterios: CriterioPedido[], recarregar: () => void): HTMLElement | null {
  if (!v.id) return null;
  const id = v.id;
  const caixa = criar("div", { className: "retorno-video" });
  const responder = async (usavel: boolean, botao: HTMLButtonElement) => {
    if (!usavel) {
      janelaMudar(v, criterios, botao, recarregar);
      return;
    }
    ocupado(botao, true);
    try {
      await api(`/api/videos/${encodeURIComponent(id)}/retorno`, { method: "POST", body: JSON.stringify({ usavel: true }) });
      anunciar("Obrigado pela resposta.");
      recarregar();
    } catch (e) {
      ocupado(botao, false);
      anunciar(textoErro(e), true);
    }
  };
  const sim = criar("button", { type: "button", className: "botao pequeno", dados: { foco: `${id}:usavel` } }, ["Sim, pode usar"]);
  const nao = criar("button", { type: "button", className: "botao pequeno", dados: { foco: `${id}:mudar` } }, ["Não, precisa mudar"]);
  sim.addEventListener("click", () => void responder(true, sim));
  nao.addEventListener("click", () => void responder(false, nao));
  if (!v.retorno) {
    caixa.append(criar("p", { className: "pergunta-retorno", textContent: "Pode usar como está?" }), criar("div", { className: "acoes" }, [sim, nao]));
    return caixa;
  }
  const r = v.retorno;
  caixa.append(criar("p", { className: "fraco", textContent: r.usavel ? "Você marcou: pode usar como está." : `Você marcou: precisa mudar (${r.motivo_texto ?? r.motivo}).` }));
  if (v.correcao) {
    const c = criar("p", { className: "aviso" });
    aviso(c, v.correcao.texto, v.correcao.status === "aberta" ? "info" : "ok");
    caixa.append(c);
    return caixa;
  }
  const mudar = criar("button", { type: "button", className: "botao fantasma pequeno", dados: { foco: `${id}:trocar` } }, ["Mudar a resposta"]);
  mudar.addEventListener("click", () => {
    // trocar a resposta: as duas opções de novo
    caixa.replaceChildren(criar("p", { className: "pergunta-retorno", textContent: "Pode usar como está?" }), criar("div", { className: "acoes" }, [sim, nao]));
    sim.focus();
  });
  const acoes: HTMLElement[] = [mudar];
  if (v.pode_corrigir && politica.ligada) {
    const corrigir = criar("button", { type: "button", className: "botao pequeno", dados: { foco: `${id}:corrigir` } }, ["Pedir correção"]);
    corrigir.addEventListener("click", async () => {
      const ok = await confirmar({
        titulo: "Pedir a correção deste vídeo?",
        texto: [
          `Um pedido de correção por vídeo, até ${politica.prazo_h} h depois da entrega. A equipe confere o vídeo e o pedido que você aprovou.`,
          "Se o vídeo saiu com defeito ou não atendeu ao pedido, o valor dele volta para o seu saldo como crédito. Se o que você quer agora não estava no pedido, é um pedido novo.",
          "Para refazer, envie a foto de novo num pedido novo: as fotos do pedido são apagadas quando ele termina.",
        ],
        confirmar: "Pedir correção",
      });
      if (!ok) return;
      ocupado(corrigir, true);
      try {
        await api(`/api/videos/${encodeURIComponent(id)}/correcao`, { method: "POST" });
        anunciar("Pedido de correção enviado. A equipe responde aqui.");
        recarregar();
      } catch (e) {
        ocupado(corrigir, false);
        anunciar(textoErro(e), true);
      }
    });
    acoes.unshift(corrigir);
  }
  caixa.append(criar("div", { className: "acoes" }, acoes));
  return caixa;
}
