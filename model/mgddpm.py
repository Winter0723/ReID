"""Inference-only MGD-DPM teacher, matching upstream 7ff0676.

Training classifiers/discriminator are deliberately excluded; every inference
parameter must be present. No pretrained downloads or random fallback weights.
"""
import numpy as np
import torch
from torch import nn

from .clip.model import VisionTransformer


class ResidualAdapter(nn.Module):
    def __init__(self, dimension):
        super().__init__()
        self.alpha = nn.Parameter(torch.zeros(()))
        self.fc = nn.Sequential(nn.Linear(dimension, dimension // 4, bias=False),
                                nn.ReLU(inplace=True), nn.Linear(dimension // 4, dimension, bias=False))

    def forward(self, value):
        return value + self.alpha * self.fc(value)


class MGDTeacher(nn.Module):
    def __init__(self, cameras, image_size=(256, 128), stride=12, camera_index=0, sie_coefficient=1.0):
        super().__init__()
        height, width = [(size - 16) // stride + 1 for size in image_size]
        self.image_encoder = VisionTransformer(height, width, 16, stride, 768, 12, 12, 512)
        self.camera_index = camera_index
        self.sie_coefficient = sie_coefficient
        self.cv_embed = nn.Parameter(torch.zeros(cameras, 768))
        self.adapter = ResidualAdapter(768)
        self.adapter_proj = ResidualAdapter(512)
        self.bottleneck = nn.BatchNorm1d(768)
        self.bottleneck_proj = nn.BatchNorm1d(512)

    def forward(self, inputs):
        # The default evaluation recipe uses camera index 0 and SIE_COE 1.0.
        _, features, projected = self.image_encoder(inputs, self.sie_coefficient * self.cv_embed[self.camera_index])
        return torch.cat((self.bottleneck(self.adapter(features[:, 0])),
                          self.bottleneck_proj(self.adapter_proj(projected[:, 0]))), dim=1)


def load_teacher(path, *, image_size=(256, 128), stride=12, camera_index=0, sie_coefficient=1.0):
    # Older checkpoints include a numpy scalar mAP alongside tensor dictionaries.
    from numpy.core.multiarray import scalar
    with torch.serialization.safe_globals([(scalar, "numpy.core.multiarray.scalar"),
                                            np.dtype, type(np.dtype("float64")), type(np.dtype("float32"))]):
        checkpoint = torch.load(path, map_location="cpu", weights_only=True)
    teacher = checkpoint["teacher"]
    state = {key.removeprefix("module."): value for key, value in teacher.items()}
    model = MGDTeacher(state["cv_embed"].shape[0], image_size, stride, camera_index, sie_coefficient)
    inference = {key: value for key, value in state.items()
                 if not key.startswith(("classifier.", "classifier_proj.", "cam_discriminator."))}
    model.load_state_dict(inference, strict=True)
    return model.float().eval()
