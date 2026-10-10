// Tela "Configurações" (/configuracoes): tudo o que diz respeito ao acesso. Tema; e-mail (troca com código no
// endereço novo); telefone (em breve); verificação em duas etapas (aplicativo autenticador e códigos de
// recuperação); aparelhos conectados; cópia dos dados; desativar (pausar) e excluir a conta. Trocar o e-mail,
// mexer na verificação e excluir pedem a confirmação recente (confirmacao.ts).
import {
  CHAVE_CONECTADO,
  type ItemLista,
  anunciar,
  api,
  aviso,
  baixarJson,
  baixarTexto,
  confirmar,
  copiarTexto,
  criar,
  dialogo,
  el,
  icone,
  marcarConectado,
  marcarErro,
  ocupado,
  plural,
  reais,
  reconciliar,
  tempo,
  textoErro,
} from "./comum";
import { comConfirmacao } from "./confirmacao";
import { type Conta, aoMudarConta, atualizarConta, carregarConta, contaAtual } from "./conta";
import { botaoInteresse } from "./interesse";
import { type Tema, aplicarTema, temaAtual } from "./tema";

type Aparelho = { ref: string; aparelho: string; ip: string | null; criada: number; ultimo_uso: number | null; atual: boolean };
type InicioMfa = { segredo: string; uri: string; qr_svg: string; validade_s: number };

