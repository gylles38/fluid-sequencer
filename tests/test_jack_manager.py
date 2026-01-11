import unittest
from unittest.mock import Mock, patch

from sequencer.jack_manager import JackManager

class TestJackManager(unittest.TestCase):

    def setUp(self):
        self.mock_sequencer = Mock()
        self.mock_sequencer.song.name = "TestSong"
        self.jack_manager = JackManager(self.mock_sequencer)

    def test_initialization(self):
        self.assertIsNotNone(self.jack_manager)
        self.assertEqual(self.jack_manager.sequencer, self.mock_sequencer)
        self.assertIsNone(self.jack_manager.jack_client)
        self.assertFalse(self.jack_manager.is_running)

if __name__ == '__main__':
    unittest.main()
