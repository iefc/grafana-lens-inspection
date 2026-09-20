// Grafana 空面板判定：No data 是终态，不得当成“还在渲染”去空等。
'use strict';

(function (root) {
  const EMPTY_SELECTORS = [
    '.panel-empty',
    '.datapoints-warning',
    '[data-testid="data-testid No data"]',
    '[class*="PanelDataErrorMessage"]',
  ].join(',');

  const EXACT =
    /^\s*(no data(?: to show|points)?|没有数据|无数据)\s*[.。!！]?\s*$/i;

  function textLooksLikeNoData(text) {
    const normalized = String(text || '').replace(/\s+/g, ' ').trim();
    if (!normalized) return false;
    if (EXACT.test(normalized)) return true;
    if (normalized.length > 200) return false;
    return /(?:^|\s)(no data(?: to show|points)?|没有数据|无数据)\s*[.。!！]?\s*$/i.test(
      normalized,
    );
  }

  function isVisible(el) {
    if (!el || typeof el.getBoundingClientRect !== 'function') return false;
    const rect = el.getBoundingClientRect();
    if (!(rect.width > 0 && rect.height > 0)) return false;
    const style = typeof getComputedStyle === 'function' ? getComputedStyle(el) : null;
    if (style && (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0')) {
      return false;
    }
    return true;
  }

  function noDataVisible(root) {
    if (!root || typeof root.querySelectorAll !== 'function') return false;
    try {
      for (const el of root.querySelectorAll(EMPTY_SELECTORS)) {
        if (isVisible(el)) return true;
      }
    } catch (_err) {
      // 选择器在非 DOM 环境可能抛错。
    }
    const nodes = root.querySelectorAll('div,span,p,h2,h3,h4,h5');
    for (const el of nodes) {
      if (!isVisible(el)) continue;
      const text = String(el.innerText || el.textContent || '').trim();
      if (text && text.length <= 80 && textLooksLikeNoData(text)) return true;
    }
    return textLooksLikeNoData(root.innerText || root.textContent || '');
  }

  const api = { textLooksLikeNoData, noDataVisible, EMPTY_SELECTORS };
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  }
  root.GrafanaLensNoData = api;
})(typeof globalThis !== 'undefined' ? globalThis : self);
