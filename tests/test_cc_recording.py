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
        self.auto_track = AutomationTrack(name="Modulation", target_track_index=0, active_parameter='cc1')
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
        self.assertEqual(len(self.auto_track.points), 1)
        new_point = self.auto_track.points[0]
        self.assertEqual(new_point.parameter, 'cc1')
        self.assertEqual(new_point.start_time, 1.0)
        self.assertEqual(new_point.value, 64.0)

    def test_merge_other_cc_to_automation_track(self):
        """Test that other CC recorded events (like Volume) are now merged into automation tracks."""
        # Volume (CC7)
        event_data = {
            'type': 'cc',
            'track_idx': 0,
            'control': 7, # Volume
            'value': 100,
            'start_time': 2.0
        }
        self.sequencer.jack_manager._recorded_events_to_merge.append(event_data)

        self.sequencer._merge_recorded_events(0)

        # Should NOT be in midi_track events anymore, but in automation
        self.assertEqual(len(self.midi_track.events), 0)

        # Check automation points
        # setUp adds one automation track targeting track 0
        self.assertEqual(len(self.auto_track.points), 1)
        new_point = self.auto_track.points[0]
        self.assertEqual(new_point.parameter, 'vol')
        self.assertEqual(new_point.start_time, 2.0)
        self.assertAlmostEqual(new_point.value, 100.0/127.0)

    def test_record_cc_to_audio_track(self):
        """Test that CC messages can be recorded as automation for Audio tracks."""
        from sequencer.models import AudioTrack
        audio_track = AudioTrack(name="Audio", filepath="dummy.wav")
        self.sequencer.song.add_track(audio_track)
        audio_idx = len(self.sequencer.song.tracks) - 1

        # Add automation track targeting the audio track
        auto_track = AutomationTrack(name="Audio Vol", target_track_index=audio_idx, active_parameter='vol')
        self.sequencer.song.add_track(auto_track)

        # Simulate recorded Volume CC for the audio track
        event_data = {
            'type': 'cc',
            'track_idx': audio_idx,
            'control': 7,
            'value': 80,
            'start_time': 3.0
        }
        self.sequencer.jack_manager._recorded_events_to_merge.append(event_data)

        self.sequencer._merge_recorded_events(0)

        # Check that it went to the automation track
        self.assertEqual(len(auto_track.points), 1)
        self.assertEqual(auto_track.points[0].parameter, 'vol')
        self.assertAlmostEqual(auto_track.points[0].value, 80.0/127.0)

if __name__ == '__main__':
    unittest.main()
