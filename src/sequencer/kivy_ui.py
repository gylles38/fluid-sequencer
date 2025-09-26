import kivy
kivy.require('2.3.1') # replace with your kivy version

from kivymd.app import MDApp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.textinput import TextInput
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.dropdown import DropDown
from kivy.uix.scrollview import ScrollView
from kivy.uix.popup import Popup
from kivy.uix.filechooser import FileChooserListView
from kivy.uix.slider import Slider
from kivymd.uix.slider import MDSlider
from kivy.uix.togglebutton import ToggleButton
from kivy.uix.widget import Widget
from kivymd.uix.button import MDIconButton, MDRaisedButton
from kivymd.uix.tooltip import MDTooltip
from kivymd.uix.menu import MDDropdownMenu
from kivymd.uix.list import OneLineIconListItem

from sequencer.sequencer import Sequencer
from sequencer.models import MidiTrack, AudioTrack, AutomationTrack
import sys

class TooltipMDIconButton(MDIconButton, MDTooltip):
    pass

class DraggableSlider(MDSlider):
    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            # Find the parent ScrollView and disable scrolling
            parent = self.parent
            while parent and not isinstance(parent, ScrollView):
                parent = parent.parent
            if parent:
                parent.do_scroll_y = False
        return super(DraggableSlider, self).on_touch_down(touch)

    def on_touch_up(self, touch):
        # Find the parent ScrollView and re-enable scrolling
        parent = self.parent
        while parent and not isinstance(parent, ScrollView):
            parent = parent.parent
        if parent:
            parent.do_scroll_y = True
        return super(DraggableSlider, self).on_touch_up(touch)

class SaveDiscardCancelPopup(Popup):
    def __init__(self, prompt_text, callback, **kwargs):
        super(SaveDiscardCancelPopup, self).__init__(**kwargs)
        self.title = "Unsaved Changes"
        self.size_hint = (0.8, 0.4)
        self.callback = callback

        layout = BoxLayout(orientation='vertical', padding=10, spacing=10)
        layout.add_widget(Label(text=prompt_text))

        buttons_layout = BoxLayout(size_hint_y=None, height=50, spacing=10)
        save_button = TooltipMDIconButton(icon='content-save', tooltip_text='Save')
        save_button.bind(on_press=lambda instance: self.on_answer(instance, 's'))
        discard_button = TooltipMDIconButton(icon='delete', tooltip_text='Discard')
        discard_button.bind(on_press=lambda instance: self.on_answer(instance, 'd'))
        cancel_button = TooltipMDIconButton(icon='cancel', tooltip_text='Cancel')
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
        select_button = TooltipMDIconButton(icon='check', tooltip_text='Select')
        select_button.bind(on_press=self.on_select)
        cancel_button = TooltipMDIconButton(icon='cancel', tooltip_text='Cancel')
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
        yes_button = TooltipMDIconButton(icon='check', tooltip_text='Yes')
        yes_button.bind(on_press=lambda instance: self.on_answer(instance, 'y'))
        no_button = TooltipMDIconButton(icon='cancel', tooltip_text='No')
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
        ok_button = TooltipMDIconButton(icon='check', tooltip_text='OK')
        ok_button.bind(on_press=self.on_ok)
        cancel_button = TooltipMDIconButton(icon='cancel', tooltip_text='Cancel')
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
        menu_bar = BoxLayout(size_hint_y=None, height=40, padding=5)
        file_button = MDRaisedButton(text='File', pos_hint={'center_y': 0.5})

        menu_items = [
            {"viewclass": "OneLineIconListItem", "text": "New Project", "on_release": lambda: self.new_project_popup(), "leading_icon": "file-plus"},
            {"viewclass": "OneLineIconListItem", "text": "Load Project", "on_release": lambda: self.load_project_popup(), "leading_icon": "folder-open"},
            {"viewclass": "OneLineIconListItem", "text": "Save Project", "on_release": lambda: self.save_project(), "leading_icon": "content-save"},
            {"viewclass": "OneLineIconListItem", "text": "Save Project As...", "on_release": lambda: self.save_project_as_popup(), "leading_icon": "content-save-edit"},
            {"viewclass": "OneLineIconListItem", "text": "Quit", "on_release": lambda: self.process_command_ui('quit'), "leading_icon": "exit-to-app"},
        ]
        self.file_menu = MDDropdownMenu(
            caller=file_button,
            items=menu_items,
            width_mult=4,
        )
        file_button.bind(on_release=lambda x: self.file_menu.open())
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

        play_button = TooltipMDIconButton(icon='play', tooltip_text='Play', on_press=self.play_pressed)
        loop_button = TooltipMDIconButton(icon='repeat', tooltip_text='Loop', on_press=self.loop_pressed)
        pause_button = TooltipMDIconButton(icon='pause', tooltip_text='Pause', on_press=lambda x: self.process_command_ui('pause'))
        stop_button = TooltipMDIconButton(icon='stop', tooltip_text='Stop', on_press=lambda x: self.process_command_ui('stop'))
        record_button = TooltipMDIconButton(icon='record', tooltip_text='Record', on_press=lambda x: self.process_command_ui('record'))

        transport_layout.add_widget(play_button)
        transport_layout.add_widget(loop_button)
        transport_layout.add_widget(pause_button)
        transport_layout.add_widget(stop_button)
        transport_layout.add_widget(record_button)
        self.add_widget(transport_layout)

        # Track List (Mixer)
        self.track_list_layout = BoxLayout(orientation='vertical', size_hint_y=None)
        self.track_list_layout.bind(minimum_height=self.track_list_layout.setter('height'))
        track_scroll_view = ScrollView(size_hint=(1, 0.8))
        track_scroll_view.add_widget(self.track_list_layout)
        self.add_widget(track_scroll_view)

        self.output_label = Label(size_hint_y=0.1, text="Welcome!") # For general feedback
        self.add_widget(self.output_label)

        self.input_text = TextInput(size_hint=(1, 0.1), multiline=False)
        self.input_text.bind(on_text_validate=self.on_enter)
        self.add_widget(self.input_text)

        self.send_button = TooltipMDIconButton(icon='send', tooltip_text='Send', size_hint=(1, 0.1))
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

    def update_track_list(self):
        self.track_list_layout.clear_widgets()
        for i, track in enumerate(self.sequencer.song.tracks):
            track_widget = TrackWidget(track=track, track_index=i, sequencer_layout=self)
            self.track_list_layout.add_widget(track_widget)

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

        self.update_track_list()

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
            MDApp.get_running_app().stop()


