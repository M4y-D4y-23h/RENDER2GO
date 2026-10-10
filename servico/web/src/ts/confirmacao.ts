// Duas perguntas que a API pode fazer antes de uma ação (ADR 0007, configurações de 10/10/2026):
// - "Confirme que é você" (403 com X-Render2Go-Motivo: confirmar): trocar o e-mail, ligar ou desligar a verificação
//   em duas etapas, gerar códigos de recuperação e excluir a conta pedem uma confirmação recente. Com a verificação
//   ligada, vale o código do aplicativo (ou um de recuperação); sem ela, um código que vai para o e-mail. A
//   confirmação vale 10 minutos nesta sessão (uma sessão aberta há menos de 10 minutos já conta).
// - "Os termos mudaram" (428 com X-Render2Go-Motivo: aceite): quem estava conectado quando os Termos de Uso ou a
//   Política de Privacidade mudaram aceita a versão nova antes de pedir ou pagar.
import { ErroApi, anunciar, api, aviso, criar, dialogo, marcarErro, ocupado, textoErro } from "./comum";
import { atualizarConta, contaAtual } from "./conta";

/** Roda a ação; se a API pedir a confirmação, pergunta e tenta de novo. null: a pessoa desistiu. */
export async function comConfirmacao<T>(acao: () => Promise<T>): Promise<T | null> {
  try {
    return await acao();
  } catch (e) {
    if (!(e instanceof ErroApi && e.status === 403 && e.motivo === "confirmar")) throw e;
  }
  if (!(await confirmarIdentidade())) return null;
  return acao();
}

export function confirmarIdentidade(): Promise<boolean> {
  const mfa = !!contaAtual()?.mfa?.ativo;
  const origem = document.activeElement as HTMLElement | null;
  const { d, corpo } = dialogo("Confirme que é você", "confirmacao confirmar-identidade");
  const id = `${d.getAttribute("aria-labelledby")}-codigo`;
  const msg = criar("div", { className: "aviso", hidden: true, attrs: { role: "alert" } });
  const explicacao = criar("p", {
    textContent: mfa
      ? "Por segurança, digite o código de 6 dígitos que aparece agora no seu aplicativo autenticador. Sem o celular, use um dos seus códigos de recuperação."
      : "Por segurança, vamos mandar um código de 8 dígitos para o seu e-mail.",
  });
  const campo = criar("input", { id, autocomplete: "one-time-code", spellcheck: false, autocapitalize: "off", maxLength: 40, required: true });
  campo.setAttribute("enterkeyhint", "done");
  if (!mfa) campo.inputMode = "numeric";
  campo.addEventListener("input", () => marcarErro(campo, false));
  const confirmarB = criar("button", { type: "submit", className: "botao primario" }, ["Confirmar"]);
  const cancelar = criar("button", { type: "button", className: "botao" }, ["Cancelar"]);
  const itens: HTMLElement[] = [
    explicacao,
    criar("div", { className: "campo" }, [criar("label", { htmlFor: id, textContent: mfa ? "Código do aplicativo ou de recuperação" : "Código que chegou no e-mail" }), campo]),
    criar("p", { className: "dica", textContent: "A confirmação vale por 10 minutos neste aparelho." }),
    msg,
  ];
  let reenviar: HTMLButtonElement | null = null;
  const pedirCodigo = async () => {
    if (reenviar) ocupado(reenviar, true);
    try {
      const r = await api<{ email: string }>("/api/conta/confirmar/codigo", { method: "POST" });
      explicacao.textContent = `Por segurança, enviamos um código de 8 dígitos para ${r.email}. Ele chega em instantes (confira também a caixa de spam).`;
      aviso(msg, "");
      anunciar(`Código enviado para ${r.email}.`);
    } catch (e) {
      aviso(msg, textoErro(e), "erro");
    } finally {
      if (reenviar) ocupado(reenviar, false);
    }
  };
  if (!mfa) {
    reenviar = criar("button", { type: "button", className: "botao fantasma pequeno" }, ["Mandar outro código"]);
    reenviar.addEventListener("click", () => void pedirCodigo());
    itens.push(criar("div", { className: "acoes" }, [reenviar]));
  }
  const form = criar("form", { noValidate: true, className: "pilha" }, [...itens, criar("div", { className: "dialogo-acoes" }, [cancelar, confirmarB])]);
  corpo.append(form);
  cancelar.addEventListener("click", () => d.close("nao"));
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const codigo = campo.value.trim();
    if (!codigo) {
      marcarErro(campo, true);
      aviso(msg, "Digite o código.", "erro");
      campo.focus();
      return;
    }
    ocupado(confirmarB, true);
    try {
      const r = await api<{ confirmada_s: number }>("/api/conta/confirmar", { method: "POST", body: JSON.stringify({ codigo }) });
      atualizarConta({ confirmada_s: r.confirmada_s });
      anunciar("Confirmado.");
      d.close("ok");
    } catch (e) {
      marcarErro(campo, true);
      aviso(msg, textoErro(e), "erro");
      campo.select();
      campo.focus();
    } finally {
      ocupado(confirmarB, false);
    }
  });
  return new Promise((resolver) => {
    d.addEventListener("close", () => {
      resolver(d.returnValue === "ok");
      d.remove();
      if (origem?.isConnected) origem.focus();
    });
    d.showModal();
    campo.focus();
    if (!mfa) void pedirCodigo();
  });
}

