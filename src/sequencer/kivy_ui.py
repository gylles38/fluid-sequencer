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

class SaveDiscardCancelPopup(Popup):
    def __init__(self, prompt_text, callback, **kwargs):
        super(SaveDiscardCancelPopup, self).__init__(**kwargs)
        self.title = "Unsaved Changes"
        self.size_hint = (0.8, 0.4)
        self.callback = callback

        layout = BoxLayout(orientation='vertical', padding=10, spacing=10)
        layout.add_widget(Label(text=prompt_text))

        buttons_layout = BoxLayout(size_hint_y=None, height=50, spacing=10)
        save_button = Button(text='Save')
        save_button.bind(on_press=lambda instance: self.on_answer(instance, 's'))
        discard_button = Button(text='Discard')
        discard_button.bind(on_press=lambda instance: self.on_answer(instance, 'd'))
        cancel_button = Button(text='Cancel')
        cancel_button.bind(on_press=lambda instance: self.on_answer(instance, 'c'))
        buttons_layout.add_widget(save_button)
        buttons_layout.add_widget(discard_button)
        buttons_layout.add_widget(cancel_button)
        layout.add_widget(buttons_layout)

        self.content = layout

    def on_answer(self, instance, answer):
        self.callback(answer)
        self.dismiss()

class YesNoPopup(Popup):
    def __init__(self, prompt_text, callback, **kwargs):
        super(YesNoPopup, self).__init__(**kwargs)
        self.title = "Confirmation"
        self.size_hint = (0.8, 0.4)
        self.callback = callback

        layout = BoxLayout(orientation='vertical', padding=10, spacing=10)
        layout.add_widget(Label(text=prompt_text))

        buttons_layout = BoxLayout(size_hint_y=None, height=50, spacing=10)
        yes_button = Button(text='Yes')
        yes_button.bind(on_press=lambda instance: self.on_answer(instance, 'y'))
        no_button = Button(text='No')
        no_button.bind(on_press=lambda instance: self.on_answer(instance, 'n'))
        buttons_layout.add_widget(yes_button)
        buttons_layout.add_widget(no_button)
        layout.add_widget(buttons_layout)

        self.content = layout

    def on_answer(self, instance, answer):
        self.callback(answer)
        self.dismiss()

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

        # Status Display
        status_layout = BoxLayout(size_hint_y=None, height=30)
        self.song_name_label = Label(text="Song: New Song")
        self.tempo_label = Label(text="Tempo: 120 BPM")
        self.timesig_label = Label(text="Time Sig: 4/4")
        self.metronome_label = Label(text="Metronome: OFF")
        status_layout.add_widget(self.song_name_label)
        status_layout.add_widget(self.tempo_label)
        status_layout.add_widget(self.timesig_label)
        status_layout.add_widget(self.metronome_label)
        self.add_widget(status_layout)

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

        self.update_status_display()

    def update_status_display(self):
        song = self.sequencer.song
        self.song_name_label.text = f"Song: {song.name}"
        self.tempo_label.text = f"Tempo: {song.tempo} BPM"
        self.timesig_label.text = f"Time Sig: {song.time_signature_numerator}/{song.time_signature_denominator}"
        metro_status = "ON" if song.metronome_enabled else "OFF"
        self.metronome_label.text = f"Metronome: {metro_status}"

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
            # We wrap the user input in quotes to handle empty strings and strings with spaces.
            full_command = f'{self.current_command} "{user_input}"'
            self.process_command_ui(full_command)

        # We now use api_mode to get structured JSON responses
        # and a custom confirmation handler for the GUI.
        should_continue, output = process_command(self.current_command, self.sequencer, api_mode=True, confirmation_handler=None)

        try:
            data = json.loads(output)
            if data.get("status") == "prompt":
                prompt_message = data["message"]
                if "You have unsaved changes" in prompt_message:
                    popup = SaveDiscardCancelPopup(prompt_text=prompt_message, callback=confirmation_callback)
                elif "[y/n]" in prompt_message.lower():
                    popup = YesNoPopup(prompt_text=prompt_message, callback=confirmation_callback)
                else:
                    popup = ConfirmationPopup(prompt_text=prompt_message, callback=confirmation_callback)
                popup.open()
            else:
                self.output_label.text += data.get("message", "") + "\n"
                self.current_command = "" # Reset after a final response
        except (json.JSONDecodeError, TypeError):
            # Not a JSON response, just print it
            if output:
                self.output_label.text += output + "\n"
            self.current_command = "" # Reset after a non-JSON response

        self.update_status_display()

        if not should_continue:
            App.get_running_app().stop()


class SequencerApp(App):
    def build(self):
        return SequencerLayout()

    def on_stop(self):
        layout = self.root
        layout.sequencer.stop()
        layout.sequencer.close_virtual_ports()
