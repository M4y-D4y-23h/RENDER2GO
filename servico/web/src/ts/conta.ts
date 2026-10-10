// A conta de quem entrou, como a API mostra (GET /api/conta, pedido já no <head>): o menu lateral, o perfil, as
// configurações e o pedido leem daqui. Uma cópia só na página; quem muda algo (perfil, saldo, e-mail) avisa os
// outros por aoMudarConta, sem pedir a conta de novo à API.
import { api, criar, reais } from "./comum";
import type { Tema } from "./tema";

export type Conta = {
  id: string;
  email: string | null;
  nome: string | null;
  plano: string;
  criado: number | null;
  saldo: number;
  teste: { usado: boolean; fotos: number; duracao: number } | null;
  pagamento: { ativo: boolean; minimo: number; maximo: number } | null;
  perfil: { primeiro_nome: string | null; ultimo_nome: string | null; gravatar: boolean; foto: string | null };
  tema: Tema;
  mfa: { ativo: boolean; desde: number | null; codigos?: number | null };
  confirmada_s: number;
  sessao: boolean;
  documentos: { atuais: Record<string, string>; pendentes: string[] };
  interesses: string[];
};

export const PLANOS: Record<string, string> = { teste: "teste gratuito", pago: "pré-pago", interno: "interno" };

let atual: Conta | null = null;
const ouvintes: ((c: Conta) => void)[] = [];
/** "Tenho interesse" que esta conta já marcou (finalidades e recursos em breve). */
export const interessadas = new Set<string>();

export function contaAtual(): Conta | null {
  return atual;
}

export function definirConta(c: Conta): void {
  atual = c;
  for (const i of c.interesses ?? []) interessadas.add(i);
  for (const o of ouvintes) o(c);
}

/** Chamado agora (se a conta já chegou) e a cada mudança. */
export function aoMudarConta(ouvinte: (c: Conta) => void): void {
  ouvintes.push(ouvinte);
  if (atual) ouvinte(atual);
}

export async function carregarConta(): Promise<Conta> {
  const c = await api<Conta>("/api/conta");
  definirConta(c);
  return c;
}

/** Muda só alguns campos (o saldo depois de um pedido, o e-mail trocado) e avisa a página. */
export function atualizarConta(campos: Partial<Conta>): void {
  if (atual) definirConta({ ...atual, ...campos });
}

export function nomeCompleto(c: Conta): string {
  return [c.perfil?.primeiro_nome, c.perfil?.ultimo_nome].filter(Boolean).join(" ");
}

/** Como a conta aparece no menu: o nome, se a pessoa deu um; senão, o e-mail. */
export function nomeExibido(c: Conta): string {
  return nomeCompleto(c) || c.email || c.nome || "Sua conta";
}

export function resumoPlano(c: Conta): string {
  if (c.plano === "pago") return `Pré-pago · ${reais(c.saldo)}`;
  if (c.plano === "teste") return c.teste?.usado ? "Teste gratuito (já usado)" : "Teste gratuito";
  return `Plano ${PLANOS[c.plano] ?? c.plano}`;
}

export function resumoConta(c: Conta): string {
  const quem = c.email ?? c.nome ?? "sua conta";
  const plano = PLANOS[c.plano] ?? c.plano;
  return c.plano === "pago" ? `Conectado como ${quem} · plano ${plano} · saldo ${reais(c.saldo)}` : `Conectado como ${quem} · plano ${plano}`;
}

/** A inicial maiúscula do nome (ou, sem nome, do e-mail), para quando não há foto. */
export function inicial(c: Conta): string {
  const base = c.perfil?.primeiro_nome || c.email || c.nome || "?";
  const letra = Array.from(base.trim()).find((ch) => /\p{L}|\p{N}/u.test(ch)) ?? "?";
  return letra.toLocaleUpperCase("pt-BR");
}

/** Foto do Gravatar (por escolha da pessoa, servida pelo próprio site) ou a inicial; tamanho fixo pelo CSS. */
export function desenharAvatar(alvo: HTMLElement, c: Conta): void {
  let letra = alvo.querySelector<HTMLElement>(".avatar-inicial");
  if (!letra) {
    letra = criar("span", { className: "avatar-inicial" });
    alvo.prepend(letra);
  }
  letra.textContent = inicial(c);
  const url = c.perfil?.foto ?? null;
  let img = alvo.querySelector<HTMLImageElement>("img");
  if (!url) {
    img?.remove();
    alvo.classList.remove("com-foto");
    return;
  }
  if (img && img.dataset.url === url) return;
  img?.remove();
  img = criar("img", { alt: "", width: 160, height: 160, decoding: "async", dados: { url } });
  // a foto entra por cima da inicial só depois de carregar (sem foto no Gravatar: a API responde sem imagem)
  img.addEventListener("load", () => alvo.classList.add("com-foto"), { once: true });
  img.addEventListener("error", () => {
    alvo.classList.remove("com-foto");
    img?.remove();
  }, { once: true });
  alvo.classList.remove("com-foto");
  alvo.append(img);
  img.src = url;
}
