// Entregas (17/10/2026): a revisão dos vídeos entregues no piloto, critério a critério, com a evidência (o que a pessoa
// viu no vídeo inteiro), e os pedidos de correção dos clientes. A situação de um vídeo olha só os obrigatórios; a
// conferência automática de cor aparece como observação, sem decidir. O crédito de uma correção, só o dono.
import { anunciar, api, aviso, criar, dialogo, el, icone, marcarErro, ocupado, plural, reais, textoErro, urlSegura } from "./comum";
import { type Controle, controle, dolares, duracao, erroDeAcao, pode, semLinhas, tabela, tempoCurto, trocar } from "./painel-base";

type Criterio = { id: string; texto: string; tipo: "obrigatorio" | "preferencia"; origem: string; fonte: string; como: string; grupo: string | null };
type Avaliacao = { criterio: string; estado?: string; modo: string; avaliador: string; evidencia: string | null; criado?: number };
type Situacao = { situacao: string; texto: string; obrigatorios: { total: number; atendidos: number; sem_decisao: number }; preferencias: { total: number; atendidas: number } };
type Retorno = { usavel: number; motivo: string | null; criterio: string | null; comentario: string | null };
type VideoEntregue = {
  id: string;
  arquivo: string;
  entregue: number;
  grupo: string | null;
  criterios: string[];
  decisoes: Record<string, Avaliacao>;
  observacoes: Avaliacao[];
  situacao: Situacao | null;
  retorno: Retorno | null;
  correcao: { id: string; status: string; classificacao: string | null } | null;
};
type PedidoEntregue = { lote: string; email: string | null; criado: number; status: string; prompt: string; revisao: string | null; versao: number | null; criterios: Criterio[]; videos: VideoEntregue[] };
type Tempo = { n: number; p50_s: number | null };
type Piloto = {
  pedidos: { total: number; revisar: number; seguir: number; esperando_cliente: number; decisoes_materiais: number; aprovados_pelo_cliente: number; textos_mudados: number };
  videos: { entregues: number; com_retorno: number; sem_retorno: number; usaveis: number; precisam_mudar: number };
  primeira_entrega: { pedidos_respondidos: number; utilizaveis: number };
  correcoes: { total: number; abertas: number; creditos: number };
  tempos: { ate_aprovacao_do_cliente: Tempo; ate_peca_utilizavel: Tempo };
  custo: { gpu_usd: number; gpu_usd_por_video_entregue: number | null; gpu_usd_por_video_utilizavel: number | null };
  conferencia: Record<string, number>;
  nao_medido: string[];
};
type Entregas = {
  pedidos: PedidoEntregue[];
  correcoes: Correcao[];
  piloto: Piloto;
  estados: Record<string, string>;
  motivos: Record<string, string>;
  classificacoes: Record<string, string>;
  com_credito: string[];
};
type Correcao = {
  id: string;
  job_id: string;
  lote_id: string;
  email: string | null;
  arquivo: string | null;
  motivo_texto: string | null;
  criterio: string | null;
  comentario: string | null;
  status: string;
  classificacao: string | null;
  credito: number | null;
  decidido_por: string | null;
  criado: number;
  valor_video: number;
};

const TIPO: Record<string, string> = { obrigatorio: "Obrigatório", preferencia: "Preferência" };
const ORIGEM: Record<string, string> = { cliente: "cliente", estilo: "estilo", sistema: "serviço" };

function textoDecisao(d: Avaliacao | undefined, estados: Record<string, string>): string {
  if (!d) return "Sem decisão";
  return `${estados[d.estado ?? ""] ?? d.estado} · ${d.avaliador.replace(/^equipe:/, "")}${d.evidencia ? ` · ${d.evidencia}` : ""}`;
}

