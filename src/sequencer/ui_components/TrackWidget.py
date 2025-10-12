from . import *  # Importe tous les imports communs
from sequencer.models import MidiTrack, AudioTrack, AutomationTrack

class TrackWidget(BoxLayout):
    def __init__(self, track, track_index, sequencer_layout, **kwargs):
        super(TrackWidget, self).__init__(**kwargs)
        self.track = track
        self.track_index = track_index
        self.sequencer_layout = sequencer_layout
        self.orientation = 'horizontal'
        self.size_hint_y = None
        self.height = dp(56)  # Augmenté la hauteur pour plus d'espace
        self.spacing = dp(12)  # Espacement augmenté entre les éléments
        self.padding = [dp(12), dp(6), dp(12), dp(6)]  # Padding augmenté

        # Ajouter un fond coloré pour mieux distinguer les pistes
        with self.canvas.before:
            # Fond alterné pour mieux séparer les lignes
            Color(0.15, 0.15, 0.15, 1) if track_index % 2 == 0 else Color(0.12, 0.12, 0.12, 1)
            self.background_rect = Rectangle(pos=self.pos, size=self.size)
            # Bordure fine en bas pour séparer les pistes
            Color(0.3, 0.3, 0.3, 0.5)
            self.border_line = Line(points=[self.x, self.y, self.x + self.width, self.y], width=0.5)

        self.bind(pos=self._update_graphics, size=self._update_graphics)

        # 1. Track Info (Index and Name) - Expands to fill space
        name_label = Label(
            text=f"[{track_index}] {track.name}",
            size_hint_x=0.8,  # Utiliser size_hint_x au lieu de size_hint_x=1
            halign='left',
            valign='middle',
            shorten=True,
            shorten_from='right',
            text_size=(None, None),
            color=[0.9, 0.9, 0.9, 1],
            font_size=dp(14),
            bold=True
        )
        name_label.bind(
            size=lambda *x: setattr(name_label, 'text_size', (name_label.width, None)),
            texture_size=lambda *x: setattr(name_label, 'height', name_label.texture_size[1])
        )
        self.add_widget(name_label)

        # 2. Track Type Icon avec fond
        type_icon_layout = BoxLayout(
            size_hint_x=None,
            width=dp(44),
            size_hint_y=None,
            height=dp(44),
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

       # 3. BOUTON RECORD MODE (MODIFIÉ)
        if isinstance(track, MidiTrack):
            self.record_mode_button = ThreeStateRecordButton(
                track=track,
                track_index=track_index,  # NOUVEAU
                sequencer_layout=sequencer_layout,  # NOUVEAU
                callback=self.on_record_mode_change
            )
            self.add_widget(self.record_mode_button)
        else:
            # Pour les pistes non-MIDI, ajouter un espaceur de même largeur
            self.add_widget(Widget(size_hint_x=None, width=dp(44)))

        type_icon = MDIcon(
            icon=track_type_icon,
            theme_text_color="Custom",
            text_color=track_type_color,
            size_hint_x=None,
            size_hint_y=None,
            width=dp(36),
            height=dp(36),
            pos_hint={'center_y': 0.5},
            font_size=dp(20)
        )
        type_icon_layout.add_widget(type_icon)
        self.add_widget(type_icon_layout)

        # 3. Solo Button avec style amélioré
        self.solo_button = TooltipMDIconButton(
            icon='alpha-s-box' if track.is_solo else 'alpha-s-box-outline',
            tooltip_text='Solo' if not track.is_solo else 'Unsolo',
            on_press=self.on_solo_toggle,
            size_hint_x=None,
            width=dp(44),
            size_hint_y=None,
            height=dp(44),
            pos_hint={'center_y': 0.5},
            theme_icon_color="Custom",
            icon_color=[1, 1, 0, 1] if track.is_solo else [0.6, 0.6, 0.6, 1],
            theme_bg_color="Custom",
            md_bg_color=[0.3, 0.3, 0.1, 0.8] if track.is_solo else [0.1, 0.1, 0.1, 0.8]
        )
        self.add_widget(self.solo_button)

        # 4. MIDI Controls (or a spacer of the same size)
        midi_controls_layout = BoxLayout(
            size_hint_x=None, 
            width=dp(260),  # 125 + 4 + 125 + marge = ~260
            spacing=dp(8),
            size_hint_y=None, 
            height=dp(44),
            pos_hint={'center_y': 0.5}
        )

        if isinstance(track, MidiTrack):
            # Channel
            channel_container = BoxLayout(
                size_hint_x=None,
                width=dp(125),
                orientation='horizontal',
                spacing=dp(4),
                size_hint_y=None,
                height=dp(44),
                pos_hint={'center_y': 0.5}
            )
            
            channel_label = Label(
                text='Channel:',
                size_hint_x=None,
                width=dp(42),
                size_hint_y=None,
                height=dp(44),
                halign='right',
                valign='middle',
                color=[0.9, 0.9, 0.9, 1],
                font_size=dp(13),
            )
            channel_container.add_widget(channel_label)
            
            channel_spinner = ValueSpinner(
                min_val=1,
                max_val=16,
                initial_value=track.channel + 1,
                callback=self.on_channel_change
            )
            channel_spinner.height = dp(32)
            channel_container.add_widget(channel_spinner)
            midi_controls_layout.add_widget(channel_container)

            # RÉDUIRE FORTEMENT l'espace entre Channel et Program
            midi_controls_layout.add_widget(Widget(size_hint_x=None, width=dp(4)))  # Espace très réduit

            # Program
            program_container = BoxLayout(
                size_hint_x=None,
                width=dp(125),
                orientation='horizontal',
                spacing=dp(4),
                size_hint_y=None,
                height=dp(44),
                pos_hint={'center_y': 0.5}
            )
            
            program_label = Label(
                text='Program:',
                size_hint_x=None,
                width=dp(42),
                size_hint_y=None,
                height=dp(44),
                halign='right',
                valign='middle',
                color=[0.9, 0.9, 0.9, 1],
                font_size=dp(13),
            )
            program_container.add_widget(program_label)
            
            program_spinner = ValueSpinner(
                min_val=1,
                max_val=128,
                initial_value=track.instrument + 1,
                callback=self.on_program_change
            )
            program_spinner.height = dp(32)
            program_container.add_widget(program_spinner)
            midi_controls_layout.add_widget(program_container)

        else:
            # Pour les pistes non-MIDI, on garde la même largeur
            midi_controls_layout.add_widget(Widget(size_hint_x=None, width=dp(300)))
            
        self.add_widget(midi_controls_layout)
        
        # 5. Volume Slider avec Label - layout amélioré
        volume_layout = BoxLayout(
            size_hint_x=None, 
            width=dp(200), 
            spacing=dp(8), 
            pos_hint={'center_y': 0.5}
        )
        
        mute_button = TooltipMDIconButton(
            icon='volume-off' if track.is_muted else 'volume-high',
            tooltip_text='Mute' if not track.is_muted else 'Unmute',
            on_press=self.on_mute_toggle,
            size_hint_x=None,
            width=dp(32),
            size_hint_y=None,
            height=dp(32),
            pos_hint={'center_y': 0.5},
            theme_icon_color="Custom",
            icon_color=[1, 0.6, 0, 1] if not track.is_muted else [0.8, 0.3, 0, 1],
            theme_bg_color="Custom",
            md_bg_color=[0.3, 0.2, 0.1, 0.8] if not track.is_muted else [0.4, 0.2, 0.1, 0.8]
        )
        volume_layout.add_widget(mute_button)
        
        self.volume_label = Label(
            text=f"{int(track.volume * 100)}", 
            size_hint_x=None, 
            width=dp(35),
            color=[0.9, 0.9, 0.9, 1],
            font_size=dp(12),
            halign='center'
        )
        
        self.volume_slider = MDSlider(
            min=0, 
            max=1, 
            value=track.volume,
            size_hint_x=1,
            height=dp(20),
            pos_hint={'center_y': 0.5}
        )
        self.volume_slider.bind(value=self.on_volume_change)
        
        volume_layout.add_widget(self.volume_label)
        volume_layout.add_widget(self.volume_slider)
        self.add_widget(volume_layout)

        # 6. Pan Slider avec Label - layout amélioré
        pan_layout = BoxLayout(
            size_hint_x=None, 
            width=dp(200), 
            spacing=dp(8), 
            pos_hint={'center_y': 0.5}
        )
        
        pan_icon = MDIcon(
            icon='swap-horizontal',
            theme_text_color='Custom',
            text_color=[0.6, 0.6, 1, 1],
            size_hint_x=None,
            size_hint_y=None,
            width=dp(32),
            height=dp(32),
            pos_hint={'center_y': 0.5},
            font_size=dp(18)
        )
        pan_layout.add_widget(pan_icon)

        self.pan_label = Label(
            text=f"{track.pan:+.1f}",  # Format avec signe +
            size_hint_x=None, 
            width=dp(35),
            color=[0.9, 0.9, 0.9, 1],
            font_size=dp(12),
            halign='center'
        )
        
        self.pan_slider = MDSlider(
            min=-1, 
            max=1, 
            value=track.pan,
            size_hint_x=1,
            height=dp(20),
            pos_hint={'center_y': 0.5}
        )
        self.pan_slider.bind(value=self.on_pan_change)
        
        pan_layout.add_widget(self.pan_label)
        pan_layout.add_widget(self.pan_slider)
        self.add_widget(pan_layout)

    def _update_graphics(self, *args):
        if hasattr(self, 'background_rect'):
            self.background_rect.pos = self.pos
            self.background_rect.size = self.size
        if hasattr(self, 'border_line'):
            self.border_line.points = [self.x, self.y, self.x + self.width, self.y]

    def _update_type_icon_bg(self, *args):
        if hasattr(self, 'type_bg_rect'):
            self.type_bg_rect.pos = self.children[-1].pos  # type_icon_layout est le dernier ajouté
            self.type_bg_rect.size = self.children[-1].size
        if hasattr(self, 'type_border_rect'):
            self.type_border_rect.rectangle = [self.children[-1].x, self.children[-1].y, 
                                             self.children[-1].width, self.children[-1].height]

    def on_volume_change(self, instance, value):
        self.volume_label.text = f"{int(value * 100)}"
        self.sequencer_layout.process_slider_command(f'volume {self.track_index} {value}')

    def on_pan_change(self, instance, value):
        self.pan_label.text = f"{value:+.1f}"  # Format avec signe +
        self.sequencer_layout.process_slider_command(f'pan {self.track_index} {value}')

    def on_mute_toggle(self, instance):
        self.sequencer_layout.process_command_ui(f'mute {self.track_index}')
        # La mise à jour visuelle se fera via update_status_display

    def on_solo_toggle(self, instance):
        self.sequencer_layout.process_command_ui(f'solo {self.track_index}')
        # La mise à jour visuelle se fera via update_status_display

    def get_record_mode_tooltip(self, mode):
        """Retourne le texte du tooltip selon le mode"""
        tooltips = {
            'OFF': 'Record: OFF - Piste désactivée',
            'OVERWRITE': 'Record: OVERWRITE - Écrase les notes existantes',
            'KEEP': 'Record: KEEP - Conserve les notes existantes'
        }
        return tooltips.get(mode, 'Record Mode')

    def on_record_mode_change(self, track):
        """Callback quand le mode d'enregistrement change"""
        print(f"Record mode changed for track {self.track_index}: {track.record_mode}")
        # Mettre à jour le tooltip
        self.record_mode_button.tooltip_text = self.get_record_mode_tooltip(track.record_mode)
        # Ici vous pouvez ajouter la logique pour informer le séquenceur
        # self.sequencer_layout.process_command_ui(f'recordmode {self.track_index} {track.record_mode}')

    def on_channel_change(self, instance):
        self.sequencer_layout.process_slider_command(f'setch {self.track_index} {instance.text}')

    def on_program_change(self, instance):
        self.sequencer_layout.process_slider_command(f'setprog {self.track_index} {instance.text}')

    def on_set_as_metronome(self, instance):
        self.sequencer_layout.process_command_ui(f'setmetrotrack {self.track_index}')
