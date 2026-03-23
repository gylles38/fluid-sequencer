from .floating_window import FloatingWindow
from kivy.lang import Builder
from kivy.uix.relativelayout import RelativeLayout
from kivy.properties import ObjectProperty, NumericProperty, StringProperty, BooleanProperty, ListProperty
from . import TooltipMDIconButton, Ruler, AutomationControls
from .ui_utils import is_any_text_input_focused
from sequencer.models import AutomationTrack, MidiTrack
import copy
from kivy.core.window import Window
from kivy.clock import Clock
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.divider import MDDivider
from kivy.uix.widget import Widget
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.boxlayout import BoxLayout
from .HoverBehavior import HoverableButton
from kivy.graphics import Color, Line, Rectangle, Mesh, Translate, PushMatrix, PopMatrix
from kivy.metrics import dp
import math
from collections import deque
from kivy.uix.dropdown import DropDown
from kivy.uix.button import Button
from .SaveDiscardCancelPopup import SaveDiscardCancelPopup


# --- Easing Functions (copied from AutomationCurve.py) ---
def _interp_linear(t):
    return t

def _interp_ease_in_quad(t):
    return t * t

def _interp_ease_out_quad(t):
    return t * (2 - t)

def _interp_ease_in_out_quad(t):
    t *= 2
    if t < 1:
        return 0.5 * t * t
    t -= 1
    return -0.5 * (t * (t - 2) - 1)

def _interp_sine(t, frequency=1.0):
    # frequency=1.0 fait un demi-cycle (oscillation simple)
    # frequency=2.0 fait un cycle complet, etc.
    return 0.5 * (1 - math.cos(t * math.pi * frequency))

def _get_interp_func(curve_type):
    if curve_type == "linear": return _interp_linear
    elif curve_type == "ease-in": return _interp_ease_in_quad
    elif curve_type == "ease-out": return _interp_ease_out_quad
    elif curve_type == "ease-in-out": return _interp_ease_in_out_quad
    elif curve_type == "sine": return _interp_sine
    else: return None


class EditHistoryManager:
    """Manages undo/redo history using a single list and an index."""
    def __init__(self, max_history=31):
        self.history = deque(maxlen=max_history)
        self.index = -1

    def record_state(self, state):
        if self.index < len(self.history) - 1:
            self.history = deque(list(self.history)[:self.index + 1], maxlen=self.history.maxlen)
        self.history.append(state)
        self.index = len(self.history) - 1

    def undo(self):
        if self.can_undo():
            self.index -= 1
            return self.history[self.index]
        return None

    def redo(self):
        if self.can_redo():
            self.index += 1
            return self.history[self.index]
        return None

    def can_undo(self) -> bool:
        return self.index > 0

    def can_redo(self) -> bool:
        return self.index < len(self.history) - 1


class AutomationValueAxis(Widget):
    min_val = NumericProperty(0.0)
    max_val = NumericProperty(1.0)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bind(pos=self.draw, size=self.draw, min_val=self.draw, max_val=self.draw)
        self.labels = []

    def draw(self, *args):
        self.canvas.clear()
        self.clear_widgets()
        self.labels.clear()

        with self.canvas:
            Color(0.2, 0.2, 0.2, 1)
            Rectangle(pos=self.pos, size=self.size)
            Color(0.4, 0.4, 0.4, 1)
            Line(points=[self.right, self.y, self.right, self.top], width=1)

        # Draw labels based on the range
        v_range = self.max_val - self.min_val
        if v_range == 0: return

        def add_label(value, y_align, text=None):
            if text is None: text = f"{value:.1f}"
            y_pos = self.y + ((value - self.min_val) / v_range) * self.height

            if y_align == 'bottom':
                y_pos = self.y
            elif y_align == 'top':
                y_pos = self.top - dp(16)
            else: # Center
                y_pos -= dp(8)

            label = Label(
                text=text,
                font_size='10sp',
                pos=(self.x, y_pos),
                size=(self.width - dp(4), dp(16)),
                halign='right',
                valign='middle',
                color=(0.8, 0.8, 0.8, 1)
            )
            self.labels.append(label)
            self.add_widget(label)

        add_label(self.max_val, y_align='top')
        add_label(self.min_val, y_align='bottom')
        if self.min_val < 0 < self.max_val:
            add_label(0.0, y_align='center')


