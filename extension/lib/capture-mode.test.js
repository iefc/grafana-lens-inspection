'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {
  usesGridCapture,
  isViewMenuLabel,
  mapCropToImage,
  clearViewPanelHref,
  VIEW_MENU_SELECTOR,
  BACK_FROM_VIEW_SELECTOR,
} = require('./capture-mode.js');

test('only stat and gauge skip fullscreen; trend and table stay fullscreen', () => {
  assert.equal(usesGridCapture('stat'), true);
  assert.equal(usesGridCapture('gauge'), true);
  assert.equal(usesGridCapture('timeseries'), false);
  assert.equal(usesGridCapture('graph'), false);
  assert.equal(usesGridCapture('table'), false);
  assert.equal(usesGridCapture('piechart'), false);
  assert.equal(usesGridCapture('bargauge'), false);
  assert.equal(usesGridCapture('logs'), false);
  assert.equal(usesGridCapture('text'), false);
  assert.equal(usesGridCapture('row'), false);
});

test('Grafana 8.5 View / Go Back labels match the real panel menu', () => {
  assert.equal(isViewMenuLabel('View'), true);
  assert.equal(isViewMenuLabel('查看'), true);
  assert.equal(isViewMenuLabel('Explore'), false);
  assert.equal(VIEW_MENU_SELECTOR.includes('Panel header item View'), true);
  assert.equal(BACK_FROM_VIEW_SELECTOR.includes('Go Back'), true);
});

test('clearViewPanelHref drops viewPanel without changing the dashboard path', () => {
  const next = clearViewPanelHref(
    'https://epm.example/grafana/d/uid/slug?orgId=1&viewPanel=23&from=now-1h',
  );
  assert.equal(next.includes('viewPanel'), false);
  assert.equal(next.includes('/grafana/d/uid/slug'), true);
  assert.equal(next.includes('orgId=1'), true);
});

test('mapCropToImage scales CSS viewport box onto screenshot pixels', () => {
  const box = mapCropToImage(
    { left: 100, top: 50, width: 200, height: 80, viewportWidth: 1000, viewportHeight: 500 },
    2000,
    1000,
  );
  assert.deepEqual(box, { sx: 200, sy: 100, sw: 400, sh: 160 });
});

test('mapCropToImage clamps to image bounds', () => {
  const box = mapCropToImage(
    { left: 900, top: 400, width: 200, height: 200, viewportWidth: 1000, viewportHeight: 500 },
    1000,
    500,
  );
  assert.equal(box.sx + box.sw <= 1000, true);
  assert.equal(box.sy + box.sh <= 500, true);
  assert.equal(box.sw >= 1, true);
  assert.equal(box.sh >= 1, true);
});
