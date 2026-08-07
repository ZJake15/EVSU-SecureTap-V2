# MiniFASNetV2 model provenance

`2.7_80x80_MiniFASNetV2.onnx` is a from-source ONNX export of the official
MiniFASNetV2 (2.7, 80x80) weights from the Silent-Face-Anti-Spoofing project
by Minivision AI.

- **Original repository:** https://github.com/minivision-ai/Silent-Face-Anti-Spoofing
- **License:** Apache License 2.0
- **Original weights file:** `resources/anti_spoof_models/2.7_80x80_MiniFASNetV2.pth`
- **Original weights SHA-256:** `A5EB02E1843F19B5386B953CC4C9F011C3F985D0EE2BB9819EEA9A142099BEC0`
  (verified against the downloaded file byte-for-byte before conversion)
- **This ONNX file's SHA-256:** `8EA5552624053FEF892A77E7D40B7ADE37E996A94EFDCBAB352179CB9C46AE73`
- **Converted:** 2026-08-07, via a from-scratch reconstruction of the official
  `MiniFASNetV2` architecture (`src/model_lib/MiniFASNet.py`), loaded from the
  verified `.pth` weights with `strict=True` (zero missing/unexpected keys),
  exported with `torch.onnx.export(..., opset_version=12, dynamo=False)`.

## Verification performed before use

1. **Numerical parity**: ONNX Runtime output vs. the original PyTorch model's
   output on 5 random inputs - max absolute difference `1.43e-6`.
2. **End-to-end correctness**: ran the real pipeline (InsightFace face
   detection -> the same 2.7x context-crop algorithm as the original repo's
   `CropImage` -> this ONNX model) against the official repo's own labeled
   sample images (`images/sample/image_T1.jpg` = real,
   `image_F1.jpg`/`image_F2.jpg` = spoof):
   - `image_T1.jpg` (real): P(live) = **0.9997**
   - `image_F1.jpg` (spoof): P(live) = **0.0081**
   - `image_F2.jpg` (spoof): P(live) = **0.0007**

## Important preprocessing note

The original repo's custom `to_tensor()` (`src/data_io/functional.py`) does
**not** scale pixel values to `[0, 1]` - that line is present but commented
out in the source (`# return img.float().div(255)`). The model was trained
on raw `[0, 255]` BGR pixel values. `users/liveness_utils.py` replicates this
exactly - dividing by 255 before inference silently breaks the model's
output (verified: it collapses to a near-constant, wrong prediction
regardless of input). Preserve this if the model is ever re-converted or
replaced.

## What this model does and doesn't cover

MiniFASNetV2 is a **single-frame, passive** anti-spoofing classifier trained
primarily on print-attack and screen-replay-attack data. It has no temporal
signal and no depth/IR input. It is not designed to catch 3D masks or a very
steady, high-quality video replay - see `liveness_utils.py`'s module
docstring for how this is combined with the existing per-frame texture/
frequency/reflectance heuristics already in this project.
