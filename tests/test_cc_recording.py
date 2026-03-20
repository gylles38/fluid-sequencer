import unittest
from sequencer.sequencer import Sequencer
from sequencer.models import Song, MidiTrack, AutomationTrack, AutomationPoint, Event, Note
from kivy.clock import Clock
import time

class TestCCRecording(unittest.TestCase):

    def setUp(self):
        """Set up a new Sequencer instance before each test."""
        self.sequencer = Sequencer()
        self.midi_track = MidiTrack(name="Target Track")
        self.sequencer.song.add_track(self.midi_track)
        self.auto_track = AutomationTrack(name="Modulation", target_track_index=0)
        # Add a dummy point to satisfy the 'any(p.parameter == "cc1" for p in t.points)' check
        self.auto_track.add_point(AutomationPoint(start_time=0.0, parameter="cc1", value=0.0))
        self.sequencer.song.add_track(self.auto_track)

    def test_merge_cc1_to_automation(self):
        """Test that CC1 recorded events are merged into the automation track."""
        # 1. Simulate a recorded CC1 event
        event_data = {
            'type': 'cc',
            'track_idx': 0,
            'control': 1,
            'value': 64,
            'start_time': 1.0
        }
        self.sequencer.jack_manager._recorded_events_to_merge.append(event_data)

        # 2. Call _merge_recorded_events
        self.sequencer._merge_recorded_events(0)

        # 3. Verify that the point was added to the automation track
        # points[0] is the dummy point at t=0
        self.assertEqual(len(self.auto_track.points), 2)
        new_point = self.auto_track.points[1]
        self.assertEqual(new_point.parameter, 'cc1')
        self.assertEqual(new_point.start_time, 1.0)
        self.assertEqual(new_point.value, 64.0)

    def test_merge_other_cc_to_midi_track(self):
        """Test that other CC recorded events are merged into the MIDI track as standard messages."""
        event_data = {
            'type': 'cc',
            'track_idx': 0,
            'control': 7, # Volume
            'value': 100,
            'start_time': 2.0
        }
        self.sequencer.jack_manager._recorded_events_to_merge.append(event_data)

        self.sequencer._merge_recorded_events(0)

        # Should be in midi_track events
        self.assertEqual(len(self.midi_track.events), 1)
        self.assertEqual(self.midi_track.events[0].start_time, 2.0)
        self.assertEqual(self.midi_track.events[0].cc_messages[0].control, 7)
        self.assertEqual(self.midi_track.events[0].cc_messages[0].value, 100)

if __name__ == '__main__':
    unittest.main()
