// Painel: os pods ligados agora (desligar um, parada de emergência e retomar), o relatório das GPUs (tempo
// ligado, vídeos e custo estimado) e as recargas por Pix.
import { anunciar, api, baixarJson, confirmar, criar, curto, el, icone, ocupado, plural, quando, reais, tempo } from "./comum";
import { type Controle, controle, dolares, duracao, erroDeAcao, erroDeCarga, pode, semLinhas, tabela, tempoCurto, trocar } from "./painel-base";

type Pod = { id: string; worker: string | null; gpu: string | null; usd_h: number | null; estado: string; criado: number; pronto_em: number | null; datacentro: string | null; em_andamento: number };
type Comando = { id: string; tipo: string; alvo: string | null; criado: number; por: string | null; executado_em: number | null; resultado: string | null };
type Hibernado = { id: string; gpu: string | null; usd_h: number | null; parado_em: number | null; voltas: number | null; falhas: number | null; em_uso: boolean | null; ultima_falha?: string | null; ultima_falha_em?: number | null };
type RunPod = { saldo_usd: number | null; saldo_em: number | null; prazo_novo_pod_s: number | null; margem_s: number; hibernados: Hibernado[]; hibernados_max: number; volume_gb: number };
type Pods = { pods: Pod[]; pausado: boolean; pausado_em: number | null; orquestrador_visto_s: number | null; comandos: Comando[]; runpod: RunPod | null };
type Gerador = {
  id: string;
  pod: string | null;
  gpu: string | null;
  datacentro: string | null;
  usd_h: number | null;
  estado: string;
  motivo: string | null;
  inicio: number;
  ligado_s: number;
  ligado_estimado: boolean;
  subida_s: number | null;
  custo_usd: number | null;
  videos_ok: number;
  videos_erro: number;
  videos_andamento: number;
  traducoes: number;
  lotes: number;
  segundos_render: number;
  // sessões (17/10/2026): cada subida (do zero ou volta da hibernação) é uma linha, com os vídeos dela
  sessao?: number;
  subida_tipo?: string | null;
  primeira_entrega_s?: number | null;
  tentativas_render?: number | null;
  tentativas_falhas?: number | null;
  ocupacao?: number | null;
  ocioso_s?: number;
  custo_total_usd?: number | null;
  aproveitaveis?: number;
  incertezas?: string[];
  volume?: { inicio?: { motivo: string } | null; gravacao?: { motivo: string } | null } | null;
  volume_fim_motivo?: string | null;
};
type TotaisGpus = {
  pods: number;
  falharam: number;
  ligados_agora: number;
  horas: number;
  custo_usd: number;
  sem_preco: number;
  videos_ok: number;
  videos_erro: number;
  custo_por_video_usd: number | null;
  custo_total_usd?: number;
  custo_total_por_video_usd?: number | null;
  custo_total_por_aproveitavel_usd?: number | null;
  tentativas_render?: number;
  tentativas_falhas?: number;
  aproveitaveis?: number;
  retomadas?: number;
  ocioso_h?: number;
  sessoes_incertas?: number;
};
type RelatorioGpus = { desde: number; geradores: Gerador[]; totais: TotaisGpus };
type Pagamento = { id: string; cliente_id: string; email: string | null; valor: number; provedor: string; status: string; criado: number; creditado_em: number | null; documento: string | null };

const SITUACAO_POD: Record<string, string> = { subindo: "subindo", pronto: "ligado" };
const SITUACAO_GPU: Record<string, string> = { subindo: "subindo", pronto: "ligada", encerrado: "desligada", falhou: "falhou na subida" };
// o disco de volume nesta sessão (render2go_worker.volume, MOTIVOS): por que o cache foi ou não usado
const CACHE_GPU: Record<string, string> = {
  ok: "usado",
  primeira: "1ª subida",
  sem_local: "sem volume",
  sem_montagem: "sem montagem",
  vazio: "volume vazio",
  sem_marcador: "cópia incompleta",
  outra_versao: "outra versão",
  repo: "código não abriu",
  incompleto: "modelo faltando",
  ambiente_incompleto: "ambiente faltando",
  corrompido: "arquivo corrompido",
  outro_pod: "de outro pod",
  sem_espaco: "sem espaço",
  copia_falhou: "cópia falhou",
};

