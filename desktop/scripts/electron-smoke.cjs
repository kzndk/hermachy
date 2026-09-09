const {_electron:electron}=require('@playwright/test');
const path=require('node:path');const fs=require('node:fs/promises');const os=require('node:os');
const assert=require('node:assert/strict');
(async()=>{
  const dir=await fs.mkdtemp(path.join(os.tmpdir(),'hermachy-electron-'));
  const instance=await electron.launch({executablePath:process.env.HERMACHY_DESKTOP_ELECTRON,args:[process.env.HERMACHY_DESKTOP_APP || path.resolve(__dirname,'..'),'--ozone-platform=x11','--cwd',path.resolve(__dirname,'../..')],env:{...process.env,HERMACHY_DESKTOP_TEST_DATA:dir},timeout:30000});
  try {
    const page=await instance.firstWindow(); const errors=[]; page.on('pageerror',e=>errors.push(e.message));
    page.on('console',m=>{if(m.type()==='error')console.log('RENDERER ERROR',m.text());});
    await page.waitForFunction(()=>document.querySelector('#connection-label')?.textContent==='Hermes connected' && document.querySelector('#new-chat')?.disabled===false,{},{timeout:90000});
    console.log('CONNECTED',await page.locator('#connection-label').textContent());
    console.log('MODEL',await page.locator('#model-label').textContent());
    await page.waitForFunction(()=>Number(document.querySelector('#session-count').textContent)>0,{},{timeout:30000});
    console.log('SESSIONS',await page.locator('.session-button').count());
    await page.screenshot({path:path.join(__dirname,'../test-results/electron-live.png')});
    const security=await instance.evaluate(({BrowserWindow})=>{const p=BrowserWindow.getAllWindows()[0].webContents.getLastWebPreferences();return{sandbox:p.sandbox,contextIsolation:p.contextIsolation,nodeIntegration:p.nodeIntegration};});
    assert.deepEqual(security,{sandbox:true,contextIsolation:true,nodeIntegration:false});
    console.log('SECURITY',security);
    assert.deepEqual(errors,[]);
  } finally {await instance.close();await fs.rm(dir,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1;});
