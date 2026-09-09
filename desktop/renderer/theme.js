let revision=-1;
const names={background:'bg',foreground:'text',strong:'text-strong',accentText:'accent-text'};
export function applyTheme(theme) {
  if(!theme?.colors)return;
  if(Number.isFinite(theme.revision) && theme.revision<revision)return;
  revision=theme.revision ?? revision;
  for(const [key,value] of Object.entries(theme.colors)) {
    if(!/^#[0-9a-f]{6}$/i.test(value))continue;
    const name=names[key] || key.replace(/[A-Z]/g,c=>'-'+c.toLowerCase());
    document.documentElement.style.setProperty('--'+name,value);
  }
  document.documentElement.style.colorScheme=theme.mode==='light'?'light':'dark';
  document.documentElement.dataset.theme=theme.name;
  for(const id of ['theme-name','settings-theme-name']) document.getElementById(id).textContent=theme.name;
  document.body.classList.add('theme-ready');
}
