// "Não sou um robô" (Cloudflare Turnstile) nas páginas de entrada e de suporte. O desafio roda na página
// isolada /verificacao, dentro de um quadro do próprio site, e o resultado chega por postMessage (origem e
// quadro conferidos). Sem a chave pública (verificação desligada), nada é carregado.
import { anunciar, criar } from "./comum";

export type Verificacao = { token: () => string | null; renovar: () => void };

export function verificacao(alvo: HTMLElement, chave: string | null | undefined, acao: string): Verificacao | null {
  if (!chave) {
    alvo.hidden = true;
    return null;
  }
  let token: string | null = null;
  const endereco = () => `/verificacao?k=${encodeURIComponent(chave)}&a=${encodeURIComponent(acao)}`;
  const quadro = criar("iframe", { title: "Verificação de segurança (não sou um robô)", className: "quadro-verificacao", loading: "eager" });
  quadro.src = endereco();
  window.addEventListener("message", (ev: MessageEvent) => {
    if (ev.origin !== location.origin || ev.source !== quadro.contentWindow) return;
    const d = ev.data as { tipo?: string; estado?: string; token?: string } | null;
    if (!d || d.tipo !== "render2go-verificacao") return;
    token = d.estado === "ok" && typeof d.token === "string" && d.token.length <= 2048 ? d.token : null;
    if (d.estado === "ok") anunciar("Verificação de segurança concluída.");
  });
  alvo.replaceChildren(quadro);
  alvo.hidden = false;
  return {
    token: () => token,
    // cada resposta vale uma vez: depois de usada (ou recusada), um desafio novo
    renovar: () => {
      token = null;
      quadro.src = endereco();
    },
  };
}
