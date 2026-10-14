# Biblioteca de trilhas da montagem (ADR 0015)

As faixas que o cliente pode escolher na montagem de vídeos. **Hoje a biblioteca está vazia**: a página não mostra a opção, e toda montagem sai com o áudio em silêncio.

## Regras para uma faixa entrar

1. Só com a licença conferida e guardada aqui: CC0, domínio público (composição **e** gravação: no Brasil, a obra entra em domínio público 70 anos depois da morte do autor, e a gravação tem direitos conexos próprios, de quem gravou e de quem interpretou) ou comprada com licença para uso comercial em vídeos de terceiros (o cliente publica a montagem nas redes dele).
2. Uma linha na tabela abaixo: título, autor, intérprete, licença, onde a licença foi conferida (link e data), o arquivo e o SHA-256.
3. A faixa em `render2go_servico.montagem.TRILHAS` (id, título, autor, licença, URL de download fixa, bytes, SHA-256 e o formato do arquivo para o ffmpeg: `mp3`, `ogg`, `flac`, `wav` ou `mp4`). O build da imagem baixa e confere (`python -m render2go_servico.montagem baixar`); arquivo diferente faz o build falhar.
4. Nada de faixa enviada por cliente (ADR 0015, ponto 6).

## Faixas

| id | Título | Autor | Intérprete | Licença | Conferida em | Arquivo | SHA-256 |
|---|---|---|---|---|---|---|---|
| (nenhuma ainda) | | | | | | | |
