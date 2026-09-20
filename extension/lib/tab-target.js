// Grafana 看板标签识别：巡检不能打在 file:// 报告页或其它非看板标签上。
'use strict';

export function isGrafanaInspectUrl(url) {
  if (!url || typeof url !== 'string') return false;
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return false;
    return /\/d(?:-solo)?\/[^/?#]+/.test(parsed.pathname) || /\/dashboard\//.test(parsed.pathname);
  } catch (_error) {
    return false;
  }
}

export function isDeadCollectorResult(result) {
  if (!result || !result.contextGone) return false;
  const message = String(result.message || '');
  if (/file:\/\//i.test(message) || /chrome:\/\//i.test(message)) return true;
  if (/Cannot access contents of url/i.test(message) && !/https?:\/\//i.test(message)) return true;
  return false;
}

export function pickGrafanaTabId(tabs, preferredId) {
  const list = Array.isArray(tabs) ? tabs : [];
  const grafana = list.filter((tab) => tab && isGrafanaInspectUrl(tab.url));
  if (Number.isInteger(preferredId)) {
    const match = grafana.find((tab) => tab.id === preferredId);
    if (match) return match.id;
  }
  const active = grafana.find((tab) => tab.active);
  if (active) return active.id;
  grafana.sort((a, b) => (b.lastAccessed || 0) - (a.lastAccessed || 0));
  return grafana.length ? grafana[0].id : null;
}
