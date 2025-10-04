import kivy
kivy.require('2.3.1') # replace with your kivy version

from kivymd.app import MDApp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.textinput import TextInput
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.filechooser import FileChooserListView
from kivymd.uix.slider import MDSlider
from kivy.properties import StringProperty
from kivy.uix.widget import Widget
from kivymd.uix.button import MDIconButton, MDButton, MDButtonText
from kivymd.uix.menu import MDDropdownMenu
from kivy.metrics import dp
from kivy.core.window import Window
from kivy.clock import Clock
from kivy.logger import Logger
from kivy.graphics import Color, Rectangle

from sequencer.sequencer import Sequencer
from sequencer.models import MidiTrack, AudioTrack, AutomationTrack
import sys

class TooltipMDIconButton(MDIconButton):
    tooltip_text = StringProperty()

    def __init__(self, **kwargs):
        self.tooltip_text = kwargs.pop("tooltip_text", "")
        super().__init__(**kwargs)

        # paramètres du tooltip
        self.tooltip_delay = 0.2          # secondes avant affichage
        self.tooltip_label = None         # widget du tooltip
        self._show_event = None           # référence Clock pour annuler

        # on écoute le mouvement de la souris une seule fois (global)
        Window.bind(mouse_pos=self.on_mouse_pos)

    # ------------------------------------------------------------------
    # 1. Détection du survol
    # ------------------------------------------------------------------
    def on_mouse_pos(self, window, pos):
        # convertir la position de la fenêtre → position locale du bouton
        collide = self.collide_point(*self.to_widget(*pos))

        if collide:
            # on entre / on reste sur le bouton
            self._ensure_tooltip()
            self._update_tooltip_position(pos)
            if not self._show_event:
                # planifier l’affichage (on ne le fait qu’une fois)
                self._show_event = Clock.schedule_once(
                    self._do_show_tooltip, self.tooltip_delay
                )
        else:
            # on sort du bouton
            self._hide_tooltip()

    # ------------------------------------------------------------------
    # 2. Création du Label (une seule fois)
    # ------------------------------------------------------------------
    def _ensure_tooltip(self):
        if self.tooltip_label:
            return

        self.tooltip_label = Label(
            text=self.tooltip_text,
            size_hint=(None, None),
            size=(dp(120), dp(32)),
            color=(1, 1, 1, 1),
            font_size=dp(14),
        )
        # fond noir semi‑transparent
        with self.tooltip_label.canvas.before:
            Color(0, 0, 0, 0.85)
            self._bg_rect = Rectangle(pos=self.tooltip_label.pos,
                                      size=self.tooltip_label.size)

        # mise à jour du fond quand le label bouge/redimensionne
        self.tooltip_label.bind(pos=self._update_bg, size=self._update_bg)

    # ------------------------------------------------------------------
    # 3. Mise à jour du fond
    # ------------------------------------------------------------------
    def _update_bg(self, instance, value):
        if hasattr(self, "_bg_rect"):
            self._bg_rect.pos = instance.pos
            self._bg_rect.size = instance.size

    # ------------------------------------------------------------------
    # 4. Affichage réel (après le délai)
    # ------------------------------------------------------------------
    def _do_show_tooltip(self, dt):
        if self.tooltip_label and self.tooltip_label.parent is None:
            # on ajoute le tooltip à la fenêtre (coordonnées absolues)
            Window.add_widget(self.tooltip_label)
        self._show_event = None

    # ------------------------------------------------------------------
    # 5. Mise à jour de la position tant que la souris reste dessus
    # ------------------------------------------------------------------
    def _update_tooltip_position(self, mouse_pos):
        if not self.tooltip_label:
            return

        # convertir la position de la souris en coordonnées de la fenêtre
        # on place le tooltip juste au‑dessus du pointeur
        x, y = mouse_pos
        self.tooltip_label.pos = (x + dp(10), y + dp(10))

        # mettre à jour le fond (le bind ci‑dessus le fait déjà,
        # mais on le force ici pour éviter un décalage d’une frame)
        self._update_bg(self.tooltip_label, None)

    # ------------------------------------------------------------------
    # 6. Masquage
    # ------------------------------------------------------------------
    def _hide_tooltip(self):
        if self._show_event:
            self._show_event.cancel()
            self._show_event = None

        if self.tooltip_label and self.tooltip_label.parent:
            Window.remove_widget(self.tooltip_label)
            # on ne détruit pas le widget, on le garde pour le ré‑afficher
            # (on le retire juste de l’arbre)

    # ------------------------------------------------------------------
    # Nettoyage à la destruction du bouton
    # ------------------------------------------------------------------
    def on_parent(self, instance, parent):
        # Si le bouton est retiré de l’arbre, on enlève aussi le tooltip
        if parent is None:
            self._hide_tooltip()
            if self.tooltip_label:
                self.tooltip_label = None

