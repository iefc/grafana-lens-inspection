// Grafana Lens service worker。
// 职责：域名授权后注册/注入 collector；LENS_CALL 路由到指定标签页。
// 不维持 WS、不做压缩（offscreen 负责，后续任务接入）。

import { pickGrafanaTabId } from './lib/tab-target.js';

const COLLECTOR_SCRIPTS = ['lib/nodata.js', 'lib/capture-mode.js', 'content/collector.js'];

const OFFSCREEN_PATH = 'offscreen/offscreen.html';

chrome.runtime.onInstalled.addListener(() => {
  console.info('[lens] installed', chrome.runtime.getURL(''));
  boot();
});

chrome.runtime.onStartup.addListener(() => {
  boot();
});

// SW 每次启动（含 developerPrivate.reload 后唤醒）都执行一次顶层；
// onInstalled/onStartup 在 reload 时不保证触发：恢复注册 + 重建 offscreen 都不能只挂事件。
boot().catch((e) => console.warn('[lens] boot failed', e));

// 确保 offscreen 存在即可：新建文档加载时自行 start()；
// SW 休眠后唤醒时，旧 offscreen 的 WS 仍在，无需重发 RESTART（重发会产生第二条连接）。
async function boot() {
  await restoreRegisteredOrigins();
  await ensureOffscreen();
}

// developerPrivate.reload 会清空动态注册的 content scripts；
// storage 也可能被 Chrome 清掉，此时从实际仍持有的 optional host 权限回填。
async function restoreRegisteredOrigins() {
  let data = await chrome.storage.local.get('lens.grantedOrigins');
  let patterns = data['lens.grantedOrigins'] || [];
  if (patterns.length === 0) {
    const all = await chrome.permissions.getAll();
    // 仅回填 optional host（http/https），manifest 内置的 127/localhost 不算。
    patterns = (all.origins || []).filter(
      (o) => /^https?:\/\//.test(o) && !/^https?:\/\/(127\.0\.0\.1|localhost)\//.test(o)
    );
    if (patterns.length > 0) {
      await chrome.storage.local.set({ 'lens.grantedOrigins': patterns });
    }
  }
  const existing = new Set(
    (await chrome.scripting.getRegisteredContentScripts()).map((s) => s.id)
  );
  for (const pattern of patterns) {
    // storage 中是 match pattern（https://host/*），id 约定用裸 origin。
    const origin = pattern.replace(/\/\*$/, '');
    const id = 'lens-collector-' + origin;
    if (existing.has(id)) continue;
    try {
      await chrome.scripting.registerContentScripts([
        { id, matches: [origin + '/*'], js: COLLECTOR_SCRIPTS, runAt: 'document_idle' },
      ]);
    } catch (e) {
      console.warn('[lens] restore register failed', pattern, e);
    }
  }
}

async function normalizeBridgeUrl(value) {
  const url = new URL(value || 'ws://127.0.0.1:9527/ws');
  if (!['ws:', 'wss:'].includes(url.protocol)) return 'ws://127.0.0.1:9527/ws';
  if (!['127.0.0.1', 'localhost'].includes(url.hostname)) return 'ws://127.0.0.1:9527/ws';
  url.pathname = '/ws';
  url.search = '';
  url.hash = '';
  return url.toString();
}

async function loadWsConfig() {
  const data = await chrome.storage.local.get(['lens.bridgeUrl', 'lens.token', 'lens.devEcho']);
  return {
    bridgeUrl: await normalizeBridgeUrl(data['lens.bridgeUrl']),
    token: data['lens.token'] || 'dev-token',
    devEcho: !!data['lens.devEcho'],
  };
}

// offscreen 查询 WS 配置（offscreen 无 chrome.storage）。
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  // offscreen 保活心跳（MV3 30s 空闲休眠）。
  if (msg?.kind === 'LENS_HEARTBEAT') {
    sendResponse({ data: { ts: Date.now() } });
    return false;
  }

  if (msg?.kind === 'LENS_GET_CONFIG') {
    loadWsConfig()
      .then((config) => sendResponse({ data: config }))
      .catch((e) => sendResponse({ error: { code: 'CAPTURE_FAILED', message: String(e) } }));
    return true;
  }

  // offscreen → 写状态（offscreen 无 chrome.storage）。
  if (msg?.kind === 'LENS_SET_STATUS') {
    chrome.storage.local
      .set({ 'lens.status': msg.status })
      .then(() => sendResponse({ data: true }))
      .catch((e) => sendResponse({ error: { code: 'CAPTURE_FAILED', message: String(e) } }));
    return true;
  }
  return false;
});

