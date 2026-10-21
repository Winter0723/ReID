"""Explicit precision policies; never silently run INT8 on an unsupported device."""
import torch


def resolve_precision(requested: str, device: torch.device) -> str:
    if requested == "auto":
        return "fp16" if device.type == "cuda" else "fp32"
    if requested == "fp16" and device.type != "cuda":
        raise ValueError("FP16 inference requires MODEL_DEVICE=cuda; use fp32 on CPU/MPS")
    if requested == "int8" and device.type != "cpu":
        raise ValueError("CLIP-ReID dynamic INT8 requires MODEL_DEVICE=cpu")
    return requested


def prepare_clipreid(model, precision: str):
    model.eval()
    if precision == "fp16":
        model.half()
        # CLIP's LayerNorm explicitly computes in FP32 and casts back.
        for module in model.modules():
            if isinstance(module, torch.nn.LayerNorm):
                module.float()
    else:
        model.float()
    # This is a plain attribute, so Module.float()/half() does not update it.
    if hasattr(model, "text_encoder"):
        model.text_encoder.dtype = torch.float16 if precision == "fp16" else torch.float32
    if precision == "int8":
        from torch.ao.quantization import quantize_dynamic
        # Quantize ViT feed-forward layers only. MultiheadAttention reads raw
        # projection weights and must not receive dynamic Linear replacements.
        count = 0
        for name, module in list(model.named_modules()):
            if name.endswith(".mlp") and isinstance(module, torch.nn.Sequential):
                quantize_dynamic(module, {torch.nn.Linear}, dtype=torch.qint8, inplace=True)
                count += 1
        if not count:
            raise ValueError("No supported transformer MLP layers found for CLIP-ReID INT8")
    return model
