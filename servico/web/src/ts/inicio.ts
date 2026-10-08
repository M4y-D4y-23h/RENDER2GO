// Roda no <head>, antes de desenhar a página (sem defer, de propósito; menos de 1 KB e guardado em cache).
// 1) Quem já tem o código guardado neste navegador vê o painel desde o primeiro quadro, sem piscar o
//    formulário de entrada nem deslocar o layout (CLS). A chave vem do atributo data-chave do <script>.
// 2) Com código, já pede à API o que o painel mostra primeiro (data-antecipar), em paralelo com o CSS e
//    o JavaScript da página; o script da página usa essas respostas (comum.ts, api) ou pede de novo.
(() => {
  const script = document.currentScript as HTMLScriptElement | null;
  const chave = script?.dataset.chave;
  if (!chave) return;
  try {
    const antiga = (k: string) => k.replace("render2go-", "gpu" + "2go-"); // nome antigo (migrado no app)
    const token = localStorage.getItem(chave) || localStorage.getItem(antiga(chave));
    if (!token) return;
    document.documentElement.classList.add("com-sessao");
    // o mesmo identificador de dispositivo de comum.ts; sem ele (1º acesso), a página pede tudo depois
    const dispositivo = localStorage.getItem("render2go-dispositivo");
    const caminhos = (script?.dataset.antecipar ?? "").split(" ").filter(Boolean);
    if (!dispositivo || !caminhos.length || typeof fetch !== "function") return;
    let fuso = "";
    try {
      fuso = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
    } catch {
      /* sem fuso */
    }
    const cabecalhos = { Authorization: `Bearer ${token}`, "Content-Type": "application/json", "X-Dispositivo": dispositivo, "X-Fuso": fuso };
    const pedidos: Record<string, Promise<Response>> = {};
    for (const caminho of caminhos) {
      pedidos[caminho] = fetch(caminho, { headers: cabecalhos });
      pedidos[caminho].catch(() => undefined); // falha de rede: a página pede de novo, sem erro no console
    }
    (window as Window & { __render2go?: Record<string, Promise<Response>> }).__render2go = pedidos;
  } catch {
    /* sem armazenamento local (janela anônima restrita): mostra o formulário de entrada */
  }
})();
