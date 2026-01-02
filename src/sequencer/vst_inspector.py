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
            min_val = getattr(p, 'min_value', 0.0)
            max_val = getattr(p, 'max_value', 1.0)

            # More robust fallback for invalid ranges, including non-numeric types
            is_min_valid = isinstance(min_val, (int, float))
            is_max_valid = isinstance(max_val, (int, float))

            if not is_min_valid or not is_max_valid or max_val <= min_val:
                min_val, max_val = 0.0, 1.0

            # Ensure value and default_value are also valid numbers
            value = getattr(p, 'value', 0.5)
            if not isinstance(value, (int, float)):
                value = 0.5

            default_value = getattr(p, 'default_value', 0.5)
            if not isinstance(default_value, (int, float)):
                default_value = 0.5

            parameters[name] = {
                "value": getattr(p, 'value', 0.5),
                "default_value": getattr(p, 'default_value', 0.5),
                "min_value": min_val,
                "max_value": max_val,
            }
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
