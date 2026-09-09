const { app, BrowserWindow, ipcMain, dialog, shell, Menu, Notification } = require('electron');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const { pathToFileURL } = require('node:url');
const { Transport } = require('./transport.cjs');
const { Attachments, MAX_FILE } = require('./attachments.cjs');
const { ThemeWatcher } = require('./theme.cjs');
const { Changes } = require('./changes.cjs');
app.setName('Hermachy');
if (process.env.HERMACHY_DESKTOP_TEST_DATA) app.setPath('userData', process.env.HERMACHY_DESKTOP_TEST_DATA);
if (!app.requestSingleInstanceLock()) app.quit();
let window, settings, attachments, boot, connectionTask, quitting = false, submitting = false;
let preferencesTask = Promise.resolve();
const transport = new Transport();
const theme = new ThemeWatcher();
const changes = new Changes();
let connection = { state: 'disconnected' };
const allowed = new Set(['session.create', 'session.list', 'session.resume', 'session.history', 'session.interrupt', 'session.title', 'approval.respond', 'clarify.respond', 'sudo.respond', 'secret.respond']);
const rendererURL = pathToFileURL(path.join(__dirname, 'renderer/index.html')).href;
const settingsFile = () => path.join(app.getPath('userData'), 'preferences.json');
const send = (channel, value) => { if (window && !window.isDestroyed()) window.webContents.send(channel, value); };
function handle(channel, callback) {
  ipcMain.handle(channel, (event, ...args) => {
    if (event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame || event.senderFrame.url !== rendererURL) throw new Error('Untrusted app request');
    return callback(...args);
  });
}
async function saveSettings(patch) {
  const next = { ...settings };
  if (['moss','ember','orbit'].includes(patch.pet)) next.pet = patch.pet;
  if (typeof patch.motion === 'boolean') next.motion = patch.motion;
  if (typeof patch.notifications === 'boolean') next.notifications = patch.notifications;
  if (typeof patch.changes === 'boolean') next.changes = patch.changes;
  if (patch.cwd && (await fs.stat(patch.cwd)).isDirectory()) next.cwd = path.resolve(patch.cwd);
  await fs.mkdir(path.dirname(settingsFile()), {recursive:true, mode:0o700});
  await fs.writeFile(settingsFile()+'.tmp', JSON.stringify(next), {mode:0o600});
  await fs.rename(settingsFile()+'.tmp', settingsFile()); settings = next;
  return settings;
}
function connect() {
  if (connectionTask) return connectionTask;
  connectionTask = (async () => {
    await transport.stop();
    transport.start(settings.cwd);
  })().finally(() => { connectionTask = null; });
  return connectionTask;
}
transport.on('status', status => { connection = {...connection, ...status}; send('connection', connection); });
transport.on('event', event => {
  send('gateway-event', event);
  if (event.type === 'message.complete' && settings?.notifications && !window?.isFocused() && Notification.isSupported()) {
    new Notification({title:'Hermachy', body:'Your agent has finished. Your conversation is ready.'}).show();
  }
});
app.on('second-instance', () => { window?.show(); window?.focus(); });
app.whenReady().then(async () => {
  Menu.setApplicationMenu(null);
  settings = {pet:'moss', motion:true, notifications:false, cwd:os.homedir()};
  try { Object.assign(settings, JSON.parse(await fs.readFile(settingsFile(), 'utf8'))); } catch {}
  const cwdArg = process.argv.indexOf('--cwd');
  if (cwdArg >= 0 && process.argv[cwdArg+1]) settings.cwd = path.resolve(process.argv[cwdArg+1]);
  if (!['moss','ember','orbit'].includes(settings.pet)) settings.pet = 'moss';
  try { if (!(await fs.stat(settings.cwd)).isDirectory()) settings.cwd = os.homedir(); } catch { settings.cwd = os.homedir(); }
  attachments = new Attachments(path.join(app.getPath('userData'), 'attachments'));
  await theme.start();
  theme.on('theme', value => {
    if (window && !window.isDestroyed()) window.setBackgroundColor(value.colors.background);
    send('theme', value);
  });
  window = new BrowserWindow({width:1440, height:900, minWidth:600, minHeight:400, show:false, title:'Hermachy', backgroundColor:theme.current.colors.background, icon:path.join(__dirname,'assets/hermachy.png'),
    webPreferences:{preload:path.join(__dirname,'preload.cjs'), contextIsolation:true, nodeIntegration:false, sandbox:true, webSecurity:true}});
  window.webContents.setWindowOpenHandler(() => ({action:'deny'}));
  window.webContents.on('will-navigate', event => event.preventDefault());
  window.webContents.session.setPermissionRequestHandler((_wc,_permission,callback) => callback(false));
  window.webContents.session.setPermissionCheckHandler(() => false);
  handle('bootstrap', () => {
    if (!boot) boot = connect().catch(error => { connection = {state:'error',message:error.message}; send('connection',connection); });
    return {settings, connection, theme:theme.current, version:app.getVersion()};
  });
  handle('changes-list', () => changes.list(settings.cwd));
  handle('changes-diff', name => {
    if(typeof name !== 'string') throw new Error('Select a workspace file');
    return changes.diff(name);
  });
  handle('preferences', patch => {
    preferencesTask = preferencesTask.catch(() => {}).then(() => saveSettings(patch || {}));
    return preferencesTask;
  });
  handle('connect', () => connect());
  handle('workspace', async () => {
    if (submitting) throw new Error('Wait for the prompt to be submitted');
    const result = await dialog.showOpenDialog(window, {properties:['openDirectory'], defaultPath:settings.cwd, title:'Choose your Omarchy workspace'});
    if (result.canceled) return null;
    await saveSettings({cwd:result.filePaths[0]}); await connect(); return settings;
  });
  handle('rpc', (method, params) => {
    if (!allowed.has(method) || !params || typeof params !== 'object' || Array.isArray(params)) throw new Error('Invalid gateway request');
    if (method === 'session.create') params = {cols:120, cwd:settings.cwd};
    return transport.request(method, params);
  });
  handle('choose-files', async () => {
    const result = await dialog.showOpenDialog(window, {properties:['openFile','multiSelections'], title:'Add files or images', defaultPath:settings.cwd});
    if (result.canceled) return {items:[],errors:[]};
    const items=[], errors=[];
    for (const filename of result.filePaths.slice(0,10)) {
      try { items.push(await attachments.addPath(filename)); } catch(error) { errors.push(`${path.basename(filename)}: ${error.message}`); }
    }
    if (result.filePaths.length > 10) errors.push('Only the first 10 files were added');
    return {items,errors};
  });
  handle('add-file', async (name, bytes) => {
    if (!(bytes instanceof Uint8Array) || bytes.byteLength > MAX_FILE) throw new Error('Each attachment can be up to 8 MB');
    return attachments.add(name, Buffer.from(bytes));
  });
  handle('remove-file', id => attachments.remove(id));
  handle('submit', async ({sessionId,text,ids}) => {
    if (submitting) throw new Error('A prompt is already being submitted');
    if (typeof sessionId !== 'string' || typeof text !== 'string' || text.length > 200000) throw new Error('Invalid prompt');
    const items = attachments.get(ids);
    if (!text.trim() && !items.length) throw new Error('Write a prompt or add an attachment');
    submitting = true;
    try {
      const refs=[];
      for (const item of items) {
        if (item.mime) {
          if (!item.remote || item.remote.sessionId !== sessionId) {
            const data = await fs.readFile(item.path);
            const result = await transport.request('image.attach_bytes', {session_id:sessionId, filename:item.name, content_base64:data.toString('base64')});
            if (!result?.attached) throw new Error(`Could not attach ${item.name}`);
            item.remote = {sessionId,path:result.path};
          }
        } else {
          refs.push(`Attached file ${JSON.stringify(item.name)} on Omarchy: ${JSON.stringify(item.path)}. Read it with the Omarchy file/terminal tools; this path is on the Omarchy workstation.`);
        }
      }
      const prompt = [text.trim() || 'Please examine the attached files and images.', ...refs].join('\n\n');
      const result = await transport.request('prompt.submit', {session_id:sessionId,text:prompt});
      for (const item of items) item.sent = true;
      return result;
    } catch (error) {
      // Clear staged images after a rejected submit. A timeout has an uncertain
      // outcome: disconnect instead of risking duplicate attachment or execution.
      if (/timed out/.test(error.message)) {
        await transport.stop(); connection = {state:'error',message:error.message}; send('connection',connection);
      } else {
        for (const item of items) if (item.remote) {
          try { await transport.request('image.detach',{session_id:item.remote.sessionId,path:item.remote.path},10000); delete item.remote; } catch {}
        }
      }
      throw error;
    } finally { submitting = false; }
  });
  handle('open-link', async url => {
    const parsed = new URL(url);
    if (!['https:','http:'].includes(parsed.protocol)) throw new Error('Only web links can be opened');
    await shell.openExternal(parsed.href);
  });
  await window.loadFile(path.join(__dirname,'renderer/index.html'));
  window.show();
});
app.on('window-all-closed', () => app.quit());
app.on('before-quit', event => {
  if (quitting) return;
  event.preventDefault(); quitting = true;
  theme.stop();
  Promise.allSettled([transport.stop(),attachments?.clearPending()]).finally(() => app.quit());
});
