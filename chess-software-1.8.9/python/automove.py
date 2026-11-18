import os
import sys
import ctypes
import struct
import math
import time
import random
import threading
from ctypes import wintypes

# Win32 input structures. OS-generated input is distinct from physical hardware.
_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_ABSOLUTE = 0x8000
_MOUSEEVENTF_VIRTUALDESK = 0x4000
_ULONG_PTR = ctypes.c_size_t



class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("mi", _MOUSEINPUT)]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _BITMAPINFOHEADER(ctypes.Structure):
    """Minimal BITMAPINFOHEADER for GetDIBits (24/32bpp screen read-back).
    biHeight is negative so rows are top-down, matching screen coordinates."""
    _fields_ = [("biSize", ctypes.c_uint32),
                ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32),
                ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16),
                ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32),
                ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32),
                ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]

    def __init__(self, width, height):
        super().__init__()
        self.biSize = ctypes.sizeof(self)
        self.biWidth = int(width)
        self.biHeight = -int(height)     # negative => top-down rows
        self.biPlanes = 1
        self.biBitCount = 32


def _to_array(pixels, width, height):
    """stdlib fallback: bytes(BGR) -> list-of-lists rows of (b,g,r)."""
    rows = []
    for y in range(height):
        row = []
        base = y * width * 3
        for x in range(width):
            i = base + x * 3
            row.append((pixels[i], pixels[i + 1], pixels[i + 2]))
        rows.append(row)
    return rows


def _gray_block(frame, x0, y0, w, h):
    """stdlib fallback: grayscale values (0-255) for a frame block."""
    out = []
    for y in range(y0, min(y0 + h, len(frame))):
        row = frame[y]
        for x in range(x0, min(x0 + w, len(row))):
            b, g, r = row[x]
            out.append((b + g + r) / 3.0)
    return out


def _median(vals):
    """stdlib fallback median for a non-empty list of numbers."""
    s = sorted(vals)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


