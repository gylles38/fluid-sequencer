import kivy
kivy.require('2.3.1') # replace with your kivy version

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.textinput import TextInput
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.dropdown import DropDown
from kivy.uix.scrollview import ScrollView
from kivy.uix.popup import Popup
from kivy.uix.filechooser import FileChooserListView

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

class LoopPopup(Popup):
    def __init__(self, sequencer, callback, **kwargs):
        super(LoopPopup, self).__init__(**kwargs)
        self.title = "Set Loop Range"
        self.size_hint = (0.8, 0.5)
        self.sequencer = sequencer
        self.callback = callback

        layout = BoxLayout(orientation='vertical', padding=10, spacing=10)

        # Start Position
        start_layout = BoxLayout(size_hint_y=None, height=30)
        start_layout.add_widget(Label(text="Start (measure:beat):"))
        self.start_input = TextInput(text="1:1", multiline=False)
        start_layout.add_widget(self.start_input)
        layout.add_widget(start_layout)

        # End Position
        end_layout = BoxLayout(size_hint_y=None, height=30)
        end_layout.add_widget(Label(text="End (measure:beat):"))
        end_of_song = self.sequencer._format_beats_to_position(self.sequencer.get_song_length_in_beats())
        self.end_input = TextInput(text=end_of_song, multiline=False)
        end_layout.add_widget(self.end_input)
        layout.add_widget(end_layout)

        # Buttons
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
        self.callback(self.start_input.text, self.end_input.text)
        self.dismiss()

