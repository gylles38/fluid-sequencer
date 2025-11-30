import time
import sys
import os
import threading

# Add src to python path to import sequencer
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), 'src')))

from sequencer.sequencer import Sequencer

def run_verification():
    # 1. Instantiate and configure the sequencer
    sequencer = Sequencer(gui_mode=False) # Run in headless mode

    try:
        # Start the JACK client
        sequencer.jack_manager.start()
        if not sequencer.jack_manager.is_running:
            print("TEST FAILED: JACK client could not be started.")
            return

        # --- Test Sequence ---
        # Set the default start beat to 4.0 (measure 2:1)
        start_beat = 4.0
        print(f"ACTION: Setting default start beat to {start_beat}")
        sequencer.set_default_start_beat(start_beat)

        # Start playback
        print(f"ACTION: Starting playback")
        sequencer.play()
        time.sleep(2) # Give time for playback to advance

        current_beat_after_play = sequencer.jack_manager.get_current_beat()
        print(f"INFO: Beat after playing for 2s: {current_beat_after_play:.2f}")
        if current_beat_after_play <= start_beat:
             print(f"TEST FAILED: Playhead did not advance after play command. Current beat: {current_beat_after_play}")
             return

        # Call STOP method
        print("ACTION: Calling sequencer.stop()")
        sequencer.stop()
        time.sleep(0.5) # Give time for the command to be processed

        beat_after_stop = sequencer.jack_manager.get_current_beat()
        print(f"INFO: Beat after stop: {beat_after_stop:.2f}")

        # Verification 1: Check if playhead returned to the initial start beat
        if not (abs(beat_after_stop - start_beat) < 0.01):
            print(f"TEST FAILED: Playhead did not return to start beat. Expected: {start_beat}, Got: {beat_after_stop}")
        else:
            print("VERIFICATION 1 PASSED: Playhead correctly returned to initial start beat.")

        # Call PLAY method again
        print("ACTION: Calling sequencer.play() again")
        sequencer.play()
        time.sleep(2)

        beat_after_second_play = sequencer.jack_manager.get_current_beat()
        print(f"INFO: Beat after second play: {beat_after_second_play:.2f}")

        # Verification 2: Check it started again from the correct beat
        if beat_after_second_play <= start_beat:
            print(f"TEST FAILED: Second play did not advance from the start beat. Current beat: {beat_after_second_play}")
        else:
            print("VERIFICATION 2 PASSED: Sequencer correctly started playing from the initial start beat again.")

    finally:
        print("Cleaning up...")
        if sequencer.jack_manager.is_running:
            sequencer.jack_manager.stop()
        print("Verification finished.")


if __name__ == "__main__":
    run_verification()
