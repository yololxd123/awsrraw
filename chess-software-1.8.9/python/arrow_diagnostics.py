"""Run beside main.py to diagnose missing arrows without replacing app modules.

Launch this file with the same Python installation used for the app. Reproduce
missing arrows, close the app, and share arrow_diagnostics.log. Only runtime
versions, module hashes, counters, window state and sanitized error locations
are recorded. Settings, engine paths, positions and UCI messages are omitted.
"""
from functools import wraps
import hashlib
import importlib
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import platform
import sys
import traceback

BASE = Path(__file__).resolve().parent


def make_logger(directory=BASE):
    logger = logging.getLogger('chess.arrow_diagnostics')
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = RotatingFileHandler(Path(directory) / 'arrow_diagnostics.log',
                                  maxBytes=512_000, backupCount=1, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
    logger.addHandler(handler)
    return logger, handler


def error_location(exc):
    # No raw exception text, source lines or absolute paths: these can contain
    # settings or personal paths. Function + line still identifies the failure.
    frames = traceback.extract_tb(exc.__traceback__)[-5:]
    return type(exc).__name__ + ' at ' + ' > '.join(
        f'{Path(frame.filename).name}:{frame.name}:{frame.lineno}' for frame in frames)


def instrument(gui, logger):
    cls = gui.ChessOverlay
    if getattr(cls, '_arrow_diagnostics_installed', False):
        return
    cls._arrow_diagnostics_installed = True

    def state(o):
        if '_arrow_diagnostics' not in o.__dict__:
            o._arrow_diagnostics = {'pv_received': 0, 'pv_scored': 0,
                                    'pv_bound': 0, 'bestmove_received': 0,
                                    'draw_calls': 0, 'draw_created': 0,
                                    'refresh_calls': 0, 'errors': {}}
        return o._arrow_diagnostics

    def failure(o, stage, exc):
        errors = state(o)['errors']
        key = f'{stage}: {error_location(exc)}'
        errors[key] = errors.get(key, 0) + 1
        # The counter captures repeated failures without flooding the file.
        if errors[key] == 1:
            logger.info('ERROR %s', key)

    original_parse = cls.process_engine_line
    @wraps(original_parse)
    def parse(o, engine, line):
        s = state(o)
        tokens = line.lower().split()
        if 'pv' in tokens:
            s['pv_received'] += 1
            s['pv_scored'] += int('score' in tokens)
            s['pv_bound'] += int('lowerbound' in tokens or 'upperbound' in tokens)
        if 'bestmove' in tokens:
            s['bestmove_received'] += 1
        return original_parse(o, engine, line)
    cls.process_engine_line = parse

    original_draw = cls.draw_move
    @wraps(original_draw)
    def draw(o, *args, **kwargs):
        s = state(o); s['draw_calls'] += 1
        canvas = o.arrow_canvas
        before = len(canvas.find_all())
        result = original_draw(o, *args, **kwargs)
        # draw_move currently swallows its own failures. Count whether it
        # actually added an item so those failures are still detectable.
        s['draw_created'] += max(0, len(canvas.find_all()) - before)
        return result
    cls.draw_move = draw

    def watch(name):
        original = getattr(cls, name, None)
        if original is None:
            return
        @wraps(original)
        def watched(o, *args, **kwargs):
            if name == 'update_display':
                state(o)['refresh_calls'] += 1
            try:
                return original(o, *args, **kwargs)
            except Exception as exc:
                failure(o, name, exc)
                raise  # Keep the application's existing error handling intact.
        setattr(cls, name, watched)

    for name in ('update_display', 'update_engine_line_text', '_compute_visible_suggestions',
                 'redraw_all_arrows', '_build_arrow_layer', '_sync_arrow_layer',
                 '_recover_overlay', 'analyse_position', '_dispatch_engine_search'):
        watch(name)

    def widget_info(widget):
        if widget is None:
            return None
        try:
            exists = bool(widget.winfo_exists())
            if not exists:
                return {'exists': False}
            return {'exists': True, 'mapped': bool(widget.winfo_ismapped()),
                    'size': (widget.winfo_width(), widget.winfo_height())}
        except Exception as exc:
            return {'error': type(exc).__name__}

    def snapshot(o):
        s = dict(state(o))
        # Copy mutable counters before handing them to the log formatter.
        s['errors'] = dict(s['errors'])
        engines = list(getattr(o, 'engines', []))
        s.update(position_received=bool(getattr(o, 'current_fen', None)),
                 primary_index=getattr(o, 'primary_engine_index', None),
                 depth=getattr(o, 'current_depth', None),
                 live_lines=len(getattr(o, 'multi_pvs', {})),
                 committed_lines=len(getattr(o, 'committed_multi_pvs', {})),
                 root=widget_info(o.root),
                 layer_ready=bool(getattr(o, '_arrow_layer_ready', False)),
                 layer=widget_info(getattr(o, 'arrow_layer', None)),
                 engines=[{'alive': bool(e.get('process') and e['process'].poll() is None),
                           'accepting_info': e.get('accepting_info'),
                           'searching': bool(e.get('current_search_fen') or e.get('search_fen')),
                           'depth': e.get('current_depth'),
                           'target': e.get('target_depth'),
                           'expected_pvs': e.get('_multipv_count'),
                           'committed_depth': e.get('_committed_depth'),
                           'committed_lines': len(e.get('committed_multi_pvs', {}))}
                          for e in engines])
        canvas = o.arrow_canvas
        s['canvas'] = widget_info(canvas)
        try:
            s['arrow_items'] = len(canvas.find_withtag('arrow'))
            s['pending_arrow_items'] = len(canvas.find_withtag('arrow_next'))
            s['separate_canvas'] = canvas is not o.canvas
            # Geometry and styles only; no titles, processes or account data.
            layer = getattr(o, 'arrow_layer', None)
            if layer is not None and layer.winfo_exists():
                s['layer_alpha'] = layer.attributes('-alpha')
                s['layer_topmost'] = layer.attributes('-topmost')
                if sys.platform == 'win32' and gui.user32 is not None:
                    hwnd = gui.user32.GetParent(layer.winfo_id()) or layer.winfo_id()
                    s['win32_visible'] = bool(gui.user32.IsWindowVisible(hwnd))
                    s['win32_styles'] = gui.user32.GetWindowLongW(hwnd, gui.GWL_EXSTYLE)
        except Exception as exc:
            failure(o, 'snapshot', exc)
        logger.info('STATE %r', s)
        return s

    original_init = cls.__init__
    @wraps(original_init)
    def init(o, *args, **kwargs):
        state(o)
        try:
            original_init(o, *args, **kwargs)
        except Exception as exc:
            failure(o, '__init__', exc)
            raise
        def tick():
            if not getattr(o, 'should_run', False):
                return
            try:
                snapshot(o)
            except Exception as exc:
                failure(o, 'snapshot', exc)
            if o.root.winfo_exists():
                o.root.after(2000, tick)
        o.root.after(500, tick)
    cls.__init__ = init
    # Exposed for the offline test harness; no application callbacks depend on it.
    cls._arrow_diagnostics_snapshot = snapshot


def main():
    logger, handler = make_logger()
    logger.info('START Python=%s OS=%s', platform.python_version(), sys.platform)
    try:
        sys.path.insert(0, str(BASE))
        for filename in ('gui_overlay.py', 'engine_pool.py', 'eval_window.py', 'control_panel.py'):
            path = BASE / filename
            logger.info('MODULE %s sha256=%s', filename,
                        hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else 'MISSING')
        gui = importlib.import_module('gui_overlay')
        logger.info('Tk=%s Tcl=%s', gui.tk.TkVersion, gui.tk.TclVersion)
        instrument(gui, logger)
        app = importlib.import_module('main')
        app.main()
    except Exception as exc:
        logger.info('LAUNCH ERROR %s', error_location(exc))
        raise
    finally:
        logger.info('STOP')
        logger.removeHandler(handler)
        handler.close()


if __name__ == '__main__':
    main()
