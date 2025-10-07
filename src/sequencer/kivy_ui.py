import kivy
kivy.require('2.3.1')

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
from kivy.graphics import Color, Rectangle, Line
from kivymd.uix.label import MDIcon

from sequencer.sequencer import Sequencer
from sequencer.models import MidiTrack, AudioTrack, AutomationTrack
from typing import Optional
import sys, os

class TooltipMDIconButton(MDIconButton):
    tooltip_text = StringProperty()

    def __init__(self, **kwargs):
        self.tooltip_text = kwargs.pop("tooltip_text", "")
        super().__init__(**kwargs)
        self.tooltip_delay = 0.2
        self.tooltip_label = None
        self._show_event = None
        Window.bind(mouse_pos=self.on_mouse_pos)
        
        self.bind(tooltip_text=self._on_tooltip_text_changed)

    def _on_tooltip_text_changed(self, instance, value):
        """Quand le tooltip_text change, mettre à jour le tooltip existant"""
        if self.tooltip_label:
            # Mettre à jour le texte
            self.tooltip_label.text = value
            
            # Recalculer la taille
            self.tooltip_label.texture_update()
            natural_size = self.tooltip_label.texture_size
            if natural_size:
                new_size = (natural_size[0] + dp(20), dp(32))
            else:
                new_size = (dp(120), dp(32))
            
            # Mettre à jour la taille
            self.tooltip_label.size = new_size
            
            # Mettre à jour le fond
            if hasattr(self, "_bg_rect"):
                self._bg_rect.size = new_size

    def on_mouse_pos(self, window, pos):
        collide = self.collide_point(*self.to_widget(*pos))
        if collide:
            self._ensure_tooltip()
            self._update_tooltip_position(pos)
            if not self._show_event:
                self._show_event = Clock.schedule_once(self._do_show_tooltip, self.tooltip_delay)
        else:
            self._hide_tooltip()

    def _ensure_tooltip(self):
        if self.tooltip_label:
            return
            
        # Créer le label
        self.tooltip_label = Label(
            text=self.tooltip_text,
            size_hint=(None, None),
            color=(1, 1, 1, 1),
            font_size=dp(14),
            padding=(dp(10), dp(6))
        )
        
        # Calculer la taille naturelle
        self.tooltip_label.texture_update()
        natural_size = self.tooltip_label.texture_size
        if natural_size:
            self.tooltip_label.size = (natural_size[0] + dp(20), dp(32))
        else:
            self.tooltip_label.size = (dp(120), dp(32))
        
        with self.tooltip_label.canvas.before:
            Color(0, 0, 0, 0.85)
            self._bg_rect = Rectangle(pos=self.tooltip_label.pos, size=self.tooltip_label.size)
        self.tooltip_label.bind(pos=self._update_bg, size=self._update_bg)

    def _update_bg(self, instance, value):
        if hasattr(self, "_bg_rect"):
            self._bg_rect.pos = instance.pos
            self._bg_rect.size = instance.size

    def _do_show_tooltip(self, dt):
        if self.tooltip_label and self.tooltip_label.parent is None:
            Window.add_widget(self.tooltip_label)
        self._show_event = None

    def _update_tooltip_position(self, mouse_pos):
        if not self.tooltip_label:
            return
        x, y = mouse_pos
        self.tooltip_label.pos = (x + dp(10), y + dp(10))
        self._update_bg(self.tooltip_label, None)

    def _hide_tooltip(self):
        if self._show_event:
            self._show_event.cancel()
            self._show_event = None
        if self.tooltip_label and self.tooltip_label.parent:
            Window.remove_widget(self.tooltip_label)

    def on_parent(self, instance, parent):
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
        '''
        minus_button = TooltipMDIconButton(
            icon='minus',
            tooltip_text='Decrement',
            on_press=self.decrement,
            theme_icon_color="Custom",
            icon_color=[0.9, 0.3, 0.3, 1],
            theme_bg_color="Custom",
            md_bg_color=[0.1, 0.1, 0.1, 1]
        )
        self.add_widget(minus_button)
        '''
        
        # Utiliser CustomTextInput
        self.text_input = CustomTextInput(
            text=str(initial_value),
            multiline=False,
            halign='center',
            padding=[dp(6), dp(4), dp(6), dp(4)],
            size_hint_x=None,
            width=dp(50),
            size_hint_y=None,
            height=dp(28),
            pos_hint={'center_y': 0.65},
            font_size=dp(16),
            field_type='program',
            callback=self.handle_arrow_keys  # Callback pour les flèches
        )
        self.text_input.bind(on_text_validate=self.on_text_change)
        self.add_widget(self.text_input)
        '''
        plus_button = TooltipMDIconButton(
            icon='plus',
            tooltip_text='Increment',
            on_press=self.increment,
            theme_icon_color="Custom",
            icon_color=[0.3, 0.9, 0.3, 1],
            theme_bg_color="Custom",
            md_bg_color=[0.1, 0.1, 0.1, 1]
        )
        self.add_widget(plus_button)
        '''

    def handle_arrow_keys(self, textinput, direction, modifiers, cursor_pos=None):
        """Gère les flèches haut/bas dans le spinner"""
        print(f"DEBUG: Arrow key in program spinner: {direction}")
        
        try:
            current_value = int(self.text_input.text)
            step = 10 if 'shift' in modifiers else 1
            
            if direction == 'up':
                new_value = min(self.max_val, current_value + step)
            else:  # 'down'
                new_value = max(self.min_val, current_value - step)
            
            # Mettre à jour la valeur
            self._update_value(new_value)
            
        except ValueError:
            # En cas d'erreur, revenir à la dernière valeur valide
            self.text_input.text = str(self.last_valid_value)

    def _update_value(self, new_value):
        """Met à jour la valeur et déclenche le callback"""
        self.text_input.text = str(new_value)
        self.last_valid_value = new_value
        # Appeler le callback original
        if self.callback:
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
        self.size_hint = (0.5, 0.3)  # Réduit de (0.8, 0.4) à (0.5, 0.3)
        self.callback = callback

        layout = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(10))
        layout.add_widget(Label(text=prompt_text))

        buttons_layout = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(10))
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
        self.size_hint = (0.5, 0.4)  # Réduit de (0.8, 0.5) à (0.5, 0.4)
        self.sequencer = sequencer
        self.callback = callback

        layout = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(10))
        start_layout = BoxLayout(size_hint_y=None, height=dp(30))
        start_layout.add_widget(Label(text="Start (measure:beat):"))
        self.start_input = TextInput(text="1:1", multiline=False, size_hint_x=None, width=dp(100))
        start_layout.add_widget(self.start_input)
        layout.add_widget(start_layout)

        end_layout = BoxLayout(size_hint_y=None, height=dp(30))
        end_layout.add_widget(Label(text="End (measure:beat):"))
        end_of_song = self.sequencer._format_beats_to_position(self.sequencer.get_song_length_in_beats())
        self.end_input = TextInput(text=end_of_song, multiline=False, size_hint_x=None, width=dp(100))
        end_layout.add_widget(self.end_input)
        layout.add_widget(end_layout)

        buttons_layout = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(10))
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
        self.filechooser = FileChooserListView(
            path='/home/gilles/fluid-sequencer',
            filters=filters if filters is not None else ['*.proj.json', '*.mid']  # Utilise les filters passés en paramètre
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
        self.size_hint = (0.5, 0.3)  # Réduit de (0.8, 0.4) à (0.5, 0.3)
        self.callback = callback

        layout = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(10))
        layout.add_widget(Label(text=prompt_text))

        buttons_layout = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(10))
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

