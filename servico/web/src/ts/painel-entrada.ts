// Entrada no painel em três modos (ADR 0009): a pessoa (nome de acesso + senha + código do aplicativo dela, que
// vale uma vez), o primeiro acesso por convite (#convite=… no endereço, de uso único: QR Code → um código do
// aplicativo → a senha) e a chave do servidor (emergência, registrada como tal na auditoria). Nenhum segredo fica
// no navegador: a sessão é um cookie HttpOnly restrito ao endereço do painel, e o QR Code só existe na tela.
import { ErroApi, api, aviso, criar, el, icone, marcarErro, mostrarSenha, ocupado, quando, rotular, textoErro } from "./comum";
import { AVISO_ENTRADA, PAPEIS, type Papel } from "./painel-base";

type InicioConvite = { usuario: string; papel: Papel; expira: number; segredo: string; uri: string; qr_svg: string };

const NAO_CONFERE = "Cada código do aplicativo vale uma vez: se acabou de usar um, espere o próximo aparecer.";

function codigoDe(campo: HTMLInputElement): string {
  return campo.value.replace(/\D/g, "");
}

/** `aoEntrar` abre o painel (a sessão já está no cookie). Devolve o que a página faz quando não há sessão. */
export function iniciarEntrada(aoEntrar: () => Promise<void>): { semSessao: () => void } {
  const raiz = document.documentElement;
  const emConvite = () => raiz.dataset.convite !== undefined;

  // -- a pessoa: nome de acesso, senha e código do aplicativo --------------------------------------------------
  const form = el<HTMLFormElement>("form-admin");
  const usuario = el<HTMLInputElement>("admin-usuario");
  const senha = el<HTMLInputElement>("admin-senha");
  const codigo = el<HTMLInputElement>("admin-codigo");
  const msg = el("msg-entrada");
  mostrarSenha(el<HTMLButtonElement>("mostrar-senha"), senha);
  for (const campo of [usuario, senha, codigo]) campo.addEventListener("input", () => marcarErro(campo, false));
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    aviso(msg, "");
    const nome = usuario.value.trim().toLowerCase();
    const k = codigoDe(codigo);
    const falta: [HTMLInputElement, string] | null = !nome
      ? [usuario, "Digite o seu nome de acesso."]
      : !senha.value
        ? [senha, "Digite a sua senha."]
        : k.length !== 6
          ? [codigo, "Digite os 6 dígitos que aparecem agora no aplicativo autenticador."]
          : null;
    if (falta) {
      marcarErro(falta[0], true);
      aviso(msg, falta[1], "erro");
      falta[0].focus();
      return;
    }
    const botao = el<HTMLButtonElement>("entrar-pessoa");
    ocupado(botao, true);
    try {
      await api("api/entrar", { method: "POST", body: JSON.stringify({ usuario: nome, senha: senha.value, totp: k }) });
      senha.value = codigo.value = "";
      await aoEntrar();
    } catch (e) {
      // a mesma mensagem para qualquer erro (o servidor não diz qual campo errou): a senha e o código saem
      aviso(msg, e instanceof ErroApi && e.status === 401 ? `${textoErro(e)}. ${NAO_CONFERE}` : textoErro(e), "erro");
      senha.value = codigo.value = "";
      (nome ? senha : usuario).focus();
    } finally {
      ocupado(botao, false);
    }
  });

  // -- emergência: a chave do servidor e o código do autenticador do servidor ---------------------------------
  const formChave = el<HTMLFormElement>("form-emergencia");
  const token = el<HTMLInputElement>("admin-token");
  const totp = el<HTMLInputElement>("admin-totp");
  const msgChave = el("msg-emergencia");
  mostrarSenha(el<HTMLButtonElement>("mostrar-token"), token);
  for (const campo of [token, totp]) campo.addEventListener("input", () => marcarErro(campo, false));
  formChave.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    aviso(msgChave, "");
    if (!token.value.trim()) {
      marcarErro(token, true);
      aviso(msgChave, "Digite a chave do servidor.", "erro");
      token.focus();
      return;
    }
    const botao = el<HTMLButtonElement>("admin-entrar");
    ocupado(botao, true);
    try {
      await api("api/entrar", { method: "POST", body: JSON.stringify({ token: token.value.trim(), totp: codigoDe(totp) || null }) });
      token.value = totp.value = "";
      await aoEntrar();
    } catch (e) {
      aviso(msgChave, e instanceof ErroApi && e.status === 401 ? `${textoErro(e)}. ${NAO_CONFERE}` : textoErro(e), "erro");
      totp.value = "";
      (e instanceof ErroApi && e.status === 401 ? totp : token).focus();
    } finally {
      ocupado(botao, false);
    }
  });

  // -- primeiro acesso por convite: QR Code → código do aplicativo → senha --------------------------------------
  if (emConvite()) iniciarConvite(aoEntrar);

  return {
    semSessao: () => {
      if (emConvite()) return; // o convite já está na tela
      let texto: string | null = null;
      try {
        texto = sessionStorage.getItem(AVISO_ENTRADA);
        sessionStorage.removeItem(AVISO_ENTRADA);
      } catch {
        /* sem armazenamento */
      }
      if (texto) aviso(msg, texto, "info");
      usuario.focus();
    },
  };
}