function cacheDaSessao(g: Gerador): string {
  const inicio = g.volume?.inicio?.motivo;
  const gravacao = g.volume?.gravacao?.motivo;
  if (!inicio && !gravacao) return "—";
  const partes = [inicio ? (CACHE_GPU[inicio] ?? inicio) : null, gravacao && gravacao !== "ok" ? `gravação: ${CACHE_GPU[gravacao] ?? gravacao}` : null];
  return partes.filter(Boolean).join("; ");
}
const COMANDOS: Record<string, string> = { desligar_pod: "Desligar pod", emergencia: "Parada de emergência", retomar: "Retomar" };
const SITUACAO_PIX: Record<string, string> = {
  criando: "criando",
  pendente: "aguardando o pagamento",
  aprovado: "pago e creditado",
  cancelado: "cancelado",
  recusado: "recusado",
  divergente: "valor divergente: confira",
  contestado: "contestado",
  devolvido: "devolvido",
  erro: "erro: confira",
};

function usdh(v: number | null): string {
  return v == null ? "—" : v.toFixed(2).replace(".", ",");
}

/** O estado do orquestrador numa linha curta (a altura fica reservada: nada se mexe a cada 30 s). */
function estadoOrquestrador(r: Pods): (Node | string)[] {
  const visto = r.orquestrador_visto_s;
  const vivo = visto !== null && visto < 120;
  const sinal = visto === null ? "O orquestrador ainda não deu sinal." : vivo ? `Orquestrador visto há ${Math.round(visto)} s.` : `Orquestrador sem sinal há ${Math.round(visto / 60)} min: confira o contêiner.`;
  if (r.pausado)
    return [criar("span", { className: "selo erro" }, [icone("bloquear"), "Pausado"]), `Desde ${r.pausado_em ? quando(r.pausado_em) : "agora"}: nenhum pod novo sobe até você retomar. ${sinal}`];
  if (!vivo) return [criar("span", { className: "selo alerta" }, [icone("alerta"), "Sem sinal"]), `${sinal} Sem ele, nenhum pod sobe nem desliga.`];
  return [criar("span", { className: "selo ok" }, [icone("ok"), "Funcionando"]), `O orquestrador liga pods conforme a fila. ${sinal}`];
}

/** Saldo do RunPod, o prazo que um pod novo teria por ele e os pods hibernados (ADR 0011). Com saldo
 * baixo, o desligamento automático encurta (o worker para 10 min antes): era o "pod que sai aos 40 min". */
