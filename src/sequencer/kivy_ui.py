# Kivy UI components for the Sequencer application
import kivy
kivy.require('2.3.1')

from kivymd.app import MDApp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.textinput import TextInput
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivymd.uix.card import MDCard
from kivy.uix.filechooser import FileChooserListView
from kivymd.uix.slider import MDSlider
from kivy.uix.scrollview import ScrollView
from kivymd.uix.label import MDLabel
from kivy.properties import StringProperty
from kivy.uix.widget import Widget
from kivymd.uix.button import MDIconButton, MDButton, MDButtonText
from kivymd.uix.menu import MDDropdownMenu
from kivy.metrics import dp
from kivy.core.window import Window
from kivy.clock import Clock
from kivy.logger import Logger
from kivy.graphics import Color, Rectangle, Line

# === External UI Components (modularized) ===
from sequencer.ui_components.ConfirmationPopup import ConfirmationPopup
from sequencer.ui_components.CustomTextInput import CustomTextInput
from sequencer.ui_components.FileChooserPopup import FileChooserPopup
from sequencer.ui_components.LoopPopup import LoopPopup
from sequencer.ui_components.SaveDiscardCancelPopup import SaveDiscardCancelPopup
from sequencer.ui_components.SaveProjectAsPopup import SaveProjectAsPopup
from sequencer.ui_components.TooltipMDIconButton import TooltipMDIconButton
from sequencer.ui_components.YesNoPopup import YesNoPopup
from sequencer.ui_components.TrackWidget import TrackWidget
# ============================================

from sequencer.sequencer import Sequencer
from sequencer.models import MidiTrack, AudioTrack, AutomationTrack
from typing import Optional
import sys, os, time

class SequencerLayout(BoxLayout):
    
    def __init__(self, **kwargs):
        super(SequencerLayout, self).__init__(**kwargs)
        self.orientation = 'vertical'
        self.sequencer = Sequencer(gui_mode=True)
        self.sequencer.bind(playback_state=self.on_playback_state_change)
        self._transport_update_event = None # Pour stocker l'événement Clock        
        self.current_command = ""
        self.end_pos_manual_override = False
        self.is_looping = False
        self.blink_animation = None  # Référence à l'animation de clignotement
        self._current_measure = None # Initialisation pour la détection du beat 1

        # Tête de lecture "lissée" (celle que l'utilisateur voit)
        self.display_beat = 0.0
                
        self.track_widgets = [] # Initialisation de la liste des widgets de piste                

        menu_bar = BoxLayout(size_hint_y=None, height=40, padding=5)

        # Bouton File avec ligne en dessous
        file_button = MDButton(
            MDButtonText(text="File"),
            style="text",
            pos_hint={'center_y': 0.5},
            md_bg_color=[0, 0, 0, 0],  # Fond transparent
        )

        with file_button.canvas.before:
            Color(0.5, 0.5, 0.5, 1)  # Gris moyen
            self.file_line = Line(points=[0, -1, file_button.width, -1], width=1)  # Ligne fine

        def update_lines(instance, value):
            if hasattr(self, 'file_line'):
                self.file_line.points = [0, -1, instance.width, -1]  # Ajuste à la largeur de menu_bar
            if hasattr(self, 'edit_line'):
                self.edit_line.points = [0, -1, instance.width, -1]  # Ajuste à la largeur de menu_bar

        menu_items = [
            {"leading_icon": "file-plus", "text": "New Project", "on_release": lambda: self.menu_action(self.new_project_popup)},
            {"leading_icon": "folder-open", "text": "Load Project", "on_release": lambda: self.menu_action(self.load_project_popup)},
            {"leading_icon": "content-save", "text": "Save Project", "on_release": lambda: self.menu_action(self.save_project)},
            {"leading_icon": "content-save-edit", "text": "Save Project As...", "on_release": lambda: self.menu_action(self.save_project_as_popup)},
            {"leading_icon": "exit-to-app", "text": "Quit", "on_release": lambda: self.menu_action(lambda: self.process_command_ui('quit'))},
        ]

        self.file_menu = MDDropdownMenu(
            caller=file_button,
            items=menu_items,
        )
        file_button.bind(on_release=lambda x: self.file_menu.open())
        menu_bar.add_widget(file_button)

        # Bouton Edit avec ligne en dessous
        edit_button = MDButton(
            MDButtonText(text="Edit"),
            style="text",
            pos_hint={'center_y': 0.5},
            md_bg_color=[0, 0, 0, 0],  # Fond transparent
        )

        with edit_button.canvas.before:
            Color(0.5, 0.5, 0.5, 1)  # Gris moyen
            self.edit_line = Line(points=[0, -1, edit_button.width, -1], width=1)  # Ligne fine

        edit_items = [
            {"leading_icon": "undo", "text": "Undo", "on_release": lambda x: None},
            {"leading_icon": "redo", "text": "Redo", "on_release": lambda x: None},
            {"leading_icon": "content-cut", "text": "Cut", "on_release": lambda x: None},
            {"leading_icon": "content-copy", "text": "Copy", "on_release": lambda x: None},
            {"leading_icon": "content-paste", "text": "Paste", "on_release": lambda x: None},
        ]
        self.edit_menu = MDDropdownMenu(
            caller=edit_button,
            items=edit_items,
        )
        edit_button.bind(on_release=lambda x: self.edit_menu.open())
        menu_bar.add_widget(edit_button)

        settings_button = MDButton(
            MDButtonText(text="Settings"),
            style="text", 
            pos_hint={'center_y': 0.5},
            md_bg_color=[0, 0, 0, 0],
        )

        with settings_button.canvas.before:
            Color(0.5, 0.5, 0.5, 1)
            self.settings_line = Line(points=[0, -1, settings_button.width, -1], width=1)

        settings_items = [
            {"leading_icon": "midi", "text": "MIDI Input Settings", "on_release": lambda: self.menu_action(self.show_midi_settings)},
            {"leading_icon": "audio-input-stereo-minijack", "text": "Audio Settings", "on_release": lambda: self.menu_action(self.show_audio_settings)},
        ]

        self.settings_menu = MDDropdownMenu(
            caller=settings_button,
            items=settings_items,
        )
        settings_button.bind(on_release=lambda x: self.settings_menu.open())
        menu_bar.add_widget(settings_button)

        help_button = MDButton(
            MDButtonText(text="Help"),
            style="text",
            pos_hint={'center_y': 0.5},
            md_bg_color=[0, 0, 0, 0],
        )

        with help_button.canvas.before:
            Color(0.5, 0.5, 0.5, 1)
            self.help_line = Line(points=[0, -1, help_button.width, -1], width=1)

        help_items = [
            {"leading_icon": "information", "text": "About", "on_release": lambda: self.menu_action(self.show_about_popup)},
            {"leading_icon": "help-circle", "text": "Commands Help", "on_release": lambda: self.menu_action(self.show_help_popup)},
        ]

        self.help_menu = MDDropdownMenu(
            caller=help_button,
            items=help_items,
        )
        help_button.bind(on_release=lambda x: self.help_menu.open())
        menu_bar.add_widget(help_button)


        # Lier la mise à jour des lignes
        menu_bar.bind(size=self.update_menu_lines)

