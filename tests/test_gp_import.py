import os
import pytest
from sequencer.gp_import import import_gp
from sequencer.models import MidiTrack, AutomationTrack

def test_gp_import_module():
    import sequencer.gp_import
    assert hasattr(sequencer.gp_import, 'import_gp')

def test_pitch_bend_automation_support():
    from sequencer.models import AutomationPoint
    # This should not raise ValueError anymore
    ap = AutomationPoint(start_time=0.0, parameter="pitch", value=0.5, curve="linear")
    assert ap.parameter == "pitch"

    ap2 = AutomationPoint(start_time=1.0, parameter="pb", value=-0.5, curve="linear")
    assert ap2.parameter == "pb"
