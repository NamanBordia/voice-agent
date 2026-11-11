import { copyFile, mkdir } from 'node:fs/promises';
import { bundleBrowserEntry, verifyClientProtocol } from './build-common.mjs';
import { copyVisionAssets } from './vision-assets.mjs';

const output = new URL('../app/static/', import.meta.url);
await verifyClientProtocol();
await import('./prepare-vision.mjs');
await mkdir(output, { recursive: true });
await bundleBrowserEntry('demo', output);
await copyFile(new URL('node_modules/htmx.org/dist/htmx.min.js', import.meta.url), new URL('htmx.min.js', output));
for (const entry of ['recruiter', 'candidate', 'candidate-view', 'camera-worker']) {
  await bundleBrowserEntry(entry, output);
}
await copyVisionAssets(output, false);
