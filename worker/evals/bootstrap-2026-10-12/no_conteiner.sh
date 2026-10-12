#!/usr/bin/env bash
# Roda dentro de um contêiner python:3.12-slim-bookworm (a imagem dos pods), sem GPU, com /ctx montado
# (teste_bootstrap.sh prepara). Carrega só as funções do bootstrap_pod.sh (BOOTSTRAP_SO_FUNCOES=1) e
# confere o download dos modelos e a volta da hibernação com um disco de volume falso.
#   bash no_conteiner.sh downloads   download por arquivo, espelho ruim, espelho sem o arquivo, medida
#   bash no_conteiner.sh volta       volta da hibernação: certa, volume incompleto e arquivo corrompido
set -uo pipefail
CTX=/ctx PORTA=18777
FALHAS=0
ok() { echo "OK   $*"; }
erro() { echo "ERRO $*"; FALHAS=$((FALHAS + 1)); }
confere_que() { if eval "$2"; then ok "$1"; else erro "$1 ($2)"; fi; }

# o proxy do ambiente de testes só aceita HTTPS
sed -i 's#http://deb.debian.org#https://deb.debian.org#g' /etc/apt/sources.list.d/debian.sources
apt-get update -qq >/dev/null && apt-get install -y -qq --no-install-recommends aria2 curl rsync >/dev/null \
  || { echo "apt falhou"; exit 2; }
python3 $CTX/servidor.py $CTX/arquivos $PORTA &
sleep 1
export RENDER2GO_RAIZ=/root/render2go
mkdir -p $RENDER2GO_RAIZ && cp $CTX/repo.tgz $RENDER2GO_RAIZ/repo.tgz
mkdir -p /opt/render2go && tar -xzf $RENDER2GO_RAIZ/repo.tgz -C /opt/render2go
SERV=http://127.0.0.1:$PORTA

