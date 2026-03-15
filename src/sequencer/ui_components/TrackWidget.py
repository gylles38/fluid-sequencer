from . import *  # Importe tous les imports communs
import mido
from sequencer.models import MidiTrack, AudioTrack, AutomationTrack
from kivy.core.window import Window
from .HoverBehavior import HoverBehavior, HoverableMDButton
from kivymd.uix.slider import MDSlider
from .automation_editor import AutomationEditor
from .AutomationCurve import AutomationCurveWidget

class HoverableSlider(MDSlider, HoverBehavior):
    pass
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.relativelayout import RelativeLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.stencilview import StencilView
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.properties import NumericProperty, ObjectProperty, BooleanProperty
from kivy.uix.label import Label 
from kivy.metrics import dp
from sequencer.ui_components.MeasureGrid import MeasureGrid
from .PianoRoll import PianoRoll
from .PianoKeyboard import PianoKeyboard
from kivy.effects.scroll import ScrollEffect
from kivy.clock import Clock
from kivy.uix.widget import Widget
from kivy.graphics import Color, Rectangle
from kivymd.uix.button import MDIconButton, MDButton, MDButtonText
from kivymd.uix.boxlayout import MDBoxLayout
from kivy.graphics import Translate, PushMatrix, PopMatrix

class DragHandle(Widget):
    """A vertical drag handle that spans the full height of the track."""
    def __init__(self, track_widget, **kwargs):
        super().__init__(**kwargs)
        self.track_widget = track_widget
        self.size_hint_x = None
        self.width = dp(12)
        self.bind(pos=self._update_canvas, size=self._update_canvas)

        with self.canvas:
            self.bg_color = Color(0.15, 0.15, 0.15, 1)
            self.bg_rect = Rectangle(pos=self.pos, size=self.size)

            # Grip indicators (dots or lines)
            self.grip_color = Color(0.4, 0.4, 0.4, 1)
            self.grips = []
            for _ in range(3):
                self.grips.append(Rectangle(size=(dp(4), dp(2))))

    def _update_canvas(self, *args):
        self.bg_rect.pos = self.pos
        self.bg_rect.size = self.size

        # Center grips vertically
        center_x = self.x + self.width / 2 - dp(2)
        spacing = dp(6)
        total_height = 2 * spacing
        start_y = self.y + self.height / 2 - total_height / 2

        for i, grip in enumerate(self.grips):
            grip.pos = (center_x, start_y + i * spacing)

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            touch.grab(self.track_widget)
            if hasattr(self.track_widget.sequencer_layout, 'on_track_drag_start'):
                self.track_widget.sequencer_layout.on_track_drag_start(self.track_widget, touch)
            return True
        return False

    def on_touch_move(self, touch):
        return False # No default move handling

    def on_touch_up(self, touch):
        if touch.grab_current is self.track_widget:
            touch.ungrab(self.track_widget)
            return True
        return False

class ResizeHandle(Widget):
    """A horizontal handle at the bottom of the track for vertical resizing."""
    def __init__(self, track_widget, **kwargs):
        super().__init__(**kwargs)
        self.track_widget = track_widget
        self.size_hint_y = None
        self.height = dp(12)
        self.bind(pos=self._update_canvas, size=self._update_canvas)

        with self.canvas:
            self.bg_color = Color(0.15, 0.15, 0.15, 1)
            self.bg_rect = Rectangle(pos=self.pos, size=self.size)

            # Grip indicators (matching DragHandle aesthetic but horizontal)
            self.grip_color = Color(0.4, 0.4, 0.4, 1)
            self.grips = []
            for _ in range(3):
                self.grips.append(Rectangle(size=(dp(2), dp(4))))

    def _update_canvas(self, *args):
        self.bg_rect.pos = self.pos
        self.bg_rect.size = self.size

        # Center grips horizontally
        center_x = self.x + self.width / 2 - dp(1)
        spacing = dp(6)
        total_width = 2 * spacing
        start_x = center_x - total_width / 2
        center_y = self.y + self.height / 2 - dp(2)

        for i, grip in enumerate(self.grips):
            grip.pos = (start_x + i * spacing, center_y)

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            touch.grab(self)
            self._initial_height = self.track_widget.height
            self._initial_touch_y = touch.y
            return True
        return False

    def on_touch_move(self, touch):
        if touch.grab_current is self:
            if self.track_widget.is_minimized:
                return False

            # When dragging down, touch.y decreases, delta_y increases height
            delta_y = self._initial_touch_y - touch.y
            new_height = self._initial_height + delta_y

            # Constraints: 80dp to 320dp
            min_h = dp(80)
            max_h = dp(320)
            self.track_widget.height = max(min_h, min(max_h, new_height))
            return True
        return False

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
            return True
        return False

class AutomationGrid(RelativeLayout):
    def __init__(self, track_widget, **kwargs) -> None:
        super().__init__(**kwargs)
        self.track_widget = track_widget

    def on_touch_down(self, touch) -> None | bool:
        if self.collide_point(*touch.pos) and touch.is_double_tap:
            self.track_widget.open_automation_editor()
            return True
        return super().on_touch_down(touch)

