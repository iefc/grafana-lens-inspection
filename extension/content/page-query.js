// page-query（MAIN world）：只在页面上下文执行同源 /api/ds/query。
// 与 collector 用一次性 nonce 配对 postMessage；无 nonce / 非同窗来源一律丢弃。
'use strict';

(function () {
  if (window.__lensPageQueryInstalled) return;
  window.__lensPageQueryInstalled = true;

  window.postMessage({ __lens: 'page-query-ready' }, location.origin);

  window.addEventListener('message', async (event) => {
    if (event.source !== window) return;
    const msg = event.data;
    if (!msg || msg.__lens !== 'query' || typeof msg.nonce !== 'string') return;

    const reply = (payload) => {
      window.postMessage({ __lens: 'result', nonce: msg.nonce, ...payload }, location.origin);
    };

    try {
      const sub = (window.grafanaBootData?.settings?.appSubUrl || '').replace(/\/+$/, '');
      const resp = await fetch(sub + '/api/ds/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        credentials: 'include',
        body: JSON.stringify(msg.payload),
      });
      if (resp.status === 401 || resp.status === 403) {
        reply({ error: { code: 'AUTH_REQUIRED', message: `ds/query 返回 ${resp.status}` } });
        return;
      }
      if (!resp.ok) {
        reply({ error: { code: 'CAPTURE_FAILED', message: `ds/query 返回 ${resp.status}` } });
        return;
      }
      reply({ data: await resp.json() });
    } catch (e) {
      reply({ error: { code: 'CAPTURE_FAILED', message: String(e?.message || e) } });
    }
  });
})();