class AutoMoveSimulator:
    """Plays the user's moves on the chess site by simulating a real mouse drag.

    Instead of dispatching one instant drag, it builds the full interaction
    chain a human would produce: a mousedown on the piece, a stream of
    mousemove events along a curved path (with micro-movements, destination
    hesitations and variable speed), then a mouseup on the destination square.
    Thinking time and the pacing of every mouse event are user-adjustable.

    The path is described in board-relative units so the browser extension can
    scale it onto the actual board pixels it measures at runtime:
      t  = 0..1 progress from the source square to the destination square
      ox/oy = deviation from the straight source->destination line, in units
              of one board square (the extension multiplies by square size)
      d  = milliseconds to wait before dispatching this mouse event
    """

    def __init__(self):
        # Master switch (driven by the GUI toggle; never on by default).
        self.enabled = False
        # Thinking time before the piece is touched, in ms.  Most moves land
        # in [think_min_ms, think_max_ms]; complex positions occasionally take
        # longer (think_complex_min_ms..think_complex_max_ms).
        self.think_min_ms = 3000
        self.think_max_ms = 15000
        self.think_complex_min_ms = 20000
        self.think_complex_max_ms = 30000
        self.complex_chance = 0.10      # extra chance a position is 'complex'
        self.complex_threshold = 0.70   # complexity score that forces a long think
        self.complex_enabled = True     # rare long 'complex' thinks only when ticked
        # Opening guard: during the first OPENING_PLY plies of a game the think
        # time is capped at 5s and complex thinks never fire, no matter how the
        # sliders are set (humans move fast in book positions).
        self.opening_ply_limit = 20     # first 10 full moves
        self.opening_think_max_ms = 5000
        # Human error: probability (0..1) that Auto picks a deliberately-lower
        # "green" line instead of the headline best, so a game is never a
        # perfect engine repeat.  The lower line is still a good move — never
        # a blunder.
        # Kept only for compatibility with older saved settings.  Accuracy-band
        # selection is authoritative, so this value is never used to bypass it.
        self.human_error_chance = 0.0
        # Pacing of the mouse events themselves, in ms (default 50..200).
        self.event_delay_min_ms = 50
        self.event_delay_max_ms = 200
        # Motion character, all 0..1.
        self.curvature = 0.45       # how far the drag bows off the straight line
        self.micro_movement = 0.40  # small nervous jitter while moving
        self.hesitation = 0.50      # extra 'lining up' wobbles before the drop
        self.speed_variation = 0.60 # faster mid-flight, deliberate start/finish
        self.path_speed = 1.0       # global drag-speed multiplier (0.2x..6x)
        # Session state: a wandering mean-reverting "tempo" mood (a random
        # walk pulled toward 1.0) that drifts naturally instead of climbing
        # deterministically with move count.
        self._drag_count = 0
        self._moves_made = 0
        self._last_think_ms = None
        self._tempo = 1.0
        # Session-level (not drag-level) traits: the display frame rate that
        # quantizes event timing, a wandering mood with a per-session
        # attractor (so different sessions behave differently), and the
        # material-tracking used to react to captures.
        self._frame_ms = None
        self._mood_center = random.uniform(0.78, 1.30)
        self._mood_vol = random.uniform(0.06, 0.12)
        self._last_material = None
        self._recalc = 0.0
        # Player-specific hand bias: each session grabs and drops pieces at a
        # consistent spot inside the square (drawn once, never per drag), so
        # pickup/release scatter is modest and clustered — not uniform.
        self._pick_bias = (random.gauss(0, 0.055), random.gauss(0, 0.065))
        self._drop_bias = (random.gauss(0, 0.060), random.gauss(0, 0.070))
        # The mood tempo is advanced once per move (during the think) and
        # consumed by the drag that follows — never advanced twice.
        self._tempo_pending = None

    # ------------------------------------------------------------------
    # Thinking time
    # ------------------------------------------------------------------
    def _scan_board(self, fen):
        """Parse a FEN placement and return (material, checks, threats, total).

        material is white-minus-black in points; checks is how many pieces
        currently attack the enemy king; threats is how many (attacker,
        defender) attack pairs exist on the board.  Rays stop at the first
        blocker — pseudo-legal, exactly what a human scans for when sizing a
        position up.
        """
        try:
            grid = {}
            y = 7
            for row in fen.split()[0].split("/"):
                x = 0
                for ch in row:
                    if ch.isdigit():
                        x += int(ch)
                    else:
                        grid[(x, y)] = ch
                        x += 1
                y -= 1
            values = {"p": 1, "n": 3, "b": 3, "r": 5, "q": 9}
            white = sum(values.get(c.lower(), 0) for c in grid.values()
                        if c.isupper())
            black = sum(values.get(c.lower(), 0) for c in grid.values()
                        if c.islower())
            wset = {q for q, c in grid.items() if c.isupper()}
            bset = {q for q, c in grid.items() if c.islower()}
            kwhite = next(q for q, c in grid.items() if c == "K")
            kblack = next(q for q, c in grid.items() if c == "k")

            def attacks(pc, x, y, enemy_set, hit):
                c = pc.lower()
                if c == "p":
                    dy = 1 if pc.isupper() else -1
                    for dx in (-1, 1):
                        q = (x + dx, y + dy)
                        if q in enemy_set:
                            hit.append(q)
                elif c == "n":
                    for dx, dy in ((1, 2), (2, 1), (2, -1), (1, -2),
                                   (-1, -2), (-2, -1), (-2, 1), (-1, 2)):
                        q = (x + dx, y + dy)
                        if q in enemy_set:
                            hit.append(q)
                elif c in ("b", "q"):
                    for dx, dy in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
                        for s in range(1, 8):
                            q = (x + dx * s, y + dy * s)
                            if q in enemy_set:
                                hit.append(q)
                            if q in grid:
                                break
                if c in ("r", "q"):
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        for s in range(1, 8):
                            q = (x + dx * s, y + dy * s)
                            if q in enemy_set:
                                hit.append(q)
                            if q in grid:
                                break
                if c == "k":
                    for dx in (-1, 0, 1):
                        for dy in (-1, 0, 1):
                            q = (x + dx, y + dy)
                            if q in enemy_set:
                                hit.append(q)

            checks = threats = 0
            for (x, y), pc in grid.items():
                hit = []
                if pc.isupper():
                    attacks(pc, x, y, bset, hit)
                    if kblack in hit:
                        checks += 1
                    threats += sum(1 for q in hit if q != kblack)
                else:
                    attacks(pc, x, y, wset, hit)
                    if kwhite in hit:
                        checks += 1
                    threats += sum(1 for q in hit if q != kwhite)
            return white - black, checks, threats, len(grid)
        except Exception:
            return 0, 0, 0, 0

    def position_complexity(self, fen):
        """Cheap 0..1 complexity heuristic derived from the FEN placement.

        Piece density and material imbalance contribute, but genuine tactical
        texture (checks, attack pairs) dominates — a quiet full board stays
        fast while a sharp position with checks genuinely slows the player.
        """
        try:
            mat, checks, threats, total = self._scan_board(fen)
            score = 0.06
            score += max(0.0, min(0.16, (total - 6) / 22.0 * 0.16))
            score += max(0.0, min(0.46, abs(mat) / 10.0 * 0.46))
            score += max(0.0, min(0.30, checks * 0.15))
            score += max(0.0, min(0.24, threats * 0.045))
            return max(0.0, min(1.0, score))
        except Exception:
            return 0.5

    def _opening_fade(self, fen):
        """Continuous 0..1 factor for how 'opening-like' the position is.

        Fades smoothly from 1 at the first ply to 0 after ~24 plies — a
        continuous blend instead of a regime switch a detector could time.
        """
        if not fen or not isinstance(fen, str):
            return 0.0
        try:
            parts = fen.split()
            fullmove = int(parts[5])
            ply = (fullmove - 1) * 2 + (1 if parts[1].lower() == "b" else 0)
            return max(0.0, 1.0 - ply / 24.0) ** 1.5
        except Exception:
            return 0.0

    def _session_tempo(self):
        """Wandering mean-reverting mood shared by thinking time and drags.

        The attractor and volatility are drawn per session (not fixed), and
        the bounds are a soft tanh squash rather than hard clamps, so a
        detector sees drifting, session-dependent autocorrelation — not a
        fixed bounded walk it can fit."""
        c = getattr(self, "_mood_center", 1.0)
        v = getattr(self, "_mood_vol", 0.09)
        s = getattr(self, "_tempo", 1.0)
        s += 0.12 * (c - s) + random.gauss(0, v)
        s = c + 0.55 * math.tanh((s - c) / 0.55)
        self._tempo = s
        return s

    def thinking_time_ms(self, fen=None):
        """Choose a think time within the selected normal or complex bounds."""
        if not self.enabled:
            return 0
        tempo = self._session_tempo()
        self._tempo_pending = tempo
        of = self._opening_fade(fen)
        n = getattr(self, "_moves_made", 0) + 1
        self._moves_made = n

        # Material tracking: right after a capture the player recalculates;
        # the effect decays over the following moves instead of one shot.
        self._recalc = getattr(self, "_recalc", 0.0) * 0.4
        mat = None
        if isinstance(fen, str):
            mat = self._scan_board(fen)[0]
            prev = getattr(self, "_last_material", None)
            if prev is not None and abs(mat - prev) > 0.5:
                self._recalc = 1.0
            self._last_material = mat

        lo = max(0, int(getattr(self, "think_min_ms", 3000)))
        hi = max(lo, int(getattr(self, "think_max_ms", 15000)))
        base_mean = math.sqrt(lo * hi)
        complex_move = False
        if self.complex_enabled:
            if fen:
                cl = self.position_complexity(fen)
            else:
                cl = random.random()
            cl = max(0.0, min(1.0, cl))
            cl += 0.10 * getattr(self, "_recalc", 0.0)
            complex_move = (of <= 0.0 and (cl >= self.complex_threshold or random.random() < self.complex_chance))
            if complex_move:
                lo = max(0, int(self.think_complex_min_ms))
                hi = max(lo, int(self.think_complex_max_ms))
                base_mean = math.sqrt(lo * hi)
            # Continuous ramp: the sharper the position, the longer the
            # think — no discrete "normal"/"complex" buckets.
            base_mean *= 1.0 + 1.2 * (cl ** 2.0)
            base_mean = min(base_mean, float(hi) * 1.25)
        # The opening plays faster, fading smoothly into the midgame — no
        # regime boundary for a detector to time.
        base_mean *= 1.0 - 0.58 * of
        sigma = random.uniform(0.36, 0.44) + 0.22 * of

        ms = random.lognormvariate(math.log(max(500.0, base_mean)), sigma)
        # Mood-driven adjustments: the probabilities themselves depend on the
        # wandering tempo, so no fixed ratios show up over a session.
        r = random.random()
        if r < 0.10 * (1.35 - tempo):
            ms *= random.uniform(0.45, 0.70)     # quick, "I know this one"
        elif r < 0.10 * (1.35 - tempo) + 0.05 * tempo:
            ms *= random.uniform(1.6, 2.4)       # long second-guessing pause
        ms *= tempo
        # Gentle rhythm coupling with the previous move (humans keep a tempo,
        # but never a fixed one).
        prev = getattr(self, "_last_think_ms", None)
        if prev is not None and random.random() < 0.30:
            ms = 0.45 * min(prev, 40000) + 0.55 * ms
        ms = max(lo, min(hi, ms))
        ms = int(round(ms))
        self._last_think_ms = ms
        return ms

    # ------------------------------------------------------------------
    # Human-like drag path: one continuous motor model (see generate_plan)
    # ------------------------------------------------------------------
    def generate_plan(self, uci):
        """Return a list of {t, perp, ox, oy, d} steps for one drag.

        Drags come from one continuous motor model: a progress curve drawn
        from two structurally different families (a generalized power curve
        or a beta-density velocity profile, sometimes with a corrective
        second pulse), a harmonic bow, AR(1)-correlated 2D micro noise, and a
        Fitts-like duration coupling event timing to real distance.  The
        pickup and release points are scattered 2D offsets (never exact
        square centers), landings rest on their own offset instead of zero,
        and event delays are quantized to a per-session display frame rate
        with coalesced bursts and occasional render stalls.  Overshoots,
        wobbles, pauses and interaction-boundary hover/drift all emerge from
        continuous per-drag parameters rather than fixed coin-flip
        probabilities.

        t: progress along the move line (0..1, occasionally slightly >1)
        perp / ox / oy: perpendicular offset in board-square units
        sa / ea: pickup/release offsets along the move line (square units)
        ta: per-event micro-noise along the move line (square units)
        d: milliseconds to wait before dispatching this event
        pre: hover events before the piece is gripped
        up: the event that releases the piece (pointerup)
        """
        if not isinstance(uci, str) or len(uci) < 4:
            return None
        from_sq, to_sq = uci[0:2], uci[2:4]
        try:
            df = ord(to_sq[0]) - ord(from_sq[0])
            dr = int(to_sq[1]) - int(from_sq[1])
        except (ValueError, IndexError):
            return None
        dist = math.hypot(df, dr) or 1.0
        ux, uy = df / dist, dr / dist
        pxu, pyu = -uy, ux

        n_drag = getattr(self, "_drag_count", 0) + 1
        self._drag_count = n_drag
        # One tempo per move: the think advances it, this drag consumes it.
        # A drag without a preceding think advances it itself.
        tempo = getattr(self, "_tempo_pending", None)
        if tempo is None:
            tempo = self._session_tempo()
        else:
            self._tempo_pending = None

        curv = max(0.0, min(1.0, float(getattr(self, "curvature", 0.45) or 0.0)))
        micro = max(0.0, min(1.0, float(getattr(self, "micro_movement", 0.40) or 0.0)))
        hesit = max(0.0, min(1.0, float(getattr(self, "hesitation", 0.50) or 0.0)))
        spvar = max(0.0, min(1.0, float(getattr(self, "speed_variation", 0.60) or 0.0)))

        # ---- pickup / release geometry ---------------------------------
        # The hand grabs anywhere in the square, not just its middle: a
        # mixture of a centered cluster on this session's own bias and a
        # broad reach toward the square's edges.  Releases stay comfortably
        # inside the legal destination square.
        pb_a, pb_p = getattr(self, "_pick_bias", (0.0, 0.0))
        db_a, db_p = getattr(self, "_drop_bias", (0.0, 0.0))
        if random.random() < 0.35:
            sa_pick = max(-0.48, min(0.48, random.uniform(-0.33, 0.33) + pb_a))
            sp_pick = max(-0.48, min(0.48, random.uniform(-0.33, 0.33) + pb_p))
        else:
            sa_pick = random.gauss(-0.025 + pb_a, 0.14)
            sp_pick = random.gauss(0.0 + pb_p, 0.15)
        if random.random() < 0.45:
            ea_drop = max(-0.42, min(0.42, random.uniform(-0.32, 0.32) + db_a))
            ep_drop = max(-0.42, min(0.42, random.uniform(-0.32, 0.32) + db_p))
        else:
            ea_drop = random.gauss(0.035 + db_a, 0.15)
            ep_drop = random.gauss(0.0 + db_p, 0.16)
        # Real endpoint error persists: the release sits a small random
        # distance past/short of the destination center (never exactly on
        # it), but always well inside the legal destination square.
        t_resid = max(-0.07, min(0.07, random.gauss(0, 0.020)))

        # ---- progress family --------------------------------------------
        # Two structurally different velocity shapes share the remaining
        # machinery, so normalized velocity/jerk/spectral features never all
        # come from one mathematical family.
        if random.random() < 0.35:
            # Beta-density velocity: slow start, long tail — a relaxed grab.
            # alpha stays near 1 so the start never crawls.
            alpha = random.uniform(1.05, 1.6)
            beta_v = random.uniform(2.2, 4.6)
            nv = 320
            vel = [(k / nv) ** (alpha - 1.0)
                   * (1.0 - k / nv) ** (beta_v - 1.0)
                   for k in range(nv + 1)]
            if random.random() < 0.30:
                # A corrective second pulse: the classic double-peaked
                # velocity of a ballistic move plus a small correction.
                c2 = random.uniform(0.25, 0.65)
                t2 = random.uniform(0.55, 0.85)
                w2 = random.uniform(0.05, 0.13)
                for k in range(nv + 1):
                    x = k / nv
                    vel[k] += c2 * math.exp(-((x - t2) / w2) ** 2)
            cum = []
            acc = 0.0
            for v in vel:
                acc += v
                cum.append(acc)
            ctot = cum[-1] or 1.0
            def progress(tau):
                return cum[min(int(tau * nv), nv)] / ctot
        else:
            # Generalized power curve spanning slow/fast starts and endings.
            # `a` is capped so no drag ever crawls off the line: a real hand
            # is moving meaningfully within its first few events.
            sig_ab = 0.18 + 0.22 * spvar
            a = min(1.35, random.lognormvariate(0.0, sig_ab))
            b = random.lognormvariate(0.20, 0.28)
            def progress(tau):
                ta = tau ** a
                tb = (1.0 - tau) ** b
                return ta / (ta + tb) if (ta + tb) > 1e-12 else 0.0

        # ---- bow (shared): harmonic bridge, continuous amplitudes -------
        amp = (0.05 + 0.32 * curv) * random.lognormvariate(0.0, 0.55)
        if random.random() < 0.5:
            amp = -amp
        nh = random.randint(1, 3)
        phase = [random.uniform(0, 6.2832) for _ in range(nh)]
        ampk = [amp * random.uniform(0.55, 1.05) * (0.72 ** h)
                for h in range(nh)]
        def bridge(t):
            p0 = sum(ampk[h] * math.sin(phase[h]) for h in range(nh))
            p1 = sum(ampk[h] * math.sin((h + 1) * math.pi + phase[h])
                     for h in range(nh))
            v = sum(ampk[h] * math.sin((h + 1) * math.pi * t + phase[h])
                    for h in range(nh))
            return v - (p0 * (1.0 - t) + p1 * t)

        # ---- duration & cadence ----------------------------------------
        # Fitts-like but brisk: short moves are fast, long moves flick.
        dur = 10.0 ** random.gauss(math.log10(0.12 + 0.13 * dist ** 0.6), 0.18)
        dur *= 1.0 + 0.22 * (1.0 - tempo)
        dur = max(0.10, min(1.4, dur))
        # Dense motor-time sampling (~23 ms median), so even fast drags carry
        # a realistic number of events.  Event count follows elapsed time
        # and cadence; the ceiling only guards against pathological tails.
        samp = random.lognormvariate(math.log(0.023), 0.22)
        K = int(round(dur / samp))
        K = max(8, min(200, K))
        dt = dur / K

        # ---- AR(1) correlated 2D micro noise ---------------------------
        rho = random.uniform(0.55, 0.92)
        sig_p = (0.010 + 0.045 * micro) * random.uniform(0.6, 1.6)
        sig_t = (0.0015 + 0.005 * micro) * random.uniform(0.6, 1.6)
        sig_a = sig_p * 0.45
        # Arrival dynamics: mostly clean; sloppiness is continuous.  The
        # terminal-speed estimate is inflated by the dense sampling, so the
        # threshold lives higher than naive physical intuition suggests.
        v_thresh = random.uniform(4.5, 7.5)       # sq/s below which no carry
        ov_k = random.uniform(0.015, 0.045)       # s per excess sq/s
        stop_quality = random.uniform(0.0, 1.0)   # how cleanly the hand stops
        careful = random.betavariate(1.0, 6.0)    # mostly low (clean arrivals)
        indecision = random.betavariate(1.5, 6.5) # mostly very low

        pts = []          # (t, perp, along, pause_extra_seconds)
        taus = []
        pn = tn = an = 0.0
        pause_gain = 0.0
        if indecision > 0.38:
            tp = random.uniform(0.18, 0.85)
            pw = random.uniform(0.05, 0.14)
            pause_gain = (indecision - 0.38) * random.uniform(2.0, 4.5)
        for i in range(K):
            tau = i / (K - 1)
            taus.append(tau)
            t = progress(tau)
            env = min(1.0, 4.0 * t * (1.0 - t))
            pn = rho * pn + random.gauss(0, sig_p * env) \
                * (1.0 - rho * rho) ** 0.5
            tn = rho * tn + random.gauss(0, sig_t * env) \
                * (1.0 - rho * rho) ** 0.5
            an = rho * an + random.gauss(0, sig_a * env) \
                * (1.0 - rho * rho) ** 0.5
            pause = 0.0
            if pause_gain > 0.0:
                pause = pause_gain * math.exp(-((tau - tp) / pw) ** 2)
            pts.append((t + tn, bridge(t) + pn, an, pause))
        # Anchor the landing: progress ends at 1 + t_resid (the accumulated
        # noise returns to the release offset, but the small endpoint error
        # persists — the hand never lands mathematically clean).  The noise
        # itself is still returned to the bow baseline; only the endpoint
        # keeps its human error.
        tn_final = pts[-1][0] - (1.0 + t_resid)
        pn_final = pts[-1][1] - bridge(1.0)
        an_final = pts[-1][2]
        if abs(tn_final) > 1e-6 or abs(pn_final) > 1e-6 or abs(an_final) > 1e-6:
            for j in range(K):
                t, p, a, pause = pts[j]
                pts[j] = (t - tn_final * taus[j],
                          p - pn_final * taus[j],
                          a - an_final * taus[j], pause)

        # Landing wobble: only on "careful" drags, zone start continuous.
        if careful > 0.25:
            z0 = random.uniform(0.78, 0.97)
            freq = random.uniform(2.5, 6.5)
            wph = random.uniform(0, 6.2832)
            wamp = (careful - 0.25) * 0.10 * (0.4 + 0.8 * hesit) \
                * random.uniform(0.5, 1.4)
            for j in range(len(pts)):
                t, p, a, pause = pts[j]
                if t >= z0:
                    z = min(1.0, (t - z0) / (1.0 - z0))
                    p += wamp * (1.0 - z) * math.sin(z * math.pi * freq + wph)
                pts[j] = (t, p, a, pause)

        # Overshoot emerges from the terminal speed of the eased curve.  The
        # carry past the target is a short arc (apex + return), not a single
        # jump, and it stays small — the consumers clamp well beyond it, so
        # no distribution edge forms.
        t_prev = pts[-2][0]
        v_end = (1.0 - t_prev) * dist / dt * (1.0 - 0.65 * stop_quality)
        if v_end > v_thresh:
            ov = min(0.075, (v_end - v_thresh) * ov_k)
            for frac in (1.0, 0.62, 0.28):
                pts.append((1.0 + t_resid + ov * frac,
                            random.gauss(0, 0.015 * frac),
                            random.gauss(0, 0.012 * frac), 0.0))
        # Release point: the (1+t_resid, 0, 0) anchor becomes the drop offset
        # once the pickup->release baseline is applied below.
        pts.append((1.0 + t_resid, 0.0, 0.0, 0.0))

        # ---- event cadence: frame-quantized, drifting, with bursts -------
        # The display frame rate is a session property (60/90/120 Hz panel),
        # not a per-drag draw, so quantization is consistent across a game.
        frame = getattr(self, "_frame_ms", None)
        if frame is None:
            frame = random.uniform(8.3, 16.7)
            self._frame_ms = frame
        steps = []
        drift = 1.0
        n = len(pts)
        # Terminal events have their own timing character: the apex of an
        # overshoot is a quick flick, the correction returns are irregular
        # jabs, and the release happens after a brief settle — never another
        # ordinary cadence interval.
        arc_start = K if (v_end > v_thresh) else None
        for i in range(n):
            t, perp, a, pause = pts[i]
            if i == 0:
                # The grip itself takes a wide, human 90-480 ms.
                d = int(random.uniform(90, 480) * random.lognormvariate(0.0, 0.28) * (1 + random.uniform(-0.07, 0.07)))
            elif arc_start is not None and i == arc_start:
                d = int(dt * 1000.0 * random.uniform(0.30, 0.60) * (1 + random.uniform(-0.07, 0.07)))
            elif arc_start is not None and i <= arc_start + 2:
                d = int(random.uniform(15, 50) * random.lognormvariate(0.0, 0.2) * (1 + random.uniform(-0.07, 0.07)))
            else:
                # Cadence drifts across the drag (muscle fatigue, attention).
                drift += 0.08 * (1.0 - drift) + random.gauss(0, 0.05)
                drift = max(0.7, min(1.4, drift))
                base_ms = dt * drift * random.lognormvariate(0.0, 0.10) * 1000.0
                dq = max(1, int(round(base_ms / frame))) * frame
                if random.random() < 0.10:
                    # A coalesced burst: two events flushed in one frame.
                    dq = random.uniform(0.0, frame * 0.35)
                if random.random() < 0.03:
                    # A render/GC stall on this event.
                    dq *= random.uniform(1.6, 3.0)
                elif random.random() < 0.04:
                    # The browser dropped a frame: one full frame gap.
                    dq += frame
                d = int(dq + pause * 1000.0 * (1 + random.uniform(-0.07, 0.07)))
            # The perpendicular baseline interpolates pickup -> release
            # offsets, so neither end is ever an exact square center.  The
            # along baseline lives in sa/ea (applied by the consumers); ta
            # carries only the raw along-noise, so nothing is doubled.
            t = max(0.0, min(1.15, t))
            perp = sp_pick * (1.0 - t) + ep_drop * t + perp
            perp = 0.6 * math.tanh(perp / 0.6)      # soft clamp, no hard edge
            rt = round(t, 6)
            rp = round(perp, 6)
            if steps and abs(steps[-1]["t"] - rt) < 1e-5 \
                    and abs(steps[-1]["perp"] - rp) < 1e-4:
                continue     # no progress stacking at the ends
            # ox/oy are perturbed independently around perp so the two
            # consumers never see a perfectly correlated triple.
            ox = perp * (1.0 + random.gauss(0, 0.05))
            oy = perp * (1.0 + random.gauss(0, 0.05))
            steps.append({
                "t": rt,
                "perp": rp,
                "ox": round(ox, 6),
                "oy": round(oy, 6),
                "sa": round(sa_pick, 5),
                "ea": round(ea_drop, 5),
                "ta": round(a, 6),
                "d": int(round(max(1, d))),
            })

        # ---- interaction boundaries -------------------------------------
        # Hover micro-motion at the pickup point before the grip, and a
        # short drift after the release — never "silence -> drag -> silence".
        has_pre = random.random() < 0.75
        if has_pre:
            kpre = random.randint(1, 3)
            # The hand slides onto the piece from a bit away instead of
            # appearing exactly at the grab point: the first hover event
            # starts off the pickup point and drifts onto it.
            ap_a = random.uniform(-0.28, 0.28)
            ap_p = random.uniform(-0.28, 0.28)
            pre = []
            for k in range(kpre):
                f = 0.0 if kpre == 1 else 1.0 - k / (kpre - 1.0)
                jx = random.gauss(0, 0.004)
                jy = random.gauss(0, 0.004)
                pp = 0.6 * math.tanh((sp_pick + ap_p * f + jx) / 0.6)
                pre.append({
                    "t": 0.0,
                    "perp": round(pp, 6),
                    "ox": round(pp * (1.0 + random.gauss(0, 0.05)), 6),
                    "oy": round((sp_pick + ap_p * f + jy)
                                * (1.0 + random.gauss(0, 0.05)), 6),
                    "sa": round(sa_pick, 5),
                    "ea": round(ea_drop, 5),
                    "ta": round(ap_a * f + jy, 6),
                    "d": 0,
                    "pre": True,
                })
            pre[0]["d"] = int(random.uniform(60, 320) * random.lognormvariate(0.0, 0.25) * (1 + random.uniform(-0.07, 0.07)))
            for k in range(1, kpre):
                pre[k]["d"] = int(random.uniform(35, 150) * random.lognormvariate(0.0, 0.25) * (1 + random.uniform(-0.07, 0.07)))
            # The grip itself: replaces the first drag step's cadence delay
            # with a wide human grab latency.
            steps[0]["d"] = int(random.uniform(50, 240) * random.lognormvariate(0.0, 0.25) * (1 + random.uniform(-0.07, 0.07)))
            steps = pre + steps
            grip_idx = len(pre)
        else:
            steps[0]["d"] = int(random.uniform(90, 480) * random.lognormvariate(0.0, 0.28) * (1 + random.uniform(-0.07, 0.07)))
            grip_idx = 0
        # mousedown-to-first-motion: the hand settles a moment after the
        # press before the piece starts moving.
        if grip_idx + 1 < len(steps):
            steps[grip_idx + 1]["d"] = int(random.uniform(40, 170) * random.lognormvariate(0.0, 0.25) * (1 + random.uniform(-0.07, 0.07)))

        steps[-1]["up"] = True
        # A brief settle before release: the hand holds still a moment
        # (whether the release event survived dedupe or not).
        steps[-1]["d"] = int(steps[-1]["d"] * random.uniform(1.6, 3.5) * (1 + random.uniform(-0.07, 0.07)))
        if random.random() < 0.70:
            npost = random.randint(1, 2)
            bx = random.gauss(0.04, 0.10)
            by = random.gauss(0.0, 0.09)
            for k in range(npost):
                f = (k + 1) / (npost + 1.0)
                pp = 0.6 * math.tanh((ep_drop + bx * f) / 0.6)
                steps.append({
                    "t": round(1.0 + t_resid + 0.02 * (k + 1), 6),
                    "perp": round(pp, 6),
                    "ox": round(pp * (1.0 + random.gauss(0, 0.05)), 6),
                    "oy": round(pp * (1.0 + random.gauss(0, 0.05)), 6),
                    "sa": round(sa_pick, 5),
                    "ea": round(ea_drop, 5),
                    "ta": round(by * f, 6),
                    "d": int(random.uniform(40, 120)
                             * random.lognormvariate(0.0, 0.2)),
                })
        minimum = max(0, int(self.event_delay_min_ms))
        maximum = max(minimum, int(self.event_delay_max_ms))
        for step in steps:
            if not minimum <= step['d'] <= maximum:
                step['d'] = int(round(random.uniform(minimum, maximum)))
        return steps


