import os
import warnings
from pathlib import Path

import torch
import torch.nn as nn
import numpy as np
from timm.layers import DropPath, to_2tuple, trunc_normal_

def weights_init_kaiming(m):
    classname = m.__class__.__name__
    if classname.find('Linear') != -1:
        nn.init.kaiming_normal_(m.weight, a=0, mode='fan_out')
        nn.init.constant_(m.bias, 0.0)

    elif classname.find('Conv') != -1:
        nn.init.kaiming_normal_(m.weight, a=0, mode='fan_in')
        if m.bias is not None:
            nn.init.constant_(m.bias, 0.0)
    elif classname.find('BatchNorm') != -1:
        if m.affine:
            nn.init.constant_(m.weight, 1.0)
            nn.init.constant_(m.bias, 0.0)

def weights_init_classifier(m):
    classname = m.__class__.__name__
    if classname.find('Linear') != -1:
        nn.init.normal_(m.weight, std=0.001)
        if m.bias:
            nn.init.constant_(m.bias, 0.0)


class TextEncoder(nn.Module):
    def __init__(self, clip_model):
        super().__init__()
        self.transformer = clip_model.transformer
        self.positional_embedding = clip_model.positional_embedding
        self.ln_final = clip_model.ln_final
        self.text_projection = clip_model.text_projection
        self.dtype = clip_model.dtype

    def forward(self, prompts, tokenized_prompts):
        x = prompts + self.positional_embedding.type(self.dtype)
        x = x.permute(1, 0, 2)  # NLD -> LND
        x = self.transformer(x)
        x = x.permute(1, 0, 2)  # LND -> NLD
        x = self.ln_final(x).type(self.dtype)

        # x.shape = [batch_size, n_ctx, transformer.width]
        # take features from the eot embedding (eot_token is the highest number in each sequence)
        device = x.device
        arange_tensor = torch.arange(x.shape[0], device=device)
        tokenized_prompts_on_device = tokenized_prompts.to(device)
        x = x[arange_tensor, tokenized_prompts_on_device.argmax(dim=-1)] @ self.text_projection
        return x


def _resolve_model_device(cfg):
    configured = getattr(getattr(cfg, "MODEL", object()), "DEVICE", "")
    if configured:
        return torch.device(str(configured))
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")

