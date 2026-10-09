// Entrada na página do cliente (ADR 0007): e-mail → código de 8 dígitos no e-mail → sessão (cookie HttpOnly).
// No 1º acesso a conta nasce com o teste gratuito. Quem tinha um código de acesso antigo também entra por
// aqui ("Tenho um código de acesso antigo"). O navegador guarda só a marca "entrou" (inicio.ts), nunca a sessão.
import { ErroApi, anunciar, api, aviso, avisoComRecarga, conectado, el, marcarConectado, marcarErro, mostrarSenha, ocupado, textoErro } from "./comum";
import { type Verificacao, verificacao } from "./turnstile";

export const CHAVE_CONECTADO = "render2go-conectado";

export type Publico = { turnstile: string | null; entrada_email: boolean; dominios: string[]; documentos: Record<string, string>; pagamento: boolean };

const EMAIL = /^[^@\s<>"',;]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$/;

/** aoEntrar carrega o painel e lança ErroApi (401) se a sessão não vale mais. */
export function iniciarEntrada(aoEntrar: () => Promise<void>): void {
  const raiz = document.documentElement;
  const sair = el<HTMLButtonElement>("sair");
  const msg = el("msg-entrada");
  const formEmail = el<HTMLFormElement>("form-email");
  const formCodigo = el<HTMLFormElement>("form-codigo");
  const formToken = el<HTMLFormElement>("form-token");
  const campoEmail = el<HTMLInputElement>("email");
  const campoCodigo = el<HTMLInputElement>("codigo");
  const campoToken = el<HTMLInputElement>("token");
  const aceite = el<HTMLInputElement>("aceite");
  const botaoEmail = el<HTMLButtonElement>("enviar-email");
  let anti: Verificacao | null = null;
  let carregado = false;
  let emailEnviado = "";
  mostrarSenha(el<HTMLButtonElement>("mostrar-token"), campoToken);

  const erro = (texto: string, campo?: HTMLElement) => {
    aviso(msg, texto, "erro");
    anunciar(texto, true);
    if (campo) {
      marcarErro(campo, true);
      campo.focus();
    }
  };

  const prepararFormulario = async () => {
    if (carregado) return;
    carregado = true;
    try {
      const pub = await api<Publico>("/api/publico");
      anti = verificacao(el("verificacao-entrar"), pub.turnstile, "codigo");
      if (!pub.entrada_email) {
        aviso(msg, "A entrada por e-mail está em manutenção agora. Tente de novo mais tarde ou fale com o suporte.", "alerta");
        botaoEmail.disabled = true;
      }
    } catch (e) {
      carregado = false;
      avisoComRecarga(msg, `Não foi possível carregar a entrada: ${textoErro(e)}`);
    }
  };

  const voltarAEntrada = (texto = "") => {
    marcarConectado(CHAVE_CONECTADO, false);
    raiz.classList.remove("com-sessao");
    sair.hidden = true;
    void prepararFormulario();
    if (texto) aviso(msg, texto, "info");
    campoEmail.focus();
  };

  const entrar = async (focar: boolean) => {
    raiz.classList.add("com-sessao");
    sair.hidden = false;
    try {
      await aoEntrar();
      marcarConectado(CHAVE_CONECTADO, true);
      if (focar) el("titulo-painel").focus();
    } catch (e) {
      if (e instanceof ErroApi && e.status === 401) voltarAEntrada(focar ? "" : "Sua sessão terminou. Entre de novo com o seu e-mail.");
      else {
        const painelMsg = document.getElementById("msg-painel") ?? document.getElementById("msg");
        if (painelMsg) avisoComRecarga(painelMsg, `Não foi possível carregar: ${textoErro(e)}`);
        anunciar(`Não foi possível carregar: ${textoErro(e)}`, true);
      }
    }
  };

  const passo = (codigo: boolean) => {
    formEmail.hidden = codigo;
    formCodigo.hidden = !codigo;
  };

  formEmail.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    aviso(msg, "");
    const email = campoEmail.value.trim();
    if (!EMAIL.test(email)) return erro(email ? "Confira o e-mail: ele parece incompleto." : "Digite o seu e-mail.", campoEmail);
    if (!aceite.checked) return erro("Para continuar, aceite os Termos de Uso e a Política de Privacidade.", aceite);
    const token = anti ? anti.token() : null;
    if (anti && !token) return erro("Confirme a verificação de segurança (não sou um robô) e tente de novo.");
    ocupado(botaoEmail, true);
    try {
      const r = await api<{ email: string }>("/api/conta/codigo", { method: "POST", body: JSON.stringify({ email, aceite: true, turnstile: token }) });
      emailEnviado = r.email;
      passo(true);
      const enviado = el("codigo-enviado");
      aviso(enviado, `Enviamos um código de 8 dígitos para ${r.email}. Ele chega em instantes.`, "info");
      campoCodigo.value = "";
      campoCodigo.focus();
      anunciar(`Código enviado para ${r.email}.`);
    } catch (e) {
      erro(textoErro(e), e instanceof ErroApi && e.status === 422 ? campoEmail : undefined);
    } finally {
      anti?.renovar(); // a resposta da verificação vale uma vez
      ocupado(botaoEmail, false);
    }
  });

  formCodigo.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    aviso(msg, "");
    const codigo = campoCodigo.value.replace(/\D/g, "");
    if (codigo.length !== 8) return erro("O código tem 8 dígitos. Confira no seu e-mail.", campoCodigo);
    const botao = el<HTMLButtonElement>("enviar-codigo");
    ocupado(botao, true);
    try {
      const r = await api<{ nova: boolean }>("/api/conta/entrar", { method: "POST", body: JSON.stringify({ email: emailEnviado, codigo, aceite: true }) });
      if (r.nova) anunciar("Conta criada. Seu teste gratuito está disponível.");
      await entrar(true);
    } catch (e) {
      erro(textoErro(e), campoCodigo);
    } finally {
      ocupado(botao, false);
    }
  });

  el("trocar-email").addEventListener("click", () => {
    passo(false);
    aviso(msg, "");
    campoEmail.select();
    campoEmail.focus();
  });
  el("reenviar").addEventListener("click", () => {
    passo(false);
    aviso(msg, "Confirme a verificação de segurança e clique em “Receber o código” para mandar outro.", "info");
    botaoEmail.focus();
  });

  formToken.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    aviso(msg, "");
    const codigo = campoToken.value.trim();
    if (!codigo) return erro("Digite o seu código de acesso.", campoToken);
    if (!aceite.checked) return erro("Para continuar, aceite os Termos de Uso e a Política de Privacidade.", aceite);
    const botao = el<HTMLButtonElement>("enviar-token");
    ocupado(botao, true);
    try {
      await api("/api/conta/entrar-codigo", { method: "POST", body: JSON.stringify({ codigo, aceite: true }) });
      campoToken.value = "";
      await entrar(true);
    } catch (e) {
      erro(textoErro(e), campoToken);
    } finally {
      ocupado(botao, false);
    }
  });
  for (const campo of [campoEmail, campoCodigo, campoToken, aceite]) campo.addEventListener("input", () => marcarErro(campo, false));

  sair.addEventListener("click", async () => {
    ocupado(sair, true);
    await api("/api/conta/sair", { method: "POST" }).catch(() => undefined);
    marcarConectado(CHAVE_CONECTADO, false);
    location.reload();
  });

  if (conectado(CHAVE_CONECTADO)) void entrar(false);
  else {
    raiz.classList.remove("com-sessao");
    void prepararFormulario();
  }
}
