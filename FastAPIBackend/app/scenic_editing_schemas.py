"""Bounded editing input; source identity always comes from the saved server draft."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EditConfig(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    template_id: Literal["documentary", "upbeat", "memory"] = "documentary"
    title: str = Field(default="我的游览回忆", max_length=80)
    brand: str = Field(default="", max_length=40)
    music_id: str = Field(default="auto", max_length=80)
    music_volume: float = Field(default=0.25, ge=0, le=1)
    original_volume: float = Field(default=1.0, ge=0, le=1)
    landscape_seconds: int = Field(default=75, ge=60, le=90)
    portrait_seconds: int = Field(default=45, ge=30, le=60)
    use_tracking: bool = False
    previous_draft_id: int | None = Field(default=None, gt=0)


class ClipEdit(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=128)
    start_seconds: float = Field(ge=0, le=86400)
    end_seconds: float = Field(gt=0, le=86400)
    included: bool = True
    locked: bool = False
    keep_audio: bool = False
    focus_x: float = Field(default=0.5, ge=0, le=1)
    focus_y: float = Field(default=0.5, ge=0, le=1)
    crop_mode: Literal["fit", "focus", "track"] = "fit"
    title: str = Field(default="", max_length=80)

    @model_validator(mode="after")
    def valid_range(self):
        if self.end_seconds <= self.start_seconds:
            raise ValueError("片段结束时间必须晚于开始时间")
        return self


class DraftUpdate(BaseModel):
    revision: int = Field(gt=0)
    data: dict


class VlogSubmit(BaseModel):
    draft_id: int = Field(gt=0)
    revision: int = Field(gt=0)


class MediaEditingMetadata(BaseModel):
    recorded_at: datetime | None = None
    location: str = Field(default="", max_length=255)


class TouristReference(BaseModel):
    entry_target_box: list[float] | None = Field(default=None, min_length=4, max_length=4)

    @model_validator(mode="after")
    def valid_box(self):
        if self.entry_target_box is not None:
            x1, y1, x2, y2 = self.entry_target_box
            if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
                raise ValueError("请选择图片内有效的人物区域")
        return self