function iniciarConvite(aoEntrar: () => Promise<void>): void {
  const raiz = document.documentElement;
  const msg = el("msg-convite");
  const quem = el("convite-quem");
  const img = el<HTMLImageElement>("convite-qr");
  const passoCodigo = el<HTMLFieldSetElement>("convite-passo-codigo");
  const passoSenha = el<HTMLFieldSetElement>("convite-passo-senha");
  const campoCodigo = el<HTMLInputElement>("convite-codigo");
  const senha = el<HTMLInputElement>("convite-senha");
  const senha2 = el<HTMLInputElement>("convite-senha-2");
  const confirmar = el<HTMLButtonElement>("convite-confirmar");
  // o convite fica só no endereço (#convite=…), que nunca vai ao servidor nem a outros sites
  const token = /^#convite=([A-Za-z0-9_-]{20,200})$/.exec(location.hash)?.[1] ?? null;
  let convite: InicioConvite | null = null;
  let urlQr: string | null = null;

  const parar = (texto: string) => {
    aviso(msg, texto, "erro");
    passoCodigo.disabled = passoSenha.disabled = true;
  };

  const comecar = async () => {
    aviso(msg, "");
    passoCodigo.disabled = passoSenha.disabled = true;
    rotular(confirmar, "Confirmar o código");
    if (!token) {
      quem.textContent = "Este endereço de convite está incompleto.";
      parar("Copie o endereço inteiro que você recebeu (ele termina com uma sequência longa de letras e números) e abra de novo.");
      return;
    }
    try {
      convite = await api<InicioConvite>("api/convite/iniciar", { method: "POST", body: JSON.stringify({ convite: token }) });
    } catch (e) {
      quem.textContent = "Não foi possível abrir este convite.";
      parar(textoErro(e));
      return;
    }
    const c = convite;
    quem.replaceChildren(
      "Convite para criar o acesso ",
      criar("strong", { textContent: c.usuario }),
      ` ao painel, com o papel de ${PAPEIS[c.papel].toLowerCase()}. Vale até ${quando(c.expira)}, uma vez só.`,
    );
    // o QR Code (SVG feito pelo servidor) entra como imagem (blob:), nunca como HTML na página
    if (urlQr) URL.revokeObjectURL(urlQr);
    urlQr = URL.createObjectURL(new Blob([c.qr_svg], { type: "image/svg+xml" }));
    img.src = urlQr;
    img.hidden = false;
    el("convite-chave").textContent = c.segredo.replace(/(.{4})(?=.)/g, "$1 ");
    el<HTMLInputElement>("convite-usuario").value = c.usuario;
    passoCodigo.disabled = false;
    campoCodigo.value = "";
    campoCodigo.focus({ preventScroll: true });
  };

  // 409: o QR Code desta tela não é mais o que vale (outra aba abriu o convite, ou a confirmação venceu)
  const recomecar = (texto: string) => {
    const outro = criar("button", { type: "button", className: "botao pequeno" }, [icone("atualizar"), "Gerar outro QR Code"]);
    outro.addEventListener("click", () => void comecar());
    aviso(msg, [criar("p", { textContent: `${texto}. Apague do aplicativo o acesso lido antes e leia o QR Code novo.` }), outro], "erro");
  };

  campoCodigo.addEventListener("input", () => marcarErro(campoCodigo, false));
  el<HTMLFormElement>("form-convite-codigo").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    aviso(msg, "");
    const k = codigoDe(campoCodigo);
    if (k.length !== 6) {
      marcarErro(campoCodigo, true);
      aviso(msg, "Digite os 6 dígitos que aparecem agora no aplicativo autenticador.", "erro");
      campoCodigo.focus();
      return;
    }
    ocupado(confirmar, true);
    try {
      await api("api/convite/confirmar", { method: "POST", body: JSON.stringify({ convite: token, codigo: k }) });
      ocupado(confirmar, false);
      rotular(confirmar, "Código confirmado");
      passoCodigo.disabled = true;
      passoSenha.disabled = false;
      senha.focus();
    } catch (e) {
      ocupado(confirmar, false);
      if (e instanceof ErroApi && e.status === 409) {
        recomecar(textoErro(e));
        return;
      }
      marcarErro(campoCodigo, true);
      aviso(msg, e instanceof ErroApi && e.status === 401 ? `${textoErro(e)}. ${NAO_CONFERE}` : textoErro(e), "erro");
      campoCodigo.select();
      campoCodigo.focus();
    }
  });

  for (const campo of [senha, senha2]) campo.addEventListener("input", () => marcarErro(campo, false));
  el<HTMLFormElement>("form-convite-senha").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    aviso(msg, "");
    const nome = convite?.usuario ?? "";
    const problema: [HTMLInputElement, string] | null =
      senha.value.length < 12
        ? [senha, "A senha precisa ter pelo menos 12 caracteres."]
        : nome && senha.value.toLowerCase().includes(nome)
          ? [senha, "A senha não pode conter o nome de acesso."]
          : senha.value !== senha2.value
            ? [senha2, "As duas senhas não são iguais. Digite de novo."]
            : null;
    if (problema) {
      marcarErro(problema[0], true);
      aviso(msg, problema[1], "erro");
      problema[0].focus();
      return;
    }
    const botao = el<HTMLButtonElement>("convite-criar");
    ocupado(botao, true);
    try {
      await api("api/convite/senha", { method: "POST", body: JSON.stringify({ convite: token, senha: senha.value }) });
      senha.value = senha2.value = "";
      if (urlQr) URL.revokeObjectURL(urlQr);
      img.removeAttribute("src");
      // o convite foi gasto: sai do endereço e da tela, e o painel abre na visão geral
      history.replaceState(null, "", `${location.pathname}#visao-geral`);
      delete raiz.dataset.convite;
      raiz.dataset.secao = "visao-geral";
      await aoEntrar();
    } catch (e) {
      if (e instanceof ErroApi && e.status === 409) {
        // a confirmação do código vale 15 minutos: confirma um código novo (o mesmo QR Code continua valendo)
        passoSenha.disabled = true;
        passoCodigo.disabled = false;
        rotular(confirmar, "Confirmar o código");
        aviso(msg, `${textoErro(e)}. Digite o código que aparece agora no aplicativo e confirme de novo.`, "erro");
        campoCodigo.value = "";
        campoCodigo.focus();
        return;
      }
      marcarErro(senha, true);
      aviso(msg, textoErro(e), "erro");
      senha.focus();
    } finally {
      ocupado(botao, false);
    }
  });

  void comecar();
}
