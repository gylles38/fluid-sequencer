import json
import os

DEFAULT_MIDI_MAPPINGS = {
    "transport": {
        "stop": 117,
        "play_pause": 118,
        "record_arm": 119
    },
    "volume_sliders": [70, 71, 72, 73, 74, 75, 76, 77],
    "track_solo_buttons": [65, 30, 31, 62, 80, 81, 82, 83]
}

class MidiConfig:
    def __init__(self, filepath=None):
        self.filepath = filepath
        self.mappings = DEFAULT_MIDI_MAPPINGS.copy()
        if filepath:
            self.load_mappings(filepath)

    def load_mappings(self, filepath):
        try:
            with open(filepath, 'r') as f:
                self.mappings = json.load(f)
            self.filepath = filepath
            print(f"MIDI mappings loaded from {filepath}")
        except FileNotFoundError:
            print(f"Warning: MIDI mapping file not found at {filepath}. Using default mappings.")
            self.mappings = DEFAULT_MIDI_MAPPINGS.copy()
        except json.JSONDecodeError:
            print(f"Warning: Invalid JSON in {filepath}. Using default mappings.")
            self.mappings = DEFAULT_MIDI_MAPPINGS.copy()

    def get_transport_cc(self, control_name):
        return self.mappings.get("transport", {}).get(control_name)

    def get_volume_slider_cc(self, track_index):
        if "volume_sliders" in self.mappings and 0 <= track_index < len(self.mappings["volume_sliders"]):
            return self.mappings["volume_sliders"][track_index]
        return None

    def get_track_solo_button_cc(self, track_index):
        if "track_solo_buttons" in self.mappings and 0 <= track_index < len(self.mappings["track_solo_buttons"]):
            return self.mappings["track_solo_buttons"][track_index]
        return None

midi_config = MidiConfig()