###
        # Lier la mise à jour des lignes à la taille du menu_bar
        menu_bar.bind(size=update_lines)

        self.add_widget(menu_bar)

        # Ajouter un espacement entre le menu et la ligne de statut
        self.add_widget(Widget(size_hint_y=None, height=dp(15)))
        
        # Affichage de la premiere ligne (Titre, ... transport)
        # Définir une hauteur commune
        common_height = dp(32)

        # Ligne combinée: status + transport
        combined_layout = BoxLayout(
            size_hint_y=None, 
            height=common_height + dp(4),  # Juste un tout petit peu plus
            spacing=8, 
            padding=[10, 2, 10, 2]  # Padding vertical réduit
        )
        
        # Info du morceau
        self.song_name_label = Label(
            text="Song: New Song", 
            size_hint_x=None, 
            width=150,
            size_hint_y=None,
            height=common_height,
            halign='left', 
            valign='middle',
            text_size=(150, None)
        )
        combined_layout.add_widget(self.song_name_label)

        self.tempo_label = Label(
            text="Tempo:", 
            size_hint_x=None, 
            width=20,
            size_hint_y=None,
            height=common_height,
            halign='left', 
            valign='middle',
            text_size=(80, None)
        )
        combined_layout.add_widget(self.tempo_label)        

        self.tempo_input = CustomTextInput(
            text='120', 
            multiline=False, 
            size_hint_x=None, 
            width=40,
            size_hint_y=None,
            height=common_height,
            field_type='tempo',
            callback=self.handle_textinput_arrows
        )
        combined_layout.add_widget(self.tempo_input)
                
        self.timesig_label = Label(
            text="TS: 4/4", 
            size_hint_x=None, 
            width=60,
            size_hint_y=None,
            height=common_height,
            halign='left', 
            valign='middle',
            text_size=(60, None)
        )
        combined_layout.add_widget(self.timesig_label)

        self.metronome_button = TooltipMDIconButton(
            icon='metronome',
            tooltip_text="Toggle Metronome (M)",
            on_press=self.toggle_metronome,
            size_hint_x=None,
            width=dp(40),
            size_hint_y=None,
            height=common_height,
            pos_hint={'center_y': 0.5},
            theme_icon_color="Custom",
            icon_color=[0.5, 0.5, 0.5, 1],
            theme_bg_color="Custom",
            md_bg_color=[0.1, 0.1, 0.1, 1]
        )
        combined_layout.add_widget(self.metronome_button)

        # Séparateur visuel
        combined_layout.add_widget(Widget(
            size_hint_x=None, 
            width=dp(10),
            size_hint_y=None,
            height=common_height
        ))
        
        self.playhead_label = Label(
            text="Pos: 1:1", 
            size_hint_x=None, 
            width=80, 
            size_hint_y=None,
            height=common_height,
            halign='left', 
            valign='middle',
            text_size=(80, None)
        )
        combined_layout.add_widget(self.playhead_label)

        # Start/End avec hauteur synchronisée
        start_label = Label(
            text='Start:', 
            size_hint_x=None, 
            width=40,
            size_hint_y=None,
            height=common_height,
            halign='right', 
            valign='middle',
            text_size=(40, None)
        )
        combined_layout.add_widget(start_label)

        self.start_pos_input = CustomTextInput(
            text='1:1', 
            multiline=False, 
            size_hint_x=None, 
            width=55,
            size_hint_y=None,
            height=common_height,
            field_type='position',
            callback=self.handle_textinput_arrows
        )
        combined_layout.add_widget(self.start_pos_input)

        end_label = Label(
            text='End:', 
            size_hint_x=None, 
            width=35,
            size_hint_y=None,
            height=common_height,
            halign='right', 
            valign='middle',
            text_size=(35, None)
        )
        combined_layout.add_widget(end_label)

        self.end_pos_input = CustomTextInput(
            text='', 
            multiline=False, 
            size_hint_x=None, 
            width=55,
            size_hint_y=None,
            height=common_height,
            field_type='position', 
            callback=self.handle_textinput_arrows
        )
        combined_layout.add_widget(self.end_pos_input)

        # Variable pour suivre le champ focus
        #self.focused_input = None
        self.start_pos_input.bind(on_text_validate=self.on_start_position_validate, text=self.on_start_pos_text_change)
        self.end_pos_input.bind(on_text_validate=self.on_end_position_validate, text=self.on_end_pos_text_change)
   
        
        # Séparateur
        combined_layout.add_widget(Widget(size_hint_x=None, width=dp(15)))
        
        # Boutons de transport
        self.play_button = TooltipMDIconButton(
            icon='play',
            tooltip_text='Play',
            size_hint_x=None,
            pos_hint={'center_y': 0.5},            
            width=dp(40),
            size_hint_y=None,
            height=common_height,
            theme_icon_color="Custom",
            icon_color=[0, 0.7, 0.3, 1],
            theme_bg_color="Custom",
            md_bg_color=[0.1, 0.1, 0.1, 1]
        )

        self.loop_button = TooltipMDIconButton(
            icon='repeat',
            tooltip_text='Loop',
            size_hint_x=None,
            pos_hint={'center_y': 0.5},
            width=dp(40),
            size_hint_y=None,
            height=common_height,
            theme_icon_color="Custom",
            icon_color=[0.2, 0.6, 0.8, 1],
            theme_bg_color="Custom",
            md_bg_color=[0.1, 0.1, 0.1, 1]
        )

        self.pause_button = TooltipMDIconButton(
            icon='pause',
            tooltip_text='Pause',
            size_hint_x=None,
            pos_hint={'center_y': 0.5},
            width=dp(40),
            size_hint_y=None,
            height=common_height,
            theme_icon_color="Custom",
            icon_color=[0.9, 0.9, 0.2, 1],
            theme_bg_color="Custom",
            md_bg_color=[0.1, 0.1, 0.1, 1]
        )

        self.stop_button = TooltipMDIconButton(
            icon='stop',
            tooltip_text='Stop',
            size_hint_x=None,
            pos_hint={'center_y': 0.5},
            width=dp(40),
            size_hint_y=None,
            height=common_height,
            theme_icon_color="Custom",
            icon_color=[0.8, 0.2, 0.2, 1],
            theme_bg_color="Custom",
            md_bg_color=[0.1, 0.1, 0.1, 1]
        )

        self.record_button = TooltipMDIconButton(
            icon='record',
            tooltip_text='Record',
            size_hint_x=None,
            pos_hint={'center_y': 0.5},
            width=dp(40),
            size_hint_y=None,
            height=common_height,
            theme_icon_color="Custom",
            icon_color=[1, 0, 0, 1],
            theme_bg_color="Custom",
            md_bg_color=[0.1, 0.1, 0.1, 1]
        )

        combined_layout.add_widget(self.play_button)
        combined_layout.add_widget(self.loop_button)
        combined_layout.add_widget(self.pause_button)
        combined_layout.add_widget(self.stop_button)
        combined_layout.add_widget(self.record_button)

        self.play_button.bind(on_press=self.play_pressed)
        self.loop_button.bind(on_press=self.loop_pressed)
        self.pause_button.bind(on_press=self.pause_pressed)
        self.stop_button.bind(on_press=self.stop_pressed)
        self.record_button.bind(on_press=self.record_pressed)
        
        # Espace flexible à droite
        combined_layout.add_widget(Widget(size_hint_x=1))
        
        self.add_widget(combined_layout)

        # Ajouter un espacement entre la ligne de statut et les pistes
        self.add_widget(Widget(size_hint_y=None, height=dp(15)))

        # Layout principal pour la barre d'outils et la liste des pistes
        main_content_layout = BoxLayout(orientation='horizontal')
        
        # Barre d'outils à gauche
        toolbar = BoxLayout(
            orientation='vertical',
            size_hint_x=None,
            width=dp(50),
            spacing=dp(5),
            padding=(dp(5), 0)
        )

        # Bouton pour ajouter une piste MIDI
        add_midi_track_button = TooltipMDIconButton(
            icon="note-plus",
            tooltip_text="Ajouter une piste MIDI",
            on_release=lambda x: self.add_midi_track_popup()
        )
        toolbar.add_widget(add_midi_track_button)

        main_content_layout.add_widget(toolbar)

        # Liste des pistes - DOIT être créé AVANT update_status_display()
        self.track_list_layout = BoxLayout(orientation='vertical', size_hint_y=None)
        self.track_list_layout.bind(minimum_height=self.track_list_layout.setter('height'))

        main_content_layout.add_widget(self.track_list_layout)
        self.add_widget(main_content_layout)

        bottom_layout = BoxLayout(orientation='vertical', size_hint_y=0.3)
        self.output_label = Label(size_hint_y=0.1, text="Welcome!")
        bottom_layout.add_widget(self.output_label)

        self.input_text = TextInput(size_hint_y=0.1, multiline=False)
        self.input_text.bind(on_text_validate=self.on_enter)
        bottom_layout.add_widget(self.input_text)

        self.send_button = TooltipMDIconButton(icon='send', tooltip_text='Send', size_hint_y=0.1)
        self.send_button.bind(on_press=self.on_enter)
        bottom_layout.add_widget(self.send_button)
        self.add_widget(bottom_layout)

        # update_status_display() appelle update_track_list() qui utilise self.track_list_layout
        # donc il DOIT être appelé APRÈS la création de track_list_layout
        self.update_status_display()

        # Re-introducing a clock for smooth UI updates, but at a more reasonable rate
        Clock.schedule_interval(self.update_playhead, 1/30.0)

        # Ajouter une variable pour stocker la position de fin pendant la pause
        self.saved_end_pos = ""

        # Show MIDI input selection on startup if not already set
        if not self.sequencer.default_record_port:
            Clock.schedule_once(lambda dt: self.show_midi_settings(), 0.5)

    def on_playback_state_change(self, instance, value):
        """Callback for sequencer's playback_state changes."""
        Logger.info(f"UI: Playback state changed to '{value}'")
        state = value
        is_playing = (state == "playing")
        is_paused = (state == "paused")
        is_recording = (state == "recording")

        # --- Update Play/Blink Button ---
        if is_playing or is_recording:
            if not self.blink_animation:
                self.start_play_blink()
            self.play_button.icon = 'play-circle-outline'
        else:
            self.stop_play_blink()
            self.play_button.icon = 'play'

        # --- Update Pause Button ---
        if is_paused:
            self.pause_button.icon = 'pause-circle-outline'
            self.pause_button.md_bg_color = [0.9, 0.7, 0, 1]
        else:
            self.pause_button.icon = 'pause'
            self.pause_button.md_bg_color = [0.1, 0.1, 0.1, 1]

        # --- Update Record Button ---
        if is_recording:
            self.record_button.icon = 'record-circle-outline'
            self.record_button.md_bg_color = [0.8, 0, 0, 1]
        else:
            self.record_button.icon = 'record'
            self.record_button.md_bg_color = [0.1, 0.1, 0.1, 1]

        # --- Final UI sync on stop ---
        if state == "stopped":
            self.stop_beat_pulse_animation()
            # Force the UI to snap to the final JACK position
            Clock.schedule_once(lambda dt: self.snap_ui_to_jack(), 0.05)

    def update_menu_lines(self, instance, value):
        """Met à jour toutes les lignes des menus"""
        if hasattr(self, 'file_line'):
            self.file_line.points = [0, -1, instance.width, -1]
        if hasattr(self, 'edit_line'):
            self.edit_line.points = [0, -1, instance.width, -1]
        if hasattr(self, 'settings_line'):
            self.settings_line.points = [0, -1, instance.width, -1]
        if hasattr(self, 'help_line'):
            self.help_line.points = [0, -1, instance.width, -1]


    def show_audio_settings(self):
        """Affiche les paramètres audio"""
        # Pour l'instant, on peut juste afficher un message
        self.show_info_popup("Audio Settings", "Audio settings configuration will be available in a future version.")

    def show_midi_settings(self):
        """Affiche les paramètres MIDI"""
        # Le menu est déjà fermé par menu_action()
        
        def apply_settings(port_name):
            if port_name:
                try:
                    import mido
                    input_ports = mido.get_input_names()
                    if port_name not in input_ports:
                        self.show_error_popup("Invalid Port", 
                                            f"Port '{port_name}' is not available.")
                        return
                    
                    self.process_command_ui(f'setrecordport "{port_name}"')
                    self.show_info_popup("Success", f"MIDI input port set to:\n{port_name}")
                    
                except Exception as e:
                    self.show_error_popup("Error", f"Failed to set MIDI port:\n{str(e)}")
        
        try:
            import mido
            input_ports = mido.get_input_names()
            
            if not input_ports:
                self.show_error_popup("No MIDI Input Ports", 
                                    "No MIDI input ports found.")
                return
                
            current_port = getattr(self.sequencer, 'default_record_port', None)
            
            self.show_port_selection_popup(
                title="Select MIDI Input Port",
                ports=input_ports,
                callback=apply_settings,
                current_port=current_port
            )
            
        except Exception as e:
            self.show_error_popup("MIDI Error", f"Cannot access MIDI system:\n\n{str(e)}")

    def show_port_selection_popup(self, title, ports, callback, current_port=None):
        """Affiche un popup de sélection de port avec ListView scrollable"""
        content = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(10))
        
        # Label
        content.add_widget(Label(
            text=f"Select {title.split()[-1]} Port:",
            size_hint_y=None,
            height=dp(30)
        ))
        
        # Container scrollable pour les ports
        from kivy.uix.scrollview import ScrollView
        from kivy.uix.gridlayout import GridLayout
        from kivy.uix.button import Button
        
        scroll_view = ScrollView(size_hint=(1, 1))
        grid_layout = GridLayout(
            cols=1,
            spacing=dp(5),
            size_hint_y=None
        )
        grid_layout.bind(minimum_height=grid_layout.setter('height'))
        
        selected_port = [current_port or ports[0]]  # Liste pour stocker la sélection
        
        def on_port_select(instance):
            selected_port[0] = instance.text
            # Mettre en surbrillance la sélection
            for child in grid_layout.children:
                if hasattr(child, 'background_color'):
                    if child.text == selected_port[0]:
                        child.background_color = [0.2, 0.6, 0.8, 1]  # Bleu sélectionné
                    else:
                        child.background_color = [0.1, 0.1, 0.1, 1]  # Gris par défaut
        
        # Créer un bouton pour chaque port
        for port in ports:
            btn = Button(
                text=port,
                size_hint_y=None,
                height=dp(40),
                background_color=[0.1, 0.1, 0.1, 1],
                color=[1, 1, 1, 1]
            )
            if port == selected_port[0]:
                btn.background_color = [0.2, 0.6, 0.8, 1]
            btn.bind(on_press=on_port_select)
            grid_layout.add_widget(btn)
        
        scroll_view.add_widget(grid_layout)
        content.add_widget(scroll_view)
        
        # Info sur le nombre de ports
        info_label = Label(
            text=f"Found {len(ports)} port(s) - Select one and click OK",
            size_hint_y=None,
            height=dp(30),
            font_size=dp(12)
        )
        content.add_widget(info_label)
        
        # Boutons
        buttons_layout = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(10))
        
        ok_button = TooltipMDIconButton(icon='check', tooltip_text='OK')
        cancel_button = TooltipMDIconButton(icon='cancel', tooltip_text='Cancel')
        
        def on_ok(instance):
            callback(selected_port[0])
            popup.dismiss()
        
        def on_cancel(instance):
            popup.dismiss()
        
        ok_button.bind(on_press=on_ok)
        cancel_button.bind(on_press=on_cancel)
        
        buttons_layout.add_widget(ok_button)
        buttons_layout.add_widget(cancel_button)
        content.add_widget(buttons_layout)
        
        # Taille adaptative avec maximum
        max_height = min(dp(600), dp(200) + (len(ports) * dp(45)))
        
        popup = Popup(
            title=title,
            content=content,
            size_hint=(0.8, None),  # Encore plus large
            height=max_height,
            auto_dismiss=False
        )
        popup.open()

    def show_error_popup(self, title, message):
        """Affiche un popup d'erreur"""
        content = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(10))
        content.add_widget(Label(text=message))
        
        ok_button = TooltipMDIconButton(icon='check', tooltip_text='OK')
        buttons_layout = BoxLayout(size_hint_y=None, height=dp(50))
        buttons_layout.add_widget(ok_button)
        content.add_widget(buttons_layout)
        
        popup = Popup(
            title=title,
            content=content,
            size_hint=(0.5, 0.3)
        )
        ok_button.bind(on_press=popup.dismiss)
        popup.open()

    def show_info_popup(self, title, message):
        """Affiche un popup d'information"""
        content = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(10))
        content.add_widget(Label(text=message))
        
        ok_button = TooltipMDIconButton(icon='check', tooltip_text='OK')
        buttons_layout = BoxLayout(size_hint_y=None, height=dp(50))
        buttons_layout.add_widget(ok_button)
        content.add_widget(buttons_layout)
        
        popup = Popup(
            title=title,
            content=content,
            size_hint=(0.5, 0.3)
        )
        ok_button.bind(on_press=popup.dismiss)
        popup.open()

    def show_about_popup(self):
        """Affiche la popup 'About'."""
        content = BoxLayout(orientation='vertical', padding=dp(20), spacing=dp(15))

        # Placeholder pour le logo
        logo_placeholder = MDCard(
            size_hint=(None, None),
            size=(dp(100), dp(100)),
            pos_hint={'center_x': 0.5},
            md_bg_color=(0.2, 0.2, 0.2, 1) # Gris foncé
        )
        logo_placeholder.add_widget(Label(text="[LOGO]", font_size='20sp'))

        # Informations de version
        version_label = Label(
            text="Sequencer\nVersion 0.1.0\n\nDeveloped by Jules",
            halign='center',
            size_hint_y=None,
            height=dp(80)
        )

        # Bouton OK
        ok_button = MDButton(
            MDButtonText(text="OK"),
            pos_hint={'center_x': 0.5}
        )

        content.add_widget(logo_placeholder)
        content.add_widget(version_label)
        content.add_widget(ok_button)

        popup = Popup(
            title="About Sequencer",
            content=content,
            size_hint=(None, None),
            size=(dp(350), dp(350)),
            auto_dismiss=True
        )
        ok_button.bind(on_press=popup.dismiss)
        popup.open()

    def show_help_popup(self):
        """Affiche la popup d'aide des commandes."""
        from sequencer.help_text import COMMANDS_HELP, MIDI_MAPPING_HELP

        # Conteneur principal
        content = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(10))

        # ScrollView pour le contenu
        scroll_view = ScrollView(size_hint=(1, 1))

        # Layout principal dans le ScrollView
        scroll_content = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(15))
        scroll_content.bind(minimum_height=scroll_content.setter('height'))

        # Grille pour les commandes générales
        commands_grid = GridLayout(
            cols=2,
            size_hint_y=None,
            spacing=dp(10)
        )
        commands_grid.bind(minimum_height=commands_grid.setter('height'))

        # Grille pour les commandes MIDI
        midi_grid = GridLayout(
            cols=2,
            size_hint_y=None,
            spacing=dp(10)
        )
        midi_grid.bind(minimum_height=midi_grid.setter('height'))

        # Populate grids
        for command, description in COMMANDS_HELP:
            commands_grid.add_widget(MDLabel(text=f"[b]{command}[/b]", markup=True, size_hint_y=None, height=dp(30)))
            commands_grid.add_widget(MDLabel(text=description, size_hint_y=None, height=dp(30)))

        for command, description in MIDI_MAPPING_HELP:
            midi_grid.add_widget(MDLabel(text=f"[b]{command}[/b]", markup=True, size_hint_y=None, height=dp(30)))
            midi_grid.add_widget(MDLabel(text=description, size_hint_y=None, height=dp(30)))

        scroll_content.add_widget(MDLabel(text="[b]Sequencer CLI Commands[/b]", markup=True, size_hint_y=None, height=dp(30)))
        scroll_content.add_widget(commands_grid)
        scroll_content.add_widget(Widget(size_hint_y=None, height=dp(20))) # Spacer
        scroll_content.add_widget(MDLabel(text="[b]MIDI Mapping[/b]", markup=True, size_hint_y=None, height=dp(30)))
        scroll_content.add_widget(midi_grid)

        scroll_view.add_widget(scroll_content)

        # Bouton OK
        ok_button = MDButton(
            MDButtonText(text="OK"),
            size_hint=(1, None),
            height=dp(40)
        )

        content.add_widget(scroll_view)
        content.add_widget(ok_button)

        popup = Popup(
            title="Commands Help",
            content=content,
            size_hint=(0.8, 0.8), # 80% de la fenêtre
            auto_dismiss=True
        )
        ok_button.bind(on_press=popup.dismiss)
        popup.open()

    def close_all_menus(self):
        """Ferme tous les menus ouverts"""
        menus_to_close = ['file_menu', 'edit_menu', 'settings_menu', 'help_menu']
        for menu_name in menus_to_close:
            if hasattr(self, menu_name) and getattr(self, menu_name):
                try:
                    getattr(self, menu_name).dismiss()
                except:
                    pass

    def menu_action(self, action_callback):
        """Exécute une action de menu et ferme le menu"""
        self.close_all_menus()
        action_callback()
        
    def on_end_pos_manual_set(self, instance):
        if instance.text:
            self.end_pos_manual_override = True

    def on_tempo_change(self, tempo):
        self.process_command_ui(f'tempo {tempo}')

    def on_position_change(self, position):
        # Pour start_pos_input et end_pos_input
        # La gestion spécifique dépend de quel champ a changé
        pass

    def load_project_popup(self):
        def file_chooser_callback(filepath):
            import os
            if filepath:
                basename = os.path.basename(filepath).removesuffix('.proj.json')
                self.end_pos_manual_override = False
                self.process_command_ui(f'loadproject "{basename}"')
        popup = FileChooserPopup(
            callback=file_chooser_callback,
            title="Load Project",
            filters=['*.proj.json']  # Seulement les fichiers projet
        )
        popup.open()

    def new_project_popup(self):
        def callback(name):
            if name:
                self.end_pos_manual_override = False
                self.process_command_ui(f'newproject "{name}"')
        popup = ConfirmationPopup(prompt_text="Enter new project name:", callback=callback)
        popup.open()

    def save_project(self):
        if self.sequencer.last_project_basename:
            self.process_command_ui(f'saveproject "{self.sequencer.last_project_basename}"')
        else:
            self.save_project_as_popup()

    def save_project_as_popup(self):
        def callback(filepath):
            if filepath:
                basename = os.path.basename(filepath).removesuffix('.proj.json')
                self.process_command_ui(f'saveproject "{basename}"')
        popup = SaveProjectAsPopup(sequencer=self.sequencer, callback=callback)
        popup.open()

    def add_midi_track_popup(self):
        """Affiche un popup pour ajouter une nouvelle piste MIDI."""

        content = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(10))

        # Champ pour le nom de la piste
        track_name_input = TextInput(hint_text="Nom de la piste")
        content.add_widget(track_name_input)

        # Champ pour le programme MIDI (instrument)
        program_input = TextInput(hint_text="Programme MIDI (optionnel, 1-128)")
        content.add_widget(program_input)

        # Boutons
        buttons_layout = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(10))
        ok_button = MDButton(MDButtonText(text="OK"))
        cancel_button = MDButton(MDButtonText(text="Annuler"))
        buttons_layout.add_widget(ok_button)
        buttons_layout.add_widget(cancel_button)
        content.add_widget(buttons_layout)

        popup = Popup(
            title="Ajouter une piste MIDI",
            content=content,
            size_hint=(0.6, None),
            height=dp(200),
            auto_dismiss=False
        )

        def on_ok(instance):
            track_name = track_name_input.text.strip()
            if not track_name:
                self.show_error_popup("Erreur", "Le nom de la piste ne peut pas être vide.")
                return

            program_str = program_input.text.strip()
            command = f'add "{track_name}"'

            if program_str:
                try:
                    program_num = int(program_str)
                    if not 1 <= program_num <= 128:
                        self.show_error_popup("Erreur", "Le programme MIDI doit être entre 1 et 128.")
                        return
                    command += f' {program_num}'
                except ValueError:
                    self.show_error_popup("Erreur", "Le programme MIDI doit être un nombre.")
                    return

            self.process_command_ui(command)
            popup.dismiss()

        ok_button.bind(on_press=on_ok)
        cancel_button.bind(on_press=popup.dismiss)

        popup.open()

    def start_play_blink(self):
        """Démarre l'animation de clignotement du bouton play"""
        if self.blink_animation:
            self.blink_animation.cancel()
        
        # Animation de clignotement
        self.blink_animation = Clock.schedule_interval(self._toggle_play_button_color, 0.5)  # Clignote toutes les 0.5 secondes

    def _toggle_play_button_color(self, dt):
        """Alterne entre deux couleurs pour l'effet de clignotement (icône seulement)"""
        if self.play_button.icon_color == [0, 0.7, 0.3, 1]:
            # Couleur clignotante (plus claire) - seulement l'icône
            self.play_button.icon_color = [0.3, 0.9, 0.5, 1]
            # Ne pas changer le fond, le laisser à sa couleur normale
            # self.play_button.md_bg_color = [0.1, 0.1, 0.1, 1]  # Ligne supprimée
        else:
            # Couleur normale - seulement l'icône
            self.play_button.icon_color = [0, 0.7, 0.3, 1]
            # Ne pas changer le fond, le laisser à sa couleur normale
            # self.play_button.md_bg_color = [0.1, 0.1, 0.1, 1]  # Ligne supprimée

    def stop_play_blink(self):
        """Arrête l'animation de clignotement et remet la couleur normale"""
        if self.blink_animation:
            self.blink_animation.cancel()
            self.blink_animation = None
        
        # Remettre la couleur normale de l'icône seulement
        self.play_button.icon_color = [0, 0.7, 0.3, 1]

    def play_pressed(self, instance):
        # If already playing, do nothing. If paused, resume via the pause button.
        if self.sequencer.playback_state == "stopped":
            # Pre-sync the UI to the start beat for a smoother start
            start_pos = self.start_pos_input.text or "1:1"
            start_beat = self.sequencer.parse_position_to_beats(start_pos)
            if start_beat is not None:
                self.display_beat = start_beat
                for track_widget in self.track_widgets:
                    track_widget.set_playback_position(start_beat)
                self.playhead_label.text = f"Pos: {start_pos}"

        # Centralized logic call
        self.sequencer.process_transport_command("play_pause")

    def _start_playback(self, start_pos):
        """Démarre la lecture après configuration du loop"""
        command = f'play "{start_pos}"'
        print(f"DEBUG: Starting playback: {command}")
        
        # 1. Réinitialiser la vue de la timeline immédiatement
        for track_widget in self.track_widgets: 
            track_widget.reset_timeline_view()
        
        # 2. Exécuter la commande JACK
        self.process_command_ui(command)
    
    def start_recording_from_ui(self):
        """Démarre l'enregistrement en utilisant les paramètres de l'interface"""
        # Vérifier qu'un port MIDI est configuré
        if not self.sequencer.default_record_port:
            self.show_midi_settings()
            return False
        
        # Trouver la piste armée
        armed_track_index = None
        for i, track in enumerate(self.sequencer.song.tracks):
            if isinstance(track, MidiTrack) and track.record_mode != 'OFF':
                if armed_track_index is not None:
                    self.show_error_popup("Multiple Tracks Armed", 
                                        "Multiple tracks are armed for recording.\nPlease arm only one track.")
                    return False
                armed_track_index = i
        
        if armed_track_index is None:
            self.show_error_popup("No Track Armed", 
                                "No track is armed for recording.\nPlease arm a MIDI track first.")
            return False
        
        # Récupérer la position de départ
        start_pos_text = self.start_pos_input.text.strip()
        if not start_pos_text:
            start_pos_text = "1:1"  # Par défaut
        
        # Convertir en beats
        start_beat = self.sequencer.parse_position_to_beats(start_pos_text)
        if start_beat is None:
            self.show_error_popup("Invalid Start Position", 
                                f"Invalid start position: {start_pos_text}")
            return False
        
        # Démarrer l'enregistrement
        try:
            result = self.sequencer.record_track(
                track_idx=armed_track_index,
                start_beat=start_beat,
                inport_name=self.sequencer.default_record_port
            )
            
            if "Error" in result:
                self.show_error_popup("Recording Error", result)
                return False
                
            return True
            
        except Exception as e:
            self.show_error_popup("Recording Error", f"Failed to start recording:\n{str(e)}")
            return False

    def pause_pressed(self, instance):
        self.sequencer.process_transport_command("play_pause")

    def stop_pressed(self, instance):
        self.sequencer.process_transport_command("stop")

    def record_pressed(self, instance):
        self.sequencer.process_transport_command("record")

    def get_armed_track(self) -> Optional[int]:
        """Retourne l'index de la piste armée, ou None si aucune piste n'est armée."""
        for i, track in enumerate(self.song.tracks):
            if isinstance(track, MidiTrack) and track.record_mode != 'OFF':
                return i
        return None

    def loop_pressed(self, instance):
        # Si on désactive le looping pendant la lecture
        if self.is_looping and self.sequencer.playback_state == 'playing':
            # Récupérer la position de fin actuelle du loop
            end_pos = self.end_pos_input.text
            if end_pos:
                # Mettre à jour la position de fin pour la lecture normale
                self.end_pos_input.text = end_pos
                # Récupérer la position actuelle
                current_pos = self.playhead_label.text.replace("Pos: ", "")
                # Envoyer une commande play avec la nouvelle fin
                command = f'play "{current_pos}" "{end_pos}"'
                print(f"DEBUG: Loop disabled, setting play range from {current_pos} to {end_pos}")
                self.process_command_ui(command)
        
        self.is_looping = not self.is_looping
        if self.is_looping:
            self.loop_button.icon = 'repeat-variant'
            self.loop_button.md_bg_color = [0, 0.4, 0.8, 1]
            start_pos = self.start_pos_input.text
            end_pos = self.end_pos_input.text
            if end_pos:
                self.end_pos_manual_override = True
            command = f'setloop "{start_pos}" "{end_pos}"'
            self.process_command_ui(command)
        else:
            self.loop_button.icon = 'repeat'
            self.loop_button.md_bg_color = [0.1, 0.1, 0.1, 1]
            self.process_command_ui('loop off')
            
    def toggle_metronome(self, instance):
        # Directly toggle the metronome state in the song object
        # This avoids the heavy UI refresh caused by process_command_ui
        new_state = not self.sequencer.song.metronome_enabled
        self.sequencer.song.metronome_enabled = new_state

        # Update the button's appearance
        if new_state:
            instance.icon = 'metronome-tick'
            instance.md_bg_color = [0.5, 0.5, 0.5, 1]
        else:
            instance.icon = 'metronome'
            instance.md_bg_color = [0.1, 0.1, 0.1, 1]

    def toggle_track_mute(self, track_index):
        """Toggles mute state for a track without a full UI refresh."""
        # 1. Update the backend state
        self.sequencer.toggle_mute(track_index)

        # 2. Find the corresponding widget and update its appearance
        if 0 <= track_index < len(self.track_widgets):
            track_widget = self.track_widgets[track_index]
            track_widget.update_mute_solo_appearance()

    def toggle_track_solo(self, track_index):
        """Toggles solo state for a track and updates others without a full UI refresh."""
        # 1. Update the backend state
        # The sequencer's toggle_solo method handles the logic of unsoloing other tracks
        self.sequencer.toggle_solo(track_index)

        # 2. Update all track widgets since soloing one can affect others
        for widget in self.track_widgets:
            widget.update_mute_solo_appearance()

    def update_track_record_buttons(self, track_index=None):
        """Met à jour l'apparence des boutons record des pistes"""
        if track_index is not None:
            # Mettre à jour une piste spécifique
            if track_index < len(self.track_list_layout.children):
                track_widget = self.track_list_layout.children[-(track_index + 1)]
                if hasattr(track_widget, 'record_mode_button'):
                    track_widget.record_mode_button.update_appearance()
        else:
            # Mettre à jour toutes les pistes - CORRECTION ICI
            for i, track_widget in enumerate(self.track_list_layout.children):
                if hasattr(track_widget, 'record_mode_button'):
                    track_widget.record_mode_button.update_appearance()

    def update_playhead(self, dt):
        """
        Unified method to update the playhead, labels, and handle scrolling.
        Called by a Clock schedule.
        """
        # 1. Read the master position from the sequencer (driven by JACK)
        jack_beat = self.sequencer.current_beat

        # 2. Calculate the smoothed display beat for fluid scrolling
        if self.sequencer.playback_state == "playing":
            # Predict next position based on tempo and delta-time
            safe_dt = min(dt, 1/15.0) # Cap dt to avoid large jumps
            beats_per_second = self.sequencer.song.tempo / 60.0
            if beats_per_second > 0:
                self.display_beat += (beats_per_second * safe_dt)

            # Calculate error and apply correction (smoothing)
            error = jack_beat - self.display_beat
            correction_speed = 5.0 # Slower correction to reduce jitter

            # Snap to master position if error is too large or on big time lags
            if abs(error) > 0.5 or dt > 0.1:
                self.display_beat = jack_beat
            else:
                self.display_beat += (error * correction_speed * dt)
        else:
            # When not playing, snap directly to the master beat
            self.display_beat = jack_beat

        # 3. Update all track widgets with the smoothed position
        for track_widget in self.track_widgets:
            track_widget.set_playback_position(self.display_beat)

        # 4. Update UI labels and animations with the smoothed position
        current_position = self.sequencer._format_beats_to_position(self.display_beat)
        self.playhead_label.text = f"Pos: {current_position}"
        self._detect_beat_one_for_animation(current_position)
        
        # 5. Check if song length has changed and update widgets if needed
        new_total_beats = self.sequencer.get_song_length_in_beats()
        if self.track_widgets and self.track_widgets[0].total_beats != new_total_beats:
             for track_widget in self.track_widgets:
                if track_widget.total_beats != new_total_beats:
                    track_widget.total_beats = new_total_beats

    def snap_ui_to_jack(self):
        """
        Force l'UI à se caler immédiatement sur la dernière position 
        connue de JACK. (Utilisé pour 'stop' et 'pause').
        """
        # 1. Lire la position "maître"
        jack_current_beat = self.sequencer.current_beat

        # 3. Forcer les widgets de piste à se caler
        for track_widget in self.track_widgets:
            track_widget.set_playback_position(jack_current_beat)

        # 4. Forcer la mise à jour des labels (boucle lente)
        self.update_playhead(0)

    def _detect_beat_one_for_animation(self, position_str):
        """Détecte si on arrive sur un beat 1 et déclenche l'animation"""
        try:
            measure, beat = map(int, position_str.split(':'))
            
            if beat == 1:
                # Vérifier si c'est une NOUVELLE mesure
                current_measure_key = measure  # Juste la mesure comme clé
                
                if not hasattr(self, '_current_measure') or self._current_measure != current_measure_key:
                    # Nouvelle mesure détectée !
                    self._current_measure = current_measure_key
                    
                    # Déclencher l'animation seulement si en lecture
                    if self.sequencer.playback_state == "playing":
                        self.start_beat_pulse_animation()
                        print(f"DEBUG: Animation beat 1 - Mesure {measure}")
                        
        except ValueError:
            # Erreur de parsing, ignorer
            pass


    def update_track_list(self):
        self.track_list_layout.clear_widgets()
        self.track_widgets.clear() # Clear the list of widget references
        # ----------------------------------------------------
        # ⚠️ NOUVEAU : FORCER L'INITIALISATION DE LA TAILLE (Critique)
        # ----------------------------------------------------
        
        # 1. Obtient la longueur correcte du morceau (le cache peut être vide, donc cette première fois est la plus longue)
        # Note: Cela déclenchera la lecture initiale du fichier audio, mais seulement UNE FOIS.
        final_total_beats = self.sequencer.get_song_length_in_beats() 
                
        for i, track in enumerate(self.sequencer.song.tracks):
            if isinstance(track, MidiTrack) and track.is_metronome:
                continue
            
            # Affecter la propriété Kivy déclenche AUTOMATIQUEMENT update_timeline_size()
            track_widget = TrackWidget(track=track, track_index=i, sequencer_layout=self)
            track_widget.total_beats = final_total_beats
            self.track_widgets.append(track_widget)
            self.track_list_layout.add_widget(track_widget)
            
    def update_status_display(self):
        song = self.sequencer.song
        self.song_name_label.text = f"Song: {song.name}"
        self.tempo_input.text = f"{song.tempo}"
        self.timesig_label.text = f"TS: {song.time_signature_numerator}/{song.time_signature_denominator}"
        self.metronome_button.icon = 'metronome-tick' if song.metronome_enabled else 'metronome'
        self.metronome_button.md_bg_color = [0.5, 0.5, 0.5, 1] if song.metronome_enabled else [0.1, 0.1, 0.1, 1]
        if not self.end_pos_manual_override:
            end_of_song_beats = self.sequencer.get_song_length_in_beats()
            self.end_pos_input.text = self.sequencer._format_beats_to_position(end_of_song_beats)

        # Ensure the sequencer object is aware of the UI's start/end positions
        self.sequencer.ui_start_pos_str = self.start_pos_input.text
        self.sequencer.ui_end_pos_str = self.end_pos_input.text

        self.update_track_list()
        