# modelos.tsv do teste: <url da origem>\t<destino>\t<bytes>\t<sha256> (o lista_modelos de verdade aponta
# para o Hugging Face; aqui, a "origem" é o servidor local)
lista_teste() {  # <prefixo da origem>
  local f n
  for f in $CTX/arquivos/*; do
    n=$(basename "$f")
    printf '%s/%s/%s\t/opt/comfyui/models/teste/%s\t%s\t%s\n' "$SERV" "$1" "$n" "$n" \
      "$(stat -c %s "$f")" "$(sha256sum "$f" | cut -d' ' -f1)"
  done
}
espelho_teste() {  # <prefixo do espelho para m2> <prefixo para m3>: espelho.tsv
  local f n p
  for f in $CTX/arquivos/*; do
    n=$(basename "$f") p=espelho
    [[ $n == m2.* ]] && p=$1
    [[ $n == m3.* ]] && p=$2
    printf '%s\t%s/%s/%s\n' "$n" "$SERV" "$p" "$n"
  done > $R/espelho.tsv
}

caso_download() {  # <nome> <origem> <espelho de m2> <espelho de m3> <esperado: 0 ou 1>
  rm -rf /opt/comfyui/models $R/ok $R/rede && mkdir -p $R/ok && : > $R/tempos
  lista_teste "$2" > $CTX/lista.tsv
  espelho_teste "$3" "$4"
  echo 1 > $R/so_espelho
  local t0=$SECONDS rc=0
  modelos > $R/modelos-$1.log 2>&1 || rc=1
  local i=0  # a medida de rede sai em segundo plano, logo depois do fim do download
  while [[ ! -s $R/rede ]] && (( i++ < 30 )); do sleep 0.5; done
  sleep 1  # e a medida de um caso não pode cair no seguinte
  echo "     $1: rc $rc em $((SECONDS - t0)) s; $(cat $R/rede 2>/dev/null); $(tr '\n' ' ' < $R/tempos)"
  confere_que "$1: resultado esperado ($5)" "[[ $rc == $5 ]]"
  if [[ $5 == 0 ]]; then
    confere_que "$1: os 4 arquivos conferidos" "[[ \$(ls $R/ok | wc -l) == 4 ]]"
    confere_que "$1: SHA-256 de verdade em disco" \
      "cd /opt/comfyui/models/teste && (cd $CTX/arquivos && sha256sum *) | sha256sum -c --quiet -"
    if [[ $1 == tudo-do-espelho ]]; then  # nos outros, um arquivo apagado no meio deixa "rede -"
      confere_que "$1: a medida de rede saiu" "grep -qE '^rede [0-9]+$' $R/rede"
    else
      confere_que "$1: a medida de rede saiu (número ou -)" "grep -qE '^rede ([0-9]+|-)$' $R/rede"
    fi
    confere_que "$1: cada arquivo conferido logo depois do seu download" "grep -q '^conferido_m4' $R/tempos"
  fi
}

testa_downloads() {
  BOOTSTRAP_SO_FUNCOES=1 source $CTX/bootstrap_pod.sh $RENDER2GO_RAIZ/repo.tgz teste
  lista_modelos() { cat $CTX/lista.tsv; }
  caso_download "tudo-do-espelho" origem espelho espelho 0
  caso_download "espelho-corrompido" origem espelho-ruim espelho 0
  confere_que "espelho-corrompido: refeito da origem" \
    "grep -q 'baixando de novo da origem: m2' $R/modelos-espelho-corrompido.log"
  caso_download "espelho-sem-o-arquivo" origem espelho ausente 0
  caso_download "nada-confere" ausente espelho-ruim espelho 1
}

volume_falso() {  # o que guardar_local deixa no disco de volume, com os modelos do teste
  rm -rf /opt/venv /opt/comfyui /opt/ollama /usr/local/bin/ollama /usr/local/lib/ollama
  [[ -d $L ]] && find "$L" -mindepth 1 -maxdepth 1 ! -name apt -exec rm -rf {} +  # o cache do apt fica
  mkdir -p $L/venv/bin $L/comfyui/models/teste $L/ollama/usr/bin $L/ollama/usr/lib/ollama \
    $L/ollama/models/blobs
  ln -s /usr/local/bin/python3 $L/venv/bin/python
  printf '#!/bin/sh\necho ollama falso\n' > $L/ollama/usr/bin/ollama && chmod +x $L/ollama/usr/bin/ollama
  cp $CTX/arquivos/* $L/comfyui/models/teste/
  echo "blob do compilador" > /tmp/blob && cp /tmp/blob "$L/ollama/models/blobs/sha256-$(sha256sum /tmp/blob | cut -d' ' -f1)"
  echo "$VERSAO $(date +%s)" > $L/instalado
}

caso_volta() {  # <nome> <esperado: 0 ou 1>
  : > $R/tempos && echo INSTALANDO > $EST
  local rc=0
  ( retomada ) > $R/volta-$1.log 2>&1 || rc=$?
  echo "     $1: rc $rc; estado $(cat $EST); $(tr '\n' ' ' < $R/tempos)"
  confere_que "$1: resultado esperado ($2)" "[[ $rc == $2 ]]"
}

testa_volta() {
  export RENDER2GO_LOCAL=/workspace/r2g
  echo "versao-de-teste" > $RENDER2GO_RAIZ/versao_hib
  BOOTSTRAP_SO_FUNCOES=1 source $CTX/bootstrap_pod.sh $RENDER2GO_RAIZ/repo.tgz teste
  lista_modelos() { cat $CTX/lista.tsv; }
  lista_teste origem > $CTX/lista.tsv

  volume_falso
  caso_volta "volta-certa" 0
  confere_que "volta-certa: /opt aponta para o volume" "[[ \$(readlink /opt/venv) == $L/venv && \$(readlink /opt/comfyui) == $L/comfyui ]]"
  confere_que "volta-certa: dados do ComfyUI no contêiner" "[[ \$(readlink $L/comfyui/output) == /root/comfy-dados/output ]]"
  confere_que "volta-certa: PRONTO e SHA-256 conferido" "grep -q PRONTO $EST && grep -q conferido $R/conferir.log"
  confere_que "volta-certa: marcador mantido" "[[ -f $L/instalado ]]"

  volume_falso
  rm $L/comfyui/models/teste/m3.safetensors
  caso_volta "volume-incompleto" 1
  confere_que "volume-incompleto: nenhum link criado (subida do zero no contêiner)" \
    "[[ ! -e /opt/venv && ! -L /opt/venv && ! -L /opt/comfyui ]]"
  confere_que "volume-incompleto: não é FALHOU (a 1ª subida segue)" "! grep -q FALHOU $EST"

  volume_falso
  printf '\xff' | dd of=$L/comfyui/models/teste/m2.safetensors bs=1 seek=0 conv=notrunc status=none
  caso_volta "arquivo-corrompido" 0
  confere_que "arquivo-corrompido: FALHOU pelo SHA-256 (o worker para e o pod é encerrado)" \
    "grep -q 'FALHOU disco de volume com arquivo corrompido' $EST"
  confere_que "arquivo-corrompido: o marcador saiu" "[[ ! -f $L/instalado ]]"
}

case "${1:-}" in
  downloads) testa_downloads ;;
  volta) testa_volta ;;
  *) echo "uso: no_conteiner.sh downloads|volta"; exit 2 ;;
esac
echo "falhas: $FALHAS"
exit $((FALHAS > 0))
