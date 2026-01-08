import json
import os
from typing import Dict, Any

class ConfigManager:
    """
    Manages loading and saving application-wide settings from a JSON file.
    """
    def __init__(self, config_path: str = 'config/settings.json'):
        """
        Initializes the ConfigManager.

        Args:
            config_path (str): The path to the configuration file.
        """
        self.config_path = config_path
        self.settings: Dict[str, Any] = {}
        self.load_settings()

    def _get_default_settings(self) -> Dict[str, Any]:
        """
        Returns a dictionary with the default settings.
        The user's home directory is used as the base for default paths.
        """
        home_dir = os.path.expanduser('~')
        return {
            'default_projects_dir': home_dir,
            'default_audio_files_dir': home_dir,
            'default_carla_project_file': '',
            'default_aj_snapshot_file': '',
        }

    def load_settings(self):
        """
        Loads settings from the JSON file. If the file doesn't exist,
        it initializes with default settings.
        """
        # Start with defaults
        self.settings = self._get_default_settings()
        try:
            with open(self.config_path, 'r') as f:
                loaded_data = json.load(f)
            # Update defaults with loaded data, preserving new defaults if the file is old
            self.settings.update(loaded_data)
        except FileNotFoundError:
            # File doesn't exist yet, will be created on first save.
            # The defaults are already set.
            pass
        except json.JSONDecodeError:
            print(f"Warning: Could not parse config file '{self.config_path}'. Using default settings.")
            # Keep the defaults if the file is corrupted.
            pass

    def save_settings(self):
        """
        Saves the current settings to the JSON file.
        """
        try:
            # Ensure the config directory exists
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, 'w') as f:
                json.dump(self.settings, f, indent=4)
        except IOError as e:
            print(f"Error: Could not save settings to '{self.config_path}': {e}")

    def get_setting(self, key: str, default: Any = None) -> Any:
        """
        Retrieves a setting value by key.

        Args:
            key (str): The key of the setting to retrieve.
            default (Any, optional): The default value to return if the key is not found.

        Returns:
            Any: The value of the setting.
        """
        return self.settings.get(key, default)

    def set_setting(self, key: str, value: Any):
        """
        Sets a setting value. Note: This does not automatically save.
        Call save_settings() to persist changes.

        Args:
            key (str): The key of the setting to set.
            value (Any): The value to set.
        """
        self.settings[key] = value
