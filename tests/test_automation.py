import unittest
from src.sequencer.sequencer import Sequencer
from src.sequencer.models import Song, MidiTrack, AutomationTrack, AutomationPoint

class TestAutomation(unittest.TestCase):

    def setUp(self):
        """Set up a new Sequencer instance before each test."""
        self.sequencer = Sequencer()
        self.sequencer.song.add_track(MidiTrack(name="Target Track"))

    def test_generate_step_automation(self):
        """Test generation of 'step' automation events."""
        auto_track = AutomationTrack(name="Step", target_track_index=0)
        auto_track.add_point(AutomationPoint(start_time=0.0, parameter="vol", value=0.5, curve="step"))
        auto_track.add_point(AutomationPoint(start_time=1.0, parameter="vol", value=1.0, curve="step"))

        events = self.sequencer._generate_automation_events(auto_track)

        self.assertEqual(len(events), 2)

        self.assertEqual(events[0]['time'], 0.0)
        self.assertEqual(events[0]['value'], 0.5)
        self.assertEqual(events[0]['param_config']['control'], 7) # Volume CC

        self.assertEqual(events[1]['time'], 1.0)
        self.assertEqual(events[1]['value'], 1.0)

    def test_generate_linear_automation(self):
        """Test generation of 'linear' automation events."""
        self.sequencer.song.time_signature_numerator = 4
        auto_track = AutomationTrack(name="Linear", target_track_index=0)
        auto_track.add_point(AutomationPoint(start_time=0.0, parameter="pan", value=-1.0, curve="linear"))
        auto_track.add_point(AutomationPoint(start_time=1.0, parameter="pan", value=1.0, curve="step")) # End with a step

        events = self.sequencer._generate_automation_events(auto_track)

        # 1 initial point + 15 intermediate points for a 1-beat duration (1 / (1/16) - 1)
        self.assertEqual(len(events), 17)

        # Check first point
        self.assertAlmostEqual(events[0]['time'], 0.0)
        self.assertAlmostEqual(events[0]['value'], -1.0)
        self.assertEqual(events[0]['param_config']['control'], 10) # Pan CC

        # Check a midpoint
        self.assertAlmostEqual(events[8]['time'], 0.5)
        self.assertAlmostEqual(events[8]['value'], 0.0)

        # Check last generated point before the final "step" point
        self.assertAlmostEqual(events[15]['time'], 0.9375)
        self.assertAlmostEqual(events[15]['value'], 0.875)

        # Check the final point from the curve
        self.assertAlmostEqual(events[16]['time'], 1.0)
        self.assertAlmostEqual(events[16]['value'], 1.0)

    def test_generate_multi_parameter_automation(self):
        """Test automation with multiple different parameters on the same track."""
        auto_track = AutomationTrack(name="Multi", target_track_index=0)
        auto_track.add_point(AutomationPoint(start_time=0.0, parameter="vol", value=0.5, curve="step"))
        auto_track.add_point(AutomationPoint(start_time=0.5, parameter="pan", value=1.0, curve="step"))

        events = self.sequencer._generate_automation_events(auto_track)

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]['param_config']['control'], 7) # Volume
        self.assertEqual(events[1]['param_config']['control'], 10) # Pan

if __name__ == '__main__':
    unittest.main()
