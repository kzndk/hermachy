// Exercise native IPC and the real Omarchy resolver without changing the desktop theme.
const {_electron:electron,expect}=require('@playwright/test');
const fs=require('node:fs/promises');const path=require('node:path');const os=require('node:os');
const {parsePalette}=require('../theme.cjs');
(async()=>{
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'hermachy-native-theme-'));
  const themeDir=path.join(root,'current/theme');await fs.mkdir(themeDir,{recursive:true});
  const source=name=>path.join(__dirname,'../tests/fixtures',name+'.toml');
  await fs.copyFile(source('tokyo-night'),path.join(themeDir,'colors.toml'));
  await fs.writeFile(path.join(root,'current/theme.name'),'tokyo-night');
  const instance=await electron.launch({args:[path.resolve(__dirname,'..'),'--ozone-platform=x11'],env:{...process.env,HERMACHY_DESKTOP_TEST_DATA:path.join(root,'profile'),HERMACHY_DESKTOP_THEME_DIR:themeDir}});
  try {
    const page=await instance.firstWindow();const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await expect(page.locator('#theme-name')).toHaveText('Tokyo Night');
    await page.locator('#prompt').fill('Preserve this draft while Omarchy changes theme.');
    await page.locator('#companion-picker').click();
    const next=path.join(root,'current/next-theme');await fs.mkdir(next);await fs.copyFile(source('white'),path.join(next,'colors.toml'));
    await fs.rename(themeDir,themeDir+'.previous');await fs.writeFile(path.join(root,'current/theme.name'),'white');await fs.rename(next,themeDir);
    await expect(page.locator('#theme-name')).toHaveText('White',{timeout:10000});
    const expected=parsePalette(await fs.readFile(source('white'),'utf8')).background;
    const native=await instance.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].getBackgroundColor());
    if(native.toLowerCase()!==expected.toLowerCase())throw new Error(`Native background mismatch: ${native}`);
    await expect(page.locator('#prompt')).toHaveValue('Preserve this draft while Omarchy changes theme.');
    await expect(page.locator('#companion-dialog')).toBeVisible();
    console.log('NATIVE THEME SWITCH: Tokyo Night -> White; window, renderer, pets, dialog and draft verified');
    if(errors.length)throw new Error(errors.join('\n'));
  } finally {await instance.close();await fs.rm(root,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1;});
