const fs = require('node:fs/promises');
const path = require('node:path');
const {execFile} = require('node:child_process');
const {promisify} = require('node:util');
const execute = promisify(execFile);
async function git(cwd,args) {
  const env=Object.fromEntries(Object.entries(process.env).filter(([key])=>!key.startsWith('GIT_')));
  const {stdout}=await execute('git',['--no-optional-locks','-c','core.fsmonitor=false','-C',cwd,...args],{encoding:'utf8',maxBuffer:2*1024*1024,timeout:10000,env:{...env,GIT_TERMINAL_PROMPT:'0'}});
  return stdout;
}
class Changes {
  constructor(){this.root=null;this.files=new Map();}
  async list(cwd) {
    this.root=null;this.files.clear();
    let root;
    try{root=(await git(cwd,['rev-parse','--show-toplevel'])).trim();}
    catch{return{available:false,files:[],message:'Choose a Git workspace to review changes.'};}
    const [raw,branch]=await Promise.all([git(root,['status','--porcelain=v1','-z','--untracked-files=all']),git(root,['branch','--show-current'])]);
    const records=raw.split('\0');const files=[];
    for(let i=0;i<records.length;i++) {
      const record=records[i];if(!record)continue;
      const status=record.slice(0,2),name=record.slice(3);
      if(/[RC]/.test(status))i++;
      files.push({path:name,status:status==='??'?'new':status.includes('D')?'deleted':status.includes('R')?'renamed':status.includes('A')?'added':'modified',untracked:status==='??'});
    }
    this.root=root;this.files=new Map(files.map(file=>[file.path,file]));
    return{available:true,root,branch:branch.trim() || 'Detached HEAD',files};
  }
  async diff(name) {
    const file=this.files.get(name),root=this.root;if(!file||!root)throw new Error('Refresh changes and select a file');
    if(file.untracked) {
      const absolute=await fs.realpath(path.join(root,name));
      if(!absolute.startsWith(root+path.sep))throw new Error('The file points outside this workspace');
      const handle=await fs.open(absolute,'r');
      try{
        const stat=await handle.stat();if(!stat.isFile())return{text:'Preview is unavailable for this file type.',kind:'notice'};
        if(stat.size>512*1024)return{text:'This file exceeds the 512 KB preview limit.',kind:'notice'};
        const data=Buffer.alloc(512*1024+1);const {bytesRead}=await handle.read(data,0,data.length,0);
        if(bytesRead>512*1024)return{text:'This file exceeds the 512 KB preview limit.',kind:'notice'};
        const bytes=data.subarray(0,bytesRead);
        if(bytes.includes(0))return{text:'Binary file. Text preview is unavailable.',kind:'notice'};
        return{text:`--- /dev/null\n+++ b/${name}\n`+bytes.toString('utf8').split('\n').map(line=>'+'+line).join('\n'),kind:'diff'};
      }finally{await handle.close();}
    }
    let base=[];try{await git(root,['rev-parse','--verify','HEAD']);base=['HEAD'];}catch{base=['--cached'];}
    const text=await git(root,['diff','--no-ext-diff','--no-textconv','--color=never',...base,'--',name]);
    return{text:text || 'No text changes to display.',kind:text?'diff':'notice'};
  }
}
module.exports={Changes};