/** A janela de um vídeo: o vídeo (link de 10 min, na auditoria), cada critério com a decisão atual e o formulário. */
function janelaVideo(p: PedidoEntregue, v: VideoEntregue, dados: Entregas, origem: HTMLElement, aoMudar: () => void): void {
  const { d, corpo } = dialogo(`Revisar ${v.arquivo}`, "entrega-video");
  let mudou = false;
  d.addEventListener("close", () => {
    d.remove();
    origem.focus();
    if (mudou) aoMudar();
  });
  const area = criar("div", { className: "area-video" });
  const ver = criar("button", { type: "button", className: "botao pequeno", dados: { foco: `${v.id}:ver-video` } }, [icone("play"), "Ver o vídeo inteiro"]);
  ver.addEventListener("click", async () => {
    ocupado(ver, true);
    try {
      const r = await api<{ video: string; expira_s: number }>(`api/jobs/${encodeURIComponent(v.id)}/video`);
      const src = urlSegura(r.video);
      area.replaceChildren(src ? criar("video", { src, controls: true, preload: "metadata", attrs: { "aria-label": `Vídeo ${v.arquivo}` } }) : criar("p", { textContent: "Endereço do vídeo inválido." }));
      ver.remove();
    } catch (e) {
      ocupado(ver, false);
      aviso(area, textoErro(e), "erro");
    }
  });
  const situacao = criar("p", { className: "situacao-video", textContent: v.situacao ? `${v.situacao.texto} (${v.situacao.obrigatorios.atendidos} de ${v.situacao.obrigatorios.total} obrigatórios)` : "Pedido sem critérios (anterior às versões)." });
  corpo.append(criar("p", { className: "fraco", textContent: `Pedido: “${p.prompt}”${p.versao ? ` · versão ${p.versao}` : ""}. Cada link vale 10 min e fica na auditoria.` }), ver, area, situacao);
  const criterios = p.criterios.filter((c) => v.criterios.includes(c.id));
  const lista = criar("ul", { className: "criterios-video" });
  for (const c of criterios) {
    const atual = criar("p", { className: "fraco", textContent: textoDecisao(v.decisoes[c.id], dados.estados) });
    const base = `${v.id}-${c.id.replace(/[^a-z0-9]/gi, "-")}`;
    const estado = criar("select", { id: `${base}-estado` }, Object.entries(dados.estados).map(([k, t]) => criar("option", { value: k, textContent: t })));
    const evidencia = criar("input", { id: `${base}-evidencia`, type: "text", maxLength: 500, autocomplete: "off", placeholder: "O que você viu no vídeo" });
    const salvar = criar("button", { type: "submit", className: "botao pequeno primario", dados: { foco: `${v.id}:salvar:${c.id}` } }, ["Salvar"]);
    const msg = criar("div", { className: "aviso", hidden: true, attrs: { role: "alert" } });
    const form = criar("form", { className: "criterio-form", noValidate: true }, [
      criar("div", { className: "campo" }, [criar("label", { htmlFor: estado.id, textContent: "Estado" }), estado]),
      criar("div", { className: "campo" }, [criar("label", { htmlFor: evidencia.id, textContent: "Evidência" }), evidencia]),
      salvar,
    ]);
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const texto = evidencia.value.trim();
      if (texto.length < 3) {
        marcarErro(evidencia, true);
        aviso(msg, "Escreva o que você viu no vídeo (a decisão precisa da evidência).", "erro");
        evidencia.focus();
        return;
      }
      ocupado(salvar, true);
      try {
        const r = await api<{ situacao: Situacao }>(`api/jobs/${encodeURIComponent(v.id)}/criterios`, { method: "POST", body: JSON.stringify({ criterio: c.id, estado: estado.value, evidencia: texto }) });
        atual.textContent = `${dados.estados[estado.value]} · você · ${texto}`;
        situacao.textContent = `${r.situacao.texto} (${r.situacao.obrigatorios.atendidos} de ${r.situacao.obrigatorios.total} obrigatórios)`;
        mudou = true;
        aviso(msg, "");
        evidencia.value = "";
        anunciar(`Critério salvo. ${r.situacao.texto}.`);
      } catch (e) {
        aviso(msg, textoErro(e), "erro");
      } finally {
        ocupado(salvar, false);
      }
    });
    evidencia.addEventListener("input", () => marcarErro(evidencia, false));
    lista.append(
      criar("li", {}, [
        criar("p", {}, [criar("strong", { textContent: c.texto }), criar("span", { className: "fraco", textContent: ` · ${TIPO[c.tipo]} · ${ORIGEM[c.origem] ?? c.origem} (${c.fonte})` })]),
        atual,
        form,
        msg,
      ]),
    );
  }
  corpo.append(criar("h3", { textContent: "Critérios" }), criterios.length ? lista : semLinhas("Sem critérios neste pedido."));
  if (v.observacoes.length)
    corpo.append(
      criar("h3", { textContent: "Observações automáticas (não decidem)" }),
      criar("ul", { className: "fraco" }, v.observacoes.map((o) => criar("li", { textContent: `${o.criterio}: ${o.evidencia ?? ""} (${o.avaliador})` }))),
    );
  if (v.retorno) {
    const r = v.retorno;
    const t = r.usavel ? "Pode usar como está." : `Precisa mudar: ${dados.motivos[r.motivo ?? ""] ?? r.motivo}${r.criterio ? ` · critério ${r.criterio}` : ""}${r.comentario ? ` · “${r.comentario}”` : ""}`;
    corpo.append(criar("h3", { textContent: "Retorno do cliente" }), criar("p", { textContent: t }));
  }
  d.showModal();
  ver.focus();
}

