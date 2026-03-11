
import os
import sys
import unittest

# Add src to PYTHONPATH
sys.path.append(os.path.abspath("src"))

from sequencer.sequencer import Sequencer
from sequencer.models import MidiTrack

class TestTrackReordering(unittest.TestCase):
    def setUp(self):
        self.seq = Sequencer()
        self.seq.add_track("Track 0")
        self.seq.add_track("Track 1")
        self.seq.add_track("Track 2")

    def test_initial_order(self):
        # track_display_order should be [0, 1, 2]
        self.assertEqual(self.seq.song.track_display_order, [0, 1, 2])

    def test_move_track(self):
        # Move Track 2 to position 0
        self.seq.move_track_display_order(2, 0)
        self.assertEqual(self.seq.song.track_display_order, [2, 0, 1])

        # Move Track 0 (which is at index 1 now) to position 2
        self.seq.move_track_display_order(1, 2)
        self.assertEqual(self.seq.song.track_display_order, [2, 1, 0])

    def test_delete_track_updates_order(self):
        # Move Track 1 to position 0: [1, 0, 2]
        self.seq.move_track_display_order(1, 0)

        # Delete Track 0 (internal index 0)
        # Remaining internal indices: 1, 2 (shifted to 0, 1)
        self.seq.delete_track(0, confirm_str='y', api_mode=True)

        # Track 1 (was 1, now 0) should be at visual index 0
        # Track 2 (was 2, now 1) should be at visual index 1
        self.assertEqual(self.seq.song.track_display_order, [0, 1])

if __name__ == '__main__':
    unittest.main()
