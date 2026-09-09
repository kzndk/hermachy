const fs = require('node:fs/promises');
const { watch } = require('node:fs');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const { EventEmitter } = require('node:events');
const path = require('node:path');
const os = require('node:os');
const execute = promisify(execFile);
const HEX = /^#[0-9a-f]{6}$/i;
const COLOR_KEYS = ['background','dark_background','darker_background','lighter_background','foreground','dark_foreground','light_foreground','bright_foreground','accent','selection','muted','red','green','yellow','orange','blue','magenta','cyan'];
const ALIASES = {background:['bg','color0'],foreground:['fg','color7'],dark_background:['dark_bg'],darker_background:['darker_bg'],lighter_background:['lighter_bg'],dark_foreground:['dark_fg','color8'],light_foreground:['light_fg'],bright_foreground:['bright_fg','color15'],red:['color1'],green:['color2'],yellow:['color3'],blue:['color4'],magenta:['purple','color5'],cyan:['color6']};
function mix(a,b,amount) {
  const parts = [1,3,5].map(i => Math.round(parseInt(a.slice(i,i+2),16)*(1-amount)+parseInt(b.slice(i,i+2),16)*amount).toString(16).padStart(2,'0'));
  return '#'+parts.join('');
}
function luminance(hex) {
  const rgb=[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);
  return rgb[0]*.2126+rgb[1]*.7152+rgb[2]*.0722;
}
function contrast(a,b) {const [hi,lo]=[luminance(a),luminance(b)].sort((a,b)=>b-a);return(hi+.05)/(lo+.05);}
function readableMuted(bg,fg) {
  for(let ratio=.62;ratio<1;ratio+=.04){const color=mix(bg,fg,ratio);if(contrast(bg,color)>=4.5)return color;}
  return fg;
}
function parsePalette(text) {
  const values={};
  for(const line of text.split(/\r?\n/)) {
    const match=line.match(/^\s*["']?([a-zA-Z0-9_-]+)["']?\s*=\s*(["'])([^"']+)\2\s*(?:#.*)?$/);
    if(match && (HEX.test(match[3]) || ['mode','theme_type'].includes(match[1]) && ['light','dark'].includes(match[3]))) values[match[1]]=match[3].toLowerCase();
  }
  for(const [key,aliases] of Object.entries(ALIASES)) if(!values[key]) values[key]=aliases.map(alias=>values[alias]).find(Boolean);
  return values;
}
function buildTheme(values,{name='Omarchy',lightMode=false,source=null}={}) {
  const bg=values.background,fg=values.foreground;
  if(!HEX.test(bg || '') || !HEX.test(fg || '')) throw new Error('Theme needs a valid background and foreground');
  const pick=(key,fallback)=>HEX.test(values[key] || '')?values[key]:fallback;
  const mode=['light','dark'].includes(values.mode)?values.mode:['light','dark'].includes(values.theme_type)?values.theme_type:lightMode?'light':luminance(bg)>.45?'light':'dark';
  const accent=pick('accent',pick('blue',fg));
  const colors={background:bg,foreground:fg,accent,
    panel:pick('dark_background',mix(bg,fg,.025)),surface:pick('lighter_background',mix(bg,fg,.055)),
    inset:pick('darker_background',mix(bg,fg,.035)),border:pick('muted',mix(bg,fg,.18)),
    strong:pick('bright_foreground',pick('light_foreground',fg)),muted:readableMuted(bg,fg),
    selection:pick('selection',mix(bg,accent,.14)),accentText:contrast(accent,bg)>=contrast(accent,fg)?bg:fg,
    red:pick('red',accent),green:pick('green',accent),yellow:pick('yellow',accent),orange:pick('orange',pick('yellow',accent)),
    blue:pick('blue',accent),magenta:pick('magenta',accent),cyan:pick('cyan',accent),
  };
  colors.selection = pick('selection', pick('selection_background', colors.selection));
  const selectionText = pick('selection_foreground',
    contrast(colors.selection,bg)>=contrast(colors.selection,fg)?bg:fg);
  colors.selectionText = contrast(colors.selection,selectionText)>=4.5 ? selectionText :
    contrast(colors.selection,'#000000')>=contrast(colors.selection,'#ffffff')?'#000000':'#ffffff';
  // Pets use semantic theme accents and palette-derived shading too.
  for(const [id,color] of Object.entries({moss:colors.green,ember:colors.orange,orbit:colors.magenta})) {
    colors[`${id}Body`]=color;
    colors[`${id}Shade`]=mix(color,bg,.28);
    colors[`${id}Light`]=mix(color,fg,.4);
    colors[`${id}Ink`]=contrast(color,bg)>=contrast(color,fg)?bg:fg;
  }
  return {name,mode,source,colors};
}
const fallbackTheme=()=>buildTheme({background:'#181818',foreground:'#e5e5e5'},{name:'System theme unavailable'});
function themeDirectories(home=os.homedir()) {
  if(process.env.HERMACHY_DESKTOP_THEME_DIR)return[path.resolve(process.env.HERMACHY_DESKTOP_THEME_DIR)];
  return [...new Set([
    path.join(process.env.XDG_STATE_HOME || path.join(home,'.local/state'),'omarchy/current/theme'),
    path.join(home,'.local/state/omarchy/current/theme'),
    path.join(process.env.XDG_CONFIG_HOME || path.join(home,'.config'),'omarchy/current/theme'),
  ])];
}
async function resolvePalette(file,raw) {
  try {
    const {stdout}=await execute('omarchy-theme-color',['--file',file,'--all'],{timeout:5000,maxBuffer:65536});
    const resolved={};
    for(const line of stdout.split('\n')) {
      const [key,value]=line.split('\t');
      if(COLOR_KEYS.includes(key) && HEX.test(value || '') || key==='mode' && ['light','dark'].includes(value)) resolved[key]=value.toLowerCase();
    }
    return {...parsePalette(raw),...resolved};
  } catch {return parsePalette(raw);}
}
class ThemeWatcher extends EventEmitter {
  constructor({directories=themeDirectories(),interval=1000,resolver=resolvePalette}={}) {
    super();this.directories=directories;this.interval=interval;this.resolver=resolver;
    this.current={...fallbackTheme(),revision:0};this.fingerprint=null;this.watchers=[];this.closed=false;
  }
  async refresh() {
    if(this.closed)return this.current;
    if(this.refreshing)return this.refreshing;
    this.refreshing=this.read().finally(()=>{this.refreshing=null;});
    return this.refreshing;
  }
  async read() {
    for(const dir of this.directories) {
      try {
        const file=path.join(dir,'colors.toml');
        const stat=await fs.stat(file);if(!stat.isFile()||stat.size>65536)continue;
        const raw=await fs.readFile(file,'utf8');
        const name=await fs.readFile(path.join(path.dirname(dir),'theme.name'),'utf8').then(v=>v.trim()).catch(()=>path.basename(dir)==='theme'?'Omarchy':path.basename(dir));
        const lightMode=await fs.access(path.join(dir,'light.mode')).then(()=>true).catch(()=>false);
        const fingerprint=JSON.stringify([file,raw,name,lightMode]);
        if(fingerprint===this.fingerprint)return this.current;
        // A theme switch removes/replaces the directory. Keep the last valid
        // palette throughout that gap, and reject incomplete intermediate reads.
        buildTheme(parsePalette(raw));
        const values=await this.resolver(file,raw);
        if(await fs.readFile(file,'utf8')!==raw)return this.current;
        const theme=buildTheme(values,{name:name.replace(/(^|[- ])([a-z])/g,(_,s,c)=>(s?' ':'')+c.toUpperCase()),lightMode,source:file});
        if(this.closed)return this.current;
        this.current={...theme,revision:this.current.revision+1};this.fingerprint=fingerprint;
        this.emit('theme',this.current);return this.current;
      } catch {/* Missing, transient, or invalid palettes cannot replace a valid one. */}
    }
    return this.current;
  }
  async start() {
    await this.refresh();
    for(const dir of this.directories) {
      // Watch the parent so directory swaps are observed, plus an eventual-read
      // timer for in-place edits, missing directories, symlink swaps, and sleep.
      try {const watcher=watch(path.dirname(dir),()=>{clearTimeout(this.debounce);this.debounce=setTimeout(()=>this.refresh(),60);});watcher.on('error',()=>{});this.watchers.push(watcher);} catch {}
    }
    this.timer=setInterval(()=>this.refresh(),this.interval);this.timer.unref();return this.current;
  }
  stop(){this.closed=true;clearInterval(this.timer);clearTimeout(this.debounce);for(const watcher of this.watchers)watcher.close();}
}
module.exports={ThemeWatcher,buildTheme,parsePalette,themeDirectories,resolvePalette,contrast};
