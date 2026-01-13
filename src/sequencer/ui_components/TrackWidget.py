from . import *  # Importe tous les imports communs
import mido
from sequencer.models import MidiTrack, AudioTrack, AutomationTrack
from kivy.core.window import Window
from .HoverBehavior import HoverBehavior, HoverableMDButton
from kivymd.uix.slider import MDSlider
from .automation_editor import AutomationEditor

class HoverableSlider(MDSlider, HoverBehavior):
    pass
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.popup import Popup
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


class AutomationGrid(Widget):
    def __init__(self, track_widget, **kwargs):
        super().__init__(**kwargs)
        self.track_widget = track_widget

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos) and touch.is_double_tap:
            self.track_widget.open_automation_editor()
            return True
        return super().on_touch_down(touch)


class MidiInputSelectorPopup(Popup):
    def __init__(self, track_widget, **kwargs):
        super().__init__(**kwargs)
        self.track_widget = track_widget
        self.sequencer = track_widget.sequencer_layout.sequencer
        self.title = "Select MIDI Input Port"
        self.size_hint = (0.6, 0.8)

        content = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(10))
        scroll_view = ScrollView()
        grid = GridLayout(cols=1, size_hint_y=None, spacing=dp(5))
        grid.bind(minimum_height=grid.setter('height'))

        # Fetch available ports using the new method
        available_ports = self.sequencer.jack_manager.get_midi_input_ports()

        # Add a disconnect button
        disconnect_btn = MDButton(MDButtonText(text="-- Disconnect --"), size_hint_y=None, height=dp(40))
        disconnect_btn.bind(on_release=lambda x: self.select_port(None))
        grid.add_widget(disconnect_btn)

        # Add a button for each available port
        for port in available_ports:
            btn = MDButton(MDButtonText(text=port), size_hint_y=None, height=dp(40))
            btn.bind(on_release=lambda x, p=port: self.select_port(p))
            grid.add_widget(btn)

        scroll_view.add_widget(grid)
        content.add_widget(scroll_view)
        self.content = content

    def select_port(self, port_name):
        track = self.track_widget.track

        # Disconnect existing connection if a new port is chosen or disconnect is clicked
        if track.input_port_name and track.output_port_name:
            self.sequencer.jack_manager.disconnect_dynamic(track.output_port_name, track.input_port_name)

        track.input_port_name = port_name

        if port_name:
            # Connect to the new port
            self.sequencer.jack_manager.auto_connect_dynamic(track.output_port_name, port_name)
            self.track_widget.input_button_text_button_text.text = f"Dest: {port_name.split(':')[0]}"
        else:
            # No new port, just disconnected
            self.track_widget.input_button_text_button_text.text = "Dest: None"

        self.sequencer.is_dirty = True
        self.dismiss()


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
    controls_width = NumericProperty(dp(400))
        
    def __init__(self, track, track_index, sequencer_layout, **kwargs):
        super(TrackWidget, self).__init__(**kwargs)
        self._editor_opening = False
        self.track = track
        self.track_index = track_index
        self.sequencer_layout = sequencer_layout
        # Initialisez une liste pour stocker les widgets de courbes pour les pistes d'automation
        self.automation_curves = []
        
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

        # Non-editable track index
        self.index_label = MDLabel(
            text=f"[{track_index}]",
            halign='left',
            valign='middle',
            theme_text_color="Custom",
            text_color=[0.7, 0.7, 0.7, 1],
            font_size=dp(14),
            bold=True,
            size_hint_x=None,
            width=dp(30),
            pos_hint={'center_y': 0.5}
        )
        self.info_section.add_widget(self.index_label)

        # Editable track name
        self.name_label = EditableLabel(
            text=self.track.name,
            font_size=dp(14),
            bold=True,
            color=[0.9, 0.9, 0.9, 1],
            pos_hint={'center_y': 0.5}
        )
        self.name_label.bind(on_text_validated=self.on_name_validated)
        self.info_section.add_widget(self.name_label)

        # --- Middle Section: Controls ---
        self.controls_section = BoxLayout(size_hint_x=None, width=self.controls_width, spacing=dp(8))

        # --- Solo Button (not for Automation tracks) ---
        if not isinstance(track, AutomationTrack):
            self.solo_button = TooltipMDIconButton(
                icon='alpha-s-box' if track.is_solo else 'alpha-s-box-outline',
                tooltip_text='Solo' if not track.is_solo else 'Unsolo',
                on_press=self.on_solo_toggle,
                pos_hint={'center_y': 0.5},
                theme_icon_color="Custom",
                icon_color=[1, 1, 0, 1] if track.is_solo else [1, 1, 1, 0.8],
                md_bg_color=[0.3, 0.3, 0.1, 0.8] if track.is_solo else [0.1, 0.1, 0.1, 0.8]
            )
            self.controls_section.add_widget(self.solo_button)
        elif isinstance(track, AutomationTrack):
            # On récupère le type (midi/audio) de la piste cible
            target_track = self.sequencer_layout.sequencer.song.tracks[track.target_track_index]
            automation_type = 'midi' if isinstance(target_track, MidiTrack) else 'audio'

            # On crée les contrôles d'automation à la place du bouton Record
            self.automation_controls = AutomationControls(
                track_type=automation_type,
                #on_selection_change=self.on_automation_selection_change,
                on_selection_change=self.update_automation_visibility
            )
            self.automation_controls.size_hint_y = None
            self.automation_controls.height = dp(36)
            self.automation_controls.pos_hint = {'center_y': 0.5}
            
            # On l'ajoute directement dans la colonne de gauche
            self.controls_section.add_widget(self.automation_controls)
        else:
            # Add a spacer to maintain alignment
            self.controls_section.add_widget(Widget(size_hint_x=None, width=dp(44)))

        if isinstance(track, MidiTrack):
            self.piano_roll_button = TooltipMDIconButton(
                icon='piano',
                tooltip_text='Open Piano Roll Editor',
                on_press=self.open_piano_roll_editor,
                pos_hint={'center_y': 0.5},
                theme_icon_color="Custom",
                icon_color=[1, 1, 1, 0.8],
                size_hint_x=None,
                width=dp(36)
            )
            self.controls_section.add_widget(self.piano_roll_button)

            self.record_mode_button = ThreeStateRecordButton(
                track=track,
                track_index=track_index,
                sequencer_layout=sequencer_layout,
                callback=self.on_record_mode_change
            )
            self.controls_section.add_widget(self.record_mode_button)
        else:
            # Pour les pistes Audio standards, on garde l'espaceur de 44dp
            self.controls_section.add_widget(Widget(size_hint_x=None, width=dp(44)))

        # --- MIDI Specific Controls (Channel, Program) ---
        midi_controls_layout = BoxLayout(
            orientation='vertical',
            size_hint_x=None,
            width=dp(170),  # Increased width
            spacing=0
        )

        if isinstance(track, MidiTrack):
            # Channel and Program Spinners on top
            top_controls = BoxLayout(
                orientation='horizontal',
                spacing=dp(4),
                size_hint_y=None,
                height=dp(32),
                pos_hint={'center_x': 0.5} # Center the horizontal layout
            )
            channel_label = Label(text='Ch:', size_hint_x=None, width=dp(28), halign='right', valign='middle', color=[0.9, 0.9, 0.9, 1], font_size=dp(13))
            top_controls.add_widget(channel_label)

            # Use fixed width for spinners
            channel_spinner = ValueSpinner(min_val=1, max_val=16, initial_value=track.channel + 1, callback=self.on_channel_change)
            channel_spinner.size_hint_x = None
            channel_spinner.width = dp(50)
            top_controls.add_widget(channel_spinner)

            program_label = Label(text='Prg:', size_hint_x=None, width=dp(28), halign='right', valign='middle', color=[0.9, 0.9, 0.9, 1], font_size=dp(13))
            top_controls.add_widget(program_label)

            program_spinner = ValueSpinner(min_val=1, max_val=128, initial_value=track.instrument + 1, callback=self.on_program_change)
            program_spinner.size_hint_x = None
            program_spinner.width = dp(50)
            top_controls.add_widget(program_spinner)

            # Port Selector Button below
            port_name = track.output_port_name if track.output_port_name else "None"
            self.port_button_text = MDButtonText(text=f"In: {port_name}")
            self.port_selector_button = HoverableMDButton(
                self.port_button_text,
                on_press=self.select_midi_port_popup,
                style="outlined",
                size_hint=(None, None), # Disable size hint for centering
                width=dp(150),
                height=dp(32),
                pos_hint={'center_x': 0.5} # Center the button
            )

            # Plugin Selector Button below
            plugin_name = track.input_port_name.split(':')[0] if track.input_port_name else "None"
            self.input_button_text_button_text = MDButtonText(text=f"Dest: {plugin_name}")
            self.input_selector_button = HoverableMDButton(
                self.input_button_text_button_text,
                on_press=self.select_midi_input_popup,
                style="outlined",
                size_hint=(None, None), # Disable size hint for centering
                width=dp(150),
                height=dp(32),
                pos_hint={'center_x': 0.5} # Center the button
            )

            midi_controls_layout.add_widget(Widget(size_hint_y=0.1)) # Top spacer
            midi_controls_layout.add_widget(top_controls)
            midi_controls_layout.add_widget(self.port_selector_button)
            midi_controls_layout.add_widget(self.input_selector_button)
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
            pos_hint={'center_x': 0.5, 'center_y': 0.5},
            theme_icon_color="Custom",
            #icon_color=[0.3, 0.2, 0.1, 0.8]
            icon_color = [0.8, 0.3, 0, 1] if track.is_muted else [1, 0.6, 0, 1],
            md_bg_color = [0.4, 0.2, 0.1, 0.8] if track.is_muted else [0.3, 0.2, 0.1, 0.8]            
        )
        mute_button_container.add_widget(self.mute_button)

        if not isinstance(track, AutomationTrack):
            volume_layout.add_widget(mute_button_container)
            self.volume_slider = HoverableSlider(min=0, max=1, value=track.volume, orientation='vertical', size_hint_y=1, padding=0, track_active_width=dp(16), track_inactive_width=dp(16))
            self.volume_label = Label(text=f"{int(track.volume * 100)}", size_hint_y=None, height=dp(16), color=[0.9, 0.9, 0.9, 1], font_size=dp(10), pos_hint={'center_x': 0.5})
            volume_layout.add_widget(self.volume_slider)
            volume_layout.add_widget(self.volume_label)
            self.volume_slider.bind(value=self.on_volume_change)
            self.track.bind(volume=self.on_track_volume_changed)
        #else:
            # For automation tracks, add spacers to center the button
        #    volume_layout.add_widget(Widget())
        #    volume_layout.add_widget(mute_button_container)
        #    volume_layout.add_widget(Widget())

        self.controls_section.add_widget(volume_layout)

        # --- Pan Controls ---
        if not isinstance(track, AutomationTrack):
            pan_layout = BoxLayout(orientation='vertical', size_hint_x=None, width=dp(50), spacing=0)

            pan_icon_container = BoxLayout(size_hint_y=None, height=dp(30))
            pan_icon = MDIcon(icon='swap-horizontal', theme_text_color='Custom', text_color=[1, 1, 1, 0.38], pos_hint={'center_x': 0.5, 'center_y': 0.5})
            pan_icon_container.add_widget(pan_icon)

            self.pan_slider = HoverableSlider(min=-1, max=1, value=track.pan, orientation='vertical', size_hint_y=1, padding=0, track_active_width=dp(16), track_inactive_width=dp(16))
            self.pan_label = Label(text=f"{track.pan:+.1f}", size_hint_y=None, height=dp(16), color=[0.9, 0.9, 0.9, 1], font_size=dp(10), pos_hint={'center_x': 0.5})

            pan_layout.add_widget(pan_icon_container)
            pan_layout.add_widget(self.pan_slider)
            pan_layout.add_widget(self.pan_label)
            self.pan_slider.bind(value=self.on_pan_change)
            self.controls_section.add_widget(pan_layout)
        else:
            # Add a spacer to maintain alignment
            self.controls_section.add_widget(Widget(size_hint_x=None, width=dp(50)))

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
                orientation='vertical'
            )

            track_type_icon = "help-circle"
            track_type_color = [0.5, 0.5, 0.5, 1]

            if isinstance(track, AudioTrack):
                track_type_icon = "waveform"
                track_type_color = [1, 1, 1, 0.38]
            elif isinstance(track, AutomationTrack):
                track_type_icon = "chart-line"
                track_type_color = [1, 1, 1, 0.38]

            icon = MDIcon(
                icon=track_type_icon,
                theme_text_color="Custom",
                text_color=track_type_color,
                halign='center',
                valign='center'
            )

            icon_layout.add_widget(Widget()) # Top spacer
            icon_layout.add_widget(icon)
            icon_layout.add_widget(Widget()) # Bottom spacer

            self.timeline_scroll = ScrollView(size_hint_x=1, do_scroll_x=True, do_scroll_y=False)
            self.timeline_scroll.effect_x = ScrollEffect()  # Bounded, no bounce

            # A ScrollView must have a single child.
            self.timeline_container = AutomationGrid(track_widget=self, size_hint=(None, 1))
            self.measure_grid = MeasureGrid(
                size_hint=(1, 1), # The grid itself can fill the container
                beat_per_measure=4,
                total_beats=self.total_beats,
                pixels_per_beat=self.pixels_per_beat
            )
            self.timeline_container.add_widget(self.measure_grid)

            if isinstance(track, AutomationTrack):
                # --- ÉTAPE 1 : GROUPER LES POINTS PAR PARAMÈTRE ---
                points_by_param = {}
                for p in track.points:
                    if p.parameter not in points_by_param:
                        points_by_param[p.parameter] = []
                    points_by_param[p.parameter].append(p)

                # --- ÉTAPE 2 : CRÉER UN WIDGET POUR CHAQUE PARAMÈTRE TROUVÉ ---
                for param, filtered_points in points_by_param.items():
                    # Définition des bornes selon le paramètre
                    min_v, max_v = 0.0, 1.0
                    if param in ["prog", "vel"]: 
                        min_v, max_v = 0.0, 127.0
                    elif param == "pan": 
                        min_v, max_v = -1.0, 1.0

                    # On n'affiche que le volume par défaut
                    is_vol = (param == "vol")

                    curve_widget = AutomationCurveWidget(
                        size_hint=(1, 1),
                        total_beats=self.total_beats,
                        pixels_per_beat=self.pixels_per_beat,
                        min_val=min_v,
                        max_val=max_v,
                        points=filtered_points, # On ne donne QUE les points de ce paramètre
                        opacity=1 if is_vol else 0,
                        disabled=not is_vol
                    )
                    
                    # On ajoute une propriété personnalisée pour l'identifier facilement
                    curve_widget.param_type = param 
                    
                    self.automation_curves.append(curve_widget)
                    self.timeline_container.add_widget(curve_widget)

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
            # --- FIX: Propagate zoom changes to ALL automation curve widgets ---
            if hasattr(self, 'automation_curves'):
                for curve in self.automation_curves:
                    curve.total_beats = self.total_beats
                    curve.pixels_per_beat = self.pixels_per_beat

    def update_playback_rect(self, *args):
        self.playback_rect.pos = self.playback_line.pos
        self.playback_rect.size = self.playback_line.size

    # ... (le reste de la classe reste inchangé : set_playback_position, update_grid_parameters, etc.)

    def on_solo_changed(self, instance, value):
        self.update_mute_solo_appearance()

    def on_automation_selection_change(self, selected_param):
        """
        Callback from AutomationControls when the user selects a new parameter to view.
        """
        if not hasattr(self, 'automation_curve'):
            return

        if selected_param is None:
            self.automation_curve.points = []
        else:
            filtered_points = [p for p in self.track.points if p.parameter == selected_param]
            self.automation_curve.points = filtered_points

    def on_name_validated(self, instance, new_name):
        """Callback for when the user validates a new track name."""
        # We need to escape the name in case it contains spaces in the future
        # although the current filter doesn't allow it.
        command = f'rename {self.track_index} "{new_name}"'
        self.sequencer_layout.process_command_ui(command)

    def update_automation_visibility(self, selected_param):
        """Affiche le calque correspondant au bouton cliqué."""
        for curve in self.automation_curves:
            if curve.param_type == selected_param:
                curve.opacity = 1
                curve.disabled = False
                # Re-filter the points from the original track data to ensure the list is fresh
                filtered_points = [p for p in self.track.points if p.parameter == selected_param]
                curve.points = filtered_points
                # The on_points binding on the curve widget will automatically call draw_curve
            else:
                curve.opacity = 0
                curve.disabled = True

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

        if hasattr(self, 'solo_button'):
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

    def open_automation_editor(self, instance=None):
        if self._editor_opening:
            return
        if isinstance(self.track, AutomationTrack):
            self._editor_opening = True
            editor = AutomationEditor(track=self.track, sequencer_layout=self.sequencer_layout)
            editor.bind(on_dismiss=self._on_editor_dismiss)
            editor.open()

    def _on_editor_dismiss(self, instance):
        self._editor_opening = False

    def select_midi_port_popup(self, instance):
        """Opens a popup to select a MIDI output port for the track."""
        standard_ports = mido.get_output_names()
        virtual_port_names = [p.name for p in self.sequencer_layout.sequencer.virtual_ports]
        available_ports = sorted(list(set(standard_ports + virtual_port_names)))

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
            self.port_button_text.text = f"Port: {port_name}"
            popup.dismiss()

        for port in available_ports:
            btn = MDButton(MDButtonText(text=port), size_hint_y=None, height=dp(40))
            btn.bind(on_release=lambda x, p=port: select_port(p))
            grid.add_widget(btn)

        scroll_view.add_widget(grid)
        content.add_widget(scroll_view)

        popup.open()

    def select_midi_input_popup(self, instance):
        """Opens a popup to select a MIDI input port for the track."""
        popup = MidiInputSelectorPopup(track_widget=self)
        popup.open()
        