function estadoRunPod(r: RunPod | null): (Node | string)[] {
  if (!r || r.saldo_usd == null) return ["Saldo do RunPod: o orquestrador ainda não consultou."];
  const prazo = r.prazo_novo_pod_s ?? 0;
  const gpus = r.hibernados.map((h) => (h.gpu ?? "GPU").replace(/^NVIDIA (GeForce )?/, ""));
  const voltas = r.hibernados.reduce((t, h) => t + (h.voltas ?? 0), 0);
  const falhas = r.hibernados.reduce((t, h) => t + (h.falhas ?? 0), 0);
  const historico = r.hibernados.length ? ` Voltaram ${plural(voltas, "vez", "vezes")}; agora, ${plural(falhas, "volta seguida", "voltas seguidas")} sem dar certo.` : "";
  // o motivo da última volta que não deu certo (ex.: a GPU do host foi alugada por outra pessoa enquanto o pod
  // estava parado): sem ele, a subida do zero que veio no lugar parecia o pod "voltando com o disco vazio"
  const ultima = r.hibernados.filter((h) => h.ultima_falha).sort((a, b) => (b.ultima_falha_em ?? 0) - (a.ultima_falha_em ?? 0))[0];
  const motivo = ultima ? ` Última volta que não deu certo${ultima.ultima_falha_em ? ` (${quando(ultima.ultima_falha_em)})` : ""}: ${ultima.ultima_falha}; no lugar, subiu um pod do zero.` : "";
  const hib = r.hibernados_max
    ? `Hibernados: ${r.hibernados.length} de ${r.hibernados_max}${gpus.length ? ` (${gpus.join(", ")})` : ""}; voltam em ~1 min quando o host tem a GPU livre.${historico}${motivo} Disco de volume: ${r.volume_gb} GB por pod, ~${dolares(r.volume_gb * 0.2)} por mês parado.`
    : "Hibernação desligada (RENDER2GO_PODS_HIBERNADOS=0): todo pod sobe do zero.";
  const saldo = `Saldo no RunPod: ${dolares(r.saldo_usd)}.`;
  if (prazo < 15 * 60) return [criar("span", { className: "selo erro" }, [icone("alerta"), "Sem saldo"]), `${saldo} Não paga 15 min de um pod: nenhum pod novo sobe. Recarregue no RunPod. ${hib}`];
  const texto = `${saldo} Um pod novo fica ligado até ${duracao(prazo)} (o worker para ${Math.round(r.margem_s / 60)} min antes).`;
  if (prazo < 3600) return [criar("span", { className: "selo alerta" }, [icone("alerta"), "Saldo baixo"]), `${texto} Recarregue no RunPod para os pods ficarem até 2 h. ${hib}`];
  return [criar("span", { className: "selo ok" }, [icone("ok"), "Saldo ok"]), `${texto} ${hib}`];
}

