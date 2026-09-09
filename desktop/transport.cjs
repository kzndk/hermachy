const { spawn } = require('node:child_process');
const { EventEmitter } = require('node:events');
const { StringDecoder } = require('node:string_decoder');
const path = require('node:path');

class Transport extends EventEmitter {
  constructor() { super(); this.pending = new Map(); this.nextId = 0; this.child = null; this.ready = false; }
  start(cwd) {
    if (this.child) throw new Error('A connection is already open');
    const root = path.resolve(__dirname, '../src');
    const child = spawn('python3', ['-u', '-c',
      "import sys,runpy;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('hermachy.desktop',run_name='__main__')",
      root, '--cwd', cwd], { cwd, stdio: ['pipe', 'pipe', 'pipe'] });
    this.child = child;
    let buffer = '', stderr = '';
    const decoder = new StringDecoder('utf8');
    const errors = new StringDecoder('utf8');
    child.stdout.on('data', chunk => {
      buffer += decoder.write(chunk);
      if (Buffer.byteLength(buffer) > 32 * 1024 * 1024) {
        this.emit('status', { state: 'error', message: 'Gateway response exceeded 32 MiB' });
        child.kill('SIGTERM'); return;
      }
      let newline;
      while ((newline = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, newline); buffer = buffer.slice(newline + 1);
        try { this.receive(JSON.parse(line)); } catch { /* Gateway startup diagnostics are not RPC. */ }
      }
    });
    child.stderr.on('data', chunk => {
      const text = errors.write(chunk); stderr = (stderr + text).slice(-6000);
      if (text.includes('connection lost')) this.emit('status', { state: 'reconnecting', message: 'Reconnecting to the same agent…' });
      if (text.includes('connection restored')) this.emit('status', { state: 'connected' });
    });
    child.stdin.on('error', () => {});
    child.on('error', error => this.emit('status', { state: 'error', message: error.message }));
    child.on('close', code => {
      if (this.child !== child) return;
      this.child = null; this.ready = false;
      this.rejectAll(new Error(stderr.trim() || 'Hermes connection closed'));
      this.emit('status', { state: code === 0 ? 'disconnected' : 'error', message: stderr.trim() || 'Connection closed. Reconnect to resume a saved conversation.' });
    });
    this.emit('status', { state: 'connecting', message: 'Checking your Hermes connection…' });
  }
  receive(frame) {
    if (frame.method === 'desktop.ready') { this.ready = true; this.emit('status', { state: 'connected', ...frame.params }); }
    else if (frame.method === 'event') this.emit('event', frame.params);
    else if (frame.id != null) {
      const pending = this.pending.get(frame.id);
      if (!pending) return;
      this.pending.delete(frame.id); clearTimeout(pending.timer);
      if (frame.error) pending.reject(Object.assign(new Error(frame.error.message || 'Gateway request failed'), { code: frame.error.code }));
      else pending.resolve(frame.result);
    }
  }
  request(method, params = {}, timeout = 120000) {
    if (!this.child || !this.ready || !this.child.stdin.writable) return Promise.reject(new Error('Hermes is not connected'));
    const id = ++this.nextId;
    const frame = JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n';
    if (Buffer.byteLength(frame) > 15 * 1024 * 1024) return Promise.reject(new Error('Request exceeds the transport limit'));
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error('Request timed out. Its outcome is uncertain; reconnect and inspect the conversation before resending.'));
      }, timeout);
      this.pending.set(id, { resolve, reject, timer });
      this.child.stdin.write(frame, error => {
        if (error && this.pending.has(id)) { clearTimeout(timer); this.pending.delete(id); reject(error); }
      });
    });
  }
  rejectAll(error) {
    for (const p of this.pending.values()) { clearTimeout(p.timer); p.reject(error); }
    this.pending.clear();
  }
  async stop() {
    const child = this.child;
    if (!child) return;
    this.child = null; this.ready = false;
    this.rejectAll(new Error('Connection closed'));
    await new Promise(resolve => {
      const timer = setTimeout(() => child.kill('SIGTERM'), 12000);
      const killTimer = setTimeout(() => { child.kill('SIGKILL'); resolve(); }, 25000);
      child.once('close', () => { clearTimeout(timer); clearTimeout(killTimer); resolve(); });
      child.stdin.end();
    });
  }
}
module.exports = { Transport };
