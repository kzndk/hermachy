const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const MAX_FILE = 8 * 1024 * 1024;
const MAX_TOTAL = 32 * 1024 * 1024;
function imageMime(bytes) {
  if (bytes.subarray(0, 8).equals(Buffer.from([137,80,78,71,13,10,26,10]))) return 'image/png';
  if (bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255) return 'image/jpeg';
  if (/^GIF8[79]a/.test(bytes.subarray(0, 6).toString())) return 'image/gif';
  if (bytes.subarray(0,4).toString() === 'RIFF' && bytes.subarray(8,12).toString() === 'WEBP') return 'image/webp';
  return null;
}
class Attachments {
  constructor(root) { this.root = root; this.items = new Map(); }
  async add(name, bytes) {
    if (!Buffer.isBuffer(bytes)) bytes = Buffer.from(bytes);
    if (!bytes.length) throw new Error('This file is empty');
    if (bytes.length > MAX_FILE) throw new Error('Each attachment can be up to 8 MB');
    const pending = [...this.items.values()].filter(a => !a.sent);
    if (pending.length >= 10 || pending.reduce((n,a) => n+a.size, 0) + bytes.length > MAX_TOTAL) throw new Error('Use up to 10 attachments, 32 MB in total');
    const id = crypto.randomUUID();
    const safeName = path.basename(String(name || 'attachment')).replace(/[\x00-\x1f\x7f]/g, '_').slice(0, 180) || 'attachment';
    const dir = path.join(this.root, id);
    await fs.mkdir(dir, { recursive: true, mode: 0o700 });
    const target = path.join(dir, safeName);
    await fs.writeFile(target, bytes, { mode: 0o600, flag: 'wx' });
    const mime = imageMime(bytes);
    const item = { id, name: safeName, path: target, size: bytes.length, mime, sent: false };
    this.items.set(id, item);
    return { id, name: safeName, size: bytes.length, kind: mime ? 'image' : 'file', preview: mime ? `data:${mime};base64,${bytes.toString('base64')}` : null };
  }
  async addPath(filename) {
    const handle = await fs.open(filename, 'r');
    try {
      const stat = await handle.stat();
      if (!stat.isFile()) throw new Error('Choose a regular file');
      if (stat.size > MAX_FILE) throw new Error('Each attachment can be up to 8 MB');
      const bytes = Buffer.alloc(MAX_FILE + 1);
      let count = 0;
      while (count < bytes.length) {
        const {bytesRead} = await handle.read(bytes, count, bytes.length - count, null);
        if (!bytesRead) break;
        count += bytesRead;
      }
      return await this.add(path.basename(filename), bytes.subarray(0, count));
    } finally { await handle.close(); }
  }
  get(ids) {
    if (!Array.isArray(ids) || ids.length > 10 || new Set(ids).size !== ids.length) throw new Error('Invalid attachments');
    return ids.map(id => { const item = this.items.get(id); if (!item || item.sent) throw new Error('Attachment no longer available'); return item; });
  }
  async remove(id) {
    const item = this.items.get(id);
    if (item && !item.sent) { await fs.rm(path.dirname(item.path), { recursive: true, force: true }); this.items.delete(id); }
  }
  async clearPending() { for (const [id, item] of this.items) if (!item.sent) await this.remove(id); }
}
module.exports = { Attachments, imageMime, MAX_FILE };
