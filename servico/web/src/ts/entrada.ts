// Entrada na página do cliente (ADR 0007): e-mail → código de 8 dígitos no e-mail → (com a verificação em duas
// etapas ligada, o código do aplicativo autenticador ou um de recuperação) → sessão (cookie HttpOnly). No 1º
// acesso a conta nasce com o teste gratuito. Quem tinha um código de acesso antigo também entra por aqui ("Tenho
// um código de acesso antigo"). Entrar de novo numa conta pausada pelo próprio cliente a reativa. O navegador
// guarda só a marca "entrou" (inicio.ts), nunca a sessão.
import {
  CHAVE_CONECTADO,
  ErroApi,
  anunciar,
  api,
  aviso,
  avisoComRecarga,
  conectado,
  descartarAntecipados,
  el,
  marcarConectado,
  marcarErro,
  mostrarSenha,
  ocupado,
  textoErro,
} from "./comum";
import type { Conta } from "./conta";
import { type Verificacao, verificacao } from "./turnstile";

export { CHAVE_CONECTADO };

export type Publico = { turnstile: string | null; entrada_email: boolean; dominios: string[]; documentos: Record<string, string>; pagamento: boolean };
/** O que a página recebe ao entrar: a conta (já vem na resposta da entrada) e um aviso para mostrar (reativada). */
export type Entrou = { foco: boolean; conta?: Conta; aviso?: string };
type Resposta = { ok: boolean; nova?: boolean; reativada?: boolean; conta?: Conta; mfa?: boolean; desafio?: string; validade_s?: number };

