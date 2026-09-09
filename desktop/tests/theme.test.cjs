const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');const path=require('node:path');const os=require('node:os');
const {ThemeWatcher,buildTheme,parsePalette,resolvePalette,contrast}=require('../theme.cjs');
const palette=async name=>parsePalette(await fs.readFile(path.join(__dirname,'fixtures',name+'.toml'),'utf8'));
function nextTheme(watcher,predicate) {
  return new Promise((resolve,reject)=>{const timer=setTimeout(()=>{watcher.off('theme',listener);reject(new Error('Theme update did not arrive'));},5000);function listener(theme){if(predicate(theme)){clearTimeout(timer);watcher.off('theme',listener);resolve(theme);}}watcher.on('theme',listener);});
}
test('semantic colors are exact, light/dark mode is correct, pets inherit the theme',async()=>{
  for(const name of ['tokyo-night','white','gruvbox']) {
    const source=await palette(name),theme=buildTheme(source);
    assert.equal(theme.colors.background,source.background);assert.equal(theme.colors.foreground,source.foreground);assert.equal(theme.colors.accent,source.accent);
    assert.equal(theme.colors.mossBody,source.green);assert.equal(theme.colors.emberBody,source.orange || source.yellow);assert.equal(theme.colors.orbitBody,source.magenta);
    assert.equal(theme.mode,name==='white'?'light':'dark');
    assert.ok(contrast(theme.colors.background,theme.colors.muted)>=4.5);
    for(const color of Object.values(theme.colors))assert.match(color,/^#[0-9a-f]{6}$/i);
  }
});
test('legacy aliases work, malformed values cannot inject CSS, invalid palettes are rejected',()=>{
  const palette=parsePalette(`bg = '#202020'\nfg = "#eeeeee"\ncolor4 = "#7788cc"\npurple = "#bb88ff"\naccent = "red; background:url(evil)"\nmode = 'light'`);
  const theme=buildTheme(palette);assert.equal(theme.mode,'light');assert.equal(theme.colors.accent,'#7788cc');assert.equal(theme.colors.magenta,'#bb88ff');
  assert.throws(()=>buildTheme(parsePalette('background = "broken"')),/valid background/);
});
test('inverted selection palettes keep selected text readable',()=>{
  const values={background:'#0c0b0c',foreground:'#fafcfb',selection_background:'#fafcfb',selection_foreground:'#0c0b0c'};
  const theme=buildTheme(values);
  assert.equal(theme.colors.selection,values.selection_background);
  assert.equal(theme.colors.selectionText,values.selection_foreground);
  for(const palette of [values,{...values,selection_foreground:'#fafcfb'},{background:'#ffffff',foreground:'#111111',selection:'#111111'}]) {
    const {colors}=buildTheme(palette);
    assert.ok(contrast(colors.selection,colors.selectionText)>=4.5);
  }
});
test('live palette edits, atomic theme replacement, invalid intermediate files and late creation',async()=>{
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'hermachy-theme-'));const dir=path.join(root,'current/theme');
  const watcher=new ThemeWatcher({directories:[dir],interval:25,resolver:async(_file,raw)=>parsePalette(raw)});
  try {
    await watcher.start();assert.equal(watcher.current.source,null);
    await fs.mkdir(dir,{recursive:true});
    const tokyo=await fs.readFile(path.join(__dirname,'fixtures/tokyo-night.toml'),'utf8');
    let changed=nextTheme(watcher,t=>t.colors.background==='#1a1b26');
    await fs.writeFile(path.join(dir,'colors.toml'),tokyo);await changed;
    const saved=watcher.current;
    await fs.writeFile(path.join(dir,'colors.toml'),'background = "unfinished"');await watcher.refresh();assert.equal(watcher.current,saved);
    const next=path.join(root,'current/next-theme');await fs.mkdir(next);
    const light=await fs.readFile(path.join(__dirname,'fixtures/white.toml'),'utf8');await fs.writeFile(path.join(next,'colors.toml'),light);
    changed=nextTheme(watcher,t=>t.mode==='light');
    await fs.rename(dir,dir+'.old');await watcher.refresh();assert.equal(watcher.current,saved);
    await fs.rename(next,dir);await changed;
    assert.equal(watcher.current.colors.background,parsePalette(light).background);
    changed=nextTheme(watcher,t=>t.colors.accent==='#aa2244');
    await fs.writeFile(path.join(dir,'colors.toml'),light.replace(/^accent\s*=.*$/m,'accent = "#aa2244"'));await changed;
    assert.ok(watcher.current.revision>=3);
  } finally {watcher.stop();await fs.rm(root,{recursive:true,force:true});}
});
