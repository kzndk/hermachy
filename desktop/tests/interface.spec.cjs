const {test,expect}=require('@playwright/test');
const fs=require('node:fs');const path=require('node:path');const {buildTheme,parsePalette}=require('../theme.cjs');
const fixture=name=>({...buildTheme(parsePalette(fs.readFileSync(path.join(__dirname,'fixtures',name+'.toml'),'utf8')),{name}),revision:name==='tokyo-night'?1:2});
async function open(page) {
  await page.addInitScript(theme=>{
    const prefs=JSON.parse(localStorage.getItem('prefs') || '{"pet":"moss","motion":true,"cwd":"/home/test/Projects/hermachy"}');
    let onEvent=()=>{}, onConnection=()=>{},onTheme=()=>{},sid=0;
    window.__theme=value=>onTheme(value);
    window.__submitted=[];window.__requests=[];window.__fail=false;
    window.__event=event=>onEvent({session_id:'session-'+sid,...event});
    window.__connection=state=>onConnection({state});
    window.hermachy={
      bootstrap:async()=>({settings:prefs,connection:{state:'connected'},theme,version:'0.1.0'}),
      preferences:async patch=>{Object.assign(prefs,patch);localStorage.setItem('prefs',JSON.stringify(prefs));return prefs;},
      onConnection:fn=>{onConnection=fn;},onEvent:fn=>{onEvent=fn;},onTheme:fn=>{onTheme=fn;},
      changes:async()=>({available:true,branch:"main",files:[{path:"src/app.ts",status:"modified"},{path:"src/styles.css",status:"new"}]}),
      diff:async name=>({kind:"diff",text:"diff --git a/"+name+" b/"+name+"\n--- a/"+name+"\n+++ b/"+name+"\n@@ -1,4 +1,5 @@\n export function App() {\n-  return renderWelcome();\n+  const theme = useOmarchyTheme();\n+  return renderWorkspace(theme);\n }"}),
      connect:async()=>{onConnection({state:'connecting'});setTimeout(()=>onConnection({state:'connected'}),10);},
      workspace:async()=>null,openLink:async()=>{},
      rpc:async(method,params)=>{
        window.__requests.push({method,params});
        if(method==='session.create')return{session_id:'session-'+(++sid),stored_session_id:'stored-'+sid,messages:[],info:{model:'Hermes · Default model'}};
        if(method==='session.list')return{sessions:[{id:'past-1',title:'A gentler morning routine'},{id:'past-2',title:'Building my personal website'},{id:'past-3',title:'Ideas for a weekend project'}]};
        if(method==='session.resume')return{session_id:'session-'+(++sid),stored_session_id:params.session_id,messages:[{role:'user',text:'Where were we?'},{role:'assistant',text:'We were making something **lovely**.'}]};
        if(method==='session.interrupt'){onEvent({session_id:'session-'+sid,type:'message.complete',payload:{text:'Response stopped.',status:'interrupted'}});}
        return {resolved:true};
      },
      chooseFiles:async()=>({items:[{id:'file-1',name:'project-notes.md',size:1540,kind:'file'}],errors:[]}),
      addFile:async(name,bytes)=>({id:'drop-'+name,name,size:bytes.length,kind:'file'}),removeFile:async()=>{},
      submit:async prompt=>{window.__submitted.push(prompt);if(window.__fail)throw new Error('The attachment could not be sent');onEvent({session_id:'session-'+sid,type:'message.start'});return{status:'started'};},
    };
  },fixture('tokyo-night'));
  await page.goto('/renderer/index.html');
  await expect(page.getByRole('button',{name:'New conversation'})).toBeEnabled();
}
test('welcome and original companion selection persist, motion can be disabled',async({page})=>{
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await open(page);await expect(page.locator('h1')).toContainText('What are we building?');
  await page.screenshot({path:'test-results/welcome.png'});
  await page.locator('#companion-picker').click();
  await expect(page.getByRole('button',{name:'Choose Moss'})).toHaveAttribute('aria-pressed','true');
  await page.screenshot({path:'test-results/companions.png'});
  await page.getByRole('button',{name:'Choose Orbit'}).click();
  await expect(page.locator('#pet-name')).toHaveText('Orbit');
  await page.locator('#motion').uncheck();await expect(page.locator('body')).toHaveClass(/no-motion/);
  await page.reload();await expect(page.locator('#pet-name')).toHaveText('Orbit');await expect(page.locator('body')).toHaveClass(/no-motion/);
  expect(errors).toEqual([]);
});
test('attachments, streamed reply, tool activity, approval and completion',async({page})=>{
  await open(page);await page.locator('#attach').click();await expect(page.locator('.attachment-name')).toHaveText('project-notes.md');
  await page.locator('#prompt').fill('Let’s make this idea real.');await page.getByRole('button',{name:'Send message'}).click();
  await expect(page.locator('body')).toHaveClass(/working/);await expect(page.getByRole('button',{name:'Stop response'})).toBeEnabled();
  expect(await page.evaluate(()=>window.__submitted[0].ids)).toEqual(['file-1']);
  await page.evaluate(()=>window.__event({type:'tool.start',payload:{tool_id:'t1',name:'terminal',args:{target:'omarchy',command:'pwd'}}}));
  await expect(page.locator('.tool-row')).toContainText('omarchy');
  await page.evaluate(()=>window.__event({type:'approval.request',payload:{request_id:'approval-1',command:'echo hello',reason:'Run this command?'}}));
  await expect(page.locator('#request-dialog')).toBeVisible();await expect(page.locator('body')).toHaveClass(/waiting/);
  await page.getByRole('button',{name:'Allow once'}).click();
  expect(await page.evaluate(()=>window.__requests.find(r=>r.method==='approval.respond').params.choice)).toBe('once');
  await page.evaluate(()=>window.__event({type:'message.delta',payload:{text:'Here’s a **small beginning**.\n\n<script>window.hacked=true</script>[bad](javascript:alert(1))'}}));
  await expect(page.locator('.message.assistant strong')).toHaveText('small beginning');
  expect(await page.evaluate(()=>window.hacked)).toBeUndefined();await expect(page.locator('.message-content a[href^="javascript:"]')).toHaveCount(0);
  await page.screenshot({path:'test-results/working.png'});
  await page.evaluate(()=>window.__event({type:'message.complete',payload:{text:'We made a start.\n\n- A clear direction\n- Room to experiment',status:'complete'}}));
  await expect(page.locator('#activity-label')).toHaveText('All done. What’s next?');await expect(page.getByRole('button',{name:'Stop response'})).toHaveCount(0);
  await expect(page.locator('.attachment')).toHaveCount(0);
});
test('failed submissions preserve draft and attachments, switching and search work',async({page})=>{
  await open(page);await page.locator('#attach').click();await page.locator('#prompt').fill('Keep my draft');
  await page.evaluate(()=>window.__fail=true);await page.getByRole('button',{name:'Send message'}).click();
  await expect(page.locator('#notice')).toContainText('could not be sent');await expect(page.locator('#prompt')).toHaveValue('Keep my draft');await expect(page.locator('.attachment')).toHaveCount(1);
  await page.getByRole('button',{name:'Remove project-notes.md'}).click();await expect(page.locator('.attachment')).toHaveCount(0);
  await page.locator('#search').fill('website');await expect(page.locator('.session-button')).toHaveCount(1);
  await page.locator('.session-button').click();await expect(page.locator('.message.assistant')).toContainText('lovely');
  await page.keyboard.press('Control+n');await expect(page.locator('#welcome')).toBeVisible();
});
test('paste, drop, stop, reconnect and small-window layout',async({page})=>{
  await open(page);
  await page.evaluate(()=>{const dt=new DataTransfer();dt.items.add(new File(['context'],'notes.txt',{type:'text/plain'}));document.dispatchEvent(new DragEvent('drop',{dataTransfer:dt,bubbles:true,cancelable:true}));});
  await expect(page.locator('.attachment')).toHaveCount(1);
  await page.evaluate(()=>{const dt=new DataTransfer();dt.items.add(new File(['image'],'clipboard.png',{type:'image/png'}));document.dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}));});
  await expect(page.locator('.attachment')).toHaveCount(2);await page.getByRole('button',{name:'Send message'}).click();
  await page.getByRole('button',{name:'Stop response'}).click();await expect(page.locator('#activity-label')).toHaveText('Paused. Take your time.');
  await page.evaluate(()=>window.__connection('error'));await expect(page.locator('#connection-label')).toHaveText('Connection issue');await page.locator('#connection-button').click();await expect(page.locator('#connection-label')).toHaveText('Hermes connected');
  await page.locator('#close-changes').click();
  await page.setViewportSize({width:850,height:650});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await expect(page.locator('#prompt')).toBeInViewport();await page.screenshot({path:'test-results/compact.png'});
});
test('batched clarification shows each question and submits the matching question ID',async({page})=>{
  await open(page);await page.locator('#prompt').fill('Help me choose');await page.getByRole('button',{name:'Send message'}).click();
  await page.evaluate(()=>window.__event({type:'clarify.request',payload:{request_id:'questions-1',questions:[{qid:'color',question:'Which color?',choices:['Green','Amber']},{qid:'size',question:'Which size?',choices:['Small','Large']}]}}));
  await expect(page.locator('#request-description')).toContainText('1 of 2 · Which color?');
  await page.getByRole('button',{name:'Green',exact:true}).click();await page.getByRole('button',{name:'Continue',exact:true}).click();
  await expect(page.locator('#request-description')).toContainText('2 of 2 · Which size?');
  await page.getByRole('button',{name:'Small',exact:true}).click();await page.getByRole('button',{name:'Continue',exact:true}).click();
  expect(await page.evaluate(()=>window.__requests.filter(r=>r.method==='clarify.respond').map(r=>[r.params.question_id,r.params.answer]))).toEqual([['color','Green'],['size','Small']]);
  await expect(page.locator('#request-dialog')).not.toBeVisible();
  await page.evaluate(()=>window.__event({type:'secret.request',payload:{request_id:'expired-1',prompt:'Enter token'}}));
  await expect(page.locator('#request-input')).toHaveAttribute('type','password');
  await page.evaluate(()=>window.__event({type:'secret.expire',payload:{request_id:'expired-1'}}));
  await expect(page.locator('#request-dialog')).not.toBeVisible();
});

