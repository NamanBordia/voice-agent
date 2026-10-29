// Setup-only model download. Interview runtime never downloads third-party assets.
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile, rename } from 'node:fs/promises';
const directory = new URL('../app/static/vision/', import.meta.url);
const digest = (bytes) => createHash('sha256').update(bytes).digest('hex');
await mkdir(directory, { recursive: true });
const models = [
  { name: 'blaze_face_short_range.tflite', path: 'face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite', sha: 'b4578f35940bf5a1a655214a1cce5cab13eba73c1297cd78e1a04c2380b0152f' },
  { name: 'efficientdet_lite0.tflite', path: 'object_detector/efficientdet_lite0/int8/1/efficientdet_lite0.tflite', sha: '0720bf247bd76e6594ea28fa9c6f7c5242be774818997dbbeffc4da460c723bb' },
];
for (const asset of models) {
  const model = new URL(asset.name, directory);
  let bytes;
  try { bytes = await readFile(model); } catch {}
  if (!bytes || digest(bytes) !== asset.sha) {
    const response = await fetch(`https://storage.googleapis.com/mediapipe-models/${asset.path}`, { signal: AbortSignal.timeout(60000) });
    if (!response.ok) throw new Error(`Model download failed: ${response.status}`);
    bytes = Buffer.from(await response.arrayBuffer());
    if (digest(bytes) !== asset.sha) throw new Error('Model checksum mismatch; asset was not installed.');
    const temporary = new URL(`${asset.name}.tmp`, directory);
    await writeFile(temporary, bytes); await rename(temporary, model);
  }
  console.log(`Local ${asset.name} version 1 verified (SHA-256).`);
}