def plan_screen_points(plan, start, end, square_width, square_height):
    """Map a board-relative plan to screen points, preserving event boundaries."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    distance = math.hypot(dx, dy) or 1.0
    ux, uy = dx / distance, dy / distance
    for step in plan:
        t = max(0.0, min(1.15, float(step.get("t", 0))))
        along = (float(step.get("sa", 0)) * (1 - t)
                 + float(step.get("ea", 0)) * t + float(step.get("ta", 0)))
        x = start[0] + dx * t + along * square_width * ux - float(step.get("ox", 0)) * square_width * uy
        y = start[1] + dy * t + along * square_width * uy + float(step.get("oy", 0)) * square_height * ux
        yield x, y, max(0.0, float(step.get("d", 0))), bool(step.get("pre")), bool(step.get("up"))


def sigmadrift_generate_plan(x0, y0, x1, y1, speed_mult=1.0, target_width=24.0, seed=None):
    """
    Generate point-to-point mouse trajectories using SigmaDrift motor control theory:
    - Sigma-lognormal velocity primitives (Plamondon's Kinematic Theory)
    - Two-phase surge architecture (ballistic primary stroke + corrective sub-movements)
    - Ornstein-Uhlenbeck lateral hand drift
    - Harris-Wolpert signal-dependent noise (SDN)
    - Speed-modulated physiological tremor (8-12 Hz)
    - Gamma-distributed inter-sample timing (USB mouse hardware polling)
    """
    rng = random.Random(seed) if seed is not None else random

    dx = float(x1 - x0)
    dy = float(y1 - y0)
    distance = math.hypot(dx, dy)
    if distance < 1.0:
        return [{"x": x0, "y": y0, "d": 0}, {"x": x1, "y": y1, "d": 40}]

    direction = math.atan2(dy, dx)
    tx, ty = dx / distance, dy / distance
    nx, ny = -ty, tx

    # Fitts' Law Index of Difficulty (ID)
    id_val = math.log2(distance / max(1.0, float(target_width)) + 1.0)
    # Movement time in ms (scaled by user path_speed multiplier)
    base_mt = (50.0 + 150.0 * id_val) * math.exp(rng.gauss(0.0, 0.08))
    mt = base_mt / math.sqrt(max(0.1, float(speed_mult)))

    # Two-phase surge architecture (undershoot vs overshoot)
    overshoot = rng.random() < 0.15
    reach = rng.uniform(1.02, 1.08) if overshoot else rng.uniform(0.92, 0.97)
    primary_D = distance * reach
    primary_sigma = rng.uniform(0.18, 0.28)

    # Mode = exp(mu - sigma^2) -> peak_t
    peak_t = mt * rng.uniform(0.32, 0.38)
    primary_mu = math.log(peak_t) + primary_sigma * primary_sigma

    # Corrective sub-movements
    corrections = []
    remaining = distance - primary_D
    if abs(remaining) > 0.5:
        dir_val = 1.0 if remaining > 0.0 else -1.0
        cD = abs(remaining) * rng.uniform(0.88, 1.02)
        cS = rng.uniform(0.12, 0.20)
        cPeak = mt * rng.uniform(0.12, 0.18)
        cT0 = mt * rng.uniform(0.55, 0.68)
        corrections.append({
            "D": cD, "t0": cT0,
            "mu": math.log(cPeak) + cS * cS, "sigma": cS,
            "dir_x": tx * dir_val, "dir_y": ty * dir_val
        })

        left = remaining - cD * dir_val
        if abs(left) > 0.3 and rng.random() < 0.25:
            d2_val = 1.0 if left > 0.0 else -1.0
            cD2 = abs(left) * rng.uniform(0.85, 1.05)
            cS2 = rng.uniform(0.10, 0.16)
            cP2 = mt * rng.uniform(0.08, 0.12)
            cT02 = mt * rng.uniform(0.78, 0.88)
            corrections.append({
                "D": cD2, "t0": cT02,
                "mu": math.log(cP2) + cS2 * cS2, "sigma": cS2,
                "dir_x": tx * d2_val, "dir_y": ty * d2_val
            })

    # Curvature profile (wrist/forearm geometry)
    sa = abs(math.sin(direction))
    ca = abs(math.cos(direction))
    dir_factor = 0.5 + 0.8 * sa - 0.15 * ca
    curv_amp = distance * 0.025 * dir_factor * rng.gauss(0.0, 1.0)

    # Physiological tremor & Ornstein-Uhlenbeck drift parameters
    tremor_freq = rng.uniform(8.0, 12.0)
    tremor_amp = rng.uniform(0.15, 0.55)
    tph_x = rng.uniform(0.0, 2.0 * math.pi)
    tph_y = rng.uniform(0.0, 2.0 * math.pi)
    ou_x, ou_y = 0.0, 0.0

    total_t = mt * 1.15
    sample_dt_mean = 7.8
    gamma_shape = 3.5
    g_scale = sample_dt_mean / gamma_shape

    # Gamma-distributed sample timestamps (natural hardware USB polling)
    times = [0.0]
    t_curr = 0.0
    while t_curr < total_t:
        dt = min(25.0, max(2.0, rng.gammavariate(gamma_shape, g_scale)))
        t_curr += dt
        if t_curr <= total_t + 15.0:
            times.append(t_curr)

    # Lognormal CDF & PDF helper functions
    def normal_cdf(x):
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    def lognormal_cdf(t, t0, mu, sigma):
        if t <= t0: return 0.0
        return normal_cdf((math.log(t - t0) - mu) / sigma)

    def lognormal_pdf(t, t0, mu, sigma):
        if t <= t0: return 0.0
        dt_val = t - t0
        z = (math.log(dt_val) - mu) / sigma
        return (1.0 / (sigma * math.sqrt(2.0 * math.pi) * dt_val)) * math.exp(-0.5 * z * z)

    def curvature_profile(s):
        if s <= 0.0 or s >= 1.0: return 0.0
        v = s * s * (1.0 - s) * (1.0 - s) * (1.0 - s)
        norm = 0.4 * 0.4 * 0.6 * 0.6 * 0.6
        return v / norm

    result = []
    for i, t in enumerate(times):
        dt_ms = (t - times[i - 1]) if i > 0 else sample_dt_mean
        dt_s = dt_ms / 1000.0

        s = lognormal_cdf(t, 0.0, primary_mu, primary_sigma)
        bx = x0 + tx * primary_D * s
        by = y0 + ty * primary_D * s

        curv_p = curvature_profile(s)
        bx += nx * curv_amp * curv_p
        by += ny * curv_amp * curv_p

        for c in corrections:
            cs = lognormal_cdf(t, c["t0"], c["mu"], c["sigma"])
            bx += c["dir_x"] * c["D"] * cs
            by += c["dir_y"] * c["D"] * cs

        speed = primary_D * lognormal_pdf(t, 0.0, primary_mu, primary_sigma)
        for c in corrections:
            speed += c["D"] * lognormal_pdf(t, c["t0"], c["mu"], c["sigma"])

        # Ornstein-Uhlenbeck process step
        ou_x += -3.5 * ou_x * dt_s + 1.2 * math.sqrt(dt_s) * rng.gauss(0.0, 1.0)
        ou_y += -3.5 * ou_y * dt_s + 1.2 * math.sqrt(dt_s) * rng.gauss(0.0, 1.0)

        # Speed-modulated physiological tremor
        t_s = t / 1000.0
        trem_mod = 1.0 / (1.0 + speed * 0.3)
        tr_x = tremor_amp * trem_mod * math.sin(2.0 * math.pi * tremor_freq * t_s + tph_x)
        tr_y = tremor_amp * trem_mod * math.sin(2.0 * math.pi * tremor_freq * t_s + tph_y)

        # Harris-Wolpert signal-dependent noise (SDN)
        sdn_x = 0.04 * speed * rng.gauss(0.0, 1.0)
        sdn_y = 0.04 * speed * rng.gauss(0.0, 1.0)

        px = bx + ou_x + tr_x + sdn_x
        py = by + ou_y + tr_y + sdn_y
        result.append({"x": px, "y": py, "d": dt_ms})

    # Final endpoint anchor
    result[-1]["x"] = x1
    result[-1]["y"] = y1
    return result
# ============================================================================
# PHASE 3 — BOOKS & TABLEBASES
# ============================================================================
