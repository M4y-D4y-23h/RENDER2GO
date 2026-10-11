"""Roda no pod (venv do worker): 1 vídeo SFW (fixtures/sfw/produto-cafe.jpg, 832×480, 3 s), a
conferência e o filtro de imagem na GPU, com os tempos. Imprime uma linha JSON."""

import json
import sys
import time
from pathlib import Path

from render2go_worker import cli_submit, conferencia, filtro_imagem, lote, preparo

FOTO = Path("/opt/render2go/fixtures/sfw/produto-cafe.jpg")
PROMPT = "The camera slowly dollies in toward the coffee cup on the table. Soft natural light."
saida = Path(sys.argv[1] if len(sys.argv) > 1 else "/root/render2go/teste")
saida.mkdir(parents=True, exist_ok=True)
r: dict = {}
t = time.monotonic()
prep = preparo.preparar(FOTO, 832, 480, saida / "preparada.png")
video = saida / "video.mp4"
args = cli_submit.build_parser().parse_args(
    ["--template", str(lote.I2V_TEMPLATE), "--image", str(prep.path), "--prompt-en", PROMPT]
    + ["--width", "832", "--height", "480", "--duration", "3", "--seed", "42", "--out", str(video)]
)
code, res = cli_submit.run(args)
r["render_s"] = round(time.monotonic() - t, 1)
r["render_ok"] = code == cli_submit.EXIT_OK and video.is_file()
if not r["render_ok"]:
    r["erro"] = str(res)[:300]
    print(json.dumps(r))
    sys.exit(1)
r["similaridade"] = round(conferencia.similaridade(prep.path, video), 4)
r |= conferencia.continuidade(video)
clas = filtro_imagem.Freepik(Path("/opt/comfyui/models") / filtro_imagem.PASTA)
for vez in ("filtro_1a", "filtro_2a"):  # a 1ª inclui importar torch/timm e carregar o modelo
    t = time.monotonic()
    f = filtro_imagem.avaliar(video, clas)
    r[f"{vez}_s"] = round(time.monotonic() - t, 2)
r |= {"nsfw": f.nota, "nsfw_baixa": f.nota_baixa, "quadros": f.quadros, "barrado": f.barrado}
print(json.dumps(r))
