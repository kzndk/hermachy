// Explicit live integration check: submits one small validation prompt to Hermes.
const {_electron:electron}=require('@playwright/test');
const path=require('node:path');const fs=require('node:fs/promises');const os=require('node:os');
(async()=>{
  const dir=await fs.mkdtemp(path.join(os.tmpdir(),'hermachy-live-prompt-'));
  const instance=await electron.launch({args:[path.resolve(__dirname,'..'),'--ozone-platform=x11','--cwd',path.resolve(__dirname,'../..')],env:{...process.env,HERMACHY_DESKTOP_TEST_DATA:dir},timeout:30000});
  try {
    const page=await instance.firstWindow();
    await page.waitForFunction(()=>document.querySelector('#connection-label')?.textContent==='Hermes connected' && document.querySelector('#new-chat')?.disabled===false,{},{timeout:90000});
    const png=[...await fs.readFile(path.join(__dirname,'../assets/hermachy.png'))];
    await page.evaluate(png=>{
      const data=new DataTransfer();
      data.items.add(new File(['The validation token is birch-6429.'],'validation.txt',{type:'text/plain'}));
      data.items.add(new File([new Uint8Array(png)],'companion.png',{type:'image/png'}));
      document.dispatchEvent(new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true}));
    },png);
    await page.waitForFunction(()=>document.querySelectorAll('.attachment').length===2);
    await page.locator('#prompt').fill('Hermachy desktop validation: read the attached text file on Omarchy and inspect the attached image. Reply in one sentence containing the validation token and the main color of the illustrated character. Do not modify any files or take any other actions.');
    await page.getByRole('button',{name:'Send message',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('#send').getAttribute('aria-label')==='Stop response');
    console.log('WORKING STATE',await page.locator('body').getAttribute('class'));
    await page.waitForFunction(()=>document.querySelector('#send').getAttribute('aria-label')==='Send message',{},{timeout:180000});
    const reply=await page.locator('.message.assistant .message-content').allTextContents();
    console.log('LIVE REPLY',JSON.stringify(reply));
    if(!reply.join(' ').includes('birch-6429') || !/green/i.test(reply.join(' ')))throw new Error('Attachment validation reply did not match');
    console.log('ACTIVITY',await page.locator('#activity-label').textContent());
    console.log('TOOL ROWS',await page.locator('.tool-row summary').allTextContents());
    await page.screenshot({path:path.join(__dirname,'../test-results/live-reply.png')});
  } finally {await instance.close();console.log('Validation data kept for this saved session:',dir);}
})().catch(e=>{console.error(e);process.exitCode=1;});
