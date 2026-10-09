// Roda no <head>, antes de desenhar a página (sem defer, de propósito; menos de 1 KB e guardado em cache).
// 1) Quem já entrou neste navegador vê o painel desde o primeiro quadro, sem piscar o formulário de entrada
//    nem deslocar o layout (CLS). A sessão fica num cookie HttpOnly, que o JavaScript não lê: aqui vale só a
//    marca "entrou" (data-chave do <script>); se a sessão tiver vencido, a página volta para a entrada.
// 2) Com a marca, já pede à API o que o painel mostra primeiro (data-antecipar), em paralelo com o CSS e
//    o JavaScript da página; o script da página usa essas respostas (comum.ts, api) ou pede de novo.
(() => {
  const script = document.currentScript as HTMLScriptElement | null;
  const chave = script?.dataset.chave;
  if (!chave) return;
  try {
    if (localStorage.getItem(chave) !== "1") return;
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
    const cabecalhos = { "Content-Type": "application/json", "X-Render2Go": "1", "X-Dispositivo": dispositivo, "X-Fuso": fuso };
    const pedidos: Record<string, Promise<Response>> = {};
    for (const caminho of caminhos) {
      pedidos[caminho] = fetch(caminho, { headers: cabecalhos, credentials: "same-origin" });
      pedidos[caminho].catch(() => undefined); // falha de rede: a página pede de novo, sem erro no console
    }
    (window as Window & { __render2go?: Record<string, Promise<Response>> }).__render2go = pedidos;
  } catch {
    /* sem armazenamento local (janela anônima restrita): mostra o formulário de entrada */
  }
})();