const EMAIL = /^[^@\s<>"',;]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$/;
const CHAVE_AVISO = "render2go-aviso"; // o mesmo de configuracoes.ts: aviso depois de desativar ou excluir
const REATIVADA = "Sua conta estava desativada e voltou a valer. O saldo e o histórico continuam aqui.";

/** aoEntrar carrega a área da conta e lança ErroApi (401) se a sessão não vale mais. */
export function iniciarEntrada(aoEntrar: (e: Entrou) => Promise<void>): void {
  const raiz = document.documentElement;
  const sair = el<HTMLButtonElement>("sair");
  const msg = el("msg-entrada");
  const formEmail = el<HTMLFormElement>("form-email");
  const formCodigo = el<HTMLFormElement>("form-codigo");
  const formMfa = el<HTMLFormElement>("form-mfa");
  const formToken = el<HTMLFormElement>("form-token");
  const campoEmail = el<HTMLInputElement>("email");
  const campoCodigo = el<HTMLInputElement>("codigo");
  const campoMfa = el<HTMLInputElement>("codigo-mfa");
  const campoToken = el<HTMLInputElement>("token");
  const aceite = el<HTMLInputElement>("aceite");
  const botaoEmail = el<HTMLButtonElement>("enviar-email");
  let anti: Verificacao | null = null;
  let carregado = false;
  let emailEnviado = "";
  let desafio: { token: string; ate: number; nova: boolean } | null = null;
  let recuperacao = false;
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
    descartarAntecipados(); // pedidas com a sessão que venceu: depois de entrar, as telas pedem de novo
    marcarConectado(CHAVE_CONECTADO, false);
    raiz.classList.remove("com-sessao");
    sair.hidden = true;
    void prepararFormulario();
    if (texto) aviso(msg, texto, "info");
    campoEmail.focus();
  };

  const entrar = async (dados: Entrou) => {
    raiz.classList.add("com-sessao");
    sair.hidden = false;
    try {
      await aoEntrar(dados);
      marcarConectado(CHAVE_CONECTADO, true);
    } catch (e) {
      if (e instanceof ErroApi && e.status === 401) voltarAEntrada(dados.foco ? "" : "Sua sessão terminou. Entre de novo com o seu e-mail.");
      else {
        const painelMsg = document.getElementById("msg-painel") ?? document.getElementById("msg");
        if (painelMsg) avisoComRecarga(painelMsg, `Não foi possível carregar: ${textoErro(e)}`);
        anunciar(`Não foi possível carregar: ${textoErro(e)}`, true);
      }
    }
  };

  const passo = (qual: "email" | "codigo" | "mfa") => {
    formEmail.hidden = qual !== "email";
    formCodigo.hidden = qual !== "codigo";
    formMfa.hidden = qual !== "mfa";
    el("codigo-antigo").hidden = qual === "mfa";
  };

  /** Resposta da entrada: sessão aberta, ou o passo do aplicativo autenticador (verificação em duas etapas). */
  const depoisDaEntrada = async (r: Resposta) => {
    if (r.mfa && r.desafio) {
      desafio = { token: r.desafio, ate: Date.now() + (r.validade_s ?? 300) * 1000, nova: !!r.nova };
      modoRecuperacao(false);
      passo("mfa");
      campoMfa.value = "";
      aviso(el("mfa-pedido"), "Esta conta usa a verificação em duas etapas. Falta só o código do seu aplicativo autenticador.", "info");
      campoMfa.focus();
      anunciar("Digite o código do aplicativo autenticador.");
      return;
    }
    if (r.nova) anunciar("Conta criada. Seu teste gratuito está disponível.");
    descartarAntecipados(); // a sessão é outra: nada do que foi pedido antes dela vale
    await entrar({ foco: true, conta: r.conta, aviso: r.reativada ? REATIVADA : undefined });
  };

  const modoRecuperacao = (sim: boolean) => {
    recuperacao = sim;
    el("rotulo-mfa").textContent = sim ? "Código de recuperação" : "Código do aplicativo autenticador (6 dígitos)";
    el("codigo-mfa-dica").textContent = sim
      ? "Um dos 10 códigos que você guardou ao ligar a verificação (letras e números, como abcde-23456). Cada um vale uma vez."
      : "Os 6 dígitos que aparecem agora no aplicativo (Google Authenticator, Microsoft Authenticator, Authy…).";
    campoMfa.inputMode = sim ? "text" : "numeric";
    campoMfa.placeholder = sim ? "xxxxx-xxxxx" : "";
    el("mfa-recuperacao").textContent = sim ? "Usar o código do aplicativo" : "Perdi o celular: usar um código de recuperação";
    marcarErro(campoMfa, false);
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
      passo("codigo");
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
      await depoisDaEntrada(await api<Resposta>("/api/conta/entrar", { method: "POST", body: JSON.stringify({ email: emailEnviado, codigo, aceite: true }) }));
    } catch (e) {
      erro(textoErro(e), campoCodigo);
    } finally {
      ocupado(botao, false);
    }
  });

  formMfa.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    aviso(msg, "");
    if (!desafio || Date.now() > desafio.ate) {
      desafio = null;
      passo("email");
      return erro("A verificação venceu (vale 5 minutos). Peça um código novo no e-mail e entre de novo.", campoEmail);
    }
    const bruto = campoMfa.value.trim();
    const codigo = recuperacao ? bruto.toLowerCase().replace(/[\s-]/g, "") : bruto.replace(/\D/g, "");
    if (recuperacao ? !/^[a-z0-9]{10}$/.test(codigo) : codigo.length !== 6)
      return erro(recuperacao ? "O código de recuperação tem 10 letras e números (como abcde-23456)." : "O código do aplicativo tem 6 dígitos.", campoMfa);
    const botao = el<HTMLButtonElement>("enviar-mfa");
    ocupado(botao, true);
    try {
      const r = await api<Resposta>("/api/conta/entrar-mfa", { method: "POST", body: JSON.stringify({ desafio: desafio.token, codigo }) });
      const nova = desafio.nova;
      desafio = null;
      campoMfa.value = "";
      await depoisDaEntrada({ ...r, nova: nova || r.nova });
    } catch (e) {
      if (e instanceof ErroApi && e.status === 401 && /venceu/.test(e.message)) {
        desafio = null;
        passo("email");
        erro(textoErro(e), campoEmail);
      } else erro(textoErro(e), campoMfa);
    } finally {
      ocupado(botao, false);
    }
  });
  el("mfa-recuperacao").addEventListener("click", () => {
    modoRecuperacao(!recuperacao);
    campoMfa.value = "";
    campoMfa.focus();
  });
  el("mfa-voltar").addEventListener("click", () => {
    desafio = null;
    passo("email");
    aviso(msg, "");
    campoEmail.focus();
  });

  el("trocar-email").addEventListener("click", () => {
    passo("email");
    aviso(msg, "");
    campoEmail.select();
    campoEmail.focus();
  });
  el("reenviar").addEventListener("click", () => {
    passo("email");
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
      const r = await api<Resposta>("/api/conta/entrar-codigo", { method: "POST", body: JSON.stringify({ codigo, aceite: true }) });
      campoToken.value = "";
      await depoisDaEntrada(r);
    } catch (e) {
      erro(textoErro(e), campoToken);
    } finally {
      ocupado(botao, false);
    }
  });
  for (const campo of [campoEmail, campoCodigo, campoMfa, campoToken, aceite]) campo.addEventListener("input", () => marcarErro(campo, false));

  sair.addEventListener("click", async () => {
    ocupado(sair, true);
    await api("/api/conta/sair", { method: "POST" }).catch(() => undefined);
    marcarConectado(CHAVE_CONECTADO, false);
    location.assign("/");
  });

  if (conectado(CHAVE_CONECTADO)) void entrar({ foco: false });
  else {
    raiz.classList.remove("com-sessao");
    void prepararFormulario();
    // depois de desativar ou excluir a conta, a página volta para a entrada com o aviso
    try {
      const texto = sessionStorage.getItem(CHAVE_AVISO);
      if (texto) {
        sessionStorage.removeItem(CHAVE_AVISO);
        aviso(msg, texto, "info");
      }
    } catch {
      /* sem armazenamento */
    }
  }
}
