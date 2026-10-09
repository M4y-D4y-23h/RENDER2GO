// Recarga do saldo por Pix (ADR 0008). O valor e o CPF/CNPJ de quem paga vão para a API, que cria o Pix no
// Mercado Pago e devolve o QR Code e o "copia e cola". A página pergunta a situação a cada 5 s enquanto a
// janela está aberta; o saldo só muda quando o Mercado Pago confirma (a API confere direto com ele).
import { ErroApi, anunciar, api, aviso, criar, dialogo, el, icone, marcarErro, ocupado, reais, textoErro } from "./comum";

type Recarga = { id: string; valor: number; status: string; expira: number | null; creditado_em: number | null; copia_cola: string | null; qr_base64: string | null };

const PRESETS = [2000, 5000, 10000, 20000];
const SITUACAO: Record<string, string> = {
  pendente: "Aguardando o pagamento…",
  criando: "Gerando o Pix…",
  aprovado: "Pagamento confirmado.",
  expirado: "Este Pix venceu sem pagamento.",
  cancelado: "Este Pix foi cancelado.",
  recusado: "O pagamento foi recusado.",
  erro: "Não foi possível gerar o Pix.",
};

function digitos(texto: string): string {
  return texto.replace(/\D/g, "");
}

/** CPF ou CNPJ com os dígitos verificadores certos (a API confere de novo). */
export function documentoValido(texto: string): boolean {
  const d = digitos(texto);
  const dv = (base: string, pesos: number[]) => {
    const r = base.split("").reduce((s, n, i) => s + Number(n) * pesos[i], 0) % 11;
    return r < 2 ? 0 : 11 - r;
  };
  if (/^(\d)\1*$/.test(d)) return false;
  if (d.length === 11) return dv(d.slice(0, 9), [10, 9, 8, 7, 6, 5, 4, 3, 2]) === Number(d[9]) && dv(d.slice(0, 10), [11, 10, 9, 8, 7, 6, 5, 4, 3, 2]) === Number(d[10]);
  if (d.length === 14) {
    const p = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2];
    return dv(d.slice(0, 12), p) === Number(d[12]) && dv(d.slice(0, 13), [6, ...p]) === Number(d[13]);
  }
  return false;
}

/** "19,90" ou "50" → centavos; null fora do formato. */
function centavos(texto: string): number | null {
  const t = texto.trim().replace(/\s/g, "").replace(/^R\$/i, "");
  if (!/^\d{1,7}([.,]\d{1,2})?$/.test(t)) return null;
  return Math.round(Number(t.replace(",", ".")) * 100);
}

