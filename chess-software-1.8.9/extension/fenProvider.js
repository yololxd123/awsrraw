// fenProvider.js — runs in the PAGE's MAIN world (classic <script> injection
// by mainListener.js; also declared in the manifest with "world": "MAIN").
//
// Current chess.com renders the board on a WebGL canvas
// (<wc-chess-board class="...board-webgl-2d">) and puts NO piece elements in
// the DOM, so the isolated-world DOM scan sees 0 pieces and can never produce
// a FEN.  The web component DOES carry chess.com's own Game object ("game"
// expando), which exposes getFEN()/getOptions()/getTurn()/on(...) regardless
// of how the board is drawn.  This script — running in the page's MAIN world,
// where the page's own JS and its expando properties are visible — reads that
// native FEN and posts it back to the isolated world via window.postMessage.
// Strictly read-only: it never mutates the page's game state.
(function () {
  "use strict";

  // Guard: a page load may execute this via BOTH the manifest MAIN-world
  // entry (document_start) and a script-tag injection (document_idle).  The
  // flag lives in the MAIN world, so both copies see it.  Only one runs.
  try {
    if (window.__fenLiveProviderInjected) return;
    window.__fenLiveProviderInjected = true;
  } catch (_) {}

  var CHANNEL = "fen-live-provider-v1";

  var lastFen = null;
  var lastStateKey = null;
  var lastHeartbeat = 0;
  var cachedBoard = null;
  var scheduledScan = null;
  var lastDiagSent = 0;
  var lastDiagKey = "";

  function isValidFen(fen) {
    if (typeof fen !== "string") return false;
    var parts = fen.trim().split(/\s+/);
    if (parts.length !== 6 || !/^(?:-|K?Q?k?q?)$/.test(parts[2]) || !/^(?:-|[a-h][36])$/.test(parts[3]) || !/^\d+$/.test(parts[4]) || !/^[1-9]\d*$/.test(parts[5])) return false;
    var ranks = parts[0].split("/");
    if (ranks.length !== 8 || !/^[wb]$/.test(parts[1])) return false;
    for (var i = 0; i < ranks.length; i++) {
      var n = 0;
      var ok = true;
      for (var j = 0; j < ranks[i].length; j++) {
        var ch = ranks[i][j];
        if (/[1-8]/.test(ch)) n += parseInt(ch, 10);
        else if (/[prnbqkPRNBQK]/.test(ch)) n++;
        else { ok = false; break; }
      }
      if (!ok || n !== 8) return false;
    }
    return true;
  }

  function emit(fen, boardEl, flipped, playingAs) {
    if (!isValidFen(fen)) return;
    var norm = fen.trim().replace(/\s+/g, " ");
    var key = JSON.stringify([norm, !!flipped, playingAs || null]);
    if (key === lastStateKey) {
      if (Date.now() - lastHeartbeat >= 2000) {
        lastHeartbeat = Date.now();
        window.postMessage({source: CHANNEL, type: "heartbeat", fen: norm,
                            flipped: !!flipped, playing_as: playingAs || null}, window.location.origin);
      }
      return;
    }
    lastStateKey = key;
    lastFen = norm;
    lastHeartbeat = Date.now();
    try {
      window.postMessage({
        source: CHANNEL,
        type: "fen",
        fen: norm,
        flipped: !!flipped,
        playing_as: playingAs || null,
        tag: boardEl ? boardEl.tagName : "",
        cls: boardEl ? String(boardEl.className || "") : ""
      }, window.location.origin);
    } catch (_) {}
  }

  function readFrom(boardEl) {
    try {
      if (!boardEl || !boardEl.game) return false;
      var fen = boardEl.game.getFEN ? boardEl.game.getFEN() : null;
      if (!fen) return false;
      var flipped = false;
      try {
        var opts = boardEl.game.getOptions ? boardEl.game.getOptions() : null;
        if (opts) flipped = !!(opts.flipped);
      } catch (_) {}
      var playingAs = null;
      try {
        if (typeof boardEl.game.getPlayingAs === "function") {
          var pa = boardEl.game.getPlayingAs();
          if (pa === 1 || pa === "w" || pa === "white") playingAs = "w";
          else if (pa === 2 || pa === "b" || pa === "black") playingAs = "b";
        }
      } catch (_) {}
      emit(fen, boardEl, flipped, playingAs);
      return true;
    } catch (_) {
      return false;
    }
  }

  // Prefer the visible board that has a live Game object.  Lobby/preview
  // boards render earlier but are small and/or game-less; the area check picks
  // the real one.  The selector list is deliberately broad so daily/analysis
  // variants that mount under different containers are still found.
  function findBoard() {
    if (cachedBoard && cachedBoard.isConnected && cachedBoard.game) {
      var currentRect = cachedBoard.getBoundingClientRect();
      if (currentRect.width > 0 && currentRect.height > 0) return cachedBoard;
    }
    var nodes = document.querySelectorAll(
      "wc-chess-board, chess-board, #board-single, [id*='board'], [class*='board']"
    );
    var best = null;
    var bestArea = 0;
    for (var i = 0; i < nodes.length; i++) {
      var node = nodes[i];
      if (!node.game) continue;
      try {
        var r = node.getBoundingClientRect();
        var area = r.width * r.height;
        if (area > bestArea) { bestArea = area; best = node; }
      } catch (_) {
        if (!best) best = node;
      }
    }
    cachedBoard = best;
    return best;
  }

  // What we can see on the page right now — sent to the isolated world so the
  // service-worker console can tell "MAIN script not running" apart from
  // "board/game missing" apart from "getFEN returned junk".
  function probeState() {
    var o = { found: false, t: "", c: "", game: false, getFEN: false, len: 0, sample: "" };
    var board = null;
    try { board = findBoard(); } catch (_) {}
    o.found = !!board;
    if (!board) return o;
    o.t = board.tagName || "";
    o.c = String(board.className || "");
    o.game = !!board.game;
    o.getFEN = !!(board.game && typeof board.game.getFEN === "function");
    if (o.getFEN) {
      try {
        var f = board.game.getFEN();
        o.len = f ? String(f).length : 0;
        o.sample = f ? String(f).slice(0, 64) : "(empty)";
      } catch (e) {
        o.sample = "ERR:" + (e && e.message ? e.message : String(e));
      }
    }
    return o;
  }

  function reportDiag() {
    try {
      var now = Date.now();
      if (now - lastDiagSent < 2000) return;
      var p = probeState();
      var key = JSON.stringify(p);
      if (key === lastDiagKey) return;
      lastDiagKey = key;
      lastDiagSent = now;
      window.postMessage({ source: CHANNEL, type: "diag", probe: p }, window.location.origin);
    } catch (_) {}
  }

  function scan() {
    try {
      var board = findBoard();
      if (board) {
        readFrom(board);
        observeGame(board);
      }
      const target = board || document;
      if (observer && target !== observedTarget) {
        observer.disconnect();
        observer.observe(target, {childList:true, subtree:true, attributes:!!board,
          ...(board ? {attributeFilter:['class']} : {})});
        observedTarget = target;
      }
    } catch (_) {}
    reportDiag();
  }

  // Subscribe to chess.com's own game events so we don't wait for the poll.
  var seen = new WeakSet();
  function observeGame(boardEl) {
    try {
      var g = boardEl.game;
      if (!g || !g.on || seen.has(g)) return;
      seen.add(g);
      var onEvt = scheduleScan;
      var events = ["Move", "ResetGame", "ModeChanged", "RendererSet", "UpdateOptions"];
      for (var i = 0; i < events.length; i++) {
        try { g.on(events[i], onEvt); } catch (_) {}
      }
    } catch (_) {}
  }

  function scheduleScan() {
    if (scheduledScan !== null) return;
    scheduledScan = setTimeout(function () { scheduledScan = null; scan(); }, 20);
  }

  var observer = null;
  var observedTarget = null;
  try {
    observer = new MutationObserver(function () {
      scheduleScan();
    });
    observer.observe(document, { childList: true, subtree: true });
    observedTarget = document;
  } catch (_) {}

  // Poll fallback: analysis/puzzle boards may never fire the event names above.
  function poll() {
    scan();
    setTimeout(poll, cachedBoard?.isConnected && cachedBoard.game ? 500 : 100);
  }
  setTimeout(poll, 100);

  // The board may already exist by the time this runs.
  scan();
})();
