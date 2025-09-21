import kivy
kivy.require('2.3.1') # replace with your kivy version

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.textinput import TextInput
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.scrollview import ScrollView

from sequencer.sequencer import Sequencer
import sys

class SequencerLayout(BoxLayout):
    def __init__(self, **kwargs):
        super(SequencerLayout, self).__init__(**kwargs)
        self.orientation = 'vertical'
        self.sequencer = Sequencer()

        self.output_label = Label(size_hint_y=None, height=400)
        self.output_label.bind(texture_size=self.output_label.setter('size'))
        self.output_scroll = ScrollView(size_hint=(1, 0.8))
        self.output_scroll.add_widget(self.output_label)
        self.add_widget(self.output_scroll)

        self.input_text = TextInput(size_hint=(1, 0.1), multiline=False)
        self.input_text.bind(on_text_validate=self.on_enter)
        self.add_widget(self.input_text)

        self.send_button = Button(text='Send', size_hint=(1, 0.1))
        self.send_button.bind(on_press=self.on_enter)
        self.add_widget(self.send_button)

    def on_enter(self, instance):
        command = self.input_text.text
        self.input_text.text = ''
        self.process_command_ui(command)

    def process_command_ui(self, command):
        from main import process_command

        # TODO: Implement a proper confirmation handler for the GUI.
        # This will require a popup with a text input and buttons.
        # For now, commands that require confirmation will not work correctly.
        should_continue, output = process_command(command, self.sequencer, api_mode=False, confirmation_handler=None)

        self.output_label.text += output + "\n"
        if not should_continue:
            App.get_running_app().stop()


class SequencerApp(App):
    def build(self):
        return SequencerLayout()

    def on_stop(self):
        layout = self.root
        layout.sequencer.stop()
        layout.sequencer.close_virtual_ports()
