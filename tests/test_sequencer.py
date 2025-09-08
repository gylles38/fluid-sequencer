import unittest
from unittest.mock import patch, MagicMock, mock_open, call
from src.sequencer.sequencer import Sequencer
from src.sequencer.models import Song, MidiTrack, AudioTrack, Note, Event, CCMessage
import json

class TestSequencer(unittest.TestCase):

    def setUp(self):
        """Set up a new Sequencer instance before each test."""
        self.sequencer = Sequencer()

    def test_initialization(self):
        """Test that the sequencer initializes with default values."""
        self.assertEqual(self.sequencer.song.tempo, 120)
        self.assertEqual(self.sequencer.playback_state, "stopped")
        self.assertIsInstance(self.sequencer.song, Song)
        self.assertEqual(len(self.sequencer.song.tracks), 0)

    @patch('builtins.print')
    def test_set_tempo(self, mock_print):
        """Test the set_tempo method."""
        self.sequencer.set_tempo(150)
        self.assertEqual(self.sequencer.song.tempo, 150)
        mock_print.assert_called_with("Tempo set to 150 BPM.")

        with self.assertRaises(ValueError):
            self.sequencer.set_tempo(0)
        with self.assertRaises(ValueError):
            self.sequencer.set_tempo(-100)

    @patch('builtins.print')
    def test_set_time_signature(self, mock_print):
        """Test the set_time_signature method."""
        self.sequencer.set_time_signature(3, 4)
        self.assertEqual(self.sequencer.song.time_signature_numerator, 3)
        self.assertEqual(self.sequencer.song.time_signature_denominator, 4)
        mock_print.assert_called_with("Time signature set to 3/4.")

        # Test invalid denominator
        self.sequencer.set_time_signature(4, 5)
        mock_print.assert_called_with("Error: Invalid time signature. Denominator must be a power of 2.")

    @patch('builtins.print')
    def test_add_midi_track(self, mock_print):
        """Test adding a MIDI track."""
        self.sequencer.add_track(name="Test MIDI", track_type='midi', instrument=5)
        self.assertEqual(len(self.sequencer.song.tracks), 1)
        track = self.sequencer.song.tracks[0]
        self.assertIsInstance(track, MidiTrack)
        self.assertEqual(track.name, "Test MIDI")
        self.assertEqual(track.instrument, 5)
        mock_print.assert_called_with("MIDI track 'Test MIDI' added.")

    @patch('pydub.AudioSegment.from_file')
    @patch('builtins.print')
    def test_add_audio_track(self, mock_print, mock_from_file):
        """Test adding an Audio track."""
        mock_from_file.return_value = MagicMock() # Mock the successful loading of an audio file
        self.sequencer.add_track(name="Test Audio", track_type='audio', filepath="test.wav")
        self.assertEqual(len(self.sequencer.song.tracks), 1)
        track = self.sequencer.song.tracks[0]
        self.assertIsInstance(track, AudioTrack)
        self.assertEqual(track.name, "Test Audio")
        self.assertEqual(track.filepath, "test.wav")
        mock_print.assert_called_with("Audio track 'Test Audio' added with file 'test.wav'.")

    @patch('builtins.print')
    def test_delete_track(self, mock_print):
        """Test deleting a track."""
        self.sequencer.add_track(name="To Delete", track_type='midi')
        self.assertEqual(len(self.sequencer.song.tracks), 1)

        result = self.sequencer.delete_track(0)
        self.assertTrue(result)
        self.assertEqual(len(self.sequencer.song.tracks), 0)
        mock_print.assert_called_with("Track 'To Delete' deleted.")

        result = self.sequencer.delete_track(99)
        self.assertFalse(result)
        mock_print.assert_called_with("Error: Invalid track index.")

    @patch('builtins.print')
    def test_rename_track(self, mock_print):
        """Test renaming a track."""
        self.sequencer.add_track(name="Old Name", track_type='midi')
        self.sequencer.rename_track(0, "New Name")
        self.assertEqual(self.sequencer.song.tracks[0].name, "New Name")
        mock_print.assert_called_with("Track 'Old Name' renamed to 'New Name'.")

    def test_parse_position_to_beats(self):
        """Test the position string parsing logic."""
        self.sequencer.set_time_signature(4, 4)
        self.assertEqual(self.sequencer.parse_position_to_beats("1:1"), 0.0)
        self.assertEqual(self.sequencer.parse_position_to_beats("2:1"), 4.0)
        self.assertEqual(self.sequencer.parse_position_to_beats("3:3"), 10.0)
        self.assertIsNone(self.sequencer.parse_position_to_beats("1:5")) # Invalid beat
        self.assertIsNone(self.sequencer.parse_position_to_beats("abc")) # Invalid format

    @patch('builtins.print')
    def test_add_cc_event(self, mock_print):
        """Test adding a CC event to a track."""
        self.sequencer.add_track(name="MIDI", track_type='midi')
        self.sequencer.add_cc_event(track_index=0, position_str="1:2", control=7, value=100)

        track = self.sequencer.song.tracks[0]
        self.assertEqual(len(track.events), 1)
        event = track.events[0]
        self.assertEqual(event.start_time, 1.0) # 1:2 is the 2nd beat, which is at time 1.0
        self.assertEqual(len(event.cc_messages), 1)
        cc = event.cc_messages[0]
        self.assertEqual(cc.control, 7)
        self.assertEqual(cc.value, 100)
        mock_print.assert_called_with("Added new CC event at position 1:2 on track 'MIDI'.")

    @patch('mido.open_output')
    def test_assign_port(self, mock_open_output):
        """Test assigning a port to a track."""
        self.sequencer.add_track(name="MIDI", track_type='midi')
        self.sequencer.assign_port(0, "MyMIDIPort")
        self.assertEqual(self.sequencer.song.tracks[0].output_port_name, "MyMIDIPort")

    def test_save_and_load_project(self):
        """Test saving and loading a project file."""
        self.sequencer.add_track(name="Test MIDI", track_type='midi')
        self.sequencer.set_tempo(99)

        # Mock the writing of the project file
        m_write = mock_open()
        with patch('builtins.open', m_write):
            self.sequencer.save_project("test_project")

        # Get the content that was written to the mock file
        handle = m_write()
        written_content = "".join(call_args[0][0] for call_args in handle.write.call_args_list)

        # Mock the reading of the project file, using the content we just captured
        m_read = mock_open(read_data=written_content)
        with patch('builtins.open', m_read):
            new_sequencer = Sequencer()
            new_sequencer.load_project("test_project")

        # Assert that the loaded project has the correct data
        self.assertEqual(new_sequencer.song.tempo, 99)
        self.assertEqual(len(new_sequencer.song.tracks), 1)
        self.assertEqual(new_sequencer.song.tracks[0].name, "Test MIDI")


if __name__ == '__main__':
    unittest.main()