export function criarPods(ctx: { aoMudar: () => void }): Controle {
  const regiao = el("pods-dados");
  let partes: { estado: HTMLElement; runpod: HTMLElement; emergencia: HTMLButtonElement | null; retomar: HTMLButtonElement | null; tabela: HTMLElement; comandos: HTMLElement } | null = null;

  const depoisDaAcao = () => {
    void c.atualizar();
    ctx.aoMudar(); // o número de pods no menu e a visão geral
  };

  const montar = () => {
    const estado = criar("p", { className: "estado-orquestrador", id: "estado-orquestrador" });
    const runpod = criar("p", { className: "estado-orquestrador", id: "estado-runpod" });
    let emergencia: HTMLButtonElement | null = null;
    let retomar: HTMLButtonElement | null = null;
    const acoes = criar("div", { className: "acoes acoes-pods" });
    if (pode("POST /api/pods/emergencia")) {
      emergencia = criar("button", { type: "button", className: "botao perigo", id: "emergencia" }, [icone("bloquear"), "Parada de emergência: desligar todos"]);
      retomar = criar("button", { type: "button", className: "botao sucesso", id: "retomar", hidden: true }, [icone("ok"), "Retomar (liberar pods de novo)"]);
      const msg = criar("div", { className: "aviso", hidden: true, attrs: { role: "alert" } });
      emergencia.addEventListener("click", async () => {
        const ok = await confirmar({
          titulo: "Parada de emergência?",
          texto: "Todos os pods são encerrados e nenhum pod novo sobe até você clicar em “Retomar”. Os vídeos em andamento voltam para a fila e os clientes esperam.",
          confirmar: "Parar tudo",
          perigo: true,
        });
        if (!ok || !emergencia) return;
        ocupado(emergencia, true);
        try {
          await api("api/pods/emergencia", { method: "POST" });
          anunciar("Parada de emergência enviada. Nenhum pod novo sobe até retomar.", true);
          depoisDaAcao();
        } catch (e) {
          erroDeAcao(e, msg);
        } finally {
          ocupado(emergencia, false);
        }
      });
      retomar.addEventListener("click", async () => {
        if (!retomar) return;
        ocupado(retomar, true);
        try {
          await api("api/pods/retomar", { method: "POST" });
          anunciar("Pods liberados de novo.");
          depoisDaAcao();
        } catch (e) {
          erroDeAcao(e, msg);
        } finally {
          ocupado(retomar, false);
        }
      });
      acoes.append(emergencia, retomar);
      regiao.replaceChildren(estado, runpod, acoes, msg);
    } else regiao.replaceChildren(estado, runpod);
    const tab = criar("div", { className: "tabela", id: "pods-tabela", attrs: { role: "region", "aria-label": "Tabela dos pods ligados", tabindex: "0" } });
    const comandos = criar("ul", { className: "linhas", id: "comandos", attrs: { "aria-labelledby": "t-comandos" } });
    regiao.append(tab, criar("h2", { id: "t-comandos", textContent: "Últimos comandos" }), comandos);
    return { estado, runpod, emergencia, retomar, tabela: tab, comandos };
  };

  const carregar = async () => {
    const r = await api<Pods>("api/pods");
    partes ??= montar();
    partes.estado.replaceChildren(...estadoOrquestrador(r));
    partes.runpod.replaceChildren(...estadoRunPod(r.runpod));
    if (partes.emergencia && partes.retomar) {
      partes.emergencia.hidden = r.pausado && !r.pods.length;
      partes.retomar.hidden = !r.pausado;
    }
    const desligarPermitido = pode("POST /api/pods/{gerador}/desligar");
    const msgTabela = criar("div", { className: "aviso", hidden: true, attrs: { role: "alert" } });
    const cab: [string, boolean][] = [["Ligou em", false], ["Pod", false], ["GPU", false], ["US$/h", true], ["Situação", false], ["Gerando", true]];
    if (desligarPermitido) cab.push(["Ação", false]);
    trocar(
      partes.tabela,
      [
        r.pods.length
          ? tabela(
              cab,
              r.pods.map((p) => {
                const gpu = criar("span", {}, [p.gpu ?? "—", p.datacentro ? criar("span", { className: "fraco", textContent: ` · ${p.datacentro}` }) : null]);
                const celulas: (string | number | Node)[] = [tempoCurto(p.criado), p.worker ?? "—", gpu, usdh(p.usd_h), SITUACAO_POD[p.estado] ?? p.estado, p.em_andamento];
                if (desligarPermitido) {
                  const desligar = criar("button", { type: "button", className: "botao pequeno perigo", dados: { foco: `pod:${p.id}` } }, [icone("bloquear"), "Desligar"]);
                  desligar.setAttribute("aria-label", `Desligar o pod ${p.worker ?? p.id}`);
                  desligar.addEventListener("click", async () => {
                    const ok = await confirmar({
                      titulo: "Desligar este pod?",
                      texto: `O pod ${p.worker ?? p.id} (${p.gpu ?? "GPU"}) é encerrado no RunPod. Os vídeos que ele estava gerando voltam para a fila e saem em outro pod.`,
                      confirmar: "Desligar",
                      perigo: true,
                    });
                    if (!ok) return;
                    ocupado(desligar, true);
                    try {
                      await api(`api/pods/${encodeURIComponent(p.id)}/desligar`, { method: "POST" });
                      anunciar("Pedido de desligamento enviado ao orquestrador.");
                      depoisDaAcao();
                    } catch (e) {
                      ocupado(desligar, false);
                      erroDeAcao(e, msgTabela);
                    }
                  });
                  celulas.push(desligar);
                }
                return { celulas };
              }),
              "Pods ligados agora",
            )
          : semLinhas(r.pausado ? "Nenhum pod ligado (parada de emergência)." : "Nenhum pod ligado agora."),
        msgTabela,
      ],
      JSON.stringify([r.pods, desligarPermitido]),
    );
    trocar(
      partes.comandos,
      r.comandos.length
        ? r.comandos.map((cmd) =>
            criar("li", { className: "linha" }, [
              criar("p", { className: "linha-titulo", textContent: `${COMANDOS[cmd.tipo] ?? cmd.tipo}${cmd.alvo ? ` (${curto(cmd.alvo)})` : ""}` }),
              criar("p", { className: "linha-valor", textContent: cmd.executado_em ? (cmd.resultado ?? "feito") : "aguardando o orquestrador" }),
              criar("p", { className: "linha-detalhe" }, [tempo(cmd.criado), ` · por ${cmd.por ?? "?"}`]),
            ]),
          )
        : [criar("li", { className: "fraco sem-itens", textContent: "Nenhum comando ainda." })],
      JSON.stringify(r.comandos),
    );
  };
  const c = controle({ regiao, carregar });
  return c;
}

