// Real-model protocol check. No camera, microphone, interview records or providers.
import { createServer } from 'node:http';
import { access, mkdtemp, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, extname, join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import assert from 'node:assert/strict';
import { build } from 'esbuild';

const frontend = dirname(fileURLToPath(import.meta.url));
const root = process.argv.includes('--static') ? join(frontend, 'dist/static') : resolve(frontend, '../app/static');
let executable;
for (const path of [process.env.CHROME_PATH, 'C:/Program Files/Google/Chrome/Application/chrome.exe', '/usr/bin/google-chrome', '/usr/bin/chromium'].filter(Boolean)) {
  try { await access(path); executable = path; break; } catch { /* Try next installed browser. */ }
}
if (!executable) throw new Error('Install Chrome or set CHROME_PATH to its executable.');
const profile = await mkdtemp(join(tmpdir(), 'voice-vision-check-'));
const requests = []; const failures = [];
let mode = 'normal';
const browserCheck = `
window.monitorCheck = async () => {
  const canvas=document.createElement('canvas');canvas.width=640;canvas.height=480;
  const context=canvas.getContext('2d');context.fillStyle='white';context.fillRect(0,0,640,480);
  const track={readyState:'live',addEventListener(){},stop(){this.readyState='ended';}};
  const stream={getVideoTracks:()=>[track],getTracks:()=>[track]};
  const video={readyState:2,srcObject:stream};let snapshot;let status='';
  const monitor=new CameraMonitor({video,config:{fps:4,confidence:.6,phone_enabled:true,phone_fps:2,
    phone_confidence:.4,phone_sustain_secs:2,phone_cooldown_secs:15,absent_secs:3,multiple_secs:2,
    displacement_secs:3,displacement_ratio:.25,cooldown_secs:15},callSid:'synthetic-test',
    bitmapFactory:()=>createImageBitmap(canvas),send:async()=>({delivered:true}),
    heartbeat:(_,run)=>{snapshot=run;},status:(value)=>{status=value;}});
  try {
    await monitor.start(stream,true);
    const end=performance.now()+55000;
    while(performance.now()<end){
      if(monitor.closed)throw Error('Actual monitor failed: '+status);
      const run=monitor.summary.snapshot(monitor.seconds());
      if(run.face.frames_analyzed>=3&&run.phone.frames_analyzed>=3)
        return {faceFrames:run.face.frames_analyzed,phoneFrames:run.phone.frames_analyzed,status};
      await new Promise(done=>setTimeout(done,100));
    }
    throw Error('Actual monitor timeout: '+status);
  }finally{await monitor.stop();}
};
window.visionCheck = async () => {
  const worker = new Worker('/static/camera-worker.js');
  const wait = (type) => new Promise((resolve,reject) => {
    const timer=setTimeout(()=>reject(Error('Vision '+type+' timeout')),45000);
    worker.onerror=(event)=>{clearTimeout(timer);reject(Error(event.message));};
    worker.onmessage=({data})=>{if(data.type===type){clearTimeout(timer);resolve(data);}};
  });
  try {
    const ready=wait('ready');
    worker.postMessage({type:'init',confidence:.6,phone_enabled:window.testMode!=='face-only',phone_fps:2,phone_confidence:.4});
    const initialized=await ready;
    const canvas=document.createElement('canvas');canvas.width=640;canvas.height=480;
    const context=canvas.getContext('2d');context.fillStyle='white';context.fillRect(0,0,640,480);
    const timings=[];let result;let phoneFrames=0;
    for(let i=0;i<6;i++) {
      const bitmap=await createImageBitmap(canvas);const next=wait('result');const start=performance.now();
      worker.postMessage({type:'frame',timestamp:1000+i*250,bitmap},[bitmap]);result=await next;
      timings.push(performance.now()-start);if(result.phone)phoneFrames++;
      if(result.phone&&result.phone.count!==0)throw Error('Blank-frame phone false positive');
      if((window.testMode!=='face-failure'&&!result.face)||Object.keys(result.errors).length)throw Error('Healthy inference failed');
    }
    return {initialized,faceCount:result.face?.count??null,phoneFrames,phoneCount:result.phone?.count??null,
      inferenceMs:timings.map(n=>Math.round(n)),crossOriginIsolated};
  } finally {worker.postMessage({type:'close'});worker.terminate();}
};`;
const checkScript = (await build({stdin:{contents:`import {CameraMonitor} from './camera-monitor.js';\n${browserCheck}`,resolveDir:frontend},bundle:true,format:'esm',write:false})).outputFiles[0].contents;
const server = createServer(async (req, res) => {
  const path = new URL(req.url, 'http://localhost').pathname; requests.push({ mode, path });
  try {
    if (path === '/') {
      res.setHeader('Content-Type', 'text/html');
      res.setHeader('Content-Security-Policy', "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self' blob:; connect-src 'self' blob:; object-src 'none'");
      res.end('<!doctype html><script type="module" src="/check.js"></script>'); return;
    }
    if (path === '/check.js') { res.setHeader('Content-Type', 'text/javascript'); res.end(Buffer.concat([Buffer.from(`window.testMode=${JSON.stringify(mode)};`),checkScript])); return; }
    if ((mode === 'phone-failure' && path === '/static/vision/efficientdet_lite0.tflite')
      || (mode === 'face-failure' && path === '/static/vision/blaze_face_short_range.tflite')) { res.writeHead(404); res.end(); return; }
    const file = resolve(root, '.' + path.replace(/^\/static/, ''));
    if (!path.startsWith('/static/') || !file.startsWith(root + sep)) { res.writeHead(404); res.end(); return; }
    let content = await readFile(file);
    res.setHeader('Content-Type', extname(file) === '.wasm' ? 'application/wasm' : ['.js', '.mjs'].includes(extname(file)) ? 'text/javascript' : 'application/octet-stream');
    res.end(content);
  } catch { failures.push(path); res.writeHead(404); res.end(); }
});
await new Promise((done) => server.listen(0, '127.0.0.1', done));
const origin = `http://127.0.0.1:${server.address().port}`;
const browser = spawn(executable, ['--headless=new', '--remote-debugging-port=0', `--user-data-dir=${profile}`, '--no-first-run', '--disable-background-networking', '--disable-component-update', '--disable-extensions', 'about:blank'], { windowsHide: true, stdio: 'ignore' });
let socket; let sequence = 0; const pending = new Map(); const external = []; const errors = [];
function request(method, params = {}, sessionId) {
  return new Promise((resolve, reject) => {
    const id = ++sequence; const timer = setTimeout(() => { pending.delete(id); reject(Error(`Browser check timed out: ${method}`)); }, 60000);
    pending.set(id, { resolve, reject, timer }); socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
}
try {
  let endpoint;
  for (let attempt = 0; attempt < 150; attempt++) {
    try { endpoint = (await readFile(join(profile, 'DevToolsActivePort'), 'utf8')).trim().split('\n'); break; } catch { /* Startup. */ }
    await new Promise((done) => setTimeout(done, 100));
  }
  if (!endpoint) throw new Error('Chrome startup failed.');
  socket = new WebSocket(`ws://127.0.0.1:${endpoint[0].trim()}${endpoint[1].trim()}`);
  await new Promise((done, reject) => { socket.onopen = done; socket.onerror = reject; });
  socket.onmessage = ({ data }) => {
    const message = JSON.parse(data); const item = pending.get(message.id);
    if (item) { clearTimeout(item.timer); pending.delete(message.id); message.error ? item.reject(Error(message.error.message)) : item.resolve(message.result); }
    if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.text);
    if (message.method === 'Fetch.requestPaused') {
      const url = message.params.request.url;
      if (url.startsWith(origin + '/') || url.startsWith('blob:')) void request('Fetch.continueRequest', { requestId: message.params.requestId }, message.sessionId);
      else { external.push(url); void request('Fetch.failRequest', { requestId: message.params.requestId, errorReason: 'BlockedByClient' }, message.sessionId); }
    }
  };
  const results = [];
  for (mode of ['monitor', 'normal', 'phone-failure', 'face-failure', 'face-only', 'normal']) {
    const before = requests.length;
    const { targetId } = await request('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await request('Target.attachToTarget', { targetId, flatten: true });
    await request('Page.enable', {}, sessionId); await request('Runtime.enable', {}, sessionId);
    await request('Fetch.enable', { patterns: [{ urlPattern: '*' }] }, sessionId);
    await request('Page.navigate', { url: origin + '/' }, sessionId);
    // Wait for the external same-origin check script without an inline-script exemption.
    let value;
    for (let i = 0; i < 100; i++) {
      const response = await request('Runtime.evaluate', { expression: 'typeof window.visionCheck', returnByValue: true }, sessionId);
      if (response.result.value === 'function') break;
      await new Promise((done) => setTimeout(done, 50));
    }
    const result = await request('Runtime.evaluate', { expression: mode === 'monitor' ? 'window.monitorCheck()' : 'window.visionCheck()', awaitPromise: true, returnByValue: true }, sessionId);
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
    value = result.result.value;
    if(mode==='monitor') {
      assert.ok(value.faceFrames>=3&&value.phoneFrames>=3);assert.match(value.status,/Detector: EfficientDet-Lite0 \/ CPU/);
      results.push({mode,faceFrames:value.faceFrames,phoneFrames:value.phoneFrames});
      await request('Target.closeTarget',{targetId});continue;
    }
    assert.equal(value.initialized.detectors.face, mode === 'face-failure' ? 'unavailable' : 'ready');
    if (mode !== 'face-failure') assert.equal(value.faceCount, 0);
    assert.equal(value.crossOriginIsolated, false);
    if (mode === 'phone-failure') { assert.equal(value.initialized.detectors.phone, 'unavailable'); assert.equal(value.phoneFrames, 0); }
    else if (mode === 'face-only') {
      assert.equal(value.initialized.detectors.phone, 'disabled'); assert.equal(value.phoneFrames, 0);
      assert.ok(!requests.slice(before).some(({ path }) => /efficientdet_lite0/.test(path)));
    } else {
      assert.equal(value.initialized.detectors.phone, 'ready'); assert.equal(value.phoneFrames, 3);
      assert.equal(value.phoneCount, null); // The last frame is between phone inference intervals.
      assert.equal(value.initialized.phone_backend, 'EfficientDet-Lite0 / CPU');
    }
    results.push({ mode, backend: value.initialized.phone_backend, inferenceMs: value.inferenceMs });
    await request('Target.closeTarget', { targetId });
  }
  assert.deepEqual(failures, []); assert.deepEqual(external, []); assert.deepEqual(errors, []);
  console.log(JSON.stringify({ result: 'passed', output: root, checks: results, externalRequests: 0, providerCalls: 0, realCameraAccuracy: 'not tested' }, null, 2));
  await request('Browser.close');
} finally {
  socket?.close(); browser.kill(); server.closeAllConnections(); await new Promise((done) => server.close(done));
  for (const item of pending.values()) clearTimeout(item.timer);
}
