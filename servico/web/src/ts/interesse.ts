// "Tenho interesse" nas opções em breve (finalidades do catálogo e recursos da área do cliente: Mix, Projetos,
// indicação, telefone, nome de usuário). Cada conta conta uma vez por opção; o painel do dono mostra o total e
// cada clique. Depois do clique, no lugar do botão fica a confirmação, que recebe o foco.
import { anunciar, api, criar, icone, ocupado, textoErro } from "./comum";
import { interessadas } from "./conta";

/** Botão (ou a confirmação, se já marcou). `aoMarcar` redesenha o que mais mostra o mesmo interesse. */
export function botaoInteresse(chave: string, titulo: string, aoErro: (texto: string) => void, aoMarcar?: () => void): HTMLElement {
  if (interessadas.has(chave)) return confirmacao();
  const b = criar("button", { type: "button", className: "botao pequeno", dados: { foco: `interesse:${chave}` } }, ["Tenho interesse"]);
  b.setAttribute("aria-label", `Tenho interesse em ${titulo}`);
  b.addEventListener("click", async () => {
    ocupado(b, true);
    try {
      await api("/api/interesses", { method: "POST", body: JSON.stringify({ finalidade: chave }) });
      interessadas.add(chave);
      anunciar(`Interesse em “${titulo}” registrado. Obrigado!`);
      const ok = confirmacao();
      b.replaceWith(ok);
      ok.focus();
      // a mesma opção em outra tela (pedidos e modelos de IA) também vira a confirmação
      for (const outro of Array.from(document.querySelectorAll<HTMLElement>("[data-foco^='interesse:']")))
        if (outro.dataset.foco === `interesse:${chave}`) outro.replaceWith(confirmacao());
      aoMarcar?.();
    } catch (e) {
      ocupado(b, false);
      aoErro(textoErro(e));
    }
  });
  return b;
}

function confirmacao(): HTMLElement {
  return criar("span", { className: "interesse-ok", tabIndex: -1 }, [icone("ok"), "Interesse registrado"]);
}