class SequencerApp(MDApp):
    def build(self):
        self.theme_cls.theme_style = "Dark"
        self.theme_cls.primary_palette = "BlueGray"
        return SequencerLayout()

    def on_stop(self):
        layout = self.root
        layout.sequencer.stop()
        layout.sequencer.close_virtual_ports()

class TrackWidget(BoxLayout):
    def __init__(self, track, track_index, sequencer_layout, **kwargs):
        super(TrackWidget, self).__init__(**kwargs)
        self.track = track
        self.track_index = track_index
        self.sequencer_layout = sequencer_layout
        self.orientation = 'horizontal'
        self.size_hint_y = None
        self.height = 100

        # --- Column 1: Track Info ---
        info_layout = BoxLayout(orientation='vertical', size_hint_x=0.4)
        name_label = Label(text=f"[{track_index}] {track.name}")

        track_type_text = "Unknown"
        if isinstance(track, MidiTrack):
            track_type_text = "Type: MIDI"
        elif isinstance(track, AudioTrack):
            track_type_text = "Type: Audio"
        elif isinstance(track, AutomationTrack):
            track_type_text = "Type: Automation"
        type_label = Label(text=track_type_text, font_size='12sp', color=(0.8, 0.8, 0.8, 1))

        info_layout.add_widget(name_label)
        info_layout.add_widget(type_label)
        self.add_widget(info_layout)

        # --- Column 2: Volume & Pan ---
        slider_layout = BoxLayout(orientation='horizontal', size_hint_x=0.2)
        volume_slider = DraggableSlider(orientation='vertical', min=0, max=1, value=track.volume)
        volume_slider.bind(value=self.on_volume_change)
        pan_slider = DraggableSlider(orientation='vertical', min=-1, max=1, value=track.pan)
        pan_slider.bind(value=self.on_pan_change)
        slider_layout.add_widget(volume_slider)
        slider_layout.add_widget(pan_slider)
        self.add_widget(slider_layout)

        # --- Column 3: Mute & Solo ---
        buttons_layout = BoxLayout(orientation='horizontal', size_hint_x=0.1)
        mute_button = TooltipMDIconButton(
            icon='volume-off' if track.is_muted else 'volume-high',
            tooltip_text='Mute',
            on_press=self.on_mute_toggle
        )
        solo_button = TooltipMDIconButton(
            icon='alpha-s-box' if track.is_solo else 'alpha-s-box-outline',
            tooltip_text='Solo',
            on_press=self.on_solo_toggle
        )
        buttons_layout.add_widget(mute_button)
        buttons_layout.add_widget(solo_button)
        self.add_widget(buttons_layout)

        # --- Column 4: MIDI Controls or Spacer ---
        midi_controls_placeholder = BoxLayout(orientation='vertical', size_hint_x=0.3)
        if isinstance(track, MidiTrack):
            # Channel
            ch_layout = BoxLayout()
            ch_layout.add_widget(Label(text='Ch:'))
            channel_input = TextInput(text=str(track.channel + 1), multiline=False)
            channel_input.bind(on_text_validate=self.on_channel_change)
            ch_layout.add_widget(channel_input)
            midi_controls_placeholder.add_widget(ch_layout)
            # Program
            prog_layout = BoxLayout()
            prog_layout.add_widget(Label(text='Prog:'))
            program_input = TextInput(text=str(track.instrument + 1), multiline=False)
            program_input.bind(on_text_validate=self.on_program_change)
            prog_layout.add_widget(program_input)
            midi_controls_placeholder.add_widget(prog_layout)
        else:
            midi_controls_placeholder.add_widget(Widget()) # Spacer
        self.add_widget(midi_controls_placeholder)

    def on_volume_change(self, instance, value):
        # Call the sequencer function directly to avoid full UI refresh and provide live feedback
        self.sequencer_layout.sequencer.set_track_volume(self.track_index, str(value))

    def on_pan_change(self, instance, value):
        # Call the sequencer function directly to avoid full UI refresh and provide live feedback
        self.sequencer_layout.sequencer.set_track_pan(self.track_index, str(value))

    def on_mute_toggle(self, instance):
        self.sequencer_layout.process_command_ui(f'mute {self.track_index}')

    def on_solo_toggle(self, instance):
        self.sequencer_layout.process_command_ui(f'solo {self.track_index}')

    def on_channel_change(self, instance):
        self.sequencer_layout.process_command_ui(f'setch {self.track_index} {instance.text}')

    def on_program_change(self, instance):
        self.sequencer_layout.process_command_ui(f'setprog {self.track_index} {instance.text}')