class EditableAutomationGrid(RelativeLayout):
    editor = ObjectProperty()
    points = ListProperty([])
    pixels_per_beat = NumericProperty(dp(100))
    total_beats = NumericProperty(128.0)
    min_val = NumericProperty(0.0)
    max_val = NumericProperty(1.0)
    beats_per_measure = NumericProperty(4)
    _dragged_point = ObjectProperty(None, allownone=True)
    _drag_offset = (0, 0)
    selected_point = ObjectProperty(None, allownone=True)    

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.grid_widget = Widget(size_hint=(1, 1), pos=(0, 0))
        self.curve_widget = Widget(size_hint=(1, 1), pos=(0, 0))
        self.add_widget(self.grid_widget)
        self.add_widget(self.curve_widget)

        self.bind(size=self._update_layout, points=self.draw,
                  pixels_per_beat=self.draw, total_beats=self.draw,
                  min_val=self.draw, max_val=self.draw)

    def _update_layout(self, *args):
        self.grid_widget.size = self.size
        self.grid_widget.pos = (0, 0)
        self.curve_widget.size = self.size
        self.curve_widget.pos = (0, 0)
        self.draw()

    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos):
            return super().on_touch_down(touch)

        local_pos = self.to_local(*touch.pos)
        # RELATIVELAYOUT: local_pos is already relative to (0,0)
        clicked_beat = local_pos[0] / self.pixels_per_beat

        v_range = self.max_val - self.min_val
        if v_range == 0: v_range = 1
        
        # Valeur cliquée relative à la hauteur du widget
        clicked_value = self.min_val + (local_pos[1] / self.height) * v_range

        edit_mode = self.editor.edit_mode
        
        # --- Recherche du point cliqué (Précision accrue) ---
        clicked_point = None
        # On définit une zone de clic confortable (environ 20-25 pixels)
        click_threshold = dp(12) 

        for point in self.points:
            # On calcule la position locale du point
            point_x = point.start_time * self.pixels_per_beat
            point_y = ((point.value - self.min_val) / v_range) * self.height
            
            # On compare avec local_pos
            if abs(local_pos[0] - point_x) < click_threshold and abs(local_pos[1] - point_y) < click_threshold:
                clicked_point = point
                break

        self.selected_point = clicked_point
        # Force le rafraîchissement pour l'orange
        self.draw_curve_and_points()
        # Mettre à jour la barre de statut via l'éditeur
        self.editor.update_status_bar(clicked_point)
        
        if touch.button == 'right':
            if clicked_point:
                self.editor.show_curve_type_popup(clicked_point, touch)
            return True
        
        if edit_mode == 'insert':
            quantized_beat = round(clicked_beat * 4) / 4 # Snap to 16th
            self.editor.add_point(quantized_beat, clicked_value)
            return True

        elif edit_mode == 'delete':
            if clicked_point:
                self.editor.delete_point(clicked_point)
            return True

        elif edit_mode == 'move':
            if clicked_point:
                # On synchronise les deux variables de sélection
                self.editor.selected_point = clicked_point
                self.selected_point = clicked_point 
                
                self._dragged_point = clicked_point
                # Offset par rapport à la position réelle du point (en coordonnées locales)
                self._drag_offset = (local_pos[0] - (clicked_point.start_time * self.pixels_per_beat)), \
                                    (local_pos[1] - (((clicked_point.value - self.min_val) / v_range) * self.height))
                touch.grab(self)

        return super().on_touch_down(touch)

    def on_touch_move(self, touch):
        if touch.grab_current is not self:
            return super().on_touch_move(touch)

        if self._dragged_point:
            local_pos = self.to_local(*touch.pos)

            # --- Time (X-axis) Calculation ---
            new_x = local_pos[0] - self._drag_offset[0]
            # RELATIVELAYOUT: local_pos is already relative to (0,0)
            new_beat = new_x / self.pixels_per_beat
            quantized_beat = round(new_beat * 4) / 4 # Snap to 16th
            self._dragged_point.start_time = max(0, quantized_beat)

            # --- Value (Y-axis) Calculation ---
            v_range = self.max_val - self.min_val
            if v_range == 0: v_range = 1
            new_y = local_pos[1] - self._drag_offset[1]
            new_value_normalized = new_y / self.height
            new_value = self.min_val + new_value_normalized * v_range
            self._dragged_point.value = max(self.min_val, min(self.max_val, new_value))

            self.editor.is_dirty = True
            self.draw_curve_and_points()
            
            # Mise à jour de la barre de statut pendant le drag
            self.editor.update_status_bar(self._dragged_point)
                    
            return True

        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        if touch.grab_current is not self:
            return super().on_touch_up(touch)

        if self._dragged_point:
            self._dragged_point = None
            touch.ungrab(self)
            self.editor._record_state()
            return True

        return super().on_touch_up(touch)


    def draw(self, *args):
        self.grid_widget.canvas.clear()
        with self.grid_widget.canvas:
            # Background
            Color(0.1, 0.1, 0.1, 1)
            # RELATIVELAYOUT: Draw relative to (0,0)
            Rectangle(pos=(0, 0), size=self.size)

            # --- Grid Lines ---
            # Vertical lines (beats)
            Color(0.2, 0.2, 0.2, 1)
            for i in range(int(self.total_beats) + 1):
                x = i * self.pixels_per_beat
                if x > self.width: break
                is_measure = i % self.beats_per_measure == 0
                Line(points=[x, 0, x, self.height], width=1.5 if is_measure else 0.5)

            # Horizontal lines (values)
            num_h_lines = 10
            for i in range(num_h_lines + 1):
                y = (i / num_h_lines) * self.height
                Line(points=[0, y, self.width, y], width=0.5)

        self.draw_curve_and_points()

    def draw_curve_and_points(self, *args):
        # 1. On efface le calque de dessin (courbes + points carrés)
        self.curve_widget.canvas.clear()
        
        # 2. Si la liste est vide, on s'arrête là
        if not self.points: 
            return

        v_range = self.max_val - self.min_val
        if v_range == 0: v_range = 1

        def normalize(val):
            return (val - self.min_val) / v_range

        sorted_points = sorted(self.points, key=lambda p: p.start_time)

        with self.curve_widget.canvas:
            # --- Draw Curve ---
            Color(0.5, 0.5, 0.8, 0.4)
            vertices, indices, v_index = [], [], 0

            first_p = sorted_points[0]
            first_x = first_p.start_time * self.pixels_per_beat
            y_first = (normalize(first_p.value) * self.height)
            if first_x > 0:
                vertices.extend([0, 0, 0, 0, 0, y_first, 0, 0])
                indices.extend([v_index, v_index + 1])
                v_index += 2
                vertices.extend([first_x, 0, 0, 0, first_x, y_first, 0, 0])
                indices.extend([v_index, v_index + 1])
                v_index += 2

            for i in range(len(sorted_points)):
                p1 = sorted_points[i]
                x1 = p1.start_time * self.pixels_per_beat
                y1 = (normalize(p1.value) * self.height)

                vertices.extend([x1, 0, 0, 0, x1, y1, 0, 0])
                indices.extend([v_index, v_index + 1])
                v_index += 2

                if i < len(sorted_points) - 1:
                    p2 = sorted_points[i+1]
                    x2 = p2.start_time * self.pixels_per_beat
                    y2 = (normalize(p2.value) * self.height) # Position Y du point suivant

                    # --- LOGIQUE EN ESCALIER POUR PROGRAM CHANGE ---
                    if self.editor.selected_parameter == "prog":
                        # On crée un point intermédiaire à la même hauteur que p1, mais au temps de p2
                        # Cela crée la ligne horizontale de l'escalier
                        vertices.extend([x2, 0, 0, 0, x2, y1, 0, 0])
                        indices.extend([v_index, v_index + 1])
                        v_index += 2
                    
                    if p1.curve != "none":
                        # On récupère la fonction d'interpolation
                        interp_func = _get_interp_func(p1.curve)
                        
                        # On récupère la valeur spécifique (ex: 1.0 par défaut)
                        c_val = getattr(p1, 'curve_value', 1.0)
                        
                        num_steps = max(2, min(100, int((x2 - x1) / 5)))
                        for step in range(1, num_steps):
                            t = step / num_steps
                            curr_x = x1 + t * (x2 - x1)
                            
                            # MODIFICATION ICI : on passe c_val si c'est une sine
                            if p1.curve == "sine":
                                ratio = _interp_sine(t, c_val)
                            else:
                                ratio = interp_func(t)
                                
                            real_val = p1.value + ratio * (p2.value - p1.value)
                            curr_y = (normalize(real_val) * self.height)
                            vertices.extend([curr_x, 0, 0, 0, curr_x, curr_y, 0, 0])
                            indices.extend([v_index, v_index + 1])
                            v_index += 2

            last_p = sorted_points[-1]
            last_x = last_p.start_time * self.pixels_per_beat
            final_x = self.total_beats * self.pixels_per_beat
            if last_x < final_x:
                y_last = (normalize(last_p.value) * self.height)
                vertices.extend([final_x, 0, 0, 0, final_x, y_last, 0, 0])
                indices.extend([v_index, v_index + 1])

            Mesh(vertices=vertices, indices=indices, mode='triangle_strip')

            # --- Draw Lines & Points ---
            point_radius = dp(4)
            selected_radius = dp(7) # Plus grand pour faciliter la saisie visuelle

            # 1. Dessiner d'abord toutes les lignes de liaison
            Color(0.8, 0.8, 1, 0.9)
            if first_x > 0:
                Line(points=[0, y_first, first_x, y_first], width=1.2)

            for i in range(len(sorted_points) - 1):
                p1, p2 = sorted_points[i], sorted_points[i+1]
                x1 = p1.start_time * self.pixels_per_beat
                y1 = normalize(p1.value) * self.height
                x2 = p2.start_time * self.pixels_per_beat
                y2 = normalize(p2.value) * self.height
                Line(points=[x1, y1, x2, y2], width=1.2)

            if last_x < final_x:
                y_last = (normalize(last_p.value) * self.height)
                Line(points=[last_x, y_last, final_x, y_last], width=1.2)

            # 2. Dessiner les points normaux (on saute le sélectionné)
            for p in sorted_points:
                if p == self.selected_point: continue
                x = p.start_time * self.pixels_per_beat
                y = normalize(p.value) * self.height
                Color(0.8, 0.8, 1, 0.9)
                Rectangle(pos=(x - point_radius, y - point_radius), size=(point_radius * 2, point_radius * 2))

            # 3. Dessiner le point sélectionné en DERNIER (Orange et par-dessus)
            if self.selected_point:
                p = self.selected_point
                x = p.start_time * self.pixels_per_beat
                y = normalize(p.value) * self.height
                Color(1, 0.6, 0, 1) # Orange vif
                Rectangle(pos=(x - selected_radius, y - selected_radius), size=(selected_radius * 2, selected_radius * 2))

