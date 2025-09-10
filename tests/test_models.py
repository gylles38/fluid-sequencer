import unittest
from src.sequencer.models import Note, CCMessage, Event, MidiTrack, AudioTrack, Song, AutomationPoint, AutomationTrack

class TestModels(unittest.TestCase):
    """
    Tests for the data models in models.py.
    """

    def test_note_validation(self):
        """Tests the validation rules for the Note dataclass."""
        # Valid note
        Note(pitch=60, velocity=100, duration=1.0)

        # Test pitch validation
        with self.assertRaises(ValueError, msg="Pitch below 0 should fail"):
            Note(pitch=-1, velocity=100, duration=1.0)
        with self.assertRaises(ValueError, msg="Pitch above 127 should fail"):
            Note(pitch=128, velocity=100, duration=1.0)

        # Test velocity validation
        with self.assertRaises(ValueError, msg="Velocity below 0 should fail"):
            Note(pitch=60, velocity=-1, duration=1.0)
        with self.assertRaises(ValueError, msg="Velocity above 127 should fail"):
            Note(pitch=60, velocity=128, duration=1.0)

        # Test duration validation
        with self.assertRaises(ValueError, msg="Duration of 0 should fail"):
            Note(pitch=60, velocity=100, duration=0)
        with self.assertRaises(ValueError, msg="Negative duration should fail"):
            Note(pitch=60, velocity=100, duration=-1.0)

    def test_cc_message_validation(self):
        """Tests the validation rules for the CCMessage dataclass."""
        # Valid CC message
        CCMessage(control=7, value=100)

        # Test control validation
        with self.assertRaises(ValueError, msg="Control below 0 should fail"):
            CCMessage(control=-1, value=100)
        with self.assertRaises(ValueError, msg="Control above 127 should fail"):
            CCMessage(control=128, value=100)

        # Test value validation
        with self.assertRaises(ValueError, msg="Value below 0 should fail"):
            CCMessage(control=7, value=-1)
        with self.assertRaises(ValueError, msg="Value above 127 should fail"):
            CCMessage(control=7, value=128)

    def test_event_validation(self):
        """Tests the validation rules for the Event dataclass."""
        # Valid event
        Event(start_time=0)
        Event(start_time=10.5)

        # Test start_time validation
        with self.assertRaises(ValueError, msg="Negative start_time should fail"):
            Event(start_time=-1.0)

    def test_midi_track_add_event(self):
        """Tests that MidiTrack.add_event keeps the event list sorted."""
        track = MidiTrack(name="Test Track")
        event1 = Event(start_time=4.0)
        event2 = Event(start_time=0.0)
        event3 = Event(start_time=2.0)

        track.add_event(event1)
        track.add_event(event2)
        track.add_event(event3)

        self.assertEqual(len(track.events), 3)
        self.assertEqual(track.events[0].start_time, 0.0)
        self.assertEqual(track.events[1].start_time, 2.0)
        self.assertEqual(track.events[2].start_time, 4.0)

    def test_song_add_track(self):
        """Tests the channel assignment logic in Song.add_track."""
        song = Song(name="Test Song")

        # Add a MIDI track, should get channel 0
        midi_track_1 = MidiTrack(name="MIDI 1")
        song.add_track(midi_track_1)
        self.assertEqual(midi_track_1.channel, 0)

        # Add an audio track, should not affect MIDI channel count
        audio_track = AudioTrack(name="Audio 1", filepath="test.wav")
        song.add_track(audio_track)

        # Add another MIDI track, should get channel 1
        midi_track_2 = MidiTrack(name="MIDI 2")
        song.add_track(midi_track_2)
        self.assertEqual(midi_track_2.channel, 1)

        # Test adding more than 16 MIDI tracks
        for i in range(2, 20):
            mt = MidiTrack(name=f"MIDI {i+1}")
            song.add_track(mt)
            # Channels 0-14 are assigned sequentially
            if i < 16:
                self.assertEqual(mt.channel, i)
            # Any more tracks get channel 15
            else:
                self.assertEqual(mt.channel, 15)

    def test_automation_point_validation(self):
        """Tests validation for the AutomationPoint dataclass."""
        # Valid points
        AutomationPoint(start_time=0, parameter="vol", value=0.5, curve="none")
        AutomationPoint(start_time=1.0, parameter="pan", value=-0.5, curve="linear")

        # Invalid start_time
        with self.assertRaises(ValueError):
            AutomationPoint(start_time=-1, parameter="vol", value=0.5)

        # Invalid parameter
        with self.assertRaises(ValueError):
            AutomationPoint(start_time=0, parameter="invalid_param", value=0.5)

        # Invalid curve
        with self.assertRaises(ValueError):
            AutomationPoint(start_time=0, parameter="vol", value=0.5, curve="invalid-curve")

    def test_automation_track_add_point(self):
        """Tests that AutomationTrack.add_point keeps the points list sorted."""
        track = AutomationTrack(name="Auto Track", target_track_index=0)
        point1 = AutomationPoint(start_time=4.0, parameter="vol", value=0.8)
        point2 = AutomationPoint(start_time=0.0, parameter="vol", value=0.1)
        point3 = AutomationPoint(start_time=2.0, parameter="vol", value=0.5)

        track.add_point(point1)
        track.add_point(point2)
        track.add_point(point3)

        self.assertEqual(len(track.points), 3)
        self.assertEqual(track.points[0].start_time, 0.0)
        self.assertEqual(track.points[1].start_time, 2.0)
        self.assertEqual(track.points[2].start_time, 4.0)

if __name__ == '__main__':
    unittest.main()
