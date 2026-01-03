import sys
import json
from pedalboard import VST3Plugin
import warnings

# Suppress all warnings, including potential DeprecationWarnings from pedalboard
warnings.filterwarnings("ignore")

def get_plugin_parameters(plugin_path):
    """
    Loads a VST3 plugin and returns its parameters as a dictionary.
    This function is designed to be called from a separate process
    to isolate potential crashes from the main application.
    """
    try:
        # pedalboard can be noisy with warnings on some plugins; redirect stdout/stderr
        # This is a bit of a heavy-handed approach, but necessary for some plugins
        # that print directly to stdout/stderr instead of using Python's warning system.
        import os

        # Note: We can't truly silence C-level stdout/stderr without more complex
        # maneuvers like os.dup2, which can be risky. We rely on the parent
        # process to ignore this output if needed.

        plugin = VST3Plugin(plugin_path)

        parameters = {}
        for name, p in plugin.parameters.items():
            # Correctly get the current value from the plugin instance itself
            current_value = getattr(plugin, name)

            param_info = { "type": None, "value": current_value }

            # Type 1: Boolean (On/Off Switch)
            if isinstance(current_value, bool):
                param_info["type"] = "boolean"
                param_info["default_value"] = getattr(p, 'default_value', False)

            # Type 2: Choice (Dropdown Menu)
            elif hasattr(p, 'choices') and p.choices:
                param_info["type"] = "choice"
                param_info["choices"] = p.choices

                # Safely get default_value, falling back to the first choice
                default_value = getattr(p, 'default_value', p.choices[0])
                if not isinstance(default_value, str):
                    default_value = p.choices[int(default_value)] if isinstance(default_value, (int, float)) and 0 <= int(default_value) < len(p.choices) else p.choices[0]
                param_info["default_value"] = default_value

                # Ensure the current value is also a valid string from the list
                if not isinstance(current_value, str):
                    param_info["value"] = p.choices[int(current_value)] if isinstance(current_value, (int, float)) and 0 <= int(current_value) < len(p.choices) else p.choices[0]

            # Type 3: Float (Slider)
            else:
                param_info["type"] = "float"
                min_val, max_val = p.min_value, p.max_value

                is_min_valid = isinstance(min_val, (int, float))
                is_max_valid = isinstance(max_val, (int, float))

                if not is_min_valid or not is_max_valid or max_val <= min_val:
                    min_val, max_val = 0.0, 1.0

                param_info["min_value"] = min_val
                param_info["max_value"] = max_val

                # Safely get default_value, falling back to min_value
                default_value = getattr(p, 'default_value', min_val)
                if not isinstance(default_value, (int, float)):
                    default_value = min_val
                param_info["default_value"] = default_value

                if not isinstance(param_info["value"], (int, float)):
                    param_info["value"] = default_value

            parameters[name] = param_info

        return parameters
    except Exception as e:
        # If any error occurs during loading, print it to stderr
        # The parent process will interpret a non-empty stderr as failure.
        print(f"Error loading plugin {plugin_path}: {e}", file=sys.stderr)
        return None

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python vst_inspector.py <path_to_vst3>", file=sys.stderr)
        sys.exit(1)

    plugin_path = sys.argv[1]
    params = get_plugin_parameters(plugin_path)

    if params is not None:
        # On success, print the JSON representation of the parameters to stdout
        print(json.dumps(params))
        sys.exit(0)
    else:
        # On failure, exit with a non-zero status code
        sys.exit(1)
