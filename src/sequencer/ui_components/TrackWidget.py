from . import *  # Importe tous les imports communs
from sequencer.models import MidiTrack, AudioTrack, AutomationTrack
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.scrollview import ScrollView
from kivy.properties import NumericProperty, ObjectProperty
from kivy.uix.label import Label 
from kivy.metrics import dp
from sequencer.ui_components.MeasureGrid import MeasureGrid
from .PianoRoll import PianoRoll
from .PianoKeyboard import PianoKeyboard
from kivy.effects.scroll import ScrollEffect
from kivy.clock import Clock
from kivy.uix.widget import Widget
from kivy.graphics import Color, Rectangle


class TrackWidget(BoxLayout):
    """
    Represents a single track in the sequencer UI. It contains the track's info,
    a timeline for its content (which can be a piano roll for MIDI or a waveform for audio),
    and a set of controls (mute, solo, volume, etc.).
    """
    total_beats = NumericProperty(128.0) 
    pixels_per_beat = NumericProperty(dp(100))
    timeline_container = ObjectProperty(None)
    info_width = NumericProperty(dp(150))
    controls_width = NumericProperty(dp(350))
        
    def __init__(self, track, track_index, sequencer_layout, **kwargs):
        super(TrackWidget, self).__init__(**kwargs)
        self.track = track
        self.track_index = track_index
        self.sequencer_layout = sequencer_layout
        self.orientation = 'horizontal'
        self.size_hint_y = None
        if isinstance(track, MidiTrack):
            self.height = dp(128)
        else:
            self.height = dp(112)
        self.spacing = dp(12)
        self.padding = [dp(12), 0, dp(12), 0]

        # --- Canvas Background ---
        with self.canvas.before:
            Color(0.15, 0.15, 0.15, 1) if track_index % 2 == 0 else Color(0.12, 0.12, 0.12, 1)
            self.background_rect = Rectangle(pos=self.pos, size=self.size)
            Color(0.3, 0.3, 0.3, 0.5)
            self.border_line = Line(points=[self.x, self.y, self.x + self.width, self.y], width=0.5)

        self.bind(pos=self._update_graphics, size=self._update_graphics)

        # --- Left Section: Track Info ---
        self.info_section = BoxLayout(size_hint_x=None, width=self.info_width, orientation='horizontal', spacing=dp(8), padding=[dp(4), 0, 0, 0])

        if isinstance(track, MidiTrack):
            self.piano_roll_button = TooltipMDIconButton(
                icon='piano',
                tooltip_text='Open Piano Roll Editor',
                on_press=self.open_piano_roll_editor,
                pos_hint={'center_y': 0.5},
                theme_icon_color="Custom",
                icon_color=[0.7, 0.7, 0.9, 1],
                size_hint_x=None,
                width=dp(36)
            )
            self.info_section.add_widget(self.piano_roll_button)

        self.name_label = Label(
            text=f"[{track_index}] {track.name}",
            halign='left', 
            valign='middle', 
            color=[0.9, 0.9, 0.9, 1],
            font_size=dp(14),
            bold=True,
            text_size=(self.info_width - dp(50), None) # Allow text to wrap if needed
        )
        self.info_section.add_widget(self.name_label)

        # --- Middle Section: Controls ---
        self.controls_section = BoxLayout(size_hint_x=None, width=self.controls_width, spacing=dp(8))

        if isinstance(track, MidiTrack):
            self.record_mode_button = ThreeStateRecordButton(
                track=track,
                track_index=track_index,
                sequencer_layout=sequencer_layout,
                callback=self.on_record_mode_change
            )
            self.controls_section.add_widget(self.record_mode_button)
        else:
            # Add a spacer to maintain alignment with MIDI tracks that have a record button
            self.controls_section.add_widget(Widget(size_hint_x=None, width=dp(44)))

        # --- Solo Button ---
        self.solo_button = TooltipMDIconButton(
            icon='alpha-s-box' if track.is_solo else 'alpha-s-box-outline',
            tooltip_text='Solo' if not track.is_solo else 'Unsolo',
            on_press=self.on_solo_toggle,
            pos_hint={'center_y': 0.5},
            theme_icon_color="Custom",
            icon_color=[1, 1, 0, 1] if track.is_solo else [0.6, 0.6, 0.6, 1],
            md_bg_color=[0.3, 0.3, 0.1, 0.8] if track.is_solo else [0.1, 0.1, 0.1, 0.8]
        )
        self.controls_section.add_widget(self.solo_button)

        # --- MIDI Specific Controls (Channel, Program) ---
        midi_controls_layout = BoxLayout(
            orientation='vertical',
            size_hint_x=None,
            width=dp(130),
            spacing=dp(4)
        )

        if isinstance(track, MidiTrack):
            # Channel and Program Spinners on top
            top_controls = BoxLayout(orientation='horizontal', spacing=dp(4), size_hint_y=None, height=dp(32))
            channel_label = Label(text='Ch:', size_hint_x=None, width=dp(28), halign='right', valign='middle', color=[0.9, 0.9, 0.9, 1], font_size=dp(13))
            top_controls.add_widget(channel_label)
            channel_spinner = ValueSpinner(min_val=1, max_val=16, initial_value=track.channel + 1, callback=self.on_channel_change)
            top_controls.add_widget(channel_spinner)

            program_label = Label(text='Prg:', size_hint_x=None, width=dp(28), halign='right', valign='middle', color=[0.9, 0.9, 0.9, 1], font_size=dp(13))
            top_controls.add_widget(program_label)
            program_spinner = ValueSpinner(min_val=1, max_val=128, initial_value=track.instrument + 1, callback=self.on_program_change)
            top_controls.add_widget(program_spinner)

            # Port Selector Button below
            port_name = track.output_port_name if track.output_port_name else "None"
            self.port_selector_button = MDTextButton(
                text=f"Port: {port_name}",
                on_press=self.select_midi_port_popup,
                style="outlined",
                size_hint_y=None,
                height=dp(32)
            )

            midi_controls_layout.add_widget(Widget(size_hint_y=0.1)) # Top spacer
            midi_controls_layout.add_widget(top_controls)
            midi_controls_layout.add_widget(self.port_selector_button)
            midi_controls_layout.add_widget(Widget(size_hint_y=0.1)) # Bottom spacer
        else:
            midi_controls_layout.add_widget(Widget())
            
        self.controls_section.add_widget(midi_controls_layout)
        
        # --- Volume Controls ---
        volume_layout = BoxLayout(orientation='vertical', size_hint_x=None, width=dp(50), spacing=0)

        mute_button_container = BoxLayout(size_hint_y=None, height=dp(30), pos_hint={'center_x': 0.5})
        self.mute_button = TooltipMDIconButton(
            icon='volume-off' if track.is_muted else 'volume-high',
            tooltip_text='Mute' if not track.is_muted else 'Unmute',
            on_press=self.on_mute_toggle,
            pos_hint={'center_x': 0.5, 'center_y': 0.5}
        )
        mute_button_container.add_widget(self.mute_button)

        self.volume_slider = MDSlider(min=0, max=1, value=track.volume, orientation='vertical', size_hint_y=1, padding=0, track_active_width=dp(8), track_inactive_width=dp(8))
        self.volume_label = Label(text=f"{int(track.volume * 100)}", size_hint_y=None, height=dp(16), color=[0.9, 0.9, 0.9, 1], font_size=dp(10), pos_hint={'center_x': 0.5})

        volume_layout.add_widget(mute_button_container)
        volume_layout.add_widget(self.volume_slider)
        volume_layout.add_widget(self.volume_label)
        self.volume_slider.bind(value=self.on_volume_change)
        self.track.bind(volume=self.on_track_volume_changed)
        self.controls_section.add_widget(volume_layout)

        # --- Pan Controls ---
        pan_layout = BoxLayout(orientation='vertical', size_hint_x=None, width=dp(50), spacing=0)

        pan_icon_container = BoxLayout(size_hint_y=None, height=dp(30))
        pan_icon = MDIcon(icon='swap-horizontal', theme_text_color='Custom', text_color=[0.6, 0.6, 1, 1], pos_hint={'center_x': 0.5, 'center_y': 0.5})
        pan_icon_container.add_widget(pan_icon)

        self.pan_slider = MDSlider(min=-1, max=1, value=track.pan, orientation='vertical', size_hint_y=1, padding=0, track_active_width=dp(8), track_inactive_width=dp(8))
        self.pan_label = Label(text=f"{track.pan:+.1f}", size_hint_y=None, height=dp(16), color=[0.9, 0.9, 0.9, 1], font_size=dp(10), pos_hint={'center_x': 0.5})

        pan_layout.add_widget(pan_icon_container)
        pan_layout.add_widget(self.pan_slider)
        pan_layout.add_widget(self.pan_label)
        self.pan_slider.bind(value=self.on_pan_change)
        self.controls_section.add_widget(pan_layout)

        # --- Left Panel Container ---
        left_panel = BoxLayout(
            orientation='horizontal',
            size_hint_x=None,
            spacing=self.spacing
        )
        left_panel.add_widget(self.info_section)
        left_panel.add_widget(self.controls_section)
        left_panel.width = self.info_width + self.controls_width + self.spacing
        self.add_widget(left_panel)

        # --- Right Section: Timeline ---
        if isinstance(track, MidiTrack):
            note_height = dp(12)

            # 1. Keyboard (fixed width)
            keyboard_sv = ScrollView(size_hint_x=None, width=dp(40), do_scroll_x=False, do_scroll_y=True)
            keyboard_sv.effect_y = ScrollEffect()  # Bounded, no bounce
            self.piano_keyboard = PianoKeyboard(note_height=note_height)
            keyboard_sv.add_widget(self.piano_keyboard)

            # 2. Timeline ScrollView (expanding, with both x and y scroll)
            self.timeline_scroll = ScrollView(size_hint_x=1, do_scroll_x=True, do_scroll_y=True)
            self.timeline_scroll.effect_x = ScrollEffect()  # Bounded, no bounce
            self.timeline_scroll.effect_y = ScrollEffect()  # Bounded, no bounce

            # Content container (FloatLayout for overlaying playback line)
            self.content = FloatLayout(size_hint=(None, None))
            self.content.size = (self.total_beats * self.pixels_per_beat, 128 * note_height)
            self.timeline_container = self.content  # For compatibility with other methods

            # Piano roll grid/notes
            self.piano_roll = PianoRoll(
                track=track,
                total_beats=self.total_beats,
                pixels_per_beat=self.pixels_per_beat,
                note_height=note_height,
                size_hint=(None, None)
            )
            self.piano_roll.size = self.content.size
            self.piano_roll.pos = (0, 0)
            self.content.add_widget(self.piano_roll)

            # Playback line (spans full content height)
            self.playback_line = Widget(size_hint_x=None, width=dp(2))
            self.playback_line.height = self.content.height
            self.playback_line.y = 0
            with self.playback_line.canvas:
                Color(1, 0, 0, 0.8)
                self.playback_rect = Rectangle(pos=self.playback_line.pos, size=self.playback_line.size)
            self.playback_line.bind(pos=self.update_playback_rect, size=self.update_playback_rect)
            self.content.add_widget(self.playback_line)

            self.timeline_scroll.add_widget(self.content)

            # Bind for size/zoom updates
            self.bind(total_beats=self.update_timeline_size, pixels_per_beat=self.update_timeline_size)

            # Link vertical scrolling between keyboard and timeline
            keyboard_sv.bind(scroll_y=lambda i, v: setattr(self.timeline_scroll, 'scroll_y', v))
            self.timeline_scroll.bind(scroll_y=lambda i, v: setattr(keyboard_sv, 'scroll_y', v))

            # Center on C4 (note 60) by default
            def set_default_scroll(dt):
                total_height = 128 * note_height
                view_height = self.height
                note_center_y = 60 * note_height + note_height / 2
                desired_top_y = note_center_y - view_height / 2
                max_top_y = total_height - view_height
                desired_top_y = max(0, min(desired_top_y, max_top_y))
                if max_top_y > 0:
                    scroll_y = 1 - (desired_top_y / max_top_y)
                else:
                    scroll_y = 0
                keyboard_sv.scroll_y = scroll_y
            Clock.schedule_once(set_default_scroll)

            # Add to main layout
            self.add_widget(keyboard_sv)
            self.add_widget(self.timeline_scroll)

        else:  # Audio and Automation tracks (unchanged, no vertical scroll)
            # Create a layout for the track type icon, replacing the old spacer
            icon_layout = BoxLayout(
                size_hint_x=None,
                width=dp(40),
                orientation='vertical',
                pos_hint={'center_y': 0.5}
            )

            track_type_icon = "help-circle"
            track_type_color = [0.5, 0.5, 0.5, 1]

            if isinstance(track, AudioTrack):
                track_type_icon = "waveform"
                track_type_color = [0.9, 0.5, 0.2, 1]
            elif isinstance(track, AutomationTrack):
                track_type_icon = "chart-line"
                track_type_color = [0.2, 0.8, 0.8, 1]

            icon = MDIcon(
                icon=track_type_icon,
                theme_text_color="Custom",
                text_color=track_type_color,
                halign='center',
                valign='center'
            )
            icon_layout.add_widget(icon)

            self.timeline_scroll = ScrollView(size_hint_x=1, do_scroll_x=True, do_scroll_y=False)
            self.timeline_scroll.effect_x = ScrollEffect()  # Bounded, no bounce

            # A ScrollView must have a single child.
            self.timeline_container = Widget(size_hint=(None, 1))
            self.measure_grid = MeasureGrid(
                size_hint=(1, 1), # The grid itself can fill the container
                beat_per_measure=4,
                total_beats=self.total_beats,
                pixels_per_beat=self.pixels_per_beat
            )
            self.timeline_container.add_widget(self.measure_grid)
            self.timeline_scroll.add_widget(self.timeline_container)

            # Add the icon and timeline directly to the main layout
            self.add_widget(icon_layout)
            self.add_widget(self.timeline_scroll)

            # --- Playback Line (Cursor) ---
            self.playback_line = Widget(size_hint_x=None, width=dp(2))
            with self.playback_line.canvas:
                Color(1, 0, 0, 0.8)
                self.playback_rect = Rectangle(pos=self.playback_line.pos, size=self.playback_line.size)
            self.playback_line.bind(pos=self.update_playback_rect, size=self.update_playback_rect)

            self.timeline_container.add_widget(self.playback_line)
            self.playback_line.size_hint_y = 1

        self.bind(total_beats=self.update_timeline_size, pixels_per_beat=self.update_timeline_size)
        self.update_timeline_size()

        self.track.bind(is_solo=self.on_solo_changed)
        
    def update_timeline_size(self, *args):
        if hasattr(self, 'content'):
            # For MIDI tracks
            self.content.width = self.total_beats * self.pixels_per_beat
            self.piano_roll.width = self.content.width
            
            # --- CORRECTION ICI ---
            # Il faut propager les nouvelles valeurs à l'instance piano_roll
            # avant d'appeler draw()
            self.piano_roll.total_beats = self.total_beats
            self.piano_roll.pixels_per_beat = self.pixels_per_beat
            # ----------------------
            
            self.piano_roll.draw()
        else:
            # For other tracks (unchanged)
            content_width = self.total_beats * self.pixels_per_beat
            self.timeline_container.width = content_width
            # Vous le faisiez déjà ici pour measure_grid, mais pas pour piano_roll !
            self.measure_grid.total_beats = self.total_beats
            self.measure_grid.pixels_per_beat = self.pixels_per_beat

    def update_playback_rect(self, *args):
        self.playback_rect.pos = self.playback_line.pos
        self.playback_rect.size = self.playback_line.size

    # ... (le reste de la classe reste inchangé : set_playback_position, update_grid_parameters, etc.)

    def on_solo_changed(self, instance, value):
        self.update_mute_solo_appearance()


    def set_playback_position(self, current_beat: float):
        """
        Updates the visual position of the playback line (cursor) and handles automatic
        scrolling of the timeline to keep the cursor in view during playback.
        """
        pixels_per_beat = self.pixels_per_beat
        x_pos = current_beat * pixels_per_beat

        # Update the line's x-coordinate.
        if self.playback_line:
            self.playback_line.x = x_pos

        # Auto-scroll logic only runs when the transport is active.
        if self.sequencer_layout.sequencer.playback_state in ['playing', 'recording']:
            EPSILON_PIXELS = dp(1)

            timeline_width = self.timeline_scroll.children[0].width
            scroll_view_width = self.timeline_scroll.width
            scroll_view = self.timeline_scroll

            # If the content is smaller than the view, no need to scroll.
            if timeline_width <= scroll_view_width + EPSILON_PIXELS:
                scroll_view.scroll_x = 0.0
                return

            # Define a margin on the left and right of the view. The auto-scroll
            # will try to keep the playhead within these margins.
            margin_x = scroll_view_width * 0.3
            max_displacement = timeline_width - scroll_view_width + EPSILON_PIXELS

            # Pin the scroll to the start if the playhead is in the initial left margin.
            if x_pos < margin_x:
                scroll_view.scroll_x = 0.0
                return

            current_scroll_x_pixels = scroll_view.scroll_x * max_displacement
            new_scroll_x_pixels = -1

            # If the playhead moves past the right margin, calculate new scroll position.
            if x_pos > current_scroll_x_pixels + scroll_view_width - margin_x:
                new_scroll_x_pixels = x_pos - (scroll_view_width - margin_x)
            # If the playhead moves before the left margin (while scrolling), calculate new scroll position.
            elif x_pos < current_scroll_x_pixels + margin_x and current_scroll_x_pixels > EPSILON_PIXELS:
                new_scroll_x_pixels = x_pos - margin_x

            # If no scroll is needed, exit.
            if new_scroll_x_pixels == -1:
                return

            # Clamp the new scroll position to be within the valid range [0, max_displacement].
            new_scroll_x_pixels = max(0, min(new_scroll_x_pixels, max_displacement))

            # Normalize the pixel value to Kivy's scroll_x format [0.0, 1.0].
            if max_displacement < EPSILON_PIXELS:
                normalized_scroll_value = 0.0
            else:
                normalized_scroll_value = new_scroll_x_pixels / max_displacement

            scroll_view.scroll_x = max(0.0, min(1.0, normalized_scroll_value))

    def update_grid_parameters(self, total_beats: float, pixels_per_beat: float):
        """Called by the parent layout to propagate zoom/length changes to this widget."""
        self.total_beats = total_beats
        self.pixels_per_beat = pixels_per_beat

    def reset_timeline_view(self):
        """Resets the scroll position and playback line to the beginning."""
        if self.playback_line:
            self.playback_line.x = 0 
        self.timeline_scroll.scroll_x = 0.0

    def _update_graphics(self, *args):
        """Callback to update the size and position of canvas elements when the widget moves or resizes."""
        if hasattr(self, 'background_rect'):
            self.background_rect.pos = self.pos
            self.background_rect.size = self.size
        if hasattr(self, 'border_line'):
            self.border_line.points = [self.x, self.y, self.x + self.width, self.y]

    def _update_type_icon_bg(self, *args):
        """Updates the background of the track type icon."""
        if hasattr(self, 'type_bg_rect'):
            self.type_bg_rect.pos = self.children[-1].pos
            self.type_bg_rect.size = self.children[-1].size
        if hasattr(self, 'type_border_rect'):
            self.type_border_rect.rectangle = [self.children[-1].x, self.children[-1].y, 
                                             self.children[-1].width, self.children[-1].height]

    def on_track_volume_changed(self, instance, value):
        """Callback for when the track's volume property changes in the backend model."""
        self.volume_label.text = f"{int(value * 100)}"
        if abs(self.volume_slider.value - value) > 0.001:
            self.volume_slider.value = value

    def on_volume_change(self, instance, value):
        """Callback for when the user moves the volume slider."""
        self.volume_label.text = f"{int(value * 100)}"
        self.sequencer_layout.process_slider_command(f'volume {self.track_index} {value}')

    def on_pan_change(self, instance, value):
        """Callback for when the user moves the pan slider."""
        self.pan_label.text = f"{value:+.1f}"
        self.sequencer_layout.process_slider_command(f'pan {self.track_index} {value}')

    def on_mute_toggle(self, instance):
        """Called when the mute button is pressed."""
        self.sequencer_layout.toggle_track_mute(self.track_index)

    def on_solo_toggle(self, instance):
        """Called when the solo button is pressed."""
        self.sequencer_layout.toggle_track_solo(self.track_index)

    def update_mute_solo_appearance(self):
        """Updates the visual state of the mute and solo buttons based on the track's state."""
        is_muted = self.track.is_muted
        self.mute_button.icon = 'volume-off' if is_muted else 'volume-high'
        self.mute_button.tooltip_text = 'Unmute' if is_muted else 'Mute'
        self.mute_button.icon_color = [0.8, 0.3, 0, 1] if is_muted else [1, 0.6, 0, 1]
        self.mute_button.md_bg_color = [0.4, 0.2, 0.1, 0.8] if is_muted else [0.3, 0.2, 0.1, 0.8]

        is_solo = self.track.is_solo
        self.solo_button.icon = 'alpha-s-box' if is_solo else 'alpha-s-box-outline'
        self.solo_button.tooltip_text = 'Unsolo' if is_solo else 'Solo'
        self.solo_button.icon_color = [1, 1, 0, 1] if is_solo else [0.6, 0.6, 0.6, 1]
        self.solo_button.md_bg_color = [0.3, 0.3, 0.1, 0.8] if is_solo else [0.1, 0.1, 0.1, 0.8]

    def get_record_mode_tooltip(self, mode):
        """Returns the appropriate tooltip text for the given record mode."""
        tooltips = {
            'OFF': 'Record: OFF - Piste désactivée',
            'OVERWRITE': 'Record: OVERWRITE - Écrase les notes existantes',
            'KEEP': 'Record: KEEP - Conserve les notes existantes'
        }
        return tooltips.get(mode, 'Record Mode')

    def on_record_mode_change(self, track):
        """Callback for when the record mode button changes state."""
        print(f"Record mode changed for track {self.track_index}: {track.record_mode}")
        self.record_mode_button.tooltip_text = self.get_record_mode_tooltip(track.record_mode)

    def on_channel_change(self, instance):
        """Callback for when the MIDI channel spinner value changes."""
        self.sequencer_layout.process_slider_command(f'setch {self.track_index} {instance.text}')

    def on_program_change(self, instance):
        """Callback for when the MIDI program spinner value changes."""
        self.sequencer_layout.process_slider_command(f'setprog {self.track_index} {instance.text}')

    def on_set_as_metronome(self, instance):
        """Callback for a potential future feature to set a track as the metronome source."""
        self.sequencer_layout.process_command_ui(f'setmetrotrack {self.track_index}')

    def open_piano_roll_editor(self, instance=None):
        """Creates and opens the piano roll editor popup for the current track."""
        if isinstance(self.track, MidiTrack):
            sequencer = self.sequencer_layout.sequencer
            
            # 1. Capturer la position actuelle AVANT d'arrêter
            captured_beat = sequencer.current_beat

            # 2. Arrêter la lecture
            if sequencer.playback_state in ['playing', 'recording']:
                sequencer.stop()

            # 3. Restaurer la position dans le séquenceur
            # (Car sequencer.stop() l'a probablement remise à 0)
            if captured_beat > 0:
                sequencer.current_beat = captured_beat

            editor = PianoRollEditor(track=self.track, sequencer_layout=self.sequencer_layout)
            editor.open()

    def select_midi_port_popup(self, instance):
        """Opens a popup to select a MIDI output port for the track."""
        available_ports = mido.get_output_names()

        content = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(10))
        scroll_view = ScrollView()
        grid = GridLayout(cols=1, size_hint_y=None, spacing=dp(5))
        grid.bind(minimum_height=grid.setter('height'))

        popup = Popup(
            title="Select MIDI Output Port",
            content=content,
            size_hint=(0.5, 0.7)
        )

        def select_port(port_name):
            command = f'assign {self.track_index} "{port_name}"'
            self.sequencer_layout.process_command_ui(command)
            self.port_selector_button.text = f"Port: {port_name}"
            popup.dismiss()

        for port in available_ports:
            btn = MDTextButton(text=port, size_hint_y=None, height=dp(40))
            btn.bind(on_release=lambda x, p=port: select_port(p))
            grid.add_widget(btn)

        scroll_view.add_widget(grid)
        content.add_widget(scroll_view)

        popup.open()
