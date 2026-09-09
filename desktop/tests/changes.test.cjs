const {test}=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs/promises');const os=require('node:os');const path=require('node:path');const {execFileSync}=require('node:child_process');
const {Changes}=require('../changes.cjs');
test('review reads tracked, staged and untracked changes without modifying files',async()=>{
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'hermachy-review-'));
  const git=(...args)=>execFileSync('git',['-C',root,...args],{stdio:'pipe'});
  try {
    git('init');git('config','user.email','test@example.invalid');git('config','user.name','Test');
    await fs.writeFile(path.join(root,'example.txt'),'before\n');git('add','.');git('commit','-m','fixture');
    await fs.writeFile(path.join(root,'example.txt'),'after\n');git('add','example.txt');
    await fs.writeFile(path.join(root,'new <file>.txt'),'untracked content\n');
    await fs.symlink('/etc/hosts',path.join(root,'outside'));
    const changes=new Changes(),list=await changes.list(root);assert.equal(list.available,true);assert.equal(list.files.length,3);
    assert.match((await changes.diff('example.txt')).text,/-before\n\+after/);
    assert.match((await changes.diff('new <file>.txt')).text,/\+untracked content/);
    await assert.rejects(changes.diff('../outside'),/select a file/);
    await assert.rejects(changes.diff('outside'),/outside this workspace/);
    assert.equal(await fs.readFile(path.join(root,'example.txt'),'utf8'),'after\n');
    assert.equal(git('diff','--cached','--name-only').toString().trim(),'example.txt');
  } finally {await fs.rm(root,{recursive:true,force:true});}
});
