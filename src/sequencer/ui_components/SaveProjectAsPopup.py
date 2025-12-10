from kivy.uix.popup import Popup
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.filechooser import FileChooserListView
from kivy.metrics import dp
from sequencer.ui_components.TooltipMDIconButton import TooltipMDIconButton

class SaveProjectAsPopup(Popup):
    def __init__(self, sequencer, callback, **kwargs):
        super(SaveProjectAsPopup, self).__init__(**kwargs)
        self.title = "Save Project As"
        self.size_hint = (0.6, 0.6)
        self.sequencer = sequencer
        self.callback = callback

        layout = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(10))
        self.filechooser = FileChooserListView(path='.', filters=['*.proj.json'])
        layout.add_widget(self.filechooser)

        filename_layout = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(10))
        filename_layout.add_widget(Label(text="Filename:", halign='left', size_hint_x=None, width=dp(80)))
        self.filename_input = TextInput(
            text=self.sequencer.last_project_basename if self.sequencer.last_project_basename else "",
            multiline=False,
            size_hint_x=None,
            width=dp(200)
        )
        filename_layout.add_widget(self.filename_input)
        layout.add_widget(filename_layout)

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
        filepath = self.filechooser.path
        filename = self.filename_input.text
        if filename:
            if not filename.endswith('.proj.json'):
                filename += '.proj.json'
            full_path = f"{filepath}/{filename}"
            self.callback(full_path)
            self.dismiss()
