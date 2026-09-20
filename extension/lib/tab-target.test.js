'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

test('only Grafana dashboard and d-solo URLs are inspectable', async () => {
  const { isGrafanaInspectUrl } = await import('./tab-target.js');
  assert.equal(
    isGrafanaInspectUrl('https://epm.example/grafana/d/uid/slug?orgId=1'),
    true,
  );
  assert.equal(
    isGrafanaInspectUrl('https://epm.example/grafana/d-solo/uid/slug?panelId=3'),
    true,
  );
  assert.equal(
    isGrafanaInspectUrl('file:///Users/fei/report.html'),
    false,
  );
  assert.equal(isGrafanaInspectUrl('https://example.com/docs'), false);
});

test('dead collector results from file:// fail immediately', async () => {
  const { isDeadCollectorResult } = await import('./tab-target.js');
  assert.equal(
    isDeadCollectorResult({
      contextGone: true,
      code: 'CAPTURE_FAILED',
      message: 'Cannot access contents of url "file:///Users/fei/report.html"',
    }),
    true,
  );
  assert.equal(
    isDeadCollectorResult({ contextGone: true, message: 'tab reloading' }),
    false,
  );
});

test('pickGrafanaTabId prefers Grafana even if file report is active', async () => {
  const { pickGrafanaTabId } = await import('./tab-target.js');
  const tabs = [
    { id: 1, url: 'file:///tmp/report.html', active: true, lastAccessed: 9 },
    {
      id: 2,
      url: 'https://epm.example/grafana/d/uid/slug',
      active: false,
      lastAccessed: 8,
    },
  ];
  assert.equal(pickGrafanaTabId(tabs), 2);
  assert.equal(pickGrafanaTabId(tabs, 2), 2);
  assert.equal(pickGrafanaTabId([{ id: 1, url: 'file:///tmp/a.html' }]), null);
});
