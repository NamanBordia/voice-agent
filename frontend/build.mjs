import { copyFile, cp, mkdir } from 'node:fs/promises';
import { bundleBrowserEntry, verifyClientProtocol } from './build-common.mjs';

const output = new URL('../app/static/', import.meta.url);
await verifyClientProtocol();
await mkdir(output, { recursive: true });
await bundleBrowserEntry('demo', output);
await copyFile(new URL('node_modules/htmx.org/dist/htmx.min.js', import.meta.url), new URL('htmx.min.js', output));
for (const entry of ['recruiter', 'candidate', 'candidate-view', 'camera-worker', 'workspace-pairing']) {
  await bundleBrowserEntry(entry, output);
}
await mkdir(new URL('vision/', output), { recursive: true });
await cp(new URL('node_modules/@mediapipe/tasks-vision/wasm/', import.meta.url), new URL('vision/wasm/', output), { recursive: true });
