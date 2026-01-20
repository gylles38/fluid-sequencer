from kivy.uix.widget import Widget
from kivy.graphics import Color, Line
from kivy.properties import NumericProperty
from kivy.metrics import dp

# --- Définition de MeasureGrid ---
class MeasureGrid(Widget):
    """Dessine les lignes de mesures verticales en arrière-plan."""
    beat_per_measure = NumericProperty(4)
    total_beats = NumericProperty(128) 
    pixels_per_beat = NumericProperty(dp(100)) 

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bind(pos=self.draw_measure_lines, size=self.draw_measure_lines,
                  beat_per_measure=self.draw_measure_lines, total_beats=self.draw_measure_lines,
                  pixels_per_beat=self.draw_measure_lines)
        self.draw_measure_lines()

    def draw_measure_lines(self, *args):
        self.canvas.clear()
        
        total_width = self.total_beats * self.pixels_per_beat
        
        with self.canvas:
            current_beat = 0
            while current_beat <= self.total_beats:
                x_pos = current_beat * self.pixels_per_beat
                
                if x_pos > total_width + 1:
                    break 

                # Style de la ligne
                if current_beat % self.beat_per_measure == 0:
                    line_width = 1.5
                    Color(0.8, 0.8, 0.8, 0.8) # Mesure (Barre)
                else:
                    line_width = 0.5
                    Color(0.5, 0.5, 0.5, 0.4) # Temps intermédiaire (si vous implémentez l'affichage des temps)

                # Dessin : local coordinates thanks to RelativeLayout parent
                Line(points=[x_pos, 0, x_pos, self.height], width=line_width)

                # Incrémenter par 1 beat pour afficher les temps
                current_beat += 1
# ----------------------------------