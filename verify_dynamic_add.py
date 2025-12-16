import time
import psutil
from sequencer.sequencer import Sequencer

def check_for_mpv_process(filepath):
    """Check if an mpv process is running for the given filepath."""
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if proc.info['name'] == 'mpv' and filepath in proc.info['cmdline']:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return False

# 1. Create a sequencer instance
seq = Sequencer()

# 2. Start the JackManager
print("Starting JackManager...")
seq.jack_manager.start()
time.sleep(1) # Give it a moment to initialize

if not seq.jack_manager.is_running:
    print("Error: JackManager failed to start.")
    exit(1)

# 3. Simulate starting playback
print("Starting playback...")
seq.play()
time.sleep(1)

# 4. Add a new audio track
audio_file = "projects/dummy.wav"
print(f"Adding new audio track: {audio_file}")
result = seq.add_track(name="New Drums", track_type="audio", filepath=audio_file)
print(f"Result of add_track: {result}")

# Wait a bit for the process to be launched
time.sleep(2)

# 5. Verify that the mpv process was launched for the new track
print("Verifying mpv process...")
if check_for_mpv_process(audio_file):
    print("SUCCESS: mpv process for the new track was found.")
else:
    print("FAILURE: mpv process for the new track was NOT found.")

# Clean up
print("Stopping sequencer...")
seq.stop()
time.sleep(1)
print("Verification complete.")
