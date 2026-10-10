// "Sua conta" na página do cliente: e-mail, plano e saldo; recarga por Pix; cópia dos dados (LGPD); sair
// deste aparelho ou de todos; excluir a conta (com saldo, o reembolso vem antes, pelo suporte).
import { CHAVE_CONECTADO, anunciar, api, aviso, baixarJson, confirmar, criar, dialogo, el, icone, marcarConectado, ocupado, reais, textoErro } from "./comum";

export type Conta = {
  id: string;
  email: string | null;
  nome: string | null;
  plano: string;
  criado: number | null;
  saldo: number;
  teste: { usado: boolean; fotos: number; duracao: number } | null;
  pagamento: { ativo: boolean; minimo: number; maximo: number } | null;
};

const PLANOS: Record<string, string> = { teste: "teste gratuito", pago: "pré-pago", interno: "interno" };

export function resumoConta(c: Conta): string {
  const quem = c.email ?? c.nome ?? "sua conta";
  const plano = PLANOS[c.plano] ?? c.plano;
  return c.plano === "pago" ? `Conectado como ${quem} · plano ${plano} · saldo ${reais(c.saldo)}` : `Conectado como ${quem} · plano ${plano}`;
}

export function abrirConta(c: Conta, abrirPix: () => void): void {
  const origem = document.activeElement as HTMLElement | null;
  const { d, corpo } = dialogo("Sua conta", "conta");
  const msg = criar("div", { className: "aviso", hidden: true });
  const linhas: HTMLElement[] = [
    criar("p", {}, [criar("strong", { textContent: "E-mail: " }), c.email ?? "não informado (conta com código de acesso)"]),
    criar("p", {}, [criar("strong", { textContent: "Plano: " }), PLANOS[c.plano] ?? c.plano]),
  ];
  if (c.plano === "pago") linhas.push(criar("p", {}, [criar("strong", { textContent: "Saldo: " }), reais(c.saldo)]));
  if (c.teste) linhas.push(criar("p", { className: "fraco", textContent: c.teste.usado ? "Teste gratuito: já usado." : `Teste gratuito disponível: até ${c.teste.fotos} fotos, vídeos de ${c.teste.duracao} s em 480p.` }));

  const acoes: HTMLElement[] = [];
  if (c.pagamento?.ativo && c.plano !== "interno") {
    const pix = criar("button", { type: "button", className: "botao primario" }, [icone("brilho"), "Adicionar saldo por Pix"]);
    pix.addEventListener("click", () => {
      d.close();
      abrirPix();
    });
    acoes.push(pix);
  }
  const dados = criar("button", { type: "button", className: "botao" }, [icone("baixar"), "Baixar meus dados"]);
  dados.addEventListener("click", async () => {
    ocupado(dados, true);
    try {
      baixarJson(await api<unknown>("/api/conta/dados"), `render2go-meus-dados.json`);
      anunciar("Seus dados foram baixados.");
    } catch (e) {
      aviso(msg, textoErro(e), "erro");
    } finally {
      ocupado(dados, false);
    }
  });
  const todos = criar("button", { type: "button", className: "botao" }, [icone("sair"), "Sair de todos os aparelhos"]);
  todos.addEventListener("click", async () => {
    const ok = await confirmar({ titulo: "Sair de todos os aparelhos?", texto: "Todas as sessões desta conta terminam agora, inclusive esta. Para voltar, entre de novo com o código do e-mail.", confirmar: "Sair de todos" });
    if (!ok) return;
    try {
      await api("/api/conta/sair-de-todos", { method: "POST" });
      marcarConectado(CHAVE_CONECTADO, false);
      location.reload();
    } catch (e) {
      aviso(msg, textoErro(e), "erro");
    }
  });
  const excluir = criar("button", { type: "button", className: "botao perigo" }, [icone("lixo"), "Excluir minha conta"]);
  excluir.addEventListener("click", async () => {
    const ok = await confirmar({
      titulo: "Excluir a conta?",
      texto: "A conta e o e-mail saem do serviço e você deixa de conseguir entrar. Pedidos em andamento precisam terminar antes. Com saldo, peça o reembolso pelo suporte primeiro. O extrato e os registros que a lei manda guardar ficam pelo prazo legal.",
      confirmar: "Excluir a conta",
      perigo: true,
    });
    if (!ok) return;
    ocupado(excluir, true);
    try {
      await api("/api/conta/excluir", { method: "POST", body: JSON.stringify({ confirmacao: "EXCLUIR" }) });
      marcarConectado(CHAVE_CONECTADO, false);
      location.reload();
    } catch (e) {
      aviso(msg, textoErro(e), "erro");
      ocupado(excluir, false);
    }
  });
  acoes.push(dados, todos, excluir);
  corpo.append(...linhas, criar("div", { className: "acoes" }, acoes), msg, criar("p", { className: "dica" }, ["Seus direitos sobre os dados pessoais estão na ", criar("a", { href: "/privacidade", textContent: "Política de Privacidade" }), "."]));
  d.addEventListener("close", () => {
    d.remove();
    (origem ?? el("titulo-painel")).focus();
  });
  d.showModal();
}