Builder.load_string("""
<AutomationEditor>:
    size_hint: 0.9, 0.9

    MDBoxLayout:
        orientation: 'vertical'

        # Top Toolbar
        MDBoxLayout:
            id: toolbar
            size_hint_y: None
            height: dp(56)
            padding: dp(8)
            spacing: dp(8)
            md_bg_color: 0.2, 0.2, 0.2, 1

            AutomationControls:
                id: automation_controls
                size_hint_x: None
                width: dp(200) # Adjust as needed
                pos_hint: {'center_y': 0.5}

            MDDivider:
                orientation: 'vertical'

            Label:
                text: "Modes:"
                size_hint_x: None
                width: self.texture_size[0]

            TooltipMDIconButton:
                id: insert_button
                icon: 'pencil'
                tooltip_text: "Insert Mode (Ctrl+I)"
                theme_icon_color: "Custom"
                on_press: root.set_edit_mode('insert', self)
            TooltipMDIconButton:
                id: move_button
                icon: 'cursor-move'
                tooltip_text: "Move Mode (Ctrl+M)"
                theme_icon_color: "Custom"
                on_press: root.set_edit_mode('move', self)
            TooltipMDIconButton:
                id: delete_button
                icon: 'eraser'
                tooltip_text: "Delete Mode (Ctrl+D)"
                theme_icon_color: "Custom"
                on_press: root.set_edit_mode('delete', self)
            TooltipMDIconButton:
                id: clear_button
                icon: 'trash-can-outline'
                tooltip_text: "Delete Automation Points (Ctrl+E)"
                theme_icon_color: "Custom"
                icon_color: [1, 1, 1, 0.5]
                on_release: root.clear_all_points()

            MDDivider:
                orientation: 'vertical'

            TooltipMDIconButton:
                id: undo_button
                icon: 'undo'
                tooltip_text: "Undo (Ctrl+Z)"
                on_press: root.undo()
                disabled: True
            TooltipMDIconButton:
                id: redo_button
                icon: 'redo'
                tooltip_text: "Redo (Ctrl+Y)"
                on_press: root.redo()
                disabled: True

            Widget:
                size_hint_x: 1

            MDBoxLayout:
                adaptive_width: True
                spacing: dp(4)

                TooltipMDIconButton:
                    icon: "magnify-plus-outline"
                    tooltip_text: "Zoom In"
                    on_release: root.zoom_in()

                TooltipMDIconButton:
                    icon: "magnify-minus-outline"
                    tooltip_text: "Zoom Out"
                    on_release: root.zoom_out()

                TooltipMDIconButton:
                    icon: "magnify-close"
                    tooltip_text: "Reset Zoom"
                    on_release: root.zoom_reset()

            MDDivider:
                orientation: "vertical"

            TooltipMDIconButton:
                id: rewind_button
                icon: 'skip-backward'
                tooltip_text: "Rewind to Start"
                on_press: root.rewind_pressed()
            TooltipMDIconButton:
                id: play_button
                icon: 'play'
                tooltip_text: "Play"
                on_press: root.play_pressed()
            TooltipMDIconButton:
                id: pause_button
                icon: 'pause'
                tooltip_text: "Pause / Resume"
                on_press: root.pause_pressed()
            TooltipMDIconButton:
                id: stop_button
                icon: 'stop'
                tooltip_text: "Stop"
                on_press: root.stop_pressed()

            Widget:
                size_hint_x: 0.5

            Label:
                text: "Pos:"
                size_hint_x: None
                width: self.texture_size[0]

            TextInput:
                id: pos_label
                text: "1:1"
                size_hint_x: None
                size_hint_y: None
                height: dp(30)
                pos_hint: {"center_y": .5}                
                width: dp(70)
                multiline: False
                on_text_validate: root.seek_from_input(self.text)

        # Ruler
        Ruler:
            id: ruler
            size_hint_y: None
            height: dp(30)
            sequencer_layout: root.sequencer_layout
            pixels_per_beat: root.pixels_per_beat
            total_beats: root.total_beats
            end_pos_str: root.end_pos_str
            beats_per_measure: root.sequencer_layout.sequencer.song.time_signature_numerator
            info_width: dp(60) 
            controls_width: 0
            keyboard_width: 0
            spacing: 0
            padding: [0, 0, 0, 0]

        # Main Content Area (Grid + Value Axis)
        BoxLayout:
            id: main_content
            orientation: 'horizontal'

            AutomationValueAxis:
                id: value_axis
                size_hint_x: None
                width: dp(60)
                min_val: root.min_val
                max_val: root.max_val

            ScrollView:
                id: timeline_scroll
                size_hint: (1, 1)
                do_scroll_x: True
                do_scroll_y: False
                bar_width: dp(15)
                scroll_type: ['bars', 'content']
                bar_pos_x: 'bottom'
                bar_margin: dp(2)

                # L'ENFANT UNIQUE DU SCROLLVIEW
                FloatLayout:
                    id: scroll_content
                    size_hint: None, 1
                    width: grid.width

                    # 1. Le contenu principal (Grille + Sécurité)
                    BoxLayout:
                        orientation: 'vertical'
                        size_hint: (1, 1)
                        padding: [0, 0, 0, dp(15)]
                        
                        EditableAutomationGrid:
                            id: grid
                            editor: root
                            size_hint: None, 1
                            width: root.total_beats * root.pixels_per_beat
                            points: root.visible_points
                            total_beats: root.total_beats
                            pixels_per_beat: root.pixels_per_beat
                            min_val: root.min_val
                            max_val: root.max_val
                            beats_per_measure: root.sequencer_layout.sequencer.song.time_signature_numerator

                        Widget:
                            size_hint_y: None
                            height: dp(18)

                    # 2. La Playhead (Superposée grâce au FloatLayout)
                    Widget:
                        id: playhead
                        size_hint: None, 1
                        width: dp(2)
                        x: 0
                        canvas:
                            Color:
                                rgba: 1, 0, 0, 0.8
                            Rectangle:
                                pos: self.pos
                                size: self.size

        # Bottom Toolbar
        MDBoxLayout:
            size_hint_y: None
            height: dp(48)
            padding: dp(8)
            spacing: dp(8)
            md_bg_color: 0.2, 0.2, 0.2, 1

            MDBoxLayout:
                id: edit_zone
                adaptive_width: True
                spacing: dp(10)
                opacity: 0 # Caché par défaut si rien n'est sélectionné

                MDLabel:
                    text: "Beat:"
                    adaptive_width: True
                TextInput:
                    id: input_beat
                    size_hint: None, None
                    size: dp(60), dp(30)
                    multiline: False
                    on_text_validate: root.apply_manual_edit()

                MDLabel:
                    text: "Value:"
                    adaptive_width: True
                TextInput:
                    id: input_value
                    size_hint: None, None
                    size: dp(80), dp(30)
                    multiline: False
                    on_text_validate: root.apply_manual_edit()

                MDBoxLayout:
                    id: sine_zone
                    adaptive_width: True
                    spacing: dp(5)
                    opacity: 0  # Caché par défaut
                    disabled: True

                    MDLabel:
                        text: "Sine Ph:"
                        adaptive_width: True
                  
                    MDIconButton:
                        icon: "minus"
                        user_font_size: "16sp"
                        on_release: root.adjust_sine_value(-0.5)
                                
                    TextInput:
                        id: input_sine
                        size_hint: None, None
                        size: dp(60), dp(30)
                        multiline: False
                        on_text_validate: root.apply_manual_edit()

                    MDIconButton:
                        icon: "plus"
                        user_font_size: "16sp"
                        on_release: root.adjust_sine_value(0.5)

            Widget:
                size_hint_x: 1

            HoverableButton:
                text: 'Save & Close'
                size_hint_x: None
                width: dp(120)
                on_press: root.dismiss('save_and_close')
            HoverableButton:
                text: 'Discard & Close'
                size_hint_x: None
                width: dp(140)
                on_press: root.dismiss('discard_and_close')
            HoverableButton:
                text: 'Cancel'
                size_hint_x: None
                width: dp(100)
                on_press: root.dismiss()
""")

