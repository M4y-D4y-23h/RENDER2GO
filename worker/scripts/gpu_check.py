"""Confere se o PyTorch desta imagem roda um kernel de verdade na GPU.

Chamadas só de driver (torch.cuda.is_available) passam mesmo quando a build do torch não
tem kernels para a arquitetura da GPU; aí o ComfyUI morreria na primeira operação.
Mesma ideia da checagem do worker-comfyui, implementada aqui.
"""

import sys

import torch

try:
    torch.cuda.init()
    name = torch.cuda.get_device_name(0)
    major, minor = torch.cuda.get_device_capability(0)
    free, total = torch.cuda.mem_get_info(0)
    value = (torch.zeros(8, device="cuda") + 1).sum().item()
    torch.cuda.synchronize()
    assert value == 8.0
except Exception as e:  # noqa: BLE001
    print(f"FALHA: GPU indisponível ou incompatível com torch {torch.__version__}: {e}")
    sys.exit(1)

print(
    f"OK: {name} (sm_{major}{minor}), {total / 2**30:.1f} GB de VRAM, "
    f"torch {torch.__version__}, CUDA {torch.version.cuda}"
)
