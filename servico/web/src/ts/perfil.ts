// Tela "Perfil" (/perfil): a foto (do Gravatar, só se a pessoa ligar; sem foto, a inicial), o nome e o sobrenome
// (POST /api/conta/perfil) e, em breve, o nome de usuário e os ganhos por indicação (com "Tenho interesse").
// A foto vem do próprio site (GET /api/conta/foto): o navegador nunca fala com o Gravatar.
import { anunciar, api, aviso, el, marcarErro, ocupado, textoErro } from "./comum";
import { type Conta, aoMudarConta, contaAtual, definirConta, desenharAvatar, nomeCompleto, resumoPlano } from "./conta";
import { botaoInteresse } from "./interesse";

const NOME = /^[\p{L}\p{M} '’.-]*$/u;

/** O mesmo que a API confere (render2go_servico/perfil.py): até 60 caracteres, letras, espaço, ' ’ - e ponto. */
function problemaNome(texto: string): string | null {
  const t = texto.replace(/\s+/g, " ").trim();
  if (!t) return null;
  if (t.length > 60) return "use até 60 caracteres";
  if (!NOME.test(t) || !/\p{L}/u.test(t)) return "use só letras, espaço, apóstrofo, hífen e ponto";
  return null;
}

export function criarPerfil(): { abrir: () => void } {
  const form = el<HTMLFormElement>("form-perfil");
  const primeiro = el<HTMLInputElement>("perfil-primeiro");
  const ultimo = el<HTMLInputElement>("perfil-ultimo");
  const gravatar = el<HTMLInputElement>("perfil-gravatar");
  const msg = el("perfil-msg");
  let editando = false; // a pessoa mexeu nos campos: a conta que chega depois não apaga o que ela digitou
  let interesses = false;

  const preencher = (c: Conta) => {
    desenharAvatar(el("perfil-avatar"), c);
    el("perfil-nome").textContent = nomeCompleto(c) || "Sem nome no perfil";
    el("perfil-email").textContent = c.email ?? "Conta sem e-mail (código de acesso)";
    el("perfil-plano").textContent = resumoPlano(c);
    if (!editando) {
      primeiro.value = c.perfil?.primeiro_nome ?? "";
      ultimo.value = c.perfil?.ultimo_nome ?? "";
    }
    gravatar.checked = !!c.perfil?.gravatar;
    gravatar.disabled = !c.email;
    if (!interesses) {
      interesses = true; // os "Tenho interesse" já sabem o que a conta marcou
      const erro = (t: string) => aviso(msg, t, "erro");
      el("interesse-nome-usuario").replaceChildren(botaoInteresse("recurso:nome-usuario", "Nome de usuário", erro));
      el("interesse-indicacao").replaceChildren(botaoInteresse("recurso:indicacao", "Ganhos por indicação", erro));
    }
  };
  aoMudarConta(preencher);

  for (const campo of [primeiro, ultimo]) {
    campo.addEventListener("input", () => {
      editando = true;
      marcarErro(campo, false);
    });
  }

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    aviso(msg, "");
    for (const [campo, rotulo] of [[primeiro, "Nome"], [ultimo, "Sobrenome"]] as const) {
      const problema = problemaNome(campo.value);
      if (problema) {
        marcarErro(campo, true);
        aviso(msg, `${rotulo}: ${problema}.`, "erro");
        campo.focus();
        return;
      }
    }
    const botao = el<HTMLButtonElement>("perfil-salvar");
    ocupado(botao, true);
    try {
      const c = await api<Conta>("/api/conta/perfil", { method: "POST", body: JSON.stringify({ primeiro_nome: primeiro.value, ultimo_nome: ultimo.value }) });
      editando = false;
      definirConta({ ...(contaAtual() ?? c), ...c });
      aviso(msg, "Perfil salvo.", "ok");
      anunciar("Perfil salvo.");
    } catch (e) {
      aviso(msg, textoErro(e), "erro");
    } finally {
      ocupado(botao, false);
    }
  });

  gravatar.addEventListener("change", async () => {
    aviso(msg, "");
    const ligar = gravatar.checked;
    gravatar.disabled = true;
    try {
      const c = await api<Conta>("/api/conta/perfil", { method: "POST", body: JSON.stringify({ gravatar: ligar }) });
      definirConta({ ...(contaAtual() ?? c), ...c });
      anunciar(ligar ? "Foto do Gravatar ligada. Se você tiver uma foto lá, ela aparece em instantes." : "Foto do Gravatar desligada: fica a inicial.");
    } catch (e) {
      gravatar.checked = !ligar;
      aviso(msg, textoErro(e), "erro");
    } finally {
      gravatar.disabled = !contaAtual()?.email;
    }
  });

  return { abrir: () => undefined };
}