// offscreen 会被 Chrome 在空闲时关闭；用到时确保存在。
async function ensureOffscreen() {
  const existing = await chrome.runtime.getContexts({
    contextTypes: ['OFFSCREEN_DOCUMENT'],
  });
  if (existing.length > 0) return;
  await chrome.offscreen.createDocument({
    url: OFFSCREEN_PATH,
    reasons: ['DOM_PARSER', 'WORKERS'],
    justification: 'JPEG 压缩与本地桥 WebSocket 长连接',
  });
}

// popup 授权成功后调用：为该 origin 持久注册 collector（后续刷新自动注入）。
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.kind === 'LENS_REGISTER_ORIGIN' && typeof msg.origin === 'string') {
    chrome.scripting
      .registerContentScripts([
        {
          id: 'lens-collector-' + msg.origin,
          matches: [msg.origin + '/*'],
          js: COLLECTOR_SCRIPTS,
          runAt: 'document_idle',
        },
      ])
      .then(() => sendResponse({ data: true }))
      .catch((e) => sendResponse({ error: { code: 'CAPTURE_FAILED', message: String(e) } }));
    return true;
  }

  // 截图请求（offscreen 发起）：排队串行截图，返回 dataUrl。
  if (msg?.kind === 'LENS_CAPTURE') {
    resolveTab(msg.tabId)
      .then((tabId) => enqueueCapture(tabId))
      .then((dataUrl) => sendResponse({ data: dataUrl }))
      .catch((e) =>
        sendResponse({ error: { code: e.code || 'CAPTURE_FAILED', message: String(e.message || e) } })
      );
    return true;
  }

  // offscreen/popup → 指定标签页执行 collector 方法（tabId 缺省取活动标签）。
  if (msg?.kind === 'LENS_RUN_ON_TAB') {
    resolveTab(msg.tabId)
      .then((tabId) => runOnTab(tabId, msg.method, msg.args || {}))
      .then((data) => sendResponse({ data }))
      .catch((e) =>
        sendResponse({ error: { code: e.code || 'CAPTURE_FAILED', message: String(e.message || e) } })
      );
    return true;
  }
  return false;
});

// 优先钉在 Grafana 看板标签；file:// 报告页不能注入 collector。
async function resolveTab(tabId) {
  const tabs = await chrome.tabs.query({});
  const picked = pickGrafanaTabId(tabs, Number.isInteger(tabId) ? tabId : undefined);
  if (picked != null) return picked;
  const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
  const url = active?.url || '';
  const error = new Error(
    `当前标签不是 Grafana 看板（${url}）。请把 Grafana 看板放到前台后再巡检，不要打开本地 report.html。`
  );
  error.code = 'TAB_NOT_ACTIVE';
  throw error;
}

// ---- 截图：全局互斥，一次只截一张（captureVisibleTab 互相干扰，§6.5）----

let captureChain = Promise.resolve();

async function activateTab(tabId) {
  const tab = await chrome.tabs.get(tabId);
  // 多窗口时仅 tabs.update active 不够：窗口非焦点会让 captureVisibleTab 报
  // “'activeTab' permission is required”，必须先把窗口提到焦点。
  await chrome.windows.update(tab.windowId, { focused: true });
  await chrome.tabs.update(tabId, { active: true });
}

async function captureVisible(tabId) {
  const tab = await chrome.tabs.get(tabId);
  const win = await chrome.windows.get(tab.windowId);
  const alreadyFront = tab.active && win.focused;
  if (!alreadyFront) {
    await activateTab(tabId);
    await new Promise((r) => setTimeout(r, 200));
  }
  try {
    // dataUrl（image/jpeg）；窗口最小化/标签不可见时 Chrome 抛错。
    return await chrome.tabs.captureVisibleTab(undefined, {
      format: 'jpeg',
      quality: 90,
    });
  } catch (e) {
    const msg = String(e?.message || e);
    const code = /active|visible|minimize/i.test(msg) ? 'TAB_NOT_ACTIVE' : 'CAPTURE_FAILED';
    const err = new Error(msg);
    err.code = code;
    throw err;
  }
}

// 排队执行，保证并发 capture 请求物理串行。
function enqueueCapture(tabId) {
  const run = captureChain.then(() => captureVisible(tabId));
  captureChain = run.then(
    () => undefined,
    () => undefined
  );
  return run;
}

async function ensureInjected(tabId) {
  // 已注册脚本会自动注入；executeScript 兜底处理授权后未刷新的标签。
  await chrome.scripting.executeScript({ files: COLLECTOR_SCRIPTS, target: { tabId } });
}

async function runOnTab(tabId, method, args) {
  await ensureInjected(tabId);
  const resp = await chrome.tabs.sendMessage(tabId, { kind: 'LENS_CALL', method, args });
  if (resp?.error) {
    const e = new Error(resp.error.message);
    e.code = resp.error.code;
    throw e;
  }
  return resp?.data;
}
