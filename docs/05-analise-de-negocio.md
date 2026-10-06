# 05 — Análise de negócio: adulto × SFW, e a decisão

- **Data:** 06/10/2026
- **Decisão do dono do projeto:** **caminho A, serviço de marketing com IA para pequenos negócios** (seção 5). O produto adulto e o app SFW próprio ficam em pausa.
- Não é parecer jurídico nem financeiro. Os custos vêm das medições do projeto; preços de mercado e de concorrentes são estimativas, não conferidas em fonte.

## 1. Os dois caminhos lado a lado

| | Adulto | SFW |
|---|---|---|
| Custo de GPU por vídeo de 5 s | ~R$ 0,10 a 0,15, mais moderação obrigatória | ~R$ 0,10 a 0,15 |
| Para começar a vender | Meses e alguns milhares de reais (advogado, pagamentos especializados, moderação, verificação de idade, provedor que aceite) | Dias, vendendo o serviço |
| Risco principal | Conta banida, pagamento bloqueado, responsabilidade legal | Concorrência e pouca diferenciação técnica |
| Divulgação | Sem anúncios comuns (Google e Meta proíbem); só afiliados e redes do setor | Redes sociais, anúncios, abordagem direta, parcerias |
| Dinheiro rápido? | Não | Sim, como serviço |

## 2. Custos

**Comuns** (medidos na Etapa 3, `worker/evals/custos.md`):
- GPU: ~US$ 0,02 por vídeo de 5 s com o worker já ligado (RTX 4090, serverless, US$ 1,10/h).
- Fixos iniciais: R$ 50 a 200 por mês (armazenamento, domínio, backend simples).

**Só no adulto:**
- advogado;
- verificação de idade (por usuário);
- moderação de cada vídeo (CSAM, idade aparente, pessoas reais);
- processador de pagamento especializado (taxas bem acima das comuns);
- provedor de GPU que aceite o conteúdo por escrito (doc 01, itens 1 e 2).

## 3. Riscos

**Adulto:**
- Os termos do RunPod proíbem o conteúdo, e os outros provedores que pesquisei também.
- Pagamentos bloqueados ou retidos.
- Deepfake de pessoas reais e menores: o compilador não recusa de forma confiável (`worker/evals/compiler/RESULTADOS.md`).
- Divulgação difícil.

**SFW:**
- ChatGPT, Gemini e os serviços grandes de vídeo com IA entregam, em geral, mais qualidade que o pipeline atual, muitas vezes dentro de assinaturas que a pessoa já tem ou com cota gratuita.
- O pipeline atual entrega 640p, 5 s, sem áudio, com movimento limitado, e no I2V a imagem domina (`worker/evals/gpu-2026-10-05/LEIAME.md`).

## 4. A pergunta franca: empresas escolheriam o GPU2GO?

**Como ferramenta, na maioria dos casos, não.** Para uma empresa que quer um vídeo bonito, as ferramentas das grandes empresas são mais simples e melhores.

Onde o projeto ganha:
1. **Custo em volume:** ~R$ 0,10 por vídeo, sem cota. Conta para quem gera centenas de vídeos por mês, como catálogo de produtos ou imóveis.
2. **Controle e automação:** lotes, API própria, integração com outros sistemas.
3. **Sem as restrições de conteúdo das grandes empresas.** Esse era o motivo original do projeto, e é exatamente o caminho bloqueado hoje.

**O que a empresa compra é o resultado:** divulgação pronta, com roteiro, identidade visual, legenda, música, edição e constância de postagens. O gerador de vídeo é a parte mais fácil e mais barata.

## 5. Decisão: serviço de marketing com IA (caminho A)

- **O que se vende:** pacotes de vídeos e posts de divulgação para pequenos negócios (lojas, restaurantes, imobiliárias, estética), entregues prontos para postar.
- **Ferramentas:** a melhor para cada caso, inclusive ChatGPT ou Gemini. O GPU2GO é a ferramenta interna de volume e custo baixo, com receitas por nicho, geração em lote e acabamento (logo, música, cartão final). Ver `worker/README.md`, seção "Modo serviço".
- **Preço sugerido** (validar com os primeiros clientes):

| Pacote | Conteúdo | Preço |
|---|---|---|
| Avulso | 1 vídeo curto pronto (com logo e música) | R$ 25 a 50 |
| Mensal básico | 8 vídeos/mês | R$ 149 a 249 |
| Mensal completo | 15 a 20 vídeos/mês + legendas dos posts | R$ 299 a 499 |

  O custo de GPU fica abaixo de 1% do preço. O custo real é o seu tempo.
- **Divulgação e venda:**
  - portfólio no Instagram e no TikTok com antes e depois (foto → vídeo pronto);
  - abordagem direta pelo WhatsApp a negócios do bairro e da cidade, com um vídeo de amostra feito com a foto do próprio negócio;
  - parcerias com social medias e agências, que revendem os vídeos.
- **O que fica em pausa:** Etapas 4 a 6 como app público (contas, créditos, pagamentos, PWA). Só voltam se o volume do serviço justificar automatizar o atendimento.

## 6. Vender o projeto inteiro agora? Não

- Comprador paga por receita e usuários. Sem clientes, o projeto vale perto do custo de refazer, e boa parte do código usa componentes abertos.
- A versão adulta derruba o preço: o comprador herda o problema dos termos e o risco legal.
- Anunciar a venda cedo passa a imagem de projeto abandonado.

Formas mais rápidas de transformar isto em dinheiro:
- vender o serviço já;
- pré-vender pacotes com desconto aos primeiros clientes;
- oferecer automação com IA para agências, usando o projeto como portfólio.

O projeto vale mais tarde, se tiver receita recorrente.

## 7. Próximos 30 dias

1. **Semana 1:**
   - escolher 1 ou 2 nichos;
   - testar as receitas numa GPU (pod com desligamento automático, ~US$ 1 a 2) e descartar as que não ficarem boas;
   - montar 10 a 20 exemplos e a página ou perfil com Pix e WhatsApp.
2. **Semana 2:** abordar 30 a 50 negócios e fechar os primeiros pacotes.
3. **Semanas 3 e 4:** entregar com a ferramenta de lote e de acabamento, e medir o tempo gasto por cliente. Decidir o que automatizar a seguir pelo que mais tomar tempo.
