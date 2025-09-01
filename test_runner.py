import os
from sequencer.sequencer import Sequencer

# --- Test Setup ---
# Ensure we have a clean slate
if os.path.exists("test_project.proj.json"):
    os.remove("test_project.proj.json")
if os.path.exists("test_export.mid"):
    os.remove("test_export.mid")

# --- Test Execution ---
print("--- Running Integration Test ---")
seq = Sequencer()

print("\n1. Creating virtual port...")
seq.create_virtual_port("testport")

print("\n2. Adding MIDI track...")
seq.add_track(name="midi_track", track_type='midi', instrument=0)

print("\n3. Assigning port to MIDI track...")
seq.assign_port(track_index=0, port_name="testport")

print("\n4. Adding Audio track...")
seq.add_track(name="audio_track", track_type='audio', filepath="test_audio.wav")

print("\n5. Listing tracks...")
track_list = seq.list_tracks()
print(track_list)

# Verification for step 5
assert "midi_track (MIDI)" in track_list
assert "audio_track (Audio)" in track_list
print("--> Verification PASSED")


print("\n6. Saving project...")
seq.save_project("test_project")

# Verification for step 6
assert os.path.exists("test_project.proj.json")
print("--> Verification PASSED")


# --- Second part: Load the project ---
print("\n7. Creating new sequencer and loading project...")
seq2 = Sequencer()
seq2.load_project("test_project")

print("\n8. Listing tracks from loaded project...")
track_list_2 = seq2.list_tracks()
print(track_list_2)

# Verification for step 8
assert "midi_track (MIDI)" in track_list_2
assert "audio_track (Audio)" in track_list_2
assert len(seq2.song.tracks) == 2
print("--> Verification PASSED")


print("\n9. Saving to MIDI file...")
# Suppress print statements from export_to_midi to keep output clean
# This is a bit of a hack, but necessary for automated checking
import sys
from io import StringIO
original_stdout = sys.stdout
sys.stdout = captured_output = StringIO()

seq2.save_song("test_export.mid")

sys.stdout = original_stdout
print(captured_output.getvalue().strip())

# Verification for step 9
assert "Warning: Skipping audio track 'audio_track' during MIDI export." in captured_output.getvalue()
assert os.path.exists("test_export.mid")
print("--> Verification PASSED")


print("\n--- Test Complete ---")
