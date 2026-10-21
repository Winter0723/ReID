# CLIP-ReID provenance

The local CLIP-ReID adapter is derived from the official
[`Syliz517/CLIP-ReID`](https://github.com/Syliz517/CLIP-ReID) implementation of
the AAAI 2023 paper “CLIP-ReID: Exploiting Vision-Language Model for Image
Re-Identification without Concrete Text Labels”. The upstream repository is
published under the MIT License and acknowledges TransReID, OpenAI CLIP, and
CoOp as upstream codebases.

The upstream MIT text is preserved verbatim in `LICENSE.CLIP-REID`.

The historical import did not record an upstream Git commit. Consequently the
repository URL, paper, local source files, and the two immutable weight hashes
below are the current provenance boundary; a future upstream refresh must pin a
commit before any code is copied.

Local runtime files:

- `make_model_clipreid.py`: reduced inference-oriented model builder used by
  `FastAPIBackend/app/services/model_runtime.py`.
- `clip/`: local CLIP implementation required by the checkpoint architecture.
- `ViT-MSMT17-60.pth`: MSMT17 ViT-B/16 60-epoch checkpoint expected by the
  default configuration; SHA-256
  `8181c2607f37d84fc1f85e61ac56a121c2cd7a60c7b9fb64c50b366fba479c47`.
- `clip_weights/ViT-B-16.pt`: CLIP ViT-B/16 base weights; SHA-256
  `5806e77cd80f8b59890b7e101eabd078d9fb84e6937f9e85e4ecb61988df416f`.

The unused legacy baseline builder `make_model.py` was removed after repository
reference analysis confirmed that production imports only
`make_model_clipreid.py`. Any future checkpoint replacement must update these
fingerprints and pass the real-model upload/index/search smoke test before
release.

## Equivalence scope

This fork is not claimed to be source-identical or numerically interchangeable
with every upstream training/evaluation entry point. The production adapter
keeps the checkpoint-compatible ViT-B/16 image branch, uses RGB bicubic resize
to `336 x 160` with the upstream CLIP mean/standard deviation, and exposes the
512-dimensional projected image/text feature followed by L2 normalization. The
upstream ReID evaluation path can instead return a concatenated BN-neck feature,
so callers must not mix vectors produced by those two contracts in one index.

For this project, equivalence means that the pinned source and weights preserve
the existing 512-dimensional normalized-vector contract and stable retrieval
ordering for the release smoke fixture. A source, preprocessing, Torch, or
checkpoint change requires rebuilding the vector index and comparing fixture
embeddings (`torch.allclose`, `rtol=1e-4`, `atol=1e-5`) plus top-k ordering
before release. The completed real-model upload/index/search smoke test is a
behavioral compatibility check, not a claim of bit-for-bit cross-device output.

The upstream CLIP checkpoints are TorchScript archives. PyTorch currently emits
a Python 3.14 deprecation warning while loading these existing archives; the two
archive-loading call sites suppress only that exact warning. Other warnings are
left visible, and production containers remain pinned to Python 3.11 until the
upstream checkpoint format is migrated and numerically revalidated.