// -- GPUs ---------------------------------------------------------------------------------------------------

export function criarGpus(): Controle {
  const regiao = el("gpus-dados");
  const filtros = el("filtros-gpus");
  let dias = 30;
  let geracao = 0;
  let relatorio: RelatorioGpus | null = null;
  let partes: { resumo: HTMLElement; tabela: HTMLElement } | null = null;

  const carregar = async () => {
    const minha = ++geracao;
    const r = await api<RelatorioGpus>(`api/geradores?dias=${dias}`);
    if (minha !== geracao) return;
    relatorio = r;
    if (!partes) {
      const resumo = criar("dl", { className: "numeros", id: "resumo-gpus" });
      const tab = criar("div", { className: "tabela", id: "gpus-tabela", attrs: { role: "region", "aria-label": "GPUs ligadas no período", tabindex: "0" } });
      regiao.replaceChildren(resumo, tab);
      partes = { resumo, tabela: tab };
    }
    const t = r.totais;
    const item = (rotulo: string, valor: string) => criar("div", {}, [criar("dt", { textContent: rotulo }), criar("dd", { textContent: valor })]);
    // a cada 30 s só o tempo das GPUs ligadas muda: a troca acontece só quando algo visível muda
    const assinatura = JSON.stringify([dias, t, r.geradores.map((g) => [g.id, g.estado, g.videos_ok, g.videos_erro, g.videos_andamento, Math.round(g.ligado_s / 60)])]);
    trocar(
      partes.resumo,
      [
        item("GPUs ligadas", `${t.pods}${t.ligados_agora ? ` (${t.ligados_agora} agora)` : ""}${t.retomadas ? ` · ${plural(t.retomadas, "volta", "voltas")} da hibernação` : ""}`),
        item("Tempo ligado", duracao(t.horas * 3600) + (t.ocioso_h ? ` (${duracao(t.ocioso_h * 3600)} sem trabalho)` : "")),
        item("Custo de GPU", dolares(t.custo_usd) + (t.sem_preco ? " *" : "")),
        item("Com os discos", t.custo_total_usd == null ? "—" : dolares(t.custo_total_usd)),
        item("Vídeos entregues", `${t.videos_ok}${t.aproveitaveis ? ` (${t.aproveitaveis} aproveitáveis)` : ""}`),
        item("Custo por vídeo", `${dolares(t.custo_por_video_usd)} de GPU${t.custo_total_por_video_usd != null ? ` · ${dolares(t.custo_total_por_video_usd)} com os discos` : ""}`),
        item("Tentativas", t.tentativas_render ? `${t.tentativas_render} · ${plural(t.tentativas_falhas ?? 0, "falhou", "falharam")}` : "—"),
        item("Falhas", `${plural(t.falharam, "subida", "subidas")} · ${plural(t.videos_erro, "vídeo", "vídeos")}`),
      ],
      assinatura,
    );
    const notas = [
      "Cada linha é uma sessão: a subida do pod do zero (1ª) ou uma volta da hibernação (2ª em diante), com os vídeos dela. Ocupação: a soma dos tempos dos vídeos e das traduções sobre o tempo ligado (não é a medida da GPU). Com os discos: a GPU mais o disco do contêiner e o de volume (ligado e parado), pela tabela do RunPod; sem servidor, R2, pagamentos e revisão.",
    ];
    if (r.geradores.some((g) => g.ligado_estimado) || t.sem_preco) notas.push("* Estimado: o orquestrador parou sem registrar o desligamento (o desligamento automático do pod encerra no máximo 2 h depois) ou a GPU não informou o preço.");
    if (t.sessoes_incertas) notas.push("† Atribuição incerta: vídeos de antes de 17/10/2026, divididos entre as sessões do pod pelo horário, ou sessão sem os discos registrados.");
    const nota = criar("div", {}, notas.map((x) => criar("p", { className: "fraco nota-tabela", textContent: x })));
    trocar(
      partes.tabela,
      [
        r.geradores.length
          ? tabela(
              [
                ["Ligou em", false],
                ["Pod", false],
                ["Sessão", true],
                ["GPU", false],
                ["Datacenter", false],
                ["US$/h", true],
                ["Até começar o 1º vídeo", true],
                ["Até a 1ª entrega", true],
                ["Tempo ligado", true],
                ["Vídeos", true],
                ["Tentativas", true],
                ["Pedidos", true],
                ["Ocupação", true],
                ["Custo de GPU", true],
                ["Com os discos", true],
                ["Cache", false],
                ["Situação", false],
              ],
              r.geradores.map((g) => {
                const ocupacao = g.ocupacao ?? (g.ligado_s > 0 ? g.segundos_render / g.ligado_s : null);
                const uso = ocupacao == null ? "—" : `${Math.min(100, Math.round(ocupacao * 100))}%`;
                const fim = g.volume_fim_motivo ? `disco de volume encerrado: ${g.volume_fim_motivo}` : "";
                const situacao = [SITUACAO_GPU[g.estado] ?? g.estado, g.motivo && g.estado !== "pronto" ? g.motivo : "", fim].filter(Boolean).join(": ");
                const sessao = g.sessao ? `${g.sessao}ª${g.sessao > 1 ? " (volta)" : ""}` : "—";
                const tentativas = g.tentativas_render == null ? "—" : `${g.tentativas_render}${g.tentativas_falhas ? ` (${g.tentativas_falhas} falha${g.tentativas_falhas > 1 ? "s" : ""})` : ""}`;
                return {
                  classe: g.estado === "falhou" ? "sinal" : "",
                  celulas: [
                    tempoCurto(g.inicio),
                    g.pod ?? "—",
                    sessao,
                    g.gpu ?? "—",
                    g.datacentro ?? "—",
                    usdh(g.usd_h),
                    duracao(g.subida_s),
                    duracao(g.primeira_entrega_s ?? null),
                    duracao(g.ligado_s) + (g.ligado_estimado ? " *" : ""),
                    `${g.videos_ok}${g.videos_andamento ? ` (+${g.videos_andamento})` : ""}${g.videos_erro ? ` · ${g.videos_erro} com falha` : ""}${g.incertezas?.length ? " †" : ""}`,
                    tentativas,
                    g.lotes,
                    uso,
                    dolares(g.custo_usd),
                    g.custo_total_usd == null ? "—" : dolares(g.custo_total_usd),
                    cacheDaSessao(g),
                    situacao,
                  ],
                };
              }),
              "GPUs ligadas no período",
            )
          : semLinhas("Nenhuma GPU ligada neste período."),
        nota,
      ],
      assinatura,
    );
  };
  const c = controle({ regiao, carregar });

  filtros.addEventListener("click", (ev) => {
    const b = (ev.target as Element).closest<HTMLButtonElement>("button[data-dias]");
    if (!b || Number(b.dataset.dias) === dias) return;
    dias = Number(b.dataset.dias) || 30;
    for (const x of Array.from(filtros.querySelectorAll("button"))) x.setAttribute("aria-pressed", String(x === b));
    regiao.setAttribute("aria-busy", "true");
    carregar()
      .catch((e) => erroDeCarga(e, partes ? null : regiao, () => undefined))
      .finally(() => regiao.setAttribute("aria-busy", "false"));
  });
  el("baixar-gpus").addEventListener("click", async () => {
    const botao = el<HTMLButtonElement>("baixar-gpus");
    ocupado(botao, true);
    try {
      await carregar(); // o relatório do período escolhido, como está agora
      if (relatorio) baixarJson(relatorio, `gpus-${dias}-dias.json`);
    } catch (e) {
      erroDeCarga(e, null, () => undefined);
    } finally {
      ocupado(botao, false);
    }
  });
  return c;
}

