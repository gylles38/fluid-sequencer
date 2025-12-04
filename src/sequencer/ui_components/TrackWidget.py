from . import *  # Importe tous les imports communs
from sequencer.models import MidiTrack, AudioTrack, AutomationTrack
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.scrollview import ScrollView
from kivy.properties import NumericProperty, ObjectProperty
from kivy.uix.widget import Widget 
from kivy.uix.label import Label 
from kivy.metrics import dp
from sequencer.ui_components.MeasureGrid import MeasureGrid

class TrackWidget(BoxLayout):
    # Propriétés de contrôle des dimensions de la grille
    total_beats = NumericProperty(128.0) 
    pixels_per_beat = NumericProperty(dp(100))
    timeline_container = ObjectProperty(None)
        
    def __init__(self, track, track_index, sequencer_layout, **kwargs):
        super(TrackWidget, self).__init__(**kwargs)
        self.track = track
        self.track_index = track_index
        self.sequencer_layout = sequencer_layout
        self.orientation = 'horizontal' # Conteneur principal: Nom | Timeline | Contrôles
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

        # 1. Track Info (Index and Name)
        self.name_label = Label(
            text=f"[{track_index}] {track.name}",
            size_hint_x=None, 
            width=dp(150), # Largeur fixe pour le nom
            halign='left', 
            valign='middle', 
            color=[0.9, 0.9, 0.9, 1],
            font_size=dp(14),
            bold=True            
        )
        self.add_widget(self.name_label)

        # ScrollView pour le défilement horizontal de la timeline
        self.timeline_scroll = ScrollView(size_hint_x=1, do_scroll_y=False) # Prend tout l'espace restant
        
        # Conteneur interne : un Widget de taille variable (la largeur sera mise à jour dans update_timeline_size)
        self.timeline_container = Widget(size_hint=(None, 1)) 
        
        # Grille de Mesures (en premier plan pour être en arrière-plan)
        self.measure_grid = MeasureGrid(
            size_hint=(1, 1), 
            beat_per_measure=4, 
            total_beats=self.total_beats,
            pixels_per_beat=self.pixels_per_beat
        )
        self.timeline_container.add_widget(self.measure_grid)

        # Conteneur d'événements (où vos notes/clips seront dessinés PAR DESSUS la grille)
        # Il doit aussi avoir size_hint=(1, 1) pour s'aligner avec measure_grid
        self.event_container = BoxLayout(size_hint=(1, 1), padding=dp(2))
        self.timeline_container.add_widget(self.event_container)
       
        # NOUVEAU : Tête de lecture (Playback Head)
        self.playback_line = Widget(size_hint_x=None, width=dp(2), size_hint_y=1)
        with self.playback_line.canvas:
            Color(1, 0, 0, 0.8) # Rouge vif
            self.playback_rect = Rectangle(pos=self.playback_line.pos, size=self.playback_line.size)
        self.playback_line.bind(pos=self.update_playback_rect, size=self.update_playback_rect)
        
        # Ajouter la ligne de lecture en DERNIER pour qu'elle soit au-dessus de tout
        self.timeline_container.add_widget(self.playback_line)        
        
        self.timeline_scroll.add_widget(self.timeline_container)
        self.add_widget(self.timeline_scroll) # Ajout au conteneur principal (TrackWidget)

        # Lier les propriétés à la mise à jour de la taille du conteneur
        self.bind(total_beats=self.update_timeline_size, pixels_per_beat=self.update_timeline_size)
        
        # Mise à jour initiale
        self.update_timeline_size()

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
        
        self.mute_button = TooltipMDIconButton(
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
        volume_layout.add_widget(self.mute_button)
        
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
        
        # Bind the track's volume property (from the backend model) to a UI-updating callback.
        self.track.bind(volume=self.on_track_volume_changed)

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

        # Bind UI updates to property changes
        self.track.bind(is_solo=self.on_solo_changed)

    def on_solo_changed(self, instance, value):
        """Callback for when the track's solo property changes from the backend."""
        self.update_mute_solo_appearance()

    def update_playback_rect(self, *args):
        if self.playback_rect:
            self.playback_rect.pos = self.playback_line.pos
            self.playback_rect.size = self.playback_line.size

    def update_timeline_size(self, *args):
        # Met à jour la largeur du conteneur pour correspondre à la longueur du morceau et au zoom.
        if not self.timeline_container: return

        width = self.total_beats * self.pixels_per_beat
        
        scroll_view_width = self.timeline_scroll.width 
        
        # Calcul de la marge utilisée pour le centrage (la même que dans set_playback_position)
        margin_x = scroll_view_width * 0.3 

        # ⚠️ CORRECTION CRITIQUE ⚠️
        # La largeur minimale du contenu doit inclure la ScrollView width PLUS la marge de fin
        # pour permettre à la tête de lecture de se centrer sur le dernier beat du morceau.
        min_width = scroll_view_width 
        
        EPSILON_PIXELS = dp(1)
        # Largeur de la piste + une marge de fin pour que le dernier beat puisse être centré.
        required_width = width + margin_x + EPSILON_PIXELS
        
        # La largeur finale doit être au moins la largeur de la ScrollView ou la largeur requise.
        final_width = max(required_width, min_width)
        
        self.timeline_container.width = final_width
        
        # Assurez-vous que le MeasureGrid reçoit les paramètres de mise à jour.
        self.measure_grid.total_beats = self.total_beats
        self.measure_grid.pixels_per_beat = self.pixels_per_beat
        
        print(f"DEBUG: update_timeline_size width= {width}, margin_x={margin_x}, final_width= {final_width}")

    def set_playback_position(self, current_beat: float):
        """Met à jour la tête de lecture et force le défilement si nécessaire."""
        
        EPSILON_PIXELS = dp(1) 
        pixels_per_beat = self.pixels_per_beat
        
        # timeline_width inclut maintenant la marge de fin
        timeline_width = self.timeline_container.width 
        scroll_view_width = self.timeline_scroll.width
        
        # 1. Mise à jour de la position du curseur
        x_pos = current_beat * pixels_per_beat
        if self.playback_line:
            self.playback_line.x = x_pos 
            
        # Vérification des dimensions (si le contenu est plus petit que la ScrollView)
        if timeline_width <= scroll_view_width + EPSILON_PIXELS:
            self.timeline_scroll.scroll_x = 0.0
            return
                
        margin_x = scroll_view_width * 0.3 
        
        # ⚠️ CORRECTION CRITIQUE DU DÉPLACEMENT MAXIMAL
        # Nous ajoutons EPSILON_PIXELS pour s'assurer que le MAX_DISPLACEMENT_PHYSICAL
        # n'est jamais trop petit à cause des erreurs de flottant, garantissant que scroll_x = 1.0 est atteignable
        MAX_DISPLACEMENT_PHYSICAL = timeline_width - scroll_view_width + EPSILON_PIXELS
        
        # -----------------------------------------------------------
        # CRITIQUE 1 : CORRECTION DU PINNAGE AU DÉPART 
        if x_pos < margin_x: 
            self.timeline_scroll.scroll_x = 0.0
            return
        # -----------------------------------------------------------

        
        # --- 2. Logique de Défilement Automatique ---

        # current_scroll_x_pixels doit être calculé avec le nouveau MAX_DISPLACEMENT_PHYSICAL
        current_scroll_x_pixels = self.timeline_scroll.scroll_x * MAX_DISPLACEMENT_PHYSICAL
        new_scroll_x_pixels = -1

        # Cas A: Défilement vers la DROITE 
        if x_pos > current_scroll_x_pixels + scroll_view_width - margin_x:
            new_scroll_x_pixels = x_pos - (scroll_view_width - margin_x)
            
        # Cas B: Défilement vers la GAUCHE 
        elif x_pos < current_scroll_x_pixels + margin_x and current_scroll_x_pixels > EPSILON_PIXELS:
            new_scroll_x_pixels = x_pos - margin_x
            
        
        if new_scroll_x_pixels == -1:
            return

        # -----------------------------------------------------------
        # CRITIQUE 2 : LIMITE DE FIN DE PISTE
        
        # Plafonnement des pixels de défilement
        new_scroll_x_pixels = max(0, min(new_scroll_x_pixels, MAX_DISPLACEMENT_PHYSICAL))

        # Normalisation L->R 
        # ⚠️ Plafonner la division pour éviter l'erreur si MAX_DISPLACEMENT_PHYSICAL est nul ou très proche de zéro
        if MAX_DISPLACEMENT_PHYSICAL < EPSILON_PIXELS:
            normalized_scroll_value = 0.0
        else:
            normalized_scroll_value = new_scroll_x_pixels / MAX_DISPLACEMENT_PHYSICAL
        
        # Appliquer le défilement (doit être entre 0.0 et 1.0)
        self.timeline_scroll.scroll_x = max(0.0, min(1.0, normalized_scroll_value))


    def update_grid_parameters(self, total_beats: float, pixels_per_beat: float):
        """Méthode appelée par SequencerApp pour mettre à jour les paramètres de la grille."""
        self.total_beats = total_beats
        self.pixels_per_beat = pixels_per_beat

    def reset_timeline_view(self):
        """Force la vue à se positionner à l'extrême gauche (beat 0) et le curseur à 0."""
        # S'assurer que le curseur est à 0 (même si la lecture ne démarre pas à 0)
        if self.playback_line:
            self.playback_line.x = 0 
        
        # S'assurer que la ScrollView est à l'extrême gauche
        self.timeline_scroll.scroll_x = 0.0

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

    def on_track_volume_changed(self, instance, value):
        """
        Callback for when the track's volume property changes from the backend.
        This updates the UI to reflect the new state.
        """
        # Always update the text label to ensure it's in sync.
        self.volume_label.text = f"{int(value * 100)}"

        # Update the slider's position only if it has changed significantly,
        # to prevent a feedback loop (backend -> UI -> backend -> ...).
        if abs(self.volume_slider.value - value) > 0.001:
            self.volume_slider.value = value

    def on_volume_change(self, instance, value):
        self.volume_label.text = f"{int(value * 100)}"
        self.sequencer_layout.process_slider_command(f'volume {self.track_index} {value}')

    def on_pan_change(self, instance, value):
        self.pan_label.text = f"{value:+.1f}"  # Format avec signe +
        self.sequencer_layout.process_slider_command(f'pan {self.track_index} {value}')

    def on_mute_toggle(self, instance):
        # Directly call a lightweight method on the main layout
        self.sequencer_layout.toggle_track_mute(self.track_index)

    def on_solo_toggle(self, instance):
        # Directly call a lightweight method on the main layout
        self.sequencer_layout.toggle_track_solo(self.track_index)

    def update_mute_solo_appearance(self):
        """Updates the visual state of mute and solo buttons."""
        # Update Mute Button
        is_muted = self.track.is_muted
        self.mute_button.icon = 'volume-off' if is_muted else 'volume-high'
        self.mute_button.tooltip_text = 'Unmute' if is_muted else 'Mute'
        self.mute_button.icon_color = [0.8, 0.3, 0, 1] if is_muted else [1, 0.6, 0, 1]
        self.mute_button.md_bg_color = [0.4, 0.2, 0.1, 0.8] if is_muted else [0.3, 0.2, 0.1, 0.8]

        # Update Solo Button
        is_solo = self.track.is_solo
        self.solo_button.icon = 'alpha-s-box' if is_solo else 'alpha-s-box-outline'
        self.solo_button.tooltip_text = 'Unsolo' if is_solo else 'Solo'
        self.solo_button.icon_color = [1, 1, 0, 1] if is_solo else [0.6, 0.6, 0.6, 1]
        self.solo_button.md_bg_color = [0.3, 0.3, 0.1, 0.8] if is_solo else [0.1, 0.1, 0.1, 0.8]

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
