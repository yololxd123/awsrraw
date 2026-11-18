// Optional page metadata. Missing values stay null; never guess player identity.
(() => {
  const text = el => (el?.textContent || '').trim();
  const rating = el => {
    const raw = el?.getAttribute?.('data-rating') || text(el);
    const match = /^\(?\s*(\d{2,4})\s*\??\s*\)?$/.exec(raw);
    const value = match ? Number(match[1]) : null;
    return value != null && value <= 4000 ? value : null;
  };
  function read({playingAs=null, flipped=false, clocks=null, variant=null}={}) {
    const empty = {site:location.hostname,game_id:null,self_rating:null,opponent_rating:null,
      self_ms:null,opp_ms:null,initial_seconds:null,increment_seconds:null,speed:null,rated:null,variant};
    const match = location.pathname.match(/\/(?:game\/(?:live|daily)\/|live\/game\/)(\d+)/) ||
      (/lichess\.org$/.test(location.hostname) ? location.pathname.match(/^\/([A-Za-z0-9]{8})(?:[A-Za-z0-9]{4})?(?:\/(?:white|black))?\/?$/) : null);
    if(match && !['analysis','training','practice','insights','features'].includes(match[1])) empty.game_id=match[1];
    const bottom = document.querySelector('.player-bottom, .player-component-bottom, .player-row-bottom, .ruser-bottom');
    const top = document.querySelector('.player-top, .player-component-top, .player-row-top, .ruser-top');
    if(['w','b'].includes(playingAs)) {
      const selfBottom = playingAs === (flipped ? 'b' : 'w');
      const findRating = container => rating(container?.querySelector('[data-rating], .user-rating, .rating, .rating-number, .user-tagline-rating'));
      empty.self_rating=findRating(selfBottom?bottom:top);
      empty.opponent_rating=findRating(selfBottom?top:bottom);
      empty.self_ms=Number.isFinite(clocks?.self)?clocks.self:null;
      empty.opp_ms=Number.isFinite(clocks?.opponent)?clocks.opponent:null;
    }
    const timeEl=document.querySelector('[data-time-control], .game-time-control, .time-control, .setup .clock');
    const value=timeEl?.getAttribute('data-time-control') || text(timeEl);
    const control=/^\s*(\d+(?:\.\d+)?)\s*\+\s*(\d+)\s*$/.exec(value);
    if(control) {
      // Explicit data attribute uses seconds; displayed controls use minutes.
      empty.initial_seconds=Number(control[1])*(timeEl?.hasAttribute('data-time-control')?1:60);
      empty.increment_seconds=Number(control[2]);
      const total=empty.initial_seconds+40*empty.increment_seconds;
      empty.speed=total<180?'bullet':total<480?'blitz':total<1500?'rapid':'classical';
    }
    const rated=document.querySelector('[data-rated]');
    const isRated=rated?.getAttribute('data-rated');
    if(isRated==='true'||isRated==='false')empty.rated=isRated==='true';
    return empty;
  }
  globalThis.ChessGameContext={read};
})();
