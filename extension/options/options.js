// 选项页：保留手动 token 路径，并确保保存后立即使用新配置重连。
const $ = (id) => document.getElementById(id);
const DEFAULT_BRIDGE_URL = 'ws://127.0.0.1:9527/ws';

function normalizeBridgeUrl(value) {
  const url = new URL(value || DEFAULT_BRIDGE_URL);
  if (!['ws:', 'wss:'].includes(url.protocol)) {
    throw new Error('桥地址必须以 ws:// 或 wss:// 开头');
  }
  if (!['127.0.0.1', 'localhost'].includes(url.hostname)) {
    throw new Error('桥地址仅支持本机 127.0.0.1 或 localhost');
  }
  url.pathname = '/ws';
  url.search = '';
  url.hash = '';
  return url.toString();
}

async function load() {
  const data = await chrome.storage.local.get(['lens.bridgeUrl', 'lens.token']);
  $('bridgeUrl').value = normalizeBridgeUrl(data['lens.bridgeUrl']);
  if (data['lens.token']) $('token').value = data['lens.token'];
}

$('save').addEventListener('click', async () => {
  try {
    const bridgeUrl = normalizeBridgeUrl($('bridgeUrl').value);
    await chrome.storage.local.set({
      'lens.bridgeUrl': bridgeUrl,
      'lens.token': $('token').value.trim(),
    });
    chrome.runtime.sendMessage({ kind: 'LENS_RESTART_WS' }).catch(() => {});
    $('bridgeUrl').value = bridgeUrl;
    $('saved').textContent = '已保存，正在重连';
  } catch (error) {
    $('saved').textContent = String(error?.message || error);
  }
  setTimeout(() => ($('saved').textContent = ''), 2000);
});

load().catch((error) => {
  $('saved').textContent = String(error?.message || error);
});
