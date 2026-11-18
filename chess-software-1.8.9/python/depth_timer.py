"""Session-only, thread-safe primary-engine search timings."""
from collections import deque
import threading
import time


class DepthTimer:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.RLock()
        self.enabled = False
        self.serial = 0
        self.reset()

    def reset(self):
        with self.lock:
            self.active = None
            self.rows = deque(maxlen=50)
            self.count = self.total = self.incomplete = self.cached = 0
            self.fastest = self.slowest = None

    def toggle(self):
        with self.lock:
            self.enabled = not self.enabled
            self.active = None
            return self.enabled

    def begin(self, fen, target, engine, candidates):
        with self.lock:
            self.active = None
            if not self.enabled:
                return None
            self.serial += 1
            self.active = dict(ticket=self.serial, fen=fen, target=target,
                               engine=engine, candidates=candidates, start=self.clock())
            return self.serial

    def finish(self, ticket, fen, depth, ready, no_moves=False):
        with self.lock:
            current = self.active
            if not self.enabled or not current or ticket != current['ticket'] or fen != current['fen']:
                return
            self.active = None
            elapsed = max(0.0, self.clock()-current['start'])
            success = bool(ready and depth >= current['target'] and not no_moves)
            status = 'Ready' if success else ('No legal moves' if no_moves else 'Incomplete')
            self.rows.append(dict(current, elapsed=elapsed, depth=depth, status=status))
            if success:
                self.count += 1
                self.total += elapsed
                self.fastest = elapsed if self.fastest is None else min(self.fastest, elapsed)
                self.slowest = elapsed if self.slowest is None else max(self.slowest, elapsed)
            elif not no_moves:
                self.incomplete += 1

    def cache_hit(self, fen, depth):
        with self.lock:
            self.active = None
            if self.enabled:
                self.cached += 1
                self.rows.append(dict(fen=fen, target=depth, depth=depth,
                                      status='Cache hit', engine='', candidates=0, elapsed=None))

    def snapshot(self):
        with self.lock:
            active = dict(self.active) if self.active else None
            if active:
                active['elapsed'] = max(0.0, self.clock()-active['start'])
            return dict(enabled=self.enabled, active=active, rows=list(self.rows), count=self.count,
                        average=self.total/self.count if self.count else None,
                        fastest=self.fastest, slowest=self.slowest,
                        incomplete=self.incomplete, cached=self.cached)
