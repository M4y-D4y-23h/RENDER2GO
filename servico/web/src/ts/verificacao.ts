// Página /verificacao (dentro de um quadro): mostra o "não sou um robô" (Cloudflare Turnstile) e manda o
// resultado para a página que a abriu, sempre do mesmo site (postMessage com a origem conferida).
// A chave pública e a ação vêm do endereço (?k=...&a=...), conferidas por formato.
type Turnstile = { render: (alvo: string, opcoes: Record<string, unknown>) => string };

(() => {
  const p = new URLSearchParams(location.search);
  const chave = p.get("k") ?? "";
  const acao = p.get("a") ?? "";
  const avisar = (msg: Record<string, string>) => {
    if (window.parent !== window) window.parent.postMessage({ tipo: "render2go-verificacao", ...msg }, location.origin);
  };
  const erro = (texto: string) => {
    const m = document.getElementById("msg-verificacao");
    if (m) {
      m.textContent = texto;
      m.hidden = false;
    }
    avisar({ estado: "erro" });
  };
  (window as Window & { render2goVerificacao?: () => void }).render2goVerificacao = () => {
    const t = (window as Window & { turnstile?: Turnstile }).turnstile;
    if (!t || !/^[0-9A-Za-z_-]{10,100}$/.test(chave) || !/^[a-z-]{1,32}$/.test(acao)) {
      erro("Não foi possível carregar a verificação. Recarregue a página.");
      return;
    }
    t.render("#widget", {
      sitekey: chave,
      action: acao,
      language: "pt-br",
      theme: "auto",
      size: "flexible",
      callback: (token: string) => avisar({ estado: "ok", token }),
      "expired-callback": () => avisar({ estado: "expirado" }),
      "timeout-callback": () => avisar({ estado: "expirado" }),
      "error-callback": () => {
        erro("A verificação falhou. Recarregue a página e tente de novo.");
        return true;
      },
    });
  };
})();
