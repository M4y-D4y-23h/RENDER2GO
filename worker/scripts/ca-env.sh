# Carregado com "." nos RUN que acessam a rede. Se uma CA extra for passada como secret de
# build (proxy corporativo ou de sandbox que reassina TLS), passa a confiar nela.
# A CA não fica na imagem:  docker build --secret id=extra_ca,src=/caminho/ca.pem ...
if [ -s /run/secrets/extra_ca ]; then
  cat /etc/ssl/certs/ca-certificates.crt /run/secrets/extra_ca > /run/gpu2go-ca/bundle.pem
  export SSL_CERT_FILE=/run/gpu2go-ca/bundle.pem CURL_CA_BUNDLE=/run/gpu2go-ca/bundle.pem \
         GIT_SSL_CAINFO=/run/gpu2go-ca/bundle.pem REQUESTS_CA_BUNDLE=/run/gpu2go-ca/bundle.pem
fi
