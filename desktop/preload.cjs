const { contextBridge, ipcRenderer } = require('electron');
const subscribe = channel => callback => {
  const listener = (_event, value) => callback(value);
  ipcRenderer.on(channel, listener);
  return () => ipcRenderer.removeListener(channel, listener);
};
contextBridge.exposeInMainWorld('hermachy', {
  bootstrap:() => ipcRenderer.invoke('bootstrap'),
  preferences:patch => ipcRenderer.invoke('preferences',patch),
  connect:() => ipcRenderer.invoke('connect'),
  workspace:() => ipcRenderer.invoke('workspace'),
  rpc:(method,params={}) => ipcRenderer.invoke('rpc',method,params),
  chooseFiles:() => ipcRenderer.invoke('choose-files'),
  addFile:(name,bytes) => ipcRenderer.invoke('add-file',name,bytes),
  removeFile:id => ipcRenderer.invoke('remove-file',id),
  submit:prompt => ipcRenderer.invoke('submit',prompt),
  openLink:url => ipcRenderer.invoke('open-link',url),
  onConnection:subscribe('connection'),
  onEvent:subscribe('gateway-event'),
  onTheme:subscribe('theme'),
  changes:() => ipcRenderer.invoke('changes-list'),
  diff:name => ipcRenderer.invoke('changes-diff',name),
});