/** A decisão de um pedido de correção: a classificação, o motivo (fica no painel) e, se couber, o crédito. */
function janelaDecisao(x: Correcao, dados: Entregas, origem: HTMLElement, aoMudar: () => void): void {
  const { d, corpo } = dialogo(`Correção de ${x.arquivo ?? x.job_id}`, "entrega-correcao");
  const base = d.getAttribute("aria-labelledby");
  d.addEventListener("close", () => {
    d.remove();
    origem.focus();
  });
  const classe = criar("select", { id: `${base}-classe` }, Object.entries(dados.classificacoes).map(([k, t]) => criar("option", { value: k, textContent: t })));
  const nota = criar("textarea", { id: `${base}-nota`, maxLength: 500, required: true });
  const creditar = criar("input", { type: "checkbox", id: `${base}-credito` });
  const rotuloCredito = criar("label", { className: "marcacao", htmlFor: creditar.id }, [creditar, criar("span", { textContent: x.valor_video ? `Devolver ${reais(x.valor_video)} (o valor cobrado do vídeo) como crédito no saldo` : "Este vídeo não foi cobrado: não há crédito a devolver" })]);
  const ajustar = () => {
    const pode_ = dados.com_credito.includes(classe.value) && x.valor_video > 0;
    creditar.disabled = !pode_;
    if (!pode_) creditar.checked = false;
  };
  classe.addEventListener("change", ajustar);
  ajustar();
  const msg = criar("div", { className: "aviso", hidden: true, attrs: { role: "alert" } });
  const cancelar = criar("button", { type: "button", className: "botao", textContent: "Cancelar" });
  const salvar = criar("button", { type: "submit", className: "botao primario", textContent: "Salvar a decisão" });
  cancelar.addEventListener("click", () => d.close());
  const form = criar("form", { className: "pilha", noValidate: true }, [
    criar("p", { textContent: `${x.motivo_texto ?? "Sem motivo"}${x.criterio ? ` · critério ${x.criterio}` : ""}${x.comentario ? ` · “${x.comentario}”` : ""}` }),
    criar("div", { className: "campo" }, [criar("label", { htmlFor: classe.id, textContent: "Classificação" }), classe]),
    criar("div", { className: "campo" }, [criar("label", { htmlFor: nota.id, textContent: "Motivo da decisão (fica no painel; o cliente vê só a classificação)" }), nota]),
    rotuloCredito,
    msg,
    criar("div", { className: "dialogo-acoes" }, [cancelar, salvar]),
  ]);
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (nota.value.trim().length < 3) {
      marcarErro(nota, true);
      aviso(msg, "Escreva o motivo da decisão.", "erro");
      nota.focus();
      return;
    }
    ocupado(salvar, true);
    try {
      const r = await api<{ credito: number; ja: boolean }>(`api/correcoes/${encodeURIComponent(x.id)}/decidir`, {
        method: "POST",
        body: JSON.stringify({ classificacao: classe.value, nota: nota.value.trim(), creditar: creditar.checked }),
      });
      anunciar(r.ja ? "Esta correção já tinha sido decidida." : r.credito ? `Decisão salva, com ${reais(r.credito)} de crédito.` : "Decisão salva.");
      d.close();
      aoMudar();
    } catch (e) {
      ocupado(salvar, false);
      erroDeAcao(e, msg);
    }
  });
  corpo.append(form);
  d.showModal();
  classe.focus();
}

/** As métricas do piloto (docs/16): só o que o banco mede; o resto aparece como não medido. */
function resumoPiloto(m: Piloto): HTMLElement[] {
  const t = (x: Tempo) => (x.p50_s == null ? "sem amostra" : `${duracao(x.p50_s)} (mediana de ${x.n})`);
  const linhas = [
    `Pedidos com versões: ${m.pedidos.total} (${m.pedidos.seguir} seguir, ${m.pedidos.revisar} revisar) · esperando o cliente: ${m.pedidos.esperando_cliente} · decisões pedidas por mudança na adaptação: ${m.pedidos.decisoes_materiais} · textos mudados: ${m.pedidos.textos_mudados}`,
    `Vídeos entregues: ${m.videos.entregues} · com retorno: ${m.videos.com_retorno} (pode usar: ${m.videos.usaveis}; precisa mudar: ${m.videos.precisam_mudar}) · sem retorno: ${m.videos.sem_retorno}`,
    `1ª entrega utilizável: ${m.primeira_entrega.utilizaveis} de ${m.primeira_entrega.pedidos_respondidos} pedidos com todos os vídeos respondidos`,
    `Revisão da equipe (vídeos listados): atende ${m.conferencia.atende ?? 0} · em parte ${m.conferencia.atende_em_parte ?? 0} · não atende ${m.conferencia.nao_atende ?? 0} · não verificado ${m.conferencia.nao_verificado ?? 0}`,
    `Correções: ${m.correcoes.total} (${m.correcoes.abertas} abertas) · créditos: ${reais(m.correcoes.creditos)}`,
    `Tempo até a aprovação do cliente: ${t(m.tempos.ate_aprovacao_do_cliente)} · até a peça utilizável: ${t(m.tempos.ate_peca_utilizavel)}`,
    `GPU: ${dolares(m.custo.gpu_usd)} · por vídeo entregue: ${dolares(m.custo.gpu_usd_por_video_entregue)} · por vídeo utilizável: ${dolares(m.custo.gpu_usd_por_video_utilizavel)}`,
  ];
  return [criar("ul", { className: "fraco" }, linhas.map((x) => criar("li", { textContent: x }))), criar("p", { className: "fraco", textContent: `Não medido aqui: ${m.nao_medido.join(", ")}.` })];
}

