import { readFile } from 'node:fs/promises';
import { dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

export function sdkLoopbackPlugin(requireSDK = false) {
  return {
    name: 'candidate-sdk-loopback-fetch',
    setup(build) {
      const adaptedModules = new Set();
      build.onLoad({ filter: /[\\/]@pipecat-ai[\\/](client-js|small-webrtc-transport)[\\/]dist[\\/]index(\.module)?\.js$/ }, async ({ path }) => {
        const source = await readFile(path, 'utf8');
        // Scope this adapter to the pinned SDK modules in hosted bundles. Keep
        // negotiation/retry behavior and do not patch the browser's global fetch.
        const adapted = source.replace(/(?<![\w$.])fetch\(/g, 'sdkCandidateFetch(');
        if (adapted === source) throw new Error('Pinned SDK fetch locations changed; review the loopback adapter.');
        adaptedModules.add(path);
        const helper = fileURLToPath(new URL('candidate-config.js', import.meta.url));
        return { contents: `import { sdkCandidateFetch } from ${JSON.stringify(helper)};\n${adapted}`, loader: 'js', resolveDir: dirname(path) };
      });
      build.onEnd((result) => {
        if (requireSDK && !result.errors.length && adaptedModules.size !== 2) return { errors: [{ text: 'Expected both pinned Pipecat SDK fetch adapters; review SDK entry points.' }] };
      });
    },
  };
}
