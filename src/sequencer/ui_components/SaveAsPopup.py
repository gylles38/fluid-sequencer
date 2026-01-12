from kivy.uix.popup import Popup
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.filechooser import FileChooserListView
from kivy.metrics import dp
from sequencer.ui_components.TooltipMDIconButton import TooltipMDIconButton
import os

class SaveAsPopup(Popup):
    def __init__(self, callback, title="Save As", default_filename="", filters=None, path='.', **kwargs):
        super(SaveAsPopup, self).__init__(**kwargs)
        self.title = title
        self.size_hint = (0.8, 0.8)
        self.callback = callback

        layout = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(10))

        # File chooser for directory selection
        self.filechooser = FileChooserListView(
            path=path,
            dirselect=True,  # Allow directory selection
            filters=filters if filters else []
        )
        layout.add_widget(self.filechooser)

        # Layout for filename input
        filename_layout = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(10))
        filename_layout.add_widget(Label(text="Filename:", size_hint_x=None, width=dp(80)))

        self.filename_input = TextInput(
            text=default_filename,
            multiline=False
        )
        filename_layout.add_widget(self.filename_input)
        layout.add_widget(filename_layout)

        # Buttons
        buttons_layout = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(10))
        save_button = TooltipMDIconButton(icon='content-save', tooltip_text='Save')
        save_button.bind(on_press=self.on_save)
        cancel_button = TooltipMDIconButton(icon='cancel', tooltip_text='Cancel')
        cancel_button.bind(on_press=self.dismiss)
        buttons_layout.add_widget(save_button)
        buttons_layout.add_widget(cancel_button)
        layout.add_widget(buttons_layout)

        self.content = layout

    def on_save(self, instance):
        # Prioritize the selected directory, fall back to the current path
        directory = self.filechooser.selection[0] if self.filechooser.selection else self.filechooser.path
        filename = self.filename_input.text.strip()

        if filename:
            # Construct the full path
            full_path = os.path.join(directory, filename)

            # Ensure the filename has the correct extension
            if self.filechooser.filters and not any(filename.endswith(f) for f in self.filechooser.filters):
                 # This is a simplification; for multiple filters like ['*.mid', '*.midi'], it just picks the first.
                 # A more robust implementation might check the filter or have a dropdown.
                 if self.filechooser.filters[0] != "*":
                    ext = self.filechooser.filters[0].replace('*.', '.')
                    if not full_path.endswith(ext):
                        full_path += ext

            self.callback(full_path)
            self.dismiss()
