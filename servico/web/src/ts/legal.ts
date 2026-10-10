// Páginas legais (/termos, /privacidade e /cookies) e a página 404: o aviso e as preferências de cookies, o
// topo de quem entrou ("Sair") e, nos textos longos, o índice que marca a seção sendo lida (aria-current).
import { iniciarCookies } from "./cookies";
import { iniciarTopo } from "./topo";

/** Índice "Nesta página" (fixo ao lado do texto no computador): marca a seção na faixa de leitura da tela. */
function marcarIndice(): void {
  const links = Array.from(document.querySelectorAll<HTMLAnchorElement>(".indice a[href^='#']"));
  if (!links.length || typeof IntersectionObserver !== "function") return;
  const alvos = links
    .map((a) => [a, document.getElementById(decodeURIComponent(a.hash.slice(1)))] as const)
    .filter((par): par is readonly [HTMLAnchorElement, HTMLElement] => par[1] !== null);
  if (!alvos.length) return;
  const visiveis = new Set<Element>();
  const marcar = (link: HTMLAnchorElement) => {
    for (const [a] of alvos) {
      if (a === link) a.setAttribute("aria-current", "location");
      else a.removeAttribute("aria-current");
    }
  };
  const atualizar = () => {
    // no fim da página, a última seção (curta demais para chegar à faixa de leitura)
    if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4) return marcar(alvos[alvos.length - 1][0]);
    const atual = alvos.find(([, s]) => visiveis.has(s));
    if (atual) marcar(atual[0]); // entre duas seções, fica a marcação anterior
  };
  const io = new IntersectionObserver(
    (entradas) => {
      for (const e of entradas) {
        if (e.isIntersecting) visiveis.add(e.target);
        else visiveis.delete(e.target);
      }
      atualizar();
    },
    { rootMargin: "-15% 0px -70% 0px" },
  );
  for (const [, s] of alvos) io.observe(s);
  window.addEventListener("scroll", atualizar, { passive: true });
}

document.addEventListener("DOMContentLoaded", () => {
  iniciarCookies();
  iniciarTopo();
  marcarIndice();
});
