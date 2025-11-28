import unittest
from unittest.mock import MagicMock, patch, call, ANY
from sequencer.models import Song, MidiTrack, AudioTrack, Note, CCMessage, Event
from sequencer.midi_export import export_to_midi
import mido

class TestMidiExport(unittest.TestCase):
    """
    Tests for the MIDI export functionality in midi_export.py.
    """

    def setUp(self):
        """Set up a basic song structure for use in tests."""
        self.song = Song(name="Test Song", tempo=120)
        self.midi_track = MidiTrack(name="Piano", channel=0, instrument=0)
        self.audio_track = AudioTrack(name="Vocals", filepath="vox.wav")
        self.song.add_track(self.midi_track)
        self.song.add_track(self.audio_track)

    @patch('sequencer.midi_export.mido.MidiTrack')
    @patch('sequencer.midi_export.mido.MidiFile')
    def test_export_simple_song(self, mock_midi_file_constructor, mock_midi_track_constructor):
        """Tests exporting a basic song with one MIDI track."""
        # Arrange
        mock_mid = MagicMock()
        mock_midi_file_constructor.return_value = mock_mid

        mock_tempo_track = MagicMock()
        mock_midi_track = MagicMock()
        # The constructor will be called twice: once for the tempo track, once for the midi track.
        mock_midi_track_constructor.side_effect = [mock_tempo_track, mock_midi_track]

        note = Note(pitch=60, velocity=100, duration=1.0) # 1 beat = 480 ticks
        event = Event(start_time=1.0, notes=[note]) # starts at 1 beat = 480 ticks
        self.midi_track.add_event(event)

        # Act
        export_to_midi(self.song, "simple_export.mid")

        # Assert
        mock_midi_file_constructor.assert_called_once_with(type=1, ticks_per_beat=480)

        # Check that two tracks were appended to the main midi file mock
        self.assertEqual(mock_mid.tracks.append.call_count, 2)

        # Check for meta messages and program change on the correct mock track
        mock_midi_track.append.assert_any_call(mido.MetaMessage('track_name', name='Piano'))
        mock_midi_track.append.assert_any_call(mido.Message('program_change', channel=0, program=0, time=0))

        # Check for note on/off messages with correct timing
        note_on_call = call(mido.Message('note_on', channel=0, note=60, velocity=100, time=480))
        note_off_call = call(mido.Message('note_off', channel=0, note=60, velocity=0, time=480))
        mock_midi_track.append.assert_has_calls([note_on_call, note_off_call])

        # Check that the file was saved
        mock_mid.save.assert_called_once_with("simple_export.mid")

    @patch('sequencer.midi_export.mido.MidiTrack')
    @patch('sequencer.midi_export.mido.MidiFile')
    def test_export_skips_audio_tracks(self, mock_midi_file_constructor, mock_midi_track_constructor):
        """Tests that audio tracks are ignored during export."""
        # Arrange
        mock_mid = MagicMock()
        mock_midi_file_constructor.return_value = mock_mid

        # Add a note to the midi track to ensure it's not considered empty
        note = Note(pitch=60, velocity=100, duration=1.0)
        event = Event(start_time=1.0, notes=[note])
        self.midi_track.add_event(event)

        # Act
        export_to_midi(self.song, "audio_skip.mid")

        # Assert
        # The MidiTrack constructor should only be called for the tempo track and the one MIDI track.
        self.assertEqual(mock_midi_track_constructor.call_count, 2)
        # Should be 2 tracks appended: 1 tempo track + 1 MIDI track. Audio track is skipped.
        self.assertEqual(mock_mid.tracks.append.call_count, 2)

    @patch('sequencer.midi_export.mido.MidiTrack')
    @patch('sequencer.midi_export.mido.MidiFile')
    def test_export_with_cc_message(self, mock_midi_file_constructor, mock_midi_track_constructor):
        """Tests that CC messages are exported correctly."""
        # Arrange
        mock_mid = MagicMock()
        mock_midi_file_constructor.return_value = mock_mid
        mock_midi_track = MagicMock()
        mock_midi_track_constructor.side_effect = [MagicMock(), mock_midi_track]

        cc = CCMessage(control=7, value=120)
        event = Event(start_time=0.5, cc_messages=[cc]) # at 240 ticks
        self.midi_track.add_event(event)

        # Act
        export_to_midi(self.song, "cc_export.mid")

        # Assert
        cc_call = call(mido.Message('control_change', channel=0, control=7, value=120, time=240))
        mock_midi_track.append.assert_has_calls([cc_call])

    @patch('sequencer.midi_export.mido.MidiTrack')
    @patch('sequencer.midi_export.mido.MidiFile')
    def test_track_velocity_multiplier(self, mock_midi_file_constructor, mock_midi_track_constructor):
        """Tests that the track's velocity multiplier is applied."""
        # Arrange
        mock_mid = MagicMock()
        mock_midi_file_constructor.return_value = mock_mid
        mock_midi_track = MagicMock()
        mock_midi_track_constructor.side_effect = [MagicMock(), mock_midi_track]

        self.midi_track.velocity = 0.5 # Set multiplier to half
        note = Note(pitch=60, velocity=100, duration=1.0)
        event = Event(start_time=1.0, notes=[note])
        self.midi_track.add_event(event)

        # Act
        export_to_midi(self.song, "velocity_test.mid")

        # Assert
        # Expected velocity = 100 * 0.5 = 50
        note_on_call = call(mido.Message('note_on', channel=0, note=60, velocity=50, time=480))
        mock_midi_track.append.assert_has_calls([note_on_call])

    @patch('sequencer.midi_export.mido.MidiTrack')
    @patch('sequencer.midi_export.mido.MidiFile')
    def test_event_ordering(self, mock_midi_file_constructor, mock_midi_track_constructor):
        """Tests that events are sorted correctly before export."""
        # Arrange
        mock_mid = MagicMock()
        mock_midi_file_constructor.return_value = mock_mid
        mock_midi_track = MagicMock()
        mock_midi_track_constructor.side_effect = [MagicMock(), mock_midi_track]

        event1 = Event(start_time=2.0, notes=[Note(pitch=60, velocity=100, duration=1.0)]) # tick 960
        event2 = Event(start_time=1.0, notes=[Note(pitch=62, velocity=100, duration=1.0)]) # tick 480
        self.midi_track.events = [event1, event2] # Add them in the wrong order

        # Act
        export_to_midi(self.song, "ordering_test.mid")

        # Assert
        # Get all the message objects from the mock calls
        appended_messages = [c[0][0] for c in mock_midi_track.append.call_args_list]

        # Filter for just note_on messages
        note_on_msgs = [msg for msg in appended_messages if isinstance(msg, mido.Message) and msg.type == 'note_on']

        # Check that the note_on messages were generated with correct delta times,
        # implying they were sorted correctly before deltas were calculated.
        # First note (pitch 62) should have a delta time of 480.
        # Second note (pitch 60) should have a delta time of 960 - 480 = 480.
        self.assertEqual(note_on_msgs[0].note, 62)
        self.assertEqual(note_on_msgs[0].time, 480)
        self.assertEqual(note_on_msgs[1].note, 60)
        self.assertEqual(note_on_msgs[1].time, 480)


if __name__ == '__main__':
    unittest.main()