class ValueSpinner(BoxLayout):
    def __init__(self, min_val, max_val, initial_value, callback, **kwargs):
        super(ValueSpinner, self).__init__(**kwargs)
        self.min_val = min_val
        self.max_val = max_val
        self.callback = callback
        self.last_valid_value = initial_value
        self.orientation = 'horizontal'
        self.size_hint_y = None
        self.height = dp(30)

        minus_button = TooltipMDIconButton(
            icon='minus',
            tooltip_text='Decrement',
            on_press=self.decrement,
            theme_icon_color="Custom",
            icon_color=[0.9, 0.3, 0.3, 1], # Reddish
            theme_bg_color="Custom",
            md_bg_color=[0.1, 0.1, 0.1, 1]
        )
        self.add_widget(minus_button)

        self.text_input = TextInput(
            text=str(initial_value),
            multiline=False,
            halign='center',
            padding=[dp(6), dp(6), dp(6), dp(6)],
            size_hint_x=None,
            width=dp(50)
        )
        self.text_input.bind(on_text_validate=self.on_text_change)
        self.add_widget(self.text_input)

        plus_button = TooltipMDIconButton(
            icon='plus',
            tooltip_text='Increment',
            on_press=self.increment,
            theme_icon_color="Custom",
            icon_color=[0.3, 0.9, 0.3, 1], # Greenish
            theme_bg_color="Custom",
            md_bg_color=[0.1, 0.1, 0.1, 1]
        )
        self.add_widget(plus_button)

    def _update_value(self, new_value):
        self.text_input.text = str(new_value)
        self.last_valid_value = new_value
        self.callback(self.text_input)

    def increment(self, instance):
        try:
            value = int(self.text_input.text)
            if value < self.max_val:
                self._update_value(value + 1)
        except ValueError:
            self.text_input.text = str(self.last_valid_value)

    def decrement(self, instance):
        try:
            value = int(self.text_input.text)
            if value > self.min_val:
                self._update_value(value - 1)
        except ValueError:
            self.text_input.text = str(self.last_valid_value)

    def on_text_change(self, instance):
        try:
            value = int(instance.text)
            if not (self.min_val <= value <= self.max_val):
                value = max(self.min_val, min(value, self.max_val))
        except ValueError:
            value = self.last_valid_value

        self._update_value(value)

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

        # Créer le FileChooser avec chemin et filtres
        self.filechooser = FileChooserListView(
            path='/home/gilles/fluid-sequencer',  # Chemin initial
            filters=['*.proj.json', '*.mid']     # Extensions à filtrer
        )  
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
        self.is_playing = False  # Propriété pour suivre l'état du bouton play 

        # Menu Bar
        menu_bar = BoxLayout(size_hint_y=None, height=40, padding=5)
        file_button = MDButton(
            MDButtonText(
                text="File",
            ),
            style="elevated",
            pos_hint={'center_y': 0.5}
        )

        menu_items = [
            {"leading_icon": "file-plus", "text": "New Project", "on_release": lambda: self.new_project_popup()},
            {"leading_icon": "folder-open", "text": "Load Project", "on_release": lambda: self.load_project_popup()},
            {"leading_icon": "content-save", "text": "Save Project", "on_release": lambda: self.save_project()},
            {"leading_icon": "content-save-edit", "text": "Save Project As...", "on_release": lambda: self.save_project_as_popup()},
            {"leading_icon": "exit-to-app", "text": "Quit", "on_release": lambda: self.process_command_ui('quit')},
        ]
        self.file_menu = MDDropdownMenu(
            caller=file_button,
            items=menu_items,
        )
        file_button.bind(on_release=lambda x: self.file_menu.open())
        menu_bar.add_widget(file_button)
        self.add_widget(menu_bar)

        # Status Display
        status_layout = BoxLayout(size_hint_y=None, height=40, spacing=10, padding=5)
        self.song_name_label = Label(text="Song: New Song", size_hint_x=None, width=200, halign='left', text_size=(200, None))
        self.tempo_label = Label(text="Tempo: 120 BPM", size_hint_x=None, width=120, halign='left', text_size=(120, None))
        self.timesig_label = Label(text="Time Sig: 4/4", size_hint_x=None, width=100, halign='left', text_size=(100, None))
        self.metronome_button = TooltipMDIconButton(
            icon='metronome',
            tooltip_text="Toggle Metronome (M)",
            on_press=self.toggle_metronome
        )
        self.playhead_label = Label(text="Position: 1:1", size_hint_x=None, width=120, halign='left', text_size=(120, None))
        status_layout.add_widget(self.song_name_label)
        status_layout.add_widget(self.tempo_label)
        status_layout.add_widget(self.timesig_label)
        status_layout.add_widget(self.metronome_button)
        status_layout.add_widget(self.playhead_label)
        status_layout.add_widget(Widget()) # Spacer to push everything to the left
        self.add_widget(status_layout)