const NOMES_DOCUMENTOS: Record<string, [string, string]> = { termos: ["Termos de Uso", "/termos"], privacidade: ["Política de Privacidade", "/privacidade"] };

/** Os documentos mudaram: mostra o que mudou e pede o aceite. true: aceitou (ou não havia nada pendente). */
export function pedirAceite(): Promise<boolean> {
  const c = contaAtual();
  if (!c?.documentos?.pendentes?.length) return Promise.resolve(true);
  const origem = document.activeElement as HTMLElement | null;
  const { d, corpo } = dialogo("Os nossos termos mudaram", "confirmacao aceite-documentos");
  const msg = criar("div", { className: "aviso", hidden: true, attrs: { role: "alert" } });
  const id = `${d.getAttribute("aria-labelledby")}-aceite`;
  const caixa = criar("input", { type: "checkbox", id });
  const aceitar = criar("button", { type: "button", className: "botao primario", disabled: true }, ["Aceitar e continuar"]);
  const depois = criar("button", { type: "button", className: "botao" }, ["Agora não"]);
  caixa.addEventListener("change", () => (aceitar.disabled = !caixa.checked));
  const links = c.documentos.pendentes
    .filter((doc) => NOMES_DOCUMENTOS[doc])
    .map((doc) => criar("li", {}, [criar("a", { href: NOMES_DOCUMENTOS[doc][1], target: "_blank", rel: "noopener", textContent: `${NOMES_DOCUMENTOS[doc][0]} (abre em outra aba)` })]));
  corpo.append(
    criar("p", { textContent: "Atualizamos os documentos abaixo. Para continuar fazendo pedidos e recargas, leia e aceite a versão nova." }),
    criar("ul", { className: "lista-links" }, links),
    criar("p", {
      className: "dica",
      textContent:
        "O que mudou: nome e foto do perfil (a foto só se você ligar o Gravatar), verificação em duas etapas, confirmação antes de ações importantes, troca de e-mail, aparelhos conectados, desativar a conta e o tema das páginas.",
    }),
    criar("label", { className: "marcacao", htmlFor: id }, [caixa, criar("span", { textContent: "Li e aceito os Termos de Uso e a Política de Privacidade." })]),
    msg,
    criar("div", { className: "dialogo-acoes" }, [depois, aceitar]),
  );
  depois.addEventListener("click", () => d.close("nao"));
  aceitar.addEventListener("click", async () => {
    ocupado(aceitar, true);
    try {
      await api("/api/conta/aceitar", { method: "POST", body: JSON.stringify({ documentos: c.documentos.atuais }) });
      atualizarConta({ documentos: { atuais: c.documentos.atuais, pendentes: [] } });
      anunciar("Obrigado! A versão nova foi aceita.");
      d.close("ok");
    } catch (e) {
      ocupado(aceitar, false);
      if (e instanceof ErroApi && e.status === 409) {
        const recarregar = criar("button", { type: "button", className: "botao pequeno", textContent: "Recarregar a página" });
        recarregar.addEventListener("click", () => location.reload());
        aviso(msg, [criar("p", { textContent: textoErro(e) }), recarregar], "erro");
      } else aviso(msg, textoErro(e), "erro");
    }
  });
  return new Promise((resolver) => {
    d.addEventListener("close", () => {
      resolver(d.returnValue === "ok");
      d.remove();
      if (origem?.isConnected) origem.focus();
    });
    d.showModal();
    caixa.focus();
  });
}
