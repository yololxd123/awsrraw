"""Serialized UCI lifecycle: negotiate, stop/acknowledge, configure, ready, search.

No GUI imports. Every process gets its own session and latest pending request.
"""
import re
import threading
import time


class UciSession:
    def __init__(self, process):
        self.process = process
        self.lock = threading.RLock()
        self.options = {}
        self.applied = {}
        self.rejected = {}
        self.pending = None
        self.prepared = None
        self.active = None
        self.discard_active = False
        self.state = 'uci'
        self.since = time.monotonic()
        self.closed = False
        self._write('uci')

    def _state(self, value):
        self.state = value
        self.since = time.monotonic()

    def _write(self, command):
        if not self.closed:
            self.process.stdin.write(command + '\n')
            self.process.stdin.flush()

    def close(self):
        with self.lock:
            self.closed = True
            self.pending = None

    def submit(self, signature, options, start):
        with self.lock:
            if self.closed:
                return
            if self.state == 'search' and self.active and self.active[0] == signature and not self.discard_active:
                return
            self.pending = (signature, dict(options), start)
            if self.state == 'search':
                self.discard_active = True
                self._state('stopping')
                self._write('stop')
            elif self.state == 'idle':
                self._prepare()

    def cancel(self):
        with self.lock:
            self.pending = None
            self.discard_active = True
            if self.state == 'search':
                self._state('stopping')
                self._write('stop')

    def normalize(self, name, value):
        option = self.options.get(name.lower())
        if not option:
            return None
        kind = option['type']
        text = str(value)
        if any(c in text for c in '\r\n\0'):
            return None
        try:
            if kind == 'spin':
                value = int(value)
                value = max(option.get('min', value), min(option.get('max', value), value))
                return str(value)
            if kind == 'check':
                return text.lower() if text.lower() in ('true', 'false') else None
            if kind == 'combo':
                return text if text in option.get('vars', []) else None
            if kind == 'string':
                return text
        except (ValueError, TypeError, OverflowError):
            pass
        return None

    def _prepare(self):
        if not self.pending or self.closed:
            return
        self.prepared = self.pending
        self.rejected = {}
        for name, requested in self.prepared[1].items():
            value = self.normalize(name, requested)
            if value is None:
                self.rejected[name] = requested
                continue
            if self.applied.get(name.lower()) != value:
                actual_name = self.options[name.lower()]['name']
                self._write(f'setoption name {actual_name} value {value}')
                self.applied[name.lower()] = value
        self._state('ready')
        self._write('isready')

    def handle_line(self, line):
        with self.lock:
            if self.closed:
                return
            line = line.strip()
            match = re.match(r'option name (.+?) type (spin|check|combo|string|button)(?:\s+(.*))?$', line)
            if match:
                name, kind, tail = match.groups()
                option = {'name': name, 'type': kind}
                for key in ('min', 'max'):
                    m = re.search(r'\b' + key + r' (-?\d+)', tail or '')
                    if m:
                        option[key] = int(m.group(1))
                option['vars'] = re.findall(r'\bvar (.*?)(?= var |$)', tail or '')
                self.options[name.lower()] = option
            elif line == 'uciok' and self.state == 'uci':
                self._state('idle')
                self._prepare()
            elif line == 'readyok' and self.state == 'ready':
                self._state('idle')
                if self.pending is self.prepared:
                    request = self.pending
                    self.pending = None
                    self.active = request
                    self.discard_active = False
                    self._state('search')
                    if request[2](dict(self.applied)) is False:
                        self.active = None
                        self._state('idle')
                        self._prepare()
                else:
                    self._prepare()
            elif line.startswith('bestmove ') and self.state in ('search', 'stopping'):
                self.active = None
                self._state('idle')
                self._prepare()

    def timed_out(self):
        limit = {'uci': 20, 'ready': 20, 'stopping': 5}.get(self.state)
        return not self.closed and limit is not None and time.monotonic() - self.since > limit