####
    def on_tempo_validate(self, instance=None):
        """Valide le tempo saisi"""
        try:
            tempo = int(self.tempo_input.text)
            if 1 <= tempo <= 300:
                self.process_command_ui(f'tempo {tempo}')
            else:
                # Remettre la valeur précédente si hors limites
                self.tempo_input.text = str(self.sequencer.song.tempo)
        except ValueError:
            # Remettre la valeur précédente si invalide
            self.tempo_input.text = str(self.sequencer.song.tempo)

    def on_start_position_validate(self, instance=None):
        """Valide la position de début"""
        position = self.start_pos_input.text
        print(f"Start position validated: {position}")
        # Ici vous pouvez ajouter la logique pour traiter la nouvelle position de début
        # Par exemple :
        # self.process_command_ui(f'startpos "{position}"')

    def on_end_position_validate(self, instance=None):
        """Valide la position de fin"""
        position = self.end_pos_input.text
        self.sequencer.ui_end_pos_str = position
        print(f"End position validated: {position}")
        self.end_pos_manual_override = True
        # Ici vous pouvez ajouter la logique pour traiter la nouvelle position de fin
        # Par exemple :
        # self.process_command_ui(f'endpos "{position}"')

    def on_start_pos_text_change(self, instance, value):
        self.sequencer.ui_start_pos_str = value

    def on_end_pos_text_change(self, instance, value):
        self.sequencer.ui_end_pos_str = value

    # Méthodes de gestion des flèches
    def handle_textinput_arrows(self, textinput, direction, modifiers, cursor_pos):
        """Gère les flèches haut/bas dans les TextInput avec position du curseur"""
        print(f"DEBUG: handle_textinput_arrows called - {textinput.field_type}, {direction}, cursor_pos: {cursor_pos}")
        
        if textinput.field_type == 'position':
            self.handle_position_arrows_in_textinput(textinput, direction, modifiers, cursor_pos)
        elif textinput.field_type == 'tempo':
            self.handle_tempo_arrows_in_textinput(textinput, direction, modifiers)

    def handle_position_arrows_in_textinput(self, textinput, direction, modifiers, cursor_pos):
        """Gère les flèches pour les champs position avec gestion du curseur"""
        try:
            # Analyser la position actuelle
            measure, beat = map(int, textinput.text.split(':'))
            step = 10 if 'shift' in modifiers else 1
            beats_per_measure = self.sequencer.song.time_signature_numerator
            
            # Déterminer si le curseur est sur la mesure ou le beat
            cursor_index = cursor_pos
            colon_index = textinput.text.find(':')
            
            if cursor_index <= colon_index:
                # Curseur sur la mesure
                if direction == 'up':
                    measure += step
                    # Le beat reste inchangé, mais on le limite à la signature rythmique
                    beat = min(beat, beats_per_measure)
                else:  # 'down'
                    if measure > 1:
                        measure = max(1, measure - step)
                    # Si on est à la mesure 1, on ne change rien
                    # Le beat reste inchangé
                print(f"DEBUG: Adjusting measure only: {measure}:{beat}")
                
            else:
                # Curseur sur le beat
                if direction == 'up':
                    beat += step
                    if beat > beats_per_measure:
                        measure += 1
                        beat = 1
                else:  # 'down'
                    if beat > 1:
                        beat -= step
                    else:
                        # Beat = 1, on veut descendre
                        if measure > 1:
                            measure -= 1
                            beat = beats_per_measure
                        # Si measure = 1 et beat = 1, on ne fait rien
                print(f"DEBUG: Adjusting beat only: {measure}:{beat}")
            
            # Contraintes finales
            measure = max(1, measure)
            beat = max(1, min(beat, beats_per_measure))  # Entre 1 et beats_per_measure
            
            new_position = f"{measure}:{beat}"
            textinput.text = new_position
            
            # Restaurer la position du curseur
            self.restore_cursor_position(textinput, cursor_index, colon_index, new_position)
            
            print(f"DEBUG: Position changed to {new_position}")
            
            # Déclencher la validation
            if textinput == self.start_pos_input:
                self.on_start_position_validate()
            elif textinput == self.end_pos_input:
                self.on_end_position_validate()
                
        except ValueError as e:
            print(f"DEBUG: Error parsing position: {e}")
            textinput.text = "1:1"

    def restore_cursor_position(self, textinput, old_cursor_index, old_colon_index, new_text):
        """Tente de restaurer une position logique du curseur"""
        new_colon_index = new_text.find(':')
        
        if old_cursor_index <= old_colon_index:
            # Curseur était sur la mesure - le garder sur la mesure
            # Calculer la nouvelle longueur de la mesure
            new_measure_length = len(new_text.split(':')[0])
            # Placer le curseur à la fin de la mesure ou à sa position relative
            if old_cursor_index == old_colon_index:
                # Curseur était sur le ':' - le placer sur le nouveau ':'
                new_cursor_pos = new_colon_index
            else:
                # Curseur était dans la mesure - ajuster proportionnellement
                old_measure_length = old_colon_index
                if old_measure_length > 0:
                    ratio = old_cursor_index / old_measure_length
                    new_cursor_pos = min(int(new_measure_length * ratio), new_measure_length)
                else:
                    new_cursor_pos = new_measure_length
        else:
            # Curseur était sur le beat - le garder sur le beat
            old_beat_position = old_cursor_index - old_colon_index - 1
            old_beat_length = len(textinput.text) - old_colon_index - 1
            
            new_beat_length = len(new_text) - new_colon_index - 1
            
            if old_beat_length > 0:
                ratio = old_beat_position / old_beat_length
                new_beat_position = min(int(new_beat_length * ratio), new_beat_length)
                new_cursor_pos = new_colon_index + 1 + new_beat_position
            else:
                new_cursor_pos = new_colon_index + 1
        
        # Appliquer la nouvelle position du curseur
        textinput.cursor = (new_cursor_pos, new_cursor_pos)

    def handle_tempo_arrows_in_textinput(self, textinput, direction, modifiers):
        """Gère les flèches pour le champ tempo"""
        try:
            current_tempo = int(float(textinput.text))
            step = 10 if 'shift' in modifiers else 1
            
            if direction == 'up':
                new_tempo = current_tempo + step
            else:  # 'down'
                new_tempo = max(1, current_tempo - step)
                
            textinput.text = str(new_tempo)
            print(f"DEBUG: Tempo changed to {new_tempo}")
            self.on_tempo_validate()
            
        except ValueError as e:
            print(f"DEBUG: Error parsing tempo: {e}")
            textinput.text = str(self.sequencer.song.tempo)