class CustomTextInput(TextInput):
    def __init__(self, field_type='', callback=None, **kwargs):
        self.field_type = field_type
        self.callback = callback
        super().__init__(**kwargs)
    
    def keyboard_on_key_down(self, window, keycode, text, modifiers):
        print(f"DEBUG: Key pressed in {self.field_type}: {keycode}")
        
        if isinstance(keycode, tuple) and len(keycode) > 1:
            key_name = keycode[1]
        else:
            key_name = str(keycode)
        
        if key_name in ('up', 'down') and self.callback:
            print(f"DEBUG: Processing arrow key {key_name}")
            # Passer la position du curseur au callback
            cursor_pos = self.cursor_index()
            self.callback(self, key_name, modifiers, cursor_pos)
            return True
        
        return super().keyboard_on_key_down(window, keycode, text, modifiers)
   
    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            self.focus = True
        return super().on_touch_down(touch)   
    
    def on_touch_up(self, touch):
        # Capturer la molette dans on_touch_up
        if self.collide_point(*touch.pos) and hasattr(touch, 'button'):
            if touch.button == 'scrollup' and self.callback:
                # 'scrollup' = vers le haut = diminuer la valeur
                cursor_pos = self.cursor_index()
                self.callback(self, 'down', [], cursor_pos)  # Note: 'down' pour scrollup
                return True
            elif touch.button == 'scrolldown' and self.callback:
                # 'scrolldown' = vers le bas = augmenter la valeur  
                cursor_pos = self.cursor_index()
                self.callback(self, 'up', [], cursor_pos)  # Note: 'up' pour scrolldown
                return True
        return super().on_touch_up(touch)  

