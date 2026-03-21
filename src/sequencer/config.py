import json
import os

DEFAULT_MIDI_MAPPINGS = {
    "transport": {
        "rewind": 115,
        "forward": 116,
        "stop": 117,
        "play_pause": 118,
        "record_arm": 119,
        "loop": 114,
        "panic": 113
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

    def get_mapping(self, category, parameter):
        """Generic getter for any mapping."""
        val = self.mappings.get(category)
        if isinstance(val, dict):
            return val.get(parameter)
        elif isinstance(val, list):
            try:
                idx = int(parameter)
                if 0 <= idx < len(val):
                    return val[idx]
            except (ValueError, TypeError):
                pass
        return None

    def update_mapping(self, category, parameter, cc_value):
        """Updates or adds a mapping."""
        if category not in self.mappings:
            self.mappings[category] = {}

        target = self.mappings[category]
        if isinstance(target, dict):
            target[parameter] = cc_value
        elif isinstance(target, list):
            try:
                idx = int(parameter)
                while len(target) <= idx:
                    target.append(None)
                target[idx] = cc_value
            except (ValueError, TypeError):
                # If we can't use an index on a list, we might have a problem.
                # For now, let's just ignore or convert to dict if really needed.
                pass

    def save_mappings(self, filepath=None):
        """Saves current mappings to a JSON file."""
        target_path = filepath or self.filepath
        if not target_path:
            # If no filepath was provided during init and none provided now
            target_path = "config/midi_mappings.json"

        try:
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            with open(target_path, 'w') as f:
                json.dump(self.mappings, f, indent=4)
            print(f"MIDI mappings saved to {target_path}")
            self.filepath = target_path
            return True
        except Exception as e:
            print(f"Error saving MIDI mappings: {e}")
            return False

midi_config = MidiConfig()
