// Roda no <head>, antes de desenhar a página (sem defer, de propósito; cerca de 1 KB e guardado em cache).
// 1) O tema escolhido na conta (claro ou escuro; sem escolha, o do aparelho) vale desde o primeiro quadro: o
//    <html> ganha data-tema, que o tokens.css segue (nada pisca nem muda de lugar quando a página termina).
// 2) A tela vem do endereço: na área do cliente, do caminho (/extratos, /logs...: data-vista); no painel
//    (data-secoes), do # (#pods, #acessos...: data-secao). O CSS mostra só aquela tela e marca o item do menu,
//    também antes do primeiro quadro. No painel, o convite de primeiro acesso (#convite=...) abre a entrada
//    (data-convite), mesmo com uma sessão aberta neste navegador.
// 3) Quem já entrou neste navegador vê a área da conta desde o primeiro quadro, sem piscar o formulário de entrada
//    nem deslocar o layout (CLS). A sessão fica num cookie HttpOnly, que o JavaScript não lê: aqui vale só a
//    marca "entrou" (data-chave do <script>); se a sessão tiver vencido, a página volta para a entrada. No painel,
//    o papel de quem entrou da última vez (data-papel: a chave guardada) esconde desde já o que o papel não abre.
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
  const nomes = Object.keys(telas);
  let tela = "";
  if (script?.dataset.secoes !== undefined && nomes.length) {
    const h = location.hash.slice(1);
    if (h.startsWith("convite=")) raiz.dataset.convite = "";
    else {
      tela = telas[h] ? h : nomes[0];
      raiz.dataset.secao = tela;
    }
  } else if (nomes.length) {
    tela = location.pathname.replace(/^\/+|\/+$/g, "") || "pedidos";
    if (!telas[tela]) tela = "pedidos";
    raiz.dataset.vista = tela;
  }
  const chave = script?.dataset.chave;
  if (!chave || raiz.dataset.convite !== undefined) return;
  try {
    if (localStorage.getItem(chave) !== "1") return;
    raiz.classList.add("com-sessao");
    const papel = script?.dataset.papel ? localStorage.getItem(script.dataset.papel) : null;
    if (papel === "dono" || papel === "operador") raiz.dataset.papel = papel;
    // o operador num endereço de seção só do dono (data-so-dono): a 1ª seção, sem pedir o que o papel não abre
    if (papel === "operador" && tela && (script?.dataset.soDono ?? "").split(/\s+/).includes(tela)) {
      tela = nomes[0];
      raiz.dataset.secao = tela;
    }
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
