import pytest
import torch

from vllm.model_executor.layers.rotary_embedding.base import RotaryEmbedding
from vllm.platforms import current_platform


DTYPES = (torch.float16, torch.bfloat16, torch.float32)


def _as_tuple(value):
    return value if isinstance(value, tuple) else (value,)


def _clone_like(value):
    if isinstance(value, torch.Tensor):
        return value.clone()
    if isinstance(value, (tuple, list)):
        value_type = type(value)
        return value_type(_clone_like(item) for item in value)
    return value


@pytest.mark.skipif(not current_platform.is_cuda(), reason="CUDA required")
@pytest.mark.parametrize("dtype", DTYPES)
@pytest.mark.parametrize(
    "num_tokens, num_heads, head_size, rotary_dim",
    (
        (128, 8, 64, 64),
        (257, 4, 128, 64),
    ),
)
@pytest.mark.parametrize("with_key", (False, True))
@torch.inference_mode()
def test_rotary_embedding_forward_cuda_matches_native(
    dtype: torch.dtype,
    num_tokens: int,
    num_heads: int,
    head_size: int,
    rotary_dim: int,
    with_key: bool,
) -> None:
    torch.manual_seed(0)

    device = torch.device("cuda", 0)

    op = RotaryEmbedding(
        head_size=head_size,
        rotary_dim=rotary_dim,
        max_position_embeddings=max(1024, num_tokens + 5),
        base=10000.0,
        is_neox_style=True,
        dtype=dtype,
    ).to(device)

    positions = torch.randint(
        low=0,
        high=op.max_position_embeddings,
        size=(num_tokens,),
        device=device,
    )
    query = torch.randn(
        num_tokens, num_heads, head_size, device=device, dtype=dtype
    )
    key = torch.randn_like(query) if with_key else None

    native_args = (
        positions,
        _clone_like(query),
        _clone_like(key),
    )
    cuda_args = (
        positions,
        _clone_like(query),
        _clone_like(key),
    )

    native_out = _as_tuple(op.forward_native(*native_args))
    cuda_out = _as_tuple(op.forward_cuda(*cuda_args))

    assert len(native_out) == len(cuda_out)

    rtol, atol = {
        torch.float16: (1e-3, 1e-3),
        torch.bfloat16: (1e-3, 1e-3),
        torch.float32: (1e-4, 1e-5),
    }[dtype]

    for native_tensor, cuda_tensor in zip(native_out, cuda_out):
        if native_tensor is None and cuda_tensor is None:
            continue
        assert native_tensor is not None and cuda_tensor is not None
        assert native_tensor.shape == cuda_tensor.shape
        assert native_tensor.device == cuda_tensor.device == device
        assert native_tensor.dtype == cuda_tensor.dtype == dtype
        torch.testing.assert_close(
            native_tensor,
            cuda_tensor,
            rtol=rtol,
            atol=atol,
        )

