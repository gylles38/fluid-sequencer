from kivy.uix.popup import Popup
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.metrics import dp
from sequencer.ui_components.TooltipMDIconButton import TooltipMDIconButton

class LoopPopup(Popup):
    def __init__(self, sequencer, callback, **kwargs):
        super(LoopPopup, self).__init__(**kwargs)
        self.title = "Set Loop Range"
        self.size_hint = (0.5, 0.4)
        self.sequencer = sequencer
        self.callback = callback

        layout = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(10))
        start_layout = BoxLayout(size_hint_y=None, height=dp(30))
        start_layout.add_widget(Label(text="Start (measure:beat):"))
        self.start_input = TextInput(text="1:1", multiline=False, size_hint_x=None, width=dp(100))
        start_layout.add_widget(self.start_input)
        layout.add_widget(start_layout)

        end_layout = BoxLayout(size_hint_y=None, height=dp(30))
        end_layout.add_widget(Label(text="End (measure:beat):"))
        end_of_song = self.sequencer._format_beats_to_position(self.sequencer.get_song_length_in_beats())
        self.end_input = TextInput(text=end_of_song, multiline=False, size_hint_x=None, width=dp(100))
        end_layout.add_widget(self.end_input)
        layout.add_widget(end_layout)

        buttons_layout = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(10))
        ok_button = TooltipMDIconButton(icon='check', tooltip_text='OK')
        ok_button.bind(on_press=self.on_ok)
        cancel_button = TooltipMDIconButton(icon='cancel', tooltip_text='Cancel')
        cancel_button.bind(on_press=self.dismiss)
        buttons_layout.add_widget(ok_button)
        buttons_layout.add_widget(cancel_button)
        layout.add_widget(buttons_layout)
        self.content = layout

    def on_ok(self, instance):
        self.callback(self.start_input.text, self.end_input.text)
        self.dismiss()
