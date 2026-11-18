"""One background writer for immutable JSON settings snapshots."""
import json
import logging
import os
import tempfile
import threading


class SettingsWriter:
    def __init__(self, path):
        self.path = os.fspath(path)
        self._condition = threading.Condition()
        self._pending = None
        self._writing = False
        self._closed = False
        self._last = None
        self._error = None
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="settings-writer")
        self._thread.start()

    def submit(self, data):
        snapshot = json.dumps(data, sort_keys=True)
        with self._condition:
            if self._closed:
                raise RuntimeError("settings writer is closed")
            self._pending = snapshot
            self._condition.notify_all()

    def flush(self, timeout=5):
        with self._condition:
            done = self._condition.wait_for(
                lambda: self._pending is None and not self._writing, timeout)
            if not done:
                raise TimeoutError("settings save did not finish")
            if self._error is not None:
                raise OSError("settings save failed") from self._error

    def close(self):
        try:
            self.flush()
        finally:
            with self._condition:
                self._closed = True
                self._condition.notify_all()
            self._thread.join(timeout=5)

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending is not None)
                if self._closed and self._pending is None:
                    return
                snapshot, self._pending = self._pending, None
                self._writing = True
            temporary = None
            try:
                if snapshot != self._last:
                    directory = os.path.dirname(os.path.abspath(self.path))
                    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                                     dir=directory, delete=False) as stream:
                        temporary = stream.name
                        stream.write(snapshot)
                    os.replace(temporary, self.path)
                    temporary = None
                    self._last = snapshot
                self._error = None
            except Exception as exc:
                self._error = exc
                logging.getLogger(__name__).exception("Could not save settings")
            finally:
                if temporary is not None:
                    try:
                        os.unlink(temporary)
                    except OSError:
                        pass
                with self._condition:
                    self._writing = False
                    self._condition.notify_all()
