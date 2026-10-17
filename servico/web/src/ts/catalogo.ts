// O catálogo da conta (GET /api/receitas): estilos, plano, finalidades e modelos de IA (render2go_servico/modelos.py).
// Pedidos e "Modelos de IA" usam a mesma resposta, pedida uma vez só (no <head>, quando a tela abre por eles).
import { api, ehIcone, icone } from "./comum";
import { interessadas } from "./conta";

export type Receita = { id: string; label: string; nicho: string; acao: string; exemplo?: string; duracao?: number; exige_consentimento: boolean };
// plano do cliente: durações aceitas; no teste gratuito, os limites que a API impõe (a página só os mostra);
// no pré-pago, o preço por segundo de cada qualidade (centavos) e o saldo
export type Plano = {
  nome: string;
  qualidades?: string[];
  duracoes?: number[];
  max_fotos?: number;
  disponivel?: boolean;
  motivo?: string | null;
  precos?: Record<string, number>;
  saldo?: number;
};
export type Modelo = { id: string; nome: string; descricao: string; disponivel: boolean; finalidade?: string; melhor_para?: string; licenca?: string };
export type Previsto = { nome: string; melhor_para?: string; licenca?: string };
// o que o cliente quer criar (finalidade), com os modelos que a atendem
export type Finalidade = {
  id: string;
  titulo: string;
  tipo: string;
  descricao: string;
  entrada: string;
  icone: string;
  destaque: boolean;
  disponivel: boolean;
  modelos: Modelo[];
  previstos: Previsto[];
};
export type Receitas = {
  plano?: Plano;
  pagamento?: boolean;
  conta?: { email: string | null; plano: string };
  autorizacao?: { versao: string; texto: string };
  modelos?: Modelo[];
  modelo_padrao?: string;
  finalidades?: Finalidade[];
  entradas?: Record<string, string>;
  finalidade_padrao?: string;
  interesses?: string[];
  receitas: Receita[];
  formatos: string[];
  // tamanho do vídeo (largura, altura) por formato e qualidade, a regra do formato automático
  // (largura/altura abaixo da 1ª = vertical, acima da 2ª = horizontal) e a ampliação que vira aviso
  saida?: Record<string, Record<string, [number, number]>>;
  proporcao_auto?: [number, number];
  ampliacao_aviso?: number;
  qualidades: string[];
  limites: { fotos: number | null; por_envio?: number; bytes_foto: number; tipos: string[] };
  // ADR 0018: o modo "revisar antes de gerar" (desligável no servidor), os usos e o "Não pode acontecer"
  revisao_cliente?: boolean;
  usos?: { id: string; texto: string }[];
  restricoes?: { id: string; texto: string }[];
};

let pedido: Promise<Receitas> | null = null;

/** O catálogo (uma vez por página; `novo` pede de novo, ex.: o teste gratuito depois de usado). */
export function obterReceitas(novo = false): Promise<Receitas> {
  if (!pedido || novo) {
    const p = api<Receitas>("/api/receitas").then((r) => {
      for (const i of r.interesses ?? []) interessadas.add(i);
      return r;
    });
    pedido = p;
    p.catch(() => {
      if (pedido === p) pedido = null; // falhou: a próxima tela pede de novo
    });
  }
  return pedido;
}

export function iconeDe(f: Finalidade): SVGSVGElement {
  return icone(ehIcone(f.icone) ? f.icone : "brilho");
}
