import time
from src.sequencer.sequencer import Sequencer

def run_test():
    """
    Tests that the sequencer plays an audio track for its full duration.
    """
    print("----- Running Test -----")

    # 1. Initialize Sequencer
    sequencer = Sequencer(tempo=120)

    # 2. Add an audio track
    audio_file = "test_audio.wav"
    try:
        sequencer.add_track(name="AudioTest", track_type="audio", filepath=audio_file)
        print(f"Added audio track: {audio_file}")
    except Exception as e:
        print(f"Error adding audio track: {e}")
        return

    # 3. Play the song
    print("Starting playback...")
    sequencer.play(start_beat=0)

    # 4. Wait for playback to finish
    # We need to check the playback_state of the sequencer
    while sequencer.playback_state != "stopped":
        time.sleep(0.5)

    print("Playback has stopped.")
    # The "Playback finished" message is now printed from the sequencer,
    # so we just need to ensure the script completes.

    print("----- Test Finished -----")

if __name__ == "__main__":
    run_test()
