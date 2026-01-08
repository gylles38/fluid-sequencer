from kivymd.app import MDApp
from kivy.uix.popup import Popup
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivymd.uix.button import MDButton, MDButtonText
from kivy.metrics import dp
from sequencer.ui_components.FileChooserPopup import FileChooserPopup
import os

class PreferencesPopup(Popup):
    def __init__(self, sequencer, **kwargs):
        super(PreferencesPopup, self).__init__(**kwargs)
        self.sequencer = sequencer
        self.config_manager = sequencer.config_manager
        self.title = "Preferences"
        self.size_hint = (0.8, 0.7)
        self.auto_dismiss = False

        self.path_inputs = {}

        # Main layout
        content = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(15))

        # Grid for settings
        settings_grid = GridLayout(cols=3, spacing=dp(10), size_hint_y=1)

        # --- Define settings to be displayed ---
        settings = {
            'default_projects_dir': 'Default Projects Directory',
            'default_audio_files_dir': 'Default Audio Files Directory',
            'default_carla_project_file': 'Default Carla Project File',
            'default_aj_snapshot_file': 'Default JACK Snapshot File'
        }

        # --- Create a row for each setting ---
        for key, display_name in settings.items():
            # Label
            settings_grid.add_widget(Label(text=display_name, size_hint_x=0.3))

            # TextInput
            path_input = TextInput(
                text=self.config_manager.get_setting(key, default=''),
                size_hint_x=0.6
            )
            self.path_inputs[key] = path_input
            settings_grid.add_widget(path_input)

            # Browse Button
            browse_button = MDButton(
                MDButtonText(text="Browse"),
                size_hint_x=0.1
            )
            browse_button.bind(on_release=lambda x, k=key, i=path_input: self.open_path_chooser(k, i))
            settings_grid.add_widget(browse_button)

        content.add_widget(settings_grid)

        # --- Save/Cancel Buttons ---
        buttons_layout = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(10))
        save_button = MDButton(MDButtonText(text="Save"))
        save_button.bind(on_press=self.on_save)
        cancel_button = MDButton(MDButtonText(text="Cancel"))
        cancel_button.bind(on_press=self.dismiss)

        buttons_layout.add_widget(save_button)
        buttons_layout.add_widget(cancel_button)
        content.add_widget(buttons_layout)

        self.content = content

    def open_path_chooser(self, config_key, text_input):
        """Opens a directory or file chooser based on the config key."""

        is_dir_chooser = config_key.endswith('_dir')
        filters = []
        if config_key == 'default_carla_project_file':
            filters = ['*.carxp']
        elif config_key == 'default_aj_snapshot_file':
            filters = ['*.ajs']

        def callback(path):
            if path: # For both files and directories, path will be a string
                text_input.text = path

        # Use the directory of the current file path as the starting path for the chooser
        start_path = text_input.text
        if not is_dir_chooser and start_path and os.path.exists(start_path):
            start_path = os.path.dirname(start_path)
        if not os.path.isdir(start_path):
             start_path = self.config_manager.get_setting('default_projects_dir')


        popup = FileChooserPopup(
            callback=callback,
            title=f"Select {'Directory' if is_dir_chooser else 'File'} for {config_key}",
            path=start_path,
            dirselect=is_dir_chooser,
            filters=filters
        )
        popup.open()


    def on_save(self, instance):
        """Saves the settings from the text inputs."""
        for key, text_input in self.path_inputs.items():
            self.config_manager.set_setting(key, text_input.text)

        self.config_manager.save_settings()
        app = MDApp.get_running_app()
        if hasattr(app, 'root') and hasattr(app.root, 'show_info_popup'):
            app.root.show_info_popup("Success", "Preferences saved successfully.")
        self.dismiss()
