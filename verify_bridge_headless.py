
import os
import sys
import time
import subprocess
import threading
import jack
import mido

# Add src to path
sys.path.append(os.path.join(os.getcwd(), 'src'))

from sequencer.sequencer import Sequencer
from sequencer.models import MidiTrack

def test_midi_bridge():
    print("Starting MIDI Bridge Verification Test...")

    # 1. Start a dummy JACK server in the background
    print("Starting dummy JACK server...")
    jackd_proc = subprocess.Popen(["jackd", "-r", "-d", "dummy"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2.0)

    try:
        # 2. Create Sequencer and add a MIDI track
        seq = Sequencer(gui_mode=False)
        seq.add_track("TestTrack", track_type='midi')

        # 3. Start Jack Manager
        print("Starting JackManager...")
        seq.jack_manager.start()

        # Wait for startup
        time.sleep(1.0)

        if not seq.jack_manager.is_running:
            print("FAIL: JackManager failed to start.")
            return

        client_name = seq.jack_manager.jack_client.name
        print(f"JackManager running as client: {client_name}")

        # 4. Create a test client to send and receive MIDI
        test_client = jack.Client("TestVerifier")
        in_port = test_client.midi_inports.register("input")
        out_port = test_client.midi_outports.register("output")

        received_events = []
        send_flag = [True]

        note_on = mido.Message('note_on', note=60, velocity=100)

        @test_client.set_process_callback
        def process(frames):
            if send_flag[0]:
                out_port.write_midi_event(0, note_on.bytes())
                send_flag[0] = False

            for offset, data in in_port.incoming_midi_events():
                msg = mido.Message.from_bytes(data)
                received_events.append(msg)
                print(f"SUCCESS: Received forwarded MIDI: {msg}")

        test_client.activate()

        # 5. Connect ports
        clavier_port = f"{client_name}:Clavier"
        track_out_port = f"{client_name}:out_0_TestTrack"

        print(f"Connecting {test_client.name}:output to {clavier_port}")
        test_client.connect(f"{test_client.name}:output", clavier_port)
        print(f"Connecting {track_out_port} to {test_client.name}:input")
        test_client.connect(track_out_port, f"{test_client.name}:input")

        # Wait for processing
        time.sleep(1.0)

        if received_events:
            print("\nTEST PASSED: MIDI Bridge correctly forwarded the message.")
        else:
            print("\nTEST FAILED: No MIDI message received.")
            if os.path.exists("/tmp/sequencer_rt.log"):
                with open("/tmp/sequencer_rt.log", "r") as f:
                    print("--- RT LOG ---")
                    print(f.read())

        seq.jack_manager.stop()
        test_client.close()
    finally:
        jackd_proc.terminate()
        jackd_proc.wait()

if __name__ == "__main__":
    test_midi_bridge()
