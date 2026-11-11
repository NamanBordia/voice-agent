import { copyFile, cp, mkdir } from 'node:fs/promises';

export const modelFiles = ['blaze_face_short_range.tflite', 'efficientdet_lite0.tflite'];

/** Copy the pinned MediaPipe runtime and detector models. */
export async function copyVisionAssets(output, includeModels = true) {
  await mkdir(new URL('vision/', output), { recursive: true });
  await cp(new URL('node_modules/@mediapipe/tasks-vision/wasm/', import.meta.url), new URL('vision/wasm/', output), { recursive: true });
  if (includeModels) for (const name of modelFiles) await copyFile(new URL(`../app/static/vision/${name}`, import.meta.url), new URL(`vision/${name}`, output));
}