// -- recargas por Pix ---------------------------------------------------------------------------------------

export function criarPagamentos(): Controle {
  const regiao = el("pagamentos-dados");
  let partes: { config: HTMLElement; tabela: HTMLElement } | null = null;
  const carregar = async () => {
    const r = await api<{ configurado: boolean; pagamentos: Pagamento[] }>("api/pagamentos");
    if (!partes) {
      const config = criar("p", { className: "fraco", id: "pagamentos-config" });
      const tab = criar("div", { className: "tabela", id: "pagamentos-tabela", attrs: { role: "region", "aria-label": "Tabela das recargas por Pix", tabindex: "0" } });
      regiao.replaceChildren(config, tab);
      partes = { config, tabela: tab };
    }
    partes.config.textContent = r.configurado
      ? "Recarga automática ligada (Mercado Pago)."
      : "Recarga automática desligada: configure o Mercado Pago (docs/09) ou lance as recargas à mão em “Contas e saldo”.";
    const msg = criar("div", { className: "aviso", hidden: true, attrs: { role: "alert" } });
    trocar(
      partes.tabela,
      [
        r.pagamentos.length
          ? tabela(
              [["Quando", false], ["Conta", false], ["Valor", true], ["Situação", false], ["Creditado", false], ["Documento", false], ["Ação", false]],
              r.pagamentos.map((p) => {
                let acao: Node | string = "—";
                if (["pendente", "criando", "divergente", "erro"].includes(p.status)) {
                  const conferir = criar("button", { type: "button", className: "botao pequeno", dados: { foco: `pix:${p.id}` } }, [icone("atualizar"), "Conferir"]);
                  conferir.setAttribute("aria-label", `Conferir no Mercado Pago a recarga de ${reais(p.valor)}`);
                  conferir.addEventListener("click", async () => {
                    ocupado(conferir, true);
                    try {
                      const res = await api<{ status: string }>(`api/pagamentos/${encodeURIComponent(p.id)}/conferir`, { method: "POST" });
                      anunciar(`Recarga conferida: ${SITUACAO_PIX[res.status] ?? res.status}.`);
                      void c.atualizar();
                    } catch (e) {
                      erroDeAcao(e, msg);
                    } finally {
                      ocupado(conferir, false);
                    }
                  });
                  acao = conferir;
                }
                return {
                  classe: p.status === "divergente" || p.status === "contestado" ? "sinal" : "",
                  celulas: [tempoCurto(p.criado), p.email ?? p.cliente_id, reais(p.valor), SITUACAO_PIX[p.status] ?? p.status, p.creditado_em ? tempoCurto(p.creditado_em) : "—", p.documento ?? "—", acao],
                };
              }),
              "Recargas por Pix",
            )
          : semLinhas("Nenhuma recarga ainda."),
        msg,
      ],
      JSON.stringify(r.pagamentos),
    );
  };
  const c = controle({ regiao, carregar });
  return c;
}

