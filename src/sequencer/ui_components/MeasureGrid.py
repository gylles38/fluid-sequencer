from kivy.uix.widget import Widget
from kivy.graphics import Color, Line, Mesh
from kivy.properties import NumericProperty
from kivy.metrics import dp

# --- Définition de MeasureGrid ---
class MeasureGrid(Widget):
    """Dessine les lignes de mesures verticales en arrière-plan avec optimisation Mesh."""
    beat_per_measure = NumericProperty(4)
    total_beats = NumericProperty(128) 
    pixels_per_beat = NumericProperty(dp(100)) 

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # On ne binde plus 'pos' pour éviter les redraws inutiles au scroll
        self.bind(size=self.draw_measure_lines,
                  beat_per_measure=self.draw_measure_lines, total_beats=self.draw_measure_lines,
                  pixels_per_beat=self.draw_measure_lines)
        self.draw_measure_lines()

    def draw_measure_lines(self, *args):
        if not self.canvas:
            return
        self.canvas.clear()
        
        # total_width = self.total_beats * self.pixels_per_beat
        
        with self.canvas:
            # On utilise des lignes simples pour le moment car Mesh pour des lignes verticales
            # nécessite un mode spécial ou des rectangles très fins.
            # Cependant, on regroupe par couleur pour minimiser les changements de contexte.

            # 1. Barres de mesures (épaisses)
            Color(0.8, 0.8, 0.8, 0.8)
            current_beat = 0
            while current_beat <= self.total_beats:
                if current_beat % self.beat_per_measure == 0:
                    x_pos = current_beat * self.pixels_per_beat
                    Line(points=[x_pos, 0, x_pos, self.height], width=1.5)
                current_beat += 1

            # 2. Temps intermédiaires (fins)
            Color(0.5, 0.5, 0.5, 0.4)
            current_beat = 0
            while current_beat <= self.total_beats:
                if current_beat % self.beat_per_measure != 0:
                    x_pos = current_beat * self.pixels_per_beat
                    Line(points=[x_pos, 0, x_pos, self.height], width=0.5)
                current_beat += 1
# ----------------------------------