####

    def on_enter(self, instance):
        command = self.input_text.text
        self.input_text.text = ''
        self.process_command_ui(command)

    def process_slider_command(self, command):
        from .main import process_command
        process_command(command, self.sequencer, api_mode=True, confirmation_handler=None)

    def process_command_ui(self, command):
        import json
        from .main import process_command

        self.current_command = command

        def confirmation_callback(user_input):
            full_command = f'{self.current_command} "{user_input}"'
            self.process_command_ui(full_command)

        should_continue, output = process_command(self.current_command, self.sequencer, api_mode=True, confirmation_handler=None)

        try:
            data = json.loads(output)
            if data.get("status") == "file_chooser_prompt":
                def file_chooser_callback(filepath):
                    track_name = data["track_name"]
                    full_command = f'addaudio "{track_name}" "{filepath}"'
                    self.process_command_ui(full_command)
                popup = FileChooserPopup(
                    callback=file_chooser_callback,
                    title="Select Audio File",
                    filters=['*.wav', '*.mp3', '*.aiff', '*.ogg']  # Fichiers audio
                )
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
                self.current_command = ""
        except (json.JSONDecodeError, TypeError):
            if output:
                self.output_label.text += output + "\n"
            self.current_command = ""

        # --- Conditional UI Refresh ---
        # Define commands that DON'T require a full UI rebuild
        lightweight_commands = [
            'play', 'pause', 'stop', 'loop', 'setloop', 'seek'
        ]

        # Check if the processed command starts with any of the lightweight commands
        is_lightweight = any(command.startswith(cmd) for cmd in lightweight_commands)

        if not is_lightweight:
            print(f"DEBUG: Performing full UI refresh for command: {command}")
            self.update_status_display()
        else:
            print(f"DEBUG: Skipping full UI refresh for lightweight command: {command}")


        if not should_continue:
            MDApp.get_running_app().stop()

    def start_beat_pulse_animation(self):
        """Animation combinée pulse + glow pour le beat 1"""
        # Ne pas animer si on est en pause ou arrêté
        if self.sequencer.playback_state != "playing":
            return
            
        # Arrêter toute animation existante
        self.stop_beat_pulse_animation()
        
        self.pulse_phase = 0
        self.original_width = self.play_button.width
        self.original_height = self.play_button.height
        self.beat_pulse_animation = Clock.schedule_interval(self._beat_pulse_glow, 0.1)
        
    def stop_beat_pulse_animation(self):
        """Arrête l'animation et remet tout à la normale"""
        if hasattr(self, 'beat_pulse_animation') and self.beat_pulse_animation is not None:
            try:
                self.beat_pulse_animation.cancel()
            except:
                pass  # Ignorer les erreurs si l'animation est déjà arrêtée
            self.beat_pulse_animation = None
        
        # Remettre les valeurs originales
        self.play_button.icon_color = [0, 0.7, 0.3, 1]
        self.play_button.md_bg_color = [0.1, 0.1, 0.1, 1]
        if hasattr(self, 'original_width'):
            self.play_button.width = self.original_width
            self.play_button.height = self.original_height

    def _beat_pulse_glow(self, dt):
        """Animation de pulse avec effet glow"""
        # Vérifier que l'animation est toujours valide
        if not hasattr(self, 'beat_pulse_animation') or self.beat_pulse_animation is None:
            return
            
        self.pulse_phase += 1
        
        if self.pulse_phase <= 5:  # Phase d'expansion (0.5 seconde)
            # Effet de glow progressif
            intensity = 0.3 + (self.pulse_phase * 0.14)  # 0.3 → 1.0
            glow_color = [intensity, 0.3 + intensity * 0.7, 0.1 + intensity * 0.3, 1]
            
            self.play_button.icon_color = glow_color
            
            # Effet d'agrandissement subtil
            scale = 1.0 + (self.pulse_phase * 0.03)
            self.play_button.width = self.original_width * scale
            self.play_button.height = self.original_height * scale
            
        else:
            # Phase de contraction (0.5 seconde)
            if self.pulse_phase <= 10:
                intensity = 1.0 - ((self.pulse_phase - 5) * 0.14)  # 1.0 → 0.3
                glow_color = [intensity, 0.3 + intensity * 0.7, 0.1 + intensity * 0.3, 1]
                
                self.play_button.icon_color = glow_color
                
                scale = 1.15 - ((self.pulse_phase - 5) * 0.03)
                self.play_button.width = self.original_width * scale
                self.play_button.height = self.original_height * scale
            else:
                # Fin de l'animation
                self.stop_beat_pulse_animation()


class SequencerApp(MDApp):
    def build(self):
        self.theme_cls.theme_style = "Dark"
        self.theme_cls.primary_palette = "Blue"
        return SequencerLayout()

    def on_stop(self):
        layout = self.root
        layout.sequencer.stop()
        layout.sequencer.close_virtual_ports()

if __name__ == '__main__':
    SequencerApp().run()