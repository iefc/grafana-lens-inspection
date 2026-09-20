// offscreen：持有到本地桥的 WS；按桥指令驱动 collector/SW；压缩截图。
import { WSClient } from '../lib/ws-client.js';
import { compressJpeg, cropJpeg } from '../lib/image.js';
import { isDeadCollectorResult } from '../lib/tab-target.js';

const DEFAULT_BRIDGE_URL = 'ws://127.0.0.1:9527/ws';
const DEV_ECHO = 'ws://127.0.0.1:9528/ws';

let client = null;
let keepAliveTimer = null;
const cancelledRequests = new Set();

function throwIfCancelled(requestId) {
  if (!requestId || !cancelledRequests.has(requestId)) return;
  const error = new Error(`请求 ${requestId} 已取消`);
  error.code = 'RUN_INTERRUPTED';
  throw error;
}

// MV3 SW 30s 无活动即休眠，offscreen 长编排期间 SW 一旦休眠会连带丢连接；
// 每 10s 发一条消息重置 SW 空闲计时。
function startKeepAlive() {
  if (keepAliveTimer) return;
  keepAliveTimer = setInterval(() => {
    sendToSw({ kind: 'LENS_HEARTBEAT' }).catch(() => {});
  }, 10000);
}

// Chrome 153 offscreen 文档无 chrome.storage，配置统一向 SW 查询。
async function loadConfig() {
  const resp = await sendToSw({ kind: 'LENS_GET_CONFIG' });
  const cfg = resp || {};
  return {
    url: cfg.devEcho ? DEV_ECHO : cfg.bridgeUrl || DEFAULT_BRIDGE_URL,
    token: cfg.token || 'dev-token',
  };
}

function setStatus(extra = {}) {
  const status = {
    wsOnline: client?.state === 'CONNECTED',
    wsState: client?.state || 'STOPPED',
    updatedAt: new Date().toISOString(),
    ...extra,
  };
  // 失败静默：状态写入不影响主链路。
  sendToSw({ kind: 'LENS_SET_STATUS', status }).catch(() => {});
}

// 统一解包 SW 的 {data} / {error:{code,message}} 响应。
async function sendToSw(message) {
  const resp = await chrome.runtime.sendMessage(message);
  if (resp?.error) {
    const e = new Error(resp.error.message);
    e.code = resp.error.code;
    throw e;
  }
  return resp?.data;
}