class build_transformer(nn.Module):
    def __init__(self, num_classes, camera_num, view_num, cfg):
        super(build_transformer, self).__init__()
        self.model_name = cfg.MODEL.NAME
        self.cos_layer = cfg.MODEL.COS_LAYER
        self.neck = cfg.MODEL.NECK
        self.neck_feat = cfg.TEST.NECK_FEAT
        if self.model_name == 'ViT-B-16':
            self.in_planes = 768
            self.in_planes_proj = 512
        elif self.model_name == 'RN50':
            self.in_planes = 2048
            self.in_planes_proj = 1024
        self.num_classes = num_classes
        self.camera_num = camera_num
        self.view_num = view_num
        self.sie_coe = cfg.MODEL.SIE_COE

        self.classifier = nn.Linear(self.in_planes, self.num_classes, bias=False)
        self.classifier.apply(weights_init_classifier)
        self.classifier_proj = nn.Linear(self.in_planes_proj, self.num_classes, bias=False)
        self.classifier_proj.apply(weights_init_classifier)

        self.bottleneck = nn.BatchNorm1d(self.in_planes)
        self.bottleneck.bias.requires_grad_(False)
        self.bottleneck.apply(weights_init_kaiming)
        self.bottleneck_proj = nn.BatchNorm1d(self.in_planes_proj)
        self.bottleneck_proj.bias.requires_grad_(False)
        self.bottleneck_proj.apply(weights_init_kaiming)

        self.h_resolution = int((cfg.INPUT.SIZE_TRAIN[0]-16)//cfg.MODEL.STRIDE_SIZE[0] + 1)
        self.w_resolution = int((cfg.INPUT.SIZE_TRAIN[1]-16)//cfg.MODEL.STRIDE_SIZE[1] + 1)
        self.vision_stride_size = cfg.MODEL.STRIDE_SIZE[0]
        clip_model = load_clip_to_cpu(self.model_name, self.h_resolution, self.w_resolution, self.vision_stride_size)
        clip_model.to(_resolve_model_device(cfg))

        self.image_encoder = clip_model.visual
        self.token_embedding = clip_model.token_embedding

        if cfg.MODEL.SIE_CAMERA and cfg.MODEL.SIE_VIEW:
            self.cv_embed = nn.Parameter(torch.zeros(camera_num * view_num, self.in_planes))
            trunc_normal_(self.cv_embed, std=.02)
            print('camera number is : {}'.format(camera_num))
        elif cfg.MODEL.SIE_CAMERA:
            self.cv_embed = nn.Parameter(torch.zeros(camera_num, self.in_planes))
            trunc_normal_(self.cv_embed, std=.02)
            print('camera number is : {}'.format(camera_num))
        elif cfg.MODEL.SIE_VIEW:
            self.cv_embed = nn.Parameter(torch.zeros(view_num, self.in_planes))
            trunc_normal_(self.cv_embed, std=.02)
            print('camera number is : {}'.format(view_num))

        dataset_name = cfg.DATASETS.NAMES
        # self.prompt_learner = PromptLearner(num_classes, dataset_name, clip_model.dtype, clip_model.token_embedding)
        self.prompt_learner = ImprovedDynamicPromptLearner(num_classes, dataset_name, clip_model.dtype, clip_model.token_embedding)
        self.text_encoder = TextEncoder(clip_model)

    def forward(self, x = None, label=None, get_image = False, get_text = False, cam_label= None, view_label=None):
        if get_text == True:
            prompts = self.prompt_learner(label)
            text_features = self.text_encoder(prompts, self.prompt_learner.tokenized_prompts)
            return text_features

        if get_image == True:
            image_features_last, image_features, image_features_proj = self.image_encoder(x)
            if self.model_name == 'RN50':
                return image_features_proj[0]
            elif self.model_name == 'ViT-B-16':
                return image_features_proj[:,0]

        if self.model_name == 'RN50':
            image_features_last, image_features, image_features_proj = self.image_encoder(x)
            img_feature_last = nn.functional.avg_pool2d(image_features_last, image_features_last.shape[2:4]).view(x.shape[0], -1)
            img_feature = nn.functional.avg_pool2d(image_features, image_features.shape[2:4]).view(x.shape[0], -1)
            img_feature_proj = image_features_proj[0]

        elif self.model_name == 'ViT-B-16':
            if cam_label != None and view_label!=None:
                cv_embed = self.sie_coe * self.cv_embed[cam_label * self.view_num + view_label]
            elif cam_label != None:
                cv_embed = self.sie_coe * self.cv_embed[cam_label]
            elif view_label!=None:
                cv_embed = self.sie_coe * self.cv_embed[view_label]
            else:
                cv_embed = None
            image_features_last, image_features, image_features_proj = self.image_encoder(x, cv_embed)
            img_feature_last = image_features_last[:,0]
            img_feature = image_features[:,0]
            img_feature_proj = image_features_proj[:,0]

        feat = self.bottleneck(img_feature)
        feat_proj = self.bottleneck_proj(img_feature_proj)

        if self.training:
            cls_score = self.classifier(feat)
            cls_score_proj = self.classifier_proj(feat_proj)
            return [cls_score, cls_score_proj], [img_feature_last, img_feature, img_feature_proj], img_feature_proj

        else:
            if self.neck_feat == 'after':
                # print("Test with feature after BN")
                return torch.cat([feat, feat_proj], dim=1)
            else:
                return torch.cat([img_feature, img_feature_proj], dim=1)

    def encode_text_tokens(self, tokenized_text):
        device = self.token_embedding.weight.device
        tokenized_text = tokenized_text.to(device)
        prompts = self.token_embedding(tokenized_text).type(self.text_encoder.dtype)
        return self.text_encoder(prompts, tokenized_text)

    def load_param(self, trained_path):
        param_dict = torch.load(trained_path, map_location="cpu")
        state_dict = self.state_dict()
        for i in param_dict:
            key = i.replace('module.', '')
            if key not in state_dict:
                raise KeyError(f"Unexpected checkpoint key: {key}")
            target = state_dict[key]
            state_dict[key].copy_(param_dict[i].to(device=target.device, dtype=target.dtype))
        print('Loading pretrained model from {}'.format(trained_path))

    def load_param_finetune(self, model_path):
        param_dict = torch.load(model_path)
        for i in param_dict:
            self.state_dict()[i].copy_(param_dict[i])
        print('Loading pretrained model for finetuning from {}'.format(model_path))


def make_model(cfg, num_class, camera_num, view_num):
    model = build_transformer(num_class, camera_num, view_num, cfg)
    return model


from .clip import clip
def load_clip_to_cpu(backbone_name, h_resolution, w_resolution, vision_stride_size):
    url = clip._MODELS[backbone_name]
    cache_root = os.getenv("CLIP_CACHE_DIR")
    if not cache_root:
        cache_root = Path(__file__).resolve().parent / "clip_weights"
    model_path = clip._download(url, root=str(cache_root))

    try:
        # loading JIT archive
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r"`torch\.jit\.load` is not supported in Python 3\.14\+ and may break.*",
                category=DeprecationWarning,
            )
            model = torch.jit.load(model_path, map_location="cpu").eval()
        state_dict = None

    except RuntimeError:
        state_dict = torch.load(model_path, map_location="cpu")

    model = clip.build_model(state_dict or model.state_dict(), h_resolution, w_resolution, vision_stride_size)

    return model

class PromptLearner(nn.Module):
    def __init__(self, num_class, dataset_name, dtype, token_embedding):
        super().__init__()
        if dataset_name == "VehicleID" or dataset_name == "veri":
            ctx_init = "A photo of a X X X X vehicle."
        else:
            ctx_init = "A photo of a X X X X person."

        ctx_dim = 512
        # use given words to initialize context vectors
        ctx_init = ctx_init.replace("_", " ")
        n_ctx = 4

        device = token_embedding.weight.device
        tokenized_prompts = clip.tokenize(ctx_init).to(device)
        with torch.no_grad():
            embedding = token_embedding(tokenized_prompts).type(dtype)
        self.tokenized_prompts = tokenized_prompts  # torch.Tensor

        n_cls_ctx = 4
        cls_vectors = torch.empty(num_class, n_cls_ctx, ctx_dim, dtype=dtype)
        nn.init.normal_(cls_vectors, std=0.02)
        self.cls_ctx = nn.Parameter(cls_vectors)


        # These token vectors will be saved when in save_model(),
        # but they should be ignored in load_model() as we want to use
        # those computed using the current class names
        self.register_buffer("token_prefix", embedding[:, :n_ctx + 1, :])
        self.register_buffer("token_suffix", embedding[:, n_ctx + 1 + n_cls_ctx: , :])
        self.num_class = num_class
        self.n_cls_ctx = n_cls_ctx

    def forward(self, label):
        cls_ctx = self.cls_ctx[label]
        b = label.shape[0]
        prefix = self.token_prefix.expand(b, -1, -1)
        suffix = self.token_suffix.expand(b, -1, -1)

        prompts = torch.cat(
            [
                prefix,  # (n_cls, 1, dim)
                cls_ctx,     # (n_cls, n_ctx, dim)
                suffix,  # (n_cls, *, dim)
            ],
            dim=1,
        )

        return prompts


# Have already tested. Same as the original model.
class ImprovedDynamicPromptLearner(nn.Module):
    def __init__(self, num_class, dataset_name, dtype, token_embedding, ctx_dim=512, n_ctx=4, hidden_dim=1024, alpha=0.1):
        super().__init__()

        if dataset_name in ["VehicleID", "veri"]:
            ctx_init = "A photo of a X X X X vehicle."
        else:
            ctx_init = "A photo of a X X X X person."

        ctx_init = ctx_init.replace("_", " ")
        self.n_ctx = n_ctx
        self.n_cls_ctx = n_ctx
        self.alpha = alpha  # 控制静态和动态上下文的权重

        device = token_embedding.weight.device
        tokenized_prompts = clip.tokenize(ctx_init).to(device)
        with torch.no_grad():
            embedding = token_embedding(tokenized_prompts).type(dtype)
        self.tokenized_prompts = tokenized_prompts

        self.register_buffer("token_prefix", embedding[:, :n_ctx + 1, :])
        self.register_buffer("token_suffix", embedding[:, n_ctx + 1 + n_ctx:, :])

        # 可学习的静态上下文
        self.static_cls_ctx = nn.Parameter(torch.randn(num_class, n_ctx, ctx_dim) * 0.02)

        # 类别特征嵌入
        self.class_embedding = nn.Embedding(num_class, ctx_dim)

        # 动态上下文生成网络
        self.context_generator = nn.Sequential(
            nn.Linear(ctx_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, n_ctx * ctx_dim)
        )

        self.num_class = num_class
        self.ctx_dim = ctx_dim

    def forward(self, label):
        # 通过 label 获取类别特征
        label = label.to(self.class_embedding.weight.device)
        class_features = self.class_embedding(label)  # (batch_size, ctx_dim)

        # 生成动态上下文
        dynamic_ctx = self.context_generator(class_features)
        dynamic_ctx = dynamic_ctx.view(-1, self.n_cls_ctx, self.ctx_dim)  # (batch_size, n_ctx, ctx_dim)

        # 获取静态上下文
        static_ctx = self.static_cls_ctx[label]  # (batch_size, n_ctx, ctx_dim)

        # 融合静态和动态上下文
        cls_ctx = self.alpha * static_ctx + (1 - self.alpha) * dynamic_ctx

        b = label.shape[0]
        prefix = self.token_prefix.expand(b, -1, -1)
        suffix = self.token_suffix.expand(b, -1, -1)

        prompts = torch.cat([
            prefix,    # (batch_size, n_ctx+1, ctx_dim)
            cls_ctx,   # (batch_size, n_ctx, ctx_dim)
            suffix     # (batch_size, *, ctx_dim)
        ], dim=1)

        return prompts


class ImprovedDynamicPromptLearner2(nn.Module):
    # 新增 vis_dim 参数，用于接收图像特征的维度
    def __init__(self, num_class, dataset_name, dtype, token_embedding, vis_dim, ctx_dim=512, n_ctx=4, hidden_dim=1024, alpha=0.1):
        super().__init__()

        if dataset_name in ["VehicleID", "veri"]:
            ctx_init = "A photo of a X X X X vehicle."
        else:
            ctx_init = "A photo of a X X X X person."

        ctx_init = ctx_init.replace("_", " ")
        self.n_ctx = n_ctx
        self.n_cls_ctx = n_ctx
        self.alpha = alpha  # 控制静态和动态上下文的权重

        device = token_embedding.weight.device
        tokenized_prompts = clip.tokenize(ctx_init).to(device)
        with torch.no_grad():
            embedding = token_embedding(tokenized_prompts).type(dtype)
        self.tokenized_prompts = tokenized_prompts

        self.register_buffer("token_prefix", embedding[:, :n_ctx + 1, :])
        self.register_buffer("token_suffix", embedding[:, n_ctx + 1 + n_ctx:, :])

        # 可学习的静态上下文
        self.static_cls_ctx = nn.Parameter(torch.randn(num_class, n_ctx, ctx_dim) * 0.02)

        # 类别特征嵌入 (ID Embedding)
        self.class_embedding = nn.Embedding(num_class, ctx_dim)

        # ================= 新增：视觉特征投影网络 (Meta-Net) =================
        self.vis_projector = nn.Sequential(
            nn.Linear(vis_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, ctx_dim)
        )
        # ===================================================================

        # 动态上下文生成网络
        self.context_generator = nn.Sequential(
            nn.Linear(ctx_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, n_ctx * ctx_dim)
        )

        self.num_class = num_class
        self.ctx_dim = ctx_dim

    # 新增 img_features 参数
    def forward(self, label, img_features):
        # 1. 获取 ID 级别的类别特征
        label = label.to(self.class_embedding.weight.device)
        class_features = self.class_embedding(label)  # (batch_size, ctx_dim)

        # 2. 获取 Instance 级别的视觉条件特征
        # 将 img_features 转换为与 class_features 相同的数据类型 (防止 AMP 混合精度报错)
        img_features = img_features.type(class_features.dtype)
        vis_cond = self.vis_projector(img_features)   # (batch_size, ctx_dim)

        # 3. 融合：ID 引导 + 实例感知 (核心创新点在这里！)
        # 采用 CoCoOp 的加法融合策略
        fused_features = class_features + vis_cond    # (batch_size, ctx_dim)

        # 4. 生成动态上下文 (现在它真正是 instance-aware 了)
        dynamic_ctx = self.context_generator(fused_features)
        dynamic_ctx = dynamic_ctx.view(-1, self.n_cls_ctx, self.ctx_dim)  # (batch_size, n_ctx, ctx_dim)

        # 获取静态上下文
        static_ctx = self.static_cls_ctx[label]  # (batch_size, n_ctx, ctx_dim)

        # 融合静态和动态上下文
        cls_ctx = self.alpha * static_ctx + (1 - self.alpha) * dynamic_ctx

        b = label.shape[0]
        prefix = self.token_prefix.expand(b, -1, -1)
        suffix = self.token_suffix.expand(b, -1, -1)

        prompts = torch.cat([
            prefix,    # (batch_size, n_ctx+1, ctx_dim)
            cls_ctx,   # (batch_size, n_ctx, ctx_dim)
            suffix     # (batch_size, *, ctx_dim)
        ], dim=1)

        return prompts