export function abrirPix(minimo: number, maximo: number, aoCreditar: () => void): void {
  const origem = document.activeElement as HTMLElement | null;
  const { d, corpo } = dialogo("Adicionar saldo por Pix", "pix");
  const msg = criar("div", { className: "aviso", hidden: true });
  let escolhido = PRESETS[1];
  let timer = 0;

  // -- 1) valor e documento -------------------------------------------------------------------------
  const valores = criar("div", { className: "filtros", attrs: { role: "group", "aria-label": "Valor da recarga" } });
  const outro = criar("input", { id: "pix-outro", inputMode: "decimal", maxLength: 12, placeholder: "Outro valor", autocomplete: "off" });
  outro.setAttribute("aria-label", "Outro valor em reais");
  const marcar = (v: number | null) => {
    for (const b of Array.from(valores.querySelectorAll<HTMLButtonElement>("button"))) b.setAttribute("aria-pressed", String(Number(b.dataset.valor) === v));
  };
  for (const v of PRESETS.filter((x) => x >= minimo && x <= maximo)) {
    const b = criar("button", { type: "button", textContent: reais(v), dados: { valor: String(v) } });
    b.addEventListener("click", () => {
      escolhido = v;
      outro.value = "";
      marcar(v);
    });
    valores.append(b);
  }
  marcar(escolhido);
  outro.addEventListener("input", () => {
    const v = centavos(outro.value);
    marcar(null);
    if (v !== null) escolhido = v;
    marcarErro(outro, false);
  });
  const doc = criar("input", { id: "pix-documento", inputMode: "numeric", maxLength: 18, autocomplete: "off", spellcheck: false });
  doc.setAttribute("aria-describedby", "pix-documento-dica");
  doc.addEventListener("input", () => marcarErro(doc, false));
  const gerar = criar("button", { type: "submit", className: "botao primario largo" }, [icone("brilho"), "Gerar o Pix"]);
  const form = criar("form", { noValidate: true, className: "pilha" }, [
    criar("fieldset", { className: "passo" }, [criar("legend", { textContent: "Valor" }), valores, outro]),
    criar("div", { className: "campo" }, [
      criar("label", { htmlFor: "pix-documento", textContent: "CPF ou CNPJ de quem vai pagar" }),
      doc,
      criar("p", {
        id: "pix-documento-dica",
        className: "dica",
        textContent: "O Mercado Pago exige o documento no Pix. Guardamos só os 4 últimos dígitos, para o seu extrato.",
      }),
    ]),
    criar("p", { className: "dica", textContent: `De ${reais(minimo)} a ${reais(maximo)}. O saldo entra assim que o banco confirma, e vale para qualquer pedido.` }),
    gerar,
  ]);

  // -- 2) o Pix gerado -------------------------------------------------------------------------------
  const area = criar("div", { className: "pix-gerado", hidden: true });
  const situacao = criar("p", { className: "situacao-pix", tabIndex: -1, attrs: { role: "status" } });

  const mostrar = (r: Recarga) => {
    window.clearTimeout(timer);
    situacao.textContent = SITUACAO[r.status] ?? r.status;
    if (r.status === "aprovado") {
      area.replaceChildren(criar("p", { className: "aviso ok", textContent: `Pronto! ${reais(r.valor)} entraram no seu saldo.` }));
      anunciar(`Pagamento confirmado: ${reais(r.valor)} no seu saldo.`);
      aoCreditar();
      return;
    }
    if (r.status !== "pendente" && r.status !== "criando") {
      const denovo = criar("button", { type: "button", className: "botao" }, ["Gerar outro Pix"]);
      denovo.addEventListener("click", () => {
        area.hidden = true;
        form.hidden = false;
        gerar.focus();
      });
      area.replaceChildren(situacao, denovo);
      return;
    }
    timer = window.setTimeout(() => void consultar(r.id), 5000);
  };

  const consultar = async (id: string) => {
    if (!d.open) return;
    try {
      mostrar(await api<Recarga>(`/api/pagamentos/${encodeURIComponent(id)}`));
    } catch {
      timer = window.setTimeout(() => void consultar(id), 8000); // rede instável: tenta de novo
    }
  };

  const desenharPix = (r: Recarga) => {
    const itens: HTMLElement[] = [criar("p", {}, [criar("strong", { textContent: `Pix de ${reais(r.valor)}` }), r.expira ? ` · vale até as ${new Date(r.expira * 1000).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}` : ""])];
    if (r.qr_base64 && /^[A-Za-z0-9+/=]+$/.test(r.qr_base64)) {
      const bin = atob(r.qr_base64);
      const url = URL.createObjectURL(new Blob([Uint8Array.from(bin, (ch) => ch.charCodeAt(0))], { type: "image/png" }));
      const img = criar("img", { src: url, alt: "QR Code do Pix: abra o app do seu banco e escaneie", width: 220, height: 220, className: "qr-pix" });
      d.addEventListener("close", () => URL.revokeObjectURL(url), { once: true });
      itens.push(img);
    }
    if (r.copia_cola) {
      const campo = criar("input", { id: "pix-copia", readOnly: true, value: r.copia_cola, spellcheck: false });
      const copiar = criar("button", { type: "button", className: "botao pequeno" }, [icone("copiar"), "Copiar"]);
      copiar.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(r.copia_cola ?? "");
          copiar.replaceChildren(icone("ok"), "Copiado");
          anunciar("Código Pix copiado.");
        } catch {
          campo.select(); // sem permissão: deixa selecionado para Ctrl+C
        }
      });
      itens.push(
        criar("div", { className: "campo" }, [
          criar("label", { htmlFor: "pix-copia", textContent: "Pix copia e cola" }),
          criar("div", { className: "codigo-gerado" }, [campo, copiar]),
        ]),
      );
    }
    itens.push(situacao, criar("p", { className: "dica", textContent: "Pode deixar esta janela aberta: quando o pagamento for confirmado, o saldo aparece aqui. Se fechar, ele entra do mesmo jeito." }));
    area.replaceChildren(...itens);
    area.hidden = false;
    form.hidden = true;
    situacao.focus();
  };

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const valor = outro.value.trim() ? centavos(outro.value) : escolhido;
    if (valor === null || valor < minimo || valor > maximo) {
      marcarErro(outro, true);
      aviso(msg, `Escolha um valor de ${reais(minimo)} a ${reais(maximo)}.`, "erro");
      outro.focus();
      return;
    }
    if (!documentoValido(doc.value)) {
      marcarErro(doc, true);
      aviso(msg, "Confira o CPF ou o CNPJ: os números não conferem.", "erro");
      doc.focus();
      return;
    }
    aviso(msg, "");
    ocupado(gerar, true);
    try {
      const r = await api<Recarga>("/api/pagamentos/pix", { method: "POST", body: JSON.stringify({ valor: (valor / 100).toFixed(2), documento: digitos(doc.value) }) });
      desenharPix(r);
      mostrar(r);
    } catch (e) {
      aviso(msg, textoErro(e), "erro");
      anunciar(textoErro(e), true);
      if (e instanceof ErroApi && e.status === 422) doc.focus();
    } finally {
      ocupado(gerar, false);
    }
  });

  corpo.append(form, area, msg);
  d.addEventListener("close", () => {
    window.clearTimeout(timer);
    d.remove();
    (origem ?? el("titulo-painel")).focus();
  });
  d.showModal();
  (valores.querySelector<HTMLButtonElement>("[aria-pressed=true]") ?? outro).focus();
}
