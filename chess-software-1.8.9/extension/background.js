// Chessalyzah 1.8.3: follow the active board, retain newest snapshots, acknowledge searches.
const WS_URL = 'ws://localhost:8085';
const MAX_EVENTS = 256;
let ws = null, ready = false, reconnectTimer = null, drainTimer = null;
let reconnectDelay = 1000, activeTabId = -1, lastFenTabId = -1;
let selection = 0, requestSequence = 0, hasEverSent = false;
let lastServerMessage = null, lastDiag = null, lastDiagAt = 0;
const states = new Map(), documents = new Map(), keys = new Map(), statuses = new Map();
const snapshots = new Map(), events = [];
const commands = new Set(['execute_move','set_privacy','panic','get_clocks','get_exploits',
  'get_board_geometry','get_promotion_geometry','click_promotion','queue_next']);
const relays = new Set(['clocks','site_exploits','board_geometry','promotion_geometry','ui_state','move_played','variant_detected','privacy_state']);
const ordered = new Set(['move_played','ui_state']);
function broadcast(value) { try { chrome.runtime.sendMessage(value).catch(() => {}); } catch (_) {} }
function forward(value) {
  const id = lastFenTabId >= 0 ? lastFenTabId : activeTabId;
  if (id < 0 || (lastFenTabId < 0 && value.action !== 'get_state')) return;
  try { chrome.tabs.sendMessage(id, value).catch(() => {}); } catch (_) {}
}
function socketState() {
  return ws?.readyState === WebSocket.OPEN && ready ? 'connected' :
    ws?.readyState === WebSocket.CONNECTING ? 'connecting' : 'disconnected';
}
function send(value) {
  if (!ready || ws?.readyState !== WebSocket.OPEN || ws.bufferedAmount > 65536) return false;
  try { ws.send(JSON.stringify(value)); return true; }
  catch (_) { ready = false; try { ws.close(); } catch (_) {} reconnect(); return false; }
}
function queueDrain() {
  if (drainTimer || !ready) return;
  drainTimer = setTimeout(() => { drainTimer = null; flush(); }, 50);
}
function flush() {
  while (events.length) {
    if (!send(events[0])) { queueDrain(); return; }
    events.shift();
  }
  for (const [type, value] of snapshots) {
    if (!send(value)) { queueDrain(); return; }
    snapshots.delete(type);
  }
}
function retain(value) {
  if (ordered.has(value.type)) {
    if (events.length >= MAX_EVENTS) {
      events.length = 0;
      const state = states.get(lastFenTabId);
      if (state) state.resync = true;
    }
    events.push(value);
  } else snapshots.set(value.type, value);
  flush();
}
function publish(state, resync = false) {
  const value = {...state, type:'analysis', resync,
    request_id: `${state.epoch}:${++requestSequence}`};
  states.set(lastFenTabId, value);
  statuses.set(lastFenTabId, {stage:'queued', request_id:value.request_id});
  retain(value);
  connect();
}
function resync() {
  const state = states.get(lastFenTabId);
  if (state) publish(state, true);
  forward({action:'get_state'});
}
function selectTab(id) {
  if (lastFenTabId === id) return false;
  lastFenTabId = id;
  selection++;
  events.length = 0; snapshots.clear();
  const state = states.get(id);
  if (state) state.epoch = `${id}:${documents.get(id)}:${selection}`;
  return true;
}
function activate(id) {
  activeTabId = id;
  if (states.has(id)) {
    if (selectTab(id)) resync();
  } else {
    selectTab(-1);
    forward({action:'get_state'});
  }
}
function reconnect() {
  if (reconnectTimer) return;
  broadcast({type:'wsStatus',state:'connecting'});
  reconnectTimer = setTimeout(() => { reconnectTimer = null; connect(); }, reconnectDelay);
  reconnectDelay = Math.min(15000, reconnectDelay * 2);
}
function connect() {
  if (ws && [WebSocket.OPEN, WebSocket.CONNECTING].includes(ws.readyState)) return;
  let socket;
  try { socket = new WebSocket(WS_URL); } catch (_) { reconnect(); return; }
  ws = socket;
  socket.onopen = () => {
    if (ws !== socket) return;
    ready = true; reconnectDelay = 1000;
    clearTimeout(reconnectTimer); reconnectTimer = null;
    broadcast({type:'wsStatus',state:'connected'});
    // Replace any unsent analysis with the current state before draining.
    snapshots.delete('analysis');
    resync(); flush();
  };
  socket.onclose = () => {
    if (ws !== socket) return;
    ready = false; broadcast({type:'wsStatus',state:'disconnected'}); reconnect();
  };
  socket.onerror = () => { if (ws === socket) { ready = false; socket.close(); reconnect(); } };
  socket.onmessage = event => {
    if (ws !== socket) return;
    try {
      const data = JSON.parse(event.data);
      if (!data || typeof data !== 'object') return;
      if (data.type === 'analysis_status') {
        const state = states.get(lastFenTabId);
        if (!state || data.request_id !== state.request_id || data.epoch !== state.epoch) return;
        statuses.set(lastFenTabId, data);
      }
      lastServerMessage = data;
      broadcast({type:'serverMessage',payload:data});
      if (commands.has(data.action)) forward(data);
    } catch (_) {}
  };
}
function cleanContext(input) {
  const v=input && typeof input==='object' ? input : {};
  const result={};
  for(const [key,max] of Object.entries({self_rating:4000,opponent_rating:4000,self_ms:86400000,opp_ms:86400000,initial_seconds:86400,increment_seconds:3600})) {
    result[key]=typeof v[key]==='number' && Number.isFinite(v[key]) && v[key]>=0 && v[key]<=max ? v[key] : null;
  }
  for(const key of ['site','game_id','speed','variant'])result[key]=typeof v[key]==='string'?v[key].slice(0,100):null;
  result.rated=typeof v.rated==='boolean'?v.rated:null;
  return result;
}
function validFen(fen) {
  if (typeof fen !== 'string' || fen.length > 200) return false;
  const p = fen.trim().split(/\s+/);
  if (p.length !== 6 || !/^[wb]$/.test(p[1]) || !/^(?:-|K?Q?k?q?)$/.test(p[2]) ||
      !/^(?:-|[a-h][36])$/.test(p[3]) || !/^\d+$/.test(p[4]) || !/^[1-9]\d*$/.test(p[5])) return false;
  const ranks=p[0].split('/');
  return ranks.length===8 && ranks.every(r=>/^[prnbqkPRNBQK1-8]+$/.test(r) && [...r].reduce((n,c)=>n+(/[1-8]/.test(c)?Number(c):1),0)===8);
}
function removeTab(id) {
  states.delete(id); documents.delete(id); keys.delete(id); statuses.delete(id);
  if (id === lastFenTabId) selectTab(-1);
}
chrome.runtime.onMessage.addListener((msg, sender, respond) => {
  const id = sender?.tab?.id ?? -1;
  if (msg?.type === 'getStatus') {
    const target = msg.tabId ?? id;
    const status = statuses.get(target);
    respond({ok:true,fen:states.get(target)?.fen || '',wsState:socketState(),showStatus:hasEverSent,
      inflight:['queued','received','started','refining'].includes(status?.stage),analysis:status,
      selected:target === lastFenTabId,lastServerMessage,diag:lastDiag,diagAt:lastDiagAt});
    return;
  }
  if (id < 0 || sender.frameId > 0) { respond?.({ok:false,reason:'not_board_tab'}); return; }
  if (msg?.type === 'contentStatus') {
    if (id === lastFenTabId || id === activeTabId) {
      lastDiag = msg; lastDiagAt = Date.now(); broadcast({type:'popupStatus',diag:msg});
    }
    respond?.({ok:true}); return;
  }
  if (msg?.type === 'FEN') {
    if (!validFen(msg.fen)) { respond?.({ok:false,reason:'bad_fen'}); return; }
    const document = `${sender.documentId || 'legacy'}:${msg.document_epoch || 'legacy'}`;
    if (documents.get(id) !== document) { removeTab(id); documents.set(id,document); }
    const fen=msg.fen.trim().replace(/\s+/g,' '), player=['w','b'].includes(msg.playing_as)?msg.playing_as:null;
    const key=JSON.stringify([fen,!!msg.flipped,player]);
    const previous=states.get(id);
    const state={fen,flipped:msg.flipped?1:0,playing_as:player,game_context:cleanContext(msg.game_context),
      epoch:previous?.epoch || `${id}:${document}:${selection}`};
    states.set(id,state);
    // sender.tab.active covers initial FEN arriving before tabs.query resolves.
    if (activeTabId < 0 && sender.tab.active) activeTabId=id;
    const changed = id === activeTabId ? selectTab(id) : false;
    const duplicate = keys.get(id) === key; keys.set(id,key);
    if (id === lastFenTabId && (!duplicate || msg.force || changed)) publish(states.get(id), !!msg.force || changed);
    else if (previous && !changed) states.set(id,{...state,request_id:previous.request_id});
    hasEverSent=true;broadcast({type:'fenUpdate',tabId:id,fen});
    if (states.size>64) {
      const old=[...states.keys()].find(k=>k!==lastFenTabId && k!==activeTabId);
      if(old!==undefined)removeTab(old);
    }
    respond?.({ok:true,dedup:duplicate});return;
  }
  if(msg?.type==='game_context') {
    const state=states.get(id);
    const document=`${sender.documentId || 'legacy'}:${msg.document_epoch || 'legacy'}`;
    if(id!==lastFenTabId || documents.get(id)!==document || !state || msg.fen!==state.fen) {
      respond?.({ok:false,reason:'stale_context'});return;
    }
    state.game_context=cleanContext(msg.context);
    retain({type:'game_context',context:state.game_context,fen:state.fen,
      epoch:state.epoch,request_id:state.request_id});
    respond?.({ok:true});return;
  }
  if (relays.has(msg?.type)) {
    if (id!==lastFenTabId) { respond?.({ok:false,reason:'other_board'});return; }
    const payload={...msg};delete payload.tabId;
    retain(payload);connect();respond?.({ok:true});
  }
});
chrome.tabs.onRemoved.addListener(removeTab);
chrome.tabs.onActivated.addListener(info=>activate(info.tabId));
chrome.tabs.onUpdated.addListener((id,change)=>{
  if(change.status==='loading') { removeTab(id); if(id===activeTabId)forward({action:'get_state'}); }
});
chrome.windows?.onFocusChanged?.addListener(()=>chrome.tabs.query({active:true,currentWindow:true},tabs=>{if(tabs[0])activate(tabs[0].id);}));
chrome.runtime.onInstalled.addListener(connect);
chrome.runtime.onStartup.addListener(connect);
chrome.tabs.query({active:true,currentWindow:true},tabs=>{if(activeTabId < 0 && tabs[0])activate(tabs[0].id);});
// Keep a quiet bridge observable and recover a suspended/restarted worker.
setInterval(()=>{if(ready){send({type:'bridge_ping'});flush();}else connect();},20000);
connect();