class FileChooserPopup(Popup):
    def __init__(self, callback, title="Select File", filters=None, **kwargs):
        super(FileChooserPopup, self).__init__(**kwargs)
        self.title = title
        self.size_hint = (0.9, 0.9)
        self.callback = callback

        layout = BoxLayout(orientation='vertical', padding=10, spacing=10)

        self.filechooser = FileChooserListView(filters=filters or [])
        layout.add_widget(self.filechooser)

        buttons_layout = BoxLayout(size_hint_y=None, height=50, spacing=10)
        select_button = Button(text='Select')
        select_button.bind(on_press=self.on_select)
        cancel_button = Button(text='Cancel')
        cancel_button.bind(on_press=self.dismiss)
        buttons_layout.add_widget(select_button)
        buttons_layout.add_widget(cancel_button)
        layout.add_widget(buttons_layout)

        self.content = layout

    def on_select(self, instance):
        if self.filechooser.selection:
            self.callback(self.filechooser.selection[0])
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
        self.sequencer = Sequencer(gui_mode=True)
        self.current_command = ""
        self.end_pos_manual_override = False

        # Menu Bar
        menu_bar = BoxLayout(size_hint_y=None, height=30)
        file_button = Button(text='File', size_hint_x=None, width=100)
        self.file_dropdown = DropDown()

        btn_new = Button(text='New Project', size_hint_y=None, height=44)
        btn_new.bind(on_release=lambda x: (self.file_dropdown.dismiss(), self.new_project_popup()))
        self.file_dropdown.add_widget(btn_new)

        btn_load = Button(text='Load Project', size_hint_y=None, height=44)
        btn_load.bind(on_release=lambda x: (self.file_dropdown.dismiss(), self.load_project_popup()))
        self.file_dropdown.add_widget(btn_load)

        btn_save = Button(text='Save Project', size_hint_y=None, height=44)
        btn_save.bind(on_release=lambda x: (self.file_dropdown.dismiss(), self.save_project()))
        self.file_dropdown.add_widget(btn_save)

        btn_save_as = Button(text='Save Project As...', size_hint_y=None, height=44)
        btn_save_as.bind(on_release=lambda x: (self.file_dropdown.dismiss(), self.save_project_as_popup()))
        self.file_dropdown.add_widget(btn_save_as)

        btn_quit = Button(text='Quit', size_hint_y=None, height=44)
        btn_quit.bind(on_release=lambda x: self.process_command_ui('quit'))
        self.file_dropdown.add_widget(btn_quit)

        file_button.bind(on_release=self.file_dropdown.open)
        menu_bar.add_widget(file_button)
        self.add_widget(menu_bar)

        # Status Display
        status_layout = BoxLayout(size_hint_y=None, height=30)
        self.song_name_label = Label(text="Song: New Song")
        self.tempo_label = Label(text="Tempo: 120 BPM")
        self.timesig_label = Label(text="Time Sig: 4/4")
        self.metronome_label = Label(text="Metronome: OFF")
        self.playhead_label = Label(text="Position: 1:1")
        status_layout.add_widget(self.song_name_label)
        status_layout.add_widget(self.tempo_label)
        status_layout.add_widget(self.timesig_label)
        status_layout.add_widget(self.metronome_label)
        status_layout.add_widget(self.playhead_label)
        self.add_widget(status_layout)

        # Transport Controls
        transport_layout = BoxLayout(size_hint_y=None, height=40, spacing=5, padding=5)
        transport_layout.add_widget(Label(text='Start:', size_hint_x=0.1))
        self.start_pos_input = TextInput(text='1:1', multiline=False, size_hint_x=0.2)
        transport_layout.add_widget(self.start_pos_input)
        transport_layout.add_widget(Label(text='End:', size_hint_x=0.1))
        self.end_pos_input = TextInput(text='', multiline=False, size_hint_x=0.2)
        self.end_pos_input.bind(on_text_validate=self.on_end_pos_manual_set)
        transport_layout.add_widget(self.end_pos_input)

        play_button = Button(text='Play', on_press=self.play_pressed)
        loop_button = Button(text='Loop', on_press=self.loop_pressed)
        pause_button = Button(text='Pause', on_press=lambda x: self.process_command_ui('pause'))
        stop_button = Button(text='Stop', on_press=lambda x: self.process_command_ui('stop'))
        record_button = Button(text='Record', on_press=lambda x: self.process_command_ui('record'))

        transport_layout.add_widget(play_button)
        transport_layout.add_widget(loop_button)
        transport_layout.add_widget(pause_button)
        transport_layout.add_widget(stop_button)
        transport_layout.add_widget(record_button)
        self.add_widget(transport_layout)

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
        self.sequencer.bind(current_beat=self.update_playhead_display)

    def on_end_pos_manual_set(self, instance):
        if instance.text:
            self.end_pos_manual_override = True

    def load_project_popup(self):
        def callback(filepath):
            import os
            if filepath:
                # We need the basename without extension for the command
                basename = os.path.basename(filepath).removesuffix('.proj.json')
                self.end_pos_manual_override = False # Reset the flag
                self.process_command_ui(f'loadproject "{basename}"')
        popup = FileChooserPopup(
            callback=callback,
            title="Load Project",
            filters=['*.proj.json']
        )
        popup.open()

    def new_project_popup(self):
        def callback(name):
            if name:
                self.end_pos_manual_override = False # Reset the flag
                self.process_command_ui(f'newproject "{name}"')
        popup = ConfirmationPopup(prompt_text="Enter new project name:", callback=callback)
        popup.open()

    def save_project(self):
        if self.sequencer.last_project_basename:
            self.process_command_ui(f'saveproject "{self.sequencer.last_project_basename}"')
        else:
            self.save_project_as_popup()

    def save_project_as_popup(self):
        def callback(basename):
            if basename:
                self.process_command_ui(f'saveproject "{basename}"')
        popup = ConfirmationPopup(prompt_text="Enter project basename to save:", callback=callback)
        popup.open()

    def play_pressed(self, instance):
        start_pos = self.start_pos_input.text
        end_pos = self.end_pos_input.text
        if end_pos:
            self.end_pos_manual_override = True
        command = f'play "{start_pos}" "{end_pos}"'
        self.process_command_ui(command)

    def loop_pressed(self, instance):
        start_pos = self.start_pos_input.text
        end_pos = self.end_pos_input.text
        if end_pos:
            self.end_pos_manual_override = True
        command = f'loop "{start_pos}" "{end_pos}"'
        self.process_command_ui(command)

    def update_playhead_display(self, instance, value):
        self.playhead_label.text = f"Position: {self.sequencer._format_beats_to_position(value)}"

    def update_status_display(self):
        song = self.sequencer.song
        self.song_name_label.text = f"Song: {song.name}"
        self.tempo_label.text = f"Tempo: {song.tempo} BPM"
        self.timesig_label.text = f"Time Sig: {song.time_signature_numerator}/{song.time_signature_denominator}"
        metro_status = "ON" if song.metronome_enabled else "OFF"
        self.metronome_label.text = f"Metronome: {metro_status}"

        # Update end position input, but only if the user hasn't manually set it.
        if not self.end_pos_manual_override:
            end_of_song_beats = self.sequencer.get_song_length_in_beats()
            self.end_pos_input.text = self.sequencer._format_beats_to_position(end_of_song_beats)

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
            if data.get("status") == "file_chooser_prompt":
                def file_chooser_callback(filepath):
                    track_name = data["track_name"]
                    full_command = f'addaudio "{track_name}" "{filepath}"'
                    self.process_command_ui(full_command)
                popup = FileChooserPopup(callback=file_chooser_callback, title="Select Audio File")
                popup.open()
            elif data.get("status") == "loop_prompt":
                def loop_callback(start, end):
                    full_command = f'loop "{start}" "{end}"'
                    self.process_command_ui(full_command)
                popup = LoopPopup(sequencer=self.sequencer, callback=loop_callback)
                popup.open()
            elif data.get("status") == "prompt":
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