class ThreeStateRecordButton(TooltipMDIconButton):
    def __init__(self, track, track_index, sequencer_layout, callback=None, **kwargs):
        super().__init__(**kwargs)
        
        self.track = track
        self.track_index = track_index
        self.sequencer_layout = sequencer_layout
        self.callback = callback
        self.size_hint_x = None
        self.width = dp(44)
        self.size_hint_y = None
        self.height = dp(44)
        self.pos_hint = {'center_y': 0.5}
        self.theme_icon_color = "Custom"
        self.theme_bg_color = "Custom"
        
        self.states = {
            'OFF': {
                'icon': 'record-circle-outline',
                'tooltip': 'Record: OFF - Piste désactivée',
                'icon_color': [0.5, 0.5, 0.5, 1],
                'bg_color': [0.1, 0.1, 0.1, 1]
            },
            'OVERWRITE': {
                'icon': 'record-rec',
                'tooltip': 'Record: OVERWRITE - Écrase les notes existantes',
                'icon_color': [1, 0, 0, 1],  # Rouge
                'bg_color': [0.1, 0.1, 0.1, 1]
            },
            'KEEP': {
                'icon': 'record-circle',
                'tooltip': 'Record: KEEP - Conserve les notes existantes',
                'icon_color': [1, 0.6, 0, 1],  # Orange pour différencier
                'bg_color': [0.1, 0.1, 0.1, 1]
            }
        }
        
        self.update_appearance()
        
    def on_press(self):
        """Cycle through the 3 states on press avec gestion d'exclusivité"""
        old_mode = self.track.record_mode
        
        # Si on essaie d'activer une piste (passer de OFF à OVERWRITE/KEEP)
        if old_mode == 'OFF':
            # Désactiver toutes les autres pistes MIDI
            self._disable_other_tracks()
            # Activer cette piste
            self.track.record_mode = 'OVERWRITE'
            
        # Si on est déjà activé, cycler entre OVERWRITE et KEEP
        elif old_mode == 'OVERWRITE':
            self.track.record_mode = 'KEEP'
            
        else:  # 'KEEP' -> retour à OFF
            self.track.record_mode = 'OFF'
            
        print(f"DEBUG: Track {self.track_index} record mode changed from {old_mode} to {self.track.record_mode}")
            
        self.update_appearance()
        
        # FORCER la mise à jour de tous les boutons record
        self.sequencer_layout.update_track_record_buttons()
        
        if self.callback:
            self.callback(self.track)
    
    def _disable_other_tracks(self):
        """Désactive toutes les autres pistes MIDI"""
        print(f"DEBUG: Disabling other MIDI tracks...")
        for i, track in enumerate(self.sequencer_layout.sequencer.song.tracks):
            if (isinstance(track, MidiTrack) and 
                i != self.track_index and 
                track.record_mode != 'OFF'):
                
                print(f"DEBUG: Disabling track {i} (was {track.record_mode})")
                track.record_mode = 'OFF'
    
    def update_appearance(self):
        """Update button appearance and tooltip based on current state"""
        state_config = self.states[self.track.record_mode]
        self.icon = state_config['icon']
        self.tooltip_text = state_config['tooltip']
        self.icon_color = state_config['icon_color']
        self.md_bg_color = state_config['bg_color']

class ConfirmationPopup(Popup):
    def __init__(self, prompt_text, callback, **kwargs):
        super(ConfirmationPopup, self).__init__(**kwargs)
        self.title = "Confirmation"
        self.size_hint = (0.5, 0.3)  # Réduit de (0.8, 0.4) à (0.5, 0.3)
        self.callback = callback

        layout = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(10))
        layout.add_widget(Label(text=prompt_text))

        self.text_input = TextInput(multiline=False, size_hint=(1, None), width=dp(200), height=dp(30))  # Taille ajustée
        layout.add_widget(self.text_input)

        buttons_layout = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(10))
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