const EMAIL = /^[^@\s<>"',;]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$/;
/** Aviso guardado para a página de entrada (depois de desativar ou excluir a conta, a página volta para ela). */
export const CHAVE_AVISO = "render2go-aviso";

function sairPara(aviso_: string): void {
  try {
    sessionStorage.setItem(CHAVE_AVISO, aviso_);
  } catch {
    /* sem armazenamento: a entrada aparece sem o aviso */
  }
  marcarConectado(CHAVE_CONECTADO, false);
  location.assign("/");
}

export function criarConfiguracoes(ctx: { erro: (e: unknown, alvo: HTMLElement) => void }): { abrir: () => Promise<void> } {
  const msgGeral = el("config-msg");
  let aparelhos: Aparelho[] = [];
  let interesse = false;

  // -- aparência ----------------------------------------------------------------------------------------
  const radios = Array.from(document.querySelectorAll<HTMLInputElement>('input[name="tema"]'));
  const marcarTema = (t: Tema) => {
    for (const r of radios) r.checked = r.value === t;
  };
  marcarTema(temaAtual());
  for (const r of radios) {
    r.addEventListener("change", async () => {
      if (!r.checked) return;
      const tema = r.value as Tema;
      aplicarTema(tema);
      atualizarConta({ tema });
      anunciar(tema === "auto" ? "Tema automático: igual ao do aparelho." : `Tema ${tema} aplicado.`);
      try {
        await api("/api/conta/preferencias", { method: "POST", body: JSON.stringify({ tema }) });
      } catch (e) {
        aviso(el("tema-msg"), `O tema vale neste aparelho, mas não foi guardado na conta: ${textoErro(e)}`, "alerta");
      }
    });
  }

  // -- conta: e-mail, verificação em duas etapas e interesse no telefone ---------------------------------
  const mostrarConta = (c: Conta) => {
    // a escolha da conta vale em todos os aparelhos (o tema deste navegador acompanha)
    if (c.tema && c.tema !== temaAtual()) aplicarTema(c.tema);
    marcarTema(c.tema ?? temaAtual());
    el("config-email-atual").textContent = c.email ?? "Esta conta não tem e-mail (entra com código de acesso).";
    el<HTMLButtonElement>("config-trocar-email").hidden = !c.email;
    const mfa = c.mfa ?? { ativo: false, desde: null };
    const restantes = mfa.codigos ?? 0;
    // texto curto: o lugar dele tem a altura de 2 ou 3 linhas reservada (a conta chega depois do 1º quadro)
    el("mfa-estado").textContent = mfa.ativo
      ? `Ligada${mfa.desde ? ` desde ${new Date(mfa.desde * 1000).toLocaleDateString("pt-BR")}` : ""}. ${plural(restantes, "código de recuperação ainda vale", "códigos de recuperação ainda valem")}.`
      : "Desligada: a entrada pede só o código do e-mail.";
    el("mfa-estado").classList.toggle("ligada", mfa.ativo);
    el<HTMLButtonElement>("mfa-ligar").hidden = mfa.ativo;
    el<HTMLButtonElement>("mfa-codigos").hidden = !mfa.ativo;
    el<HTMLButtonElement>("mfa-desligar").hidden = !mfa.ativo;
    aviso(el("mfa-aviso"), mfa.ativo && restantes <= 3 ? "Restam poucos códigos de recuperação: gere códigos novos e guarde-os num lugar seguro." : "", "alerta");
    if (!interesse) {
      interesse = true;
      el("interesse-telefone").replaceChildren(botaoInteresse("recurso:telefone", "Telefone na conta", (t) => aviso(msgGeral, t, "erro")));
    }
  };
  aoMudarConta(mostrarConta);

  // -- troca de e-mail: código no endereço novo -----------------------------------------------------------
  const formEmail = el<HTMLFormElement>("form-email-novo");
  const campoEmail = el<HTMLInputElement>("email-novo");
  const campoCodigo = el<HTMLInputElement>("email-novo-codigo");
  const msgEmail = el("email-msg");
  let emailPedido = "";
  const passoEmail = (codigo: boolean) => {
    el("email-passo-codigo").hidden = !codigo;
    el<HTMLButtonElement>("email-enviar").hidden = codigo;
    el<HTMLButtonElement>("email-confirmar").hidden = !codigo;
    campoEmail.readOnly = codigo;
  };
  el("config-trocar-email").addEventListener("click", () => {
    formEmail.hidden = false;
    el<HTMLButtonElement>("config-trocar-email").setAttribute("aria-expanded", "true");
    passoEmail(false);
    aviso(msgEmail, "");
    campoEmail.focus();
  });
  el("email-cancelar").addEventListener("click", () => {
    formEmail.hidden = true;
    formEmail.reset();
    el<HTMLButtonElement>("config-trocar-email").setAttribute("aria-expanded", "false");
    el("config-trocar-email").focus();
  });
  for (const campo of [campoEmail, campoCodigo]) campo.addEventListener("input", () => marcarErro(campo, false));
  formEmail.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    aviso(msgEmail, "");
    const noCodigo = !el("email-passo-codigo").hidden;
    if (!noCodigo) {
      const email = campoEmail.value.trim();
      if (!EMAIL.test(email)) {
        marcarErro(campoEmail, true);
        aviso(msgEmail, email ? "Confira o e-mail: ele parece incompleto." : "Digite o e-mail novo.", "erro");
        campoEmail.focus();
        return;
      }
      const botao = el<HTMLButtonElement>("email-enviar");
      ocupado(botao, true);
      try {
        const r = await comConfirmacao(() => api<{ email: string }>("/api/conta/email/codigo", { method: "POST", body: JSON.stringify({ email }) }));
        if (!r) return;
        emailPedido = r.email;
        passoEmail(true);
        aviso(msgEmail, `Enviamos um código de 8 dígitos para ${r.email}. Digite-o abaixo para confirmar o endereço novo.`, "info");
        campoCodigo.value = "";
        campoCodigo.focus();
      } catch (e) {
        marcarErro(campoEmail, true);
        aviso(msgEmail, textoErro(e), "erro");
        campoEmail.focus();
      } finally {
        ocupado(botao, false);
      }
      return;
    }
    const codigo = campoCodigo.value.replace(/\D/g, "");
    if (codigo.length !== 8) {
      marcarErro(campoCodigo, true);
      aviso(msgEmail, "O código tem 8 dígitos. Confira no e-mail novo.", "erro");
      campoCodigo.focus();
      return;
    }
    const botao = el<HTMLButtonElement>("email-confirmar");
    ocupado(botao, true);
    try {
      const r = await api<{ email: string }>("/api/conta/email", { method: "POST", body: JSON.stringify({ email: emailPedido, codigo }) });
      formEmail.hidden = true;
      formEmail.reset();
      el<HTMLButtonElement>("config-trocar-email").setAttribute("aria-expanded", "false");
      await carregarConta().catch(() => atualizarConta({ email: r.email }));
      aviso(msgEmail, `Pronto: o e-mail da conta agora é ${r.email}. Avisamos o endereço antigo.`, "ok");
      anunciar(`E-mail trocado para ${r.email}.`);
      el("config-trocar-email").focus();
    } catch (e) {
      marcarErro(campoCodigo, true);
      aviso(msgEmail, textoErro(e), "erro");
      campoCodigo.focus();
    } finally {
      ocupado(botao, false);
    }
  });

  // -- verificação em duas etapas -------------------------------------------------------------------------
  const mostrarCodigos = (d: HTMLDialogElement, titulo: HTMLElement, corpo: HTMLElement, codigos: string[], novos: boolean) => {
    titulo.textContent = "Códigos de recuperação";
    const id = `${d.getAttribute("aria-labelledby")}-guardei`;
    const guardei = criar("input", { type: "checkbox", id });
    const concluir = criar("button", { type: "button", className: "botao primario", disabled: true, textContent: "Concluir" });
    guardei.addEventListener("change", () => (concluir.disabled = !guardei.checked));
    concluir.addEventListener("click", () => d.close());
    const texto = codigos.join("\n");
    const copiar = criar("button", { type: "button", className: "botao pequeno" }, [icone("copiar"), "Copiar todos"]);
    copiar.addEventListener("click", async () => {
      if (await copiarTexto(texto)) {
        copiar.replaceChildren(icone("ok"), "Copiados");
        anunciar("Códigos copiados.");
      }
    });
    const baixar = criar("button", { type: "button", className: "botao pequeno" }, [icone("baixar"), "Baixar (.txt)"]);
    baixar.addEventListener("click", () =>
      baixarTexto(`Códigos de recuperação do Render2Go (${contaAtual()?.email ?? ""})\nCada código vale uma vez.\n\n${texto}\n`, "render2go-codigos-de-recuperacao.txt"),
    );
    const lista = criar("ol", { className: "codigos-recuperacao", attrs: { "aria-label": "Códigos de recuperação" } }, codigos.map((k) => criar("li", {}, [criar("code", { textContent: k })])));
    const ok = criar("p", { className: "aviso ok", tabIndex: -1, textContent: novos ? "Códigos novos gerados. Os anteriores deixaram de valer." : "Pronto: a verificação em duas etapas está ligada." });
    corpo.replaceChildren(
      ok,
      criar("p", {
        textContent:
          "Guarde estes códigos num lugar seguro (um gerenciador de senhas ou um papel bem guardado). Se você perder o celular, cada um deles vale uma vez para entrar no lugar do código do aplicativo. Eles não aparecem de novo.",
      }),
      lista,
      criar("div", { className: "acoes" }, [copiar, baixar]),
      criar("label", { className: "marcacao", htmlFor: id }, [guardei, criar("span", { textContent: "Guardei os códigos num lugar seguro." })]),
      criar("div", { className: "dialogo-acoes" }, [concluir]),
    );
    ok.focus();
  };

  el("mfa-ligar").addEventListener("click", async () => {
    const botao = el<HTMLButtonElement>("mfa-ligar");
    ocupado(botao, true);
    let r: InicioMfa | null = null;
    try {
      r = await comConfirmacao(() => api<InicioMfa>("/api/conta/mfa/iniciar", { method: "POST" }));
    } catch (e) {
      aviso(el("mfa-aviso"), textoErro(e), "erro");
    } finally {
      ocupado(botao, false);
    }
    if (!r) return;
    const inicio = r;
    const { d, corpo, titulo } = dialogo("Ligar a verificação em duas etapas", "mfa");
    const base = d.getAttribute("aria-labelledby");
    // o QR Code (SVG feito pelo servidor) entra como imagem (blob:), nunca como HTML na página
    const qr = URL.createObjectURL(new Blob([inicio.qr_svg], { type: "image/svg+xml" }));
    const chave = inicio.segredo.replace(/(.{4})(?=.)/g, "$1 ");
    const copiar = criar("button", { type: "button", className: "botao pequeno" }, [icone("copiar"), "Copiar a chave"]);
    copiar.addEventListener("click", async () => {
      if (await copiarTexto(inicio.segredo)) {
        copiar.replaceChildren(icone("ok"), "Copiada");
        anunciar("Chave copiada.");
      }
    });
    const campo = criar("input", { id: `${base}-codigo`, inputMode: "numeric", autocomplete: "one-time-code", maxLength: 7, spellcheck: false, required: true });
    campo.setAttribute("aria-describedby", `${base}-codigo-dica`);
    campo.addEventListener("input", () => marcarErro(campo, false));
    const msg = criar("div", { className: "aviso", hidden: true, attrs: { role: "alert" } });
    const ligar = criar("button", { type: "submit", className: "botao primario", textContent: "Ligar" });
    const cancelar = criar("button", { type: "button", className: "botao", textContent: "Cancelar" });
    cancelar.addEventListener("click", () => d.close());
    const form = criar("form", { noValidate: true, className: "pilha" }, [
      criar("p", { textContent: "1. Abra o aplicativo autenticador do seu celular (Google Authenticator, Microsoft Authenticator, Authy, 1Password ou outro) e leia este QR Code:" }),
      criar("img", { src: qr, className: "qr-mfa", width: 220, height: 220, alt: "QR Code para cadastrar a sua conta do Render2Go no aplicativo autenticador" }),
      criar("details", { className: "chave-manual" }, [
        criar("summary", { textContent: "Não consigo ler o QR Code" }),
        criar("p", { className: "dica", textContent: "No aplicativo, escolha digitar uma chave (baseada em tempo) e use esta:" }),
        criar("div", { className: "codigo-gerado" }, [criar("code", { textContent: chave }), copiar]),
      ]),
      criar("div", { className: "campo" }, [
        criar("label", { htmlFor: campo.id, textContent: "2. Digite o código de 6 dígitos que aparece no aplicativo" }),
        campo,
        criar("p", { className: "dica", id: `${base}-codigo-dica`, textContent: "O código muda a cada 30 segundos. Este QR Code vale por 15 minutos." }),
      ]),
      msg,
      criar("div", { className: "dialogo-acoes" }, [cancelar, ligar]),
    ]);
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const codigo = campo.value.replace(/\D/g, "");
      if (codigo.length !== 6) {
        marcarErro(campo, true);
        aviso(msg, "O código do aplicativo tem 6 dígitos.", "erro");
        campo.focus();
        return;
      }
      ocupado(ligar, true);
      try {
        const res = await api<{ codigos: string[] }>("/api/conta/mfa/ativar", { method: "POST", body: JSON.stringify({ codigo }) });
        URL.revokeObjectURL(qr);
        mostrarCodigos(d, titulo, corpo, res.codigos, false);
        anunciar("Verificação em duas etapas ligada. Guarde os códigos de recuperação.");
        void carregarConta().catch(() => undefined);
      } catch (e) {
        marcarErro(campo, true);
        aviso(msg, textoErro(e), "erro");
        campo.select();
        campo.focus();
      } finally {
        ocupado(ligar, false);
      }
    });
    corpo.append(form);
    d.addEventListener("close", () => {
      URL.revokeObjectURL(qr);
      d.remove();
      const proximo = el<HTMLButtonElement>(contaAtual()?.mfa?.ativo ? "mfa-codigos" : "mfa-ligar");
      (proximo.hidden ? el("t-mfa") : proximo).focus();
    });
    d.showModal();
    campo.focus();
  });

  el("mfa-codigos").addEventListener("click", async () => {
    const ok = await confirmar({ titulo: "Gerar códigos novos?", texto: "Os códigos de recuperação que você tem hoje deixam de valer.", confirmar: "Gerar códigos novos" });
    if (!ok) return;
    try {
      const r = await comConfirmacao(() => api<{ codigos: string[] }>("/api/conta/mfa/codigos", { method: "POST" }));
      if (!r) return;
      const { d, corpo, titulo } = dialogo("Códigos de recuperação", "mfa");
      d.addEventListener("close", () => {
        d.remove();
        el("mfa-codigos").focus();
      });
      d.showModal();
      mostrarCodigos(d, titulo, corpo, r.codigos, true);
      void carregarConta().catch(() => undefined);
    } catch (e) {
      aviso(el("mfa-aviso"), textoErro(e), "erro");
    }
  });

  el("mfa-desligar").addEventListener("click", async () => {
    const ok = await confirmar({
      titulo: "Desligar a verificação em duas etapas?",
      texto: "Depois disso, só o código do e-mail protege a entrada na sua conta, e os códigos de recuperação deixam de valer.",
      confirmar: "Desligar",
      perigo: true,
    });
    if (!ok) return;
    try {
      const r = await comConfirmacao(() => api("/api/conta/mfa/desligar", { method: "POST" }));
      if (r === null) return;
      await carregarConta().catch(() => undefined);
      anunciar("Verificação em duas etapas desligada.");
      el("mfa-ligar").focus();
    } catch (e) {
      aviso(el("mfa-aviso"), textoErro(e), "erro");
    }
  });

  // -- aparelhos conectados -------------------------------------------------------------------------------
  const listaAparelhos = el("aparelhos");
  const desenharAparelhos = () => {
    const itens: ItemLista[] = aparelhos.map((a) => ({
      chave: a.ref,
      assinatura: JSON.stringify([a.aparelho, a.ip, a.ultimo_uso, a.atual]),
      criar: () => {
        const botao = criar("button", { type: "button", className: "botao pequeno", dados: { foco: `aparelho:${a.ref}` } }, [icone("sair"), a.atual ? "Sair deste aparelho" : "Encerrar"]);
        botao.setAttribute("aria-label", a.atual ? "Sair deste aparelho" : `Encerrar a sessão de ${a.aparelho}`);
        botao.addEventListener("click", () => void encerrar(a, botao));
        const detalhe: (Node | string)[] = [a.ip ? `IP ${a.ip} · entrou em ` : "Entrou em ", tempo(a.criada)];
        if (a.ultimo_uso) detalhe.push(" · último uso em ", tempo(a.ultimo_uso));
        return criar("li", { className: "linha aparelho" }, [
          criar("p", { className: "linha-titulo" }, [icone("celular"), criar("span", { textContent: a.aparelho }), a.atual ? criar("span", { className: "selo info", textContent: "Este aparelho" }) : null]),
          criar("div", { className: "linha-acao" }, [botao]),
          criar("p", { className: "linha-detalhe" }, detalhe),
        ]);
      },
    }));
    reconciliar(listaAparelhos, itens, () => criar("li", { className: "fraco", textContent: "Nenhum aparelho conectado." }));
    el<HTMLButtonElement>("encerrar-outras").disabled = aparelhos.filter((a) => !a.atual).length === 0;
  };
  const carregarAparelhos = async () => {
    try {
      aparelhos = (await api<{ sessoes: Aparelho[] }>("/api/conta/sessoes")).sessoes;
      desenharAparelhos();
    } catch (e) {
      listaAparelhos.setAttribute("aria-busy", "false");
      ctx.erro(e, el("aparelhos-msg"));
    }
  };
  const encerrar = async (a: Aparelho, botao: HTMLButtonElement) => {
    if (a.atual) {
      const ok = await confirmar({ titulo: "Sair deste aparelho?", texto: "Para voltar, entre de novo com o código do e-mail.", confirmar: "Sair" });
      if (!ok) return;
    }
    ocupado(botao, true);
    try {
      const r = await api<{ atual: boolean }>(`/api/conta/sessoes/${encodeURIComponent(a.ref)}/encerrar`, { method: "POST" });
      if (r.atual) {
        marcarConectado(CHAVE_CONECTADO, false);
        location.assign("/");
        return;
      }
      const posicao = aparelhos.findIndex((x) => x.ref === a.ref);
      aparelhos = aparelhos.filter((x) => x.ref !== a.ref);
      desenharAparelhos();
      anunciar(`Sessão de ${a.aparelho} encerrada.`);
      const botoes = listaAparelhos.querySelectorAll<HTMLElement>("button");
      (botoes[Math.min(posicao, botoes.length - 1)] ?? el("t-aparelhos")).focus();
    } catch (e) {
      ocupado(botao, false);
      aviso(el("aparelhos-msg"), textoErro(e), "erro");
    }
  };
  el("encerrar-outras").addEventListener("click", async () => {
    const outras = aparelhos.filter((a) => !a.atual).length;
    const ok = await confirmar({
      titulo: "Encerrar os outros aparelhos?",
      texto: `${plural(outras, "sessão termina", "sessões terminam")} agora; este aparelho continua conectado.`,
      confirmar: "Encerrar os outros",
    });
    if (!ok) return;
    try {
      const r = await api<{ sessoes: number }>("/api/conta/sessoes/encerrar-outras", { method: "POST" });
      anunciar(`${plural(r.sessoes, "sessão encerrada", "sessões encerradas")}.`);
      await carregarAparelhos();
      el("encerrar-outras").focus();
    } catch (e) {
      aviso(el("aparelhos-msg"), textoErro(e), "erro");
    }
  });
  el("sair-de-todos").addEventListener("click", async () => {
    const ok = await confirmar({
      titulo: "Sair de todos os aparelhos?",
      texto: "Todas as sessões desta conta terminam agora, inclusive esta. Para voltar, entre de novo com o código do e-mail.",
      confirmar: "Sair de todos",
    });
    if (!ok) return;
    try {
      await api("/api/conta/sair-de-todos", { method: "POST" });
      marcarConectado(CHAVE_CONECTADO, false);
      location.assign("/");
    } catch (e) {
      aviso(el("aparelhos-msg"), textoErro(e), "erro");
    }
  });

  // -- seus dados, desativar e excluir --------------------------------------------------------------------
  el("baixar-dados").addEventListener("click", async () => {
    const botao = el<HTMLButtonElement>("baixar-dados");
    ocupado(botao, true);
    try {
      baixarJson(await api<unknown>("/api/conta/dados"), "render2go-meus-dados.json");
      anunciar("Seus dados foram baixados.");
    } catch (e) {
      aviso(el("dados-msg"), textoErro(e), "erro");
    } finally {
      ocupado(botao, false);
    }
  });

  el("desativar-conta").addEventListener("click", async () => {
    const ok = await confirmar({
      titulo: "Desativar a conta?",
      texto: [
        "Você sai de todos os aparelhos e a conta fica pausada: ninguém entra nela até você voltar.",
        "O saldo, os pedidos e o histórico ficam guardados. Para voltar, é só entrar de novo com o código do e-mail.",
      ],
      confirmar: "Desativar a conta",
      perigo: true,
    });
    if (!ok) return;
    const botao = el<HTMLButtonElement>("desativar-conta");
    ocupado(botao, true);
    try {
      await api("/api/conta/desativar", { method: "POST", body: JSON.stringify({ confirmacao: "DESATIVAR" }) });
      sairPara("Conta desativada. Para voltar a usar, entre com o código do e-mail: a conta é reativada na hora.");
    } catch (e) {
      ocupado(botao, false);
      aviso(el("desativar-msg"), textoErro(e), "erro");
    }
  });

  el("excluir-conta").addEventListener("click", async () => {
    const c = contaAtual();
    const ok = await confirmar({
      titulo: "Excluir a conta para sempre?",
      texto: [
        "A conta e o e-mail saem do serviço e você deixa de conseguir entrar. Isto não tem volta.",
        c && c.saldo > 0 ? `Você tem ${reais(c.saldo)} de saldo: peça o reembolso pelo suporte antes de excluir.` : "Pedidos em andamento precisam terminar antes.",
        "O extrato e os registros que a lei manda guardar ficam pelo prazo legal.",
      ],
      confirmar: "Excluir para sempre",
      perigo: true,
      palavra: "EXCLUIR",
    });
    if (!ok) return;
    const botao = el<HTMLButtonElement>("excluir-conta");
    ocupado(botao, true);
    try {
      const r = await comConfirmacao(() => api("/api/conta/excluir", { method: "POST", body: JSON.stringify({ confirmacao: "EXCLUIR" }) }));
      if (r === null) {
        ocupado(botao, false);
        return;
      }
      sairPara("Conta excluída. Obrigado por ter usado o Render2Go.");
    } catch (e) {
      ocupado(botao, false);
      aviso(el("excluir-msg"), textoErro(e), "erro");
    }
  });

  return {
    // os aparelhos mudam com o tempo (último uso): a cada visita à tela, a lista vem de novo
    abrir: () => carregarAparelhos(),
  };
}
