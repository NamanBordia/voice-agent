# Retired browser YOLO phone trial

Status: retired on 2026-11-12 at the user's explicit request to switch back to
EfficientDet-Lite0. This document records the experiment; it is not the current
setup guide. Use [the interview workflow](INTERVIEWS.md) for current instructions.

The trial used YOLO11n float32 ONNX with a 640x640 input through
onnxruntime-web@1.31.1 (WebGPU with a WASM fallback). It kept BlazeFace for faces,
existing consent and neutral phone events, and the single browser worker.

The real-camera report confirmed two saved phone observations, but only 95
analyzed face/phone frames during a 123-second interview. Face coverage was
31 seconds and phone coverage 79 seconds. Slow shared-worker processing delayed
face observations and repeatedly reset sustained-face confirmation. These tests
did not establish that YOLO was more accurate than EfficientDet-Lite0.

The active implementation again uses the original MediaPipe EfficientDet-Lite0
int8 version-1 model and classic worker. ONNX Runtime, YOLO processing code, model
downloads and deployment assets have been removed. Proportional bitmap capture,
the 0.40 default phone threshold, diagnostic scores and acknowledged event delivery
remain. Existing activated definitions retain their saved camera settings.

Both current models and all required MediaPipe assets are same-origin. No images
are uploaded or recorded. Model accuracy and actual voice responsiveness still
require human testing. No automatic camera-based technical rating is produced.

To verify a rebuilt deployment, look for `Detector: EfficientDet-Lite0 / CPU`.
The Vercel settings are unchanged; its static output is again 21 allowlisted files.
`npm.cmd --prefix frontend run test:vision-browser` checks actual current model
loading, blank-frame inference, independent model failures, face-only mode and
monitor cleanup without real media or speech/LLM provider calls. Add `-- --static`
to check the hosted output after `build:static`.

Return-to-EfficientDet verification: 99 frontend tests, 63 relevant backend/template
tests, both builds, and actual-model Chrome checks against local and hosted-build
assets passed. The static audit permits 21 public files and rejects retired model
assets. These checks made no paid provider calls and do not validate camera accuracy
or audible interview behavior.
