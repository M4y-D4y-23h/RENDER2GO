// Roda no <head>, antes de desenhar a página (sem defer, de propósito; cerca de 1 KB e guardado em cache).
// 1) O tema escolhido na conta (claro ou escuro; sem escolha, o do aparelho) vale desde o primeiro quadro: o
//    <html> ganha data-tema, que o tokens.css segue (nada pisca nem muda de lugar quando a página termina).
// 2) Na área do cliente, a tela vem do endereço (/extratos, /logs...): o <html> ganha data-vista e o CSS mostra
//    só aquela tela e marca o item do menu, também antes do primeiro quadro.
// 3) Quem já entrou neste navegador vê a área da conta desde o primeiro quadro, sem piscar o formulário de entrada
//    nem deslocar o layout (CLS). A sessão fica num cookie HttpOnly, que o JavaScript não lê: aqui vale só a
//    marca "entrou" (data-chave do <script>); se a sessão tiver vencido, a página volta para a entrada.
// 4) Com a marca, já pede à API o que a tela mostra primeiro (data-antecipar: "tela=caminho,caminho ..."), em
//    paralelo com o CSS e o JavaScript da página; o script da página usa essas respostas (comum.ts, api) ou pede
//    de novo.
(() => {
  const raiz = document.documentElement;
  const script = document.currentScript as HTMLScriptElement | null;
  try {
    const tema = localStorage.getItem("render2go-tema");
    if (tema === "claro" || tema === "escuro") {
      raiz.dataset.tema = tema;
      // a barra do navegador (celular) na cor do tema escolhido, quando as <meta> já existirem
      document.addEventListener("DOMContentLoaded", () => {
        for (const m of Array.from(document.querySelectorAll('meta[name="theme-color"]')))
          m.setAttribute("content", tema === "escuro" ? "#0f0e17" : "#f7f7fb");
      });
    }
  } catch {
    /* sem armazenamento local: o tema do aparelho */
  }
  const telas: Record<string, string[]> = Object.create(null); // sem "constructor" e afins vindos do endereço
  for (const item of (script?.dataset.antecipar ?? "").split(/\s+/).filter(Boolean)) {
    const i = item.indexOf("="); // só o 1º "=": os caminhos podem ter "?dias=30"
    telas[i < 0 ? item : item.slice(0, i)] = i < 0 ? [] : item.slice(i + 1).split(",").filter(Boolean);
  }
  let tela = "";
  if (Object.keys(telas).length) {
    tela = location.pathname.replace(/^\/+|\/+$/g, "") || "pedidos";
    if (!telas[tela]) tela = "pedidos";
    raiz.dataset.vista = tela;
  }
  const chave = script?.dataset.chave;
  if (!chave) return;
  try {
    if (localStorage.getItem(chave) !== "1") return;
    raiz.classList.add("com-sessao");
    // o mesmo identificador de dispositivo de comum.ts; sem ele (1º acesso), a página pede tudo depois
    const dispositivo = localStorage.getItem("render2go-dispositivo");
    const caminhos = tela ? telas[tela] : [];
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
