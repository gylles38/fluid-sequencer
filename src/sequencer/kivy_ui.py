import kivy
kivy.require('2.3.1') # replace with your kivy version

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.textinput import TextInput
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.scrollview import ScrollView
from kivy.uix.popup import Popup

from sequencer.sequencer import Sequencer
import sys

class ConfirmationPopup(Popup):
    def __init__(self, prompt_text, callback, **kwargs):
        super(ConfirmationPopup, self).__init__(**kwargs)
        self.title = "Confirmation"
        self.size_hint = (0.8, 0.4)
        self.callback = callback

        layout = BoxLayout(orientation='vertical', padding=10, spacing=10)
        layout.add_widget(Label(text=prompt_text))
        self.text_input = TextInput(multiline=False)
        layout.add_widget(self.text_input)

        buttons_layout = BoxLayout(size_hint_y=None, height=50, spacing=10)
        ok_button = Button(text='OK')
        ok_button.bind(on_press=self.on_ok)
        cancel_button = Button(text='Cancel')
        cancel_button.bind(on_press=self.dismiss)
        buttons_layout.add_widget(ok_button)
        buttons_layout.add_widget(cancel_button)
        layout.add_widget(buttons_layout)

        self.content = layout

    def on_ok(self, instance):
        self.callback(self.text_input.text)
        self.dismiss()

class SequencerLayout(BoxLayout):
    def __init__(self, **kwargs):
        super(SequencerLayout, self).__init__(**kwargs)
        self.orientation = 'vertical'
        self.sequencer = Sequencer()
        self.current_command = ""

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
        import json
        from main import process_command

        self.current_command = command

        def confirmation_callback(user_input):
            # When the popup is answered, we append the answer to the command and process it again.
            full_command = f"{self.current_command} {user_input}"
            self.process_command_ui(full_command)

        # We now use api_mode to get structured JSON responses
        # and a custom confirmation handler for the GUI.
        should_continue, output = process_command(self.current_command, self.sequencer, api_mode=True, confirmation_handler=None)

        try:
            data = json.loads(output)
            if data.get("status") == "prompt":
                popup = ConfirmationPopup(prompt_text=data["message"], callback=confirmation_callback)
                popup.open()
            else:
                self.output_label.text += data.get("message", "") + "\n"
                self.current_command = "" # Reset after a final response
        except json.JSONDecodeError:
            # Not a JSON response, just print it
            self.output_label.text += output + "\n"
            self.current_command = "" # Reset after a non-JSON response


        if not should_continue:
            App.get_running_app().stop()


class SequencerApp(App):
    def build(self):
        return SequencerLayout()

    def on_stop(self):
        layout = self.root
        layout.sequencer.stop()
        layout.sequencer.close_virtual_ports()