class SaveProjectAsPopup(Popup):
    def __init__(self, sequencer, callback, **kwargs):
        super(SaveProjectAsPopup, self).__init__(**kwargs)
        self.title = "Save Project As"
        self.size_hint = (0.6, 0.6)
        self.sequencer = sequencer
        self.callback = callback

        layout = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(10))
        self.filechooser = FileChooserListView(
            path='/home/gilles/fluid-sequencer',
            filters=['*.proj.json']
        )
        layout.add_widget(self.filechooser)

        filename_layout = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(10))
        filename_layout.add_widget(Label(text="Filename:", halign='left', size_hint_x=None, width=dp(80)))  # Aligné à gauche avec largeur fixe
        self.filename_input = TextInput(
            text=self.sequencer.last_project_basename if self.sequencer.last_project_basename else "",
            multiline=False,
            size_hint_x=None,
            width=dp(200)
        )
        filename_layout.add_widget(self.filename_input)
        layout.add_widget(filename_layout)

        buttons_layout = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(10))
        save_button = TooltipMDIconButton(icon='content-save', tooltip_text='Save')
        save_button.bind(on_press=self.on_save)
        cancel_button = TooltipMDIconButton(icon='cancel', tooltip_text='Cancel')
        cancel_button.bind(on_press=self.dismiss)
        buttons_layout.add_widget(save_button)
        buttons_layout.add_widget(cancel_button)
        layout.add_widget(buttons_layout)

        self.content = layout

    def on_save(self, instance):
        filepath = self.filechooser.path
        filename = self.filename_input.text
        if filename:
            if not filename.endswith('.proj.json'):
                filename += '.proj.json'
            full_path = f"{filepath}/{filename}"
            self.callback(full_path)
            self.dismiss()

