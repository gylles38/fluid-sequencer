from . import *
from sequencer.models import MidiTrack, AudioTrack, AutomationTrack
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.relativelayout import RelativeLayout
from kivy.properties import NumericProperty, ObjectProperty
from kivy.uix.widget import Widget
from kivy.uix.label import Label
from kivy.metrics import dp
from sequencer.ui_components.MeasureGrid import MeasureGrid

class TrackWidget(BoxLayout):
    total_beats = NumericProperty(128.0)
    pixels_per_beat = NumericProperty(dp(100))
    timeline_container = ObjectProperty(None)
    info_width = NumericProperty(dp(150))
    controls_width = NumericProperty(dp(832))

    def __init__(self, track, track_index, sequencer_layout, **kwargs):
        super(TrackWidget, self).__init__(**kwargs)
        self.track = track
        self.track_index = track_index
        self.sequencer_layout = sequencer_layout
        self.orientation = 'horizontal'
        self.size_hint_y = None
        self.height = dp(56)
        self.spacing = dp(12)
        self.padding = [dp(12), dp(6), dp(12), dp(6)]

        with self.canvas.before:
            Color(0.15, 0.15, 0.15, 1) if track_index % 2 == 0 else Color(0.12, 0.12, 0.12, 1)
            self.background_rect = Rectangle(pos=self.pos, size=self.size)
            Color(0.3, 0.3, 0.3, 0.5)
            self.border_line = Line(points=[self.x, self.y, self.x + self.width, self.y], width=0.5)

        self.bind(pos=self._update_graphics, size=self._update_graphics)

        self.info_section = BoxLayout(size_hint_x=None, width=self.info_width)
        self.name_label = Label(
            text=f"[{track_index}] {track.name}",
            halign='left',
            valign='middle',
            color=[0.9, 0.9, 0.9, 1],
            font_size=dp(14),
            bold=True
        )
        self.info_section.add_widget(self.name_label)
        self.add_widget(self.info_section)

        # The timeline_container is now a RelativeLayout that acts as a viewport.
        # It fills the available space and clips its content.
        self.timeline_container = RelativeLayout(size_hint_x=None)

        # The timeline_content is the scrollable part inside the viewport.
        self.timeline_content = Widget(size_hint=(None, 1))
        self.timeline_container.add_widget(self.timeline_content)

        self.measure_grid = MeasureGrid(size_hint=(None, 1))
        self.timeline_content.add_widget(self.measure_grid)

        self.event_container = BoxLayout(size_hint=(None, 1), padding=dp(2))
        self.timeline_content.add_widget(self.event_container)

        self.playback_line = Widget(size_hint_x=None, width=dp(2), size_hint_y=1)
        with self.playback_line.canvas:
            Color(1, 0, 0, 0.8)
            self.playback_rect = Rectangle(pos=self.playback_line.pos, size=self.playback_line.size)
        self.playback_line.bind(pos=self.update_playback_rect, size=self.update_playback_rect)
        self.timeline_content.add_widget(self.playback_line)

        self.add_widget(self.timeline_container)

        self.bind(total_beats=self.update_timeline_size, pixels_per_beat=self.update_timeline_size)
        self.update_timeline_size()

        self.controls_section = BoxLayout(size_hint_x=None, width=self.controls_width, spacing=dp(8))

        type_icon_layout = BoxLayout(
            size_hint_x=None,
            width=dp(44),
            pos_hint={'center_y': 0.5},
            padding=dp(4)
        )
        
        track_type_icon = "help-circle"
        track_type_color = [0.5, 0.5, 0.5, 1]
        bg_color = [0.2, 0.2, 0.2, 1]
        
        if isinstance(track, MidiTrack):
            track_type_icon = "midi"
            track_type_color = [0.3, 0.5, 0.9, 1]
            bg_color = [0.2, 0.3, 0.4, 0.3]
        elif isinstance(track, AudioTrack):
            track_type_icon = "waveform"
            track_type_color = [0.9, 0.5, 0.2, 1]
            bg_color = [0.4, 0.3, 0.2, 0.3]
        elif isinstance(track, AutomationTrack):
            track_type_icon = "chart-line"
            track_type_color = [0.2, 0.8, 0.8, 1]
            bg_color = [0.2, 0.4, 0.4, 0.3]

        with type_icon_layout.canvas.before:
            Color(*bg_color)
            self.type_bg_rect = Rectangle(pos=type_icon_layout.pos, size=type_icon_layout.size)
            Color(0.4, 0.4, 0.4, 0.5)
            self.type_border_rect = Line(rectangle=[type_icon_layout.x, type_icon_layout.y, 
                                                   type_icon_layout.width, type_icon_layout.height], width=1)

        type_icon_layout.bind(pos=self._update_type_icon_bg, size=self._update_type_icon_bg)

        if isinstance(track, MidiTrack):
            self.record_mode_button = ThreeStateRecordButton(
                track=track,
                track_index=track_index,
                sequencer_layout=sequencer_layout,
                callback=self.on_record_mode_change
            )
            self.controls_section.add_widget(self.record_mode_button)
        else:
            self.controls_section.add_widget(Widget(size_hint_x=None, width=dp(44)))

        type_icon = MDIcon(
            icon=track_type_icon,
            theme_text_color="Custom",
            text_color=track_type_color,
            pos_hint={'center_x': 0.5, 'center_y': 0.5},
            font_size=dp(20)
        )
        type_icon_layout.add_widget(type_icon)
        self.controls_section.add_widget(type_icon_layout)

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

        midi_controls_layout = BoxLayout(
            size_hint_x=None, 
            width=dp(260),
            spacing=dp(8),
            pos_hint={'center_y': 0.5}
        )

        if isinstance(track, MidiTrack):
            channel_container = BoxLayout(orientation='horizontal', spacing=dp(4))
            channel_label = Label(text='Channel:', size_hint_x=None, width=dp(42), halign='right', valign='middle', color=[0.9, 0.9, 0.9, 1], font_size=dp(13))
            channel_container.add_widget(channel_label)
            channel_spinner = ValueSpinner(min_val=1, max_val=16, initial_value=track.channel + 1, callback=self.on_channel_change, height=dp(32))
            channel_container.add_widget(channel_spinner)
            midi_controls_layout.add_widget(channel_container)

            program_container = BoxLayout(orientation='horizontal', spacing=dp(4))
            program_label = Label(text='Program:', size_hint_x=None, width=dp(42), halign='right', valign='middle', color=[0.9, 0.9, 0.9, 1], font_size=dp(13))
            program_container.add_widget(program_label)
            program_spinner = ValueSpinner(min_val=1, max_val=128, initial_value=track.instrument + 1, callback=self.on_program_change, height=dp(32))
            program_container.add_widget(program_spinner)
            midi_controls_layout.add_widget(program_container)
        else:
            midi_controls_layout.add_widget(Widget())
            
        self.controls_section.add_widget(midi_controls_layout)
        
        volume_layout = BoxLayout(size_hint_x=None, width=dp(200), spacing=dp(8), pos_hint={'center_y': 0.5})
        self.mute_button = TooltipMDIconButton(
            icon='volume-off' if track.is_muted else 'volume-high',
            tooltip_text='Mute' if not track.is_muted else 'Unmute',
            on_press=self.on_mute_toggle,
            pos_hint={'center_y': 0.5},
            theme_icon_color="Custom",
            icon_color=[1, 0.6, 0, 1] if not track.is_muted else [0.8, 0.3, 0, 1],
            md_bg_color=[0.3, 0.2, 0.1, 0.8] if not track.is_muted else [0.4, 0.2, 0.1, 0.8]
        )
        volume_layout.add_widget(self.mute_button)
        self.volume_label = Label(text=f"{int(track.volume * 100)}", size_hint_x=None, width=dp(35), color=[0.9, 0.9, 0.9, 1], font_size=dp(12), halign='center')
        self.volume_slider = MDSlider(min=0, max=1, value=track.volume, size_hint_x=1, pos_hint={'center_y': 0.5})
        self.volume_slider.bind(value=self.on_volume_change)
        self.track.bind(volume=self.on_track_volume_changed)
        volume_layout.add_widget(self.volume_label)
        volume_layout.add_widget(self.volume_slider)
        self.controls_section.add_widget(volume_layout)

        pan_layout = BoxLayout(size_hint_x=None, width=dp(200), spacing=dp(8), pos_hint={'center_y': 0.5})
        pan_icon = MDIcon(icon='swap-horizontal', theme_text_color='Custom', text_color=[0.6, 0.6, 1, 1], pos_hint={'center_y': 0.5}, font_size=dp(18))
        pan_layout.add_widget(pan_icon)
        self.pan_label = Label(text=f"{track.pan:+.1f}", size_hint_x=None, width=dp(35), color=[0.9, 0.9, 0.9, 1], font_size=dp(12), halign='center')
        self.pan_slider = MDSlider(min=-1, max=1, value=track.pan, size_hint_x=1, pos_hint={'center_y': 0.5})
        self.pan_slider.bind(value=self.on_pan_change)
        pan_layout.add_widget(self.pan_label)
        pan_layout.add_widget(self.pan_slider)
        self.controls_section.add_widget(pan_layout)

        self.add_widget(self.controls_section)
        self.track.bind(is_solo=self.on_solo_changed)

    def on_solo_changed(self, instance, value):
        self.update_mute_solo_appearance()

    def update_playback_rect(self, *args):
        if self.playback_rect:
            self.playback_rect.pos = self.playback_line.pos
            self.playback_rect.size = self.playback_line.size

    def update_timeline_size(self, *args):
        """Updates the width of all timeline components."""
        if not self.timeline_content: return

        new_width = self.total_beats * self.pixels_per_beat
        self.timeline_container.width = new_width
        self.timeline_content.width = new_width
        self.measure_grid.width = new_width
        self.event_container.width = new_width
        
        self.measure_grid.total_beats = self.total_beats
        self.measure_grid.pixels_per_beat = self.pixels_per_beat

    def set_playback_position(self, current_beat: float):
        """Updates the visual position of the playback line within the timeline_content."""
        x_pos = current_beat * self.pixels_per_beat
        if self.playback_line:
            self.playback_line.x = x_pos

    def reset_timeline_view(self):
        """Forces the playback line to position 0."""
        if self.playback_line:
            self.playback_line.x = 0

    def _update_graphics(self, *args):
        if hasattr(self, 'background_rect'):
            self.background_rect.pos = self.pos
            self.background_rect.size = self.size
        if hasattr(self, 'border_line'):
            self.border_line.points = [self.x, self.y, self.x + self.width, self.y]

    def _update_type_icon_bg(self, *args):
        if hasattr(self, 'type_bg_rect'):
            self.type_bg_rect.pos = self.children[-1].pos
            self.type_bg_rect.size = self.children[-1].size
        if hasattr(self, 'type_border_rect'):
            self.type_border_rect.rectangle = [self.children[-1].x, self.children[-1].y,
                                             self.children[-1].width, self.children[-1].height]

    def on_track_volume_changed(self, instance, value):
        self.volume_label.text = f"{int(value * 100)}"
        if abs(self.volume_slider.value - value) > 0.001:
            self.volume_slider.value = value

    def on_volume_change(self, instance, value):
        self.volume_label.text = f"{int(value * 100)}"
        self.sequencer_layout.process_slider_command(f'volume {self.track_index} {value}')

    def on_pan_change(self, instance, value):
        self.pan_label.text = f"{value:+.1f}"
        self.sequencer_layout.process_slider_command(f'pan {self.track_index} {value}')

    def on_mute_toggle(self, instance):
        self.sequencer_layout.toggle_track_mute(self.track_index)

    def on_solo_toggle(self, instance):
        self.sequencer_layout.toggle_track_solo(self.track_index)

    def update_mute_solo_appearance(self):
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
        tooltips = {
            'OFF': 'Record: OFF - Piste désactivée',
            'OVERWRITE': 'Record: OVERWRITE - Écrase les notes existantes',
            'KEEP': 'Record: KEEP - Conserve les notes existantes'
        }
        return tooltips.get(mode, 'Record Mode')

    def on_record_mode_change(self, track):
        print(f"Record mode changed for track {self.track_index}: {track.record_mode}")
        self.record_mode_button.tooltip_text = self.get_record_mode_tooltip(track.record_mode)

    def on_channel_change(self, instance):
        self.sequencer_layout.process_slider_command(f'setch {self.track_index} {instance.text}')

    def on_program_change(self, instance):
        self.sequencer_layout.process_slider_command(f'setprog {self.track_index} {instance.text}')

    def on_set_as_metronome(self, instance):
        self.sequencer_layout.process_command_ui(f'setmetrotrack {self.track_index}')