test('live theme updates recolor surfaces, pets, dialogs and diff without losing the draft',async({page})=>{
  await open(page);await page.locator('#prompt').fill('Keep this draft through the theme change');
  await expect(page.locator('.changed-file')).toHaveCount(2);
  await expect(page.locator('.diff-line.add').first()).toContainText('useOmarchyTheme');
  await page.locator('#companion-picker').click();
  const white=fixture('white');await page.evaluate(theme=>window.__theme(theme),white);
  await expect(page.locator('#theme-name')).toHaveText('white');
  const rgb=hex=>'rgb('+[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16)).join(', ')+')';
  await expect(page.locator('body')).toHaveCSS('background-color',rgb(white.colors.background));
  await expect(page.locator('#mini-pet .leaf-right')).toHaveCSS('fill',rgb(white.colors.green));
  await expect(page.locator('#companion-dialog')).toHaveCSS('background-color',rgb(white.colors.background));
  await page.screenshot({path:'test-results/companions-light.png'});
  await page.getByRole('button',{name:'Close companion picker'}).click();
  await expect(page.locator('#prompt')).toHaveValue('Keep this draft through the theme change');
  await expect(page.locator('.diff-line.add').first()).toHaveCSS('color',rgb(white.colors.green));
  await page.screenshot({path:'test-results/theme-light.png'});
  await page.evaluate(theme=>window.__theme({...theme,revision:3}),fixture('gruvbox'));
  await expect(page.locator('#theme-name')).toHaveText('gruvbox');
  await page.screenshot({path:'test-results/theme-gruvbox.png'});
  await page.locator('#close-changes').click();await expect(page.locator('#review-panel')).not.toBeVisible();
  await page.locator('#changes-toggle').click();await expect(page.locator('#review-panel')).toBeVisible();
});

test('inverted selection colors remain readable in conversations and changes',async({page})=>{
  await open(page);
  const theme={...buildTheme({background:'#0c0b0c',foreground:'#fafcfb',selection_background:'#fafcfb',selection_foreground:'#0c0b0c'}),revision:2};
  await page.evaluate(theme=>window.__theme(theme),theme);
  await page.locator('.session-button').first().click();
  const active=page.locator('.session-button.active');
  await expect(active).toHaveCSS('background-color','rgb(250, 252, 251)');
  await expect(active).toHaveCSS('color','rgb(12, 11, 12)');
  await active.hover();
  await expect(active).toHaveCSS('color','rgb(12, 11, 12)');
  const file=page.locator('.changed-file').first();await file.click();await file.hover();
  await expect(file).toHaveCSS('color','rgb(12, 11, 12)');
  await expect(file.locator('.file-status')).toHaveCSS('color','rgb(12, 11, 12)');
});
