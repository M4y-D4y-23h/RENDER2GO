"""Confere se o PyTorch desta imagem roda um kernel de verdade na GPU.

Chamadas só de driver (torch.cuda.is_available) passam mesmo quando a build do torch não
tem kernels para a arquitetura da GPU; aí o ComfyUI morreria na primeira operação.
Mesma ideia da checagem do worker-comfyui, implementada aqui.
"""

import sys

import torch

try:
    torch.cuda.init()
    n = torch.cuda.device_count()
    assert n >= 1, "nenhuma GPU visível"
    linhas = []
    for i in range(n):  # pods com várias GPUs: todas precisam rodar kernel
        name = torch.cuda.get_device_name(i)
        major, minor = torch.cuda.get_device_capability(i)
        free, total = torch.cuda.mem_get_info(i)
        value = (torch.zeros(8, device=f"cuda:{i}") + 1).sum().item()
        torch.cuda.synchronize(i)
        assert value == 8.0
        linhas.append(f"GPU {i}: {name} (sm_{major}{minor}), {total / 2**30:.1f} GB de VRAM")
except Exception as e:  # noqa: BLE001
    print(f"FALHA: GPU indisponível ou incompatível com torch {torch.__version__}: {e}")
    sys.exit(1)

for linha in linhas:
    print(f"OK: {linha}")
print(f"torch {torch.__version__}, CUDA {torch.version.cuda}")
