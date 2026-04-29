import unittest
from sequencer.sequencer import Sequencer
from sequencer.models import Song, MidiTrack, AutomationTrack, AutomationPoint, Event, Note

class TestSmoothing(unittest.TestCase):

    def setUp(self):
        """Set up a new Sequencer instance before each test."""
        self.sequencer = Sequencer()
        self.midi_track = MidiTrack(name="Target Track")
        self.sequencer.song.add_track(self.midi_track)
        self.auto_track = AutomationTrack(name="Modulation", target_track_index=0, active_parameter='cc1')
        self.sequencer.song.add_track(self.auto_track)

    def test_smoothing_linear_ramp(self):
        """Test that redundant points in a linear ramp are smoothed out."""
        # 1. Start ramp at t=0, val=0
        self.sequencer._add_smoothed_automation_point(self.auto_track, 'cc1', 0.0, 0.0)
        self.assertEqual(len(self.auto_track.points), 1)

        # 2. Midpoint at t=1, val=64
        self.sequencer._add_smoothed_automation_point(self.auto_track, 'cc1', 1.0, 64.0)
        self.assertEqual(len(self.auto_track.points), 2)

        # 3. New point at t=2, val=127
        # Point at t=1 is exactly on the line (0,0) to (2,127)?
        # (64 is approx 127/2)
        # Expected value at t=1 for line (0,0)-(2,127) is 63.5.
        # 64 - 63.5 = 0.5. Threshold is 0.5. So it should be smoothed!
        self.sequencer._add_smoothed_automation_point(self.auto_track, 'cc1', 2.0, 127.0)

        # Point at t=1 should have been replaced/updated to t=2, val=127
        self.assertEqual(len(self.auto_track.points), 2)
        self.assertEqual(self.auto_track.points[0].start_time, 0.0)
        self.assertEqual(self.auto_track.points[1].start_time, 2.0)
        self.assertEqual(self.auto_track.points[1].value, 127.0)

    def test_smoothing_jump(self):
        """Test that sharp jumps are preserved."""
        self.sequencer._add_smoothed_automation_point(self.auto_track, 'cc1', 0.0, 0.0)
        self.sequencer._add_smoothed_automation_point(self.auto_track, 'cc1', 1.0, 0.0)
        # Sharp jump
        self.sequencer._add_smoothed_automation_point(self.auto_track, 'cc1', 1.1, 127.0)

        # All points should be kept because the jump at 1.1 is far from the line (0,0)-(1,0)
        self.assertEqual(len(self.auto_track.points), 3)

if __name__ == '__main__':
    unittest.main()