# Transport Controls
        transport_layout = BoxLayout(
            size_hint_y=None,
            height=40,
            spacing=10,  # Espacement fixe entre les widgets
            padding=5,
            orientation='horizontal'
        )

        # Label et TextInput pour Start
        start_label = Label(
            text='Start:',
            size_hint_x=None,
            width=50,  # Largeur fixe pour le label
            halign='left',
            text_size=(50, None)  # Limiter la taille du texte pour éviter le débordement
        )
        self.start_pos_input = TextInput(
            text='1:1',
            multiline=False,
            size_hint_x=None,
            width=60  # Largeur fixe pour le TextInput
        )
        transport_layout.add_widget(start_label)
        transport_layout.add_widget(self.start_pos_input)

        # Label et TextInput pour End
        end_label = Label(
            text='End:',
            size_hint_x=None,
            width=50,
            halign='left',
            text_size=(50, None)
        )
        self.end_pos_input = TextInput(
            text='',
            multiline=False,
            size_hint_x=None,
            width=60
        )
        self.end_pos_input.bind(on_text_validate=self.on_end_pos_manual_set)
        transport_layout.add_widget(end_label)
        transport_layout.add_widget(self.end_pos_input)

        # Boutons avec icônes, couleurs et tooltips
        self.play_button = TooltipMDIconButton(
            icon='play',
            tooltip_text='Play',
            size_hint_x=None,
            width=dp(40),
            theme_icon_color="Custom",
            icon_color=[0, 0.7, 0.3, 1],  # Vert
            theme_bg_color="Custom",
            md_bg_color=[0.1, 0.1, 0.1, 1]  # Gris foncé
        )
        loop_button = TooltipMDIconButton(
            icon='repeat',
            tooltip_text='Loop',
            size_hint_x=None,
            width=dp(40),
            theme_icon_color="Custom",
            icon_color=[0.2, 0.6, 0.8, 1],  # Blue for loop
            theme_bg_color="Custom",
            md_bg_color=[0.1, 0.1, 0.1, 1]
        )
        pause_button = TooltipMDIconButton(
            icon='pause',
            tooltip_text='Pause',
            size_hint_x=None,
            width=dp(40),
            theme_icon_color="Custom",
            icon_color=[0.9, 0.9, 0.2, 1],  # Yellow for pause
            theme_bg_color="Custom",
            md_bg_color=[0.1, 0.1, 0.1, 1]
        )
        stop_button = TooltipMDIconButton(
            icon='stop',
            tooltip_text='Stop',
            size_hint_x=None,
            width=dp(40),
            theme_icon_color="Custom",
            icon_color=[0.8, 0.2, 0.2, 1],  # Red for stop
            theme_bg_color="Custom",
            md_bg_color=[0.1, 0.1, 0.1, 1]
        )
        record_button = TooltipMDIconButton(
            icon='record',
            tooltip_text='Record',
            size_hint_x=None,
            width=dp(40),
            theme_icon_color="Custom",
            icon_color=[1, 0, 0, 1],  # Bright Red for record
            theme_bg_color="Custom",
            md_bg_color=[0.1, 0.1, 0.1, 1]
        )

        # Bind des actions aux boutons
        self.play_button.bind(on_press=self.play_pressed)
        loop_button.bind(on_press=self.loop_pressed)
        pause_button.bind(on_press=lambda x: self.process_command_ui('pause'))
        stop_button.bind(on_press=lambda x: self.process_command_ui('stop'))
        record_button.bind(on_press=lambda x: self.process_command_ui('record'))

        # Ajout des boutons au layout
        transport_layout.add_widget(self.play_button)
        transport_layout.add_widget(loop_button)
        transport_layout.add_widget(pause_button)
        transport_layout.add_widget(stop_button)
        transport_layout.add_widget(record_button)

        # Ajouter un widget vide pour occuper l'espace restant
        transport_layout.add_widget(Widget(size_hint_x=1))

        # Ajouter le transport_layout au widget principal
        self.add_widget(transport_layout)

        # Track List (Mixer)
        self.track_list_layout = BoxLayout(orientation='vertical')
        self.add_widget(self.track_list_layout)

        # Bottom controls
        bottom_layout = BoxLayout(orientation='vertical', size_hint_y=0.3)
        self.output_label = Label(size_hint_y=0.1, text="Welcome!") # For general feedback
        bottom_layout.add_widget(self.output_label)

        self.input_text = TextInput(size_hint_y=0.1, multiline=False)
        self.input_text.bind(on_text_validate=self.on_enter)
        bottom_layout.add_widget(self.input_text)

        self.send_button = TooltipMDIconButton(icon='send', tooltip_text='Send', size_hint_y=0.1)
        self.send_button.bind(on_press=self.on_enter)
        bottom_layout.add_widget(self.send_button)
        self.add_widget(bottom_layout)

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
        # Basculer l'état du bouton
        self.is_playing = not self.is_playing
        if self.is_playing:
            # État actif (enfoncé)
            self.play_button.md_bg_color = [0, 0.5, 0, 1]  # Vert pour indiquer l'état actif
            self.play_button.icon = 'play-circle'  # Optionnel : changer l'icône
            self.process_command_ui('play')
        else:
            self.is_playing = False
            # État au repos
            self.play_button.md_bg_color = [0.2, 0.2, 0.2, 1]  # Gris pour l'état repos
            self.play_button.icon = 'play'  # Revenir à l'icône par défaut
            self.process_command_ui('pause')  # Ou 'pause', selon votre logique
            return            

    # Méthodes factices pour éviter les erreurs (remplacez par vos implémentations)        
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

    def toggle_metronome(self, instance):
        # Optimistically update the icon
        if instance.icon == 'metronome':
            instance.icon = 'metronome-tick'
            self.process_command_ui('metronome on')
        else:
            instance.icon = 'metronome'
            self.process_command_ui('metronome off')

    def update_playhead_display(self, instance, value):
        self.playhead_label.text = f"Position: {self.sequencer._format_beats_to_position(value)}"

    def update_track_list(self):
        self.track_list_layout.clear_widgets()
        for i, track in enumerate(self.sequencer.song.tracks):
            # Hide the metronome track from the mixer view if it's flagged as such
            if isinstance(track, MidiTrack) and track.is_metronome:
                continue
            track_widget = TrackWidget(track=track, track_index=i, sequencer_layout=self)
            self.track_list_layout.add_widget(track_widget)

    def update_status_display(self):
        song = self.sequencer.song
        self.song_name_label.text = f"Song: {song.name}"
        self.tempo_label.text = f"Tempo: {song.tempo} BPM"
        self.timesig_label.text = f"Time Sig: {song.time_signature_numerator}/{song.time_signature_denominator}"
        self.metronome_button.icon = 'metronome-tick' if song.metronome_enabled else 'metronome'

        # Update end position input, but only if the user hasn't manually set it.
        if not self.end_pos_manual_override:
            end_of_song_beats = self.sequencer.get_song_length_in_beats()
            self.end_pos_input.text = self.sequencer._format_beats_to_position(end_of_song_beats)

        self.update_track_list()

    def on_enter(self, instance):
        command = self.input_text.text
        self.input_text.text = ''
        self.process_command_ui(command)

    def process_slider_command(self, command):
        # This is a lightweight version of process_command_ui that does not
        # trigger a full UI refresh, which would interrupt the slider drag.
        from main import process_command
        # We don't handle prompts here as sliders are not interactive.
        process_command(command, self.sequencer, api_mode=True, confirmation_handler=None)

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

        # Always do a full refresh to ensure UI is in sync with backend state.
        self.update_status_display()

        if not should_continue:
            MDApp.get_running_app().stop()


