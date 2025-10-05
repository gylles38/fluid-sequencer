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

    def on_mouse_pos(self, window, pos):
        collide = self.collide_point(*self.to_widget(*pos))
        #Logger.info(f"Tooltip: '{self.tooltip_text}' – mouse {pos} – collide {collide}")
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
        self.tooltip_label = Label(
            text=self.tooltip_text,
            size_hint=(None, None),
            size=(dp(120), dp(32)),
            color=(1, 1, 1, 1),
            font_size=dp(14),
        )
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

        self.text_input = TextInput(
            text=str(initial_value),
            multiline=False,
            halign='center',
            padding=[dp(6), dp(4), dp(6), dp(4)],
            size_hint_x=None,
            width=dp(50),
            size_hint_y=None,
            height=dp(28),
            pos_hint={'center_y': 0.65},  # Ajusté de 0.6 à 0.65 pour un meilleur alignement
            font_size=dp(16)
        )
        self.text_input.bind(on_text_validate=self.on_text_change)
        self.add_widget(self.text_input)

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
        self.blink_animation = None  # Référence à l'animation de clignotemen

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
            text="Tempo: 120", 
            size_hint_x=None, 
            width=80,
            size_hint_y=None,
            height=common_height,
            halign='left', 
            valign='middle',
            text_size=(80, None)
        )
        combined_layout.add_widget(self.tempo_label)

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

        self.start_pos_input = TextInput(
            text='1:1', 
            multiline=False, 
            size_hint_x=None, 
            width=55,
            size_hint_y=None,
            height=common_height
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

        self.end_pos_input = TextInput(
            text='', 
            multiline=False, 
            size_hint_x=None, 
            width=55,
            size_hint_y=None,
            height=common_height
        )
        self.end_pos_input.bind(on_text_validate=self.on_end_pos_manual_set)
        combined_layout.add_widget(self.end_pos_input)

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

    def menu_action(self, action_callback):
        action_callback()
        self.file_menu.dismiss()
        
    def on_end_pos_manual_set(self, instance):
        if instance.text:
            self.end_pos_manual_override = True

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
        # Optionnel : désactiver aussi le loop au stop si vous le souhaitez
        # self.is_looping = False
        # self.loop_button.icon = 'repeat'
        # self.loop_button.md_bg_color = [0.1, 0.1, 0.1, 1]
        
        self.play_button.icon = 'play'
        self.stop_play_blink()
        self.pause_button.icon = 'pause'
        self.pause_button.md_bg_color = [0.1, 0.1, 0.1, 1]
        self.record_button.icon = 'record'
        self.record_button.md_bg_color = [0.1, 0.1, 0.1, 1]
        self.process_command_ui('stop')

    def record_pressed(self, instance):
        self.is_recording = not self.is_recording
        if self.is_recording:
            self.record_button.icon = 'record-circle-outline'
            self.record_button.md_bg_color = [0.8, 0, 0, 1]
            self.process_command_ui('record')
            # Arrêter le clignotement du play si actif
            if self.is_playing:
                self.is_playing = False
                self.play_button.icon = 'play'
                self.stop_play_blink()
            if self.is_paused:
                self.is_paused = False
                self.pause_button.icon = 'pause'
                self.pause_button.md_bg_color = [0.1, 0.1, 0.1, 1]
        else:
            self.record_button.icon = 'record'
            self.record_button.md_bg_color = [0.1, 0.1, 0.1, 1]
            self.process_command_ui('stop')

    def loop_pressed(self, instance):
        self.is_looping = not self.is_looping
        if self.is_looping:
            self.loop_button.icon = 'repeat-variant'
            self.loop_button.md_bg_color = [0, 0.4, 0.8, 1]
            start_pos = self.start_pos_input.text
            end_pos = self.end_pos_input.text
            if end_pos:
                self.end_pos_manual_override = True
            # Utiliser la nouvelle commande setloop qui ne démarre pas la lecture
            command = f'setloop "{start_pos}" "{end_pos}"'
            self.process_command_ui(command)
            # Ne pas démarrer la lecture automatiquement
        else:
            self.loop_button.icon = 'repeat'
            self.loop_button.md_bg_color = [0.1, 0.1, 0.1, 1]
            # Utiliser simplement 'loop off' sans paramètres
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

    def update_playhead_display(self, instance, value):
        self.playhead_label.text = f"Pos: {self.sequencer._format_beats_to_position(value)}"

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
        self.tempo_label.text = f"Tempo: {song.tempo}"
        self.timesig_label.text = f"TS: {song.time_signature_numerator}/{song.time_signature_denominator}"
        self.metronome_button.icon = 'metronome-tick' if song.metronome_enabled else 'metronome'
        self.metronome_button.md_bg_color = [0.5, 0.5, 0.5, 1] if song.metronome_enabled else [0.1, 0.1, 0.1, 1]
        if not self.end_pos_manual_override:
            end_of_song_beats = self.sequencer.get_song_length_in_beats()
            self.end_pos_input.text = self.sequencer._format_beats_to_position(end_of_song_beats)
        self.update_track_list()

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
            width=dp(300),
            spacing=dp(15),
            size_hint_y=None, 
            height=dp(50),
            pos_hint={'center_y': 0.5}
        )

        if isinstance(track, MidiTrack):
            # Channel - layout vertical
            channel_container = BoxLayout(
                size_hint_x=None,
                width=dp(80),
                orientation='vertical',
                spacing=dp(2)
            )
            channel_label = Label(
                text='Channel', 
                size_hint_y=None,
                height=dp(16),
                halign='right',
                text_size=(dp(90), None),  # FORCE le centrage en définissant text_size
                color=[0.8, 0.8, 0.8, 1],
                font_size=dp(11),
            )
            channel_container.add_widget(channel_label)
            
            channel_spinner = ValueSpinner(
                min_val=1,
                max_val=16,
                initial_value=track.channel + 1,
                callback=self.on_channel_change
            )
            channel_container.add_widget(channel_spinner)
            midi_controls_layout.add_widget(channel_container)

            midi_controls_layout.add_widget(Widget(size_hint_x=None, width=dp(20)))

            # Program - layout vertical
            program_container = BoxLayout(
                size_hint_x=None,
                width=dp(80),
                orientation='vertical',
                spacing=dp(2)
            )
            program_label = Label(
                text='Program', 
                size_hint_y=None,
                height=dp(16),
                halign='right',
                text_size=(dp(90), None),  # FORCE le centrage en définissant text_size
                color=[0.8, 0.8, 0.8, 1],
                font_size=dp(11)
            )
            program_container.add_widget(program_label)
            
            program_spinner = ValueSpinner(
                min_val=1,
                max_val=128,
                initial_value=track.instrument + 1,
                callback=self.on_program_change
            )
            program_container.add_widget(program_spinner)
            midi_controls_layout.add_widget(program_container)
            
            # Espace restant
            midi_controls_layout.add_widget(Widget(size_hint_x=1))
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

    def on_channel_change(self, instance):
        self.sequencer_layout.process_slider_command(f'setch {self.track_index} {instance.text}')

    def on_program_change(self, instance):
        self.sequencer_layout.process_slider_command(f'setprog {self.track_index} {instance.text}')

    def on_set_as_metronome(self, instance):
        self.sequencer_layout.process_command_ui(f'setmetrotrack {self.track_index}')


if __name__ == '__main__':
    SequencerApp().run()