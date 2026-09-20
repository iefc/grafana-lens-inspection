// WS 客户端状态机（§5.4）：连接 / ping-pong 心跳 / 指数退避重连。
// onFrame(frame) 由 offscreen 注入，处理桥指令。

export const WS_STATE = {
  CONNECTING: 'CONNECTING',
  CONNECTED: 'CONNECTED',
  RECONNECTING: 'RECONNECTING',
  STOPPED: 'STOPPED',
};

export class WSClient {
  constructor({ url, token, onFrame, onState, heartbeatMs = 15000 }) {
    this.baseUrl = url;
    this.token = token;
    this.onFrame = onFrame || (() => {});
    this.onState = onState || (() => {});
    this.heartbeatMs = heartbeatMs;

    this.state = WS_STATE.CONNECTING;
    this.reconnectAttempt = 0;
    this.ws = null;
    this.heartbeatTimer = null;
    this.pongTimeoutTimer = null;
    this.reconnectTimer = null;
  }

  endpoint() {
    const u = new URL(this.baseUrl);
    u.searchParams.set('token', this.token);
    return u.toString().replace(/^http/, 'ws');
  }

  setState(next) {
    this.state = next;
    this.onState(next);
  }

  start() {
    this.setState(WS_STATE.CONNECTING);
    this.open();
  }

  stop() {
    this.setState(WS_STATE.STOPPED);
    this.clearTimers();
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.close();
      this.ws = null;
    }
  }

  open() {
    const ws = new WebSocket(this.endpoint());
    this.ws = ws;
    ws.onopen = () => this.handleOpen();
    ws.onmessage = (ev) => this.handleMessage(ev);
    ws.onclose = () => this.handleClose();
    ws.onerror = () => {
      // close 紧随其后，统一在 handleClose 重连。
      try { ws.close(); } catch {}
    };
  }

  handleOpen() {
    this.reconnectAttempt = 0;
    this.setState(WS_STATE.CONNECTED);
    this.schedulePing();
  }

  handleMessage(ev) {
    let frame;
    try {
      frame = JSON.parse(ev.data);
    } catch {
      return;
    }
    if (frame.type === 'ping') {
      this.send({ type: 'pong' });
      this.schedulePing();
      return;
    }
    if (frame.type === 'pong') {
      if (this.pongTimeoutTimer) clearTimeout(this.pongTimeoutTimer);
      this.schedulePing();
      return;
    }
    this.onFrame(frame);
  }

  handleClose() {
    this.clearTimers();
    if (this.state === WS_STATE.STOPPED) return;
    this.setState(WS_STATE.RECONNECTING);
    // delay = min(2 * 2^n, 30)s，±20% 抖动。
    const base = Math.min(2 * 2 ** this.reconnectAttempt, 30) * 1000;
    const delay = base * (0.8 + Math.random() * 0.4);
    this.reconnectAttempt += 1;
    this.reconnectTimer = setTimeout(() => {
      this.setState(WS_STATE.CONNECTING);
      this.open();
    }, delay);
  }

  schedulePing() {
    if (this.heartbeatTimer) clearTimeout(this.heartbeatTimer);
    if (this.pongTimeoutTimer) clearTimeout(this.pongTimeoutTimer);
    this.heartbeatTimer = setTimeout(() => {
      this.send({ type: 'ping' });
      // 对端不回 pong → 视为断线，主动关闭走重连。
      this.pongTimeoutTimer = setTimeout(() => {
        try { this.ws.close(); } catch {}
      }, this.heartbeatMs);
    }, this.heartbeatMs);
  }

  clearTimers() {
    if (this.heartbeatTimer) clearTimeout(this.heartbeatTimer);
    if (this.pongTimeoutTimer) clearTimeout(this.pongTimeoutTimer);
    this.heartbeatTimer = null;
    this.pongTimeoutTimer = null;
  }

  send(frame) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(frame));
    }
  }
}
