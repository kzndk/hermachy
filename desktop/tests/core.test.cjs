const {test}=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs/promises');const os=require('node:os');const path=require('node:path');
const {Attachments,MAX_FILE}=require('../attachments.cjs');const {Transport}=require('../transport.cjs');
test('attachments snapshot exact bytes, reject limits, distinguish images and retain sent files',async()=>{
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'hermachy-attachments-'));
  try {
    const a=new Attachments(root);const bytes=Buffer.from('original context');const item=await a.add('../notes.txt',bytes);
    const record=a.get([item.id])[0];assert.equal(record.name,'notes.txt');assert.deepEqual(await fs.readFile(record.path),bytes);
    assert.equal((await fs.stat(record.path)).mode&0o777,0o600);
    await assert.rejects(a.add('empty',Buffer.alloc(0)),/empty/);await assert.rejects(a.add('large',Buffer.alloc(MAX_FILE+1)),/8 MB/);
    assert.throws(()=>a.get([item.id,item.id]),/Invalid/);
    const png=await a.add('not-an-extension',await fs.readFile(path.join(__dirname,'../assets/hermachy.png')));assert.equal(png.kind,'image');assert.match(png.preview,/^data:image\/png/);
    const svg=await a.add('bad.svg',Buffer.from('<svg onload="evil()"/>'));assert.equal(svg.kind,'file');assert.equal(svg.preview,null);
    record.sent=true;await a.clearPending();assert.deepEqual(await fs.readFile(record.path),bytes);assert.throws(()=>a.get([png.id]),/no longer/);
  } finally {await fs.rm(root,{recursive:true,force:true});}
});
test('transport resolves interleaved RPCs and events, preserves errors and rejects uncertain requests',async()=>{
  const t=new Transport();const frames=[];t.child={stdin:{writable:true,write:(frame,cb)=>{frames.push(JSON.parse(frame));cb();}}};t.ready=true;
  const first=t.request('first'),second=t.request('second');let event;t.on('event',e=>event=e);
  t.receive({method:'event',params:{type:'message.delta',payload:{text:'Danish æøå 🐾'}}});
  t.receive({id:frames[1].id,result:{ok:2}});t.receive({id:frames[0].id,result:{ok:1}});
  assert.deepEqual(await second,{ok:2});assert.deepEqual(await first,{ok:1});assert.equal(event.payload.text,'Danish æøå 🐾');
  const failed=t.request('bad');t.receive({id:frames[2].id,error:{code:4001,message:'missing session'}});await assert.rejects(failed,{code:4001});
  await assert.rejects(t.request('uncertain',{},5),/outcome is uncertain/);assert.equal(t.pending.size,0);
});
