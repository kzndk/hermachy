import { marked } from '../node_modules/marked/lib/marked.esm.js';
import DOMPurify from '../node_modules/dompurify/dist/purify.es.mjs';
import { pets, petSVG } from './pets.js';
import { icon } from './icons.js';
import { applyTheme } from './theme.js';
const $ = id => document.getElementById(id);
const api = window.hermachy;
const state = {settings:{pet:'moss',motion:true},connection:'disconnected',session:null,stored:null,sessions:[],files:[],busy:false,submitting:false,loading:false,assistant:null,started:0,tools:new Map(),requests:[],pending:null,initialized:false,generation:0,autoScroll:true};
const escape = text => String(text ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function hydrateIcons() { document.querySelectorAll('[data-icon]').forEach(el => {el.innerHTML=icon(el.dataset.icon); el.removeAttribute('data-icon');}); }
function notice(error) { $('notice-text').textContent=String(error?.message || error).replace(/^Error invoking remote method '[^']+': (Error: )?/, ''); $('notice').hidden=false; }
function run(fn) { return (...args) => Promise.resolve().then(()=>fn(...args)).catch(notice); }
function scrollDown(force=false) { if (state.autoScroll || force) requestAnimationFrame(()=>{$('scroll').scrollTop=$('scroll').scrollHeight;}); }
function setActivity(text,mode='idle') {
  $('activity-label').textContent=text;
  document.body.classList.remove('working','waiting','done');
  if(mode!=='idle') document.body.classList.add(mode);
}
function updateControls() {
  $('send').disabled=state.loading || state.submitting || !state.session || state.connection!=='connected' || (!state.busy && !$('prompt').value.trim() && !state.files.length);
  $('send').classList.toggle('working',state.busy);
  $('send').innerHTML=icon(state.busy?'stop':'arrow');
  $('send').setAttribute('aria-label',state.busy?'Stop response':'Send message');
  $('new-chat').disabled=state.busy || state.loading || state.submitting || state.connection!=='connected';
  $('workspace').disabled=state.busy || state.loading || state.submitting;
  $('prompt').disabled=state.submitting;
  $('attach').disabled=state.submitting;
  $('reconnect').disabled=state.busy || state.submitting;
}
function applySettings(settings) {
  state.settings={...state.settings,...settings}; const pet=pets[state.settings.pet] || pets.moss;
  document.body.classList.toggle('no-motion',!state.settings.motion);
  $('mini-pet').innerHTML=petSVG(state.settings.pet);
  $('pet-name').textContent=pet.name; $('motion').checked=state.settings.motion; $('notifications').checked=state.settings.notifications;
  $('workspace-name').textContent=(state.settings.cwd || '').split('/').filter(Boolean).pop() || 'Home';
  $('workspace').title=state.settings.cwd || '';
  $('top-workspace').textContent=$('workspace-name').textContent;
  if(!state.changesInitialized){state.changesInitialized=true;setChangesVisible(state.settings.changes ?? window.innerWidth>1100);}
  $('pet-options').innerHTML=Object.entries(pets).map(([id,pet])=>`<button class="pet-option ${id===state.settings.pet?'selected':''}" data-pet="${id}" aria-pressed="${id===state.settings.pet}" aria-label="Choose ${pet.name}">${id===state.settings.pet?`<span class="pet-selected">${icon('check')}</span>`:''}${petSVG(id)}<small>${pet.kind}</small><h3>${pet.name}</h3><p>${pet.description}</p></button>`).join('');
}
async function refreshSessions() {
  const result=await api.rpc('session.list',{limit:80}); state.sessions=result.sessions || []; renderSessions();
}
function renderSessions() {
  const query=$('search').value.toLowerCase();
  const rows=state.sessions.filter(row=>(row.title || row.preview || row.id || '').toLowerCase().includes(query));
  $('session-count').textContent=state.sessions.length;
  $('session-list').innerHTML=rows.length?rows.map(row=>{
    const id=row.id || row.session_id; const title=row.title || row.preview || 'Untitled conversation';
    return `<button class="session-button ${id===state.stored?'active':''}" data-session="${escape(id)}" title="${escape(title)}">${icon('chat')}<span>${escape(title)}</span></button>`;
  }).join(''):`<p class="empty-list">${query?'No matching conversations.':'Your next idea starts here.'}</p>`;
}
function clearConversation() {
  $('messages').replaceChildren(); $('welcome').hidden=false; state.assistant=null; state.tools.clear(); state.autoScroll=true;
  state.pending=null; state.requests=[]; $('request-dialog').close(); $('elapsed').textContent='';
}
function modelInfo(info) { if(info?.model) {$('model-label').textContent=info.model; $('model-label').title=info.model;} }
function loadResult(result, title) {
  state.session=result.session_id; state.stored=result.stored_session_id || result.session_key || state.session;
  if(!state.session) throw new Error('Hermes did not return a session identifier');
  clearConversation(); modelInfo(result.info);
  $('conversation-title').textContent=title || 'New conversation';
  for(const message of result.messages || []) {
    const role=message.role || message.kind; if(!['user','assistant'].includes(role)) continue;
    const text=message.text ?? message.content ?? '';
    if(typeof text==='string' && text.trim()) addMessage(role,text);
    else if(Array.isArray(text)) addMessage(role,text.filter(p=>p.type==='text').map(p=>p.text).join('\n'));
  }
  state.busy=Boolean(result.running || result.info?.running);
  if(state.busy) {state.started=Date.now(); setActivity('Continuing your conversation…','working');} else setActivity('Ready when you are');
  renderSessions();
  if($('messages').children.length) scrollDown(true); else $('scroll').scrollTop=0;
  updateControls();
  if(result.pending_approval) queueRequest({type:'approval.request',payload:result.pending_approval,session_id:state.session});
}
async function newConversation() {
  if(state.busy || state.loading || state.submitting) return;
  state.loading=true; updateControls();
  try { const result=await api.rpc('session.create',{}); loadResult(result); } finally {state.loading=false; updateControls();}
}
async function resumeConversation(id) {
  if(state.busy || state.loading || state.submitting) {notice('Stop the current response before switching conversations.'); return;}
  state.loading=true; updateControls();
  try {
    const result=await api.rpc('session.resume',{session_id:id,cols:120});
    const row=state.sessions.find(row=>(row.id || row.session_id)===id);
    loadResult(result,row?.title || 'Conversation');
  } finally {state.loading=false; updateControls();}
}
function markdown(text) { return DOMPurify.sanitize(marked.parse(text || '',{breaks:true}),{USE_PROFILES:{html:true},FORBID_TAGS:['img','style','input','button','form','iframe','video','audio'],FORBID_ATTR:['style']}); }
function addMessage(role,text='',files=[]) {
  $('welcome').hidden=true;
  const article=document.createElement('article'); article.className=`message ${role}`;
  const heading=document.createElement('div'); heading.className='message-header'; heading.innerHTML=role==='assistant'?`${icon('spark')}Hermes`:'You'; article.append(heading);
  const content=document.createElement('div'); content.className='message-content';
  if(role==='assistant') content.innerHTML=markdown(text); else content.textContent=text;
  article.append(content);
  if(files.length) {
    const list=document.createElement('div'); list.className='sent-attachments';
    for(const file of files) {
      if(file.preview) {const img=document.createElement('img'); img.src=file.preview; img.alt=file.name; list.append(img);}
      else {const label=document.createElement('span'); label.className='sent-file'; label.textContent=file.name; list.append(label);}
    } article.append(list);
  }
  $('messages').append(article); scrollDown(); return {article,content,text};
}
function assistant() {
  if(!state.assistant) {
    state.assistant=addMessage('assistant');
    state.assistant.content.innerHTML='<span class="thinking-dots" aria-label="Thinking"><i></i><i></i><i></i></span>';
  }
  return state.assistant;
}
let paintPending=false;
function paintAssistant() {
  if(paintPending) return; paintPending=true;
  requestAnimationFrame(()=>{paintPending=false;if(state.assistant)state.assistant.content.innerHTML=markdown(state.assistant.text);scrollDown();});
}
function renderFiles() {
  $('attachments').innerHTML=state.files.map(file=>`<div class="attachment">${file.preview?`<img src="${escape(file.preview)}" alt="${escape(file.name)}">`:icon('file')}<span><span class="attachment-name">${escape(file.name)}</span><small>${file.size>=1048576?(file.size/1048576).toFixed(1)+' MB':Math.ceil(file.size/1024)+' KB'}</small></span><button type="button" class="icon-button" data-remove="${file.id}" aria-label="Remove ${escape(file.name)}" ${state.submitting?'disabled':''}>${icon('close')}</button></div>`).join('');
  updateControls();
}
async function chooseFiles() {
  if(state.submitting)return; const result=await api.chooseFiles(); state.files.push(...result.items); renderFiles();
  if(result.errors?.length) notice(result.errors.join('\n')); $('prompt').focus();
}
async function addFiles(files) {
  if(state.submitting) {notice('Wait for the current prompt to be submitted.'); return;}
  for(const file of files) {
    try { if(file.size>8*1024*1024) throw new Error(`${file.name}: each attachment can be up to 8 MB`);
      state.files.push(await api.addFile(file.name,new Uint8Array(await file.arrayBuffer())));
    } catch(error) {notice(error);}
  } renderFiles(); $('prompt').focus();
}
async function submit() {
  if(state.busy) {setActivity('Stopping…','waiting'); await api.rpc('session.interrupt',{session_id:state.session}); return;}
  if(state.loading || state.submitting || !state.session || state.connection!=='connected') return;
  const text=$('prompt').value, files=[...state.files]; if(!text.trim()&&!files.length)return;
  state.submitting=true; state.busy=true; state.started=Date.now(); state.assistant=null; state.tools.clear(); state.autoScroll=true;
  updateControls(); renderFiles(); setActivity(files.length?'Preparing your attachments…':'Thinking it through…','working');
  const user=addMessage('user',text,files); const previousTitle=$('conversation-title').textContent;
  if(previousTitle==='New conversation') $('conversation-title').textContent=text.trim().slice(0,70) || 'A closer look';
  try {
    await api.submit({sessionId:state.session,text,ids:files.map(f=>f.id)});
    $('prompt').value=''; $('prompt').style.height=''; state.files=[];
  } catch(error) {
    if(!state.assistant) user.article.remove();
    state.busy=false; setActivity('Your draft is here when you’re ready'); notice(error);
    if(!$('messages').children.length) $('welcome').hidden=false;
  } finally {state.submitting=false;renderFiles();updateControls();$('prompt').focus();}
}
function toolEvent(type,payload) {
  const key=payload.tool_id || payload.name;
  let row=state.tools.get(key);
  if(!row) {
    const node=document.createElement('details'); node.className='tool-row';
    const summary=document.createElement('summary'), detail=document.createElement('pre'); node.append(summary,detail);
    assistant().article.insertBefore(node,state.assistant.content); row={node,summary,detail}; state.tools.set(key,row);
  }
  const target=payload.args?.target; const name=(payload.name || 'Tool').replaceAll('_',' ');
  row.summary.textContent=`${type==='tool.complete'?'✓':'◌'} ${name}${target?` · ${target}`:''}${payload.context?` · ${payload.context}`:''}`;
  row.detail.textContent=[payload.args?.command || (payload.args?JSON.stringify(payload.args,null,2):''),payload.result || payload.output || ''].filter(Boolean).join('\n\n').slice(0,20000);
  if(type==='tool.start')setActivity(`Working · ${name}${target?` on ${target}`:''}`,'working');
  scrollDown();
}
function queueRequest(event) {
  const id=event.payload?.request_id;
  if(id && (state.pending?.payload?.request_id===id || state.requests.some(r=>r.payload?.request_id===id))) return;
  state.requests.push(event); showNextRequest();
}
function showNextRequest() {
  if(state.pending || !state.requests.length)return;
  state.pending=state.requests.shift(); const {type,payload:p={}}=state.pending;
  const approval=type==='approval.request', secret=['sudo.request','secret.request'].includes(type);
  const questions=Array.isArray(p.questions)?p.questions:[];
  const question=questions[state.pending.questionIndex || 0];
  $('request-title').textContent=approval?'A moment for your approval.':secret?'A private input is needed.':'Your agent has a question.';
  $('request-description').textContent=question?`${(state.pending.questionIndex || 0)+1} of ${questions.length} · ${question.question}`:(p.question || p.prompt || p.description || p.reason || (approval?'Review this action before your agent continues.':'Please provide a response to continue.'));
  $('request-command').hidden=!p.command; $('request-command').textContent=p.command || '';
  $('request-input-label').hidden=approval; $('request-input').value=''; $('request-input').type=secret?'password':'text';
  $('request-deny').textContent=approval?'Deny':'Cancel'; $('request-accept').textContent=approval?'Allow once':'Continue';
  $('request-accept').hidden=approval && Array.isArray(p.choices) && !p.choices.includes('once');
  $('request-options').replaceChildren();
  const options=approval?[]:(question?.choices || p.choices || p.options || []);
  for(const option of options) {
    const button=document.createElement('button'); button.className='secondary-button'; const value=typeof option==='string'?option:(option.label || option.value || '');
    button.textContent=value; button.addEventListener('click',()=>{$('request-input').value=value;}); $('request-options').append(button);
  }
  $('request-dialog').showModal(); setActivity('Waiting for you','waiting');
}
async function respond(accept) {
  if(!state.pending)return; const pending=state.pending, p=pending.payload || {},type=pending.type;
  let method,params={session_id:state.session,request_id:p.request_id};
  if(type==='approval.request') {method='approval.respond';params.choice=accept?'once':'deny';}
  else if(type==='sudo.request') {method='sudo.respond';params.password=accept?$('request-input').value:'';}
  else if(type==='secret.request') {method='secret.respond';params.value=accept?$('request-input').value:'';}
  else {method='clarify.respond';params.answer=accept?$('request-input').value:'Cancelled by user';
    const question=p.questions?.[pending.questionIndex || 0]; if(question)params.question_id=question.qid;
  }
  $('request-accept').disabled=true; $('request-deny').disabled=true;
  try {
    await api.rpc(method,params);
    if(p.questions && !accept) {
      for(const question of p.questions.slice((pending.questionIndex || 0)+1)) await api.rpc(method,{...params,question_id:question.qid,answer:'Cancelled by user'});
    }
    $('request-input').value='';$('request-dialog').close();state.pending=null;
    if(accept && p.questions && (pending.questionIndex || 0)+1<p.questions.length) state.requests.unshift({...pending,questionIndex:(pending.questionIndex || 0)+1});
    if(state.busy)setActivity('Back to work…','working');showNextRequest();
  }
  finally {$('request-accept').disabled=false;$('request-deny').disabled=false;}
}
function gatewayEvent(event) {
  if(event.session_id!==state.session)return; const p=event.payload || {},type=event.type;
  if(type==='session.info') {modelInfo(p);return;}
  if(type==='session.title') {$('conversation-title').textContent=p.title || 'Conversation';run(refreshSessions)();return;}
  if(type==='message.start') {state.busy=true;state.started ||= Date.now();assistant();setActivity('Thinking it through…','working');}
  else if(type==='message.delta') {assistant().text+=p.text || '';paintAssistant();setActivity('Putting it into words…','working');}
  else if(type==='reasoning.delta' || type==='reasoning.available') setActivity('Thinking it through…','working');
  else if(type==='tool.start' || type==='tool.complete') toolEvent(type,p);
  else if(type==='tool.generating') setActivity(`Preparing ${(p.name || 'a tool').replaceAll('_',' ')}…`,'working');
  else if(type==='status.update') {if(p.text)setActivity(p.text,state.busy?'working':'idle');}
  else if(type==='message.complete') {
    const current=assistant(); if(typeof p.text==='string') current.text=p.text;
    current.content.innerHTML=markdown(current.text || (p.status==='interrupted'?'Response stopped.':'This turn finished without a text response.'));
    state.busy=false; state.assistant=null; state.pending=null;state.requests=[];$('request-dialog').close();
    setActivity(p.status==='interrupted'?'Paused. Take your time.':p.status==='error'?'Something needs a closer look':'All done. What’s next?',p.status==='complete'?'done':'idle');
    run(refreshSessions)();scrollDown();
  } else if(['approval.request','clarify.request','sudo.request','secret.request'].includes(type)) queueRequest(event);
  else if(type.endsWith('.expire')) {
    state.requests=state.requests.filter(r=>r.payload?.request_id!==p.request_id);
    if(state.pending?.payload?.request_id===p.request_id){state.pending=null;$('request-dialog').close();if(state.busy)setActivity('Back to work…','working');showNextRequest();}
  }
  else if(type==='error') {notice(p.message || 'Hermes reported an error');state.busy=false;setActivity('Something needs a closer look');}
  updateControls();
}
async function onConnection(status) {
  const previous=state.connection; state.connection=status.state;
  $('connection-button').className=`connection-pill ${status.state}`; $('sidebar-dot').className=`connection-dot ${status.state}`;
  const labels={connected:'Hermes connected',connecting:'Connecting',reconnecting:'Reconnecting',disconnected:'Disconnected',error:'Connection issue'};
  $('connection-label').textContent=labels[status.state] || status.state;
  $('sidebar-status').textContent=status.state==='connected'?(status.hostname || 'Connected to Hermes'):labels[status.state];
  if(status.cwd) {state.settings.cwd=status.cwd;$('workspace-name').textContent=status.cwd.split('/').filter(Boolean).pop() || '/';$('top-workspace').textContent=$('workspace-name').textContent;$('workspace').title=status.cwd;run(refreshChanges)();}
  if(status.state==='connected' && (previous==='connecting' || !state.initialized)) {
    state.initialized=true; state.session=null; state.busy=false;
    await newConversation(); await refreshSessions();
  } else if(status.state==='reconnecting') setActivity('Finding our connection again…','waiting');
  else if(status.state==='connected' && previous==='reconnecting') setActivity(state.busy?'Back to work…':'Ready when you are',state.busy?'working':'idle');
  else if(['error','disconnected'].includes(status.state)) {state.busy=false;state.session=null;setActivity('Reconnect to pick up where you left off');if(status.message)notice(status.message);}
  updateControls();
}
let changesVisible=false,changesLoading=false,selectedFile=null,changeSnapshot='',diffRequest=0;
function setChangesVisible(visible) {
  changesVisible=visible;
  document.body.classList.toggle('changes-closed',!visible);
  $('review-panel').hidden=!visible;
  $('changes-toggle').setAttribute('aria-expanded',String(visible));
  if(visible)run(refreshChanges)();
}
function changesEmpty(message) {
  $('diff-heading').hidden=true;
  $('diff-content').innerHTML=`<div class="review-empty">${icon('diff')}<p>${escape(message)}</p></div>`;
}
async function refreshChanges() {
  if(!api?.changes || changesLoading)return;
  changesLoading=true;$('refresh-changes').disabled=true;
  const cwd=state.settings.cwd;
  try {
    const result=await api.changes();
    if(cwd!==state.settings.cwd)return;
    const files=result.files || [];
    $('change-count').textContent=files.length;
    $('branch-name').textContent=result.branch || 'Workspace';
    $('changed-files').innerHTML=files.map(file=>`<button class="changed-file ${selectedFile===file.path?'selected':''}" data-file="${escape(file.path)}" title="${escape(file.path)}">${icon('file')}<span class="file-path">${escape(file.path)}</span><span class="file-status ${file.status}">${({new:'U',added:'A',deleted:'D',renamed:'R',modified:'M'})[file.status] || 'M'}</span></button>`).join('');
    if(!files.length){selectedFile=null;changeSnapshot='';diffRequest++;changesEmpty(result.available?'Working tree clean. Changes will appear as you work.':result.message || 'Choose a Git workspace to review changes.');return;}
    if(!files.some(file=>file.path===selectedFile))selectedFile=files[0].path;
    if(changesVisible)await showDiff(selectedFile);
  } catch(error){changesEmpty(error.message || 'Could not read workspace changes.');}
  finally{changesLoading=false;$('refresh-changes').disabled=false;}
}
async function showDiff(name) {
  const request=++diffRequest;selectedFile=name;
  document.querySelectorAll('.changed-file').forEach(button=>button.classList.toggle('selected',button.dataset.file===name));
  $('diff-name').textContent=name;$('diff-heading').hidden=false;
  try {
    const result=await api.diff(name);if(request!==diffRequest)return;
    const snapshot=JSON.stringify([name,result]);if(snapshot===changeSnapshot)return;changeSnapshot=snapshot;
    if(result.kind==='notice'){$('diff-content').innerHTML=`<div class="review-empty"><p>${escape(result.text)}</p></div>`;return;}
    const lines=result.text.split('\n').map(line=>{
      const type=line.startsWith('@@')?'hunk':/^(diff |index |--- |\+\+\+ )/.test(line)?'meta':line.startsWith('+')?'add':line.startsWith('-')?'remove':'';
      return `<span class="diff-line ${type}">${escape(line) || ' '}</span>`;
    }).join('');
    $('diff-content').innerHTML=`<div class="diff-lines">${lines}</div>`;
  }catch(error){if(request===diffRequest)changesEmpty(error.message);}
}
$('sidebar-toggle').onclick=()=>document.body.classList.toggle('sidebar-hidden');
$('changes-toggle').onclick=run(async()=>{state.changesUserToggled=true;setChangesVisible(!changesVisible);state.settings=await api.preferences({changes:changesVisible});});
$('close-changes').onclick=run(async()=>{state.changesUserToggled=true;setChangesVisible(false);state.settings=await api.preferences({changes:false});});
window.addEventListener('resize',()=>{if(window.innerWidth<=1100 && !state.changesUserToggled)setChangesVisible(false);});
$('refresh-changes').onclick=run(refreshChanges);
$('changed-files').onclick=run(event=>{const button=event.target.closest('[data-file]');if(button)return showDiff(button.dataset.file);});
setInterval(()=>{if(changesVisible && !document.hidden)run(refreshChanges)();},15000);
hydrateIcons();
$('notice-close').onclick=()=>{$('notice').hidden=true;};
$('composer').addEventListener('submit',event=>{event.preventDefault();run(submit)();});
$('prompt').addEventListener('input',()=>{const field=$('prompt');field.style.height='auto';field.style.height=Math.min(field.scrollHeight,220)+'px';updateControls();});
$('prompt').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();if(!state.busy)run(submit)();}});
$('new-chat').onclick=run(newConversation); $('search').oninput=renderSessions;
$('session-list').onclick=run(event=>{const button=event.target.closest('[data-session]');if(button)return resumeConversation(button.dataset.session);});
$('attach').onclick=run(chooseFiles);$('suggest-attach').onclick=run(chooseFiles);
$('attachments').onclick=run(async event=>{const button=event.target.closest('[data-remove]');if(!button || state.submitting)return;await api.removeFile(button.dataset.remove);state.files=state.files.filter(f=>f.id!==button.dataset.remove);renderFiles();});
$('workspace').onclick=run(async()=>{if(state.busy)return;const settings=await api.workspace();if(settings)applySettings(settings);});
$('companion-picker').onclick=()=>{$('companion-dialog').showModal();};
$('pet-options').onclick=run(async event=>{const button=event.target.closest('[data-pet]');if(button)applySettings(await api.preferences({pet:button.dataset.pet}));});
$('motion').onchange=run(async event=>applySettings(await api.preferences({motion:event.target.checked})));
$('notifications').onchange=run(async event=>applySettings(await api.preferences({notifications:event.target.checked})));
$('settings').onclick=()=>{$('settings-dialog').showModal();};
$('reconnect').onclick=run(async()=>{$('settings-dialog').close();state.initialized=false;await api.connect();});
$('connection-button').onclick=run(async()=>{if(['error','disconnected'].includes(state.connection)){state.initialized=false;await api.connect();}else $('settings-dialog').showModal();});
$('request-deny').onclick=run(()=>respond(false));$('request-accept').onclick=run(()=>respond(true));
$('request-dialog').addEventListener('cancel',event=>{event.preventDefault();run(()=>respond(false))();});
$('request-input').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();run(()=>respond(true))();}});
document.querySelectorAll('[data-close]').forEach(button=>button.onclick=()=>$(button.dataset.close).close());
document.querySelectorAll('[data-prompt]').forEach(button=>button.onclick=()=>{$('prompt').value=button.dataset.prompt;$('prompt').focus();updateControls();});
document.addEventListener('click',event=>{const link=event.target.closest('a[href]');if(link){event.preventDefault();run(()=>api.openLink(link.href))();}});
document.addEventListener('keydown',event=>{
  if(event.ctrlKey && event.key.toLowerCase()==='n'){event.preventDefault();run(newConversation)();}
  if(event.ctrlKey && event.key===','){event.preventDefault();$('settings-dialog').showModal();}
  if(event.key==='/'&&!['INPUT','TEXTAREA'].includes(document.activeElement.tagName)){event.preventDefault();$('search').focus();}
});
let dragDepth=0;
document.addEventListener('dragenter',event=>{if([...event.dataTransfer.types].includes('Files')){event.preventDefault();dragDepth++;$('drop-overlay').hidden=false;}});
document.addEventListener('dragover',event=>{event.preventDefault();event.dataTransfer.dropEffect='copy';});
document.addEventListener('dragleave',()=>{if(--dragDepth<=0){dragDepth=0;$('drop-overlay').hidden=true;}});
document.addEventListener('drop',event=>{event.preventDefault();dragDepth=0;$('drop-overlay').hidden=true;run(()=>addFiles([...event.dataTransfer.files]))();});
document.addEventListener('paste',event=>{if(document.querySelector('dialog[open]'))return;const files=[...event.clipboardData.items].filter(i=>i.kind==='file').map(i=>i.getAsFile()).filter(Boolean);if(files.length){event.preventDefault();run(()=>addFiles(files))();}});
$('scroll').addEventListener('scroll',()=>{const el=$('scroll');state.autoScroll=el.scrollHeight-el.scrollTop-el.clientHeight<90;});
setInterval(()=>{if(state.busy&&state.started){const elapsed=Math.floor((Date.now()-state.started)/1000);$('elapsed').textContent=`${Math.floor(elapsed/60)}:${String(elapsed%60).padStart(2,'0')}`;}},1000);
if(!api) {document.body.classList.add('theme-ready');notice('Open this app with hermachy-app to connect to your agent.');applySettings(state.settings);}
else {
  api.onEvent(gatewayEvent); api.onConnection(run(onConnection)); api.onTheme?.(applyTheme);
  run(async()=>{const data=await api.bootstrap();applyTheme(data.theme);applySettings(data.settings);if(state.connection==='disconnected')await onConnection(data.connection);$('prompt').focus();})();
}
