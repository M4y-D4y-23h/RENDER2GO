// Página do cliente: a apresentação com a entrada (quem não entrou) e a área da conta (quem entrou), com o menu
// lateral e uma tela por endereço: Pedidos (/), Modelos de IA, Extratos, Logs, Perfil, Configurações, Projetos e
// Mix (em breve). A mesma página serve todos esses endereços (a API, com noindex): o inicio.js marca a tela antes
// do primeiro quadro, e cada tela pede à API só o que mostra, quando abre.
import { CHAVE_CONECTADO, ErroApi, aviso, avisoComRecarga, el, marcarConectado, textoErro } from "./comum";
import { pedirAceite } from "./confirmacao";
import { criarConfiguracoes } from "./configuracoes";
import { type Conta, carregarConta, contaAtual, definirConta } from "./conta";
import { iniciarCookies } from "./cookies";
import { iniciarEmBreve } from "./embreve";
import { iniciarEntrada } from "./entrada";
import { criarExtratos } from "./extratos";
import { criarLogs } from "./logs";
import { criarModelosIA } from "./modelosia";
import { type NomeTela, iniciarMoldura } from "./moldura";
import { criarPedidos } from "./pedidos";
import { criarPerfil } from "./perfil";
import { abrirPix } from "./pix";

function iniciarCliente(): void {
  let contaPronta: Promise<Conta> | null = null;
  const obterConta = (): Promise<Conta> => {
    if (!contaPronta) {
      const p = carregarConta();
      contaPronta = p;
      p.catch(() => {
        if (contaPronta === p) contaPronta = null; // falhou (rede): a próxima tela pede de novo
      });
    }
    return contaPronta;
  };

  // erro ao carregar uma tela: sessão vencida (ou conta desativada) volta para a entrada; o resto vira um aviso
  // com "Tentar de novo"
  const erro = (e: unknown, alvo: HTMLElement) => {
    if (e instanceof ErroApi && e.status === 401) {
      marcarConectado(CHAVE_CONECTADO, false);
      location.reload();
      return;
    }
    avisoComRecarga(alvo, `Não foi possível carregar: ${textoErro(e)}`);
  };

  const pix = () => {
    const c = contaAtual();
    if (!c?.pagamento) return;
    const eraTeste = c.plano === "teste";
    abrirPix(c.pagamento.minimo, c.pagamento.maximo, () => {
      if (eraTeste) window.setTimeout(() => location.reload(), 2500); // a 1ª recarga passa a conta para o plano pago
      else {
        void carregarConta().catch(() => undefined); // o saldo do menu e do pedido acompanham
        extratos.recarregar();
      }
    });
  };

  const pedidos = criarPedidos({ contaPronta: obterConta, pix, erro });
  const modelos = criarModelosIA({
    erro,
    usar: (id) => {
      moldura.ir("pedidos", { foco: false });
      void pedidos.usarFinalidade(id);
    },
  });
  const extratos = criarExtratos({ pix, erro });
  const logs = criarLogs({ erro });
  const perfil = criarPerfil();
  const configuracoes = criarConfiguracoes({ erro });
  iniciarEmBreve();

  const telas: Record<NomeTela, () => unknown> = {
    pedidos: () => pedidos.abrir(),
    modelos: () => modelos.abrir(),
    extratos: () => extratos.abrir(),
    logs: () => logs.abrir(),
    perfil: () => perfil.abrir(),
    configuracoes: () => configuracoes.abrir(),
    projetos: () => undefined,
    mix: () => undefined,
  };
  let naConta = false;
  const moldura = iniciarMoldura((tela) => {
    if (naConta) void telas[tela]();
  });

  iniciarEntrada(async ({ foco, conta, aviso: texto }) => {
    if (conta) {
      // a resposta da entrada já traz a conta: nada de pedir de novo
      definirConta(conta);
      contaPronta = Promise.resolve(conta);
    }
    const c = await obterConta(); // 401: a entrada volta a aparecer
    naConta = true;
    moldura.entrar(foco);
    if (texto) aviso(el("aviso-conta"), texto, "ok");
    if (c.documentos?.pendentes?.length) void pedirAceite();
  });
}

document.addEventListener("DOMContentLoaded", () => {
  iniciarCookies();
  iniciarCliente();
});