/** Entregas: as métricas do piloto, os pedidos de correção e, por pedido, os vídeos entregues com a situação. */
export function criarEntregas(): Controle {
  const atualizar = () => void c.atualizar();
  const carregar = async () => {
    const ent = await api<Entregas>("api/entregas");
    const decide = pode("POST /api/correcoes/{correcao_id}/decidir");
    const linhasCor = ent.correcoes.map((x) => {
      let acao: Node | string = x.status === "aberta" ? "Aberta" : "—";
      if (x.status === "aberta" && decide) {
        const b = criar("button", { type: "button", className: "botao pequeno", dados: { foco: `${x.id}:decidir` } }, ["Decidir"]);
        b.addEventListener("click", () => janelaDecisao(x, ent, b, atualizar));
        acao = b;
      }
      const situacao = x.status === "aberta" ? "Aberta" : `${ent.classificacoes[x.classificacao ?? ""]?.split(":")[0] ?? x.classificacao}${x.credito ? ` · crédito ${reais(x.credito)}` : ""}`;
      return {
        celulas: [tempoCurto(x.criado), x.email ?? "—", x.arquivo ?? x.job_id, [x.motivo_texto, x.criterio, x.comentario && `“${x.comentario}”`].filter(Boolean).join(" · "), situacao, acao],
        classe: x.status === "aberta" ? "sinal" : "",
      };
    });
    const pedidos = ent.pedidos.map((p) => {
      const linhas = p.videos.map((v) => {
        const b = criar("button", { type: "button", className: "botao pequeno", dados: { foco: `${v.id}:revisar` } }, ["Revisar"]);
        b.addEventListener("click", () => janelaVideo(p, v, ent, b, atualizar));
        const ret = !v.retorno ? "—" : v.retorno.usavel ? "Pode usar" : `Precisa mudar: ${ent.motivos[v.retorno.motivo ?? ""] ?? v.retorno.motivo}`;
        return {
          celulas: [v.arquivo, v.situacao ? v.situacao.texto : "Sem critérios", v.situacao ? `${v.situacao.obrigatorios.atendidos}/${v.situacao.obrigatorios.total}` : "—", ret, b],
          classe: v.situacao?.situacao === "nao_atende" ? "sinal" : "",
        };
      });
      return criar("article", { className: "entrega-pedido" }, [
        criar("h3", {}, [tempoCurto(p.criado), ` · ${p.email ?? "conta sem e-mail"} · ${plural(p.videos.length, "vídeo", "vídeos")}`]),
        criar("p", { className: "pedido", textContent: p.prompt }),
        criar("p", { className: "fraco", textContent: p.versao ? `Versão ${p.versao} · ${p.revisao === "revisar" ? "revisado pelo cliente antes de gerar" : "seguir com a adaptação"}` : "Pedido anterior às versões: sem critérios." }),
        criar("div", { className: "tabela", attrs: { role: "region", "aria-label": `Vídeos do pedido de ${p.email ?? "conta sem e-mail"}`, tabindex: "0" } }, [
          tabela([["Vídeo", false], ["Situação", false], ["Obrigatórios", true], ["Retorno do cliente", false], ["Ação", false]], linhas, `Vídeos do pedido de ${p.email ?? "conta sem e-mail"}`),
        ]),
      ]);
    });
    trocar(el("entregas-piloto"), resumoPiloto(ent.piloto), JSON.stringify(ent.piloto));
    trocar(
      el("entregas-correcoes"),
      [linhasCor.length ? tabela([["Pedida", false], ["Conta", false], ["Vídeo", false], ["O que o cliente disse", false], ["Situação", false], ["Ação", false]], linhasCor, "Pedidos de correção") : semLinhas("Nenhum pedido de correção.")],
      JSON.stringify(ent.correcoes),
    );
    trocar(el("entregas-lista"), pedidos.length ? pedidos : [semLinhas("Nenhum vídeo entregue nos últimos 14 dias.")], JSON.stringify(ent.pedidos));
  };
  const c = controle({ regiao: el("entregas-lista"), ocupado: el("entregas-dados"), carregar });
  return c;
}