// tabId 缺省由 SW 解析当前活动标签（offscreen 无 chrome.tabs）。
async function runCollector(method, args) {
  return sendToSw({ kind: 'LENS_RUN_ON_TAB', method, args });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// 页面重载/导航会销毁 collector 上下文，SW 侧 sendMessage 因此 reject；
// 导航类调用点预期如此，吞掉这类瞬时错误。
async function runCollectorQuiet(method, args) {
  try {
    return await runCollector(method, args);
  } catch (e) {
    return { contextGone: true, code: e.code, message: String(e?.message || e) };
  }
}

// 轮询 collector 直到 fn(结果) 为真（页面重载/d-solo 导航期间会连续失败，忽略）。
async function pollCollector(
  method,
  args,
  fn,
  { timeoutMs = 30000, intervalMs = 100, requestId = null } = {}
) {
  const deadline = Date.now() + timeoutMs;
  let last = null;
  while (Date.now() < deadline) {
    throwIfCancelled(requestId);
    const r = await runCollectorQuiet(method, args);
    last = r;
    if (isDeadCollectorResult(r)) {
      const error = new Error(r.message || '采集标签不是 Grafana 看板');
      error.code = r.code || 'TAB_NOT_ACTIVE';
      throw error;
    }
    if (r && !r.contextGone && fn(r)) return r;
    await sleep(intervalMs);
  }
  const e = new Error(`等待 ${method} 超时（最后结果：${JSON.stringify(last)?.slice(0, 120)}）`);
  e.code = 'RENDER_TIMEOUT';
  throw e;
}

function noDataCapturePayload(step, payload, viewPath) {
  return {
    skipped: true,
    noData: true,
    reason: 'no_data',
    panelMeta: {
      panelId: payload.panelId,
      title: step?.title || '',
      type: step?.type || 'other',
      datasourceType: step?.datasourceType || 'other',
      from: step?.from || payload.from || null,
      to: step?.to || payload.to || null,
      viewPath: viewPath || step?.viewPath || 'spa',
    },
  };
}

async function leavePanelView(viewPath, navArgs) {
  if (viewPath === 'dsolo') {
    await runCollectorQuiet('returnToDashboard', navArgs);
  } else {
    await runCollectorQuiet('exitFullscreen', {});
  }
}

function isNoDataStep(step) {
  return !!(step && (step.noData || step.skipped || step.reason === 'no_data'));
}

// 采集面板：SPA 进视图 → 失败硬重载 SPA 重试 → 再失败走 d-solo；全程对桥表现为一次调用。
async function handleCapturePanel(payload, requestId) {
  const panelId = payload.panelId;
  const navArgs = { panelId, from: payload.from, to: payload.to };
  const pollOptions = { timeoutMs: 30000, requestId };

  throwIfCancelled(requestId);
  // 起始可能在主看板（含重载后）或 d-solo（上一张回退失败）。先等到主看板 SPA 就绪。
  await pollCollector('spaReady', {}, (r) => r.ready === true, pollOptions);

  let step = await runCollector('capturePanel', payload);
  let viewPath;

  if (isNoDataStep(step)) {
    if (step.viewPath !== 'grid') {
      await leavePanelView(step.viewPath || 'spa', navArgs);
    }
    return noDataCapturePayload(step, payload, step.viewPath || 'spa');
  }

  if (step?.ready && step.viewPath === 'grid' && step.crop) {
    throwIfCancelled(requestId);
    try {
      const rawDataUrl = await sendToSw({ kind: 'LENS_CAPTURE' });
      const cropped = await cropJpeg(rawDataUrl, step.crop);
      const img = await compressJpeg(cropped.dataUrl);
      return {
        imageBase64: img.dataUrl.split(',')[1],
        mimeType: 'image/jpeg',
        width: img.width,
        height: img.height,
        sizeBytes: img.bytes,
        panelMeta: {
          panelId,
          title: step?.title || '',
          type: step?.type || 'other',
          datasourceType: step?.datasourceType || 'other',
          from: step?.from || payload.from || null,
          to: step?.to || payload.to || null,
          viewPath: 'grid',
        },
      };
    } catch (e) {
      if (e.code && e.code !== 'CAPTURE_FAILED') throw e;
      step = await runCollector('capturePanel', { ...payload, forceFullscreen: true });
    }
  }

  if (step?.ready) {
    viewPath = step.viewPath;
  } else if (step?.needReload) {
    // 不要 location.replace 硬重载：会丢掉已展开的 row，并在请求未完成时弹出“确认离开”。
    await runCollectorQuiet('exitFullscreen', {});
    await pollCollector('spaReady', {}, (r) => r.ready === true, pollOptions);
    try {
      step = await runCollector('capturePanelRetry', payload);
      viewPath = step.viewPath;
    } catch (e) {
      if (e.code !== 'RENDER_TIMEOUT') throw e;
      step = null;
    }
    if (isNoDataStep(step)) {
      if (step.viewPath !== 'grid') {
        await leavePanelView(step.viewPath || 'spa', navArgs);
      }
      return noDataCapturePayload(step, payload, step.viewPath || 'spa');
    }
  } else {
    const e = new Error(`capturePanel 异常返回：${JSON.stringify(step)?.slice(0, 120)}`);
    e.code = 'CAPTURE_FAILED';
    throw e;
  }

  // SPA 重试仍失败 → d-solo 回退（导航后在新页面轮询就绪）。
  if (!step?.ready) {
    await runCollectorQuiet('goDsolo', navArgs);
    step = await pollCollector(
      'dsoloState',
      navArgs,
      (r) => r.ready === true || r.noData === true,
      pollOptions,
    );
    viewPath = 'dsolo';
    if (isNoDataStep(step)) {
      await leavePanelView('dsolo', navArgs);
      return noDataCapturePayload(step, payload, 'dsolo');
    }
  }

  throwIfCancelled(requestId);
  const rawDataUrl = await sendToSw({ kind: 'LENS_CAPTURE' });
  const img = await compressJpeg(rawDataUrl);

  if (viewPath === 'grid') {
    // stat/gauge 格子裁切不进全屏，保持看板滚动位置。
  } else if (viewPath === 'dsolo') {
    // 回主看板（导航销毁上下文）；不阻塞返回，下一张采集前会先等 spaReady。
    await runCollectorQuiet('returnToDashboard', navArgs);
  } else {
    await runCollectorQuiet('exitFullscreen', {});
  }

  return {
    imageBase64: img.dataUrl.split(',')[1],
    mimeType: 'image/jpeg',
    width: img.width,
    height: img.height,
    sizeBytes: img.bytes,
    panelMeta: {
      panelId,
      title: step?.title || '',
      type: step?.type || 'other',
      datasourceType: step?.datasourceType || 'other',
      from: step?.from || payload.from || null,
      to: step?.to || payload.to || null,
      viewPath,
    },
  };
}

async function handleFrame(frame) {
  try {
    let payload;
    switch (frame.type) {
      case 'get_meta':
        payload = await runCollector('getMeta', {});
        break;
      case 'list_panels':
        payload = await runCollector('listPanels', {});
        break;
      case 'query_datasource':
        payload = await runCollector('queryDatasource', { payload: frame.payload });
        break;
      case 'capture_panel':
        payload = await handleCapturePanel(frame.payload || {}, frame.id);
        break;
      case 'capture_viewport': {
        const rawDataUrl = await sendToSw({ kind: 'LENS_CAPTURE' });
        const img = await compressJpeg(rawDataUrl);
        payload = {
          imageBase64: img.dataUrl.split(',')[1],
          mimeType: 'image/jpeg',
          width: img.width,
          height: img.height,
          sizeBytes: img.bytes,
        };
        break;
      }
      default:
        client.send({
          id: frame.id,
          type: 'error',
          ok: false,
          error: { code: 'CAPTURE_FAILED', message: `不支持的指令 ${frame.type}` },
        });
        return;
    }
    client.send({ id: frame.id, type: 'result', ok: true, payload });
  } catch (e) {
    client.send({
      id: frame.id,
      type: 'error',
      ok: false,
      error: { code: e.code || 'CAPTURE_FAILED', message: String(e?.message || e) },
    });
  }
}

async function start() {
  if (client) client.stop();
  const { url, token } = await loadConfig();
  // 帧串行：capture 编排含页面导航/全屏切换，并发会互相破坏。
  let chain = Promise.resolve();
  client = new WSClient({
    url,
    token,
    onState: (state) => setStatus(),
    onFrame: (frame) => {
      if (!frame.id) return;
      if (frame.type === 'cancel') {
        const targetId = frame.payload?.targetId;
        if (typeof targetId === 'string') cancelledRequests.add(targetId);
        return;
      }
      chain = chain
        .then(() => handleFrame(frame))
        .finally(() => cancelledRequests.delete(frame.id));
    },
  });
  client.start();
  setStatus();
  startKeepAlive();
}

chrome.runtime.onMessage.addListener((msg) => {
  if (msg?.kind === 'LENS_RESTART_WS') start();
});

start();
