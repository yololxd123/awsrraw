"""Isolate application smoke checks from saved settings and background hooks."""
from contextlib import contextmanager, ExitStack
from unittest.mock import patch


@contextmanager
def isolated_app(overlay_class, directory):
    with ExitStack() as stack:
        for method, name in [('_settings_path', 'settings.json'),
                             ('_engine_settings_path', 'engine_settings.json'),
                             ('_coach_settings_path', 'coach_settings.json')]:
            stack.enter_context(patch.object(overlay_class, method, return_value=str(directory / name)))
        for method in ['_start_screen_bottom_detector', 'start_websocket_server',
                       'setup_global_hotkeys', '_start_settings_autosave', '_maybe_fetch_cloud_lines']:
            stack.enter_context(patch.object(overlay_class, method))
        stack.enter_context(patch.object(overlay_class, 'setup_window',
                                        lambda self: self.root.geometry('650x650')))
        yield
