import { build } from 'esbuild';
import { copyFile, mkdir, readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const output = new URL('../app/static/', import.meta.url);
const clientTypes = await readFile(new URL('node_modules/@pipecat-ai/client-js/dist/index.d.ts', import.meta.url), 'utf8');
if (!clientTypes.includes('RTVI_PROTOCOL_VERSION = "1.3.0"')) {
  throw new Error('Browser client must match the pinned Python RTVI 1.3.0 protocol.');
}
await mkdir(output, { recursive: true });
await build({
  entryPoints: [fileURLToPath(new URL('demo.js', import.meta.url))],
  outfile: fileURLToPath(new URL('demo.js', output)),
  bundle: true,
  minify: true,
  format: 'esm',
  target: ['chrome120', 'firefox121', 'safari17'],
});
await copyFile(new URL('node_modules/htmx.org/dist/htmx.min.js', import.meta.url), new URL('htmx.min.js', output));