class ChangeTargetPopup(Popup):
    def __init__(self, track_widget, **kwargs):
        super().__init__(**kwargs)
        self.track_widget = track_widget
        self.title = "Choisir la piste cible"
        self.size_hint = (0.5, 0.6)
        self.background_color = [0.1, 0.1, 0.1, 1]

        layout = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(10))
        scroll = ScrollView()
        list_layout = GridLayout(cols=1, spacing=dp(5), size_hint_y=None)
        list_layout.bind(minimum_height=list_layout.setter('height'))

        sequencer = track_widget.sequencer_layout.sequencer
        for i, track in enumerate(sequencer.song.tracks):
            # On liste tout sauf les pistes d'automation
            if not isinstance(track, AutomationTrack):
                # Création du bouton style KivyMD
                btn = MDButton(
                    MDButtonText(text=f"Track {i}: {track.name}"),
                    style="filled",
                    size_hint_x=1,
                    on_release=lambda x, idx=i: self.select_target(idx)
                )
                list_layout.add_widget(btn)

        scroll.add_widget(list_layout)
        layout.add_widget(scroll)
        self.content = layout

    def select_target(self, index):
        # 1. On change l'index dans le modèle de données
        self.track_widget.track.target_track_index = index
        
        # 2. On rafraîchit l'UI du TrackWidget
        self.track_widget.update_track_name_display()
        
        # 3. On ferme la popin
        self.dismiss()

class MidiInputSelectorPopup(Popup):
    def __init__(self, track_widget, **kwargs) -> None:
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

    def select_port(self, port_name) -> None:
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


