from kivy.uix.boxlayout import BoxLayout
from kivy.uix.widget import Widget
from kivy.uix.relativelayout import RelativeLayout
from kivy.properties import ObjectProperty, NumericProperty, StringProperty
from kivy.metrics import dp
from kivy.clock import Clock
from kivy.uix.scrollview import ScrollView
from kivy.effects.scroll import ScrollEffect
from kivy.graphics import Color, Rectangle, Line, Mesh, PushMatrix, PopMatrix, Translate
from kivy.core.text import Label as CoreLabel # On utilise CoreLabel pour dessiner sur le canvas

class RulerContent(Widget):
    sequencer_layout = ObjectProperty(None)
    pixels_per_beat = NumericProperty(dp(100))
    total_beats = NumericProperty(16)
    beats_per_measure = NumericProperty(4)
    label_padding_x = NumericProperty(dp(4))

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.size_hint_x = None
        self._redraw_event = None
        self._texture_cache = {}  # CACHE POUR LES NUMÉROS
        
        # --- TRANSLATION GPU ---
        with self.canvas.before:
            PushMatrix()
            self.g_translate = Translate(0, 0, 0)
        with self.canvas.after:
            PopMatrix()

        self.bind(size=self._trigger_redraw, 
                  total_beats=self._update_width,
                  pixels_per_beat=self._update_width)
        self._update_width()

    def get_measure_texture(self, number):
        """ Crée ou récupère la texture du numéro de mesure """
        if number not in self._texture_cache:
            lbl = CoreLabel(text=str(number), font_size=dp(12), color=(0.7, 0.7, 0.7, 1))
            lbl.refresh()
            self._texture_cache[number] = lbl.texture
        return self._texture_cache[number]

    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos):
            return super().on_touch_down(touch)

        if not self.sequencer_layout or not self.sequencer_layout.sequencer:
            return super().on_touch_down(touch)

        local_touch = self.to_local(*touch.pos)
        # Account for GPU translation
        world_x = local_touch[0] - self.g_translate.x

        beat = world_x / self.pixels_per_beat
        if beat < 0: beat = 0

        seq = self.sequencer_layout.sequencer

        if touch.button == 'left':
            pos_str = seq._format_beats_to_position(beat)
            self.sequencer_layout.start_pos_input.text = pos_str
            self.sequencer_layout.on_start_position_validate()
            self.redraw()
            return True
        elif touch.button == 'right':
            pos_str = seq._format_beats_to_position(beat)
            self.sequencer_layout.end_pos_input.text = pos_str
            self.sequencer_layout.on_end_position_validate()
            self.redraw()
            return True

        return super().on_touch_down(touch)

    def _trigger_redraw(self, *args):
        if self._redraw_event:
            self._redraw_event.cancel()
        self._redraw_event = Clock.schedule_once(self.redraw, 0)
        
    def redraw(self, *args):
        """Debounced redraw of the ruler content."""
        Clock.unschedule(self._do_redraw)
        Clock.schedule_once(self._do_redraw, 0)

    def _update_width(self, *args):
        new_width = self.total_beats * self.pixels_per_beat
        if self.width != new_width:
            self.width = new_width

    def _get_viewport(self):
        """Calculates the visible horizontal viewport."""
        viewport_x = 0
        viewport_w = self.width
        parent = self.parent
        while parent:
            if isinstance(parent, ScrollView):
                viewport_x = parent.scroll_x * max(0, self.width - parent.width)
                viewport_w = parent.width
                break
            parent = parent.parent
        return viewport_x, viewport_w

    def _do_redraw(self, dt):
        self.canvas.clear()
        
        c_bg = (0.18, 0.18, 0.18, 1)
        c_measure = (0.8, 0.8, 0.8, 1)
        c_beat = (0.4, 0.4, 0.4, 0.5)
        c_white = (1, 1, 1, 1)
        c_selection = (0.2, 0.6, 0.8, 0.5) # Bleu semi-transparent
        c_selection_range = (0.2, 0.6, 0.8, 0.15)

        # Performance Optimization: Calculate visible viewport to skip rendering non-visible elements.
        viewport_x, viewport_w = self._get_viewport()
        start_beat = max(0, int(viewport_x / self.pixels_per_beat))
        end_beat = min(int(self.total_beats), int((viewport_x + viewport_w) / self.pixels_per_beat) + 1)

        with self.canvas:
            Color(*c_bg)
            # Only draw background for the visible part
            Rectangle(pos=(self.x + viewport_x, self.y), size=(viewport_w, self.height))

            # --- DESSIN DE LA SÉLECTION (PLAGE START/END) ---
            if self.sequencer_layout and self.sequencer_layout.sequencer:
                seq = self.sequencer_layout.sequencer
                start_beat_sel = seq.parse_position_to_beats(seq.ui_start_pos_str)
                end_beat_sel = seq.parse_position_to_beats(seq.ui_end_pos_str) if seq.ui_end_pos_str else None

                if start_beat_sel is not None and end_beat_sel is not None and end_beat_sel > start_beat_sel:
                    # Clip selection range to viewport
                    draw_start = max(start_beat_sel * self.pixels_per_beat, viewport_x)
                    draw_end = min(end_beat_sel * self.pixels_per_beat, viewport_x + viewport_w)
                    if draw_end > draw_start:
                        Color(*c_selection_range)
                        Rectangle(pos=(self.x + draw_start, self.y), size=(draw_end - draw_start, self.height))

                if start_beat_sel is not None:
                    x = start_beat_sel * self.pixels_per_beat
                    if viewport_x <= x <= viewport_x + viewport_w:
                        Color(*c_selection)
                        Rectangle(pos=(self.x + x, self.y), size=(dp(3), self.height))

                if end_beat_sel is not None:
                    x = end_beat_sel * self.pixels_per_beat
                    if viewport_x <= x <= viewport_x + viewport_w:
                        Color(*c_selection)
                        Rectangle(pos=(self.x + x - dp(3), self.y), size=(dp(3), self.height))

            # --- Optimized Grid Lines using Mesh ---
            major_vertices = []
            minor_vertices = []

            for beat in range(start_beat, end_beat + 1):
                x = beat * self.pixels_per_beat
                if beat % self.beats_per_measure == 0:
                    major_vertices.extend([self.x + x, self.y, 0, 0, self.x + x, self.y + self.height, 0, 0])
                else:
                    minor_vertices.extend([self.x + x, self.y + self.height * 0.4, 0, 0, self.x + x, self.y + self.height * 0.6, 0, 0])

            if major_vertices:
                Color(*c_measure)
                Mesh(vertices=major_vertices, indices=list(range(len(major_vertices)//4)), mode='lines')

            if minor_vertices:
                Color(*c_beat)
                Mesh(vertices=minor_vertices, indices=list(range(len(minor_vertices)//4)), mode='lines')

            # --- Labels ---
            for beat in range(start_beat, end_beat + 1):
                if beat % self.beats_per_measure == 0:
                    x = beat * self.pixels_per_beat
                    measure_num = (beat // self.beats_per_measure) + 1
                    texture = self.get_measure_texture(measure_num)
                    Color(*c_white)
                    Rectangle(
                        texture=texture,
                        pos=(int(self.x + x + self.label_padding_x), int(self.y + self.height * 0.2)),
                        size=texture.size
                    )

class Ruler(BoxLayout):
    total_beats = NumericProperty(16)
    pixels_per_beat = NumericProperty(dp(100))
    beats_per_measure = NumericProperty(4)
    info_width = NumericProperty(dp(150))
    controls_width = NumericProperty(dp(430))
    keyboard_width = NumericProperty(dp(40))
    spacing = NumericProperty(dp(12))
    sequencer_layout = ObjectProperty(None)    
    
    def __init__(self, **kwargs):
        # On extrait sequencer_layout avant le super() si on veut être prudent, 
        # mais avec ObjectProperty déclaré plus haut, super() l'acceptera.
        super().__init__(**kwargs)
        
        self.orientation = 'horizontal'
        self.size_hint_y = None
        self.height = dp(30)

        # --- CALCUL DE L'ALIGNEMENT PRÉCIS ---
        # On doit additionner les largeurs ET les espacements (spacing)
        # Dans TrackWidget, il y a souvent un spacing entre info/controls, 
        # puis entre controls/keyboard, puis entre keyboard/timeline.
        
        total_left_width = (
            self.info_width + 
            self.controls_width + 
            self.keyboard_width + 
            (self.spacing * 2) # Ajustez ce multiplicateur selon le nombre de gaps dans TrackWidget
        )

        self.ruler_left_panel = Widget(size_hint_x=None, width=total_left_width)
        self.add_widget(self.ruler_left_panel)
        
        # Timeline
        self.scroll_view = ScrollView(size_hint=(1, 1), do_scroll_x=True, do_scroll_y=False, bar_width=0, scroll_type=['bars', 'content'], effect_cls=ScrollEffect)
        self.ruler_content = RulerContent(
            sequencer_layout=self.sequencer_layout,
            total_beats=self.total_beats,
            pixels_per_beat=self.pixels_per_beat,
            beats_per_measure=self.beats_per_measure,
            height=self.height
        )
        self.scroll_view.add_widget(self.ruler_content)
        self.add_widget(self.scroll_view)

        # Export du g_translate pour l'interface
        self.g_translate = self.ruler_content.g_translate
        
        self.bind(sequencer_layout=self.ruler_content.setter('sequencer_layout'),
                  total_beats=lambda i, v: setattr(self.ruler_content, 'total_beats', v),
                  pixels_per_beat=lambda i, v: setattr(self.ruler_content, 'pixels_per_beat', v),
                  beats_per_measure=lambda i, v: setattr(self.ruler_content, 'beats_per_measure', v),
                  info_width=self._update_left_panel_width,
                  controls_width=self._update_left_panel_width,
                  keyboard_width=self._update_left_panel_width,
                  spacing=self._update_left_panel_width)

    def _update_left_panel_width(self, *args):
        self.ruler_left_panel.width = (
            self.info_width +
            self.controls_width +
            self.keyboard_width +
            (self.spacing * 2)
        )

    def redraw(self, *args):
        self.ruler_content.redraw()
