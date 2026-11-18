"""No GUI/provider imports, no engine binary, no third-party test runner."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from adaptive_policy import choose_candidate, clean_context
from depth_timer import DepthTimer
from engine_session import UciSession
from recommendation_policy import move_accuracy
from settings_store import SettingsWriter


class TimerTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.timer = DepthTimer(lambda: self.now)
        self.timer.toggle()

    def test_completed_incomplete_cache_averages_and_reset(self):
        ticket = self.timer.begin('position', 12, 'fixture', 8)
        self.now = .5
        self.timer.finish(ticket, 'position', 12, True)
        self.timer.finish(ticket, 'position', 12, True)  # duplicate ignored
        ticket = self.timer.begin('position', 12, 'fixture', 8)
        self.now = 1
        self.timer.finish(ticket, 'position', 11, False)
        self.timer.cache_hit('position', 12)
        snapshot = self.timer.snapshot()
        self.assertEqual((snapshot['count'], snapshot['incomplete'], snapshot['cached']), (1, 1, 1))
        self.assertEqual(snapshot['average'], .5)
        old = self.timer.begin('position', 12, 'fixture', 8)
        self.timer.reset()
        self.timer.finish(old, 'position', 12, True)
        self.assertTrue(self.timer.enabled)
        self.assertEqual(self.timer.snapshot()['count'], 0)

    def test_stale_fen_and_ticket_cannot_complete_current_search(self):
        old = self.timer.begin('old', 12, 'fixture', 8)
        current = self.timer.begin('new', 12, 'fixture', 8)
        self.timer.finish(old, 'old', 12, True)
        self.timer.finish(current, 'old', 12, True)
        self.assertEqual(self.timer.snapshot()['active']['ticket'], current)
        self.now = .2
        self.timer.finish(current, 'new', 12, True)
        self.assertEqual(self.timer.snapshot()['count'], 1)


class SelectionTests(unittest.TestCase):
    def test_strict_target_uses_caller_safety_and_nearest_priority(self):
        candidates = [(1, (['best'], '0'), 0., 100.),
                      (2, (['planned'], '-.15'), -.15, 94.),
                      (3, (['target'], '-.4'), -.4, 85.),
                      (4, (['allowed_by_user'], '-.7'), -.7, 75.)]
        selected, _ = choose_candidate(candidates, [], 50, 85, 75, 'target')
        self.assertEqual(selected[0], 4)  # No hidden .50 cap after the user's cap.
        safe = candidates[:3]  # Caller imposes a tighter loss limit.
        selected, _ = choose_candidate(safe, [], 50, 85, 75, 'planned')
        self.assertEqual(selected[0], 3)
        selected, status = choose_candidate(safe[:2], [100] * 30, 50, 85, 75)
        self.assertEqual(selected[0], 2)
        self.assertIn('unavailable', status)

    def test_style_cannot_escape_range_or_drift_far_from_priority(self):
        candidates = [(1, (['best'], '0'), 0., 100.),
                      (2, (['target'], '-.7'), -.7, 75.),
                      (3, (['style'], '-.72'), -.72, 74.),
                      (4, (['too_far'], '-1.5'), -1.5, 55.)]
        reverse_rank = lambda options: sorted(options, key=lambda c: -c[0])
        selected, _ = choose_candidate(candidates, [], 50, 85, 75,
                                       order_candidates=reverse_rank)
        self.assertEqual(selected[0], 3)
        # Styles also cannot worsen the nearest range miss.
        selected, status = choose_candidate(candidates, [], 80, 85, 82,
                                            order_candidates=reverse_rank)
        self.assertEqual(selected[0], 2)
        self.assertIn('unavailable', status)

    def test_accuracy_reference_and_context_bounds(self):
        self.assertAlmostEqual(move_accuracy(0, 0), 100, places=2)
        self.assertGreater(move_accuracy(0, -15), move_accuracy(0, -40))
        self.assertGreater(move_accuracy(0, -40), move_accuracy(0, -100))
        context = clean_context({'self_rating': True, 'opponent_rating': float('nan'),
                                 'self_ms': -1, 'opp_ms': 500, 'secret': 'ignored'})
        self.assertIsNone(context['self_rating'])
        self.assertIsNone(context['opponent_rating'])
        self.assertIsNone(context['self_ms'])
        self.assertEqual(context['opp_ms'], 500)
        self.assertNotIn('secret', context)


class UciTests(unittest.TestCase):
    def test_stop_ack_configure_ready_then_only_latest_search_starts(self):
        stream = io.StringIO()
        session = UciSession(SimpleNamespace(stdin=stream))
        starts = []
        session.submit('one', {'Threads': 99, 'Unsupported': 1}, lambda options: starts.append('one'))
        self.assertEqual(stream.getvalue(), 'uci\n')
        session.handle_line('option name Threads type spin default 1 min 1 max 8')
        session.handle_line('uciok')
        self.assertIn('setoption name Threads value 8\nisready\n', stream.getvalue())
        self.assertFalse(starts)
        session.handle_line('readyok')
        self.assertEqual(starts, ['one'])
        session.submit('two', {'Threads': 2}, lambda options: starts.append('two'))
        session.submit('three', {'Threads': 3}, lambda options: starts.append('three'))
        self.assertEqual(stream.getvalue().count('stop\n'), 1)
        self.assertNotIn('setoption name Threads value 3', stream.getvalue())
        session.handle_line('bestmove e2e4')
        self.assertIn('setoption name Threads value 3\nisready\n', stream.getvalue())
        session.handle_line('readyok')
        self.assertEqual(starts, ['one', 'three'])
        session.close()

    def test_option_validation_rejects_unknown_and_command_injection(self):
        session = UciSession(SimpleNamespace(stdin=io.StringIO()))
        session.handle_line('option name Path type string default empty')
        self.assertIsNone(session.normalize('Path', 'x\nquit'))
        self.assertIsNone(session.normalize('missing', 1))
        self.assertEqual(session.normalize('Path', 'valid path'), 'valid path')
        session.close()


class SettingsTests(unittest.TestCase):
    def test_saved_snapshot_is_immutable_and_last_write_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            writer = SettingsWriter(path)
            try:
                data = {'value': 1}
                writer.submit(data)
                data['value'] = 99
                writer.flush()
                self.assertEqual(json.loads(path.read_text()), {'value': 1})
                for value in range(20):
                    writer.submit({'value': value})
                writer.flush()
                self.assertEqual(json.loads(path.read_text()), {'value': 19})
                self.assertEqual([p.name for p in Path(directory).iterdir()], ['settings.json'])
            finally:
                writer.close()
            with self.assertRaises(RuntimeError):
                writer.submit({})


if __name__ == '__main__':
    unittest.main()
