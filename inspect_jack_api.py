
import jack
import sys

def inspect_jack():
    try:
        client = jack.Client("Inspect")
        in_port = client.midi_inports.register("test_in")
        out_port = client.midi_outports.register("test_out")

        print(f"In Port type: {type(in_port)}")
        print(f"In Port dir: {dir(in_port)}")

        print(f"Out Port type: {type(out_port)}")
        print(f"Out Port dir: {dir(out_port)}")

        client.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    inspect_jack()
