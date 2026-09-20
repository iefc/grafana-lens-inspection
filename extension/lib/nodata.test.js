'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { textLooksLikeNoData } = require('./nodata.js');

test('exact Grafana empty copy is no data', () => {
  assert.equal(textLooksLikeNoData('No data'), true);
  assert.equal(textLooksLikeNoData('No data.'), true);
  assert.equal(textLooksLikeNoData('No datapoints'), true);
  assert.equal(textLooksLikeNoData('No data to show'), true);
  assert.equal(textLooksLikeNoData('没有数据'), true);
  assert.equal(textLooksLikeNoData('无数据'), true);
});

test('panel title plus No data is no data', () => {
  assert.equal(textLooksLikeNoData('CPU Usage No data'), true);
});

test('real content is not no data', () => {
  assert.equal(textLooksLikeNoData(''), false);
  assert.equal(textLooksLikeNoData('CPU 92%'), false);
  assert.equal(
    textLooksLikeNoData('error: connection refused while querying prometheus'),
    false,
  );
});
