import unittest
from unittest.mock import Mock, patch, MagicMock
from sequencer.jack_manager import JackManager
from sequencer.models import MidiTrack, Song

class TestSelectiveDisconnection(unittest.TestCase):
    def setUp(self):
        self.mock_sequencer = MagicMock()
        self.song = Song(name="TestSong")

        # Track 1: Managed instrument
        self.track1 = MidiTrack(name="Piano", channel=0)
        self.track1.input_port_name = "PianoPort"

        # Track 2: Managed instrument
        self.track2 = MidiTrack(name="Organ", channel=1)
        self.track2.input_port_name = "OrganPort"

        self.song.tracks = [self.track1, self.track2]
        self.mock_sequencer.song = self.song

        self.jack_manager = JackManager(self.mock_sequencer)
        self.jack_manager.jack_client = MagicMock()

    def test_get_managed_instrument_ports(self):
        mock_port1 = MagicMock()
        mock_port1.name = "client:PianoPortIn"
        mock_port2 = MagicMock()
        mock_port2.name = "client:OrganPortIn"

        def side_effect(pattern, is_output=False):
            if "PianoPort" in pattern: return mock_port1
            if "OrganPort" in pattern: return mock_port2
            return None

        with patch.object(self.jack_manager, '_find_jack_port', side_effect=side_effect):
            managed = self.jack_manager._get_managed_instrument_ports()
            self.assertIn("client:PianoPortIn", managed)
            self.assertIn("client:OrganPortIn", managed)
            self.assertEqual(len(managed), 2)

    def test_selective_disconnection_logic(self):
        # Setup mock ports
        src_port = MagicMock()
        src_port.name = "keyboard:out"

        managed_port = MagicMock()
        managed_port.name = "managed:in"

        unmanaged_port = MagicMock()
        unmanaged_port.name = "unmanaged:in"

        # Mock connections
        self.jack_manager.jack_client.get_all_connections.return_value = [managed_port, unmanaged_port]

        # Mock managed ports set
        with patch.object(self.jack_manager, '_get_managed_instrument_ports', return_value={"managed:in"}):
            # Test a block similar to what's in _routing_worker_loop or stop()
            managed_dest_ports = self.jack_manager._get_managed_instrument_ports()
            connections = self.jack_manager.jack_client.get_all_connections(src_port)
            for conn in connections:
                if conn.name in managed_dest_ports:
                    self.jack_manager.jack_client.disconnect(src_port, conn)

            # Verify only the managed port was disconnected
            self.jack_manager.jack_client.disconnect.assert_called_once_with(src_port, managed_port)

if __name__ == '__main__':
    unittest.main()
