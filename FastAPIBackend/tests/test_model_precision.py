from types import SimpleNamespace

import pytest
import torch

from app.services.model_precision import prepare_clipreid, resolve_precision


def test_precision_rejects_unsupported_devices():
    assert resolve_precision("auto", torch.device("cuda")) == "fp16"
    assert resolve_precision("auto", torch.device("cpu")) == "fp32"
    with pytest.raises(ValueError, match="requires MODEL_DEVICE=cpu"):
        resolve_precision("int8", torch.device("cuda"))
    with pytest.raises(ValueError, match="FP16"):
        resolve_precision("fp16", torch.device("cpu"))


def tiny_clip():
    from model.clip.model import ResidualAttentionBlock
    model = torch.nn.Module()
    model.visual = torch.nn.Sequential(ResidualAttentionBlock(32, 4))
    model.text_encoder = SimpleNamespace(dtype=torch.float16)
    return model


# PyTorch still exposes this supported compatibility path; only its known
# migration notice is excluded from strict warning checks.
@pytest.mark.filterwarnings("ignore:torch.ao.quantization is deprecated:DeprecationWarning")
def test_int8_preserves_attention_and_produces_finite_features():
    model = tiny_clip()
    prepare_clipreid(model, "int8")
    block = model.visual[0]
    assert isinstance(block.mlp.c_fc, torch.ao.nn.quantized.dynamic.Linear)
    assert isinstance(block.attn.in_proj_weight, torch.nn.Parameter)
    assert isinstance(block.attn.out_proj.weight, torch.nn.Parameter)
    assert model.text_encoder.dtype == torch.float32
    with torch.inference_mode():
        result = model.visual(torch.randn(8, 1, 32))
    assert result.shape == (8, 1, 32)
    assert torch.isfinite(result).all()


def test_fp16_keeps_clip_layernorm_float32():
    model = prepare_clipreid(tiny_clip(), "fp16")
    assert model.visual[0].mlp.c_fc.weight.dtype == torch.float16
    assert model.visual[0].ln_1.weight.dtype == torch.float32
    assert model.text_encoder.dtype == torch.float16
    with torch.inference_mode():
        assert torch.isfinite(model.visual(torch.randn(8, 1, 32).half())).all()
