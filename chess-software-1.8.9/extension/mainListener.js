// Isolated content bridge. The MAIN-world provider supplies native FENs via
// same-window messages; page-supplied state is validated, not authenticated.
(function () {
  const documentEpoch = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
  let stopped = false;
  let boardCache = null, boardCacheUntil = 0, shadowRefreshAt = 0;
  let rootCache = new WeakMap();
  const invalidateBoard = () => { boardCacheUntil = 0; rootCache = new WeakMap(); shadowRefreshAt = 0; };


  const getSiteProfile = () => {
    const host = window.location.hostname;

    if (host.includes('chess.com')) {
      return {
        board: '#board-layout-chessboard .board, wc-chess-board, chess-board, .board',
        pieces: '.piece:not(.dragging):not(.ghost):not(.premove):not(.fading):not(.hidden)',
        isFlipped: () => {
          const primary = document.querySelector('.board.flipped, wc-chess-board.flipped, chess-board.flipped, #board-layout-chessboard.flipped');
          if (primary) return true;
          const boardEl = findBoardElement();
          if (!boardEl) return false;
          try {
            return allElementsPiercing(boardEl).some(el => el.classList && el.classList.contains('flipped'));
          } catch (_) {
            return false;
          }
        },
        getPieceInfo: (el, boardRect) => {
          const styleNorm = (el.getAttribute('style') || '').replace(/\s+/g, '');
          if (styleNorm.includes('opacity:0') || styleNorm.includes('display:none')) return null;
          const classes = Array.from(el.classList);
          const pieceCls = classes.find(c => c.length === 2 && /^[bw][prnbqk]$/.test(c));
          if (!pieceCls) return null;
          const fenChar = pieceCls[0] === 'w' ? pieceCls[1].toUpperCase() : pieceCls[1].toLowerCase();

          // 1) square-XX on the piece itself, then on a wrapper element
          //    (some boards nest the piece inside a <div class="square-XX">).
          let sqCls = classes.find(c => c.startsWith('square-'));
          if (!sqCls) {
            const parent = el.closest('[class*="square-"]');
            if (parent) sqCls = Array.from(parent.classList).find(c => c.startsWith('square-'));
          }
          if (sqCls) {
            const parts = sqCls.split('-');
            if (parts[1] && parts[1].length >= 2) {
              const x = parseInt(parts[1][0], 10) - 1;
              const y = parseInt(parts[1][1], 10) - 1;
              if (x >= 0 && x < 8 && y >= 0 && y < 8) return { fenChar, x, y };
            }
          }

          // 2) CSS transform (plain matrix OR matrix3d).
          const flipped = profile.isFlipped();
          const fromTransform = coordsFromTransform(window.getComputedStyle(el).transform, boardRect, flipped);
          if (fromTransform) return { fenChar, x: fromTransform.x, y: fromTransform.y };

          // 3) Layout fallback: the piece's own bounding box (parent square
          //    wrappers position the piece; no square-XX / transform on it).
          const fromRect = coordsFromRect(el, boardRect, flipped);
          if (fromRect) return { fenChar, x: fromRect.x, y: fromRect.y };

          return null;
        }
      };
    }

    if (host.includes('lichess.org')) {
      return {
        board: 'cg-board',
        pieces: 'piece:not(.dragging):not(.ghost):not(.fading)',
        isFlipped: () => {
          const filesElem = document.querySelector('coords.files');
          return filesElem?.classList?.contains('black') ||
            document.querySelector('.cg-wrap.orientation-black') !== null;
        },
        getPieceInfo: (el, boardRect) => {
          const isWhite = el.classList.contains('white');
          let type = '';
          if (el.classList.contains('pawn')) type = 'p';
          else if (el.classList.contains('knight')) type = 'n';
          else if (el.classList.contains('bishop')) type = 'b';
          else if (el.classList.contains('rook')) type = 'r';
          else if (el.classList.contains('queen')) type = 'q';
          else if (el.classList.contains('king')) type = 'k';
          if (!type) return null;

          const fenChar = isWhite ? type.toUpperCase() : type.toLowerCase();

          if (el.cgKey) {
            const x = el.cgKey.charCodeAt(0) - 97;
            const y = parseInt(el.cgKey[1], 10) - 1;
            return { fenChar, x, y };
          }

          const isFlipped =
            document.querySelector('coords.files')?.classList.contains('black') ||
            document.querySelector('.cg-wrap.orientation-black') !== null;
          const fromTransform = coordsFromTransform(window.getComputedStyle(el).transform, boardRect, isFlipped);
          if (fromTransform) return { fenChar, x: fromTransform.x, y: fromTransform.y };
          const fromRect = coordsFromRect(el, boardRect, isFlipped);
          if (fromRect) return { fenChar, x: fromRect.x, y: fromRect.y };
          return null;
        }
      };
    }

    if (host.includes('worldchess.com')) {
      return {
        board: 'cg-board',
        pieces: 'cg-piece:not(.dragging):not(.ghost):not(.fading)',
        isFlipped: () => document.querySelector('cg-titles.rotated') !== null,
        getPieceInfo: (el, boardRect) => {
          const classes = Array.from(el.classList);
          const pieceCls = classes.find(c => c.length === 2 && /^[bw][prnbqk]$/.test(c));
          if (!pieceCls) return null;
          const fenChar = pieceCls[0] === 'w' ? pieceCls[1].toUpperCase() : pieceCls[1].toLowerCase();

          const flipped = document.querySelector('cg-titles.rotated') !== null;
          const fromTransform = coordsFromTransform(window.getComputedStyle(el).transform, boardRect, flipped);
          if (fromTransform) return { fenChar, x: fromTransform.x, y: fromTransform.y };
          const fromRect = coordsFromRect(el, boardRect, flipped);
          if (fromRect) return { fenChar, x: fromRect.x, y: fromRect.y };
          return null;
        }
      };
    }

    return null;
  };

  const profile = getSiteProfile();
  if (!profile) return;

  // ---------------------------------------------------------------------
  // Main-world FEN provider.  chess.com's WebGL board has no DOM pieces, so
  // the only position source is chess.com's own Game object on the
  // wc-chess-board element — visible only from the page's MAIN world.  Inject
  // fenProvider.js as a classic <script> (the mechanism BetterMint uses) so it
  // runs in the page's JS realm regardless of `world: "MAIN"` support.  The
  // provider guards against double execution itself, so this coexists with the
  // manifest-declared MAIN-world entry without running twice.
  // ---------------------------------------------------------------------
  try {
    const s = document.createElement('script');
    s.src = chrome.runtime.getURL('fenProvider.js');
    s.onload = () => { try { s.remove(); } catch (_) {} };
    (document.head || document.documentElement).appendChild(s);
  } catch (_) {}

  // Locate the main board element.  Analysis and puzzle pages can drop the
  // live-game container ids, so fall back through the common variants and
  // always take the first one with a real size.
  const findBoardElement = () => {
    if (boardCache?.isConnected && Date.now() < boardCacheUntil) return boardCache;

    const selectors = [
      profile.board,
      '#board-layout-chessboard .board',
      'wc-chess-board',
      'chess-board',
      '#board-single',
      '#board-single .board',
      '.board',
      '#board',
      '.board-layout-board',
      '[class*="board-wrap"] [class*="board"]'
    ];
    const hasSize = (el) => {
      try {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      } catch (_) {
        return false;
      }
    };
    const candidates = [];
    const seenCandidates = new Set();
    for (const sel of selectors) {
      try {
        for (const el of document.querySelectorAll(sel)) {
          if (!seenCandidates.has(el) && hasSize(el)) { seenCandidates.add(el); candidates.push(el); }
        }
      } catch (_) {}
    }
    const remember = el => { boardCache = el; boardCacheUntil = Date.now() + 1500; return el; };
    // A native provider identifies the main chess.com board even without DOM pieces.
    if (apiFresh()) {
      const native = candidates.filter(el => /^(WC-CHESS-BOARD|CHESS-BOARD)$/.test(el.tagName))
        .sort((a,b)=> { const x=a.getBoundingClientRect(),y=b.getBoundingClientRect(); return y.width*y.height-x.width*x.height; });
      if (native[0]) return remember(native[0]);
    }
    // Prefer the candidate that actually CONTAINS pieces: on game pages a
    // static thumbnail/preview board can sit earlier in the DOM than the live
    // board and would otherwise win the size check and freeze the FEN forever.
    for (const el of candidates) {
      if (findPiecesPiercing(el, profile.pieces).length > 0) return remember(el);
    }
    for (const el of candidates) {
      if (findPiecesPiercing(el,
        '[class*="piece"]:not(.dragging):not(.ghost):not(.premove):not(.fading):not(.hidden)').length > 0) return remember(el);
    }
    for (const el of candidates) {
      if (findPiecesPiercing(el,
        '[class*="wp"],[class*="wq"],[class*="wk"],[class*="wn"],[class*="wb"],[class*="wr"],' +
        '[class*="bp"],[class*="bq"],[class*="bk"],[class*="bn"],[class*="bb"],[class*="br"]').length > 0) return remember(el);
    }
    return remember(candidates[0] || null);
  };

  // Square coordinates from a CSS transform string.  Handles BOTH the plain
  // "matrix(a b c d tx ty)" that live boards produce and the "matrix3d(...)"
  // that analysis/puzzle boards produce when they animate with translate3d.
  const coordsFromTransform = (transform, boardRect, flipped) => {
    try {
      if (!transform || transform === 'none') return null;
      const m3 = transform.match(/matrix3d\((.+)\)/);
      const m2 = m3 ? null : transform.match(/matrix\((.+)\)/);
      const matrix = m3 || m2;
      if (!matrix) return null;
      const values = matrix[1].split(',').map(v => parseFloat(v.trim()));
      const tx = m3 ? values[12] : values[4];
      const ty = m3 ? values[13] : values[5];
      if (!isFinite(tx) || !isFinite(ty)) return null;
      const sqWidth = boardRect.width / 8;
      const sqHeight = boardRect.height / 8;
      let x = Math.round(tx / sqWidth);
      let y = 7 - Math.round(ty / sqHeight);
      if (flipped) {
        x = 7 - x;
        y = 7 - y;
      }
      if (x >= 0 && x < 8 && y >= 0 && y < 8) return { x, y };
    } catch (_) {}
    return null;
  };

  // Square coordinates from the piece's own bounding box.  Works for boards
  // that position pieces through parent square wrappers, where the piece
  // element carries neither a square-XX class nor a transform.
  const coordsFromRect = (el, boardRect, flipped) => {
    try {
      const pr = el.getBoundingClientRect();
      if (!(pr.width > 0 && pr.height > 0)) return null;
      const sqW = boardRect.width / 8;
      const sqH = boardRect.height / 8;
      const px = pr.left + pr.width / 2 - boardRect.left;
      const py = pr.top + pr.height / 2 - boardRect.top;
      if (px < -sqW || py < -sqH || px > boardRect.width + sqW || py > boardRect.height + sqH) return null;
      let x = Math.floor(px / sqW);
      let y = 7 - Math.floor(py / sqH);
      if (flipped) {
        x = 7 - x;
        y = 7 - y;
      }
      if (x >= 0 && x < 8 && y >= 0 && y < 8) return { x, y };
    } catch (_) {}
    return null;
  };

  // Chess.com embeds the game/puzzle state in a __NEXT_DATA__ JSON blob whose
  // "fen" field is authoritative for puzzles (where no move list is visible).
  let _embeddedFenCache = null;
  let _embeddedFenAt = 0;
  const readEmbeddedFen = () => {
    try {
      const now = Date.now();
      if (_embeddedFenCache && now - _embeddedFenAt < 1000) return _embeddedFenCache;
      const el = document.querySelector('script#__NEXT_DATA__');
      if (!el || !el.textContent) return null;
      const data = JSON.parse(el.textContent);
      let found = null;
      const search = (obj, depth) => {
        if (found || !obj || depth > 8) return;
        if (typeof obj === 'object' && !Array.isArray(obj)) {
          for (const k of Object.keys(obj)) {
            if (k.toLowerCase() === 'fen' && typeof obj[k] === 'string') {
              const parts = obj[k].trim().split(/\s+/);
              if (parts.length >= 4 && parts[0].split('/').length === 8 && /^[wb]$/.test(parts[1])) {
                found = parts;
                return;
              }
            }
            search(obj[k], depth + 1);
          }
        } else if (Array.isArray(obj)) {
          for (const v of obj) search(v, depth + 1);
        }
      };
      search(data, 0);
      _embeddedFenCache = found;
      _embeddedFenAt = now;
      return found;
    } catch (_) {
      return null;
    }
  };

  // Authoritative side-to-move when there is NO running clock (analysis,
  // after-game analysis, puzzles).  The move list reflects the exact current
  // position: the last move's colour tells who just played, and the ply count
  // parity confirms it.  Falls back to the embedded FEN (chess.com puzzles).
  let _analysisTurnCache = null;
  let _analysisTurnAt = 0;

  // Number of half-moves shown in the current move list (null when there is
  // no readable move list).  Used to detect backward scrubbing in analysis.
  const getMoveListPlyCount = () => {
    try {
      const host = window.location.hostname;
      if (host.includes('lichess.org')) {
        const plies = [...document.querySelectorAll('.tview2 .node, [data-ply]')]
          .map(n => parseInt(n.getAttribute('data-ply'), 10))
          .filter(p => Number.isInteger(p) && p > 0);
        return plies.length > 0 ? Math.max(...plies) : null;
      }
      const moves = document.querySelectorAll(
        '[class*="move-list"] .move, .vertical-move-list .move, .flex-move-list .move');
      return moves.length > 0 ? moves.length : null;
    } catch (_) {
      return null;
    }
  };

  const getPuzzleTurnFromDom = () => {
    try {
      const text = (document.body ? document.body.innerText : '') || '';
      if (/\bwhite to (move|play)\b/i.test(text) || /\bfind the best move for white\b/i.test(text)) return 'w';
      if (/\bblack to (move|play)\b/i.test(text) || /\bfind the best move for black\b/i.test(text)) return 'b';
      if (/\byour turn\b/i.test(text)) {
        return profile.isFlipped() ? 'b' : 'w';
      }
      const boardEl = findBoardElement();
      if (boardEl) {
        const highlights = boardEl.querySelectorAll('.highlight, square.last-move, .last-move');
        if (highlights.length >= 2) {
          // Opponent has already played their move! Human's turn:
          return profile.isFlipped() ? 'b' : 'w';
        }
      }
    } catch (_) {}
    return null;
  };

  const getAnalysisTurn = () => {
    try {
      const now = Date.now();
      if (_analysisTurnCache && now - _analysisTurnAt < 350) return _analysisTurnCache;
      const host = window.location.hostname;
      let result = null;

      if (/puzzles|training/i.test(window.location.pathname) ||
          document.querySelector('.puzzle-view, .puzzle-header, .puzzle__side, [data-test-element="puzzle-status"]')) {
        const pz = getPuzzleTurnFromDom();
        if (pz) result = pz;
      }

      if (!result) {
        const moves = document.querySelectorAll(
          '[class*="move-list"] .move, .vertical-move-list .move, .flex-move-list .move');
        if (moves.length > 0) {
          const last = moves[moves.length - 1];
          const cls = ' ' + (last.className || '') + ' ';
          if (/\swhite-move\s/.test(cls) && !/\sblack-move\s/.test(cls)) {
            result = 'b'; // White just played -> Black to move
          } else if (/\sblack-move\s/.test(cls) && !/\swhite-move\s/.test(cls)) {
            result = 'w';
          } else {
            const ply = getMoveListPlyCount();
            result = (ply != null) ? ((ply % 2 === 0) ? 'w' : 'b') : null;
          }
        }
      }

      if (!result && host.includes('lichess.org')) {
        const ply = getMoveListPlyCount();
        if (ply != null) result = (ply % 2 === 0) ? 'w' : 'b';
      }

      if (!result && host.includes('chess.com')) {
        const emb = readEmbeddedFen();
        if (emb && emb[1]) result = emb[1];
      }

      _analysisTurnCache = result;
      _analysisTurnAt = now;
      return result;
    } catch (_) {
      return null;
    }
  };

  // Authoritative side-to-move from the site's RUNNING CLOCK.  Board-diff
  // derivation breaks under premoves: a premove places a piece early, and when
  // it executes the placement often does not change, so the diff sees no move
  // and the turn drifts.  The running clock always reflects the true turn.
  let _lastRealTurnResult = null;
  let _lastRealTurnTime = 0;
  const getRealTurn = () => {
    const now = Date.now();
    if (_lastRealTurnResult !== null && (now - _lastRealTurnTime < 100)) {
      return _lastRealTurnResult;
    }
    let detectedTurn = null;
    try {
      const host = window.location.hostname;
      if (host.includes('lichess.org')) {
        // Current lichess renders the clock as div.rclock.running with
        // rclock-white/rclock-black and rclock-top/rclock-bottom classes.
        // Older layouts used .clock.running + clock-white/clock-black.
        const running = document.querySelector('.rclock.running, .clock.running');
        if (!running) {
          _lastRealTurnResult = null;
          _lastRealTurnTime = now;
          return null;
        }
        let node = running;
        for (let i = 0; i < 3 && node; i++, node = node.parentElement) {
          const cls = node.className || '';
          if (cls.includes('rclock-white') || cls.includes('clock-white')) {
            detectedTurn = 'w';
            break;
          }
          if (cls.includes('rclock-black') || cls.includes('clock-black')) {
            detectedTurn = 'b';
            break;
          }
        }
        if (!detectedTurn) {
          // Position classes: the bottom clock is White's unless flipped.
          let node2 = running;
          for (let i = 0; i < 3 && node2; i++, node2 = node2.parentElement) {
            const cls = node2.className || '';
            if (cls.includes('rclock-bottom') || cls.includes('clock-bottom')) {
              detectedTurn = profile.isFlipped() ? 'b' : 'w';
              break;
            }
            if (cls.includes('rclock-top') || cls.includes('clock-top')) {
              detectedTurn = profile.isFlipped() ? 'w' : 'b';
              break;
            }
          }
        }
        if (!detectedTurn) {
          // Geometry fallback: above/below the board centre.
          const rect = running.getBoundingClientRect();
          const boardEl = findBoardElement();
          if (boardEl && rect.width > 0) {
            const bRect = boardEl.getBoundingClientRect();
            const isBottom = rect.top > bRect.top + bRect.height / 2;
            detectedTurn = profile.isFlipped() ? (isBottom ? 'b' : 'w') : (isBottom ? 'w' : 'b');
          }
        }
      }
      if (!detectedTurn && host.includes('chess.com')) {
        // Chess.com has shipped several clock layouts over the years: a
        // wc-clock web component with clock-white/clock-black classes, and
        // older .clock-component containers that mark the active side with
        // clock-player-turn / -active / -running.
        const activeSel = [
          'wc-clock.clock-running',
          '.clock-component.clock-player-turn',
          '.clock-running',
          '.clock-player-turn',
          '.clock-active',
          '.timer-active',
          '.clock.running',
          '.clock-component.is-active'
        ];
        for (const sel of activeSel) {
          const el = document.querySelector(sel);
          if (!el) continue;
          // Walk up a few ancestors looking for colour classes.
          let node = el;
          for (let i = 0; i < 4 && node; i++, node = node.parentElement) {
            const cls = node.className || '';
            if (cls.includes('clock-white')) { detectedTurn = 'w'; break; }
            if (cls.includes('clock-black')) { detectedTurn = 'b'; break; }
          }
          if (detectedTurn) break;
          // Position classes: the bottom clock is White's unless flipped.
          let node2 = el;
          for (let i = 0; i < 4 && node2; i++, node2 = node2.parentElement) {
            const cls = node2.className || '';
            if (cls.includes('clock-player-bottom') || cls.includes('clock-bottom')) {
              detectedTurn = profile.isFlipped() ? 'b' : 'w';
              break;
            }
            if (cls.includes('clock-player-top') || cls.includes('clock-top')) {
              detectedTurn = profile.isFlipped() ? 'w' : 'b';
              break;
            }
          }
          if (detectedTurn) break;
          // Geometry fallback: above/below the board centre.
          const rect = el.getBoundingClientRect();
          const boardEl = findBoardElement();
          if (boardEl && rect.width > 0) {
            const bRect = boardEl.getBoundingClientRect();
            const isBottom = rect.top > bRect.top + bRect.height / 2;
            detectedTurn = profile.isFlipped() ? (isBottom ? 'b' : 'w') : (isBottom ? 'w' : 'b');
            break;
          }
        }
      }
    } catch (_) {}
    _lastRealTurnResult = detectedTurn;
    _lastRealTurnTime = now;
    return detectedTurn;
  };

  // Modern chess.com renders the board as a <wc-chess-board> web component and
  // puts the piece layer inside its SHADOW ROOT, which querySelectorAll cannot
  // pierce from this world — the old scan saw the board element but zero pieces.
  // Collect every element under `root`, descending into nested shadow trees, so
  // the piece scan sees the whole board regardless of encapsulation.
  const allElementsPiercing = (root) => {
    const cached = rootCache.get(root);
    if (cached && cached.until > Date.now()) return cached.elements;

    const out = [];
    const collect = (node) => {
      try {
        const kids = node.querySelectorAll('*');
        for (let i = 0; i < kids.length; i++) {
          out.push(kids[i]);
          // Descend into shadow trees of children only — do NOT also recurse
          // on node.shadowRoot here; it was already handled by the PARENT call,
          // so the old unconditional tail-call doubled every shadow-root walk.
          if (kids[i].shadowRoot) collect(kids[i].shadowRoot);
        }
      } catch (_) {}
    };
    // Seed: if the root itself has a shadow tree, start from there.
    if (root.shadowRoot) collect(root.shadowRoot);
    collect(root);
    rootCache.set(root, {until: Date.now() + 16, elements: out});
    return out;
  };

  const findPiecesPiercing = (boardEl, selector) => {
    const pieces = [];
    try {
      for (const el of allElementsPiercing(boardEl)) {
        if (el.matches && el.matches(selector)) pieces.push(el);
      }
    } catch (_) {}
    return pieces;
  };

  const getBoardData = () => {
    const boardEl = findBoardElement();
    if (!boardEl) return null;
    const boardRect = boardEl.getBoundingClientRect();
    if (boardRect.width === 0 || boardRect.height === 0) return null;

    let pieces = findPiecesPiercing(boardEl, profile.pieces);
    if (pieces.length === 0) {
      // Fallback for boards that renamed their piece class: any descendant
      // whose class mentions "piece".  Non-piece containers get filtered out
      // by getPieceInfo (they carry no bw 2-character class).
      pieces = findPiecesPiercing(boardEl,
        '[class*="piece"]:not(.dragging):not(.ghost):not(.premove):not(.fading):not(.hidden)');
    }
    if (pieces.length === 0) {
      // Last resort: any element whose class list holds the standard 2-char
      // piece token (wp/wn/wb/wq/wk/wr/…).  getPieceInfo validates it before
      // use, so stray matches simply fail its piece-class check and drop out.
      pieces = findPiecesPiercing(boardEl, '[class*="wp"],[class*="wq"],[class*="wk"],[class*="wn"],[class*="wb"],[class*="wr"],[class*="bp"],[class*="bq"],[class*="bk"],[class*="bn"],[class*="bb"],[class*="br"]');
    }
    if (pieces.length === 0) return null;

    const board = Array(8).fill(null).map(() => Array(8).fill(null));
    let resolved = 0;

    for (const piece of pieces) {
      const info = profile.getPieceInfo(piece, boardRect);
      if (info && info.x >= 0 && info.x < 8 && info.y >= 0 && info.y < 8) {
        board[7 - info.y][info.x] = info.fenChar;
        resolved++;
      }
    }
    // No piece resolved: the board is mid-render or in an unsupported layout.
    // Emitting an all-empty FEN would just be garbage the GUI rejects.
    if (resolved === 0) return null;

    let fen = '';
    for (let r = 0; r < 8; r++) {
      let empty = 0;
      for (let c = 0; c < 8; c++) {
        if (board[r][c]) {
          if (empty > 0) {
            fen += empty;
            empty = 0;
          }
          fen += board[r][c];
        } else {
          empty++;
        }
      }
      if (empty > 0) fen += empty;
      if (r < 7) fen += '/';
    }

    return { board, basicFen: fen };
  };

  let lastStableFen = null;
  let candidateFen = null;
  let stableTimer = null;
  let currentTurn = null;
  let lastCommittedTurn = null;
  let lastStableBoard = null;
  let rafPending = false;
  let firstTurnRetries = 0;
  let _lastListPly = null;

  // ---------------------------------------------------------------
  // chess.com API-backed FEN (fed by fenProvider.js in the MAIN world).
  // The current WebGL board has no DOM pieces, so the only reliable position
  // source is chess.com's own Game object (wc-chess-board.game.getFEN()).
  // When a fresh API FEN is flowing, it is authoritative and the DOM scan is
  // bypassed; the API FEN is a complete FEN (placement + turn + rights + ep).
  // ---------------------------------------------------------------
  const PROVIDER_CHANNEL = 'fen-live-provider-v1';
  let apiFen = null;
  let apiBasicFen = null;
  let apiAt = 0;
  let apiBoardCls = '';
  let apiProbe = null;

  const isApiFenValid = (fen) => {
    if (typeof fen !== 'string') return false;
    const parts = fen.trim().split(/\s+/);
    if (parts.length !== 6 || !/^(?:-|K?Q?k?q?)$/.test(parts[2]) ||
        !/^(?:-|[a-h][36])$/.test(parts[3]) || !/^\d+$/.test(parts[4]) ||
        !/^[1-9]\d*$/.test(parts[5])) return false;
    const ranks = parts[0].split('/');
    if (ranks.length !== 8 || !/^[wb]$/.test(parts[1])) return false;
    for (const r of ranks) {
      let n = 0;
      let ok = true;
      for (const ch of r) {
        if (/[1-8]/.test(ch)) n += parseInt(ch, 10);
        else if (/[prnbqkPRNBQK]/.test(ch)) n++;
        else { ok = false; break; }
      }
      if (!ok || n !== 8) return false;
    }
    return true;
  };

  // True while the API provider has delivered a FEN recently enough to trust.
  const apiFresh = () => apiAt !== 0 && Date.now() - apiAt < 6000;

  // Incremental castling/en-passant state (see commitStableBoard).
  let castlingFlags = null;
  let hasCastlingTracking = false;
  let prevBoardForCastling = null;
  let lastEpSq = null;
  let fullmoveNumber = 1;
  let halfmoveClock = 0;

  const epTargetForMove = (prevBoard, newBoard) => {
    // Returns the en-passant target square (e.g. 'd6') set by a double pawn
    // push from prevBoard -> newBoard, or null if the transition is not a
    // double push.  Both boards are 8x8 arrays indexed [row][col] with
    // row = 8 - rank (row 7 = rank 1, white's back rank).
    try {
      for (let r = 0; r < 8; r++) {
        for (let c = 0; c < 8; c++) {
          const p = prevBoard[r][c];
          if (!p || prevBoard[r][c] === newBoard[r][c]) continue;
          if (p.toLowerCase() !== 'p') continue;
          // Find where the pawn landed (same piece char on the same file).
          if (newBoard[r][c] !== null) continue; // not a source square
          let movedTo = -1;
          for (let r2 = 0; r2 < 8 && movedTo < 0; r2++) {
            if (newBoard[r2][c] === p && prevBoard[r2][c] !== p) movedTo = r2;
          }
          if (movedTo < 0) continue;
          const dr = Math.abs(movedTo - r);
          if (dr !== 2 || (p === "P" ? r !== 6 || movedTo !== 4 : r !== 1 || movedTo !== 3)) continue;
          const midRow = (r + movedTo) / 2;
          const file = String.fromCharCode(97 + c);
          const rank = 8 - midRow;
          return file + rank;
        }
      }
      return null;
    } catch (_) {
      return null;
    }
  };

  // True when two 8x8 board arrays hold identical placements.
  const boardsEqual = (a, b) => {
    try {
      if (!a || !b) return false;
      for (let r = 0; r < 8; r++) {
        for (let c = 0; c < 8; c++) {
          if (a[r][c] !== b[r][c]) return false;
        }
      }
      return true;
    } catch (_) {
      return false;
    }
  };

  let lastContextKey = null;
  let lastContextAt = 0;
  let cachedContext = null;
  const readGameContext = () => {
    const now=Date.now();
    if(cachedContext && now-lastContextAt<1000)return cachedContext;
    try {
      cachedContext=globalThis.ChessGameContext?.read({playingAs:_lastPlayingAs,
        flipped:_lastFlipped,clocks:readClocks(),variant:detectVariant()}) || null;
    } catch (_) { cachedContext=null; }
    lastContextAt=now;
    return cachedContext;
  };
  const emitGameContext = () => {
    if(stopped || !_lastSentFen)return;
    const context=readGameContext(), key=JSON.stringify(context);
    if(key===lastContextKey)return;
    Promise.resolve().then(()=>chrome.runtime.sendMessage({type:'game_context',
      context,fen:_lastSentFen,document_epoch:documentEpoch})).then(ack=>{
        if(ack?.ok)lastContextKey=key;
      }).catch(()=>{});
  };
  setInterval(emitGameContext,1500);
  let _fenSentCount = 0;
  let _lastSentFen = null;
  let _lastSentKey = null;
  let _lastPlayingAs = null;
  let _lastFlipped = null;

  let pendingDelivery = null, deliveryBusy = false, deliveryRetry = null, deliveryDelay = 100;
  const flushDelivery = () => {
    if (stopped || deliveryBusy || !pendingDelivery) return;
    const item = pendingDelivery;
    deliveryBusy = true;
    Promise.resolve().then(() => chrome.runtime.sendMessage(item.payload)).then(response => {
      if (!response?.ok) throw new Error('Position not acknowledged');
      _lastSentKey = item.key;
      _fenSentCount++;
      deliveryDelay = 100;
      if (pendingDelivery === item) pendingDelivery = null;
    }).catch(() => {
      if (!stopped && !deliveryRetry) {
        deliveryRetry = setTimeout(() => { deliveryRetry = null; flushDelivery(); }, deliveryDelay);
        deliveryDelay = Math.min(2000, deliveryDelay * 2);
      }
    }).finally(() => {
      deliveryBusy = false;
      if (pendingDelivery && pendingDelivery !== item && !deliveryRetry) flushDelivery();
    });
  };
  const dispatch = (fen, flipped = null, playingAs = null, force = false) => {
    if (!fen || stopped) return;
    const isFlipped = flipped !== null ? !!flipped : profile.isFlipped();
    const player = ['w', 'b'].includes(playingAs) ? playingAs : null;
    const key = JSON.stringify([fen, isFlipped, player]);
    if(_lastPlayingAs!==player || _lastFlipped!==isFlipped){cachedContext=null;lastContextAt=0;}
    _lastSentFen = fen; _lastPlayingAs = player; _lastFlipped = isFlipped;
    if (!force && key === _lastSentKey && !pendingDelivery) return;
    if (!force && key === pendingDelivery?.key) return;
    pendingDelivery = {key, payload:{type:'FEN', fen, flipped:isFlipped,
      playing_as:player, force, document_epoch:documentEpoch,game_context:readGameContext()}};
    flushDelivery();
  };

  // True while a piece is being dragged.  Mid-drag the board snapshot is
  // misleading: the lifted piece is omitted by the `.dragging` filter, and
  // Lichess renders a ghost on the hovered square, so the placement can look
  // like a quiet move or a capture that was never played.  Suppress all
  // dispatch while dragging and emit the final position only after the drag
  // ends (real move: new board; cancel/snap-back: nothing is sent).
  const isDragActive = () => {
    try {
      const boardEl = findBoardElement();
      return !!boardEl && findPiecesPiercing(boardEl, '.dragging').length > 0;
    } catch (_) {
      return false;
    }
  };

  const commitStableBoard = (boardData) => {
    // The API-backed FEN is authoritative and already full (turn/rights/ep).
    // On WebGL boards the DOM scan resolves nothing anyway; on legacy DOM
    // boards both paths may be live, and the API FEN wins — the DOM-derived
    // castling/ep are only ever guesses.
    if (apiFresh()) return;
    const previousBoard = lastStableBoard;
    const previousTurn = lastCommittedTurn;
    let movedPiece = null;
    let piecesAdded = [];
    if (lastStableBoard) {
      for (let r = 0; r < 8; r++) {
        for (let c = 0; c < 8; c++) {
          if (boardData.board[r][c] !== lastStableBoard[r][c]) {
            if (boardData.board[r][c] !== null) {
              piecesAdded.push(boardData.board[r][c]);
            }
          }
        }
      }
    }

    const isPuzzle = () => /puzzles|training/i.test(window.location.pathname) ||
      !!document.querySelector('.puzzle-view, .puzzle-header, .puzzle__side, [data-test-element="puzzle-status"]');

    // Detect if this is a brand new board layout (new puzzle, new game, or board reload)
    // rather than a single chess move. In chess, at most 2 pieces of the SAME color can be added
    // in one move (e.g. castling). If more than 2 pieces changed or different colors were added,
    // this is a position transition, not a move!
    const isNewBoardSetup = piecesAdded.length > 2 ||
      (piecesAdded.length === 2 && (piecesAdded[0] === piecesAdded[0].toUpperCase()) !== (piecesAdded[1] === piecesAdded[1].toUpperCase()));

    if (isNewBoardSetup) {
      piecesAdded = [];
      currentTurn = null; // force turn re-detection for the fresh setup
      castlingFlags = null;
      hasCastlingTracking = false;
      prevBoardForCastling = null;
      lastEpSq = null;
      halfmoveClock = 0;
      const ply = getMoveListPlyCount();
      fullmoveNumber = ply == null ? 1 : Math.floor(ply / 2) + 1;
    }

    if (!currentTurn) {
      if (boardData.basicFen === 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR') {
        currentTurn = 'w';
      } else if (isPuzzle()) {
        // In puzzles: "if a move is played its always the puzzle first".
        // The human is at the bottom (unflipped = White, flipped = Black).
        const boardEl = findBoardElement();
        const hasHighlight = boardEl && boardEl.querySelectorAll('.highlight, square.last-move, .last-move').length >= 2;
        const domTurn = getPuzzleTurnFromDom();
        if (domTurn) {
          currentTurn = domTurn;
        } else if (hasHighlight) {
          // Opponent has already played their move! Human's turn:
          currentTurn = profile.isFlipped() ? 'b' : 'w';
        } else {
          // Initial puzzle position before opponent plays: the puzzle plays first!
          currentTurn = profile.isFlipped() ? 'w' : 'b';
        }
      } else if (!getRealTurn() && !getAnalysisTurn() && firstTurnRetries < 3) {
        // Mid-game join / SPA mount: the pieces can render before the running
        // clock does.  Don't latch the flipped-or-white/black guess yet — wait
        // briefly for a clock- or move-list-backed turn so the very first FEN
        // is correct.
        firstTurnRetries++;
        scheduleStableCheck(350);
        return;
      } else {
        // Last resort with no clock and no move list: the player sitting at
        // the BOTTOM of the board is conventionally the one to move (their
        // clock was running).  The bottom player is White unless the board is
        // flipped, so the side to move is the bottom colour: 'w' unflipped,
        // 'b' flipped.
        currentTurn = getAnalysisTurn() || (profile.isFlipped() ? 'b' : 'w');
      }
    }

    // Turn priority: the running clock (live games) is authoritative.  Without
    // a clock (analysis, puzzles, after-game analysis) the board-diff toggle is
    // the PRIMARY heuristic — it is correct for every forward move, and this is
    // exactly the path live games relied on.  The move list is consulted ONLY
    // when the diff has nothing to say (stable position) or when the user steps
    // BACKWARD through a list (the diff toggle assumes forward play and would
    // invert the turn).  This keeps the live-game behaviour byte-for-byte
    // identical to the previous, proven version.
    const realTurn = getRealTurn();
    if (realTurn) {
      currentTurn = realTurn;
    } else if (piecesAdded.length > 0) {
      movedPiece = piecesAdded[0];
      currentTurn = movedPiece === movedPiece.toUpperCase() ? 'b' : 'w';
      const listPly = getMoveListPlyCount();
      if (listPly != null && _lastListPly != null && listPly < _lastListPly) {
        const at = getAnalysisTurn();
        if (at) currentTurn = at;
      }
      _lastListPly = listPly;
    }
    // No clock and no board change: keep the latched turn.  The first-commit
    // block above already picked it (move list -> diff -> bottom-colour guess).

    lastStableBoard = boardData.board;
    lastStableFen = boardData.basicFen;

    // The board this transition started from (may be null on the very first
    // observed position).  Captured before any state is updated below.
    const prevBoard = prevBoardForCastling;

    // ---------------------------------------------------------------------
    // Castling rights + en-passant are TRACKED incrementally, never recomputed
    // from the current placement alone.  Rights can only be LOST during a game
    // (king/rook moves or is captured away from its home square); recomputing
    // from placement is wrong because a king/rook that moved and came back home
    // would be granted rights it no longer has — which made the GUI suggest an
    // impossible castle the site would refuse.  We persist a flags object and
    // only revoke on a diff, and reset to full rights on a fresh game.
    // ---------------------------------------------------------------------
    const START_PLACEMENT = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR';
    if (boardData.basicFen === START_PLACEMENT) {
      // Fresh game (or the very first observed position): grant full rights.
      // The incremental branch can only ever revoke rights, so without this a
      // second game starting from the start placement would inherit the
      // previous game's (already revoked) flags and emit '-' castling forever.
      castlingFlags = { K: true, Q: true, k: true, q: true };
      hasCastlingTracking = true;
    } else if (!hasCastlingTracking) {
      // First observed board (mid-game join): rights cannot be deduced from a
      // placement, so make our best guess from home squares, then from here on
      // out only ever revoke.  The GUI will still see the correct rights once
      // the king/rook ever leaves home afterward.
      const g = boardData.board;
      const flags = { K: true, Q: true, k: true, q: true };
      if (g[7][4] !== 'K') { flags.K = false; flags.Q = false; }
      else { if (g[7][7] !== 'R') flags.K = false; if (g[7][0] !== 'R') flags.Q = false; }
      if (g[0][4] !== 'k') { flags.k = false; flags.q = false; }
      else { if (g[0][7] !== 'r') flags.k = false; if (g[0][0] !== 'r') flags.q = false; }
      castlingFlags = flags;
      hasCastlingTracking = true;
    } else {
      // Apply prevBoard -> newBoard to our persistent rights.
      const prev = prevBoard || boardData.board;
      const g = boardData.board;
      const flags = { ...castlingFlags };
      // White king left e1 -> forfeit both white rights.
      if (prev[7][4] === 'K' && g[7][4] !== 'K') { flags.K = false; flags.Q = false; }
      // White king present but a back-rank rook lost -> forfeit that side only.
      if (g[7][4] === 'K') {
        if (flags.K && prev[7][7] === 'R' && g[7][7] !== 'R') flags.K = false;
        if (flags.Q && prev[7][0] === 'R' && g[7][0] !== 'R') flags.Q = false;
      }
      // Black king moved -> both black rights gone.
      if (prev[0][4] === 'k' && g[0][4] !== 'k') { flags.k = false; flags.q = false; }
      if (g[0][4] === 'k') {
        if (flags.k && prev[0][7] === 'r' && g[0][7] !== 'r') flags.k = false;
        if (flags.q && prev[0][0] === 'r' && g[0][0] !== 'r') flags.q = false;
      }
      castlingFlags = flags;
    }
    prevBoardForCastling = boardData.board;

    // En-passant: a double pawn push (a pawn whose starting and ending ranks on
    // the same file differ by 2) sets the ep target to the square it passed
    // over.  Any other transition clears it.  epIndex = (8 - rank); a white
    // push 2->4 passes rank 3 (row 5), a black push 7->5 passes rank 6
    // (row 2).
    //
    // lastEpSq persists the ep square for the current position.  Re-commits of
    // an identical placement (turn-watcher corrections, fallback ticks) must
    // NOT clear or recompute it: the ep target belongs to the position, and the
    // previous code derived it from prevEpFen, which was overwritten on every
    // commit — so a corrected re-emit erased the en-passant field the GUI had
    // already received.
    let epSq = lastEpSq || '-';
    if (prevBoard && !boardsEqual(prevBoard, boardData.board)) {
      epSq = epTargetForMove(prevBoard, boardData.board) || '-';
    }
    if (boardData.basicFen === START_PLACEMENT) {
      epSq = '-';
    }
    lastEpSq = epSq;

    let rights = '';
    if (castlingFlags.K) rights += 'K';
    if (castlingFlags.Q) rights += 'Q';
    if (castlingFlags.k) rights += 'k';
    if (castlingFlags.q) rights += 'q';
    if (!rights) rights = '-';

    if (boardData.basicFen === START_PLACEMENT) {
      fullmoveNumber = 1;
      halfmoveClock = 0;
    } else if (previousBoard && !isNewBoardSetup && !boardsEqual(previousBoard, boardData.board)) {
      const oldPieces = previousBoard.flat().filter(Boolean).length;
      const newPieces = boardData.board.flat().filter(Boolean).length;
      let pawnMoved = false;
      for (let r = 0; r < 8; r++) for (let c = 0; c < 8; c++) {
        if (previousBoard[r][c]?.toLowerCase() === 'p' && previousBoard[r][c] !== boardData.board[r][c]) pawnMoved = true;
      }
      halfmoveClock = pawnMoved || newPieces < oldPieces ? 0 : halfmoveClock + 1;
      if (previousTurn === 'b') fullmoveNumber++;
      const ply = getMoveListPlyCount();
      if (ply != null) fullmoveNumber = Math.floor(ply / 2) + 1;
    }
    const fullFen = `${boardData.basicFen} ${currentTurn} ${rights} ${epSq} ${halfmoveClock} ${fullmoveNumber}`;
    lastCommittedTurn = currentTurn;
    dispatch(fullFen, profile.isFlipped(), null);
  };

  const scheduleStableCheck = (delayBase = 100) => {
    const delay = Math.max(20, delayBase);
    clearTimeout(stableTimer);
    stableTimer = setTimeout(() => {
      stableTimer = null;
      if (stopped) return;
      if (isDragActive()) { scheduleStableCheck(60); return; }
      const currentData = getBoardData();
      if (currentData && currentData.basicFen === candidateFen) {
        commitStableBoard(currentData);
      } else {
        candidateFen = null;
        checkBoard();
      }
    }, delay);
  };

  const checkBoard = () => {
    if (stopped || apiFresh()) return;
    if (isDragActive()) {
      candidateFen = null;
      clearTimeout(stableTimer);
      stableTimer = null;
      return;
    }
    const boardData = getBoardData();
    if (!boardData) return;

    if (boardData.basicFen !== lastStableFen) {
      if (boardData.basicFen !== candidateFen || stableTimer === null) {
        candidateFen = boardData.basicFen;
        _lastRealTurnTime = 0;
        scheduleStableCheck(30);
      }
    } else if (candidateFen !== lastStableFen) {
      candidateFen = lastStableFen;
      clearTimeout(stableTimer);
    }
  };

  let mo = null;
  let fallbackTimer = null;
  let turnTimer = null;
  let observing = false;
  let observedBoard = null;

  const attachObserver = () => {
    const boardEl = findBoardElement();
    if (!boardEl) {
      observing = false;
      observedBoard = null;
      return !!boardEl;
    }
    if (observing && observedBoard === boardEl && Date.now() < shadowRefreshAt) return !!boardEl;
    shadowRefreshAt = Date.now() + 2000;

    // The SPA can swap the board element out and back in (lobby <-> game,
    // game <-> analysis).  Re-observing the CURRENT element every poll keeps
    // the observer alive across those swaps instead of silently going dead.
    if (mo) {
      try { mo.disconnect(); } catch (_) {}
      mo = null;
    }

    mo = new MutationObserver(mutations => {
      rootCache = new WeakMap();
      if (mutations.some(m => m.type === 'childList')) shadowRefreshAt = 0;

      if (rafPending) return;
      rafPending = true;
      requestAnimationFrame(() => {
        rafPending = false;
        checkBoard();
      });
    });

    mo.observe(boardEl, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ['class', 'style', 'transform']
    });
    // Pieces live inside the web-component's shadow tree; mutations there do
    // NOT fire observers attached to the host element.  Watch EVERY shadow
    // root reachable under the board (the piece layer can be nested several
    // levels deep) or live moves would only arrive via the slower poll.
    const shadowRoots = [];
    for (const el of allElementsPiercing(boardEl)) {
      if (el.shadowRoot) shadowRoots.push(el.shadowRoot);
    }
    if (boardEl.shadowRoot) shadowRoots.push(boardEl.shadowRoot);
    for (const sr of shadowRoots) {
      try {
        mo.observe(sr, {
          subtree: true,
          childList: true,
          attributes: true,
          attributeFilter: ['class', 'style', 'transform']
        });
      } catch (_) {}
    }
    if (boardEl.parentElement) {
      mo.observe(boardEl.parentElement, { childList: true, subtree: false });
    }
    observedBoard = boardEl;
    observing = true;
    checkBoard();
    return true;
  };

  const startFallback = () => {
    const tick = () => {
      if (stopped) return;
      // Always run this safety poll, not just when the first attach failed:
      // attachObserver re-binds to the current board element if the SPA
      // replaced it, and checkBoard is a cheap no-op when nothing changed.
      attachObserver();
      checkBoard();
      const next = apiFresh() ? 2000 : 600 + Math.floor(Math.random() * 400);
      fallbackTimer = setTimeout(tick, next);
    };
    tick();
  };

  // Re-emit when ONLY the side-to-move changes (the placement is stable).  A
  // premove can leave the board at its final placement while the running clock
  // flips afterwards (e.g. enemy premove executing against our move), and the
  // board-diff heuristic in commitStableBoard would never fire — the GUI would
  // keep showing the old turn.  The running clock is authoritative, so when it
  // no longer matches the turn we last emitted, push the same placement again
  // with the corrected turn.
  const startTurnWatcher = () => {
    turnTimer = setInterval(() => {
      try {
        if (apiFresh() || isDragActive()) return;
        const realTurn = getRealTurn();
        if (!realTurn) return;
        const boardData = getBoardData();
        if (!boardData) return;
        if (boardData.basicFen !== lastStableFen) return;
        if (realTurn === lastCommittedTurn) return;
        currentTurn = realTurn;
        commitStableBoard(boardData);
      } catch (_) {}
    }, 120);
  };
  startTurnWatcher();

  // Native FEN updates and periodic snapshots also recover late listeners.
  // Keep DOM fallback state in sync and deduplicate complete position metadata.
  window.addEventListener('message', (event) => {
    try {
      // Cross-world postMessage: event.source is the frame's WindowProxy.
      // Reject other windows and origins. The page itself can still publish
      // this channel, so it is a source check rather than authentication.
      if (event.source !== window || event.origin !== window.location.origin) return;
      const d = event.data;
      if (!d || typeof d !== 'object' || d.source !== PROVIDER_CHANNEL) return;
      if (d.type === 'diag' && d.probe) {
        apiProbe = d.probe;
        return;
      }
      if (!['fen', 'heartbeat'].includes(d.type) || !isApiFenValid(d.fen)) return;
      const fullFen = d.fen.trim().replace(/\s+/g, ' ');
      if (fullFen === apiFen) {
        apiAt = Date.now();
        dispatch(fullFen, d.flipped, d.playing_as);
        return;
      }
      apiFen = fullFen;
      apiAt = Date.now();
      apiBoardCls = String(d.cls || '');
      const parts = fullFen.split(' ');
      const basic = parts[0];
      const turn = parts[1];
      apiBasicFen = basic;
      if (parts.length === 6) {
        const rows = basic.split('/').map(row => [...row].flatMap(c => /[1-8]/.test(c) ? Array(Number(c)).fill(null) : [c]));
        lastStableBoard = rows;
        prevBoardForCastling = rows;
        castlingFlags = Object.fromEntries([..."KQkq"].map(c => [c, parts[2].includes(c)]));
        hasCastlingTracking = true;
        lastEpSq = parts[3] === '-' ? null : parts[3];
        halfmoveClock = Number(parts[4]);
        fullmoveNumber = Number(parts[5]);
      }
      lastStableFen = basic;
      candidateFen = basic;
      lastCommittedTurn = turn;
      currentTurn = turn;
      _lastListPly = null;
      dispatch(fullFen, d.flipped, d.playing_as);
    } catch (_) {}
  });

  // Diagnostic heartbeat: reports whether the board is being read and how many
  // FENs were sent, straight to the service-worker console (never the page).
  // Lets us tell apart "extension not reading the board" from "FENs sent but
  // suggestions not drawn" without exposing anything to the site.  Reports
  // immediately on load AND on a timer (even when nothing changed) so the
  // popup can show whether the content script is alive before any FEN exists.
  let _diagSentAt = 0;
  const _instId = Math.random().toString(36).slice(2, 8);
  const reportStatus = () => {
    try {
      const boardEl = findBoardElement();
      const native = apiFresh();
      const elements = !native && boardEl ? allElementsPiercing(boardEl) : [];
      const pieces = elements.filter(el => el.matches?.(profile.pieces)).length;
      const piecesFallback = native ? 0 : elements.filter(el => el.matches?.('[class*="piece"]')).length;
      const piecesLastResort = 0;
      const currentData = !native ? getBoardData() : null;
      const resolved = currentData?.board.flat().filter(Boolean).length || 0;
      const basicFen = currentData?.basicFen || null;
      chrome.runtime.sendMessage({
        type: 'contentStatus',
        inst: _instId,
        host: window.location.hostname,
        board: !!boardEl,
        boardTag: boardEl ? (boardEl.tagName || '') : '',
        boardCls: boardEl ? String(boardEl.className || '') : '',
        shadow: !!boardEl && !!boardEl.shadowRoot,
        pieces,
        piecesFallback,
        piecesLastResort,
        resolved,
        fen: apiFresh() ? (apiBasicFen || basicFen) : basicFen,
        api: apiFresh(),
        apiFen: apiFresh() ? apiFen : null,
        apiBoardCls,
        probe: apiProbe,
        drag: !native && !!boardEl && findPiecesPiercing(boardEl, '.dragging').length > 0,
        turn: currentTurn,
        lastFen: lastStableFen,
        candidate: candidateFen,
        fenCount: _fenSentCount
      }).catch(() => {});
    } catch (_) {}
  };
  reportStatus();
  const diagnosticTimer = setInterval(() => {
    const now = Date.now();
    if (now - _diagSentAt < 3000) return;
    _diagSentAt = now;
    reportStatus();
  }, 3000);

  // Keep the GUI supplied with the board's absolute screen position so the
  // real-mouse AutoMove never has to wait for a request/response roundtrip.
  try {
    sendBoardGeometry();
  } catch (_) {}
  const geometryTimer = setInterval(() => {
    try {
      sendBoardGeometry();
    } catch (_) {}
  }, 1500);

  // Always poll (see startFallback above) so FEN updates survive SPA board
  // swaps that would otherwise leave the MutationObserver watching a detached
  // element with no fallback to recover.
  const onGeometryChange = () => { invalidateBoard(); sendBoardGeometry(); };
  window.addEventListener('resize', onGeometryChange);
  window.addEventListener('scroll', onGeometryChange, {passive:true, capture:true});
  const onDragEnd = () => { if (!stopped && !apiFresh()) { candidateFen = null; checkBoard(); } };
  window.addEventListener('pointerup', onDragEnd, true);
  window.addEventListener('pointercancel', onDragEnd, true);
  startFallback();

  // Cleanup hook (used by automated tests). Lives on the isolated world's own
  // global scope, which page scripts can never see, so it adds no footprint.
  try {
    Object.defineProperty(globalThis, Symbol.for('0x7f3a9c'), {
      value: {
        stop() {
          stopped = true;
          window.removeEventListener('resize', onGeometryChange);
          window.removeEventListener('scroll', onGeometryChange, true);
          window.removeEventListener('pointerup', onDragEnd, true);
          window.removeEventListener('pointercancel', onDragEnd, true);
          clearTimeout(deliveryRetry);
          pendingDelivery = null;
          try { mo && mo.disconnect(); } catch (_) {}
          clearTimeout(stableTimer);
          clearTimeout(fallbackTimer);
          clearInterval(turnTimer);
          clearInterval(diagnosticTimer);
          clearInterval(geometryTimer);
        }
      },
      enumerable: false,
      configurable: true
    });
  } catch (_) {}

  // ============================================================================
  // PHASE 5: Extension internals — handle commands from the Python GUI
  // ============================================================================
  let _privacyHidden = false;

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (!msg || !msg.action) return;

    if (msg.action === "get_state") {
      if (_lastSentFen) dispatch(_lastSentFen, _lastFlipped, _lastPlayingAs, true);
      sendBoardGeometry(true);
      sendResponse?.({ok:!!_lastSentFen});
      return;
    }
    if (msg.action === "queue_next") {
      // No verified next-game site adapter was supplied. Do not click a guessed element.
      sendResponse?.({ok:false,reason:"queue_next_not_implemented"});
      return;
    }
    if (msg.action === "execute_move" || msg.action === "click_promotion") {
      sendResponse?.({ok:false, reason:"native_input_required"});
      return true;
    }
    if (msg.action === "get_promotion_geometry") {
      const result = promotionGeometry(msg.piece);
      chrome.runtime.sendMessage({type:"promotion_geometry", request_id:msg.request_id,
                                  piece:msg.piece, ...result}).catch(() => {});
      sendResponse?.({ok:true});
      return true;
    }

    if (msg.action === "get_board_geometry") {
      // Report the board's absolute screen position so the Python GUI can
      // drive the REAL OS mouse (SendInput) instead of dispatching synthetic
      // DOM events.  Coordinates are in CSS pixels relative to the primary
      // screen's top-left; the browser chrome offset is approximated from
      // outer/inner sizes and "scale" (devicePixelRatio) lets the GUI convert
      // to physical pixels.  The same geometry is also broadcast periodically
      // (see sendBoardGeometry) so the GUI never has to wait on this roundtrip.
      const resp = computeBoardGeometry();
      try {
        chrome.runtime.sendMessage({ type: "board_geometry", ...resp });
      } catch (_) {}
      sendResponse?.({ ok: resp.ok });
      return true;
    }

    if (msg.action === "click_promotion") {
      // The GUI already dragged with the real mouse; just pick the promoted
      // piece from the chooser on the page.
      const ok = clickPromotionPiece(msg.piece);
      sendResponse?.({ ok });
      return true;
    }

    if (msg.action === "set_privacy") {
      _privacyHidden = !!msg.hidden;
      const overlay = document.getElementById("chess-overlay-frame") ||
                      document.querySelector("[id*='chess-overlay']");
      if (overlay) overlay.style.display = _privacyHidden ? "none" : "";
      sendResponse?.({ ok: true });
      return;
    }

    if (msg.action === "panic") {
      _privacyHidden = true;
      document.querySelectorAll("[id*='chess-overlay'], [class*='chess-overlay']").forEach(el => {
        el.style.display = "none";
      });
      sendResponse?.({ ok: true });
      return;
    }

    if (msg.action === "get_clocks") {
      const clocks = readClocks();
      try {
        chrome.runtime.sendMessage({ type: "clocks", self_ms: clocks?.self, opp_ms: clocks?.opponent });
      } catch (_) {}
      sendResponse?.({ ok: true, clocks });
      return true;
    }

    if (msg.action === "get_exploits") {
      const host = window.location.hostname;
      const features = [];
      if (host.includes("chess.com")) {
        features.push("auto_promote", "fast_board", "free_analysis", "unlock_bots");
      } else if (host.includes("lichess.org")) {
        features.push("auto_queen", "zen_off", "berserk");
      }
      features.push("clock_read", "variant_detect");
      try {
        chrome.runtime.sendMessage({ type: "site_exploits", features });
      } catch (_) {}
      sendResponse?.({ ok: true, features });
      return true;
    }
  });

  function readClocks() {
    const host = window.location.hostname;
    try {
      if (host.includes("chess.com")) {
        const clocks = document.querySelectorAll(".clock-time-monospace, .clock time, [class*='clock'] [class*='time']");
        const withPos = [];
        for (const el of clocks) {
          const m = /(\d+):(\d+)(?:[.:](\d+))?/.exec(el.textContent || "");
          if (!m) continue;
          const value = (Number(m[1]) * 60 + Number(m[2])) * 1000 +
            (m[3] ? Number(m[3].padEnd(3, "0").slice(0, 3)) : 0);
          withPos.push({v:value,y:el.getBoundingClientRect().top});
        }
        if (withPos.length >= 2) {
          withPos.sort((a,b) => a.y-b.y);
          const bottom = withPos.at(-1).v, top = withPos[0].v;
          const bottomColor = profile.isFlipped() ? 'b' : 'w';
          const selfAtBottom = !_lastPlayingAs || _lastPlayingAs === bottomColor;
          return {self:selfAtBottom?bottom:top,opponent:selfAtBottom?top:bottom};
        }
      }
      if (host.includes("lichess.org")) {
        const clockEl = document.querySelector(".clock .time, .clock1 .time");
        const oppEl = document.querySelectorAll(".clock .time, .clock1 .time");
        if (oppEl.length >= 2) {
          const parse = (txt) => {
            const m = /(\d+):(\d+)/.exec(txt || "");
            return m ? (Number(m[1]) * 60 + Number(m[2])) * 1000 : null;
          };
          const positioned = [...oppEl].sort((a,b) => a.getBoundingClientRect().top-b.getBoundingClientRect().top);
          const bottomColor = profile.isFlipped() ? 'b' : 'w';
          const selfAtBottom = !_lastPlayingAs || _lastPlayingAs === bottomColor;
          const myEl = selfAtBottom ? positioned.at(-1) : positioned[0];
          const oppClock = selfAtBottom ? positioned[0] : positioned.at(-1);
          const selfMs = parse(myEl?.textContent);
          const oppMs = parse(oppClock?.textContent);
          if (selfMs != null && oppMs != null) return { self: selfMs, opponent: oppMs };
        }
      }
    } catch (_) {}
    return null;
  }

  // Detect chess variant from page
  function detectVariant() {
    const host = window.location.hostname;
    const url = window.location.href;
    if (url.includes("chess960") || url.includes("chess-960") || document.body?.classList?.contains("variant960")) return "chess960";
    if (url.includes("crazyhouse") || url.includes("crazy-house")) return "crazyhouse";
    if (url.includes("kingofthehill") || url.includes("king-of-the-hill")) return "king_of_the_hill";
    if (url.includes("3check") || url.includes("three-check")) return "three_check";
    if (url.includes("antichess") || url.includes("giveaway")) return "antichess";
    if (url.includes("atomic")) return "atomic";
    if (url.includes("horde")) return "horde";
    if (url.includes("racing-kings")) return "racing_kings";
    return "standard";
  }

  // Report variant on load
  try {
    const variant = detectVariant();
    if (variant !== "standard") {
      chrome.runtime.sendMessage({ type: "variant_detected", variant });
    }
  } catch (_) {}

  // ---------------------------------------------------------------------------
  // Board geometry for the REAL OS mouse (SendInput path).
  // ---------------------------------------------------------------------------
  function computeBoardGeometry() {
    let resp = { ok: false };
    try {
      const profile = getSiteProfile();
      const boardEl = findBoardElement();
      if (boardEl) {
        const r = boardEl.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) {
          const sx = window.screenX + (window.outerWidth - window.innerWidth) / 2;
          const sy = window.screenY + (window.outerHeight - window.innerHeight);
          resp = {
            ok: true,
            left: r.left + sx,
            top: r.top + sy,
            width: r.width,
            height: r.height,
            flipped: !!profile.isFlipped(),
            scale: window.devicePixelRatio || 1,
            host: window.location.hostname
          };
        }
      }
    } catch (_) {
      resp = { ok: false };
    }
    return resp;
  }

  // Broadcast the board's absolute screen position on an interval so the GUI
  // always has fresh geometry without a request/response roundtrip.
  var lastGeometryKey = null, lastGeometryAt = 0;
  function sendBoardGeometry(force = false) {
    try {
      const geometry = computeBoardGeometry();
      const key = JSON.stringify(geometry);
      if (!force && key === lastGeometryKey && Date.now() - lastGeometryAt < 10000) return;
      chrome.runtime.sendMessage({type:'board_geometry', ...geometry}).then(response => {
        if (response?.ok) { lastGeometryKey = key; lastGeometryAt = Date.now(); }
      }).catch(() => {});
    } catch (_) {}
  }

  // Locate only a visible promotion option; this function never generates input.
  function promotionGeometry(pieceLetter) {
    const pl = String(pieceLetter || '').toLowerCase();
    const names = {q:'queen',r:'rook',b:'bishop',n:'knight'};
    if (!names[pl]) return {ok:false};
    const board = findBoardElement();
    if (!board) return {ok:false};
    const br = board.getBoundingClientRect();
    if (!(br.width > 0 && br.height > 0)) return {ok:false};
    const pieces = document.querySelectorAll(
      ".promotion-piece, [class*='promotion'] .piece, [class*='promotion'] [data-piece], [class*='piece' i][class*='promo' i]");
    for (const el of pieces) {
      const tokens = [String(el.dataset?.piece || ''), ...String(el.className || '').split(/\s+/),
                      String(el.textContent || '').trim()].map(s => s.toLowerCase());
      if (!tokens.some(t => [pl,'w'+pl,'b'+pl,names[pl]].includes(t))) continue;
      const r = el.getBoundingClientRect();
      if (!(r.width > 0 && r.height > 0)) continue;
      const x = r.left+r.width/2, y = r.top+r.height/2;
      const hit = document.elementFromPoint(x,y);
      if (hit !== el && !el.contains(hit)) continue;
      return {ok:true, x:(x-br.left)/br.width, y:(y-br.top)/br.height,
              width:r.width/br.width, height:r.height/br.height};
    }
    return {ok:false};
  }
})();
