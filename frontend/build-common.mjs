import { build } from 'esbuild';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { sdkLoopbackPlugin } from './sdk-loopback-plugin.mjs';

export async function verifyClientProtocol() {
  const types = await readFile(new URL('node_modules/@pipecat-ai/client-js/dist/index.d.ts', import.meta.url), 'utf8');
  if (!types.includes('RTVI_PROTOCOL_VERSION = "1.3.0"')) throw new Error('Browser client must match the pinned Python RTVI 1.3.0 protocol.');
}

export async function bundleBrowserEntry(entry, output, apiBase) {
  await build({
    entryPoints: [fileURLToPath(new URL(`${entry}.js`, import.meta.url))],
    outfile: fileURLToPath(new URL(`${entry}.js`, output)), bundle: true, minify: true,
    format: entry === 'camera-worker' ? 'iife' : 'esm', target: ['chrome120', 'firefox121', 'safari17'],
    define: apiBase === undefined ? {} : { __CANDIDATE_API_BASE_URL__: JSON.stringify(apiBase) },
    plugins: apiBase === undefined ? [] : [sdkLoopbackPlugin(['candidate', 'demo', 'workspace'].includes(entry))],
  });
}
