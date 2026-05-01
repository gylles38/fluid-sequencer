from kivy.uix.relativelayout import RelativeLayout
from kivy.graphics import Color, Line, Mesh
from kivy.properties import NumericProperty
from kivy.metrics import dp
from kivy.clock import Clock

# --- Définition de MeasureGrid ---
class MeasureGrid(RelativeLayout):
    """Dessine les lignes de mesures verticales en arrière-plan."""
    beat_per_measure = NumericProperty(4)
    total_beats = NumericProperty(128) 
    pixels_per_beat = NumericProperty(dp(100)) 

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bind(pos=self.redraw, size=self.redraw,
                  beat_per_measure=self.redraw, total_beats=self.redraw,
                  pixels_per_beat=self.redraw)
        self.redraw()

    def redraw(self, *args):
        """Debounced redraw of the measure lines."""
        if getattr(self, '_redraw_pending', False): return
        self._redraw_pending = True
        Clock.schedule_once(self.draw_measure_lines, 0)

    def draw_measure_lines(self, *args):
        self._redraw_pending = False
        if not self.canvas: return
        self.canvas.clear()
        
        with self.canvas:
            major_vertices = []
            minor_vertices = []

            for i in range(int(self.total_beats) + 1):
                x_pos = round(i * self.pixels_per_beat)
                if i % self.beat_per_measure == 0:
                    # Major line
                    major_vertices.extend([x_pos, 0, 0, 0, x_pos, self.height, 0, 0])
                else:
                    # Minor line
                    minor_vertices.extend([x_pos, 0, 0, 0, x_pos, self.height, 0, 0])

            if major_vertices:
                Color(0.8, 0.8, 0.8, 0.8)
                Mesh(vertices=major_vertices, indices=list(range(len(major_vertices)//4)), mode='lines')

            if minor_vertices:
                Color(0.5, 0.5, 0.5, 0.4)
                Mesh(vertices=minor_vertices, indices=list(range(len(minor_vertices)//4)), mode='lines')
# ----------------------------------