class AutomationEditor(FloatingWindow):
    min_width = NumericProperty(dp(750))
    sequencer_layout = ObjectProperty()
    track = ObjectProperty() # This will be the AutomationTrack
    original_track_index = NumericProperty(None)
    track_copy = ObjectProperty()

    pixels_per_beat = NumericProperty(dp(100))
    total_beats = NumericProperty(128)
    end_pos_str = StringProperty('')

    edit_mode = StringProperty('insert')
    is_dirty = BooleanProperty(False)
    _is_scrolling = False

    # Properties for managing visible data
    visible_points = ListProperty([])
    selected_parameter = StringProperty('vol')
    selected_point = ObjectProperty(None, allownone=True)
    min_val = NumericProperty(0.0)
    max_val = NumericProperty(1.0)
    history = ObjectProperty(None)


    def __init__(self, initial_param='vol', **kwargs):
        self.history = EditHistoryManager()
        # On extrait track et sequencer_layout de kwargs avant le super s'ils y sont
        # ou on s'assure qu'ils sont passés par propriétés.        
        super(AutomationEditor, self).__init__(**kwargs)
        self.source_track = self.track
        self.title = f"Automation: {self.track.name}"

        self.track_copy = AutomationTrack(
            name=self.track.name,
            target_track_index=self.track.target_track_index,
            points=copy.deepcopy(self.track.points)
        )
        self.total_beats = self.sequencer_layout.sequencer.get_song_length_in_beats()

        # Bind end_pos_str to sequencer
        self.end_pos_str = self.sequencer_layout.sequencer.ui_end_pos_str
        self.sequencer_layout.sequencer.bind(ui_end_pos_str=self.setter('end_pos_str'))

        # On stocke le paramètre souhaité
        self.selected_parameter = initial_param
        
        self.sequencer_layout.sequencer.bind(playback_state=self.on_playback_state_change)
        self.sequencer_layout.sequencer.bind(song_structure_changed=self.on_song_structure_changed)

        Clock.schedule_once(self._post_kv_init)
        Window.bind(on_key_down=self._on_key_down)

    def _post_kv_init(self, dt):
        """Final UI setup after the kv string is loaded."""
        target_track = self.sequencer_layout.sequencer.song.tracks[self.track.target_track_index]
        track_type = 'midi' if isinstance(target_track, MidiTrack) else 'audio'

        automation_controls = self.ids.automation_controls
        automation_controls.track_type = track_type
        automation_controls.bind(on_selection_change=self.on_automation_selection_change)
        
        # ASTUCE : On réinitialise proprement pour forcer le rafraîchissement visuel
        automation_controls.selected_param = None        

        # On utilise Clock pour être sûr que le canevas et les boutons sont prêts
        Clock.schedule_once(lambda dt: self._force_initial_selection(automation_controls, self.selected_parameter), 0)
        
        self.mode_buttons = {
            'insert': self.ids.insert_button, 'move': self.ids.move_button, 'delete': self.ids.delete_button
        }
        self.set_edit_mode(self.edit_mode, self.mode_buttons[self.edit_mode])

        # Record initial state for undo
        self._record_state()
        self._update_undo_redo_buttons_state()

        # Scroll synchronization
        ruler_scroll = self.ids.ruler.scroll_view
        timeline_scroll = self.ids.timeline_scroll
        ruler_scroll.bind(scroll_x=self.sync_horizontal_scroll)
        timeline_scroll.bind(scroll_x=self.sync_horizontal_scroll)

    def _force_initial_selection(self, controls, param_name):
        # On sélectionne le paramètre passé en argument (au lieu de 'vol' en dur)
        controls.select_param(param_name)
        # On force l'appel de mise à jour de la courbe pour ce paramètre
        self.on_automation_selection_change(controls, param_name)

    def on_open(self):
        if not self.track:
            return

        # On récupère la durée totale du PROJET (song) et non de la piste
        # Cela garantit que la grille d'automation va jusqu'au bout du morceau
        sequencer = self.sequencer_layout.sequencer
        # On force la récupération de la durée maximale du projet
        # get_song_length_in_beats() est généralement plus fiable que song.duration
        total_beats = sequencer.get_song_length_in_beats()
        
        if total_beats <= 0:
            total_beats = 128.0 # Valeur par défaut de sécurité

        self.total_beats = total_beats
        
        # On met à jour la règle
        ruler = self.ids.ruler
        ruler.total_beats = total_beats
        ruler.pixels_per_beat = self.pixels_per_beat
        
        # Ré-alignement technique de la règle
        ruler.info_width = dp(60)
        ruler.spacing = 0
        ruler.padding = [0, 0, 0, 0]
        
        ruler.redraw()
        self.ids.grid.draw() # Calls draw_curve_and_points

        # Démarrage de la playhead
        if hasattr(self, '_playhead_event'):
            self._playhead_event.cancel()
        self._playhead_event = Clock.schedule_interval(self.update_playhead, 1/60)
        
    def _sync_ruler_scroll(self, instance, value):
        """Répercute le défilement de la grille sur la règle."""
        if hasattr(self.ids.ruler, 'scroll_view'):
            self.ids.ruler.scroll_view.scroll_x = value

    def on_playback_state_change(self, instance, state):
        play_btn = self.ids.get('play_button')
        pause_btn = self.ids.get('pause_button')
        if not play_btn or not pause_btn: return

        if state in ('playing', 'recording'):
            play_btn.icon = 'play-circle-outline'
            play_btn.icon_color = [0, 0.7, 0.3, 1]
            pause_btn.icon = 'pause'
            pause_btn.md_bg_color = [0.1, 0.1, 0.1, 1]
        elif state == 'paused':
            play_btn.icon = 'play'
            play_btn.icon_color = [1, 1, 1, 0.8]
            pause_btn.icon = 'pause-circle-outline'
            pause_btn.md_bg_color = [0.9, 0.7, 0, 1]
        else: # stopped
            play_btn.icon = 'play'
            play_btn.icon_color = [1, 1, 1, 0.8]
            pause_btn.icon = 'pause'
            pause_btn.md_bg_color = [0.1, 0.1, 0.1, 1]

    def on_song_structure_changed(self, instance, value):
        if self.sequencer_layout.sequencer.playback_state == 'recording':
            # Check if points changed to avoid unnecessary heavy copying
            if len(self.track_copy.points) != len(self.source_track.points):
                # Sync track_copy with the actual track points for the current parameter
                self.track_copy.points = copy.deepcopy(self.source_track.points)
                self.visible_points = [p for p in self.track_copy.points if p.parameter == self.selected_parameter]

    def update_status_bar(self, point):
        if point:
            self.ids.edit_zone.opacity = 1
            self.ids.input_beat.text = f"{point.start_time:.2f}"
            
            # Valeur principale
            if self.selected_parameter in ["prog", "vel"] or self.selected_parameter.startswith("cc"):
                self.ids.input_value.text = f"{int(point.value)}"
            else:
                self.ids.input_value.text = f"{point.value:.3f}"

            # --- Gestion spécifique à la courbe Sine ---
            if point.curve == "sine":
                self.ids.sine_zone.opacity = 1
                self.ids.sine_zone.disabled = False
                # On affiche la valeur de courbure actuelle (souvent point.curve_value)
                self.ids.input_sine.text = f"{getattr(point, 'curve_value', 1.0):.2f}"
            else:
                self.ids.sine_zone.opacity = 0
                self.ids.sine_zone.disabled = True
        else:
            self.ids.edit_zone.opacity = 0

    def update_playhead(self, dt):
        if 'playhead' not in self.ids:
            return

        sequencer = self.sequencer_layout.sequencer
        current_beat = sequencer.current_beat 
        current_state = sequencer.playback_state

        # --- RESET AU STOP ---
        if current_state == "stopped" and getattr(self, 'last_playback_state', 'stopped') != "stopped":
            self.ids.ruler.g_translate.x = 0
            self.ids.grid.g_translate = Translate(0, 0, 0) # Fallback optimization
            self.scroll_to_beat(current_beat)

        self.last_playback_state = current_state
        
        # Déplacement de la barre rouge
        self.ids.playhead.x = current_beat * self.pixels_per_beat
        
        # Mise à jour du texte M:B
        if not self.ids.pos_label.focus:
            # On utilise le formateur officiel du séquenceur pour éviter les erreurs de calcul
            self.ids.pos_label.text = sequencer._format_beats_to_position(current_beat)
        
        # Auto-scroll uniquement en lecture
        if sequencer.playback_state == 'playing':
            self._scroll_to_logic(current_beat)

    def scroll_to_beat(self, beat):
        """Défile la timeline pour afficher le beat spécifié."""
        scroll_view = self.ids.timeline_scroll
        grid_width = self.total_beats * self.pixels_per_beat
        viewport_width = scroll_view.width

        if grid_width <= viewport_width:
            scroll_view.scroll_x = 0
            return

        target_pixel = beat * self.pixels_per_beat
        max_scroll = grid_width - viewport_width
        new_scroll_x = target_pixel / max_scroll

        scroll_view.scroll_x = max(0, min(1, new_scroll_x))

    def _scroll_to_logic(self, current_beat):
        scroll_view = self.ids.timeline_scroll
        # grid.width est maintenant égal à song.duration * pixels_per_beat
        total_width = self.ids.grid.width 
        viewport_width = scroll_view.width
        
        max_scroll_dist = total_width - viewport_width
        if max_scroll_dist <= 0:
            return

        playhead_pixel_x = current_beat * self.pixels_per_beat
        trigger_point = viewport_width * 0.5 
        
        if playhead_pixel_x > trigger_point:
            target_view_start = playhead_pixel_x - trigger_point
            new_scroll_x = target_view_start / max_scroll_dist
            scroll_view.scroll_x = max(0, min(1, new_scroll_x))

    def seek_from_input(self, text):
        try:
            seq = self.sequencer_layout.sequencer
            target_beat = seq.parse_position_to_beats(text)
            
            if target_beat is not None:
                target_beat = max(0, min(self.total_beats, target_beat))
                
                # Mise à jour profonde du séquenceur
                seq.current_beat = target_beat
                seq.ui_start_pos_str = seq._format_beats_to_position(target_beat)
                seq._resync_all_at_beat(target_beat)
                
                # IMPORTANT : Si le séquenceur est en pause, on s'assure que 
                # la prochaine lecture partira de cette nouvelle position.
                # Certains séquenceurs utilisent : seq.start_beat = target_beat
                
                self.ids.playhead.x = target_beat * self.pixels_per_beat
                self._scroll_to_logic(target_beat)
                
            self.ids.pos_label.focus = False
        except:
            self.ids.pos_label.focus = False

    def on_dismiss(self):
        """Nettoyage des bindings et de l'horloge à la fermeture de l'éditeur."""
        self.sequencer_layout.sequencer.unbind(playback_state=self.on_playback_state_change)
        self.sequencer_layout.sequencer.unbind(song_structure_changed=self.on_song_structure_changed)

        # 1. On libère le clavier
        Window.unbind(on_key_down=self._on_key_down)
        
        # 2. On arrête la mise à jour de la Playhead
        if hasattr(self, '_playhead_event'):
            self._playhead_event.cancel()
            # Alternativement : Clock.unschedule(self.update_playhead)
            
        super().on_dismiss()

    def adjust_sine_value(self, delta):
        """Ajuste la fréquence du sinus avec les boutons +/-."""
        point = self.ids.grid.selected_point
        if point and point.curve == "sine":
            current_val = getattr(point, 'curve_value', 1.0)
            # Limitation entre 0.5 et 20.0
            new_val = max(0.5, min(20.0, current_val + delta))
            point.curve_value = new_val
            
            # Mise à jour visuelle immédiate
            self.update_status_bar(point)
            self.ids.grid.draw_curve_and_points()
            self.is_dirty = True
            self._record_state()

    def apply_manual_edit(self):
        """Applique les modifications saisies manuellement."""
        if not self.ids.grid.selected_point:
            return

        point = self.ids.grid.selected_point
        try:
            # Récupération et conversion des textes
            new_beat = float(self.ids.input_beat.text)
            new_val = float(self.ids.input_value.text)

            # Application des limites (clamping)
            point.start_time = max(0, min(self.total_beats, new_beat))
            point.value = max(self.min_val, min(self.max_val, new_val))

            # Lecture de la valeur Sine si applicable
            if point.curve == "sine" and self.ids.input_sine.text:
                raw_val = float(self.ids.input_sine.text)
                # Sécurité : on bride entre 0.5 et 20.0
                point.curve_value = max(0.5, min(20.0, raw_val))
                
            # Mise à jour visuelle et historique
            self.ids.grid.draw_curve_and_points()
            self.update_status_bar(point)
            self.is_dirty = True
            self._record_state()
            
            # On retire le focus pour valider visuellement
            self.ids.input_beat.focus = False
            self.ids.input_value.focus = False
            self.ids.input_sine.focus = False
            
        except ValueError:
            # En cas d'erreur de saisie (ex: texte au lieu de chiffre), on réinitialise
            self.update_status_bar(point)

    def on_automation_selection_change(self, instance, param):
        self.selected_parameter = param

        if param in ["prog", "vel"] or param.startswith("cc"):
            self.min_val, self.max_val = 0.0, 127.0
        elif param == "pan":
            self.min_val, self.max_val = -1.0, 1.0
        else: # vol, etc.
            self.min_val, self.max_val = 0.0, 1.0

        # --- FIX : Réinitialiser la sélection visuelle et textuelle ---
        self.ids.grid.selected_point = None
        self.update_status_bar(None)

        self.visible_points = [p for p in self.track_copy.points if p.parameter == param]
        self.ids.grid.points = self.visible_points

    def _on_key_down(self, instance, keyboard, keycode, text, modifiers):
        # --- Sécurité : Désactiver les raccourcis si un champ texte a le focus ---
        if is_any_text_input_focused():
            return False

        sequencer = self.sequencer_layout.sequencer

        # --- SPACE (Play/Pause) ---
        if keyboard == 32:
            self.play_pressed()
            return True
        
        # HOME : Retour au début
        if keyboard == 278:
            self.ids.playhead.x = 0
            self.ids.timeline_scroll.scroll_x = 0
            
            sequencer.ui_start_pos_str = "1:1"
            if hasattr(self.sequencer_layout, 'start_pos_input'):
                self.sequencer_layout.start_pos_input.text = "1:1"
            
            sequencer.current_beat = 0
            sequencer._resync_all_at_beat(0)
            return True

        # END : Aller à la fin (définit la fin du morceau)
        if keyboard == 279:
            ts_num = getattr(sequencer.song, 'time_signature_numerator', 4)
            target_beat = max(0, self.total_beats - ts_num)
            pos_str = sequencer._format_beats_to_position(target_beat)
            
            self.ids.playhead.x = target_beat * self.pixels_per_beat
            self.ids.timeline_scroll.scroll_x = 1.0
            
            # --- FIX: Update End field instead of Start field ---
            sequencer.ui_end_pos_str = pos_str
            if hasattr(self.sequencer_layout, 'end_pos_input'):
                self.sequencer_layout.end_pos_input.text = pos_str
                self.sequencer_layout.end_pos_manual_override = True
            
            sequencer.current_beat = target_beat
            sequencer._resync_all_at_beat(target_beat)
            return True

        if 'ctrl' in modifiers:
            if text == 'z':
                self.undo()
                return True
            elif text == 'y':
                self.redo()
                return True
            elif text == 'i':
                self.set_edit_mode('insert', self.mode_buttons['insert'])
                return True
            elif text == 'd':
                self.set_edit_mode('delete', self.mode_buttons['delete'])
                return True
            elif text == 'm':
                self.set_edit_mode('move', self.mode_buttons['move'])
                return True

        if text in ('+', '-'):
            if self.selected_point:
                if self.selected_parameter in ('prog', 'vel'):
                    delta = 10 if text == '+' else -10
                else:
                    delta = 1.0 if text == '+' else -1.0

                new_val = self.selected_point.value + delta

                self.selected_point.value = max(self.min_val, min(self.max_val, new_val))
                self.is_dirty = True
                self.ids.grid.draw_curve_and_points()
                # AJOUT : Mise à jour des champs de texte
                self.update_status_bar(self.selected_point)
                self._record_state()

        return False

    # Placeholder methods for actions
    def set_edit_mode(self, mode, btn):
        self.edit_mode = mode
        self._update_button_states(self.mode_buttons, btn)
        print(f"Edit mode set to: {mode}")
        
    def _update_button_states(self, group, active_button) -> None:
        """Met à jour l'apparence des boutons d'outils selon l'outil sélectionné."""
        orange_vif = [1, 0.6, 0, 1]
        blanc_semi = [1, 1, 1, 0.8]

        for btn in group.values():
            if btn == active_button:
                # On force la couleur orange
                btn.icon_color = orange_vif
                # Optionnel : On peut aussi augmenter l'opacité pour plus de peps
                btn.opacity = 1.0
            else:
                # On remet en blanc semi-transparent
                btn.icon_color = blanc_semi
                btn.opacity = 0.8
                
            btn.canvas.ask_update()                
  
    def undo(self):
        previous_state = self.history.undo()
        if previous_state:
            self._apply_state(previous_state)

    def redo(self):
        next_state = self.history.redo()
        if next_state:
            self._apply_state(next_state)

    def _record_state(self):
        state = [{
            'start_time': p.start_time,
            'value': p.value,
            'curve': p.curve,
            'parameter': p.parameter
        } for p in self.track_copy.points]
        self.history.record_state(state)
        self._update_undo_redo_buttons_state()

    def _apply_state(self, state):
        from sequencer.models import AutomationPoint
        self.track_copy.points = [AutomationPoint(**data) for data in state]
        # Re-filter visible points based on the current parameter
        self.on_automation_selection_change(None, self.selected_parameter)
        self.is_dirty = True
        self._update_undo_redo_buttons_state()

    def _update_undo_redo_buttons_state(self):
        self.ids.undo_button.disabled = not self.history.can_undo()
        self.ids.redo_button.disabled = not self.history.can_redo()

    def add_point(self, beat, value):
        from sequencer.models import AutomationPoint
        new_point = AutomationPoint(
            start_time=beat,
            value=value,
            parameter=self.selected_parameter,
            curve='linear'
        )
        self.track_copy.points.append(new_point)
        self.on_automation_selection_change(None, self.selected_parameter) # Refresh view
        self._record_state()
        self.is_dirty = True

    def delete_point(self, point):
        if point in self.track_copy.points:
            self.track_copy.points.remove(point)
            self.on_automation_selection_change(None, self.selected_parameter)
            self._record_state()
            self.is_dirty = True

    def clear_all_points(self, *args):
        # 1. Filtrage de la source de données (ne supprime que le paramètre actuel)
        # On garde les points qui appartiennent à d'autres types d'automation
        self.track_copy.points = [
            p for p in self.track_copy.points 
            if p.parameter != self.selected_parameter
        ]
        
        # 2. Vider la liste VISIBLE pour ce paramètre
        # Comme on ne visualise qu'un paramètre à la fois dans la grille, 
        # vider cette liste efface la courbe à l'écran.
        self.visible_points = []
        
        self.is_dirty = True
        
        # 3. Réinitialiser les sélections (évite les crashs après suppression)
        self.selected_point = None
        if hasattr(self.ids, 'grid'):
            self.ids.grid.selected_point = None
            
        if hasattr(self, 'update_status_bar'):
            self.update_status_bar(None)

        # 4. Gestion de l'historique (Undo)
        if hasattr(self, 'undo_stack'):
            self.undo_stack.append(copy.deepcopy(self.track_copy.points))

        # 5. Forcer le redessin du widget Grille
        if hasattr(self.ids, 'grid'):
            self.ids.grid.draw_curve_and_points()

    def show_curve_type_popup(self, point, touch):
        if self.selected_parameter == 'prog': 
            return

        # 1. On récupère la position souris AVANT toute chose
        from kivy.core.window import Window
        mouse_x, mouse_y = Window.mouse_pos

        # 2. Création d'une NOUVELLE instance à chaque clic
        dropdown = DropDown()
        dropdown.auto_width = False
        dropdown.width = dp(160)
        
        curve_types = ['none', 'linear', 'ease-in', 'ease-out', 'ease-in-out', 'sine']

        for curve_type in curve_types:
            btn = Button(
                text=curve_type, 
                size_hint=(None, None),
                height=dp(44),
                width=dp(160),
                background_color=[0.15, 0.15, 0.15, 1],
                halign='center'
            )
            btn.text_size = (dp(160), None)
            
            # Utilisation d'un callback qui ferme bien l'instance locale 'dropdown'
            btn.bind(on_release=lambda btn, t=curve_type: self.set_curve_type(point, t, dropdown))
            dropdown.add_widget(btn)

        # 3. Le proxy_widget doit être géré proprement
        proxy_widget = Widget(size_hint=(None, None), size=(1, 1), pos=(mouse_x, mouse_y))
        Window.add_widget(proxy_widget)
        
        # 4. Ouverture
        dropdown.open(proxy_widget)
        
        # NETTOYAGE : On enlève le proxy
        def on_menu_close(instance):
            Window.remove_widget(proxy_widget)
            # SUPPRIMÉ : self.ids.grid.selected_point = None 
            # On ne touche plus à la sélection visuelle ici pour qu'elle persiste
            
        dropdown.bind(on_dismiss=on_menu_close)

    def set_curve_type(self, point, curve_type, dropdown):
        point.curve = curve_type
        dropdown.dismiss()
        
        # On s'assure que la grille sait que ce point est toujours le "héros"
        self.ids.grid.selected_point = point 
        
        # On met à jour l'affichage (Barre de statut + Dessin)
        self.update_status_bar(point)
        self.ids.grid.draw_curve_and_points()
        
        self._record_state()
        self.is_dirty = True

    def zoom_in(self):
        self._apply_zoom(self.pixels_per_beat * 1.25)

    def zoom_out(self):
        # On limite pour éviter que la grille disparaisse (dp(20) est une bonne base)
        new_zoom = max(dp(20), self.pixels_per_beat / 1.25)
        self._apply_zoom(new_zoom)

    def zoom_reset(self):
        self._apply_zoom(dp(100))

    def _apply_zoom(self, new_pixels_per_beat):
        """Applique le zoom et conserve le point central de la vue."""
        scroll_view = self.ids.timeline_scroll
        
        # 1. Calculer quel beat est au centre du ScrollView avant le zoom
        old_total_width = self.total_beats * self.pixels_per_beat
        viewport_width = scroll_view.width
        
        # Position du centre actuel en pixels
        # scroll_x va de 0 à 1, on le multiplie par la plage scrollable
        center_pixel = (scroll_view.scroll_x * (old_total_width - viewport_width)) + (viewport_width / 2)
        center_beat = center_pixel / self.pixels_per_beat

        # 2. Appliquer la nouvelle valeur
        self.pixels_per_beat = new_pixels_per_beat
        
        # 3. Mettre à jour les dimensions de la grille et de la règle immédiatement
        new_width = self.total_beats * self.pixels_per_beat
        self.ids.grid.width = new_width
        self.ids.ruler.ruler_content.width = new_width
        
        # 4. Repositionner le scroll après que Kivy a recalculé le layout
        Clock.schedule_once(lambda dt: self._update_scroll_after_zoom(center_beat), 0)

    def _update_scroll_after_zoom(self, target_beat):
        scroll_view = self.ids.timeline_scroll
        new_total_width = self.total_beats * self.pixels_per_beat
        viewport_width = scroll_view.width
        
        if new_total_width <= viewport_width:
            scroll_view.scroll_x = 0
        else:
            # Calcul du nouveau point d'ancrage en pixels
            new_center_pixel = target_beat * self.pixels_per_beat
            new_scroll_pixels = new_center_pixel - (viewport_width / 2)
            
            # Normalisation vers scroll_x (0.0 à 1.0)
            max_scroll = new_total_width - viewport_width
            scroll_view.scroll_x = max(0, min(1, new_scroll_pixels / max_scroll))
        
        # 5. Forcer le redessin graphique de tous les composants
        self.ids.ruler.redraw()
        self.ids.grid.draw_curve_and_points()

    def sync_horizontal_scroll(self, source_scroll_view, scroll_x_value):
        if self._is_scrolling: return
        self._is_scrolling = True

        try:
            # Calculate absolute pixel offset from source
            content_width_source = source_scroll_view.children[0].width
            viewport_width_source = source_scroll_view.width
            max_scroll_source = max(0, content_width_source - viewport_width_source)
            pixel_offset = scroll_x_value * max_scroll_source if max_scroll_source > 0 else 0

            ruler_scroll = self.ids.ruler.scroll_view
            timeline_scroll = self.ids.timeline_scroll

            targets = [ruler_scroll, timeline_scroll]
            for sv in targets:
                if sv is not source_scroll_view:
                    try:
                        content_width = sv.children[0].width
                        viewport_width = sv.width
                        max_scroll = max(0, content_width - viewport_width)
                        if max_scroll > 0:
                            sv.scroll_x = max(0.0, min(1.0, pixel_offset / max_scroll))
                        else:
                            sv.scroll_x = 0
                    except (IndexError, AttributeError):
                        continue
        except (IndexError, AttributeError):
            pass

        self._is_scrolling = False

    def play_pressed(self, *args) -> None: self.sequencer_layout.sequencer.process_transport_command("play")
    def pause_pressed(self, *args) -> None: self.sequencer_layout.sequencer.process_transport_command("pause")
    def stop_pressed(self, *args) -> None: self.sequencer_layout.sequencer.process_transport_command("stop")

    def rewind_pressed(self, *args):
        self.sequencer_layout.sequencer._resync_all_at_beat(0)

    def dismiss(self, action=None, *args):
        if action == 'save_and_close':
            self._save_changes()
            super().dismiss(*args)
            return
        if action == 'discard_and_close':
            super().dismiss(*args)
            return
        if self.is_dirty:
            SaveDiscardCancelPopup(prompt_text="You have unsaved changes.", callback=self._handle_save_dialog).open()
        else:
            super().dismiss(*args)

    def _handle_save_dialog(self, answer):
        if answer == 's':
            self._save_changes()
            super().dismiss()
        elif answer == 'd':
            super().dismiss()
            
    def _save_changes(self):
        # 1. On applique les changements aux points (qu'ils soient vides ou modifiés)
        self.track.points = copy.deepcopy(self.track_copy.points)
        self.is_dirty = False
        
        # 2. On rafraîchit l'affichage des miniatures (Timeline)
        for tw in self.sequencer_layout.track_widgets:
            if tw.track == self.track:
                for curve in tw.automation_curves:
                    curve.points = [p for p in self.track.points if p.parameter == curve.param_type]
            
            # 3. MISE À JOUR DES VALEURS (Optionnel mais recommandé)
            # Si on vient de sauvegarder, on demande au widget cible de se caler 
            # sur la valeur de l'automation à la position actuelle du curseur.
            if hasattr(tw.track, 'track_index') and tw.track.track_index == self.track.target_track_index:
                tw.update_sliders_from_automation(self.sequencer_layout.sequencer.current_beat)