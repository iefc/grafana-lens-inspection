const $ = (id) => document.getElementById(id);
const DEFAULT_BRIDGE_URL = 'ws://127.0.0.1:9527/ws';

let currentOrigin = null;

function originPattern(origin) {
  return `${origin}/*`;
}

function normalizeBridgeUrl(value) {
  const url = new URL(value || DEFAULT_BRIDGE_URL);
  if (!['ws:', 'wss:'].includes(url.protocol)) {
    throw new Error('桥地址必须以 ws:// 或 wss:// 开头');
  }
  if (!['127.0.0.1', 'localhost'].includes(url.hostname)) {
    throw new Error('一键配对仅支持本机 127.0.0.1 或 localhost 桥');
  }
  url.pathname = '/ws';
  url.search = '';
  url.hash = '';
  return url.toString();
}

function pairUrlFromBridgeUrl(bridgeUrl) {
  const url = new URL(normalizeBridgeUrl(bridgeUrl));
  url.protocol = url.protocol === 'wss:' ? 'https:' : 'http:';
  url.pathname = '/pair';
  return url.toString();
}

async function getActiveOrigin() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.url) return null;
  let u;
  try {
    u = new URL(tab.url);
  } catch {
    return null;
  }
  return u.protocol === 'http:' || u.protocol === 'https:' ? u.origin : null;
}

async function isGranted(origin) {
  return chrome.permissions.contains({ origins: [originPattern(origin)] });
}

async function persistGranted() {
  const result = await chrome.permissions.getAll();
  const origins = (result.origins || []).filter(
    (origin) => origin.startsWith('http://') || origin.startsWith('https://')
  );
  await chrome.storage.local.set({ 'lens.grantedOrigins': origins });
}

function setPairMessage(text, color = '#555') {
  $('pairMessage').textContent = text;
  $('pairMessage').style.color = color;
}

function renderBridgeStatus({ token, status } = {}) {
  const target = $('bridgeStatus');
  if (!token) {
    target.textContent = '未配对';
    target.style.color = '#b00';
    return;
  }
  const state = status?.wsState || 'CONNECTING';
  const labels = {
    CONNECTED: '在线',
    CONNECTING: '连接中',
    RECONNECTING: '重连中',
    STOPPED: '已停止',
  };
  const updated = status?.updatedAt ? `（${new Date(status.updatedAt).toLocaleTimeString()}）` : '';
  target.textContent = `${labels[state] || state}${updated}`;
  target.style.color = state === 'CONNECTED' ? '#0a0' : '#a60';
}

async function render() {
  currentOrigin = await getActiveOrigin();
  const status = $('status');
  const grantBtn = $('grant');
  const revokeBtn = $('revoke');

  if (!currentOrigin) {
    status.textContent = '请在 http(s) 的 Grafana 页面打开本弹窗';
    status.style.color = '#b00';
    grantBtn.disabled = true;
    revokeBtn.disabled = true;
  } else {
    $('origin').textContent = currentOrigin;
    const granted = await isGranted(currentOrigin);
    if (granted) {
      status.textContent = '已授权（采集可用）';
      status.style.color = '#0a0';
      grantBtn.disabled = true;
      revokeBtn.disabled = false;
    } else {
      status.textContent = '未授权';
      status.style.color = '#b00';
      grantBtn.disabled = false;
      revokeBtn.disabled = true;
    }
  }

  const data = await chrome.storage.local.get(['lens.token', 'lens.status']);
  renderBridgeStatus({ token: data['lens.token'], status: data['lens.status'] });
}

$('grant').addEventListener('click', async () => {
  if (!currentOrigin) return;
  const ok = await chrome.permissions.request({
    origins: [originPattern(currentOrigin)],
  });
  if (ok) {
    await persistGranted();
    await chrome.runtime.sendMessage({ kind: 'LENS_REGISTER_ORIGIN', origin: currentOrigin });
  }
  await render();
});

$('revoke').addEventListener('click', async () => {
  if (!currentOrigin) return;
  await chrome.permissions.remove({ origins: [originPattern(currentOrigin)] });
  await persistGranted();
  try {
    await chrome.scripting.unregisterContentScripts({
      ids: ['lens-collector-' + currentOrigin],
    });
  } catch {
    // 未注册过，忽略。
  }
  await render();
});

$('pair').addEventListener('click', async () => {
  const button = $('pair');
  button.disabled = true;
  setPairMessage('正在请求本地桥配对…');
  try {
    const data = await chrome.storage.local.get('lens.bridgeUrl');
    const bridgeUrl = normalizeBridgeUrl(data['lens.bridgeUrl']);
    const response = await fetch(pairUrlFromBridgeUrl(bridgeUrl), { method: 'POST' });
    if (response.status === 403) {
      throw new Error('配对窗口未开启、已过期或已被使用；请重新运行 grafana-lens-bridge pair');
    }
    if (!response.ok) throw new Error(`本地桥返回 HTTP ${response.status}`);
    const payload = await response.json();
    if (!payload || typeof payload.token !== 'string' || payload.token.length === 0) {
      throw new Error('本地桥未返回有效配对 token');
    }
    await chrome.storage.local.set({
      'lens.bridgeUrl': bridgeUrl,
      'lens.token': payload.token,
    });
    chrome.runtime.sendMessage({ kind: 'LENS_RESTART_WS' }).catch(() => {});
    setPairMessage('配对成功，正在连接本地桥。', '#0a0');
  } catch (error) {
    setPairMessage(String(error?.message || error), '#b00');
  } finally {
    button.disabled = false;
    await render();
  }
});

chrome.permissions.onAdded.addListener(render);
chrome.permissions.onRemoved.addListener(render);
chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName === 'local' && (changes['lens.token'] || changes['lens.status'])) render();
});

render();
