// Player dos vídeos (pedidos e logs): um <dialog> só, reaproveitado; "Abrir em outra aba" e "Baixar" ficam junto
// do vídeo. Os links são assinados pela API e valem 24 h; só https (urlSegura).
import { criar, dialogo, icone, urlSegura } from "./comum";

export type VideoLink = { arquivo: string; url?: string; baixar?: string };

let player: { d: HTMLDialogElement; video: HTMLVideoElement; abrir: HTMLAnchorElement; baixar: HTMLAnchorElement; titulo: HTMLElement } | null = null;
let origem: HTMLElement | null = null;

export function abrirVideo(v: VideoLink, quem: HTMLElement): void {
  const url = urlSegura(v.url);
  if (!url) return;
  if (!player) {
    const { d, corpo, titulo } = dialogo("Vídeo", "player");
    const video = criar("video", { controls: true, playsInline: true, loop: true, preload: "auto" });
    const abrir = criar("a", { className: "botao", target: "_blank", rel: "noopener noreferrer" }, [icone("abrir"), "Abrir em outra aba"]);
    const baixar = criar("a", { className: "botao primario baixar", rel: "noopener" }, [icone("baixar"), "Baixar"]);
    corpo.append(video, criar("div", { className: "dialogo-acoes" }, [abrir, baixar]));
    d.addEventListener("close", () => {
      video.pause();
      video.removeAttribute("src");
      video.load();
      if (origem?.isConnected) origem.focus();
    });
    player = { d, video, abrir, baixar, titulo };
  }
  origem = quem;
  player.titulo.textContent = v.arquivo;
  player.video.src = url;
  player.abrir.href = url;
  const baixar = urlSegura(v.baixar);
  player.baixar.hidden = !baixar;
  if (baixar) player.baixar.href = baixar;
  player.d.showModal();
  player.video.play().catch(() => undefined); // sem autoplay permitido: o controle de play fica à mão
}
