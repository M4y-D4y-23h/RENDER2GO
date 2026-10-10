// Página de suporte (/suporte): funciona com ou sem conta. Conectado, a mensagem vai ligada à conta; sem
// conta (quem não consegue entrar), o e-mail é obrigatório, há o "não sou um robô" e os limites são menores.
import { CHAVE_CONECTADO, ErroApi, anunciar, api, aviso, conectado, contador, el, marcarErro, ocupado, textoErro } from "./comum";
import { iniciarCookies } from "./cookies";
import type { Publico } from "./entrada";
import { iniciarTopo, semSessao } from "./topo";
import { type Verificacao, verificacao } from "./turnstile";

// a mesma regra do servidor (render2go_servico/suporte.py), só para avisar antes de enviar
const EMAIL = /^[^@\s<>"',;]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$/;

async function iniciarSuporte(): Promise<void> {
  const form = el<HTMLFormElement>("form-suporte");
  const mensagem = el<HTMLTextAreaElement>("suporte-mensagem");
  const email = el<HTMLInputElement>("suporte-email");
  const saida = el("suporte-msg");
  const botao = el<HTMLButtonElement>("suporte-enviar");
  const atualizarContador = contador(mensagem, el("suporte-contador"));
  // conectado (a sessão é conferida pela API): a mensagem vai ligada à conta. O aviso e o "(opcional)" já
  // aparecem desde o 1º quadro, pela marca de sessão (CSS); aqui só entra o e-mail, numa linha de altura fixa
  let comConta = false;
  if (conectado(CHAVE_CONECTADO)) {
    try {
      const c = await api<{ email: string | null }>("/api/conta");
      comConta = true;
      if (c.email) {
        el("suporte-conta-email").textContent = ` como ${c.email}`;
        el("suporte-conta-linha").title = `Você está conectado como ${c.email}`; // a linha pode ser cortada (…)
      }
    } catch (e) {
      if (e instanceof ErroApi && e.status === 401) semSessao(); // sessão vencida: segue como quem não entrou
      else el("suporte-conta").hidden = true; // sem confirmar a conta, o formulário segue como o de quem não entrou
    }
  }
  let anti: Verificacao | null = null;
  if (!comConta) {
    email.required = true;
    el("email-opcional").hidden = true;
    const caixa = el("verificacao-suporte");
    caixa.classList.remove("so-sem-sessao"); // sem conta confirmada, o quadro aparece mesmo com a marca de sessão
    try {
      anti = verificacao(caixa, (await api<Publico>("/api/publico")).turnstile, "suporte");
    } catch {
      /* sem a configuração: o envio mostra o erro da API */
    }
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
    if ((!comConta && !e) || (e && !EMAIL.test(e))) {
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
    const turnstile = anti ? anti.token() : null;
    if (anti && !turnstile) {
      aviso(saida, "Confirme a verificação de segurança (não sou um robô) e envie de novo.", "erro");
      return;
    }
    ocupado(botao, true);
    try {
      await api("/api/suporte", {
        method: "POST",
        body: JSON.stringify({
          assunto: el<HTMLInputElement>("suporte-assunto").value.trim(),
          mensagem: mensagem.value.trim(),
          email: email.value.trim() || null,
          turnstile,
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
      anti?.renovar(); // a resposta da verificação vale uma vez
      ocupado(botao, false);
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  iniciarCookies();
  iniciarTopo();
  void iniciarSuporte();
});
