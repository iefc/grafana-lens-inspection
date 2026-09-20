// stat/gauge 走看板格子裁切；timeseries/table 等点 Grafana View 全屏（格子太挤看不清）。
'use strict';

(function (root) {
  const GRID_CAPTURE_TYPES = { stat: true, gauge: true };
  // Grafana 8.5.9：PanelHeaderMenuItem / PageToolbar BackButton。
  const VIEW_MENU_SELECTOR =
    '[aria-label="Panel header item View"], [aria-label="Panel header item 查看"]';
  const BACK_FROM_VIEW_SELECTOR =
    'button[aria-label="Go Back"], a[aria-label="Go Back"], button[aria-label="Go back to dashboard"], a[aria-label="Go back to dashboard"]';
  const PANEL_HEADER_SELECTOR = '[data-testid^="Panel header"], .panel-title, .panel-header';

  function usesGridCapture(type) {
    return !!GRID_CAPTURE_TYPES[String(type || '')];
  }

  function isViewMenuLabel(text) {
    return /^(view|查看)$/i.test(String(text || '').trim());
  }

  function clearViewPanelHref(href) {
    const u = new URL(href, 'https://grafana.invalid');
    u.searchParams.delete('viewPanel');
    return u.pathname + u.search + u.hash;
  }

  function mapCropToImage(crop, imageWidth, imageHeight) {
    const vw = Number(crop && crop.viewportWidth) || 1;
    const vh = Number(crop && crop.viewportHeight) || 1;
    const imgW = Math.max(1, Math.round(Number(imageWidth) || 0));
    const imgH = Math.max(1, Math.round(Number(imageHeight) || 0));
    let sx = Math.round((Number(crop && crop.left) || 0) * imgW / vw);
    let sy = Math.round((Number(crop && crop.top) || 0) * imgH / vh);
    let sw = Math.round((Number(crop && crop.width) || 0) * imgW / vw);
    let sh = Math.round((Number(crop && crop.height) || 0) * imgH / vh);
    sx = Math.min(Math.max(0, sx), imgW - 1);
    sy = Math.min(Math.max(0, sy), imgH - 1);
    sw = Math.max(1, Math.min(sw, imgW - sx));
    sh = Math.max(1, Math.min(sh, imgH - sy));
    return { sx, sy, sw, sh };
  }

  const api = {
    usesGridCapture,
    isViewMenuLabel,
    mapCropToImage,
    clearViewPanelHref,
    GRID_CAPTURE_TYPES,
    VIEW_MENU_SELECTOR,
    BACK_FROM_VIEW_SELECTOR,
    PANEL_HEADER_SELECTOR,
  };
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  }
  root.GrafanaLensCaptureMode = api;
})(typeof globalThis !== 'undefined' ? globalThis : self);
