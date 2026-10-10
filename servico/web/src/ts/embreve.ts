// Telas "Projetos" e "Mix" (em breve): o texto fica no HTML; aqui só o "Tenho interesse" de cada uma, que precisa
// saber o que a conta já marcou (GET /api/conta). O plano do Mix está no ADR 0010 (proposto).
import { aviso, el } from "./comum";
import { aoMudarConta } from "./conta";
import { botaoInteresse } from "./interesse";

export function iniciarEmBreve(): void {
  let feito = false;
  aoMudarConta(() => {
    if (feito) return;
    feito = true;
    el("interesse-projetos").replaceChildren(botaoInteresse("recurso:projetos", "Projetos", (t) => aviso(el("projetos-msg"), t, "erro")));
    el("interesse-mix").replaceChildren(botaoInteresse("recurso:mix", "Mix", (t) => aviso(el("mix-msg"), t, "erro")));
  });
}
