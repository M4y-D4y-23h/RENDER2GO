// Página de suporte (/suporte): funciona com ou sem código de acesso. Conectado, a mensagem vai
// ligada à conta; sem código (quem perdeu o código), o e-mail é obrigatório e os limites são menores.
import { anunciar, api, aviso, contador, el, lerToken, marcarErro, ocupado, textoErro } from "./comum";
import { iniciarCookies } from "./cookies";

// a mesma regra do servidor (render2go_servico/suporte.py), só para avisar antes de enviar
const EMAIL = /^[^@\s<>"',;]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$/;

function iniciarSuporte(): void {
  const token = lerToken("render2go-token-cliente"); // conectado: a mensagem vai ligada à conta
  const form = el<HTMLFormElement>("form-suporte");
  const mensagem = el<HTMLTextAreaElement>("suporte-mensagem");
  const email = el<HTMLInputElement>("suporte-email");
  const saida = el("suporte-msg");
  const botao = el<HTMLButtonElement>("suporte-enviar");
  const atualizarContador = contador(mensagem, el("suporte-contador"));
  if (token) {
    aviso(el("suporte-conta"), "Você está conectado com o seu código de acesso: a mensagem vai junto com os seus pedidos.");
    el("email-opcional").hidden = false;
  } else {
    email.required = true;
  }
  mensagem.addEventListener("input", () => marcarErro(mensagem, false));
  email.addEventListener("input", () => marcarErro(email, false));

  const validar = (): HTMLElement | null => {
    if (mensagem.value.trim().length < 10) {
      marcarErro(mensagem, true);
      aviso(saida, "Escreva a mensagem com pelo menos 10 letras.", "erro");
      return mensagem;
    }
    const e = email.value.trim();
    if ((!token && !e) || (e && !EMAIL.test(e))) {
      marcarErro(email, true);
      aviso(saida, e ? "Confira o e-mail: ele parece incompleto." : "Informe o seu e-mail para podermos responder.", "erro");
      return email;
    }
    return null;
  };

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const invalido = validar();
    if (invalido) {
      invalido.focus();
      anunciar(saida.textContent ?? "", true);
      return;
    }
    ocupado(botao, true);
    try {
      await api("/api/suporte", token, {
        method: "POST",
        body: JSON.stringify({
          assunto: el<HTMLInputElement>("suporte-assunto").value.trim(),
          mensagem: mensagem.value.trim(),
          email: email.value.trim() || null,
        }),
      });
      aviso(saida, "Mensagem enviada. Respondemos assim que possível.", "ok");
      anunciar("Mensagem enviada. Respondemos assim que possível.");
      form.reset();
      atualizarContador();
    } catch (e) {
      aviso(saida, textoErro(e), "erro");
      anunciar(textoErro(e), true);
    } finally {
      ocupado(botao, false);
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  iniciarCookies();
  iniciarSuporte();
});
