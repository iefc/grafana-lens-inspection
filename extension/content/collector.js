// Grafana Lens collector（ISOLATED world，注入到已授权的 Grafana 看板页）。
// 监听 {kind:'LENS_CALL', method, args}，返回 {data} 或 {error:{code,message}}。
'use strict';

(function () {
  if (window.__grafanaLensInstalled) return;
  window.__grafanaLensInstalled = true;

  // 与桥错误码目录（bridge errors.py）保持一致的字符串。
  const ERR = {
    NOT_ON_DASHBOARD: 'NOT_ON_DASHBOARD',
    AUTH_REQUIRED: 'AUTH_REQUIRED',
    PANEL_NOT_FOUND: 'PANEL_NOT_FOUND',
    RENDER_TIMEOUT: 'RENDER_TIMEOUT',
    TAB_NOT_ACTIVE: 'TAB_NOT_ACTIVE',
    CAPTURE_FAILED: 'CAPTURE_FAILED',
    EVIDENCE_UNSUPPORTED: 'EVIDENCE_UNSUPPORTED',
  };

  function fail(code, message) {
    const e = new Error(message || code);
    e.code = code;
    throw e;
  }

  // Grafana 可部署在子路径下（如 /grafana）；appSubUrl 为官方暴露的前缀（可能为空）。
  function appSubUrl() {
    const fromBoot = window.grafanaBootData?.settings?.appSubUrl;
    if (fromBoot) return fromBoot.replace(/\/+$/, '');
    // 兜底：从路径中 /d/ 之前的部分推断；非子路径部署时为空串。
    const i = location.pathname.indexOf('/d/');
    return i > 0 ? location.pathname.slice(0, i) : '';
  }

  // /d/<uid> 与 /d-solo/<uid> 均为已授权 Grafana 页（含子路径前缀）。
  function dashboardUidFromUrl() {
    const m = location.pathname.match(/\/d(?:-solo)?\/([^/]+)/);
    return m ? decodeURIComponent(m[1]) : null;
  }

  function grafanaVersion() {
    return window.grafanaBootData?.settings?.buildInfo?.version || null;
  }

  async function fetchDashboard(uid) {
    const resp = await fetch(`${appSubUrl()}/api/dashboards/uid/${encodeURIComponent(uid)}`, {
      headers: { Accept: 'application/json' },
      credentials: 'include',
    });
    if (resp.status === 401 || resp.status === 403) {
      fail(ERR.AUTH_REQUIRED, `看板 API 返回 ${resp.status}`);
    }
    if (!resp.ok) fail(ERR.NOT_ON_DASHBOARD, `看板 API 返回 ${resp.status}`);
    return (await resp.json()).dashboard;
  }

  // datasource 可能是 {type,uid}（uid 可能是 ${DS_XXX} 变量）或字符串。
  function datasourceType(panel) {
    const ds = panel.datasource;
    if (ds && typeof ds === 'object' && ds.type) return ds.type;
    const t = panel.targets?.find((x) => x.datasource && typeof x.datasource === 'object');
    return t ? t.datasource.type : null;
  }

  // 摊平：collapsed row 的子面板内嵌 row.panels；展开 row 的子面板在顶层紧随其后。
  function flattenPanels(panels) {
    const out = [];
    let currentRow = null;
    for (const p of panels || []) {
      if (p.type === 'row') {
        currentRow = { title: p.title, collapsed: p.collapsed !== false };
        for (const child of p.panels || []) {
          out.push({ panel: child, rowTitle: p.title, collapsed: currentRow.collapsed });
        }
      } else {
        out.push({
          panel: p,
          rowTitle: currentRow ? currentRow.title : null,
          collapsed: currentRow ? currentRow.collapsed : false,
        });
      }
    }
    return out;
  }

  function timeRange(dash) {
    const params = new URLSearchParams(location.search);
    return {
      from: params.get('from') || dash.time?.from || null,
      to: params.get('to') || dash.time?.to || null,
    };
  }

  function datasourceVariables(dash) {
    const values = {};
    for (const variable of dash.templating?.list || []) {
      if (variable.type !== 'datasource' || !variable.name) continue;
      const current = variable.current || {};
      values[variable.name] = current.value || current.text || null;
    }
    return values;
  }

  async function getMeta() {
    const uid = dashboardUidFromUrl();
    if (!uid) fail(ERR.NOT_ON_DASHBOARD, location.pathname);
    const dash = await fetchDashboard(uid);
    const rows = flattenPanels(dash.panels).filter(
      (x) => x.panel.type !== 'text'
    );
    const types = new Set();
    for (const x of rows) {
      const t = datasourceType(x.panel);
      if (t) types.add(t);
    }
    return {
      uid,
      title: dash.title || null,
      url: location.origin + location.pathname,
      ...timeRange(dash),
      panelCount: rows.length,
      grafanaVersion: grafanaVersion(),
      datasourceTypes: [...types],
    };
  }

  function panelDatasource(panel) {
    const direct = panel.datasource;
    if (direct && typeof direct === 'object') return direct;
    const target = panel.targets?.find((item) => item.datasource && typeof item.datasource === 'object');
    return target?.datasource || null;
  }

  function analysisTargets(panel) {
    return (panel.targets || [])
      .filter((target) => target.hide !== true && typeof target.expr === 'string' && target.expr.trim())
      .map((target) => ({
        refId: target.refId || 'A',
        expr: target.expr,
        format: target.format || 'time_series',
        instant: target.instant === true,
        datasource: target.datasource || panelDatasource(panel),
        interval: target.interval,
        intervalFactor: target.intervalFactor,
        legendFormat: target.legendFormat,
      }));
  }

  async function listPanels() {
    const uid = dashboardUidFromUrl();
    if (!uid) fail(ERR.NOT_ON_DASHBOARD, location.pathname);
    const dash = await fetchDashboard(uid);
    const range = timeRange(dash);
    const variables = datasourceVariables(dash);
    return flattenPanels(dash.panels)
      .filter((x) => x.panel.type !== 'text')
      .map((x) => ({
        panelId: x.panel.id,
        title: x.panel.title || '',
        type: x.panel.type,
        rowTitle: x.rowTitle,
        collapsed: x.collapsed,
        datasourceType: datasourceType(x.panel) || 'other',
        datasource: panelDatasource(x.panel),
        targets: analysisTargets(x.panel),
        datasourceVariables: variables,
        from: range.from,
        to: range.to,
      }));
  }

  // ---- 视图进入（§3.2 实测口径）----

  function currentViewPanel() {
    return new URLSearchParams(location.search).get('viewPanel');
  }

  // 必须在“无 viewPanel 的干净 SPA”态进入；硬导航带 viewPanel 不会挂载。
  function assertCleanSpa() {
    if (currentViewPanel() !== null) {
      fail(ERR.RENDER_TIMEOUT, '当前不是干净 SPA（URL 已带 viewPanel），需重载恢复');
    }
  }

  function captureMode() {
    return globalThis.GrafanaLensCaptureMode || {};
  }

  function nativeClick(el) {
    if (!el) return;
    const r = el.getBoundingClientRect();
    const x = Math.floor(r.left + Math.max(1, r.width / 2));
    const y = Math.floor(r.top + Math.min(10, Math.max(1, r.height / 2)));
    const down = {
      bubbles: true,
      cancelable: true,
      composed: true,
      view: window,
      clientX: x,
      clientY: y,
      screenX: x,
      screenY: y,
      button: 0,
      buttons: 1,
    };
    const up = { ...down, buttons: 0 };
    el.dispatchEvent(new MouseEvent('pointerdown', down));
    el.dispatchEvent(new MouseEvent('mousedown', down));
    el.dispatchEvent(new MouseEvent('pointerup', up));
    el.dispatchEvent(new MouseEvent('mouseup', up));
    el.dispatchEvent(new MouseEvent('click', up));
  }

  function isViewMenuLabel(text) {
    const api = captureMode();
    if (typeof api.isViewMenuLabel === 'function') return api.isViewMenuLabel(text);
    return /^(view|查看)$/i.test(String(text || '').trim());
  }

  function findViewMenuItem() {
    const sel = captureMode().VIEW_MENU_SELECTOR
      || '[aria-label="Panel header item View"], [aria-label="Panel header item 查看"]';
    const labeled = document.querySelector(sel);
    if (labeled) return labeled;
    const menus = [...document.querySelectorAll('.dropdown-menu, [role="menu"]')];
    for (let i = menus.length - 1; i >= 0; i--) {
      const item = [...menus[i].querySelectorAll('a, button, [role="menuitem"]')].find((node) =>
        isViewMenuLabel(node.textContent),
      );
      if (item) return item;
    }
    return null;
  }

  async function waitForFullscreen(panelId, timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (fullscreenGrid(panelId)) return true;
      await sleep(40);
    }
    return false;
  }

  // 点 Grafana 自己的 View（locationService.partial），沿用已加载数据。
  // 自己 pushState+popstate 会被当成路由 POP，面板重挂载才会打查询。
  async function clickNativeView(panelId) {
    const el = gridItem(panelId);
    if (!el) return false;
    el.scrollIntoView({ block: 'center', inline: 'nearest' });
    await sleep(50);

    const headerSel = captureMode().PANEL_HEADER_SELECTOR
      || '[data-testid^="Panel header"], .panel-title, .panel-header';
    const header = el.querySelector(headerSel);
    if (!header) return false;
    nativeClick(header);
    await sleep(80);
    let item = findViewMenuItem();
    if (!item) {
      nativeClick(header);
      await sleep(80);
      item = findViewMenuItem();
    }
    if (!item) return false;
    nativeClick(item);
    return waitForFullscreen(panelId, 2500);
  }

  async function enterViewPanel(panelId, found) {
    if (currentViewPanel() !== null || document.querySelector('.react-grid-item--fullscreen')) {
      await exitFullscreen();
    }
    if (found) await ensureRowExpanded(found, panelId);
    if (await clickNativeView(panelId)) return;
    if (currentViewPanel() !== null) {
      fail(ERR.RENDER_TIMEOUT, '当前不是干净 SPA（URL 已带 viewPanel），需重载恢复');
    }
    const u = new URL(location.href);
    u.searchParams.set('viewPanel', String(panelId));
    applyViewUrl(u.toString());
  }

  function clearViewPanelUrl() {
    const api = globalThis.GrafanaLensCaptureMode;
    if (api && typeof api.clearViewPanelHref === 'function') {
      return location.origin + api.clearViewPanelHref(location.href);
    }
    const u = new URL(location.href);
    u.searchParams.delete('viewPanel');
    return u.toString();
  }

  function suppressUnloadPrompt(fn) {
    const stopper = (event) => {
      event.stopImmediatePropagation();
    };
    window.addEventListener('beforeunload', stopper, true);
    const prev = window.onbeforeunload;
    window.onbeforeunload = null;
    try {
      return fn();
    } finally {
      window.removeEventListener('beforeunload', stopper, true);
      window.onbeforeunload = prev;
    }
  }

  function applyViewUrl(url) {
    suppressUnloadPrompt(() => {
      history.replaceState({ q: 1 }, '', url);
      window.dispatchEvent(new PopStateEvent('popstate', { state: { q: 1 } }));
    });
  }

  function pressEscape() {
    const opts = {
      key: 'Escape',
      code: 'Escape',
      keyCode: 27,
      which: 27,
      bubbles: true,
      cancelable: true,
      composed: true,
    };
    const event = new KeyboardEvent('keydown', opts);
    try {
      Object.defineProperty(event, 'keyCode', { get: () => 27 });
      Object.defineProperty(event, 'which', { get: () => 27 });
    } catch (_) {
      /* Chrome 偶发不可重定义 */
    }
    document.dispatchEvent(event);
  }

  // 退出全屏：点工具栏 Go Back（locationService.partial 清 viewPanel）或 Escape。
  // 禁止 history.back()——后退会离开看板并弹出“确认离开”。
  async function exitFullscreen() {
    if (currentViewPanel() === null && !document.querySelector('.react-grid-item--fullscreen')) {
      return;
    }
    const fs = document.querySelector('.react-grid-item--fullscreen');
    if (fs) {
      const deadline = Date.now() + 8000;
      while (Date.now() < deadline && loadingVisible(fs)) {
        await sleep(100);
      }
    }
    const backSel = captureMode().BACK_FROM_VIEW_SELECTOR
      || 'button[aria-label="Go Back"], a[aria-label="Go Back"], button[aria-label="Go back to dashboard"]';
    const back = document.querySelector(backSel);
    suppressUnloadPrompt(() => {
      if (back) nativeClick(back);
      else pressEscape();
    });
    const until = Date.now() + 2000;
    while (Date.now() < until) {
      if (currentViewPanel() === null && !document.querySelector('.react-grid-item--fullscreen')) {
        return;
      }
      await sleep(40);
    }
    applyViewUrl(clearViewPanelUrl());
  }

  // 路由发脏时硬重载，恢复干净 SPA（重载后 collector 自动再注入）。
  function hardReload() {
    suppressUnloadPrompt(() => {
      location.replace(clearViewPanelUrl());
    });
  }

  // 调试/状态查询：返回当前 viewPanel 与全屏 data-panelid。
  function viewState() {
    const fs = document.querySelector('.react-grid-item--fullscreen');
    return {
      urlViewPanel: currentViewPanel(),
      fullscreenPanelId: fs ? fs.getAttribute('data-panelid') : null,
    };
  }

  // ---- 渲染完成判定（§3.4 实测选择器）----

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function isNonZero(el) {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }

  // 返回当前全屏格；data-panelid 必须等于目标 id。
  function fullscreenGrid(panelId) {
    const fs = document.querySelector('.react-grid-item--fullscreen');
    if (!fs || fs.getAttribute('data-panelid') !== String(panelId)) return null;
    return isNonZero(fs) ? fs : null;
  }

  // 格内按面板类型查渲染节点。
  function hasRenderedContent(fs, type) {
    switch (type) {
      case 'timeseries': {
        const u = fs.querySelector('.uplot');
        return !!u && isNonZero(u) && [...fs.querySelectorAll('canvas')].some(isNonZero);
      }
      case 'graph':
        return !!fs.querySelector('.flot-base') || !!fs.querySelector('.graph-panel');
      case 'table': {
        const t = fs.querySelector('[role="table"]');
        return !!t && isNonZero(t) && fs.querySelectorAll('[role="row"]').length > 1;
      }
      case 'piechart':
      case 'gauge':
      case 'stat': {
        if ([...fs.querySelectorAll('svg')].some(isNonZero)) return true;
        const content = fs.querySelector('.panel-content, .panel-container');
        return !!content && isNonZero(content) && String(content.innerText || '').trim().length > 0;
      }
      default:
        // 未覆盖类型：格内有任意非零 canvas/svg 即视为已渲染。
        return [...fs.querySelectorAll('canvas,svg')].some(isNonZero);
    }
  }

  function loadingVisible(fs) {
    const root = fs || document.body;
    if (!root || typeof root.querySelectorAll !== 'function') return false;
    const selectors = [
      '.panel-loading',
      '.preloader',
      '.panel-header .fa-spinner',
      '.panel-header .fa-spin',
      '[aria-busy="true"]',
    ].join(',');
    return [...root.querySelectorAll(selectors)].some((el) => {
      if (!isNonZero(el)) return false;
      const style = typeof getComputedStyle === 'function' ? getComputedStyle(el) : el.style;
      return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
    });
  }

  const NO_DATA_STABLE_MS = 600;
  const RENDER_STABLE_MS = 400;
  const GRID_STABLE_MS = 250;

  function noDataVisible(root) {
    const api = globalThis.GrafanaLensNoData;
    if (api && typeof api.noDataVisible === 'function') return api.noDataVisible(root);
    const text = String(root?.innerText || '').replace(/\s+/g, ' ').trim();
    return text.length <= 200
      && /(?:^|\s)(no data(?: to show|points)?|没有数据|无数据)\s*[.。!！]?\s*$/i.test(text);
  }

  // 轮询直到：全屏 id 匹配 + 类型渲染节点就绪，且连续约 0.4s 不变化。
  // No data 是终态：稳定 600ms 即返回，禁止空等 SPA/d-solo 超时。
  async function waitRendered(panelId, type, timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    let stableSince = null;
    let emptySince = null;
    while (Date.now() < deadline) {
      const fs = fullscreenGrid(panelId);
      if (fs && !loadingVisible(fs) && noDataVisible(fs)) {
        if (emptySince === null) emptySince = Date.now();
        if (Date.now() - emptySince >= NO_DATA_STABLE_MS) return { noData: true };
      } else {
        emptySince = null;
      }
      const ready = !!fs && !loadingVisible(fs) && hasRenderedContent(fs, type);
      if (ready) {
        if (stableSince === null) stableSince = Date.now();
        if (Date.now() - stableSince >= RENDER_STABLE_MS) return { el: fs, noData: false };
      } else {
        stableSince = null;
      }
      await sleep(150);
    }
    fail(ERR.RENDER_TIMEOUT, `面板 ${panelId}（${type}）在 ${timeoutMs}ms 内未就绪`);
  }

  function usesGridCapture(type) {
    const api = globalThis.GrafanaLensCaptureMode;
    if (api && typeof api.usesGridCapture === 'function') return api.usesGridCapture(type);
    return type === 'stat' || type === 'gauge';
  }

  function gridItem(panelId) {
    const nodes = document.querySelectorAll('.react-grid-item[data-panelid]');
    for (const el of nodes) {
      if (el.classList.contains('react-grid-item--fullscreen')) continue;
      if (el.getAttribute('data-panelid') === String(panelId) && isNonZero(el)) return el;
    }
    return null;
  }

  function measureCrop(el) {
    const r = el.getBoundingClientRect();
    const left = Math.max(0, r.left);
    const top = Math.max(0, r.top);
    const right = Math.min(window.innerWidth, r.right);
    const bottom = Math.min(window.innerHeight, r.bottom);
    const width = right - left;
    const height = bottom - top;
    if (width < 48 || height < 48) return null;
    return {
      left,
      top,
      width,
      height,
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
    };
  }

  async function ensureRowExpanded(found, panelId) {
    if (gridItem(panelId)) return true;
    const title = found.rowTitle;
    if (!title) return false;
    const rows = [...document.querySelectorAll('.dashboard-row')];
    const row = rows.find((el) => (el.textContent || '').includes(title));
    if (!row) return false;
    const clickable = row.querySelector('.dashboard-row__title, .dashboard-row__title-wrapper, a') || row;
    clickable.click();
    const deadline = Date.now() + 4000;
    while (Date.now() < deadline) {
      if (gridItem(panelId)) return true;
      await sleep(80);
    }
    return false;
  }

  async function waitGridRendered(panelId, type, timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    let stableSince = null;
    let emptySince = null;
    while (Date.now() < deadline) {
      const el = gridItem(panelId);
      if (el && !loadingVisible(el) && noDataVisible(el)) {
        if (emptySince === null) emptySince = Date.now();
        if (Date.now() - emptySince >= NO_DATA_STABLE_MS) return { noData: true };
      } else {
        emptySince = null;
      }
      const ready = !!el && !loadingVisible(el) && hasRenderedContent(el, type);
      if (ready) {
        if (stableSince === null) stableSince = Date.now();
        if (Date.now() - stableSince >= GRID_STABLE_MS) return { el, noData: false };
      } else {
        stableSince = null;
      }
      await sleep(80);
    }
    return null;
  }

  async function tryCaptureGrid(found, panelId, type, from, to) {
    await ensureRowExpanded(found, panelId);
    let el = gridItem(panelId);
    if (!el) return null;
    el.scrollIntoView({ block: 'center', inline: 'nearest' });
    await sleep(80);
    const rendered = await waitGridRendered(panelId, type, 8000);
    if (!rendered) return null;
    if (rendered.noData) {
      return emptyCaptureResult(found, panelId, type, from, to, 'grid');
    }
    el = rendered.el || gridItem(panelId);
    if (!el) return null;
    el.scrollIntoView({ block: 'center', inline: 'nearest' });
    await sleep(50);
    const crop = measureCrop(el);
    if (!crop) return null;
    return {
      ready: true,
      panelId,
      title: found.panel.title || '',
      type,
      datasourceType: datasourceType(found.panel) || 'other',
      from,
      to,
      viewPath: 'grid',
      crop,
    };
  }

  function emptyCaptureResult(found, panelId, type, from, to, viewPath) {
    return {
      ready: true,
      noData: true,
      skipped: true,
      reason: 'no_data',
      panelId,
      title: found.panel.title || '',
      type,
      datasourceType: datasourceType(found.panel) || 'other',
      from,
      to,
      viewPath,
    };
  }

  function dsoloUrl(panelId, from, to) {
    const uid = dashboardUidFromUrl();
    const params = new URLSearchParams();
    params.set('orgId', new URLSearchParams(location.search).get('orgId') || '1');
    params.set('panelId', String(panelId));
    params.set('theme', 'light');
    if (from) params.set('from', from);
    if (to) params.set('to', to);
    return `${appSubUrl()}/d-solo/${encodeURIComponent(uid)}/solo?${params.toString()}`;
  }

  // capturePanel 状态机：SWITCH_SPA → RENDER_WAIT；失败/脏态返回 needReload。
  // 返回 {ready, panelId, type, viewPath}；READY 后由 SW captureVisibleTab。
  async function capturePanel(args) {
    const panelId = args.panelId;
    if (!Number.isInteger(panelId)) fail(ERR.PANEL_NOT_FOUND, 'panelId 非法');
    const uid = dashboardUidFromUrl();
    if (!uid) fail(ERR.NOT_ON_DASHBOARD, location.pathname);

    const dash = await fetchDashboard(uid);
    const flat = flattenPanels(dash.panels);
    const found = flat.find((x) => x.panel.id === panelId);
    if (!found) fail(ERR.PANEL_NOT_FOUND, `面板 ${panelId} 不在看板 ${uid}`);
    const type = found.panel.type;
    const from = args.from || timeRange(dash).from;
    const to = args.to || timeRange(dash).to;

    const SPA_TIMEOUT = args.spaTimeoutMs ?? 10000;
    const RENDER_TIMEOUT_MS = args.renderTimeoutMs ?? 20000;

    if (currentViewPanel() !== null || document.querySelector('.react-grid-item--fullscreen')) {
      await exitFullscreen();
    }
    if (currentViewPanel() !== null) {
      return { ready: false, needReload: true, panelId, type, reason: 'dirty-state' };
    }

    if (usesGridCapture(type) && !args.forceFullscreen) {
      try {
        const grid = await tryCaptureGrid(found, panelId, type, from, to);
        if (grid) return grid;
      } catch (e) {
        if (e.code && e.code !== ERR.RENDER_TIMEOUT && e.code !== ERR.CAPTURE_FAILED) throw e;
      }
    }

    await enterViewPanel(panelId, found);
    let rendered;
    try {
      rendered = await waitRendered(panelId, type, Math.min(SPA_TIMEOUT, RENDER_TIMEOUT_MS));
    } catch (e) {
      if (e.code !== ERR.RENDER_TIMEOUT) throw e;
      // SPA 首次失败 → 要求硬重载重试一次（由上层执行后再次调用）。
      return { ready: false, needReload: true, panelId, type, reason: 'spa-first-fail' };
    }
    if (rendered?.noData) {
      return emptyCaptureResult(found, panelId, type, from, to, 'spa');
    }
    return {
      ready: true,
      panelId,
      title: found.panel.title || '',
      type,
      datasourceType: datasourceType(found.panel) || 'other',
      from,
      to,
      viewPath: 'spa',
    };
  }

  // 硬重载后的 SPA 重试（干净态）。仍超时则由上层决定是否走 d-solo。
  async function capturePanelRetry(args) {
    const panelId = args.panelId;
    const uid = dashboardUidFromUrl();
    if (!uid) fail(ERR.NOT_ON_DASHBOARD, location.pathname);
    const dash = await fetchDashboard(uid);
    const found = flattenPanels(dash.panels).find((x) => x.panel.id === panelId);
    if (!found) fail(ERR.PANEL_NOT_FOUND, `面板 ${panelId} 不在看板 ${uid}`);
    const type = found.panel.type;
    const from = args.from || timeRange(dash).from;
    const to = args.to || timeRange(dash).to;
    const RENDER_TIMEOUT_MS = args.renderTimeoutMs ?? 20000;

    if (usesGridCapture(type) && !args.forceFullscreen) {
      try {
        const grid = await tryCaptureGrid(found, panelId, type, from, to);
        if (grid) return grid;
      } catch (e) {
        if (e.code && e.code !== ERR.RENDER_TIMEOUT && e.code !== ERR.CAPTURE_FAILED) throw e;
      }
    }

    await enterViewPanel(panelId, found);
    const rendered = await waitRendered(panelId, type, RENDER_TIMEOUT_MS);
    if (rendered?.noData) {
      return emptyCaptureResult(found, panelId, type, from, to, 'spa-retry');
    }
    return {
      ready: true,
      panelId,
      title: found.panel.title || '',
      type,
      datasourceType: datasourceType(found.panel) || 'other',
      from,
      to,
      viewPath: 'spa-retry',
    };
  }

  // 导航 d-solo 会销毁本上下文：延迟触发后立即返回，由新页面的 dsoloState 轮询就绪。
  function goDsolo(args) {
    const panelId = args.panelId;
    const uid = dashboardUidFromUrl();
    if (!uid) fail(ERR.NOT_ON_DASHBOARD, location.pathname);
    const from = args.from;
    const to = args.to;
    setTimeout(() => {
      suppressUnloadPrompt(() => {
        location.href = dsoloUrl(panelId, from, to);
      });
    }, 100);
    return { navigating: true, viewPath: 'dsolo' };
  }

  let dsoloStable = { panelId: null, signature: null, since: 0 };

  // d-solo 页内使用与 SPA 等价的 loading + 内容 + 1.2s 稳定判定。
  async function dsoloState(args) {
    const uid = dashboardUidFromUrl();
    if (!uid || !/\/d-solo\//.test(location.pathname)) {
      dsoloStable = { panelId: null, signature: null, since: 0 };
      return { ready: false, onDsolo: false };
    }
    const dash = await fetchDashboard(uid);
    const found = flattenPanels(dash.panels).find((x) => x.panel.id === args.panelId);
    if (!found) fail(ERR.PANEL_NOT_FOUND, `面板 ${args.panelId} 不在看板 ${uid}`);
    const empty = !loadingVisible(document.body) && noDataVisible(document.body);
    const rendered = empty
      || (!loadingVisible(document.body) && hasRenderedContent(document.body, found.panel.type));
    const nodes = [...document.querySelectorAll('canvas,svg,[role="table"]')]
      .filter(isNonZero)
      .map((node) => {
        const rect = node.getBoundingClientRect();
        return `${node.tagName}:${Math.round(rect.width)}x${Math.round(rect.height)}`;
      });
    const signature = rendered ? `${nodes.join('|')}:${document.body.innerText.length}` : null;
    if (!rendered || dsoloStable.panelId !== args.panelId || dsoloStable.signature !== signature) {
      dsoloStable = { panelId: args.panelId, signature, since: rendered ? Date.now() : 0 };
    }
    const ready = rendered && Date.now() - dsoloStable.since >= (empty ? 600 : 400);
    return {
      ready,
      noData: empty && ready,
      skipped: empty && ready,
      reason: empty && ready ? 'no_data' : undefined,
      onDsolo: true,
      panelId: args.panelId,
      title: found.panel.title || '',
      type: found.panel.type,
      datasourceType: datasourceType(found.panel) || 'other',
      from: args.from || timeRange(dash).from,
      to: args.to || timeRange(dash).to,
      viewPath: 'dsolo',
    };
  }

  // d-solo 截完图后回主看板（同样销毁本上下文，延迟触发）。
  function returnToDashboard(args) {
    const uid = dashboardUidFromUrl();
    if (!uid) fail(ERR.NOT_ON_DASHBOARD, location.pathname);
    const params = new URLSearchParams();
    params.set('orgId', new URLSearchParams(location.search).get('orgId') || '1');
    if (args?.from) params.set('from', args.from);
    if (args?.to) params.set('to', args.to);
    const url = `${appSubUrl()}/d/${encodeURIComponent(uid)}/?${params.toString()}`;
    setTimeout(() => {
      location.replace(url);
    }, 100);
    return { returning: true };
  }

  // 主看板 SPA 是否已可采集（重载后/从 d-solo 返回后轮询用）。
  function spaReady() {
    return {
      ready:
        /\/d\//.test(location.pathname) &&
        currentViewPanel() === null &&
        document.querySelector('.react-grid-layout') !== null,
    };
  }

  // ---- queryDatasource：经 MAIN world page-query，nonce 配对（§6.4）----

  let pageQueryReady = null;

  function injectPageQuery() {
    if (pageQueryReady) return pageQueryReady;
    pageQueryReady = new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('page-query 注入超时')), 5000);
      function onReady(event) {
        if (event.source !== window || event.data?.__lens !== 'page-query-ready') return;
        window.removeEventListener('message', onReady);
        clearTimeout(timer);
        resolve();
      }
      window.addEventListener('message', onReady);
      const s = document.createElement('script');
      s.src = chrome.runtime.getURL('content/page-query.js');
      s.onload = () => s.remove();
      s.onerror = () => {
        clearTimeout(timer);
        window.removeEventListener('message', onReady);
        reject(new Error('page-query 脚本加载失败'));
      };
      (document.head || document.documentElement).appendChild(s);
    });
    return pageQueryReady;
  }

  function randomNonce() {
    const a = new Uint8Array(16);
    crypto.getRandomValues(a);
    return [...a].map((b) => b.toString(16).padStart(2, '0')).join('');
  }

  function postQuery(payload, timeoutMs = 20000) {
    return new Promise((resolve, reject) => {
      const nonce = randomNonce();
      const timer = setTimeout(() => {
        window.removeEventListener('message', onMsg);
        const e = new Error('page-query 超时');
        e.code = ERR.RENDER_TIMEOUT;
        reject(e);
      }, timeoutMs);

      function onMsg(event) {
        if (event.source !== window) return;
        const msg = event.data;
        if (!msg || msg.__lens !== 'result' || msg.nonce !== nonce) return;
        window.removeEventListener('message', onMsg);
        clearTimeout(timer);
        if (msg.error) {
          const e = new Error(msg.error.message);
          e.code = msg.error.code;
          reject(e);
        } else {
          resolve(msg.data);
        }
      }
      window.addEventListener('message', onMsg);
      window.postMessage({ __lens: 'query', nonce, payload }, location.origin);
    });
  }

  async function resolveDatasource(ref, type, variables = {}) {
    if (ref && typeof ref === 'object' && ref.uid && !String(ref.uid).startsWith('${')) {
      return { uid: ref.uid, type: ref.type || type };
    }
    const response = await fetch(`${appSubUrl()}/api/datasources`, {
      headers: { Accept: 'application/json' },
      credentials: 'include',
    });
    if (response.status === 401 || response.status === 403) {
      fail(ERR.AUTH_REQUIRED, `数据源 API 返回 ${response.status}`);
    }
    if (!response.ok) fail(ERR.CAPTURE_FAILED, `数据源 API 返回 ${response.status}`);
    const sources = await response.json();
    const variableMatch = String(ref?.uid || '').match(/^\$\{([^}]+)\}$/);
    const selected = variableMatch ? variables[variableMatch[1]] : null;
    const matched = sources.find(
      (source) =>
        source.type === type &&
        (!selected || source.uid === selected || source.name === selected)
    ) || sources.find((source) => source.type === type);
    if (!matched?.uid) fail(ERR.EVIDENCE_UNSUPPORTED, `找不到 ${type} 数据源`);
    return { uid: matched.uid, type: matched.type };
  }

  async function normalizeQueryPayload(payload) {
    if (Array.isArray(payload?.queries)) return payload;
    const panel = payload?.panel || {};
    const type = panel.datasourceType || panel.datasource?.type || 'other';
    if (type === 'elasticsearch') {
      fail(ERR.EVIDENCE_UNSUPPORTED, 'Elasticsearch 面板不执行数值取证');
    }
    if (type !== 'prometheus') {
      fail(ERR.EVIDENCE_UNSUPPORTED, `数据源 ${type} 不支持数值取证`);
    }
    const targets = (panel.targets || []).filter((target) => target.hide !== true && target.expr);
    const queries = [];
    for (const target of targets) {
      const datasource = await resolveDatasource(
        target.datasource || panel.datasource,
        type,
        panel.datasourceVariables || {}
      );
      queries.push({
        ...target,
        datasource,
        intervalMs: payload.intervalMs || 60000,
        maxDataPoints: payload.maxDataPoints || 1200,
        instant: payload.mode === 'instant' || target.instant === true,
      });
    }
    return {
      queries,
      from: payload.from || panel.from || 'now-24h',
      to: payload.to || panel.to || 'now',
    };
  }

  async function queryDatasource(args) {
    if (dashboardUidFromUrl() === null) fail(ERR.NOT_ON_DASHBOARD, location.pathname);
    await injectPageQuery();
    return postQuery(await normalizeQueryPayload(args.payload));
  }

  const api = {
    getMeta,
    listPanels,
    enterViewPanel,
    exitFullscreen,
    hardReload,
    viewState,
    capturePanel,
    capturePanelRetry,
    goDsolo,
    dsoloState,
    returnToDashboard,
    spaReady,
    queryDatasource,
  };

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (!msg || msg.kind !== 'LENS_CALL' || !api[msg.method]) return false;
    Promise.resolve()
      .then(() => api[msg.method](msg.args || {}))
      .then((data) => sendResponse({ data }))
      .catch((e) => sendResponse({ error: { code: e.code || 'CAPTURE_FAILED', message: String(e.message || e) } }));
    return true; // 异步 sendResponse
  });
})();
