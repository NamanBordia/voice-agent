import { FaceDetector, ObjectDetector, FilesetResolver } from '@mediapipe/tasks-vision';
let face = null; let phone = null; let lastPhone = -Infinity; let phonePeriod = 500;
self.onmessage = async ({ data }) => {
  if (data.type === 'init') {
    const detectors = { face: 'unavailable', phone: data.phone_enabled ? 'unavailable' : 'disabled' };
    const errors = {};
    let files;
    try { files = await FilesetResolver.forVisionTasks('/static/vision/wasm'); }
    catch { self.postMessage({ type: 'ready', detectors, errors: { face: 'model_load_failed', phone: 'model_load_failed' } }); return; }
    try {
      face = await FaceDetector.createFromOptions(files, {
        baseOptions: { modelAssetPath: '/static/vision/blaze_face_short_range.tflite', delegate: 'CPU' },
        canvas: new OffscreenCanvas(640, 360), runningMode: 'VIDEO',
        minDetectionConfidence: data.confidence, minSuppressionThreshold: .3,
      });
      detectors.face = 'ready';
    } catch { errors.face = 'model_load_failed'; }
    if (data.phone_enabled) {
      phonePeriod = 1000 / data.phone_fps;
      try {
        phone = await ObjectDetector.createFromOptions(files, {
          baseOptions: { modelAssetPath: '/static/vision/efficientdet_lite0.tflite', delegate: 'CPU' },
          canvas: new OffscreenCanvas(320, 320), runningMode: 'VIDEO',
          // Keep weaker candidates locally for diagnosis. PhoneRules still applies
          // the recruiter's event threshold before anything can be reported.
          categoryAllowlist: ['cell phone'], scoreThreshold: Math.min(.3, data.phone_confidence), maxResults: 5,
        });
        detectors.phone = 'ready';
      } catch { errors.phone = 'model_load_failed'; }
    }
    self.postMessage({ type: 'ready', detectors, errors }); return;
  }
  if (data.type === 'frame') {
    const result = { type: 'result', face: null, phone: null, errors: {} };
    try {
      if (face) {
        try {
          const detections = face.detectForVideo(data.bitmap, data.timestamp).detections;
          const confidence = detections.length ? Math.min(...detections.map((d) => d.categories[0]?.score || 0)) : null;
          const box = detections.length === 1 ? detections[0].boundingBox : null;
          result.face = { count: detections.length, confidence,
            center: box ? { x: (box.originX + box.width / 2) / data.bitmap.width, y: (box.originY + box.height / 2) / data.bitmap.height } : null };
        } catch { result.errors.face = 'inference_failed'; try { face.close(); } catch {} face = null; }
      }
      if (phone && data.timestamp - lastPhone >= phonePeriod) {
        lastPhone = data.timestamp;
        try {
          const matches = phone.detectForVideo(data.bitmap, data.timestamp).detections.flatMap((d) =>
            d.categories.filter((category) => category.categoryName === 'cell phone' && Number.isFinite(category.score)));
          result.phone = { count: matches.length, confidence: matches.length ? Math.max(...matches.map((category) => category.score)) : null };
        } catch { result.errors.phone = 'inference_failed'; try { phone.close(); } catch {} phone = null; }
      }
      self.postMessage(result);
    } finally { data.bitmap.close(); }
  }
  if (data.type === 'close') { face?.close(); phone?.close(); face = phone = null; self.close(); }
};
