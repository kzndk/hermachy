const { Transport } = require('../transport.cjs');
const t = new Transport();
let probed = false;
t.on('status', async status => {
  console.log('STATUS', JSON.stringify(status));
  if (status.state !== 'connected' || probed) return;
  probed = true;
  try {
    const s = await t.request('session.create',{cols:120});
    console.log('SESSION',JSON.stringify(s));
    const list=await t.request('session.list',{limit:2});
    console.log('LIST SHAPE',Object.keys(list),list.sessions?.map(row=>({keys:Object.keys(row),id:row.id})));
    const image=await t.request('image.attach_bytes',{session_id:s.session_id,filename:'probe.png',content_base64:require('node:fs').readFileSync(require('node:path').join(__dirname,'../assets/hermachy.png')).toString('base64')});
    console.log('IMAGE',JSON.stringify(image));
    await t.request('image.detach',{session_id:s.session_id,path:image.path});
  } catch(error) {console.error(error);process.exitCode=1;} finally {await t.stop();}
});
t.start(process.cwd());
