import os
import pytest
from sequencer.gp_import import import_gp
from sequencer.models import MidiTrack, AutomationTrack

# Create a dummy GP file is complicated, so we might need a real one for a full test.
# But we can at least check if the module imports and basic structure is there.

def test_gp_import_module():
    import sequencer.gp_import
    assert hasattr(sequencer.gp_import, 'import_gp')

# Since I don't have a sample .gp file easily available in the environment to test with,
# I will try to see if I can find one or mock the guitarpro.parse if needed.
# But for now, let's verify if I can run a basic test.

def test_pitch_bend_automation_support():
    from sequencer.models import AutomationPoint
    # This should not raise ValueError anymore
    ap = AutomationPoint(start_time=0.0, parameter="pitch", value=0.5, curve="linear")
    assert ap.parameter == "pitch"

    ap2 = AutomationPoint(start_time=1.0, parameter="pb", value=-0.5, curve="linear")
    assert ap2.parameter == "pb"
