// Tema das páginas (claro, escuro ou o do aparelho). O inicio.js aplica o guardado neste navegador antes do
// primeiro quadro; a conta guarda a escolha (POST /api/conta/preferencias) e a página acerta pelo /api/conta,
// para valer em todos os aparelhos da pessoa. Cores só por token (tokens.css), nos dois temas.
export type Tema = "claro" | "escuro" | "auto";

export const CHAVE_TEMA = "render2go-tema";
const COR_BARRA: Record<string, string> = { claro: "#f7f7fb", escuro: "#0f0e17" };

export function temaAtual(): Tema {
  const t = document.documentElement.dataset.tema;
  return t === "claro" || t === "escuro" ? t : "auto";
}

/** Aplica na hora (sem recarregar) e guarda neste navegador. */
export function aplicarTema(tema: Tema): void {
  const raiz = document.documentElement;
  if (tema === "auto") delete raiz.dataset.tema;
  else raiz.dataset.tema = tema;
  try {
    if (tema === "auto") localStorage.removeItem(CHAVE_TEMA);
    else localStorage.setItem(CHAVE_TEMA, tema);
  } catch {
    /* sem armazenamento local: vale até fechar a página (e a conta guarda a escolha) */
  }
  // a barra do navegador no celular: a cor do tema escolhido, ou a de cada tema do aparelho
  for (const m of Array.from(document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]'))) {
    const doAparelho = (m.media || "").includes("dark") ? COR_BARRA.escuro : COR_BARRA.claro;
    m.content = tema === "auto" ? doAparelho : COR_BARRA[tema];
  }
}