class SequencerLayout(BoxLayout):
    
    def __init__(self, **kwargs):
        super(SequencerLayout, self).__init__(**kwargs)
        self.orientation = 'vertical'
        self.sequencer = Sequencer(gui_mode=True)
        self.current_command = ""
        self.end_pos_manual_override = False
        self.is_playing = False
        self.is_paused = False
        self.is_recording = False
        self.is_looping = False
        self.blink_animation = None  # Référence à l'animation de clignotement
        self._current_measure = None # Initialisation pour la détection du beat 1

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
        self.start_pos_input.bind(on_text_validate=self.on_start_position_validate)
        self.end_pos_input.bind(on_text_validate=self.on_end_position_validate)
   
        
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
        
        # Liste des pistes - DOIT être créé AVANT update_status_display()
        self.track_list_layout = BoxLayout(orientation='vertical', size_hint_y=None)
        self.track_list_layout.bind(minimum_height=self.track_list_layout.setter('height'))
        self.add_widget(self.track_list_layout)

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
        self.sequencer.bind(current_beat=self.update_playhead_display)
        # Vérifier toutes les secondes si la lecture est terminée
        Clock.schedule_interval(self.check_if_playback_finished, 1.0)        
        # Ajouter une variable pour stocker la position de fin pendant la pause
        self.saved_end_pos = ""

    def update_menu_lines(self, instance, value):
        """Met à jour toutes les lignes des menus"""
        if hasattr(self, 'file_line'):
            self.file_line.points = [0, -1, instance.width, -1]
        if hasattr(self, 'edit_line'):
            self.edit_line.points = [0, -1, instance.width, -1]
        if hasattr(self, 'settings_line'):
            self.settings_line.points = [0, -1, instance.width, -1]

    def check_if_playback_finished(self, dt):
        """Vérifie si la lecture est terminée et arrête le clignotement si besoin"""
        if self.is_playing and not self.is_looping:
            # Vérifier si on a dépassé la position de fin
            current_beat = getattr(self.sequencer, 'current_beat', 0)
            end_pos_text = self.end_pos_input.text
            
            if end_pos_text:
                try:
                    end_beat = self.sequencer.parse_position_to_beats(end_pos_text)
                    if current_beat >= end_beat:
                        # La lecture est terminée
                        self.is_playing = False
                        self.play_button.icon = 'play'
                        self.stop_play_blink()
                        print("DEBUG: Playback finished, stopping blink")
                except:
                    pass


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

    def close_all_menus(self):
        """Ferme tous les menus ouverts"""
        menus_to_close = ['file_menu', 'edit_menu', 'settings_menu']
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
        print(f"DEBUG play_pressed: play called with is_playing={self.is_playing}, is_paused={self.is_paused}, is_looping={self.is_looping}")
        
        # Ne rien faire si déjà en lecture ET pas en pause
        if self.is_playing and not self.is_paused:
            print("DEBUG: Already playing and not paused, ignoring play press")
            return
        
        # Si on est en pause, c'est le bouton pause qui doit gérer la reprise
        if self.is_paused:
            print("DEBUG: Currently paused, use pause button to resume")
            return
        
        self.is_playing = True
        self.play_button.icon = 'play-circle-outline'
        self.start_play_blink()
        start_pos = self.start_pos_input.text or "1:1"
        end_pos = self.end_pos_input.text
        
        if self.is_looping:
            # Si le looping est activé, on veut jouer en boucle
            if end_pos:
                # Avec une fin spécifique
                command = f'loop "{start_pos}" "{end_pos}"'
            else:
                # Sans fin spécifique, juste activer le looping
                command = 'loop'
            print(f"DEBUG: Sending loop command: {command}")
            self.process_command_ui(command)
            
            # Attendre un peu que le loop soit configuré puis lancer la lecture
            from kivy.clock import Clock
            Clock.schedule_once(lambda dt: self._start_playback(start_pos), 0.1)
        else:
            # Mode lecture normal - TOUJOURS spécifier une fin de lecture
            if end_pos:
                command = f'play "{start_pos}" "{end_pos}"'
            else:
                # Si pas de fin spécifiée, utiliser la fin de la chanson
                end_of_song = self.sequencer._format_beats_to_position(self.sequencer.get_song_length_in_beats())
                command = f'play "{start_pos}" "{end_of_song}"'
            print(f"DEBUG: Sending command: {command}")
            self.process_command_ui(command)
        
        # Désactiver pause et record
        if self.is_paused:
            self.is_paused = False
            self.pause_button.icon = 'pause'
            self.pause_button.md_bg_color = [0.1, 0.1, 0.1, 1]
        if self.is_recording:
            self.is_recording = False
            self.record_button.icon = 'record'
            self.record_button.md_bg_color = [0.1, 0.1, 0.1, 1]

    def _start_playback(self, start_pos):
        """Démarre la lecture après configuration du loop"""
        command = f'play "{start_pos}"'
        print(f"DEBUG: Starting playback: {command}")
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
                
            # Mettre à jour l'interface
            self.is_recording = True
            self.record_button.icon = 'record-circle-outline'
            self.record_button.md_bg_color = [0.8, 0, 0, 1]
            
            # Arrêter la lecture si active
            if self.is_playing:
                self.is_playing = False
                self.play_button.icon = 'play'
                self.stop_play_blink()
            if self.is_paused:
                self.is_paused = False
                self.pause_button.icon = 'pause'
                self.pause_button.md_bg_color = [0.1, 0.1, 0.1, 1]
                
            return True
            
        except Exception as e:
            self.show_error_popup("Recording Error", f"Failed to start recording:\n{str(e)}")
            return False

    def pause_pressed(self, instance):
        # Vérifier si une lecture est en cours OU si on est en pause
        if not self.is_playing and not self.is_paused:
            print("DEBUG: No playback in progress, ignoring pause")
            return
        
        # Vérifier si la lecture est déjà terminée (seulement si en cours de lecture)
        if self.is_playing and not self.is_paused:
            current_beat = getattr(self.sequencer, 'current_beat', 0)
            end_pos_text = self.end_pos_input.text
            
            if end_pos_text and not self.is_looping:
                try:
                    end_beat = self.sequencer.parse_position_to_beats(end_pos_text)
                    if current_beat >= end_beat:
                        # La lecture est déjà terminée, ne rien faire
                        print("DEBUG: Playback already finished, ignoring pause")
                        return
                except:
                    pass
        
        self.is_paused = not self.is_paused
        if self.is_paused:
            # Sauvegarder la position de fin actuelle
            self.saved_end_pos = self.end_pos_input.text
            
            self.pause_button.icon = 'pause-circle-outline'
            self.pause_button.md_bg_color = [0.9, 0.7, 0, 1]
            self.process_command_ui('pause')
            # Arrêter le clignotement du play si actif
            if self.is_playing:
                self.is_playing = False
                self.play_button.icon = 'play'
                self.stop_play_blink()
            if self.is_recording:
                self.is_recording = False
                self.record_button.icon = 'record'
                self.record_button.md_bg_color = [0.1, 0.1, 0.1, 1]
        else:
            self.pause_button.icon = 'pause'
            self.pause_button.md_bg_color = [0.1, 0.1, 0.1, 1]
            # Mettre à jour l'état de lecture et démarrer le clignotement
            self.is_playing = True
            self.start_play_blink()
            
            # Récupérer la position actuelle et la fin sauvegardée
            current_pos = self.playhead_label.text.replace("Pos: ", "")
            end_pos = self.saved_end_pos if self.saved_end_pos else self.end_pos_input.text
            
            if end_pos and not self.is_looping:
                # Reprendre depuis la position actuelle avec la fin sauvegardée
                command = f'play "{current_pos}" "{end_pos}"'
                print(f"DEBUG: Resuming from {current_pos} to {end_pos}")
            elif self.is_looping:
                # Mode looping - reprendre normalement
                command = 'play'
                print("DEBUG: Resuming loop playback")
            else:
                # Reprendre normalement
                command = 'play'
                print("DEBUG: Resuming normal playback")
                
            self.process_command_ui(command)
            # Réinitialiser la sauvegarde
            self.saved_end_pos = ""

    def stop_pressed(self, instance):
        self.is_playing = False
        self.is_paused = False
        self.is_recording = False
        
        # Arrêter l'animation du beat 1 si active
        self.stop_beat_pulse_animation()
        
        # Arrêter aussi l'animation de clignotement play
        self.stop_play_blink()
        
        self.play_button.icon = 'play'
        self.pause_button.icon = 'pause'
        self.pause_button.md_bg_color = [0.1, 0.1, 0.1, 1]
        self.record_button.icon = 'record'
        self.record_button.md_bg_color = [0.1, 0.1, 0.1, 1]
        self.process_command_ui('stop')

    def record_pressed(self, instance):
        if self.is_recording:
            # Arrêter l'enregistrement
            self.is_recording = False
            self.record_button.icon = 'record'
            self.record_button.md_bg_color = [0.1, 0.1, 0.1, 1]
            self.process_command_ui('stop')
        else:
            # Démarrer l'enregistrement
            if self.start_recording_from_ui():
                print("Recording started successfully")

    def get_armed_track(self) -> Optional[int]:
        """Retourne l'index de la piste armée, ou None si aucune piste n'est armée."""
        for i, track in enumerate(self.song.tracks):
            if isinstance(track, MidiTrack) and track.record_mode != 'OFF':
                return i
        return None

    def loop_pressed(self, instance):
        # Si on désactive le looping pendant la lecture
        if self.is_looping and self.is_playing:
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
        if instance.icon == 'metronome':
            instance.icon = 'metronome-tick'
            instance.md_bg_color = [0.5, 0.5, 0.5, 1]
            self.process_command_ui('metronome on')
        else:
            instance.icon = 'metronome'
            instance.md_bg_color = [0.1, 0.1, 0.1, 1]
            self.process_command_ui('metronome off')

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

    def update_playhead_display(self, instance, value):
        """Met à jour l'affichage de la position et détecte le beat 1"""
        current_position = self.sequencer._format_beats_to_position(value)
        self.playhead_label.text = f"Pos: {current_position}"
        
        # Détecter le beat 1 pour l'animation
        self._detect_beat_one_for_animation(current_position)

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
                    if self.is_playing and not self.is_paused:
                        self.start_beat_pulse_animation()
                        print(f"DEBUG: Animation beat 1 - Mesure {measure}")
                        
        except ValueError:
            # Erreur de parsing, ignorer
            pass


    def update_track_list(self):
        self.track_list_layout.clear_widgets()
        for i, track in enumerate(self.sequencer.song.tracks):
            if isinstance(track, MidiTrack) and track.is_metronome:
                continue
            track_widget = TrackWidget(track=track, track_index=i, sequencer_layout=self)
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
        print(f"End position validated: {position}")
        self.end_pos_manual_override = True
        # Ici vous pouvez ajouter la logique pour traiter la nouvelle position de fin
        # Par exemple :
        # self.process_command_ui(f'endpos "{position}"')

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
            current_tempo = int(textinput.text)
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
        from main import process_command
        process_command(command, self.sequencer, api_mode=True, confirmation_handler=None)

    def process_command_ui(self, command):
        import json
        from main import process_command

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

        self.update_status_display()

        if not should_continue:
            MDApp.get_running_app().stop()

    def start_beat_pulse_animation(self):
        """Animation combinée pulse + glow pour le beat 1"""
        # Ne pas animer si on est en pause ou arrêté
        if not self.is_playing or self.is_paused:
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


if __name__ == '__main__':
    SequencerApp().run()