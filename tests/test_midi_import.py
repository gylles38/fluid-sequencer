import unittest
from unittest.mock import MagicMock, patch
from src.sequencer.models import Song, Note, CCMessage
from src.sequencer.midi_import import import_song
import mido

class TestMidiImport(unittest.TestCase):
    """
    Tests for the MIDI import functionality in midi_import.py.
    """

    def create_mock_midi_file(self, tracks, ticks_per_beat=480):
        """Helper function to create a mock mido.MidiFile object."""
        mock_mid = MagicMock()
        mock_mid.ticks_per_beat = ticks_per_beat
        mock_mid.tracks = tracks
        return mock_mid

    @patch('src.sequencer.midi_import.mido.MidiFile')
    def test_import_simple_song(self, mock_midi_file_constructor):
        """Tests importing a basic MIDI file with one track and one note."""
        # Arrange
        mock_track = [
            mido.MetaMessage('set_tempo', tempo=mido.bpm2tempo(150), time=0),
            mido.Message('program_change', channel=0, program=5, time=0),
            mido.Message('note_on', channel=0, note=60, velocity=100, time=100),
            mido.Message('note_off', channel=0, note=60, velocity=0, time=200) # duration = 200 ticks
        ]
        mock_mid = self.create_mock_midi_file([mock_track])
        mock_midi_file_constructor.return_value = mock_mid

        # Act
        song = import_song("dummy_path.mid")

        # Assert
        self.assertEqual(song.name, "dummy_path")
        self.assertEqual(song.tempo, 150)
        self.assertEqual(song.ticks_per_beat, 480)
        self.assertEqual(len(song.tracks), 1)

        track = song.tracks[0]
        self.assertEqual(track.instrument, 5)
        self.assertEqual(len(track.events), 1)

        event = track.events[0]
        self.assertAlmostEqual(event.start_time, 100 / 480)
        self.assertEqual(len(event.notes), 1)

        note = event.notes[0]
        self.assertEqual(note.pitch, 60)
        self.assertEqual(note.velocity, 100)
        self.assertAlmostEqual(note.duration, 200 / 480)

    @patch('src.sequencer.midi_import.mido.MidiFile')
    def test_import_with_cc_messages(self, mock_midi_file_constructor):
        """Tests that CC messages are correctly imported."""
        # Arrange
        mock_track = [
            mido.Message('control_change', channel=1, control=7, value=120, time=50),
            mido.Message('note_on', channel=1, note=72, velocity=90, time=50), # at tick 100
            mido.Message('note_off', channel=1, note=72, velocity=0, time=100) # at tick 200
        ]
        mock_mid = self.create_mock_midi_file([mock_track])
        mock_midi_file_constructor.return_value = mock_mid

        # Act
        song = import_song("cc_test.mid")

        # Assert
        self.assertEqual(len(song.tracks), 1)
        track = song.tracks[0]
        self.assertEqual(len(track.events), 2) # One event for the CC, one for the note

        cc_event = track.events[0]
        self.assertAlmostEqual(cc_event.start_time, 50 / 480)
        self.assertEqual(len(cc_event.cc_messages), 1)
        self.assertEqual(len(cc_event.notes), 0)
        cc_message = cc_event.cc_messages[0]
        self.assertEqual(cc_message.control, 7)
        self.assertEqual(cc_message.value, 120)

        note_event = track.events[1]
        self.assertAlmostEqual(note_event.start_time, 100 / 480)
        self.assertEqual(len(note_event.notes), 1)

    @patch('src.sequencer.midi_import.mido.MidiFile')
    def test_import_multiple_tracks(self, mock_midi_file_constructor):
        """Tests importing a file with multiple tracks."""
        # Arrange
        track1 = [mido.MetaMessage('track_name', name='Piano', time=0),
                  mido.Message('note_on', channel=0, note=60, velocity=100, time=0),
                  mido.Message('note_off', channel=0, note=60, velocity=0, time=480)]
        track2 = [mido.MetaMessage('track_name', name='Bass', time=0),
                  mido.Message('note_on', channel=1, note=40, velocity=110, time=0),
                  mido.Message('note_off', channel=1, note=40, velocity=0, time=480)]

        mock_mid = self.create_mock_midi_file([track1, track2])
        mock_midi_file_constructor.return_value = mock_mid

        # Act
        song = import_song("multi_track.mid")

        # Assert
        self.assertEqual(len(song.tracks), 2)
        self.assertEqual(song.tracks[0].name, "Piano")
        self.assertEqual(song.tracks[1].name, "Bass")
        self.assertEqual(len(song.tracks[0].events), 1)
        self.assertEqual(len(song.tracks[1].events), 1)

    @patch('src.sequencer.midi_import.mido.MidiFile')
    def test_import_metadata_only_track(self, mock_midi_file_constructor):
        """Tests that tracks with only metadata are skipped."""
        # Arrange
        track1 = [mido.MetaMessage('track_name', name='Info', time=0),
                  mido.MetaMessage('copyright', text='(C) 2024', time=0)]
        track2 = [mido.Message('note_on', channel=0, note=60, velocity=100, time=0),
                  mido.Message('note_off', channel=0, note=60, velocity=0, time=480)]

        mock_mid = self.create_mock_midi_file([track1, track2])
        mock_midi_file_constructor.return_value = mock_mid

        # Act
        song = import_song("metadata_track.mid")

        # Assert
        self.assertEqual(len(song.tracks), 1, "Should skip the metadata-only track")
        self.assertEqual(len(song.tracks[0].events), 1)

    @patch('src.sequencer.midi_import.mido.MidiFile')
    def test_import_unclosed_note(self, mock_midi_file_constructor):
        """Tests that a note_on without a corresponding note_off is ignored."""
        # Arrange
        mock_track = [
            mido.Message('note_on', channel=0, note=60, velocity=100, time=100),
            # No note_off for note 60
            mido.Message('note_on', channel=0, note=72, velocity=100, time=100),
            mido.Message('note_off', channel=0, note=72, velocity=0, time=100)
        ]
        mock_mid = self.create_mock_midi_file([mock_track])
        mock_midi_file_constructor.return_value = mock_mid

        # Act
        song = import_song("unclosed_note.mid")

        # Assert
        self.assertEqual(len(song.tracks), 1)
        track = song.tracks[0]
        # Only the note that was closed (pitch 72) should be present.
        self.assertEqual(len(track.events), 1)
        self.assertEqual(track.events[0].notes[0].pitch, 72)


if __name__ == '__main__':
    unittest.main()
