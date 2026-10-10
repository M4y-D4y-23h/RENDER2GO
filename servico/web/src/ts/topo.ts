// Topo das páginas fora da inicial (/suporte, páginas legais e 404): quem entrou vê "Pedidos" (pelo CSS,
// com a marca que o inicio.js põe no <html>) e o botão "Sair", que encerra a sessão e volta para o início.
// A página inicial tem o próprio "Sair" (entrada.ts), que também confere a sessão com a API.
import { CHAVE_CONECTADO, api, conectado, marcarConectado, ocupado } from "./comum";

export function iniciarTopo(): void {
  const sair = document.getElementById("sair") as HTMLButtonElement | null;
  if (!sair || !conectado(CHAVE_CONECTADO)) return;
  sair.hidden = false;
  sair.addEventListener("click", async () => {
    ocupado(sair, true);
    await api("/api/conta/sair", { method: "POST" }).catch(() => undefined);
    marcarConectado(CHAVE_CONECTADO, false);
    location.assign("/");
  });
}

/** A sessão venceu (a API respondeu 401): a página volta a mostrar o topo de quem não entrou. */
export function semSessao(): void {
  marcarConectado(CHAVE_CONECTADO, false);
  document.documentElement.classList.remove("com-sessao");
  const sair = document.getElementById("sair");
  if (sair) sair.hidden = true;
}