class SequencerApp(MDApp):
    def build(self):
        self.theme_cls.theme_style = "Dark"
        self.theme_cls.primary_palette = "Blue"
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
        self.height = 48
        self.spacing = 8
        self.padding = [8, 4, 8, 4]

        # 1. Track Info (Index and Name) - Expands to fill space
        name_label = Label(
            text=f"[{track_index}] {track.name}",
            size_hint_x=1,  # Allow this widget to expand
            halign='left',
            valign='middle',
            shorten=True,
            shorten_from='right',
            ellipsis_options={'color':(0.8,0.8,0.8,1)}
        )
        name_label.bind(size=lambda *x: setattr(name_label, 'text_size', (name_label.width, None)))
        self.add_widget(name_label)

        # 2. Track Type Icon
        track_type_icon = "help-circle"
        track_type_tooltip = "Unknown"
        track_type_color = [0.5, 0.5, 0.5, 1] # Grey for unknown
        if isinstance(track, MidiTrack):
            track_type_icon = "midi"
            track_type_tooltip = "MIDI"
            track_type_color = [0.3, 0.5, 0.9, 1] # Blue for MIDI
        elif isinstance(track, AudioTrack):
            track_type_icon = "waveform"
            track_type_tooltip = "Audio"
            track_type_color = [0.9, 0.5, 0.2, 1] # Orange for Audio
        elif isinstance(track, AutomationTrack):
            track_type_icon = "chart-line"
            track_type_tooltip = "Automation"
            track_type_color = [0.2, 0.8, 0.8, 1] # Cyan for Automation

        type_icon_button = TooltipMDIconButton(
            icon=track_type_icon,
            tooltip_text=track_type_tooltip,
            size_hint_x=None,
            width=dp(36),
            pos_hint={'center_y': 0.5},
            theme_icon_color="Custom",
            icon_color=track_type_color,
            theme_bg_color="Custom",
            md_bg_color=[0.1, 0.1, 0.1, 1]
        )
        self.add_widget(type_icon_button)

        # 3. Mute & Solo Buttons
        mute_button = TooltipMDIconButton(
            icon='volume-off' if track.is_muted else 'volume-high',
            tooltip_text='Mute',
            on_press=self.on_mute_toggle,
            size_hint_x=None,
            width=dp(36),
            pos_hint={'center_y': 0.5},
            theme_icon_color="Custom",
            icon_color=[1, 0.6, 0, 1], # Orange for Mute
            theme_bg_color="Custom",
            md_bg_color=[0.1, 0.1, 0.1, 1]
        )
        self.add_widget(mute_button)

        self.solo_button = TooltipMDIconButton(
            icon='alpha-s-box' if track.is_solo else 'alpha-s-box-outline',
            tooltip_text='Solo',
            on_press=self.on_solo_toggle,
            size_hint_x=None,
            width=dp(36),
            pos_hint={'center_y': 0.5},
            theme_icon_color="Custom",
            icon_color=[1, 1, 0, 1], # Yellow for Solo
            theme_bg_color="Custom",
            md_bg_color=[0.1, 0.1, 0.1, 1]
        )
        self.add_widget(self.solo_button)

        # 4. MIDI Controls (or a spacer of the same size)
        midi_controls_layout = BoxLayout(size_hint_x=None, width=dp(400), spacing=dp(5), pos_hint={'center_y': 0.5})
        if isinstance(track, MidiTrack):
            midi_controls_layout.add_widget(Label(text='Ch:', size_hint_x=None, width=dp(25)))
            channel_spinner = ValueSpinner(
                min_val=1,
                max_val=16,
                initial_value=track.channel + 1,
                callback=self.on_channel_change
            )
            midi_controls_layout.add_widget(channel_spinner)

            midi_controls_layout.add_widget(Label(text='Prog:', size_hint_x=None, width=dp(35)))
            program_spinner = ValueSpinner(
                min_val=1,
                max_val=128,
                initial_value=track.instrument + 1,
                callback=self.on_program_change
            )
            midi_controls_layout.add_widget(program_spinner)
        else:
            # Add a spacer to keep alignment consistent for non-MIDI tracks
            midi_controls_layout.add_widget(Widget(size_hint_x=None, width=dp(400)))
        self.add_widget(midi_controls_layout)

        # 5. Volume Slider with Label
        volume_layout = BoxLayout(size_hint_x=None, width=dp(170), spacing=dp(5), pos_hint={'center_y': 0.5})
        volume_icon = TooltipMDIconButton(
            icon='volume-high',
            tooltip_text='Volume',
            size_hint_x=None,
            width=dp(24),
            pos_hint={'center_y': 0.5},
            theme_icon_color="Custom",
            icon_color=[0.6, 0.6, 1, 1], # Light blue for volume
            theme_bg_color="Custom",
            md_bg_color=[0.1, 0.1, 0.1, 1]
        )
        volume_layout.add_widget(volume_icon)
        self.volume_label = Label(text=f"{int(track.volume * 100)}", size_hint_x=None, width=dp(35))
        self.volume_slider = MDSlider(min=0, max=1, value=track.volume)
        self.volume_slider.bind(value=self.on_volume_change)
        volume_layout.add_widget(self.volume_label)
        volume_layout.add_widget(self.volume_slider)
        self.add_widget(volume_layout)

        # 6. Pan Slider with Label
        pan_layout = BoxLayout(size_hint_x=None, width=dp(170), spacing=dp(5), pos_hint={'center_y': 0.5})
        pan_icon = TooltipMDIconButton(
            icon='swap-horizontal',
            tooltip_text='Pan',
            size_hint_x=None,
            width=dp(24),
            pos_hint={'center_y': 0.5},
            theme_icon_color="Custom",
            icon_color=[0.6, 0.6, 1, 1], # Light blue for pan
            theme_bg_color="Custom",
            md_bg_color=[0.1, 0.1, 0.1, 1]
        )
        pan_layout.add_widget(pan_icon)
        self.pan_label = Label(text=f"{track.pan:.1f}", size_hint_x=None, width=dp(35))
        self.pan_slider = MDSlider(min=-1, max=1, value=track.pan)
        self.pan_slider.bind(value=self.on_pan_change)
        pan_layout.add_widget(self.pan_label)
        pan_layout.add_widget(self.pan_slider)
        self.add_widget(pan_layout)

    def on_volume_change(self, instance, value):
        self.volume_label.text = f"{int(value * 100)}"
        self.sequencer_layout.process_slider_command(f'volume {self.track_index} {value}')

    def on_pan_change(self, instance, value):
        self.pan_label.text = f"{value:.1f}"
        self.sequencer_layout.process_slider_command(f'pan {self.track_index} {value}')

    def on_mute_toggle(self, instance):
        # Optimistically update the icon to provide immediate feedback
        if instance.icon == 'volume-high':
            instance.icon = 'volume-off'
        else:
            instance.icon = 'volume-high'

        # Now, send the command to the backend to update the actual state.
        # The full UI refresh is disabled for this command to prevent the race condition.
        self.sequencer_layout.process_command_ui(f'mute {self.track_index}')

    def on_solo_toggle(self, instance):
        # The UI is now refreshed from the backend state, so we just send the command.
        self.sequencer_layout.process_command_ui(f'solo {self.track_index}')

    def on_channel_change(self, instance):
        self.sequencer_layout.process_slider_command(f'setch {self.track_index} {instance.text}')

    def on_program_change(self, instance):
        self.sequencer_layout.process_slider_command(f'setprog {self.track_index} {instance.text}')

    def on_set_as_metronome(self, instance):
        self.sequencer_layout.process_command_ui(f'setmetrotrack {self.track_index}')