class TrackWidget(BoxLayout, HoverBehavior):
    """
    Represents a single track in the sequencer UI. It contains the track's info,
    a timeline for its content (which can be a piano roll for MIDI or a waveform for audio),
    and a set of controls (mute, solo, volume, etc.).
    """
    total_beats = NumericProperty(128.0) 
    pixels_per_beat = NumericProperty(dp(100))
    beats_per_measure = NumericProperty(4)
    timeline_container = ObjectProperty(None)
    info_width = NumericProperty(dp(150))
    controls_width = NumericProperty(dp(430))
    is_active_routing = BooleanProperty(False)    
    track_index = NumericProperty(0)
    is_minimized = BooleanProperty(False)
    previous_height = NumericProperty(dp(172))
        
    def __init__(self, track, track_index, sequencer_layout, **kwargs) -> None:
        kwargs.setdefault('orientation', 'vertical')
        super(TrackWidget, self).__init__(**kwargs)
        self.track = track
        self.track_index = track_index
        self.sequencer_layout = sequencer_layout
        # Initialisez une liste pour stocker les widgets de courbes pour les pistes d'automation
        self.automation_curves = []
        self.size_hint_y = None
        # Increase track height for better visibility, matching MIDI tracks
        self.height = dp(172)
        self.spacing = 0 # No spacing between content and resize handle
        
        self.padding = [0, 0, 0, 0]
        
        with self.canvas.before:
            self.bg_color = Color(0, 0, 0, 1)
            self._update_bg_color()
            # BIEN ASSIGNER À SELF ICI
            self.background_rect = Rectangle(pos=self.pos, size=self.size)

        with self.canvas.after:
            # On ne garde que le séparateur vertical (Section Gauche / Grille)
            Color(0, 0, 0, 1) # Noir pur pour la coupure verticale
            self.vert_separator = Rectangle(pos=self.pos, size=(dp(2), self.height))

        # Assurez-vous que le fond (background_rect) est bien défini dans canvas.before
        self.bind(pos=self._update_graphics, size=self._update_graphics)
        self.bind(height=self._on_height_changed)
        
        # Main row for track content (info, controls, timeline)
        self.main_row = BoxLayout(orientation='horizontal', size_hint_y=1, spacing=dp(12))
        self.add_widget(self.main_row)

        # Bottom resize handle
        self.resize_handle = ResizeHandle(track_widget=self)
        self.add_widget(self.resize_handle)

        # --- Left Section: Track Info ---
        # Fixed-height wrapper for info elements (except DragHandle)
        self.info_section = BoxLayout(size_hint_x=None, width=self.info_width, orientation='horizontal', spacing=dp(8))

        # Drag handle (far left) - Remains full height
        self.drag_handle = DragHandle(track_widget=self)
        self.info_section.add_widget(self.drag_handle)

        # Container for other info elements - Fixed at top, clipped if track is too small
        self.info_clipped_wrapper = StencilView(size_hint_x=1, size_hint_y=1)
        self.info_clipped_rel = RelativeLayout(size_hint=(None, None))
        self.info_clipped_wrapper.add_widget(self.info_clipped_rel)
        self.info_clipped_wrapper.bind(pos=self.info_clipped_rel.setter('pos'), size=self.info_clipped_rel.setter('size'))

        # Header bar for name and index - Pinned at top
        self.info_top_bar = BoxLayout(orientation='horizontal', spacing=dp(8), padding=[0, 0, dp(10), 0], size_hint=(1, None), height=dp(160), pos_hint={'top': 1})
        self.info_clipped_rel.add_widget(self.info_top_bar)

        self.info_section.add_widget(self.info_clipped_wrapper)

        labelPadding = [0, dp(1), 0, 0] if isinstance(self.track, AutomationTrack) else [0, dp(2), 0, 0]
        # Non-editable track index
        self.index_label = MDLabel(
            text=f"[{track_index}]",
            halign='left',
            valign='middle',
            theme_text_color="Custom",
            text_color=[0.7, 0.7, 0.7, 1],
            bold=True,
            size_hint_x=None,
            width=dp(30),
            padding=labelPadding,
        )
        self.info_top_bar.add_widget(self.index_label)

        # Editable track name
        self.name_label = EditableLabel(
            text=self.track.name,
            font_size=dp(14),
            bold=True,
            color=[0.9, 0.9, 0.9, 1],
            pos_hint={'center_y': 0.5},
            padding=[0, 0, 0, dp(1)] #bottom=1dp → monte le texte de 1 pixel
        )
        self.name_label.bind(on_text_validated=self.on_name_validated)
        self.info_top_bar.add_widget(self.name_label)

        # Minimize/Maximize button at bottom left of info section
        self.minimize_button = MDIconButton(
            icon='arrow-collapse-vertical',
            pos_hint={'x': 0, 'y': 0},
            size_hint=(None, None),
            size=(dp(24), dp(24)),
            opacity=0,
            theme_icon_color="Custom",
            icon_color=[1, 1, 1, 0.8],
            md_bg_color=[0, 0, 0, 0.5]
        )
        self.minimize_button.bind(on_release=self.toggle_minimize)
        self.info_clipped_rel.add_widget(self.minimize_button)

        # AJOUT : Section pour les pistes d'automation
        if isinstance(self.track, AutomationTrack):
            # 1. Icône de ciblage (flèche ou œil) avec tooltip explicatif
            self.target_indicator_icon = TooltipMDIconButton(
                icon='target', # Ou 'arrow-right-thin', 'eye', 'link'
                tooltip_text=self._get_target_track_name_for_tooltip(), # Fonction pour le nom
                pos_hint={'center_y': 0.5},
                theme_icon_color="Custom",
                icon_color=[0.9, 0.9, 0.9, 1],
                size_hint_x=None,
                on_release=self.open_change_target_popup,
                width=dp(24)
            )
            self.info_top_bar.add_widget(self.target_indicator_icon)
         
        # --- Middle Section: Controls ---
        # Robust clipping container
        self.controls_wrapper = StencilView(size_hint_x=None, width=self.controls_width, size_hint_y=1)
        self.controls_clipped_rel = RelativeLayout(size_hint=(None, None))
        self.controls_wrapper.add_widget(self.controls_clipped_rel)
        self.controls_wrapper.bind(pos=self.controls_clipped_rel.setter('pos'), size=self.controls_clipped_rel.setter('size'))

        self.controls_section = BoxLayout(size_hint=(1, None), height=dp(160), spacing=dp(8), pos_hint={'top': 1})
        self.controls_clipped_rel.add_widget(self.controls_section)

        # --- Solo Button (not for Automation tracks) ---
        if not isinstance(track, AutomationTrack):
            self.solo_button = TooltipMDIconButton(
                icon='alpha-s-box' if track.is_solo else 'alpha-s-box-outline',
                tooltip_text='Solo' if not track.is_solo else 'Unsolo',
                on_press=self.on_solo_toggle,
                pos_hint={'center_y': 0.5},
                theme_icon_color="Custom",
                icon_color=[1, 1, 0, 1] if track.is_solo else [1, 1, 1, 0.8],
                md_bg_color=[0.3, 0.3, 0.1, 0.8] if track.is_solo else [0.1, 0.1, 0.1, 0.8],
                size_hint=(None, None),
                size=(dp(36), dp(36))
            )
            self.controls_section.add_widget(self.solo_button)
        elif isinstance(track, AutomationTrack):
            # On récupère le type (midi/audio) de la piste cible
            target_track = self.sequencer_layout.sequencer.song.tracks[track.target_track_index]
            automation_type = 'midi' if isinstance(target_track, MidiTrack) else 'audio'

            # On crée les contrôles d'automation à la place du bouton Record
            self.automation_controls = AutomationControls(
                track_type=automation_type
            )
            # Liez l'événement personnalisé à la méthode de mise à jour
            self.automation_controls.bind(on_selection_change=self.update_automation_visibility)
            self.automation_controls.size_hint=(None, None)
            self.automation_controls.height = dp(36)
            self.automation_controls.width = dp(100)
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
                size_hint=(None, None),
                size=(dp(36), dp(36))
            )
            self.piano_roll_button.pos_hint = {'center_y': 0.5}
            self.controls_section.add_widget(self.piano_roll_button)

            self.record_mode_button = ThreeStateRecordButton(
                track=track,
                track_index=track_index,
                sequencer_layout=sequencer_layout,
                callback=self.on_record_mode_change
            )
            self.record_mode_button.pos_hint = {'center_y': 0.5}
            self.record_mode_button.size_hint = (None, None)
            self.record_mode_button.size = (dp(36), dp(36))
            self.controls_section.add_widget(self.record_mode_button)
        else:
            # Pour les pistes Audio standards, on garde l'espaceur de 44dp
            self.controls_section.add_widget(Widget(size_hint_x=None, width=dp(44)))

        # --- MIDI Specific Controls (Channel, Program) ---
        midi_controls_layout = BoxLayout(
            orientation='vertical',
            size_hint=(None, None),
            height=dp(160),
            width=dp(170),  # Increased width
            spacing=0,
            pos_hint={'top': 1}
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

            midi_controls_layout.add_widget(Widget(size_hint_y=1)) # Top spacer
            midi_controls_layout.add_widget(top_controls)
            midi_controls_layout.add_widget(self.port_selector_button)
            midi_controls_layout.add_widget(self.input_selector_button)
            midi_controls_layout.add_widget(Widget(size_hint_y=1)) # Bottom spacer
        else:
            midi_controls_layout.add_widget(Widget())
            
        self.controls_section.add_widget(midi_controls_layout)
        
        # --- Volume Controls ---
        volume_layout = BoxLayout(orientation='vertical', size_hint=(None, None), height=dp(160), width=dp(50), spacing=0, pos_hint={'top': 1})

        mute_button_container = BoxLayout(size_hint_y=None, height=dp(36), pos_hint={'center_x': 0.5})
        self.mute_button = TooltipMDIconButton(
            icon='volume-off' if track.is_muted else 'volume-high',
            tooltip_text='Mute' if not track.is_muted else 'Unmute',
            on_press=self.on_mute_toggle,
            pos_hint={'center_x': 0.5, 'center_y': 0.5},
            theme_icon_color="Custom",
            icon_color = [0.8, 0.3, 0, 1] if track.is_muted else [1, 0.6, 0, 1],
            md_bg_color = [0.4, 0.2, 0.1, 0.8] if track.is_muted else [0.3, 0.2, 0.1, 0.8],
            size_hint=(None, None),
            size=(dp(36), dp(36))
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
            pan_layout = BoxLayout(orientation='vertical', size_hint=(None, None), height=dp(160), width=dp(50), spacing=0, pos_hint={'top': 1})

            pan_icon_container = BoxLayout(size_hint_y=None, height=dp(36))
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
        self.left_panel = BoxLayout(
            orientation='horizontal',
            size_hint_x=None,
            spacing=dp(12)
        )
        self.left_panel.add_widget(self.info_section)
        self.left_panel.add_widget(self.controls_wrapper)
        self.left_panel.width = self.info_width + self.controls_width + dp(12)
        self.main_row.add_widget(self.left_panel)

        # --- Right Section: Timeline ---
        if isinstance(track, MidiTrack):
            note_height = dp(12)

            # 1. Keyboard (fixed width)
            self.keyboard_sv = ScrollView(size_hint_x=None, width=dp(40), do_scroll_x=False, do_scroll_y=True, effect_cls=ScrollEffect)
            self.keyboard_sv.effect_y = ScrollEffect()  # Bounded, no bounce
            self.piano_keyboard = PianoKeyboard(note_height=note_height)
            self.keyboard_sv.add_widget(self.piano_keyboard)

            # 2. Timeline ScrollView (expanding, with both x and y scroll)
            self.timeline_scroll = ScrollView(
                size_hint=(1, 1),
                do_scroll_x=True,
                do_scroll_y=True,
                effect_cls=ScrollEffect, # Désactive les rebonds (overscroll)
                bar_width=dp(2)
            )
            self.timeline_scroll.effect_x = ScrollEffect()
            self.timeline_scroll.effect_y = ScrollEffect()

            # Content container (RelativeLayout for local coordinate system)
            self.content = RelativeLayout(size_hint=(None, None))
            self.content.size = (self.total_beats * self.pixels_per_beat, 128 * note_height)

            # AJOUT : Préparation de la translation GPU
            with self.content.canvas.before:
                PushMatrix()
                self.g_translate = Translate(0, 0, 0) # On crée l'objet de translation
            with self.content.canvas.after:
                PopMatrix()

            self.timeline_container = self.content
            
            # Piano roll grid/notes
            self.piano_roll = PianoRoll(
                track=track,
                total_beats=self.total_beats,
                pixels_per_beat=self.pixels_per_beat,
                beat_per_measure=self.beats_per_measure,
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

            # --- SYNCHRONISATION SÉCURISÉE ---
            # On utilise une variable de verrouillage pour éviter que l'un n'entraîne l'autre à l'infini
            self._scrolling_locked = False

            self.timeline_scroll.add_widget(self.content)

            # Bind for size/zoom updates
            self.bind(total_beats=self.update_timeline_size, pixels_per_beat=self.update_timeline_size)

            # Link vertical scrolling between keyboard and timeline
            self.keyboard_sv.bind(scroll_y=lambda i, v: self._sync_vertical_scrolls(self.keyboard_sv, self.timeline_scroll, v))
            self.timeline_scroll.bind(scroll_y=lambda i, v: self._sync_vertical_scrolls(self.timeline_scroll, self.keyboard_sv, v))

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
                self.keyboard_sv.scroll_y = scroll_y
            Clock.schedule_once(set_default_scroll)

            # Add to main layout
            self.main_row.add_widget(self.keyboard_sv)
            self.main_row.add_widget(self.timeline_scroll)

        else:  # Audio and Automation tracks (unchanged, no vertical scroll)
            # Create a layout for the track type icon, fixed height at top
            self.icon_wrapper = StencilView(size_hint_x=None, width=dp(40), size_hint_y=1)
            self.icon_clipped_rel = RelativeLayout(size_hint=(None, None))
            self.icon_wrapper.add_widget(self.icon_clipped_rel)
            self.icon_wrapper.bind(pos=self.icon_clipped_rel.setter('pos'), size=self.icon_clipped_rel.setter('size'))

            self.icon_layout = BoxLayout(
                size_hint=(1, None),
                height=dp(160),
                orientation='vertical',
                pos_hint={'top': 1}
            )
            self.icon_clipped_rel.add_widget(self.icon_layout)

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
                valign='center',
                size_hint=(1, None),
                height=dp(40),
                pos_hint={'center_y': 0.5}
            )

            self.icon_layout.add_widget(Widget(size_hint_y=1)) # Top spacer
            self.icon_layout.add_widget(icon)
            self.icon_layout.add_widget(Widget(size_hint_y=1)) # Bottom spacer

            self.timeline_scroll = ScrollView(
                size_hint=(1, 1),
                do_scroll_x=True,
                do_scroll_y=False,
                bar_width=dp(4),
                scroll_type=['bars']
            )
            # Add to main layout
            self.main_row.add_widget(self.icon_wrapper)
            self.main_row.add_widget(self.timeline_scroll)
            self.timeline_scroll.effect_x = ScrollEffect()  # Bounded, no bounce

            # A ScrollView must have a single child.
            self.timeline_container = AutomationGrid(track_widget=self, size_hint=(None, 1))
            # Ensure container follows main_row height perfectly
            self.main_row.bind(height=self.timeline_container.setter('height'))
            self.timeline_container.height = self.main_row.height
            # AJOUT : Préparation de la translation GPU
            with self.timeline_container.canvas.before:
                PushMatrix()
                self.g_translate = Translate(0, 0, 0)
            with self.timeline_container.canvas.after:
                PopMatrix()            
            
            self.measure_grid = MeasureGrid(
                size_hint=(1, 1), # The grid itself can fill the container
                beat_per_measure=self.beats_per_measure,
                total_beats=self.total_beats,
                pixels_per_beat=self.pixels_per_beat
            )
            self.timeline_container.add_widget(self.measure_grid)

            if isinstance(self.track, AudioTrack):
                self.waveform = AudioWaveform(
                    filepath=self.track.filepath,
                    pixels_per_beat=self.pixels_per_beat,
                    total_beats=self.total_beats,
                    start_time=self.track.start_time,
                    tempo=self.sequencer_layout.sequencer.tempo,
                    size_hint=(None, 1), # Full height hint
                    pos_hint={'y': 0}
                )
                self.waveform.width = self.total_beats * self.pixels_per_beat

                # Bindings for zoom, length and tempo
                self.bind(pixels_per_beat=self._update_waveform_size)
                self.bind(total_beats=self._update_waveform_size)
                self.sequencer_layout.sequencer.bind(tempo=self.waveform.setter('tempo'))
                self.timeline_container.add_widget(self.waveform)

            if isinstance(self.track, AutomationTrack):
                # --- ÉTAPE 1 : IDENTIFIER LES PARAMÈTRES ---
                # On définit les paramètres par défaut + ceux présents dans les points
                params: set[str] = {"vol", "pan"} 
                for p in self.track.points:
                    params.add(p.parameter)

                # --- ÉTAPE 2 : CRÉER LES WIDGETS ---
                for param in params:
                    min_v, max_v = 0.0, 1.0
                    if param in ["prog", "vel"]: min_v, max_v = 0.0, 127.0
                    elif param == "pan": min_v, max_v = -1.0, 1.0

                    is_vol: bool = (param == "vol")
                    
                    # Filtrer les points pour ce widget
                    filtered_points: list[AutomationPoint] = [p for p in self.track.points if p.parameter == param]

                    curve_widget = AutomationCurveWidget(
                        size_hint=(1, 1),
                        total_beats=self.sequencer_layout.sequencer.get_song_length_in_beats(),
                        pixels_per_beat=self.pixels_per_beat,
                        min_val=min_v,
                        max_val=max_v,
                        points=filtered_points,
                        opacity=1 if is_vol else 0,
                        disabled=not is_vol
                    )
                    
                    curve_widget.param_type = param 
                    
                    # --- ÉTAPE 3 : BINDINGS DYNAMIQUES (Le secret du Zoom) ---
                    # On lie le widget aux propriétés du TrackWidget pour le zoom
                    self.bind(pixels_per_beat=curve_widget.setter('pixels_per_beat'))
                    self.bind(total_beats=curve_widget.setter('total_beats'))

                    self.automation_curves.append(curve_widget)
                    self.timeline_container.add_widget(curve_widget)

            self.timeline_scroll.add_widget(self.timeline_container)

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
        self.bind(track_index=self._on_track_index_change)
        
        # Liaison avec le séquenceur pour la mise à jour en temps réel
        self.sequencer_layout.sequencer.bind(
            current_beat=lambda instance, val: self.update_sliders_from_automation(val),
            current_routing_index=lambda inst, val: self._sync_routing_status(inst, val),
            is_recording=lambda inst, val: self._sync_recording_status(inst, val)
        )
        
        # Appel initial pour régler les sliders au chargement du projet
        Clock.schedule_once(lambda dt: self.update_sliders_from_automation(self.sequencer_layout.sequencer.current_beat))

    def _get_target_track_name_for_tooltip(self) -> str:
        """Retourne le nom de la piste cible pour le tooltip."""
        if not isinstance(self.track, AutomationTrack):
            return "" # Non applicable si ce n'est pas une automation

        sequencer = self.sequencer_layout.sequencer
        target_idx = self.track.target_track_index

        if 0 <= target_idx < len(sequencer.song.tracks):
            target_track = sequencer.song.tracks[target_idx]
            return f"Cible: Piste {target_idx}: {target_track.name}"
        else:
            return "Cible: Piste inconnue ou invalide"

    def open_change_target_popup(self, *args):
        # Puisque la classe est dans le même fichier, l'appel est direct
        popup = ChangeTargetPopup(track_widget=self)
        popup.open()

    def _sync_vertical_scrolls(self, source_sv, target_sv, value):
        """Helper to synchronize vertical scrolling between two ScrollViews."""
        if not self._scrolling_locked:
            self._scrolling_locked = True
            target_sv.scroll_y = value
            self._scrolling_locked = False

    def update_track_name_display(self):
        """Met à jour le texte du bouton d'index [#] et le tooltip de l'icône de ciblage."""
        if isinstance(self.track, AutomationTrack):
            # Mise à jour du bouton [#]
            if hasattr(self, 'target_btn'):
                for child in self.target_btn.children:
                    if isinstance(child, MDButtonText):
                        child.text = f"[{self.track.target_track_index}]"
            
            # Mise à jour du tooltip de l'icône de ciblage
            if hasattr(self, 'target_indicator_icon'):
                self.target_indicator_icon.tooltip_text = self._get_target_track_name_for_tooltip()
        
        # Mise à jour du label de nom (toujours)
        self.name_label.text = self.track.name
        
    def update_timeline_size(self, *args) -> None:
        if hasattr(self, 'content'):
            # For MIDI tracks
            self.content.width = self.total_beats * self.pixels_per_beat
            self.piano_roll.width = self.content.width
            
            # --- CORRECTION ICI ---
            # Il faut propager les nouvelles valeurs à l'instance piano_roll
            # avant d'appeler draw()
            self.piano_roll.total_beats = self.total_beats
            self.piano_roll.pixels_per_beat = self.pixels_per_beat
            self.piano_roll.beat_per_measure = self.beats_per_measure
            # ----------------------
            
            # Redraw is handled by property bindings in PianoRoll
        else:
            # For other tracks (unchanged)
            content_width = self.total_beats * self.pixels_per_beat
            self.timeline_container.width = content_width
            # Redraw is handled by property bindings in MeasureGrid
            self.measure_grid.total_beats = self.total_beats
            self.measure_grid.pixels_per_beat = self.pixels_per_beat
            self.measure_grid.beat_per_measure = self.beats_per_measure
            # --- FIX: Propagate zoom changes to ALL automation curve widgets ---
            if hasattr(self, 'automation_curves'):
                for curve in self.automation_curves:
                    curve.total_beats = self.total_beats
                    curve.pixels_per_beat = self.pixels_per_beat

    def update_playback_rect(self, *args) -> None:
        self.playback_rect.pos = self.playback_line.pos
        self.playback_rect.size = self.playback_line.size

    def on_solo_changed(self, instance, value) -> None:
        self.update_mute_solo_appearance()

    def on_enter(self, *args):
        super().on_enter(*args)
        if hasattr(self, 'minimize_button'):
            self.minimize_button.opacity = 1

    def on_leave(self, *args):
        super().on_leave(*args)
        if hasattr(self, 'minimize_button'):
            self.minimize_button.opacity = 0

    def toggle_minimize(self, *args):
        self.is_minimized = not self.is_minimized

        if self.is_minimized:
            self.previous_height = self.height
            self.height = dp(40)
            self.minimize_button.icon = 'arrow-expand-vertical'

            # Hide components
            self.resize_handle.opacity = 0
            self.resize_handle.disabled = True
            self.resize_handle.height = 0

            self.main_row.spacing = 0

            self.drag_handle.opacity = 0
            self.drag_handle.width = 0

            self.info_section.spacing = 0
            self.info_section.width = self.info_width - dp(12)

            # Adjust top bar for minimized state
            self.info_top_bar.height = self.height
            self.info_top_bar.pos_hint = {'top': 1}

            if hasattr(self, 'target_indicator_icon'):
                self.target_indicator_icon.opacity = 0
                self.target_indicator_icon.disabled = True

            self.controls_wrapper.opacity = 0
            self.controls_wrapper.disabled = True
            self.controls_wrapper.width = 0

            self.left_panel.spacing = 0
            self.left_panel.width = self.info_section.width

            self.timeline_scroll.opacity = 0
            self.timeline_scroll.disabled = True
            self.timeline_scroll.size_hint_x = None
            self.timeline_scroll.width = 0

            if hasattr(self, 'keyboard_sv'):
                self.keyboard_sv.opacity = 0
                self.keyboard_sv.disabled = True
                self.keyboard_sv.width = 0
            if hasattr(self, 'icon_wrapper'):
                self.icon_wrapper.opacity = 0
                self.icon_wrapper.disabled = True
                self.icon_wrapper.width = 0
        else:
            # Restore to previous height or at least 80dp
            self.height = max(dp(80), self.previous_height)
            self.minimize_button.icon = 'arrow-collapse-vertical'

            # Show components
            self.resize_handle.opacity = 1
            self.resize_handle.disabled = False
            self.resize_handle.height = dp(12)

            self.main_row.spacing = dp(12)

            self.drag_handle.opacity = 1
            self.drag_handle.width = dp(12)

            self.info_section.spacing = dp(8)
            self.info_section.width = self.info_width

            # Restore original heights/proportions
            self.info_top_bar.height = dp(160)
            self.info_top_bar.pos_hint = {'top': 1}

            if hasattr(self, 'target_indicator_icon'):
                self.target_indicator_icon.opacity = 1
                self.target_indicator_icon.disabled = False

            self.controls_wrapper.opacity = 1
            self.controls_wrapper.disabled = False
            self.controls_wrapper.width = self.controls_width

            self.left_panel.spacing = dp(12)
            self.left_panel.width = self.info_width + self.controls_width + dp(12)

            self.timeline_scroll.opacity = 1
            self.timeline_scroll.disabled = False
            self.timeline_scroll.size_hint_x = 1

            if hasattr(self, 'keyboard_sv'):
                self.keyboard_sv.opacity = 1
                self.keyboard_sv.disabled = False
                self.keyboard_sv.width = dp(40)
            if hasattr(self, 'icon_wrapper'):
                self.icon_wrapper.opacity = 1
                self.icon_wrapper.disabled = False
                self.icon_wrapper.width = dp(40)

    def _on_track_index_change(self, instance, value):
        self.index_label.text = f"[{int(value)}]"
        self._update_bg_color()

    def _on_height_changed(self, instance, value):
        """Called when the TrackWidget's height changes."""
        # Force redraw of editors/grids that might depend on height
        if hasattr(self, 'piano_roll'):
            self.piano_roll.redraw()
        if hasattr(self, 'measure_grid'):
            self.measure_grid.redraw()
        if hasattr(self, 'waveform'):
            self.waveform.redraw()

    def _sync_routing_status(self, instance, value):
        # On met à jour l'état et on force la couleur IMMEDIATEMENT
        is_active = (self.track_index == value)
        if is_active != self.is_active_routing:
            self.is_active_routing = is_active
            self._update_bg_color()

        if hasattr(self, 'record_mode_button'):
            self.record_mode_button.update_appearance()

    def _sync_recording_status(self, instance, value):
        if hasattr(self, 'record_mode_button'):
            self.record_mode_button.update_appearance()

    def _update_bg_color(self, *args):
        """
        Update the background color of the track widget based on its state.
        
        Sets the background color to a blue highlight if the track is actively being routed,
        otherwise alternates between two dark gray shades based on the track's index position.
        
        Args:
            *args: Variable length argument list (typically used for Kivy event callbacks).
        """
        if self.is_active_routing:
            self.bg_color.rgba = [0.15, 0.35, 0.55, 1] # More visible blue highlight for active routing
        elif self.track_index % 2 == 0:
            self.bg_color.rgba = [0.08, 0.08, 0.08, 1]
        else:
            self.bg_color.rgba = [0.11, 0.11, 0.11, 1]


    def on_touch_move(self, touch):
        if touch.grab_current is self:
            if hasattr(self.sequencer_layout, 'on_track_drag_move'):
                self.sequencer_layout.on_track_drag_move(self, touch)
            return True
        if self.resize_handle.collide_point(*touch.pos):
             return self.resize_handle.on_touch_move(touch)
        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
            if hasattr(self.sequencer_layout, 'on_track_drag_end'):
                self.sequencer_layout.on_track_drag_end(self, touch)
            return True
        if self.resize_handle.collide_point(*touch.pos):
             return self.resize_handle.on_touch_up(touch)
        return super().on_touch_up(touch)

    def on_touch_down(self, touch):
        """
        Handle touch events for track selection.
        If the sequencer is stopped and the user clicks on the track (but not on a control),
        select this track's instrument.
        """
        if self.collide_point(*touch.pos):
            # Special case for ResizeHandle which is a direct child but we want to let it grab the touch
            if hasattr(self, 'resize_handle') and self.resize_handle.collide_point(*touch.pos):
                 return self.resize_handle.on_touch_down(touch)

            # We let the default Kivy processing happen first for buttons/sliders.
            # super().on_touch_down(touch) returns True if a child consumed the touch.
            if super().on_touch_down(touch):
                return True

            # Ignore mouse wheel events for track selection
            if hasattr(touch, 'button') and touch.button in ('scrollup', 'scrolldown', 'scrollleft', 'scrollright'):
                return False

            # If the touch wasn't consumed by a child (button, slider, etc.)
            # and the sequencer is stopped, we select this track.
            seq = self.sequencer_layout.sequencer
            if seq.playback_state == "stopped":
                # Manual override of the MIDI routing for the instrument selection.
                # We tell the JackManager to target this track specifically.
                seq.jack_manager._manual_routing_override = self.track_index
                # We need to refresh the UI to show the new routing.
                seq.current_routing_index = self.track_index
                return True
        return False

    def on_automation_selection_change(self, selected_param) -> None:
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

    def on_name_validated(self, instance, new_name) -> None:
        """Callback for when the user validates a new track name."""
        # We need to escape the name in case it contains spaces in the future
        # although the current filter doesn't allow it.
        command = f'rename {self.track_index} "{new_name}"'
        self.sequencer_layout.process_command_ui(command)

    def update_automation_visibility(self, instance, selected_param):
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

    def _update_waveform_size(self, *args):
        if hasattr(self, 'waveform'):
            self.waveform.pixels_per_beat = self.pixels_per_beat
            self.waveform.total_beats = self.total_beats
            self.waveform.width = self.total_beats * self.pixels_per_beat

    def set_playback_position(self, current_beat: float) -> None:
        if self.playback_line:
            self.playback_line.x = current_beat * self.pixels_per_beat

    def update_grid_parameters(self, total_beats: float, pixels_per_beat: float) -> None:
        """Called by the parent layout to propagate zoom/length changes to this widget."""
        self.total_beats = total_beats
        self.pixels_per_beat = pixels_per_beat

    def reset_timeline_view(self):
        """Resets the scroll position and playback line to the beginning."""
        if self.playback_line:
            self.playback_line.x = 0 
        self.timeline_scroll.scroll_x = 0.0

    def _update_graphics(self, *args):
        # Sécurité : on vérifie que background_rect existe
        if not hasattr(self, 'background_rect'):
            return

        self.background_rect.pos = self.pos
        self.background_rect.size = self.size

        if hasattr(self, 'vert_separator'):
            if self.is_minimized:
                self.vert_separator.size = (0, 0)
            else:
                # On cherche le point de séparation le plus fiable
                # On utilise le bord droit du left_panel pour placer le séparateur
                if hasattr(self, 'left_panel'):
                    split_x = self.left_panel.right + dp(6)
                else:
                    # Fallback basé sur les largeurs connues
                    split_x = self.x + self.info_width + self.controls_width + dp(6)

                self.vert_separator.pos = (split_x - dp(1), self.main_row.y)
                self.vert_separator.size = (dp(2), self.main_row.height)

    def _update_type_icon_bg(self, *args) -> None:
        """Updates the background of the track type icon."""
        if hasattr(self, 'type_bg_rect'):
            self.type_bg_rect.pos = self.children[-1].pos
            self.type_bg_rect.size = self.children[-1].size
        if hasattr(self, 'type_border_rect'):
            self.type_border_rect.rectangle = [self.children[-1].x, self.children[-1].y, 
                                             self.children[-1].width, self.children[-1].height]

    def on_track_volume_changed(self, instance, value) -> None:
        """Callback for when the track's volume property changes in the backend model."""
        self.volume_label.text = f"{int(value * 100)}"
        if abs(self.volume_slider.value - value) > 0.001:
            self.volume_slider.value = value

    def on_volume_change(self, instance, value) -> None:
        """Callback for when the user moves the volume slider."""
        self.volume_label.text = f"{int(value * 100)}"
        self.sequencer_layout.process_slider_command(f'volume {self.track_index} {value}')

    def on_pan_change(self, instance, value) -> None:
        """Callback for when the user moves the pan slider."""
        self.pan_label.text = f"{value:+.1f}"
        self.sequencer_layout.process_slider_command(f'pan {self.track_index} {value}')

    def on_mute_toggle(self, instance) -> None:
        """Called when the mute button is pressed."""
        self.sequencer_layout.toggle_track_mute(self.track_index)

    def on_solo_toggle(self, instance) -> None:
        """Called when the solo button is pressed."""
        self.sequencer_layout.toggle_track_solo(self.track_index)

    def update_mute_solo_appearance(self) -> None:
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

    def get_record_mode_tooltip(self, mode) -> str:
        """Returns the appropriate tooltip text for the given record mode."""
        tooltips: dict[str, str] = {
            'OFF': 'Record: OFF - Piste désactivée',
            'OVERWRITE': 'Record: OVERWRITE - Écrase les notes existantes',
            'KEEP': 'Record: KEEP - Conserve les notes existantes'
        }
        return tooltips.get(mode, 'Record Mode')

    def on_record_mode_change(self, track) -> None:
        """Callback for when the record mode button changes state."""
        print(f"Record mode changed for track {self.track_index}: {track.record_mode}")
        self.record_mode_button.tooltip_text = self.get_record_mode_tooltip(track.record_mode)

    def on_channel_change(self, instance) -> None:
        """Callback for when the MIDI channel spinner value changes."""
        self.sequencer_layout.process_slider_command(f'setch {self.track_index} {instance.text}')

    def on_program_change(self, instance) -> None:
        """Callback for when the MIDI program spinner value changes."""
        self.sequencer_layout.process_slider_command(f'setprog {self.track_index} {instance.text}')

    def on_set_as_metronome(self, instance) -> None:
        """Callback for a potential future feature to set a track as the metronome source."""
        self.sequencer_layout.process_command_ui(f'setmetrotrack {self.track_index}')

    def _find_existing_editor(self):
        if self.sequencer_layout.window_manager:
            for window in self.sequencer_layout.window_manager.children:
                if hasattr(window, 'source_track') and window.source_track is self.track:
                    return window
        return None

    def open_piano_roll_editor(self, instance=None) -> None:
        """Creates and opens the piano roll editor for the current track."""
        if isinstance(self.track, MidiTrack):
            existing = self._find_existing_editor()
            if existing:
                existing._bring_to_front()
                return

            sequencer = self.sequencer_layout.sequencer
            
            editor = PianoRollEditor(
                track=self.track,
                sequencer_layout=self.sequencer_layout,
                size_hint=(0.9, 0.8),
                pos_hint={'center_x': 0.5, 'center_y': 0.5}
            )
            if self.sequencer_layout.window_manager:
                self.sequencer_layout.window_manager.add_widget(editor)

    def open_automation_editor(self):
        if isinstance(self.track, AutomationTrack):
            existing = self._find_existing_editor()
            if existing:
                existing._bring_to_front()
                return
            
            sequencer = self.sequencer_layout.sequencer

            # On demande à l'objet automation_controls quel paramètre est actif
            active_param = 'vol' # Valeur de sécurité
            if hasattr(self, 'automation_controls') and self.automation_controls.selected_param:
                active_param = self.automation_controls.selected_param            

            # On passe ce paramètre à l'initialisation de l'éditeur
            editor = AutomationEditor(
                track=self.track,
                sequencer_layout=self.sequencer_layout,
                initial_param=active_param,
                pixels_per_beat=self.pixels_per_beat,
                size_hint=(0.9, 0.8),
                pos_hint={'center_x': 0.5, 'center_y': 0.5}
            )
            
            if self.sequencer_layout.window_manager:
                self.sequencer_layout.window_manager.add_widget(editor)


    def select_midi_port_popup(self, instance) -> None:
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

        def select_port(port_name) -> None:
            command: str = f'assign {self.track_index} "{port_name}"'
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

    def select_midi_input_popup(self, instance) -> None:
        """Opens a popup to select a MIDI input port for the track."""
        popup: MidiInputSelectorPopup = MidiInputSelectorPopup(track_widget=self)
        popup.open()

    def update_sliders_from_automation(self, current_beat) -> None:
        if isinstance(self.track, AutomationTrack):
            return

        vol_slider = getattr(self, 'volume_slider', None)
        pan_slider = getattr(self, 'pan_slider', None)
        if not vol_slider or not pan_slider:
            return

        found_vol = False
        found_pan = False

        # On cherche l'automation cible
        for t in self.sequencer_layout.sequencer.song.tracks:
            if isinstance(t, AutomationTrack) and t.target_track_index == self.track_index:
                
                # VOLUME : On récupère les points pour ce paramètre précis
                points_vol = [p for p in t.points if p.parameter == 'vol']
                if points_vol:
                    found_vol = True
                    # ON APPLIQUE LA VALEUR (C'est ça qui fait bouger le slider)
                    vol_slider.value = t.get_value_at(current_beat, 'vol')
                
                # PAN
                points_pan = [p for p in t.points if p.parameter == 'pan']
                if points_pan:
                    found_pan = True
                    pan_slider.value = t.get_value_at(current_beat, 'pan')

        # Mise à jour des drapeaux (utile pour changer l'opacité ou l'icône)
        self.vol_automated = found_vol
        self.pan_automated = found_pan

        # IMPORTANT : On s'assure que le slider est TOUJOURS utilisable
        # On ne met JAMAIS disabled = True ici.
        vol_slider.disabled = False
        pan_slider.disabled = False
        
        # Optionnel : baisser légèrement l'opacité pour indiquer qu'une automation "pilote" le slider
        vol_slider.opacity = 0.7 if found_vol else 1.0
        pan_slider.opacity = 0.7 if found_pan else 1.0
        
    '''
    def _update_live_notes(self, instance, value):
        if not hasattr(self, 'piano_keyboard'):
            return

        # Get notes for this specific track
        notes = value.get(self.track_index, [])
        if self.piano_keyboard.highlighted_notes != notes:
            self.piano_keyboard.highlighted_notes = notes
    '''                    