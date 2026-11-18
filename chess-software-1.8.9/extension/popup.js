// Popup status reflects acknowledged server stages, never a guessed completion timer.
let tabId = -1;
const fenEl = document.getElementById('fen');
const statusEl = document.getElementById('status');
const stateEl = document.getElementById('state');
const dotEl = document.getElementById('dot');
const ageEl = document.getElementById('age');
let lastFen = '', changedAt = 0, refreshing = false;
async function refresh() {
  if (refreshing || tabId < 0) return;
  refreshing = true;
  try {
    const s = await chrome.runtime.sendMessage({type:'getStatus',tabId});
    if (!s?.ok) return;
    const fen=s.fen || '';
    if(fen!==lastFen){lastFen=fen;changedAt=Date.now();}
    fenEl.textContent=fen || 'Waiting for a board position…';
    statusEl.style.display='block';
    dotEl.style.background=s.wsState==='connected'?'#34d399':'#f59e0b';
    const a=s.analysis;
    stateEl.textContent=s.wsState==='connected'
      ? (!s.selected?'Another board is selected':a ? `${a.stage === 'refining' ? 'Finding accuracy alternatives' : a.stage === 'incomplete' ? 'Stopped before full target-depth result' : a.stage} · target ${a.target_depth ?? '—'}${a.search_depth != null ? ' · search '+a.search_depth : ''}`:'Connected · waiting for analysis')
      : 'Python bridge: '+s.wsState;
    ageEl.textContent=changedAt ? Math.floor((Date.now()-changedAt)/1000)+'s ago' : '';
  } catch (_) {stateEl.textContent='Extension reconnecting…';}
  finally {refreshing=false;}
}
chrome.tabs.query({active:true,currentWindow:true},tabs=>{tabId=tabs[0]?.id ?? -1;refresh();});
const timer=setInterval(refresh,500);
window.addEventListener('unload',()=>clearInterval(timer));
